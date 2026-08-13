#!/usr/bin/env python3
from __future__ import annotations

"""Lifecycle-consistent facade for the baseline outcome tracker.

All non-lifecycle behavior is delegated to outcome_tracker_baseline unchanged.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from typing import Any
import sqlite3

import pandas as pd

from modules.analytics import outcome_tracker_baseline as _baseline
from modules.analytics.lifecycle_contract import (
    LIFECYCLE_CONTRACT_VERSION,
    evaluate_trade_path,
    lifecycle_state_dict,
    prepare_lifecycle_bars,
)

# Save immutable delegates before the facade installs runtime hooks back into
# the baseline module. Calling _baseline.connect after that hook would recurse.
_baseline_connect = _baseline.connect
_baseline_lifecycle_telegram = _baseline.lifecycle_telegram
_baseline_active_telegram = _baseline.active_telegram

for _name in dir(_baseline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_baseline, _name)


_LIFECYCLE_COLUMNS = {
    "current_stop": "REAL",
    "trailing_active": "INTEGER DEFAULT 0",
    "tp1_hit_date": "TEXT",
    "last_evaluated_date": "TEXT",
    "lifecycle_contract_version": "TEXT",
}


def connect(db_path: Path) -> sqlite3.Connection:
    conn = _baseline_connect(db_path)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(signal_outcome_ledger)")}
    for name, ddl in _LIFECYCLE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE signal_outcome_ledger ADD COLUMN {name} {ddl}")
    conn.commit()
    return conn


def load_prices(path: Path) -> pd.DataFrame:
    return prepare_lifecycle_bars(_baseline.read_csv(path))


def _record_get(record: sqlite3.Row | dict[str, Any], name: str, default: Any = None) -> Any:
    try:
        return record[name]
    except (KeyError, IndexError):
        return default


def _close_metrics(price: pd.DataFrame, entry_date: str, entry_price: float) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    entry_ts = pd.Timestamp(entry_date)
    positions = price.index[price["Date"] == entry_ts].tolist()
    if not positions:
        return metrics
    entry_idx = int(positions[0])
    for days in (1, 3, 5, 7):
        idx = entry_idx + days
        close = float(price.loc[idx, "Close"]) if idx < len(price) else None
        metrics[f"close_d{days}"] = close
        metrics[f"return_d{days}"] = _baseline.ret_pct(close, entry_price)
    return metrics


def evaluate_record(record: sqlite3.Row, price: pd.DataFrame) -> dict[str, Any]:
    update: dict[str, Any] = {"updated_at": _baseline.now_text()}
    status = _baseline.norm_text(record["current_status"]).upper()
    if status not in _baseline.ACTIVE_STATUSES or price.empty:
        return update

    price = prepare_lifecycle_bars(price)
    if price.empty:
        return update

    trigger_type = _baseline.norm_text(record["trigger_type"]).upper()
    entry_date = _baseline.norm_text(_record_get(record, "entry_date", ""))
    entry_price = _baseline.as_float(_record_get(record, "entry_price"))
    events: list[dict[str, Any]] = []

    if status == "WAITING_TRIGGER":
        signal_date = pd.Timestamp(record["signal_date"])
        post_signal = price[price["Date"] > signal_date].copy().reset_index(drop=True)
        if post_signal.empty:
            return update
        try:
            last_date = post_signal["Date"].max().date()
            expected_sessions = _baseline.trading_sessions_between(signal_date.date(), last_date)
            expiry = _baseline.waiting_expiry_sessions(record["trigger_expiry_days"])
            allowed_sessions = set(expected_sessions[1 : expiry + 1])
            trigger_window = post_signal[
                post_signal["Date"].dt.date.astype(str).isin(allowed_sessions)
            ].copy().reset_index(drop=True)
        except (TypeError, ValueError, OSError):
            trigger_window = pd.DataFrame()
            expiry = _baseline.waiting_expiry_sessions(record["trigger_expiry_days"])

        if trigger_window.empty:
            return update

        trigger = _baseline.first_trigger(record, trigger_window)
        if trigger is None:
            if len(trigger_window) >= expiry:
                bar = trigger_window.iloc[expiry - 1]
                update.update({
                    "current_status": "EXPIRED",
                    "final_outcome": "EXPIRED",
                    "exit_date": bar["Date"].date().isoformat(),
                    "exit_price": float(bar["Close"]),
                    "exit_reason": "TRIGGER_NOT_REACHED_WITHIN_WINDOW",
                    "lifecycle_contract_version": LIFECYCLE_CONTRACT_VERSION,
                    "_events": [{
                        "event_type": "EXPIRED",
                        "event_date": bar["Date"].date().isoformat(),
                        "event_price": float(bar["Close"]),
                        "event_reason": "TRIGGER_NOT_REACHED_WITHIN_WINDOW",
                        "new_status": "EXPIRED",
                    }],
                })
            return update

        trigger_idx, entry_price = trigger
        entry_date = trigger_window.loc[trigger_idx, "Date"].date().isoformat()
        geometry = _baseline.validate_plan_geometry(
            entry_price,
            record["stop_loss"],
            record["take_profit_1"],
            record["take_profit_2"],
        )
        if not geometry.valid:
            reason = "PLAN_GEOMETRY_INVALID_AT_ENTRY:" + "+".join(geometry.reasons)
            update.update({
                "current_status": "INVALIDATED_BEFORE_ENTRY",
                "final_outcome": "INVALIDATED",
                "trigger_date": entry_date,
                "entry_date": None,
                "entry_price": None,
                "exit_date": entry_date,
                "exit_price": None,
                "exit_reason": reason,
                "lifecycle_contract_version": LIFECYCLE_CONTRACT_VERSION,
                "_events": [{
                    "event_type": "INVALIDATED_BEFORE_ENTRY",
                    "event_date": entry_date,
                    "event_price": entry_price,
                    "event_reason": reason,
                    "new_status": "INVALIDATED_BEFORE_ENTRY",
                }],
            })
            return update

        update.update({
            "current_status": "OPEN",
            "final_outcome": "OPEN",
            "trigger_date": entry_date,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "current_stop": _baseline.as_float(record["stop_loss"]),
            "trailing_active": 0,
            "tp1_hit_date": None,
            "lifecycle_contract_version": LIFECYCLE_CONTRACT_VERSION,
        })
        events.append({
            "event_type": "ENTRY_TRIGGERED",
            "event_date": entry_date,
            "event_price": entry_price,
            "event_reason": trigger_type,
            "new_status": "OPEN",
        })
        full_positions = price.index[price["Date"] == pd.Timestamp(entry_date)].tolist()
        if not full_positions:
            update["_events"] = events
            return update
        full_entry_idx = int(full_positions[0])
        eval_start = full_entry_idx + 1 if trigger_type == "CLOSE_ABOVE" else full_entry_idx
        bars = price.iloc[eval_start:].copy().reset_index(drop=True)
        prior_holding = 0
        prior_stop = _baseline.as_float(record["stop_loss"])
        prior_tp1 = False
        prior_trailing = False
        prior_tp1_date = ""
        prior_max = None
        prior_min = None
        prior_last_eval = entry_date if trigger_type == "CLOSE_ABOVE" else ""
    else:
        if not entry_date or entry_price is None:
            return update
        last_eval = _baseline.norm_text(_record_get(record, "last_evaluated_date", ""))
        if last_eval:
            bars = price[price["Date"] > pd.Timestamp(last_eval)].copy().reset_index(drop=True)
        else:
            comparator = price["Date"] > pd.Timestamp(entry_date) if trigger_type == "CLOSE_ABOVE" else price["Date"] >= pd.Timestamp(entry_date)
            bars = price[comparator].copy().reset_index(drop=True)
        prior_holding = _baseline.as_int(_record_get(record, "holding_days"), 0)
        prior_stop = _baseline.as_float(_record_get(record, "current_stop")) or _baseline.as_float(record["stop_loss"])
        prior_tp1 = bool(_baseline.as_int(_record_get(record, "tp1_hit"), 0))
        prior_trailing = bool(_baseline.as_int(_record_get(record, "trailing_active"), int(prior_tp1)))
        prior_tp1_date = _baseline.norm_text(_record_get(record, "tp1_hit_date", ""))
        prior_max = _baseline.as_float(_record_get(record, "max_price"))
        prior_min = _baseline.as_float(_record_get(record, "min_price"))
        prior_last_eval = last_eval

    if entry_price is None:
        return update

    update.update(_close_metrics(price, entry_date, entry_price))
    stop = _baseline.as_float(record["stop_loss"])
    tp1 = _baseline.as_float(record["take_profit_1"])
    tp2 = _baseline.as_float(record["take_profit_2"])
    if stop is None or tp1 is None or tp2 is None:
        return update

    state = evaluate_trade_path(
        bars,
        entry_price=entry_price,
        initial_stop=stop,
        target_1=tp1,
        target_2=tp2,
        max_hold_days=max(_baseline.as_int(record["max_hold_days"], 20), 1),
        current_stop=prior_stop,
        holding_days=prior_holding,
        tp1_hit=prior_tp1,
        trailing_active=prior_trailing,
        tp1_hit_date=prior_tp1_date,
        max_price=prior_max,
        min_price=prior_min,
        last_evaluated_date=prior_last_eval,
    )
    state_update = lifecycle_state_dict(state)
    lifecycle_events = state_update.pop("events")
    update.update(state_update)
    events.extend(lifecycle_events)
    update["_events"] = events
    return update


def update_outcomes(conn: sqlite3.Connection, historical_dir: Path) -> dict[str, int]:
    counters = {"evaluated": 0, "updated": 0, "missing_price": 0}
    records = conn.execute(
        "SELECT * FROM signal_outcome_ledger "
        "WHERE current_status IN ('WAITING_TRIGGER','OPEN') ORDER BY signal_date,symbol"
    ).fetchall()
    for record in records:
        path = historical_dir / f"{record['symbol']}.csv"
        prices = load_prices(path)
        if prices.empty:
            counters["missing_price"] += 1
            conn.execute(
                "UPDATE signal_outcome_ledger SET updated_at=?, "
                "exit_reason=COALESCE(exit_reason,'PRICE_DATA_MISSING') WHERE signal_id=?",
                (_baseline.now_text(), record["signal_id"]),
            )
            continue
        counters["evaluated"] += 1
        changes = evaluate_record(record, prices)
        events = list(changes.pop("_events", []))
        if changes:
            assignments = ",".join(f"{key}=?" for key in changes)
            conn.execute(
                f"UPDATE signal_outcome_ledger SET {assignments} WHERE signal_id=?",
                [*changes.values(), record["signal_id"]],
            )
            counters["updated"] += 1

        previous_status = _baseline.norm_text(record["current_status"])
        rolling_status = previous_status
        for event in events:
            event_type = _baseline.norm_text(event.get("event_type")).upper()
            event_status = _baseline.norm_text(event.get("new_status"), rolling_status)
            _baseline.record_lifecycle_event(
                conn,
                signal_id=record["signal_id"],
                symbol=record["symbol"],
                event_type=event_type,
                previous_status=rolling_status,
                new_status=event_status,
                event_date=_baseline.norm_text(event.get("event_date"), record["signal_date"]),
                event_price=event.get("event_price"),
                event_reason=_baseline.norm_text(event.get("event_reason")),
            )
            if event_status == "CLOSED" and event_type in {"TP2_HIT", "STOP_LOSS_HIT", "MAX_HOLD_EXIT"}:
                _baseline.record_lifecycle_event(
                    conn,
                    signal_id=record["signal_id"],
                    symbol=record["symbol"],
                    event_type="CLOSED",
                    previous_status=rolling_status,
                    new_status="CLOSED",
                    event_date=_baseline.norm_text(event.get("event_date"), record["signal_date"]),
                    event_price=event.get("event_price"),
                    event_reason=_baseline.norm_text(event.get("event_reason")),
                )
            rolling_status = event_status
    conn.commit()
    return counters


_baseline_status_changes_telegram = _baseline._status_changes_telegram


def _status_changes_telegram(events, *, max_events: int = 20) -> str:
    text = _baseline_status_changes_telegram(events, max_events=max_events)
    return text.replace(
        "🎯 <b>TP1 HIT</b>\n💰 Exit    :",
        "🎯 <b>TP1 HIT — TRAILING ACTIVE</b>\n💰 Level   :",
    )


def _call_telegram_delegate(delegate, args):
    """Honor monkeypatches on the public facade without duplicating baseline I/O."""
    previous = _baseline.send_telegram
    try:
        _baseline.send_telegram = globals()["send_telegram"]
        return delegate(args)
    finally:
        _baseline.send_telegram = previous


def lifecycle_telegram(args):
    return _call_telegram_delegate(_baseline_lifecycle_telegram, args)


def active_telegram(args):
    return _call_telegram_delegate(_baseline_active_telegram, args)


_baseline.connect = connect
_baseline.load_prices = load_prices
_baseline.evaluate_record = evaluate_record
_baseline.update_outcomes = update_outcomes
_baseline._status_changes_telegram = _status_changes_telegram
_baseline.lifecycle_telegram = lifecycle_telegram
_baseline.active_telegram = active_telegram

if __name__ == "__main__":
    raise SystemExit(_baseline.main())
