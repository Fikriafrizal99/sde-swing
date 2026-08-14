from __future__ import annotations

"""Canonical SDE Swing lifecycle semantics.

This module owns execution semantics only. It never calculates entry zones,
initial stop-loss levels, TP1, or TP2. Those price levels remain owned by the
existing entry/exit-plan engine and frozen quant contract.
"""

from dataclasses import dataclass, field
from typing import Any

import math
import pandas as pd

from modules.analytics.execution_integrity import economic_outcome, target_is_profitable

LIFECYCLE_CONTRACT_VERSION = "SDE_SWING_LIFECYCLE_V1"
OUTCOME_VOCABULARY = {"WIN", "LOSS", "AMBIGUOUS", "OPEN"}


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.date().isoformat()


def prepare_lifecycle_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize OHLC and calculate the same EMA20/ATR14 used by Exit Engine."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    mapping = {str(c).strip().lower(): c for c in frame.columns}
    aliases = {
        "Date": ("date", "datetime", "timestamp"),
        "Open": ("open",),
        "High": ("high",),
        "Low": ("low",),
        "Close": ("close",),
    }
    resolved: dict[str, str] = {}
    for target, names in aliases.items():
        source = next((mapping.get(name) for name in names if mapping.get(name)), None)
        if source is None:
            return pd.DataFrame()
        resolved[target] = source
    out = pd.DataFrame({
        "Date": pd.to_datetime(frame[resolved["Date"]], errors="coerce"),
        "Open": pd.to_numeric(frame[resolved["Open"]], errors="coerce"),
        "High": pd.to_numeric(frame[resolved["High"]], errors="coerce"),
        "Low": pd.to_numeric(frame[resolved["Low"]], errors="coerce"),
        "Close": pd.to_numeric(frame[resolved["Close"]], errors="coerce"),
    })
    out = out.dropna().drop_duplicates("Date").sort_values("Date").reset_index(drop=True)
    if out.empty:
        return out
    prev_close = out["Close"].shift(1)
    tr = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - prev_close).abs(),
        (out["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["ATR14"] = tr.rolling(14).mean()
    out["EMA20"] = out["Close"].ewm(span=20, adjust=False).mean()
    return out


def entry_from_zone(bar: pd.Series, low: float, high: float) -> float:
    opened = float(bar["Open"])
    if low <= opened <= high:
        return opened
    if opened > high:
        return high
    if opened < low:
        return low
    return (low + high) / 2.0


def trigger_spec_from_plan(plan: dict[str, Any] | pd.Series, setup_type: str = "") -> tuple[str, float | None]:
    get = plan.get
    status = str(get("Plan_Status", get("Entry_Status", "")) or "").strip().upper()
    reason = str(get("Rejection_Reason", "") or "").strip().upper()
    if status == "CONDITIONAL" and reason == "MINOR_RESISTANCE_NEAR":
        trigger = _num(get("Minor_Resistance", get("Nearest_Resistance")))
        return ("CLOSE_ABOVE", trigger) if trigger is not None else ("INVALID", None)
    if status in {"ACCEPT", "CONDITIONAL"}:
        low = _num(get("Entry_Zone_Low"))
        high = _num(get("Entry_Zone_High"))
        if low is not None and high is not None and low > 0 and high >= low:
            return "ENTRY_ZONE_TOUCH", None
        normalized_setup = str(setup_type or get("Setup_Type", "") or "").upper().replace("_", " ")
        if normalized_setup in {"BREAKOUT", "TREND CONTINUATION"}:
            trigger = _num(get("Minor_Resistance", get("Nearest_Resistance")))
            return ("CLOSE_ABOVE", trigger) if trigger is not None else ("INVALID", None)
    return "INVALID", None


def first_trigger(
    bars: pd.DataFrame,
    *,
    trigger_type: str,
    trigger_price: float | None = None,
    entry_zone_low: float | None = None,
    entry_zone_high: float | None = None,
    expiry_sessions: int = 7,
) -> tuple[int, float] | None:
    window = bars.head(max(1, int(expiry_sessions)))
    if window.empty:
        return None
    trigger_type = str(trigger_type or "").upper()
    if trigger_type == "CLOSE_ABOVE":
        trigger = _num(trigger_price)
        if trigger is None:
            return None
        matches = window.index[window["Close"] > trigger].tolist()
        if not matches:
            return None
        idx = int(matches[0])
        return idx, float(bars.loc[idx, "Close"])
    if trigger_type == "ENTRY_ZONE_TOUCH":
        low = _num(entry_zone_low)
        high = _num(entry_zone_high)
        if low is None or high is None:
            return None
        matches = window.index[(window["Low"] <= high) & (window["High"] >= low)].tolist()
        if not matches:
            return None
        idx = int(matches[0])
        return idx, entry_from_zone(bars.loc[idx], low, high)
    return None


@dataclass
class LifecycleState:
    entry_price: float
    initial_stop: float
    target_1: float
    target_2: float
    max_hold_days: int = 20
    current_stop: float | None = None
    holding_days: int = 0
    tp1_hit: bool = False
    tp2_hit: bool = False
    sl_hit: bool = False
    trailing_active: bool = False
    tp1_hit_date: str = ""
    max_price: float | None = None
    min_price: float | None = None
    last_evaluated_date: str = ""
    current_status: str = "OPEN"
    final_outcome: str = "OPEN"
    exit_date: str = ""
    exit_price: float | None = None
    exit_reason: str = ""
    realized_return_pct: float | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.current_stop is None:
            self.current_stop = self.initial_stop
        self.max_hold_days = max(1, int(self.max_hold_days or 20))


def _ret_pct(price: float | None, entry: float | None) -> float | None:
    if price is None or entry is None or entry == 0:
        return None
    return (float(price) / float(entry) - 1.0) * 100.0


def _event(state: LifecycleState, event_type: str, bar: pd.Series, price: float | None, reason: str, *, status: str | None = None) -> None:
    state.events.append({
        "event_type": event_type,
        "event_date": _date_text(bar["Date"]),
        "event_price": price,
        "event_reason": reason,
        "new_status": status or state.current_status,
    })


def advance_lifecycle(state: LifecycleState, bar: pd.Series) -> LifecycleState:
    """Advance one completed daily candle using conservative event ordering.

    Stop/target checks use the stop that existed before this candle. Any
    breakeven/trailing adjustment calculated from the candle close becomes
    effective on the next candle, avoiding look-ahead.
    """
    if state.current_status != "OPEN":
        return state

    high = float(bar["High"])
    low = float(bar["Low"])
    close = float(bar["Close"])
    date_text = _date_text(bar["Date"])
    state.holding_days += 1
    state.last_evaluated_date = date_text
    state.max_price = high if state.max_price is None else max(state.max_price, high)
    state.min_price = low if state.min_price is None else min(state.min_price, low)

    stop_before = float(state.current_stop if state.current_stop is not None else state.initial_stop)
    hit_stop = low <= stop_before
    hit_tp1 = target_is_profitable(state.entry_price, state.target_1) and high >= state.target_1
    hit_tp2 = target_is_profitable(state.entry_price, state.target_2) and high >= state.target_2

    if hit_stop:
        state.sl_hit = True
        reason = "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE" if (hit_tp1 or hit_tp2) else "STOP_LOSS_HIT"
        state.current_status = "CLOSED"
        state.exit_date = date_text
        state.exit_price = stop_before
        state.exit_reason = reason
        state.realized_return_pct = _ret_pct(stop_before, state.entry_price)
        state.final_outcome = economic_outcome(state.realized_return_pct)
        _event(state, "STOP_LOSS_HIT", bar, stop_before, reason, status="CLOSED")
        return state

    if hit_tp2:
        if not state.tp1_hit:
            state.tp1_hit = True
            state.trailing_active = True
            state.tp1_hit_date = date_text
            _event(state, "TP1_HIT", bar, state.target_1, "TARGET_1_HIT_TRAIL_ACTIVE", status="OPEN")
        state.tp2_hit = True
        state.current_status = "CLOSED"
        state.exit_date = date_text
        state.exit_price = state.target_2
        state.exit_reason = "TP2_HIT"
        state.realized_return_pct = _ret_pct(state.target_2, state.entry_price)
        state.final_outcome = economic_outcome(state.realized_return_pct)
        _event(state, "TP2_HIT", bar, state.target_2, "TP2_HIT", status="CLOSED")
        return state

    if hit_tp1 and not state.tp1_hit:
        state.tp1_hit = True
        state.trailing_active = True
        state.tp1_hit_date = date_text
        _event(state, "TP1_HIT", bar, state.target_1, "TARGET_1_HIT_TRAIL_ACTIVE", status="OPEN")

    risk = state.entry_price - state.initial_stop
    r_multiple = (close - state.entry_price) / risk if risk > 0 else 0.0
    new_stop = stop_before
    if r_multiple >= 1.0:
        new_stop = max(new_stop, state.entry_price)
    ema20 = _num(bar.get("EMA20"))
    atr14 = _num(bar.get("ATR14"))
    if r_multiple >= 1.5 and ema20 is not None and atr14 is not None:
        new_stop = max(new_stop, ema20 - 0.5 * atr14)
    state.current_stop = new_stop

    if state.holding_days >= state.max_hold_days:
        state.current_status = "CLOSED"
        state.exit_date = date_text
        state.exit_price = close
        state.exit_reason = "MAX_HOLD_EXIT"
        state.realized_return_pct = _ret_pct(close, state.entry_price)
        state.final_outcome = economic_outcome(state.realized_return_pct)
        _event(state, "MAX_HOLD_EXIT", bar, close, "MAX_HOLD_EXIT", status="CLOSED")
        return state

    state.final_outcome = "OPEN"
    return state


def evaluate_trade_path(
    bars: pd.DataFrame,
    *,
    entry_price: float,
    initial_stop: float,
    target_1: float,
    target_2: float,
    max_hold_days: int = 20,
    current_stop: float | None = None,
    holding_days: int = 0,
    tp1_hit: bool = False,
    trailing_active: bool = False,
    tp1_hit_date: str = "",
    max_price: float | None = None,
    min_price: float | None = None,
    last_evaluated_date: str = "",
) -> LifecycleState:
    state = LifecycleState(
        entry_price=float(entry_price),
        initial_stop=float(initial_stop),
        target_1=float(target_1),
        target_2=float(target_2),
        max_hold_days=max_hold_days,
        current_stop=current_stop,
        holding_days=holding_days,
        tp1_hit=bool(tp1_hit),
        trailing_active=bool(trailing_active or tp1_hit),
        tp1_hit_date=tp1_hit_date,
        max_price=max_price,
        min_price=min_price,
        last_evaluated_date=last_evaluated_date,
    )
    if bars is None or bars.empty:
        return state
    for _, bar in bars.iterrows():
        advance_lifecycle(state, bar)
        if state.current_status == "CLOSED":
            break
    return state


def lifecycle_state_dict(state: LifecycleState) -> dict[str, Any]:
    mfe = _ret_pct(state.max_price, state.entry_price)
    mae = _ret_pct(state.min_price, state.entry_price)
    return {
        "current_status": state.current_status,
        "final_outcome": state.final_outcome if state.current_status == "CLOSED" else "OPEN",
        "current_stop": state.current_stop,
        "tp1_hit": int(state.tp1_hit),
        "tp2_hit": int(state.tp2_hit),
        "sl_hit": int(state.sl_hit),
        "trailing_active": int(state.trailing_active),
        "tp1_hit_date": state.tp1_hit_date,
        "holding_days": state.holding_days,
        "max_price": state.max_price,
        "min_price": state.min_price,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "last_evaluated_date": state.last_evaluated_date,
        "exit_date": state.exit_date or None,
        "exit_price": state.exit_price,
        "exit_reason": state.exit_reason or None,
        "realized_return_pct": state.realized_return_pct,
        "lifecycle_contract_version": LIFECYCLE_CONTRACT_VERSION,
        "events": list(state.events),
    }
