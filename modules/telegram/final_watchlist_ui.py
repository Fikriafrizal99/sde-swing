from __future__ import annotations

import html
import math
import re
from typing import Any, Mapping


_MISSING = {
    "",
    "nan",
    "none",
    "null",
    "engine_data_not_available",
    "data_not_available",
    "n/a",
}


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
    value = abs(float(price))
    if value < 200:
        return 1.0
    if value < 500:
        return 2.0
    if value < 2_000:
        return 5.0
    if value < 5_000:
        return 10.0
    return 25.0


def _round_idx_price(value: Any) -> float | None:
    number = _num(value)
    if number is None or number <= 0:
        return None
    tick = _idx_tick_size(number)
    return float(math.floor(number / tick + 0.5) * tick)


def _price(value: Any, fallback: str = "N/A") -> str:
    number = _round_idx_price(value)
    if number is None:
        return fallback
    return f"{number:,.0f}".replace(",", ".")


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
        number *= 100.0
    return f"{number:.0f}%"


def _pct(value: Any, *, concentration: bool = False) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    if concentration and abs(number) <= 1:
        number *= 100.0
    return f"{number:.2f}%" if concentration else f"{number:+.2f}%"


def _money(value: Any, *, signed: bool = True) -> str:
    number = _num(value)
    if number is None:
        return "N/A"
    sign = "+" if signed and number > 0 else "-" if signed and number < 0 else ""
    amount = abs(number)
    if amount >= 1_000_000_000:
        rendered = f"Rp{amount / 1_000_000_000:.2f}B"
    elif amount >= 1_000_000:
        rendered = f"Rp{amount / 1_000_000:.2f}Jt"
    elif amount >= 1_000:
        rendered = f"Rp{amount / 1_000:.2f}Rb"
    else:
        rendered = f"Rp{amount:,.0f}"
    return (sign + rendered).replace(".", ",")


def _rr(value: Any) -> str:
    number = _num(value)
    return f"{number:.2f}" if number is not None else "N/A"


def _day_count(value: Any) -> str:
    number = _num(value)
    return str(int(round(number))) if number is not None else "N/A"


def _participants(value: Any) -> list[str]:
    if isinstance(value, str):
        import json

        raw = value.strip()
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except Exception:
                value = []
        else:
            value = []
    items = list(value or []) if isinstance(value, (list, tuple)) else []
    lines: list[str] = []
    for index, item in enumerate(items[:3], 1):
        if not isinstance(item, Mapping):
            continue
        broker = _enum(item.get("broker") or item.get("code") or item.get("name"), "?", upper=True)
        amount = _money(item.get("value") or item.get("net_value") or item.get("amount"), signed=False)
        average = _price(item.get("avg_price") or item.get("average_price") or item.get("avg"))
        details = [broker]
        if amount != "N/A":
            details.append(amount)
        if average != "N/A":
            details.append(f"Avg Rp{average}")
        head, *tail = details
        lines.append(f"{index}. {head}" + (f" — {' | '.join(tail)}" if tail else ""))
    return lines or ["• Data tidak tersedia"]


def _inside(value: float | None, low: float | None, high: float | None) -> bool:
    return value is not None and low is not None and high is not None and low <= value <= high


def _interpretive_reason(row: Mapping[str, Any]) -> str:
    """Translate visible engine facts into a concise explanation.

    This is presentation-only. It never calculates or changes score, confidence,
    decision, entry, stop loss, or target levels.
    """
    symbol = _enum(_pick(row, "symbol", "Symbol"), "Saham", upper=True)
    setup = _enum(_pick(row, "setup", "Setup_Type"), "setup", upper=True)
    trend = _enum(_pick(row, "trend", "Technical_Regime", "technical_status", "technical_state"), "", lower=True)
    phase = _enum(_pick(row, "phase", "execution_state", "Execution_Status"), "", upper=True)

    current_raw = _pick(row, "last_price", "current_price", "Reference_Close")
    low_raw = _pick(row, "entry_low", "Entry_Zone_Low")
    high_raw = _pick(row, "entry_high", "Entry_Zone_High")
    current = _num(current_raw)
    low = _num(low_raw)
    high = _num(high_raw)

    current_text = _price(current_raw)
    low_text = _price(low_raw)
    high_text = _price(high_raw)
    entry_text = f"{low_text}–{high_text}" if low_text != "N/A" and high_text != "N/A" else ""

    if trend:
        opening = f"{symbol} masih {trend}"
    else:
        opening = f"{symbol} memiliki setup {setup}"
    if _inside(current, low, high):
        opening += f" dan harga {current_text} masih di area entry {entry_text}."
    elif current is not None and high is not None and current > high:
        opening += f", tetapi harga {current_text} sudah di atas area entry {entry_text}."
    elif current is not None and low is not None and current < low:
        opening += f" dan harga {current_text} masih di bawah area entry {entry_text}."
    else:
        opening += "."

    broker_state = _enum(
        _pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"),
        "INSUFFICIENT",
        upper=True,
    )
    net_raw = _pick(row, "broker_net_flow", "net_flow", "cumulative_net_value")
    net = _num(net_raw)
    buy_days_raw = _pick(row, "buy_days")
    sell_days_raw = _pick(row, "sell_days")
    buy_days = _num(buy_days_raw)
    sell_days = _num(sell_days_raw)
    buy_sell_text = (
        f"Buy/Sell {_day_count(buy_days_raw)}/{_day_count(sell_days_raw)}"
        if buy_days is not None and sell_days is not None
        else ""
    )

    insufficient = any(token in broker_state for token in ("INSUFFICIENT", "NO DATA", "MISSING", "UNKNOWN"))
    accumulating = "ACCUM" in broker_state or broker_state in {"BUY", "BROKER CONFIRM"}
    distributing = "DISTR" in broker_state or broker_state == "SELL"

    if insufficient:
        broker_text = "Broker belum cukup untuk konfirmasi; Score 0 pada kondisi INSUFFICIENT berarti data belum cukup, bukan otomatis distribusi."
        evidence: list[str] = []
        if net is not None:
            evidence.append(f"net flow sementara {_money(net_raw)}")
        if buy_sell_text:
            evidence.append(buy_sell_text)
        if evidence:
            broker_text += " " + " dan ".join(evidence) + " baru dibaca sebagai indikasi awal."
    elif accumulating:
        evidence = []
        if net is not None:
            evidence.append(f"net flow {_money(net_raw)}")
        if buy_sell_text:
            evidence.append(buy_sell_text)
        broker_text = f"Broker mendukung ({broker_state})"
        if evidence:
            broker_text += ": " + ", ".join(evidence)
        if buy_days is not None and sell_days is not None and buy_days > sell_days:
            broker_text += ", sehingga buying terlihat lebih konsisten daripada selling."
        else:
            broker_text += "."
    elif distributing:
        broker_text = f"Broker belum mendukung karena status {broker_state}"
        if net is not None:
            broker_text += f" dengan net flow {_money(net_raw)}"
        broker_text += "."
    else:
        broker_text = f"Broker masih {broker_state}"
        if net is not None:
            broker_text += f" dengan net flow {_money(net_raw)}"
        if buy_sell_text:
            broker_text += f" dan {buy_sell_text}"
        broker_text += ", jadi konfirmasinya belum sekuat setup teknikal."

    buy_cost_raw = _pick(row, "bandar_buy_cost", "avg_buyer_price", "weighted_broker_buy_cost")
    distance_raw = _pick(row, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct")
    buy_cost = _num(buy_cost_raw)
    distance = _num(distance_raw)
    if buy_cost is not None and distance is not None:
        if distance < 0:
            broker_text += f" Harga masih {abs(distance):.2f}% di bawah buy cost {_price(buy_cost_raw)}, jadi belum jauh meninggalkan rata-rata buyer."
        elif distance > 3:
            broker_text += f" Harga sudah {distance:.2f}% di atas buy cost {_price(buy_cost_raw)}, sehingga risiko mengejar harga lebih tinggi."

    resistance_raw = _pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance")
    resistance = _num(resistance_raw)
    resistance_text = _price(resistance_raw)
    tp1 = _price(_pick(row, "target_1", "Target_1"))
    tp2 = _price(_pick(row, "target_2", "Target_2"))
    stop = _price(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))

    waiting_phase = any(token in phase for token in ("WAIT", "NOT READY", "CONDITIONAL", "MONITOR"))
    if current is not None and high is not None and current > high:
        action = f"Harga sudah melewati area entry; jangan dikejar. Tunggu pullback kembali ke {entry_text} atau trigger baru yang valid."
    elif waiting_phase:
        if resistance is not None:
            if current is not None and current >= resistance:
                action = f"Status masih {phase}; harga sudah di atas resistance {resistance_text}, jadi tunggu kemampuan bertahan di atas level itu agar breakout terkonfirmasi."
            else:
                action = f"Status masih {phase}; resistance {resistance_text} menjadi konfirmasi terdekat. Tunggu harga menembus dan bertahan di atas {resistance_text}."
        else:
            action = f"Status masih {phase}; tunggu trigger harga yang valid sebelum entry."
    else:
        action = "Setup sudah lebih siap, tetapi eksekusi tetap hanya di area entry yang ditetapkan."

    targets = []
    if tp1 != "N/A":
        targets.append(f"TP1 {tp1}")
    if tp2 != "N/A":
        targets.append(f"TP2 {tp2}")
    if targets and waiting_phase and current is not None and (resistance is None or current < resistance):
        action += f" Jika konfirmasi lolos, {' dan '.join(targets)} menjadi target lanjutan."
    elif not waiting_phase and stop != "N/A":
        action += f" SL {stop} tetap menjadi batas invalidasi."

    # Keep the explanation useful inside Telegram's photo-caption budget.
    text = " ".join((opening, broker_text, action))
    text = re.sub(r"\s+", " ", text).strip()
    return html.escape(text, quote=False)


def format_watchlist_detail(row: Mapping[str, Any]) -> str:
    """Compact Final Watchlist card with an interpretive, fact-bound Reason."""
    symbol = html.escape(_enum(_pick(row, "symbol", "Symbol"), "N/A", upper=True), quote=False)
    setup = html.escape(_enum(_pick(row, "setup", "Setup_Type"), "N/A", upper=True), quote=False)
    analysis_date = html.escape(_enum(_pick(row, "analysis_date", "trade_date", "Trade_Date"), "N/A"), quote=False)
    current = _price(_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = _price(_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = _price(_pick(row, "entry_high", "Entry_Zone_High"))
    stop = _price(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = _price(_pick(row, "target_1", "Target_1"))
    tp2 = _price(_pick(row, "target_2", "Target_2"))
    rr = _rr(_pick(row, "risk_reward", "Target_2_RR", "Target_1_RR"))

    technical_raw = _pick(row, "technical_status", "technical_state", "Plan_Status", "decision", default=_pick(row, "trend"))
    technical_status = html.escape(_enum(technical_raw, "N/A", lower=True), quote=False)
    confidence = _confidence(_pick(row, "confidence", "Final_Score", "Final_Score_V3"))

    broker_signal = html.escape(
        _enum(_pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"), "INSUFFICIENT", upper=True),
        quote=False,
    )
    broker_score = _score(_pick(row, "broker_score", "Broker_Score"))
    net_flow = _money(_pick(row, "broker_net_flow", "net_flow", "cumulative_net_value"))
    buy_days = _day_count(_pick(row, "buy_days"))
    sell_days = _day_count(_pick(row, "sell_days"))
    buyer_concentration = _pct(_pick(row, "buyer_concentration"), concentration=True)
    seller_concentration = _pct(_pick(row, "seller_concentration"), concentration=True)
    top_buy = _participants(_pick(row, "top_buyers", default=[]))
    top_sell = _participants(_pick(row, "top_sellers", default=[]))
    buy_cost = _price(_pick(row, "bandar_buy_cost", "avg_buyer_price", "weighted_broker_buy_cost"))
    vs_cost = _pct(_pick(row, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct"))

    trend = html.escape(_enum(_pick(row, "trend", "Technical_Regime"), "N/A", lower=True), quote=False)
    phase = html.escape(_enum(_pick(row, "phase", "execution_state", "Execution_Status"), "N/A", upper=True), quote=False)
    support = _price(_pick(row, "support", "Support_Level"))
    resistance = _price(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    fib_status = html.escape(
        _enum(_pick(row, "fib_status", "Fibonacci_Status", "Fib_Status"), "ENGINE NOT AVAILABLE V1 7", upper=True),
        quote=False,
    )
    reason = _interpretive_reason(row)

    lines = [
        "<b>📈 SDE SWING — FINAL WATCHLIST</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📌 <b>{symbol} | {setup}</b>",
        f"🕒 {analysis_date}",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "<b>🎯 TRADE SETUP</b>",
        f"💰 {current} | Entry {entry_low}–{entry_high}",
        f"🛑 SL {stop} | 🎯 TP1/TP2 {tp1} | {tp2}",
        f"⚖️ RR 1:{rr}",
        f"📊 {technical_status} | 🧠 Confidence {confidence}",
        "",
        "<b>🏦 BROKER SUMMARY</b>",
        f"📌 {broker_signal} | Score {broker_score}/100",
        f"💵 Net Flow {net_flow} | 📅 Buy/Sell {buy_days}/{sell_days}",
        f"🎯 Concentration B {buyer_concentration} | S {seller_concentration}",
        "",
        "<b>🟢 Top Buy</b>",
        *top_buy,
        "",
        "<b>🔴 Top Sell</b>",
        *top_sell,
        "",
        f"💰 Buy Cost {buy_cost} | Buy Avg {vs_cost}",
        "",
        "<b>📌 SETUP CONTEXT</b>",
        f"📈 {trend} | {phase}",
        f"🟢 Support {support} | 🔴 Resistance {resistance}",
        f"📐 Fibonacci {fib_status}",
        "",
        "<b>🧠 Reason:</b>",
        reason,
    ]
    return "\n".join(lines).strip()


__all__ = ["format_watchlist_detail"]
