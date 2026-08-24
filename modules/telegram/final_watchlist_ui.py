from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime
from typing import Any, Mapping

_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"
_MISSING = {"", "nan", "none", "null", "n/a", "engine_data_not_available", "data_not_available"}


def _raw(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in _MISSING else text


def _pick(row: Mapping[str, Any], *keys: str, default: Any = "") -> Any:
    lookup = {str(key).strip().lower(): value for key, value in row.items()}
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


def _idx_tick_size(price: float) -> float:
    price = abs(float(price))
    if price < 200:
        return 1.0
    if price < 500:
        return 2.0
    if price < 2_000:
        return 5.0
    if price < 5_000:
        return 10.0
    return 25.0


def _price(value: Any, fallback: str = "N/A") -> str:
    number = _num(value)
    if number is None or number <= 0:
        return fallback
    tick = _idx_tick_size(number)
    rounded = math.floor(number / tick + 0.5) * tick
    return f"{rounded:,.0f}".replace(",", ".")


def _enum(value: Any, fallback: str = "N/A", *, upper: bool = False, lower: bool = False) -> str:
    text = _raw(value)
    if not text:
        return fallback
    text = re.sub(r"\s+", " ", text.replace("_", " ")).strip()
    if upper:
        return text.upper()
    if lower:
        return text.lower()
    return text


def _score(value: Any) -> str:
    number = _num(value)
    return f"{number:.0f}" if number is not None else "N/A"


def _confidence(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    if 0 <= number <= 1:
        number *= 100
    return f"{number:.0f}%"


def _pct(value: Any, *, concentration: bool = False) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    if concentration and abs(number) <= 1:
        number *= 100
    rendered = f"{number:.2f}%" if concentration else f"{number:+.2f}%"
    return rendered


def _money(value: Any, *, signed: bool = True) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    sign = "+" if signed and number > 0 else "-" if signed and number < 0 else ""
    amount = abs(number)
    if amount >= 1_000_000_000:
        scaled, unit = amount / 1_000_000_000, "B"
    elif amount >= 1_000_000:
        scaled, unit = amount / 1_000_000, "M"
    elif amount >= 1_000:
        scaled, unit = amount / 1_000, "K"
    else:
        return f"{sign}Rp{amount:,.0f}".replace(",", ".")
    rendered = f"{scaled:.2f}".replace(".", ",")
    return f"{sign}Rp{rendered}{unit}"


def _compact_money(value: Any) -> str:
    number = _num(value)
    if number is None:
        return ""
    amount = abs(number)
    if amount >= 1_000_000_000:
        scaled, unit = amount / 1_000_000_000, "B"
    elif amount >= 1_000_000:
        scaled, unit = amount / 1_000_000, "M"
    elif amount >= 1_000:
        scaled, unit = amount / 1_000, "K"
    else:
        return f"{amount:,.0f}".replace(",", ".")
    if scaled >= 100:
        rendered = f"{scaled:.0f}"
    elif scaled >= 10:
        rendered = f"{scaled:.1f}".rstrip("0").rstrip(".")
    else:
        rendered = f"{scaled:.2f}".rstrip("0").rstrip(".")
    rendered = rendered.replace(".", ",")
    return f"{rendered}{unit}"


def _rr(value: Any) -> str:
    number = _num(value)
    return f"{number:.2f}".replace(".", ",") if number is not None else "N/A"


def _days(value: Any) -> str:
    number = _num(value)
    return str(int(round(number))) if number is not None else "N/A"


def _list(value: Any) -> list[Any]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                value = json.loads(text)
            except Exception:
                return []
        else:
            return []
    return list(value or []) if isinstance(value, (list, tuple)) else []


def _compact_actors(value: Any) -> str:
    actors: list[str] = []
    for item in _list(value)[:3]:
        if not isinstance(item, Mapping):
            continue
        broker = _enum(item.get("broker") or item.get("code") or item.get("name"), "", upper=True)
        if not broker:
            continue
        rendered = html.escape(broker, quote=False)
        money = _compact_money(item.get("value") if item.get("value") is not None else item.get("net_value"))
        if money:
            rendered += f" {money}"
        actors.append(rendered)
    return " • ".join(actors)


def _date_label(value: Any) -> str:
    text = _raw(value)
    if not text:
        return "N/A"
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return text


def _period_is_multi(period: str) -> bool:
    return bool(period) and period.upper() not in {"1D", "1DAY", "DAY"}


def _explicit_trigger(row: Mapping[str, Any]) -> str:
    """Return an engine/source-owned trigger without inventing one from S/R.

    ``trigger_description`` and ``waiting_triggers`` are populated by the
    existing Final Watchlist enrichment from already-produced engine artifacts.
    Resistance remains technical context only and is deliberately not a
    fallback trigger source.
    """
    direct = _raw(_pick(
        row,
        "trigger_description",
        "Trigger_Description",
        "Entry_Trigger",
        "Execution_Trigger",
        default="",
    ))
    if direct:
        return direct.rstrip(" .")

    pending = _pick(row, "waiting_triggers", "Waiting_Triggers", default=[])
    items = _list(pending)
    if not items and isinstance(pending, str):
        items = [
            item.strip()
            for item in re.split(r"\s*(?:\r?\n|[;|])\s*", pending)
            if item.strip()
        ]
    for item in items:
        if isinstance(item, Mapping):
            candidate = _raw(
                item.get("description")
                or item.get("trigger")
                or item.get("condition")
                or item.get("text")
            )
        else:
            candidate = _raw(item)
        if candidate:
            return candidate.rstrip(" .")
    return ""


def _compact_action(row: Mapping[str, Any]) -> str:
    current = _num(_pick(row, "last_price", "current_price", "Reference_Close"))
    low = _num(_pick(row, "entry_low", "Entry_Zone_Low"))
    high = _num(_pick(row, "entry_high", "Entry_Zone_High"))
    phase = _enum(_pick(row, "phase", "execution_state", "Execution_Status"), "", upper=True)
    waiting = any(token in phase for token in ("WAIT", "NOT READY", "CONDITIONAL", "MONITOR"))
    explicit_trigger = _explicit_trigger(row)

    # Explicit engine/source trigger always wins. Never infer a breakout merely
    # because a technical resistance level exists on the card.
    if explicit_trigger:
        return f"⚠️ Trigger: {html.escape(explicit_trigger, quote=False)}. Jangan chase."
    if current is not None and low is not None and high is not None and current > high:
        return f"⚠️ Jangan chase. Tunggu pullback ke {_price(low)}–{_price(high)}."
    if waiting and low is not None and high is not None:
        return f"⚠️ Tunggu trigger valid di area {_price(low)}–{_price(high)}. Jangan chase."
    if waiting:
        return "⚠️ Tunggu trigger valid sebelum entry. Jangan chase."
    if low is not None and high is not None:
        return f"⚠️ Entry hanya di area {_price(low)}–{_price(high)}. Jangan chase."
    return "⚠️ Tunggu setup tetap valid. Jangan chase."


def format_watchlist_detail(row: Mapping[str, Any]) -> str:
    symbol = html.escape(_enum(_pick(row, "symbol", "Symbol"), "N/A", upper=True), quote=False)
    decision = html.escape(_enum(_pick(row, "decision", "Decision_V3", "Decision"), "WATCH", upper=True), quote=False)
    confidence = _confidence(_pick(row, "confidence", "Final_Score", "Final_Score_V3"))
    setup = html.escape(_enum(_pick(row, "setup", "Setup_Type"), "N/A", upper=True), quote=False)
    analysis_date = html.escape(_date_label(_pick(row, "analysis_date", "trade_date", "Trade_Date")), quote=False)
    current = _price(_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = _price(_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = _price(_pick(row, "entry_high", "Entry_Zone_High"))
    stop = _price(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = _price(_pick(row, "target_1", "Target_1"))
    tp2 = _price(_pick(row, "target_2", "Target_2"))
    rr = _rr(_pick(row, "risk_reward", "Target_2_RR", "Target_1_RR"))
    trend_raw = _enum(_pick(row, "trend", "Technical_Regime"), "N/A")
    trend = html.escape(trend_raw.title(), quote=False)
    phase = html.escape(_enum(_pick(row, "phase", "execution_state", "Execution_Status"), "N/A", upper=True), quote=False)
    support = _price(_pick(row, "support", "Support_Level"))
    resistance = _price(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    broker = html.escape(_enum(_pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"), "INSUFFICIENT", upper=True), quote=False)
    broker_score = _score(_pick(row, "broker_score", "Broker_Score"))
    net = _money(_pick(row, "broker_net_flow", "net_flow", "cumulative_net_value"))
    buy = _days(_pick(row, "buy_days"))
    sell = _days(_pick(row, "sell_days"))
    buy_cost = _price(_pick(row, "bandar_buy_cost", "avg_buyer_price", "weighted_broker_buy_cost"))
    buy_avg = _pct(_pick(row, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct")).replace(".", ",")
    top_buy = _compact_actors(_pick(row, "top_buyers", default=[])) or "N/A"
    top_sell = _compact_actors(_pick(row, "top_sellers", default=[])) or "N/A"

    lines = [
        f"📈 <b>{symbol}</b> | {decision} | {confidence}",
        f"{setup} • {analysis_date}",
        "",
        f"💰 Harga {current} | Entry {entry_low}–{entry_high}",
        f"🛑 SL {stop} | 🎯 {tp1} / {tp2} | RR 1:{rr}",
        "",
        f"📊 {trend} | {phase}",
        f"S {support} | R {resistance}",
        "",
        f"🏦 {broker} {broker_score}/100",
        f"Net {net} | B/S {buy}/{sell}",
        f"Cost {buy_cost} ({buy_avg})",
        "",
        f"🟢 {top_buy}",
        f"🔴 {top_sell}",
        "",
        _compact_action(row),
    ]
    return "\n".join(lines).strip()


__all__ = ["format_watchlist_detail"]
