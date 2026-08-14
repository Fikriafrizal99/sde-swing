#!/usr/bin/env python3
from __future__ import annotations

"""Lifecycle-consistent facade for the SDE Swing history archive.

Database schema/archival behavior remains baseline-owned. Only watchlist outcome
evaluation is replaced so it uses actual trigger entry and the canonical
lifecycle contract instead of a D7 TP1 shortcut.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from typing import Any, Iterable
import hashlib
import json

import pandas as pd

from modules.database import swing_history_db_baseline as _baseline
from modules.analytics.execution_integrity import validate_plan_geometry
from modules.analytics.lifecycle_contract import (
    evaluate_trade_path,
    first_trigger,
    lifecycle_state_dict,
    prepare_lifecycle_bars,
    trigger_spec_from_plan,
)

for _name in dir(_baseline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_baseline, _name)


def load_price_map(
    historical_dir: Path,
    symbols: Iterable[str] | None = None,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if not historical_dir.exists():
        return out
    requested = None
    if symbols is not None:
        requested = {_baseline.normalize_symbol(symbol) for symbol in symbols}
        requested.discard("")
    for file in sorted(historical_dir.glob("*.csv")):
        if requested is not None and _baseline.normalize_symbol(file.stem) not in requested:
            continue
        df = _baseline.load_csv(file)
        if df.empty:
            continue
        work = prepare_lifecycle_bars(df)
        if work.empty:
            continue
        symbol_col = _baseline.find_col(df, "Symbol", "Ticker", "EMITEN")
        symbol = (
            _baseline.normalize_symbol(df[symbol_col].iloc[0])
            if symbol_col and len(df)
            else _baseline.normalize_symbol(file.stem)
        )
        out[symbol] = work
    return out


def _num(value: Any) -> float | None:
    return _baseline.to_float(value)


def _close_after(px: pd.DataFrame, entry_idx: int, days: int) -> float | None:
    idx = entry_idx + days
    return float(px.loc[idx, "Close"]) if idx < len(px) else None


def _ret(price: float | None, entry: float | None) -> float | None:
    return (price / entry - 1.0) * 100.0 if price is not None and entry else None


def archive_watchlist_outcomes(
    conn,
    run_id: str,
    watchlist: pd.DataFrame,
    historical_dir: Path,
    entry_plans_path: Path,
    quality: str,
) -> int:
    if watchlist.empty:
        return 0
    active_rows = watchlist[watchlist["lifecycle"].isin(["NEW", "CONTINUING"])]
    if active_rows.empty:
        return 0
    active_symbols = {
        _baseline.normalize_symbol(symbol) for symbol in active_rows["symbol"].tolist()
    }
    active_symbols.discard("")
    prices = load_price_map(historical_dir, active_symbols)
    plans = _baseline.map_entry_plans(entry_plans_path)
    count = 0

    for _, row in active_rows.iterrows():
        symbol = row["symbol"]
        signal_date = pd.to_datetime(row.get("signal_date"), errors="coerce")
        if pd.isna(signal_date):
            continue
        px = prices.get(symbol, pd.DataFrame())
        plan = plans.get(symbol, {})
        try:
            row_payload = json.loads(row.get("row_json", "{}"))
        except Exception:
            row_payload = {}

        reference = _num(plan.get("Reference_Close")) or _num(row_payload.get("Close"))
        stop = _num(plan.get("Initial_Stop")) or _num(plan.get("Stop_Loss"))
        tp1 = _num(plan.get("Target_1")) or _num(plan.get("Take_Profit_1"))
        tp2 = _num(plan.get("Target_2")) or _num(plan.get("Take_Profit_2"))
        max_hold = int(_num(plan.get("Max_Hold_Days")) or 20)
        setup = str(plan.get("Setup_Type") or row_payload.get("Setup_Type") or "")
        trigger_type, trigger_price = trigger_spec_from_plan(plan, setup)

        entry_price = None
        entry_idx = None
        state_payload: dict[str, Any] = {
            "final_outcome": "OPEN",
            "tp1_hit": 0,
            "tp2_hit": 0,
            "sl_hit": 0,
            "max_price": None,
            "min_price": None,
            "mfe_pct": None,
            "mae_pct": None,
        }

        if not px.empty and trigger_type != "INVALID" and None not in (stop, tp1, tp2):
            post_signal = px[px["Date"] > signal_date].copy().reset_index(drop=True)
            trigger = first_trigger(
                post_signal,
                trigger_type=trigger_type,
                trigger_price=trigger_price,
                entry_zone_low=_num(plan.get("Entry_Zone_Low")),
                entry_zone_high=_num(plan.get("Entry_Zone_High")),
                expiry_sessions=7,
            )
            if trigger is not None:
                relative_idx, candidate_entry = trigger
                entry_date = post_signal.loc[relative_idx, "Date"]
                full_positions = px.index[px["Date"] == entry_date].tolist()
                if full_positions:
                    geometry = validate_plan_geometry(candidate_entry, stop, tp1, tp2)
                    if geometry.valid:
                        entry_price = float(candidate_entry)
                        entry_idx = int(full_positions[0])
                        eval_start = entry_idx + 1 if trigger_type == "CLOSE_ABOVE" else entry_idx
                        state = evaluate_trade_path(
                            px.iloc[eval_start:].copy().reset_index(drop=True),
                            entry_price=entry_price,
                            initial_stop=float(stop),
                            target_1=float(tp1),
                            target_2=float(tp2),
                            max_hold_days=max_hold,
                        )
                        state_payload = lifecycle_state_dict(state)

        close_d1 = _close_after(px, entry_idx, 1) if entry_idx is not None else None
        close_d3 = _close_after(px, entry_idx, 3) if entry_idx is not None else None
        close_d5 = _close_after(px, entry_idx, 5) if entry_idx is not None else None
        close_d7 = _close_after(px, entry_idx, 7) if entry_idx is not None else None

        signal_id = hashlib.sha256(
            f"{symbol}|{signal_date.date()}|{row.get('decision')}".encode("utf-8")
        ).hexdigest()[:20]
        record = {
            "signal_id": signal_id,
            "run_id": run_id,
            "symbol": symbol,
            "signal_date": signal_date.date().isoformat(),
            "decision": row.get("decision"),
            "reference_price": reference,
            "entry_price": entry_price,
            "stop_loss": stop,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "close_d1": close_d1,
            "close_d3": close_d3,
            "close_d5": close_d5,
            "close_d7": close_d7,
            "return_d1": _ret(close_d1, entry_price),
            "return_d3": _ret(close_d3, entry_price),
            "return_d5": _ret(close_d5, entry_price),
            "return_d7": _ret(close_d7, entry_price),
            "max_price_d7": state_payload.get("max_price"),
            "min_price_d7": state_payload.get("min_price"),
            "mfe": state_payload.get("mfe_pct"),
            "mae": state_payload.get("mae_pct"),
            "tp1_hit": int(state_payload.get("tp1_hit", 0)),
            "tp2_hit": int(state_payload.get("tp2_hit", 0)),
            "sl_hit": int(state_payload.get("sl_hit", 0)),
            "final_outcome": state_payload.get("final_outcome", "OPEN"),
            "data_quality_status": quality,
        }
        _baseline.upsert(conn, "watchlist_outcomes", record, ["signal_id"])
        count += 1
    return count


_baseline.load_price_map = load_price_map
_baseline.archive_watchlist_outcomes = archive_watchlist_outcomes

if __name__ == "__main__":
    raise SystemExit(_baseline.main())
