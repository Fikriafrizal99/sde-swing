from __future__ import annotations

"""Canonical PRIMARY + TODAY broker presentation context.

This module is intentionally presentation/read-model only. Broker Fusion owns
broker scoring and the Final Decision Engine owns trading decisions. The read
model never reconstructs multi-day windows from a local history database.

Production contract
-------------------
* PRIMARY is the exact Stockbit period selected by the operator (1D/3D/5D/CUSTOM).
* TODAY is an independent exact real-1D capture for the same Final Watchlist date.
* When PRIMARY is 1D, TODAY is not emitted a second time.
* Alignment uses ``primary_pulse_alignment`` only; it is presentation context.
* Missing local historical observations are never broker activity observations.
* A selected sidecar must name the PRIMARY raw snapshot explicitly. The active
  canonical raw file is never used as a silent substitute for that snapshot.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from modules.broker_bridge.broker_period_context import primary_pulse_alignment
from modules.broker_bridge.broker_raw import broker_raw_trade_date, read_normalized_broker_raw


_PRIMARY_SUMMARY_KEYS = {
    "net_flow": ("NET_FLOW", "Net_Flow", "Net Flow"),
    "broker_accdist": ("BROKER_ACCDIST", "Broker_AccDist", "Broker AccDist"),
    "avg_accdist": ("AVG_ACCDIST", "Avg_AccDist", "Avg AccDist"),
    "buyer_concentration": ("BUYER_CONCENTRATION", "Buyer_Concentration"),
    "seller_concentration": ("SELLER_CONCENTRATION", "Seller_Concentration"),
    "avg_buyer_price": (
        "AVG_BUYER_PRICE",
        "Average_Buyer_Price",
        "Bandar_Buy_Cost",
        "Weighted_Buyer_Avg",
    ),
    "avg_seller_price": (
        "AVG_SELLER_PRICE",
        "Average_Seller_Price",
        "Bandar_Sell_Cost",
        "Weighted_Seller_Avg",
    ),
    "total_buy": ("TOTAL_BUY", "Total_Buy"),
    "total_sell": ("TOTAL_SELL", "Total_Sell"),
    "total_value": ("TOTAL_VALUE", "Total_Value"),
}


@dataclass(frozen=True)
class BrokerPeriodView:
    trade_date: str
    metadata: dict[str, Any]
    primary_by_symbol: dict[str, dict[str, Any]]
    today_by_symbol: dict[str, dict[str, Any]]
    input_paths: tuple[str, ...]

    @property
    def period_type(self) -> str:
        return str(self.metadata.get("broker_period_type") or "").upper()

    @property
    def has_separate_today(self) -> bool:
        return bool(self.period_type and self.period_type not in {"1D", "1DAY", "DAY"})

    def symbol(self, symbol: str) -> dict[str, Any]:
        key = _symbol(symbol)
        primary = dict(self.primary_by_symbol.get(key, {}))
        today = dict(self.today_by_symbol.get(key, {})) if self.has_separate_today else {}
        primary_net = _float_or_none(primary.get("net_flow"))
        today_net = _float_or_none(today.get("net_flow"))
        alignment = (
            primary_pulse_alignment(
                primary_net,
                today_net,
                pulse_status="AVAILABLE" if today else "NOT_AVAILABLE",
            )
            if self.has_separate_today
            else ""
        )
        return {
            "symbol": key,
            "primary": primary,
            "today": today,
            "alignment": alignment,
            **self.metadata,
            "today_pulse_available": bool(today),
            "today_pulse_status": "AVAILABLE" if today else (
                "NOT_APPLICABLE" if not self.has_separate_today else "NOT_AVAILABLE"
            ),
        }


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def _norm(value: Any) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")


def _row_value(row: Mapping[str, Any], *aliases: str, default: Any = "") -> Any:
    lookup = {_norm(key): value for key, value in row.items()}
    for alias in aliases:
        value = lookup.get(_norm(alias))
        if value is not None and str(value).strip().lower() not in {"", "nan", "none", "null"}:
            return value
    return default


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_path(value: Any, *, project_root: Path) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text)
    return path if path.is_absolute() else project_root / path


def _usable_path(path: Path | None) -> Path | None:
    if path is None or not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        return None
    return path


def _selected_period_context(
    summary_path: Path,
    *,
    trade_date: str,
    project_root: Path,
    latest_selected_path: str | Path | None,
) -> tuple[dict[str, Any], Path | None]:
    """Resolve active period metadata, preferring the canonical sidecar."""
    sidecar_path = summary_path.with_suffix(".manifest.json")
    sidecar = _read_json(sidecar_path)
    latest_path = Path(latest_selected_path) if latest_selected_path else project_root / "data/output/broker_snapshots/latest_selected.json"
    if not latest_path.is_absolute():
        latest_path = project_root / latest_path
    latest = _read_json(latest_path)

    selected = sidecar
    selected_path: Path | None = sidecar_path if selected else None
    selected_end = str(
        selected.get("broker_period_end")
        or selected.get("to_date")
        or selected.get("broker_date")
        or ""
    )[:10]
    if not selected or (selected_end and selected_end != str(trade_date)[:10]):
        latest_end = str(
            latest.get("broker_period_end")
            or latest.get("to_date")
            or latest.get("broker_date")
            or ""
        )[:10]
        if latest and (not latest_end or latest_end == str(trade_date)[:10]):
            selected = latest
            selected_path = latest_path
        else:
            selected = {}
            selected_path = None
    return selected, selected_path


def _summary_map(path: Path | None) -> dict[str, dict[str, Any]]:
    path = _usable_path(path)
    if path is None:
        return {}
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return {}
    symbol_col = next(
        (column for column in frame.columns if _norm(column) in {"emiten", "symbol", "ticker", "code"}),
        None,
    )
    if symbol_col is None:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw in frame.to_dict(orient="records"):
        symbol = _symbol(raw.get(symbol_col))
        if not symbol:
            continue
        item: dict[str, Any] = {"symbol": symbol}
        for target, aliases in _PRIMARY_SUMMARY_KEYS.items():
            value = _row_value(raw, *aliases, default="")
            if value not in (None, ""):
                item[target] = value
        buyers = [
            _row_value(raw, f"TOP_BUYER_{index}", f"Top_Buyer_{index}", default="")
            for index in range(1, 4)
        ]
        sellers = [
            _row_value(raw, f"TOP_SELLER_{index}", f"Top_Seller_{index}", default="")
            for index in range(1, 4)
        ]
        item["top_buyers"] = [str(value).strip().upper() for value in buyers if str(value).strip()]
        item["top_sellers"] = [str(value).strip().upper() for value in sellers if str(value).strip()]
        result[symbol] = item
    return result


def _scalar(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return value


def _raw_participant_map(
    path: Path | None,
    *,
    expected_trade_date: str,
) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], str]:
    """Read one exact raw snapshot without consulting canonical active raw data."""
    path = _usable_path(path)
    if path is None:
        return {}, "MISSING"
    try:
        frame = read_normalized_broker_raw(path)
    except Exception:
        return {}, "INVALID"
    if frame.empty:
        return {}, "EMPTY"
    raw_date = broker_raw_trade_date(frame)
    if expected_trade_date and raw_date != expected_trade_date:
        return {}, "DATE_MISMATCH"

    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for symbol, group in frame.groupby("SYMBOL", sort=False):
        key = _symbol(symbol)
        if not key:
            continue
        sides: dict[str, list[dict[str, Any]]] = {"BUY": [], "SELL": []}
        for side in ("BUY", "SELL"):
            subset = group[group["SIDE"].astype(str).str.upper().eq(side)].copy()
            if subset.empty:
                continue
            subset = subset.sort_values(
                ["RANK", "NET_VALUE"],
                ascending=[True, False],
                na_position="last",
            )
            for _, raw in subset.head(3).iterrows():
                broker = str(raw.get("BROKER_CODE") or "").strip().upper()
                if not broker:
                    continue
                participant = {
                    "broker": broker,
                    "value": _scalar(raw.get("NET_VALUE")),
                    "avg_price": _scalar(raw.get("AVG_PRICE")),
                    "classification": str(raw.get("BROKER_TYPE") or "").strip(),
                }
                sides[side].append({key: value for key, value in participant.items() if value not in (None, "")})
        result[key] = {"top_buyers": sides["BUY"], "top_sellers": sides["SELL"]}
    return result, "AVAILABLE"


def _overlay_exact_raw(
    summary: dict[str, dict[str, Any]],
    raw: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    raw_status: str,
) -> None:
    """Raw participants are authoritative whenever the exact snapshot is valid."""
    for symbol in set(summary) | set(raw):
        item = summary.setdefault(symbol, {"symbol": symbol})
        # A summary can name brokers, but rank/value/type shown in Final
        # Watchlist must come from the exact raw PRIMARY/TODAY capture. Do not
        # expose an unverified summary fallback when that capture is missing or
        # invalid; callers can surface the accompanying raw-status instead.
        if raw_status != "AVAILABLE":
            item["top_buyers"] = []
            item["top_sellers"] = []
            continue
        participants = raw.get(symbol, {"top_buyers": [], "top_sellers": []})
        item["top_buyers"] = list(participants.get("top_buyers") or [])
        item["top_sellers"] = list(participants.get("top_sellers") or [])


def active_primary_raw_snapshot_path(
    broker_summary_path: str | Path,
    *,
    trade_date: str,
    project_root: str | Path | None = None,
    latest_selected_path: str | Path | None = None,
) -> Path | None:
    """Return the explicitly selected PRIMARY raw snapshot, or ``None``.

    A legacy no-sidecar invocation may use the sibling canonical raw path. Once
    a sidecar/selected-period record exists, omission of its raw snapshot is a
    fail-closed condition rather than permission to fall back to TODAY raw.
    """
    root = Path(project_root) if project_root is not None else Path.cwd()
    summary_path = Path(broker_summary_path)
    if not summary_path.is_absolute():
        summary_path = root / summary_path
    selected, _ = _selected_period_context(
        summary_path,
        trade_date=trade_date,
        project_root=root,
        latest_selected_path=latest_selected_path,
    )
    if selected:
        return _usable_path(
            _resolve_path(
                selected.get("primary_raw_snapshot_path") or selected.get("raw_snapshot_path"),
                project_root=root,
            )
        )
    return _usable_path(summary_path.parent / "BROKER_RAW_LATEST.csv")


def load_broker_period_view(
    broker_summary_path: str | Path,
    *,
    trade_date: str,
    project_root: str | Path | None = None,
    latest_selected_path: str | Path | None = None,
) -> BrokerPeriodView:
    """Load one canonical broker-period read model.

    The active sidecar beside ``BROKER_SUMMARY_LATEST.csv`` is authoritative.
    ``latest_selected.json`` is a compatibility lineage fallback only. No
    broker-history DB or broker-multiday artifact is consulted.
    """
    root = Path(project_root) if project_root is not None else Path.cwd()
    summary_path = Path(broker_summary_path)
    if not summary_path.is_absolute():
        summary_path = root / summary_path
    selected, selected_path = _selected_period_context(
        summary_path,
        trade_date=trade_date,
        project_root=root,
        latest_selected_path=latest_selected_path,
    )

    period_type = str(selected.get("broker_period_type") or "").strip().upper()
    metadata = {
        "broker_period_type": period_type,
        "broker_period_start": str(selected.get("broker_period_start") or selected.get("from_date") or "")[:10],
        "broker_period_end": str(selected.get("broker_period_end") or selected.get("to_date") or selected.get("broker_date") or "")[:10],
        "broker_trading_days": selected.get("broker_trading_days", ""),
        "broker_session_dates": list(selected.get("broker_session_dates") or []),
        "broker_snapshot_id": selected.get("broker_snapshot_id") or selected.get("snapshot_id") or "",
        "broker_period_source": str(selected.get("broker_period_source") or "").upper(),
        "broker_coverage": selected.get("broker_coverage", selected.get("coverage_ratio", "")),
        "broker_period_coverage": selected.get("broker_period_coverage", selected.get("broker_session_coverage", "")),
        "broker_coverage_text": selected.get("broker_coverage_text", ""),
        "broker_coverage_status": selected.get("broker_coverage_status", ""),
        "broker_freshness_status": str(selected.get("broker_freshness_status") or selected.get("freshness_status") or "").upper(),
        "today_pulse_snapshot_id": selected.get("today_pulse_snapshot_id") or selected.get("daily_capture_snapshot_id") or "",
        "today_pulse_source": str(selected.get("today_pulse_source") or "").upper(),
    }
    metadata = {key: value for key, value in metadata.items() if value not in (None, "", [], {})}

    primary_snapshot = _usable_path(
        _resolve_path(
            selected.get("primary_summary_snapshot_path") or selected.get("summary_snapshot_path"),
            project_root=root,
        )
    )
    if primary_snapshot is None and not selected:
        primary_snapshot = _usable_path(summary_path)

    daily_snapshot = _usable_path(
        _resolve_path(
            selected.get("daily_capture_summary_snapshot_path") or selected.get("today_pulse_summary_snapshot_path"),
            project_root=root,
        )
    )
    if period_type in {"1D", "1DAY", "DAY"}:
        daily_snapshot = None

    primary_raw = active_primary_raw_snapshot_path(
        summary_path,
        trade_date=trade_date,
        project_root=root,
        latest_selected_path=latest_selected_path,
    )
    daily_raw = _usable_path(
        _resolve_path(
            selected.get("daily_capture_raw_snapshot_path") or selected.get("today_pulse_raw_snapshot_path"),
            project_root=root,
        )
    )
    if period_type in {"1D", "1DAY", "DAY"}:
        daily_raw = None

    expected_primary_date = str(metadata.get("broker_period_end") or trade_date)[:10]
    primary = _summary_map(primary_snapshot)
    today = _summary_map(daily_snapshot)
    primary_participants, primary_raw_status = _raw_participant_map(
        primary_raw,
        expected_trade_date=expected_primary_date,
    )
    today_participants, today_raw_status = _raw_participant_map(
        daily_raw,
        expected_trade_date=str(trade_date)[:10],
    )
    _overlay_exact_raw(primary, primary_participants, raw_status=primary_raw_status)
    _overlay_exact_raw(today, today_participants, raw_status=today_raw_status)
    metadata["primary_raw_status"] = primary_raw_status
    if period_type not in {"1D", "1DAY", "DAY"}:
        metadata["today_raw_status"] = today_raw_status

    inputs: list[str] = []
    for path in (selected_path, primary_snapshot, primary_raw, daily_snapshot, daily_raw):
        if path is not None and path.exists():
            resolved = str(path.resolve())
            if resolved not in inputs:
                inputs.append(resolved)

    return BrokerPeriodView(
        trade_date=str(trade_date)[:10],
        metadata=metadata,
        primary_by_symbol=primary,
        today_by_symbol=today,
        input_paths=tuple(inputs),
    )


__all__ = ["BrokerPeriodView", "active_primary_raw_snapshot_path", "load_broker_period_view"]
