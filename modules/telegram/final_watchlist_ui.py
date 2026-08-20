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
        rendered = f"{scaled:.1f}"
    else:
        rendered = f"{scaled:.2f}"
    rendered = rendered.rstrip("0").rstrip(".").replace(".", ",")
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


def _actor_lines(value: Any) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(_list(value)[:3], start=1):
        if not isinstance(item, Mapping):
            continue
        broker = _enum(item.get("broker") or item.get("code") or item.get("name"), "", upper=True)
        if not broker:
            continue
        parts = [f"{index}. {html.escape(broker, quote=False)} —"]
        money = _money(item.get("value") if item.get("value") is not None else item.get("net_value"), signed=False)
        avg = _price(item.get("avg_price") if item.get("avg_price") is not None else item.get("average_price"))
        if money != "N/A":
            parts.append(money)
        if avg != "N/A":
            if money != "N/A":
                parts.append(f"| Avg Rp{avg}")
            else:
                parts.append(f"Avg Rp{avg}")
        lines.append(" ".join(parts))
    return lines


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


def _period_lines(row: Mapping[str, Any]) -> list[str]:
    period = _enum(_pick(row, "broker_period_type", "Broker_Period_Type", "primary_window"), "", upper=True)
    if not period:
        return []
    coverage = _raw(_pick(row, "broker_coverage_text", "Broker_Coverage_Text"))
    line = f"🏦 Primary {period}"
    if coverage:
        line += f" | Coverage {html.escape(coverage, quote=False)}"
    lines = [line]
    if _period_is_multi(period):
        pulse = _enum(_pick(row, "today_pulse_status"), "NOT AVAILABLE", upper=True)
        source = _enum(_pick(row, "today_pulse_source"), "STOCKBIT 1D", upper=True)
        alignment = _enum(_pick(row, "broker_alignment", "Broker_Period_Alignment"), "INSUFFICIENT", upper=True)
        lines.append(f"📍 TODAY PULSE {pulse} | {source} | {alignment}")
    return lines


def _technical_status(row: Mapping[str, Any]) -> str:
    direct = _pick(row, "technical_status", "technical_state", "Plan_Status")
    if _raw(direct):
        return _enum(direct, lower=True)
    decision = _pick(row, "decision")
    if _raw(decision):
        return _enum(decision, upper=True)
    return _enum(_pick(row, "trend"), lower=True)


def _interpretive_reason(row: Mapping[str, Any]) -> str:
    symbol = _enum(_pick(row, "symbol", "Symbol"), "Saham", upper=True)
    trend = _enum(_pick(row, "trend", "Technical_Regime", "technical_status", "technical_state"), "", lower=True)
    phase = _enum(_pick(row, "phase", "execution_state", "Execution_Status"), "", upper=True)
    current = _num(_pick(row, "last_price", "current_price", "Reference_Close"))
    low = _num(_pick(row, "entry_low", "Entry_Zone_Low"))
    high = _num(_pick(row, "entry_high", "Entry_Zone_High"))
    current_text = _price(current)
    entry_text = f"{_price(low)}–{_price(high)}"
    opening = f"{symbol} {trend}" if trend else symbol
    if current is not None and low is not None and high is not None and low <= current <= high:
        opening += f"; harga {current_text} masih di entry {entry_text}."
    elif current is not None and high is not None and current > high:
        opening += f"; harga {current_text} sudah di atas entry {entry_text}."
    elif current is not None and low is not None and current < low:
        opening += f"; harga {current_text} masih di bawah entry {entry_text}."
    else:
        opening += "."

    broker = _enum(_pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"), "INSUFFICIENT", upper=True)
    net = _pick(row, "broker_net_flow", "net_flow", "cumulative_net_value")
    buy = _num(_pick(row, "buy_days"))
    sell = _num(_pick(row, "sell_days"))
    evidence: list[str] = []
    if _num(net) is not None:
        evidence.append(f"net {_money(net)}")
    if buy is not None and sell is not None:
        evidence.append(f"Buy/Sell {_days(buy)}/{_days(sell)}")
    evidence_text = " dan ".join(evidence)

    if any(token in broker for token in ("INSUFFICIENT", "NO DATA", "UNKNOWN", "MISSING")):
        broker_text = "Broker INSUFFICIENT: Score 0 = data belum cukup, bukan distribusi."
        if evidence_text:
            broker_text += f" {evidence_text} baru indikasi awal."
    elif "ACCUM" in broker or broker in {"BUY", "BROKER CONFIRM"}:
        broker_text = f"Broker mendukung ({broker})"
        if evidence_text:
            broker_text += f": {evidence_text}"
        if buy is not None and sell is not None and buy > sell:
            broker_text += ", buying konsisten."
        else:
            broker_text += "."
    elif "DISTR" in broker or broker == "SELL":
        broker_text = f"Broker belum mendukung ({broker})"
        if evidence_text:
            broker_text += f": {evidence_text}"
        broker_text += "."
    else:
        broker_text = f"Broker masih {broker}"
        if evidence_text:
            broker_text += f": {evidence_text}"
        broker_text += "."

    resistance = _num(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    resistance_text = _price(resistance)
    waiting = any(token in phase for token in ("WAIT", "NOT READY", "CONDITIONAL", "MONITOR"))
    if current is not None and high is not None and current > high:
        action = f"Jangan kejar; tunggu pullback ke {entry_text} atau trigger baru."
    elif waiting and resistance is not None and (current is None or current < resistance):
        action = f"{phase}; resistance {resistance_text} belum lewat. Tunggu break dan bertahan >{resistance_text} sebelum entry."
    elif waiting and resistance is not None:
        action = f"{phase}; tunggu harga bertahan di atas resistance {resistance_text} sebagai konfirmasi."
    elif waiting:
        action = f"{phase}; tunggu trigger harga valid sebelum entry."
    else:
        action = "Eksekusi tetap hanya di area entry sesuai plan."

    return html.escape("\n".join([opening, broker_text, action]), quote=False)


def _compact_action(row: Mapping[str, Any]) -> str:
    current = _num(_pick(row, "last_price", "current_price", "Reference_Close"))
    low = _num(_pick(row, "entry_low", "Entry_Zone_Low"))
    high = _num(_pick(row, "entry_high", "Entry_Zone_High"))
    resistance = _num(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    phase = _enum(_pick(row, "phase", "execution_state", "Execution_Status"), "", upper=True)
    waiting = any(token in phase for token in ("WAIT", "NOT READY", "CONDITIONAL", "MONITOR"))

    if current is not None and high is not None and current > high:
        return f"⚠️ Jangan chase. Tunggu pullback ke {_price(low)}–{_price(high)}."
    if waiting and resistance is not None:
        return f"⚠️ Tunggu break >{_price(resistance)}. Jangan chase."
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
        f"📈 {symbol} | {decision} | {confidence}",
        f"{setup} • {analysis_date}",
        "",
        f"💰 {current} | Entry {entry_low}–{entry_high}",
        f"🛑 {stop} | 🎯 {tp1} / {tp2} | RR 1:{rr}",
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
