from __future__ import annotations

"""Canonical PRIMARY + TODAY broker presentation context.

This module is intentionally presentation/read-model only.  Broker Fusion remains
owner of broker scoring and the Final Decision Engine remains owner of trading
decisions.  The read model never reconstructs 3D/5D/20D windows from an
incomplete local history database.

Production contract
-------------------
* PRIMARY is the exact Stockbit period selected by the operator (1D/3D/5D/CUSTOM).
* TODAY is an independent exact real-1D capture for the same Final Watchlist date.
* When PRIMARY is 1D, TODAY is not emitted a second time.
* Alignment reuses ``primary_pulse_alignment``; no second alignment formula is
  allowed here.
* Missing local historical observations are never interpreted as missing broker
  activity.  Historical databases are audit/archive inputs only.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from modules.broker_bridge.broker_period_context import primary_pulse_alignment


_PRIMARY_SUMMARY_KEYS = {
    "net_flow": ("NET_FLOW", "Net_Flow", "Net Flow"),
    "broker_accdist": ("BROKER_ACCDIST", "Broker_AccDist", "Broker AccDist"),
    "avg_accdist": ("AVG_ACCDIST", "Avg_AccDist", "Avg AccDist"),
    "buyer_concentration": ("BUYER_CONCENTRATION", "Buyer_Concentration"),
    "seller_concentration": ("SELLER_CONCENTRATION", "Seller_Concentration"),
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
            "today_pulse_status": "AVAILABLE" if today else ("NOT_APPLICABLE" if not self.has_separate_today else "NOT_AVAILABLE"),
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


def _summary_map(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists() or not path.is_file() or path.stat().st_size <= 0:
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


def load_broker_period_view(
    broker_summary_path: str | Path,
    *,
    trade_date: str,
    project_root: str | Path | None = None,
    latest_selected_path: str | Path | None = None,
) -> BrokerPeriodView:
    """Load one canonical broker-period read model.

    The active sidecar beside ``BROKER_SUMMARY_LATEST.csv`` is authoritative.
    ``latest_selected.json`` is a backward-compatible lineage fallback only.
    No broker-history DB or broker-multiday artifact is consulted.
    """
    summary_path = Path(broker_summary_path)
    root = Path(project_root) if project_root is not None else Path.cwd()
    if not summary_path.is_absolute():
        summary_path = root / summary_path

    sidecar_path = summary_path.with_suffix(".manifest.json")
    sidecar = _read_json(sidecar_path)
    latest_path = Path(latest_selected_path) if latest_selected_path else root / "data/output/broker_snapshots/latest_selected.json"
    if not latest_path.is_absolute():
        latest_path = root / latest_path
    latest = _read_json(latest_path)

    selected = sidecar
    selected_end = str(selected.get("broker_period_end") or selected.get("to_date") or selected.get("broker_date") or "")[:10]
    if not selected or (selected_end and selected_end != str(trade_date)[:10]):
        latest_end = str(latest.get("broker_period_end") or latest.get("to_date") or latest.get("broker_date") or "")[:10]
        selected = latest if latest and (not latest_end or latest_end == str(trade_date)[:10]) else {}

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
    }
    metadata = {key: value for key, value in metadata.items() if value not in (None, "", [], {})}

    primary_snapshot = _resolve_path(selected.get("primary_summary_snapshot_path") or selected.get("summary_snapshot_path"), project_root=root)
    if primary_snapshot is None or not primary_snapshot.exists():
        primary_snapshot = summary_path if summary_path.exists() else None

    daily_snapshot = _resolve_path(
        selected.get("daily_capture_summary_snapshot_path") or selected.get("today_pulse_summary_snapshot_path"),
        project_root=root,
    )
    if period_type in {"1D", "1DAY", "DAY"}:
        daily_snapshot = None

    primary = _summary_map(primary_snapshot)
    today = _summary_map(daily_snapshot)

    inputs: list[str] = []
    for path in (sidecar_path, primary_snapshot, daily_snapshot):
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


__all__ = ["BrokerPeriodView", "load_broker_period_view"]
