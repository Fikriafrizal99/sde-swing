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
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from modules.broker_fusion.broker_fusion import broker_score_frame
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
    """Archive the current Broker Summary into the shared Swing history DB.

    This is idempotent because ``archive_broker`` keys a snapshot by broker date
    and file hash. A later normal pipeline archive of the same file therefore
    upserts the same snapshot instead of duplicating it.
    """
    if not broker_path.exists() or broker_path.stat().st_size == 0:
        return ""
    ensure_schema(conn)
    snapshot_id = archive_broker(conn, broker_path)
    conn.commit()
    return snapshot_id


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
        SELECT s.broker_date, s.created_at, b.row_json
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
        broker_date = str(row[0] or "").strip()
        if not broker_date:
            continue
        try:
            payload = json.loads(row[2] or "{}")
        except Exception:
            payload = {}
        by_date[broker_date] = {
            "broker_date": broker_date,
            "created_at": str(row[1] or ""),
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


def summarize_window(records: list[dict[str, Any]], size: int | None = None) -> dict[str, Any]:
    subset = records[-size:] if size else list(records)
    summary = _summary_metrics(subset)
    if size is not None:
        summary["required_observations"] = size
        if len(subset) < size:
            summary["context"] = "INSUFFICIENT_DATA"
            summary["coverage_status"] = "INSUFFICIENT_DATA"
            return summary
        summary["coverage_status"] = "COMPLETE"
    else:
        summary["coverage_status"] = "PARTIAL_HISTORY" if len(subset) < 3 else "AVAILABLE"
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

    if current == "UNAVAILABLE":
        return "UNAVAILABLE", "current broker data unavailable"
    if n3 < 3:
        return "NEUTRAL", f"current {current.lower()} is warning-only; 3D history insufficient ({n3}/3)"

    if current == "DISTRIBUTION":
        if c3 == "DISTRIBUTION":
            return "DISTRIBUTION", "current distribution confirmed by complete 3D broker history"
        if ns >= 5 and (c5 == "ACCUMULATION" or cs == "ACCUMULATION"):
            return "NEUTRAL", "current distribution conflicts with accumulated broker history"
        return "NEUTRAL", "current distribution is not confirmed by 3D broker history"

    if current == "ACCUMULATION":
        if c3 == "ACCUMULATION":
            return "ACCUMULATION", "current accumulation confirmed by complete 3D broker history"
        if ns >= 5 and (c5 == "DISTRIBUTION" or cs == "DISTRIBUTION"):
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
    d3 = summarize_window(records, 3)
    d5 = summarize_window(records, 5)
    d7 = summarize_window(records, 7)
    since = summarize_window(records, None)
    current_record = records[-1] if records else {}
    current_state = str(current_record.get("state") or "UNAVAILABLE").upper()
    effective_state, effective_reason = _effective_state(current_state, d3, d5, since)
    top_accumulation, top_distribution = _actor_totals(records)
    current_accumulation, current_distribution = _actor_totals(records[-1:])

    return {
        "symbol": symbol,
        "buy_date": buy_date,
        "analysis_date": analysis_date,
        "broker_data_date": str(current_record.get("broker_date") or ""),
        "observation_count": len(records),
        "current_state": current_state,
        "current_score": current_record.get("score"),
        "current_confidence": current_record.get("confidence"),
        "current_net_flow": current_record.get("net_flow"),
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
