from __future__ import annotations

"""Canonical compact Telegram card for Final Watchlist detail.

This module is presentation-only. It reads already-produced engine/broker facts
and never recalculates scores, decisions, trade-plan levels, or broker states.
The compact contract is intentionally one short Telegram photo caption/card.
"""

import html
import json
from datetime import datetime
from typing import Any, Mapping


_MISSING = {"", "nan", "none", "null", "engine_data_not_available", "data_not_available", "n/a"}


def _raw(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in _MISSING else text


def _pick(row: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    lookup = {str(key).strip().lower(): value for key, value in dict(row or {}).items()}
    for key in keys:
        value = lookup.get(str(key).strip().lower())
        if _raw(value):
            return value
    return default


def _num(value: Any) -> float | None:
    text = _raw(value)
    if not text:
        return None
    try:
        if ":" in text:
            text = text.rsplit(":", 1)[-1].strip()
        return float(text.replace(",", ""))
    except Exception:
        return None


def _price(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    return f"{number:,.0f}".replace(",", ".")


def _rr(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    return f"{number:.2f}".replace(".", ",")


def _pct(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    return f"{number:+.2f}%".replace(".", ",")


def _confidence(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    if 0 <= number <= 1:
        number *= 100.0
    return f"{number:.0f}%"


def _score(value: Any) -> str:
    number = _num(value)
    return f"{number:.0f}" if number is not None else "0"


def _compact_number(value: float, decimals: int = 2) -> str:
    rendered = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return rendered.replace(".", ",")


def _money(value: Any, *, with_rp: bool = True) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    sign = "+" if number > 0 else "-" if number < 0 else ""
    amount = abs(number)
    if amount >= 1_000_000_000:
        body = f"{_compact_number(amount / 1_000_000_000)}B"
    elif amount >= 1_000_000:
        body = f"{_compact_number(amount / 1_000_000)}M"
    elif amount >= 1_000:
        body = f"{_compact_number(amount / 1_000)}K"
    else:
        body = _compact_number(amount, 0)
    prefix = "Rp" if with_rp else ""
    return f"{sign}{prefix}{body}"


def _enum(value: Any, fallback: str = "N/A", *, upper: bool = False, title: bool = False) -> str:
    text = _raw(value)
    if not text:
        return fallback
    text = " ".join(text.replace("_", " ").split())
    if upper:
        return text.upper()
    if title:
        return text.title()
    return text


def _date(value: Any) -> str:
    text = _raw(value)
    if not text:
        return "N/A"
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except Exception:
            parsed = None
    if parsed is None:
        return text
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{parsed.day:02d} {months[parsed.month]} {parsed.year}"


def _participants(value: Any) -> str:
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except Exception:
                raw = []
        else:
            raw = []
    items = list(raw or []) if isinstance(raw, (list, tuple)) else []
    rendered: list[str] = []
    for item in items[:3]:
        if not isinstance(item, Mapping):
            continue
        code = _enum(item.get("broker") or item.get("code") or item.get("name"), "", upper=True)
        if not code:
            continue
        amount = _money(item.get("value") or item.get("net_value") or item.get("amount"), with_rp=False)
        rendered.append(f"{html.escape(code)} {html.escape(amount)}" if amount != "N/A" else html.escape(code))
    return " • ".join(rendered) if rendered else "N/A"


def _buy_sell_days(row: Mapping[str, Any]) -> tuple[str, str]:
    buy = _num(_pick(row, "buy_days", "broker_buy_days", "Buy_Days", "BUY_DAYS"))
    sell = _num(_pick(row, "sell_days", "broker_sell_days", "Sell_Days", "SELL_DAYS"))
    return (
        str(int(round(buy))) if buy is not None else "N/A",
        str(int(round(sell))) if sell is not None else "N/A",
    )


def _action(row: Mapping[str, Any]) -> str:
    resistance = _num(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    current = _num(_pick(row, "last_price", "current_price", "Reference_Close"))
    low = _num(_pick(row, "entry_low", "Entry_Zone_Low"))
    high = _num(_pick(row, "entry_high", "Entry_Zone_High"))
    phase = _enum(_pick(row, "phase", "execution_state", "Execution_Status", "technical_status"), "", upper=True)

    if current is not None and high is not None and current > high and low is not None:
        return f"⚠️ Jangan chase. Tunggu pullback ke {_price(low)}–{_price(high)}."
    if resistance is not None and any(token in phase for token in ("WAIT", "TRIGGER", "CONDITIONAL", "MONITOR")):
        return f"⚠️ Tunggu break >{_price(resistance)}. Jangan chase."
    if low is not None and high is not None:
        return f"⚠️ Tunggu trigger valid di {_price(low)}–{_price(high)}. Jangan chase."
    return "⚠️ Tunggu trigger valid. Jangan chase."


def format_watchlist_detail(row: Mapping[str, Any]) -> str:
    """Render the locked compact Final Watchlist Telegram card."""
    symbol = html.escape(_enum(_pick(row, "symbol", "Symbol"), "N/A", upper=True))
    decision = html.escape(_enum(_pick(row, "decision", "Decision_Status_Final"), "N/A", upper=True))
    confidence = html.escape(_confidence(_pick(row, "confidence", "Final_Score", "Final_Score_V3")))
    setup = html.escape(_enum(_pick(row, "setup", "Setup_Type"), "N/A", upper=True))
    analysis_date = html.escape(_date(_pick(row, "analysis_date", "trade_date", "Trade_Date")))

    current = _price(_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = _price(_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = _price(_pick(row, "entry_high", "Entry_Zone_High"))
    stop = _price(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = _price(_pick(row, "target_1", "Target_1"))
    tp2 = _price(_pick(row, "target_2", "Target_2"))
    rr = _rr(_pick(row, "risk_reward", "Target_2_RR", "Target_1_RR"))

    trend = html.escape(_enum(_pick(row, "trend", "Technical_Regime"), "N/A", title=True))
    phase = html.escape(_enum(_pick(row, "phase", "execution_state", "Execution_Status", "technical_status"), "N/A", upper=True))
    support = _price(_pick(row, "support", "Support_Level"))
    resistance = _price(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))

    broker = html.escape(_enum(_pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"), "INSUFFICIENT DATA", upper=True))
    broker_score = _score(_pick(row, "broker_score", "Broker_Score"))
    net_flow = html.escape(_money(_pick(row, "broker_net_flow", "net_flow", "NET_FLOW"), with_rp=True))
    buy_days, sell_days = _buy_sell_days(row)
    buy_cost = _price(_pick(row, "bandar_buy_cost", "avg_buyer_price", "weighted_broker_buy_cost"))
    vs_cost = html.escape(_pct(_pick(row, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct")))
    top_buy = _participants(_pick(row, "top_buyers", default=[]))
    top_sell = _participants(_pick(row, "top_sellers", default=[]))

    lines = [
        f"📈 <b>{symbol} | {decision} | {confidence}</b>",
        f"{setup} • {analysis_date}",
        "",
        f"💰 {current} | Entry {entry_low}–{entry_high}",
        f"🛑 {stop} | 🎯 {tp1} / {tp2} | RR 1:{rr}",
        "",
        f"📊 {trend} | {phase}",
        f"S {support} | R {resistance}",
        "",
        f"🏦 {broker} {broker_score}/100",
        f"Net {net_flow} | B/S {buy_days}/{sell_days}",
        f"Cost {buy_cost} ({vs_cost})",
        "",
        f"🟢 {top_buy}",
        f"🔴 {top_sell}",
        "",
        _action(row),
    ]
    return "\n".join(lines).strip()


__all__ = ["format_watchlist_detail"]
