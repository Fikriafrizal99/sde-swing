from __future__ import annotations

"""Broker-history context for actual OPEN portfolio positions.

This module is intentionally read/append-only from the perspective of the
trading engines. It reuses the canonical Broker Summary archive in
``sde_swing_history.db`` and never changes Candidate, Broker Fusion, Decision,
or Final Watchlist outputs.

Portfolio history uses one latest observation per broker date. Fixed windows
(3D/5D/7D) are only considered valid when the required number of observations
exists. A current broker signal with fewer than three observations is therefore
kept as a warning and cannot by itself drive Position Management.
"""

import json
import math
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from modules.broker_fusion.broker_fusion import broker_score_frame
from modules.broker_bridge.broker_period_context import trading_sessions_between
from modules.database.swing_history_db import archive_broker, init_schema

BROKER_CONTEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_broker_context_history (
    context_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    broker_data_date TEXT,
    observation_count INTEGER NOT NULL DEFAULT 0,
    current_state TEXT,
    effective_state TEXT,
    context_3d TEXT,
    context_5d TEXT,
    context_7d TEXT,
    context_since_entry TEXT,
    net_flow_since_entry REAL,
    avg_daily_net_flow REAL,
    buy_days INTEGER,
    sell_days INTEGER,
    accumulation_days INTEGER,
    distribution_days INTEGER,
    persistence_pct REAL,
    broker_score_avg REAL,
    broker_score_trend TEXT,
    flow_trend TEXT,
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(position_id, analysis_date)
);
CREATE INDEX IF NOT EXISTS idx_position_broker_context_position_date
    ON position_broker_context_history(position_id, analysis_date);
"""

_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "TOTAL_BUY": ("TOTAL_BUY", "TOTAL BUY"),
    "TOTAL_SELL": ("TOTAL_SELL", "TOTAL SELL"),
    "NET_FLOW": ("NET_FLOW", "NET FLOW", "NETFLOW", "NET_VALUE"),
    "TOTAL_VALUE": ("TOTAL_VALUE", "TOTAL VALUE"),
    "TOTAL_VOLUME": ("TOTAL_VOLUME", "TOTAL VOLUME"),
    "BUYER_CONCENTRATION": ("BUYER_CONCENTRATION", "BUYER CONCENTRATION"),
    "SELLER_CONCENTRATION": ("SELLER_CONCENTRATION", "SELLER CONCENTRATION"),
    "BROKER_ACCDIST": ("BROKER_ACCDIST", "BROKER ACCDIST", "ACC_DIST"),
    "AVG_ACCDIST": ("AVG_ACCDIST", "AVG ACCDIST"),
    "TOP3_ACCDIST": ("TOP3_ACCDIST", "TOP3 ACCDIST"),
}


def _norm(value: object) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _flag(value: Any) -> bool:
    return value is True or str(value).strip().upper() in {"1", "TRUE", "YES", "Y"}


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _payload_get(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload.get(key)
    wanted = _norm(key)
    for current, value in payload.items():
        if _norm(current) == wanted:
            return value
    return None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure both canonical Swing DB tables and portfolio broker context exist."""
    init_schema(conn)
    conn.executescript(BROKER_CONTEXT_SCHEMA)
    conn.commit()


def sync_latest_broker_summary(conn: sqlite3.Connection, broker_path: Path) -> str:
    """Archive only a real 1D Broker Summary into portfolio history.

    The canonical Final Watchlist file can intentionally contain a 3D/5D/
    CUSTOM PRIMARY snapshot.  Its sidecar is authoritative; such a snapshot
    is context for Final Watchlist only and is rejected here before it reaches
    the portfolio daily archive.  Legacy callers without a sidecar are still
    supported when the CSV itself proves ``FROM_DATE == TO_DATE``.
    """
    if not broker_path.exists() or broker_path.stat().st_size == 0:
        return ""
    ensure_schema(conn)
    sidecar = broker_path.with_suffix(".manifest.json")
    manifest = _daily_manifest_from_source(broker_path, sidecar if sidecar.exists() else None)
    if not manifest:
        return ""
    snapshot_id = archive_broker(
        conn,
        broker_path,
        sidecar if sidecar.exists() else None,
        manifest_payload=manifest,
    )
    conn.commit()
    return snapshot_id


def _daily_manifest_from_source(path: Path, sidecar: Path | None) -> dict[str, Any]:
    """Return a normalized real-1D provenance envelope or ``{}``.

    An explicit sidecar wins.  When it is absent, exact same-day period
    columns are the minimum evidence required for backward compatibility.
    Missing/ambiguous dates are fail-closed so an aggregate cannot be guessed
    into a daily observation.
    """
    explicit: dict[str, Any] = {}
    if sidecar and sidecar.exists():
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                explicit = dict(payload)
        except Exception:
            return {}

    period_type = str(explicit.get("broker_period_type") or "").strip().upper()
    source = str(
        explicit.get("broker_period_source")
        or explicit.get("source")
        or ""
    ).strip().upper()
    explicit_eligible = explicit.get("daily_history_eligible", period_type == "1D")
    explicit_eligible_flag = _flag(explicit_eligible)
    if explicit and (
        _flag(explicit.get("aggregate_snapshot"))
        or not explicit_eligible_flag
        or (period_type and period_type != "1D")
        or not source
        or source not in {"STOCKBIT_1D", "STOCKBIT"}
        or source in {"INTERNAL_DAILY_ROLLUP", "STOCKBIT_AGGREGATE_EXPORT", "CUSTOM_AGGREGATE"}
    ):
        return {}

    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return {}
    from_column = next((column for column in frame.columns if _norm(column) in {"FROMDATE", "BROKERFROMDATE"}), None)
    to_column = next((column for column in frame.columns if _norm(column) in {"TODATE", "BROKERTODATE"}), None)
    if to_column is None or frame.empty:
        return {}
    from_values = pd.to_datetime(frame[from_column], errors="coerce") if from_column is not None else pd.Series(pd.NaT, index=frame.index)
    to_values = pd.to_datetime(frame[to_column], errors="coerce")
    valid = from_values.notna() & to_values.notna()
    # A few pre-period portfolio fixtures only carried TO_DATE.  Keep that
    # narrow compatibility path, while real validated aggregate exports still
    # carry both FROM_DATE and TO_DATE and are rejected when they span dates.
    if from_column is None and not explicit:
        valid = to_values.notna()
    if not bool(valid.any()):
        return {}
    normalized_from = set(from_values.loc[valid].dt.date.astype(str))
    normalized_to = set(to_values.loc[valid].dt.date.astype(str))
    if from_column is None:
        normalized_from = set(normalized_to)
    if len(normalized_from) != 1 or len(normalized_to) != 1 or normalized_from != normalized_to:
        return {}
    market_date = next(iter(normalized_to))
    inferred = {
        "broker_date": market_date,
        "from_date": market_date,
        "to_date": market_date,
        "broker_period_type": "1D",
        "broker_period_start": market_date,
        "broker_period_end": market_date,
        "broker_trading_days": 1,
        "broker_period_source": "STOCKBIT_1D",
        "daily_history_eligible": True,
        "aggregate_snapshot": False,
        "broker_period_complete": True,
        "broker_session_dates": [market_date],
    }
    inferred.update(explicit)
    # A sidecar with incomplete 1D metadata must not downgrade the CSV proof
    # into an aggregate or silently accept a different date.
    if str(inferred.get("broker_period_type") or "1D").upper() != "1D":
        return {}
    inferred_eligible = inferred.get("daily_history_eligible", True)
    if not _flag(inferred_eligible):
        return {}
    inferred["broker_period_source"] = "STOCKBIT_1D"
    inferred["broker_period_type"] = "1D"
    inferred["broker_period_start"] = market_date
    inferred["broker_period_end"] = market_date
    inferred["broker_date"] = market_date
    inferred["from_date"] = market_date
    inferred["to_date"] = market_date
    inferred["broker_trading_days"] = 1
    inferred["broker_session_dates"] = [market_date]
    inferred["daily_history_eligible"] = True
    inferred["aggregate_snapshot"] = False
    return inferred


def _canonicalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    lookup = {_norm(key): key for key in payload}
    out = dict(payload)
    for canonical, aliases in _CANONICAL_ALIASES.items():
        if canonical in out:
            continue
        source = next((lookup.get(_norm(alias)) for alias in aliases if lookup.get(_norm(alias))), None)
        if source:
            out[canonical] = payload.get(source)
    return out


def _score_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not records:
        return []
    payloads = [_canonicalize_payload(dict(record.get("payload") or {})) for record in records]
    scored = broker_score_frame(pd.DataFrame(payloads))
    output: list[dict[str, Any]] = []
    for record, (_, row) in zip(records, scored.iterrows()):
        output.append({
            **record,
            "state": str(row.get("Broker_Direction", "NEUTRAL") or "NEUTRAL").upper(),
            "score": _as_float(row.get("Broker_Score")),
            "confidence": _as_float(row.get("Broker_Confidence")),
            "direction_score": _as_float(row.get("Broker_Direction_Score")),
            "net_flow": _as_float(row.get("NET_FLOW")) or 0.0,
        })
    return output


def _snapshot_is_real_daily(row: sqlite3.Row) -> bool:
    """Check snapshot provenance before exposing it to Portfolio Management."""
    try:
        manifest = json.loads(row[3] or "{}")
    except Exception:
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    period_type = str(manifest.get("broker_period_type") or "").strip().upper()
    source = str(
        manifest.get("broker_period_source")
        or manifest.get("source")
        or ""
    ).strip().upper()
    if manifest:
        eligible = manifest.get("daily_history_eligible")
        eligible_flag = _flag(eligible)
        return bool(
            period_type == "1D"
            and eligible_flag
            and not _flag(manifest.get("aggregate_snapshot"))
            and source in {"STOCKBIT_1D", "STOCKBIT"}
        )

    # Compatibility for old hand-created/legacy daily snapshots.  An old
    # aggregate archived without provenance has an empty FROM_DATE and is
    # therefore rejected instead of being guessed as a one-day observation.
    broker_date = str(row[0] or "").strip()[:10]
    from_date = str(row[1] or "").strip()[:10]
    to_date = str(row[2] or "").strip()[:10]
    return bool(broker_date and from_date == broker_date and to_date == broker_date)


def _expected_sessions(size: int, analysis_date: str) -> list[str]:
    """Return the exact latest IDX sessions ending at ``analysis_date``."""
    try:
        end = date.fromisoformat(str(analysis_date).strip()[:10])
        # 7D is retained for compatibility with the existing portfolio report;
        # all windows still use calendar sessions rather than row positions.
        span = max(45, size * 8)
        sessions = trading_sessions_between(end - timedelta(days=span), end)
        return sessions[-size:]
    except (TypeError, ValueError, OSError):
        return []


def _sessions_since(buy_date: str, analysis_date: str) -> list[str]:
    try:
        start = date.fromisoformat(str(buy_date).strip()[:10])
        end = date.fromisoformat(str(analysis_date).strip()[:10])
        if start > end:
            return []
        return trading_sessions_between(start, end)
    except (TypeError, ValueError, OSError):
        return []


def load_broker_history(
    conn: sqlite3.Connection,
    symbol: str,
    buy_date: str,
    analysis_date: str,
) -> list[dict[str, Any]]:
    """Load one latest Broker Summary observation per trading date since BUY."""
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT s.broker_date, s.from_date, s.to_date, s.manifest_json,
               s.created_at, s.broker_snapshot_id, s.snapshot_hash, b.row_json
        FROM broker_summary b
        JOIN broker_snapshots s ON s.broker_snapshot_id=b.broker_snapshot_id
        WHERE UPPER(b.symbol)=UPPER(?)
          AND COALESCE(s.broker_date, '') >= ?
          AND COALESCE(s.broker_date, '') <= ?
        ORDER BY s.broker_date, s.created_at, s.broker_snapshot_id
        """,
        (symbol, buy_date, analysis_date),
    ).fetchall()

    # Multiple reruns can legitimately produce multiple hashes for one date.
    # Keep only the most recently archived observation for that market date.
    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _snapshot_is_real_daily(row):
            continue
        broker_date = str(row[0] or "").strip()[:10]
        if not broker_date:
            continue
        try:
            session_date = date.fromisoformat(broker_date)
        except ValueError:
            continue
        # A daily row on a weekend/holiday is not a valid IDX session.  The
        # CSV can remain archived for audit, but it must not fill a portfolio
        # session window.
        if session_date.weekday() >= 5 or broker_date not in trading_sessions_between(session_date, session_date):
            continue
        try:
            payload = json.loads(row[7] or "{}")
        except Exception:
            payload = {}
        by_date[broker_date] = {
            "broker_date": broker_date,
            "created_at": str(row[4] or ""),
            "snapshot_id": str(row[5] or ""),
            "capture_hash": str(row[6] or ""),
            "source": "STOCKBIT_1D",
            "payload": payload if isinstance(payload, dict) else {},
        }
    return _score_records([by_date[key] for key in sorted(by_date)])


def _context_from_direction_score(value: float | None) -> str:
    value = float(value or 0.0)
    if value >= 15.0:
        return "ACCUMULATION"
    if value <= -15.0:
        return "DISTRIBUTION"
    return "NEUTRAL"


def _mean(values: list[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(clean) / len(clean) if clean else None


def _trend(values: list[float | None], *, relative: bool = False) -> str:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if len(clean) < 4:
        return "INSUFFICIENT_DATA"
    recent = clean[-3:]
    previous = clean[max(0, len(clean) - 6):-3]
    if not previous:
        return "INSUFFICIENT_DATA"
    recent_avg = sum(recent) / len(recent)
    previous_avg = sum(previous) / len(previous)
    delta = recent_avg - previous_avg
    threshold = max(abs(previous_avg) * 0.25, 1.0) if relative else 5.0
    if delta >= threshold:
        return "STRENGTHENING"
    if delta <= -threshold:
        return "WEAKENING"
    return "STABLE"


def _summary_metrics(subset: list[dict[str, Any]]) -> dict[str, Any]:
    if not subset:
        return {
            "context": "UNAVAILABLE",
            "observation_count": 0,
            "net_flow": None,
            "avg_net_flow": None,
            "score_avg": None,
            "confidence_avg": None,
            "direction_score_avg": None,
            "buy_days": 0,
            "sell_days": 0,
            "accumulation_days": 0,
            "distribution_days": 0,
            "neutral_days": 0,
            "persistence_pct": None,
        }

    direction_avg = _mean([item.get("direction_score") for item in subset]) or 0.0
    net_values = [float(item.get("net_flow") or 0.0) for item in subset]
    states = [str(item.get("state") or "NEUTRAL").upper() for item in subset]
    accumulation_days = states.count("ACCUMULATION")
    distribution_days = states.count("DISTRIBUTION")
    neutral_days = len(states) - accumulation_days - distribution_days
    persistence = (
        max(accumulation_days, distribution_days) / len(subset) * 100.0
        if len(subset) >= 3 else None
    )
    return {
        "context": _context_from_direction_score(direction_avg),
        "observation_count": len(subset),
        "from_date": subset[0]["broker_date"],
        "to_date": subset[-1]["broker_date"],
        "net_flow": sum(net_values),
        "avg_net_flow": sum(net_values) / len(net_values),
        "score_avg": _mean([item.get("score") for item in subset]),
        "confidence_avg": _mean([item.get("confidence") for item in subset]),
        "direction_score_avg": direction_avg,
        "buy_days": sum(1 for value in net_values if value > 0),
        "sell_days": sum(1 for value in net_values if value < 0),
        "accumulation_days": accumulation_days,
        "distribution_days": distribution_days,
        "neutral_days": neutral_days,
        "persistence_pct": persistence,
    }


def summarize_window(
    records: list[dict[str, Any]],
    size: int | None = None,
    *,
    expected_sessions: list[str] | None = None,
) -> dict[str, Any]:
    exact_window = expected_sessions is not None
    if exact_window:
        expected = [str(value)[:10] for value in expected_sessions or [] if str(value).strip()]
        expected_set = set(expected)
        by_date = {
            str(record.get("broker_date") or "")[:10]: record
            for record in records
            if str(record.get("broker_date") or "")[:10] in expected_set
        }
        subset = [by_date[value] for value in expected if value in by_date]
    else:
        expected = []
        subset = records[-size:] if size else list(records)
    summary = _summary_metrics(subset)
    if size is not None:
        summary["required_observations"] = size
        summary["expected_sessions"] = expected or [str(item.get("broker_date") or "") for item in subset]
        summary["observed_sessions"] = [str(item.get("broker_date") or "") for item in subset]
        summary["missing_sessions"] = [value for value in expected if value not in {item.get("broker_date") for item in subset}]
        summary["coverage"] = len(subset) / size if size else 0.0
        summary["coverage_text"] = f"{len(subset)}/{size}"
        if len(subset) < size or summary["missing_sessions"]:
            summary["context"] = "INSUFFICIENT_DATA"
            summary["coverage_status"] = "INSUFFICIENT_DATA"
            return summary
        summary["coverage_status"] = "COMPLETE"
    else:
        summary["actual_session_count"] = len(subset)
        summary["expected_sessions"] = expected
        summary["observed_sessions"] = [str(item.get("broker_date") or "") for item in subset]
        summary["missing_sessions"] = [
            value for value in expected
            if value not in {str(item.get("broker_date") or "")[:10] for item in subset}
        ]
        summary["expected_session_count"] = len(expected)
        summary["coverage"] = len(subset) / len(expected) if expected else None
        summary["coverage_text"] = f"{len(subset)}/{len(expected)}" if expected else ""
        summary["coverage_status"] = "PARTIAL_HISTORY" if len(subset) < 3 or summary["missing_sessions"] else "AVAILABLE"
    return summary


def _actor_totals(records: list[dict[str, Any]], limit: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate top-broker nominal values exported by portfolio backfill.

    Older Broker Summary snapshots do not contain ``TOP_*_VALUE`` fields. They
    are simply ignored; no amount is estimated or fabricated.
    """
    totals: dict[str, float] = {}
    for record in records:
        payload = dict(record.get("payload") or {})
        for rank in range(1, 4):
            buy_code = str(_payload_get(payload, f"TOP_BUYER_{rank}") or "").strip().upper()
            buy_value = _as_float(_payload_get(payload, f"TOP_BUYER_{rank}_VALUE"))
            if buy_code and buy_value is not None:
                totals[buy_code] = totals.get(buy_code, 0.0) + abs(buy_value)

            sell_code = str(_payload_get(payload, f"TOP_SELLER_{rank}") or "").strip().upper()
            sell_value = _as_float(_payload_get(payload, f"TOP_SELLER_{rank}_VALUE"))
            if sell_code and sell_value is not None:
                totals[sell_code] = totals.get(sell_code, 0.0) - abs(sell_value)

    accumulation = [
        {"broker": broker, "net_value": value}
        for broker, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
        if value > 0
    ][:limit]
    distribution = [
        {"broker": broker, "net_value": value}
        for broker, value in sorted(totals.items(), key=lambda item: item[1])
        if value < 0
    ][:limit]
    return accumulation, distribution


def _effective_state(
    current: str,
    d3: dict[str, Any],
    d5: dict[str, Any],
    since: dict[str, Any],
) -> tuple[str, str]:
    """Return the broker state consumed by Position Management.

    A current single-session direction is evidence, not confirmation. Until a
    complete 3D window exists it remains warning-only and effective state is
    neutral. This prevents one backfilled/current observation from immediately
    changing HOLD/EXIT management.
    """
    current = str(current or "UNAVAILABLE").upper()
    c3 = str(d3.get("context") or "UNAVAILABLE").upper()
    c5 = str(d5.get("context") or "UNAVAILABLE").upper()
    cs = str(since.get("context") or "UNAVAILABLE").upper()
    n3 = int(d3.get("observation_count") or 0)
    ns = int(since.get("observation_count") or 0)
    d3_complete = str(d3.get("coverage_status") or "").upper() == "COMPLETE"
    d5_complete = str(d5.get("coverage_status") or "").upper() == "COMPLETE"

    if current == "UNAVAILABLE":
        return "UNAVAILABLE", "current broker data unavailable"
    if n3 < 3 or not d3_complete:
        return "NEUTRAL", f"current {current.lower()} is warning-only; 3D history insufficient ({n3}/3)"

    if current == "DISTRIBUTION":
        if c3 == "DISTRIBUTION":
            return "DISTRIBUTION", "current distribution confirmed by complete 3D broker history"
        if d5_complete and ns >= 5 and (c5 == "ACCUMULATION" or cs == "ACCUMULATION"):
            return "NEUTRAL", "current distribution conflicts with accumulated broker history"
        return "NEUTRAL", "current distribution is not confirmed by 3D broker history"

    if current == "ACCUMULATION":
        if c3 == "ACCUMULATION":
            return "ACCUMULATION", "current accumulation confirmed by complete 3D broker history"
        if d5_complete and ns >= 5 and (c5 == "DISTRIBUTION" or cs == "DISTRIBUTION"):
            return "NEUTRAL", "current accumulation conflicts with persistent broker history"
        return "NEUTRAL", "current accumulation is not confirmed by 3D broker history"

    if c3 == "ACCUMULATION" and (c5 in {"ACCUMULATION", "INSUFFICIENT_DATA"}):
        return "ACCUMULATION", "neutral current day supported by complete 3D accumulation"
    if c3 == "DISTRIBUTION" and (c5 in {"DISTRIBUTION", "INSUFFICIENT_DATA"}):
        return "DISTRIBUTION", "neutral current day supported by complete 3D distribution"
    return "NEUTRAL", "broker history is mixed"


def build_position_broker_context(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    buy_date: str,
    analysis_date: str,
) -> dict[str, Any]:
    records = load_broker_history(conn, symbol, buy_date, analysis_date)
    sessions_3d = _expected_sessions(3, analysis_date)
    sessions_5d = _expected_sessions(5, analysis_date)
    sessions_7d = _expected_sessions(7, analysis_date)
    since_sessions = _sessions_since(buy_date, analysis_date)
    d3 = summarize_window(records, 3, expected_sessions=sessions_3d)
    d5 = summarize_window(records, 5, expected_sessions=sessions_5d)
    d7 = summarize_window(records, 7, expected_sessions=sessions_7d)
    since = summarize_window(records, None, expected_sessions=since_sessions)
    current_record = records[-1] if records else {}
    current_date = str(current_record.get("broker_date") or "")[:10]
    analysis_date_text = str(analysis_date)[:10]
    today_pulse_available = bool(current_date and current_date == analysis_date_text)
    historical_latest_state = str(current_record.get("state") or "UNAVAILABLE").upper()
    # The latest archived daily row is useful historical context, but it must
    # not masquerade as today's primary 1D pulse when the current session is
    # missing.  Keep its date/state separately for auditability.
    current_for_pulse = current_record if today_pulse_available else {}
    current_state = (
        historical_latest_state if today_pulse_available else "UNAVAILABLE"
    )
    effective_state, effective_reason = _effective_state(current_state, d3, d5, since)
    top_accumulation, top_distribution = _actor_totals(records)
    current_accumulation, current_distribution = _actor_totals(
        records[-1:] if today_pulse_available else []
    )

    return {
        "symbol": symbol,
        "buy_date": buy_date,
        "analysis_date": analysis_date,
        "broker_data_date": str(current_record.get("broker_date") or ""),
        "today_pulse_available": today_pulse_available,
        "today_pulse_status": "AVAILABLE" if today_pulse_available else "NOT_AVAILABLE",
        "today_pulse_source": current_for_pulse.get("source", "STOCKBIT_1D") if current_for_pulse else "NOT_AVAILABLE",
        "observation_count": len(records),
        "current_state": current_state,
        "latest_historical_state": historical_latest_state,
        "latest_historical_date": current_date,
        "current_score": current_for_pulse.get("score"),
        "current_confidence": current_for_pulse.get("confidence"),
        "current_net_flow": current_for_pulse.get("net_flow"),
        "current_source": current_for_pulse.get("source", "STOCKBIT_1D") if current_for_pulse else "NOT_AVAILABLE",
        "current_snapshot_id": current_for_pulse.get("snapshot_id", "") if current_for_pulse else "",
        "effective_state": effective_state,
        "effective_reason": effective_reason,
        "3D": d3,
        "5D": d5,
        "7D": d7,
        "since_entry": since,
        "broker_score_trend": _trend([item.get("score") for item in records]),
        "flow_trend": _trend([item.get("net_flow") for item in records], relative=True),
        "top_accumulation": top_accumulation,
        "top_distribution": top_distribution,
        "current_top_accumulation": current_accumulation,
        "current_top_distribution": current_distribution,
        "actor_data_status": "AVAILABLE" if (top_accumulation or top_distribution) else "UNAVAILABLE",
    }


def persist_position_broker_context(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    context: dict[str, Any],
) -> None:
    ensure_schema(conn)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    since = context.get("since_entry") or {}
    context_id = f"{position_id}:{context.get('analysis_date', '')}"
    persistence = since.get("persistence_pct")
    values = (
        context_id,
        position_id,
        context.get("symbol"),
        context.get("analysis_date"),
        context.get("broker_data_date"),
        int(context.get("observation_count") or 0),
        context.get("current_state"),
        context.get("effective_state"),
        (context.get("3D") or {}).get("context"),
        (context.get("5D") or {}).get("context"),
        (context.get("7D") or {}).get("context"),
        since.get("context"),
        since.get("net_flow"),
        since.get("avg_net_flow"),
        int(since.get("buy_days") or 0),
        int(since.get("sell_days") or 0),
        int(since.get("accumulation_days") or 0),
        int(since.get("distribution_days") or 0),
        float(persistence) if persistence is not None else None,
        since.get("score_avg"),
        context.get("broker_score_trend"),
        context.get("flow_trend"),
        json.dumps(context, ensure_ascii=False, default=str),
        timestamp,
        timestamp,
    )
    conn.execute(
        """
        INSERT INTO position_broker_context_history (
            context_id, position_id, symbol, analysis_date, broker_data_date,
            observation_count, current_state, effective_state, context_3d,
            context_5d, context_7d, context_since_entry, net_flow_since_entry,
            avg_daily_net_flow, buy_days, sell_days, accumulation_days,
            distribution_days, persistence_pct, broker_score_avg,
            broker_score_trend, flow_trend, context_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id, analysis_date) DO UPDATE SET
            broker_data_date=excluded.broker_data_date,
            observation_count=excluded.observation_count,
            current_state=excluded.current_state,
            effective_state=excluded.effective_state,
            context_3d=excluded.context_3d,
            context_5d=excluded.context_5d,
            context_7d=excluded.context_7d,
            context_since_entry=excluded.context_since_entry,
            net_flow_since_entry=excluded.net_flow_since_entry,
            avg_daily_net_flow=excluded.avg_daily_net_flow,
            buy_days=excluded.buy_days,
            sell_days=excluded.sell_days,
            accumulation_days=excluded.accumulation_days,
            distribution_days=excluded.distribution_days,
            persistence_pct=excluded.persistence_pct,
            broker_score_avg=excluded.broker_score_avg,
            broker_score_trend=excluded.broker_score_trend,
            flow_trend=excluded.flow_trend,
            context_json=excluded.context_json,
            updated_at=excluded.updated_at
        """,
        values,
    )
    conn.commit()
