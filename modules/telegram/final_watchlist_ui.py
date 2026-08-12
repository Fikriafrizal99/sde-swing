from __future__ import annotations

import html
import json
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
_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


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


def _price(value: Any, fallback: str = "N/A") -> str:
    number = _num(value)
    if number is None or number <= 0:
        return fallback
    tick = _idx_tick_size(number)
    rounded = math.floor(number / tick + 0.5) * tick
    return f"{rounded:,.0f}".replace(",", ".")


def _enum(
    value: Any,
    fallback: str = "N/A",
    *,
    upper: bool = False,
    lower: bool = False,
) -> str:
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
        scaled, unit = amount / 1_000_000_000, "B"
    elif amount >= 1_000_000:
        scaled, unit = amount / 1_000_000, "M"
    elif amount >= 1_000:
        scaled, unit = amount / 1_000, "K"
    else:
        return f"{sign}Rp{amount:,.0f}".replace(",", ".")
    rendered = f"{scaled:.2f}".rstrip("0").rstrip(".")
    return f"{sign}Rp{rendered}{unit}"


def _rr(value: Any) -> str:
    number = _num(value)
    return f"{number:.2f}".replace(".", ",") if number is not None else "N/A"


def _day_count(value: Any) -> str:
    number = _num(value)
    return str(int(round(number))) if number is not None else "N/A"


def _period_session_text(value: Any) -> str:
    raw = value
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except Exception:
                raw = [item.strip() for item in text.split(",") if item.strip()]
        else:
            raw = [item.strip() for item in text.split(",") if item.strip()]
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(item)[:10] for item in raw if str(item).strip())
    return _raw(raw)


def _period_coverage(row: Mapping[str, Any]) -> str:
    text = _raw(_pick(row, "broker_coverage_text", "Broker_Coverage_Text"))
    if text:
        return text
    ratio = _num(_pick(row, "broker_session_coverage", "broker_coverage"))
    expected = _num(_pick(row, "broker_trading_days", "Broker_Trading_Days"))
    if ratio is not None and expected is not None:
        if 0 <= ratio <= 1:
            return f"{int(round(ratio * expected))}/{int(round(expected))}"
        return f"{int(round(ratio))}/{int(round(expected))}"
    return "N/A"


def _period_is_multi(period_type: str) -> bool:
    return bool(period_type) and period_type not in {"1D", "1DAY", "DAY"}


def _period_range_text(start: Any, end: Any) -> str:
    months = {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "Mei",
        6: "Jun",
        7: "Jul",
        8: "Aug",
        9: "Sep",
        10: "Okt",
        11: "Nov",
        12: "Des",
    }

    def parts(value: Any) -> tuple[int, int, int] | None:
        text = _raw(value)[:10]
        try:
            year, month, day = (int(item) for item in text.split("-"))
            return year, month, day
        except Exception:
            return None

    left, right = parts(start), parts(end)
    if left is None or right is None:
        start_text = _raw(start) or "N/A"
        end_text = _raw(end) or "N/A"
        return start_text if start_text == end_text else f"{start_text}–{end_text}"

    sy, sm, sd = left
    ey, em, ed = right
    if left == right:
        return f"{sd} {months.get(sm, str(sm))}"
    if sy == ey and sm == em:
        return f"{sd}–{ed} {months.get(sm, str(sm))}"
    return f"{sd} {months.get(sm, str(sm))}–{ed} {months.get(em, str(em))}"


def _participant_codes(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except Exception:
                value = []
        else:
            value = []
    items = list(value or []) if isinstance(value, (list, tuple)) else []
    codes: list[str] = []
    for item in items[:3]:
        if not isinstance(item, Mapping):
            continue
        broker = _enum(
            item.get("broker") or item.get("code") or item.get("name"),
            "",
            upper=True,
        )
        if broker:
            codes.append(broker)
    return codes


def _inside(value: float | None, low: float | None, high: float | None) -> bool:
    return value is not None and low is not None and high is not None and low <= value <= high


def _interpretive_reason(row: Mapping[str, Any]) -> str:
    """Explain existing engine facts without changing any trading decision."""
    symbol = _enum(_pick(row, "symbol", "Symbol"), "Saham", upper=True)
    trend = _enum(
        _pick(row, "trend", "Technical_Regime", "technical_status", "technical_state"),
        "",
        lower=True,
    )
    phase = _enum(
        _pick(row, "phase", "execution_state", "Execution_Status"),
        "",
        upper=True,
    )

    current_raw = _pick(row, "last_price", "current_price", "Reference_Close")
    low_raw = _pick(row, "entry_low", "Entry_Zone_Low")
    high_raw = _pick(row, "entry_high", "Entry_Zone_High")
    current, low, high = _num(current_raw), _num(low_raw), _num(high_raw)
    current_text = _price(current_raw)
    low_text, high_text = _price(low_raw), _price(high_raw)
    entry_text = (
        f"{low_text}–{high_text}"
        if low_text != "N/A" and high_text != "N/A"
        else "area entry"
    )

    opening = f"{symbol} {trend}" if trend else f"{symbol} punya setup teknikal aktif"
    if _inside(current, low, high):
        opening += f"; harga {current_text} masih di entry {entry_text}."
    elif current is not None and high is not None and current > high:
        opening += f"; harga {current_text} sudah di atas entry {entry_text}."
    elif current is not None and low is not None and current < low:
        opening += f"; harga {current_text} masih di bawah entry {entry_text}."
    else:
        opening += "."

    broker_state = _enum(
        _pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"),
        "INSUFFICIENT",
        upper=True,
    )
    net_raw = _pick(row, "broker_net_flow", "net_flow", "cumulative_net_value")
    net = _num(net_raw)
    buy_days_raw, sell_days_raw = _pick(row, "buy_days"), _pick(row, "sell_days")
    buy_days, sell_days = _num(buy_days_raw), _num(sell_days_raw)
    buy_sell = (
        f"Buy/Sell {_day_count(buy_days_raw)}/{_day_count(sell_days_raw)}"
        if buy_days is not None and sell_days is not None
        else ""
    )

    insufficient = any(
        token in broker_state
        for token in ("INSUFFICIENT", "NO DATA", "MISSING", "UNKNOWN")
    )
    accumulating = "ACCUM" in broker_state or broker_state in {"BUY", "BROKER CONFIRM"}
    distributing = "DISTR" in broker_state or broker_state == "SELL"

    evidence = []
    if net is not None:
        evidence.append(f"net {_money(net_raw)}")
    if buy_sell:
        evidence.append(buy_sell)
    evidence_text = " dan ".join(evidence)

    if insufficient:
        broker_text = "Broker INSUFFICIENT: Score 0 = data belum cukup, bukan distribusi."
        if evidence_text:
            broker_text += f" {evidence_text} baru indikasi awal."
    elif accumulating:
        broker_text = f"Broker mendukung ({broker_state})"
        if evidence_text:
            broker_text += f": {evidence_text}"
        if buy_days is not None and sell_days is not None and buy_days > sell_days:
            broker_text += ", buying konsisten."
        else:
            broker_text += "."
    elif distributing:
        broker_text = f"Broker belum mendukung ({broker_state})"
        if net is not None:
            broker_text += f": net {_money(net_raw)}"
        broker_text += "."
    else:
        broker_text = f"Broker masih {broker_state}"
        if evidence_text:
            broker_text += f": {evidence_text}"
        broker_text += ", jadi konfirmasi belum kuat."

    top_buy = _participant_codes(_pick(row, "top_buyers", default=[]))
    top_sell = _participant_codes(_pick(row, "top_sellers", default=[]))
    participant_parts: list[str] = []
    if top_buy:
        participant_parts.append(f"buyer utama {', '.join(top_buy)}")
    if top_sell:
        participant_parts.append(f"seller utama {', '.join(top_sell)}")
    participant_text = (
        f"Bukti broker: {'; '.join(participant_parts)}."
        if participant_parts
        else ""
    )

    buy_cost_raw = _pick(
        row,
        "bandar_buy_cost",
        "avg_buyer_price",
        "weighted_broker_buy_cost",
    )
    buy_cost = _price(buy_cost_raw)
    vs_cost = _pct(
        _pick(
            row,
            "distance_to_buy_cost",
            "distance_to_buyer_avg_pct",
            "distance_to_buy_cost_pct",
        )
    )
    cost_text = ""
    if buy_cost != "N/A" and vs_cost != "N/A":
        cost_text = (
            f"Rata-rata biaya buyer berada di {buy_cost}; harga saat ini {vs_cost} "
            "dari buy cost."
        )
    elif buy_cost != "N/A":
        cost_text = f"Rata-rata biaya buyer berada di {buy_cost}."

    resistance_raw = _pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance")
    resistance = _num(resistance_raw)
    resistance_text = _price(resistance_raw)
    waiting = any(
        token in phase for token in ("WAIT", "NOT READY", "CONDITIONAL", "MONITOR")
    )

    if current is not None and high is not None and current > high:
        action = f"Jangan kejar; tunggu pullback ke {entry_text} atau trigger baru."
    elif waiting and resistance is not None:
        if current is not None and current >= resistance:
            action = (
                f"Status {phase}; tunggu harga bertahan di atas resistance "
                f"{resistance_text} sebagai konfirmasi."
            )
        else:
            action = (
                f"{phase}; resistance {resistance_text} belum lewat. "
                f"Tunggu break dan bertahan >{resistance_text} sebelum entry."
            )
    elif waiting:
        action = f"Status {phase}; tunggu trigger harga valid sebelum entry."
    else:
        stop = _price(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
        action = "Setup lebih siap; eksekusi tetap hanya di area entry."
        if stop != "N/A":
            action += f" SL {stop} adalah batas invalidasi."

    period_type = _enum(
        _pick(row, "broker_period_type", "Broker_Period_Type", "primary_window"),
        "",
        upper=True,
    )
    primary_text = ""
    pulse_text = ""
    if period_type:
        period_complete = _pick(row, "broker_period_complete", "Broker_Period_Complete", default=True)
        missing_sessions = _period_session_text(
            _pick(row, "broker_missing_sessions", "Broker_Missing_Sessions", default=[])
        )
        if str(period_complete).strip().lower() in {"false", "0", "no"}:
            primary_text = (
                f"Broker PRIMARY {period_type} belum lengkap"
                + (f"; missing {missing_sessions}." if missing_sessions else ".")
            )
        else:
            primary_text = f"Broker PRIMARY {period_type} {broker_state} menjadi konteks utama."

        if _period_is_multi(period_type):
            pulse_status = _enum(
                _pick(row, "today_pulse_status"),
                "NOT_AVAILABLE",
                upper=True,
            )
            pulse_date = _raw(_pick(row, "today_pulse_date")) or _raw(
                _pick(row, "broker_period_end", "Broker_Period_End")
            )
            pulse_net = _money(_pick(row, "today_pulse_net_flow"))
            pulse_buy = _day_count(_pick(row, "today_pulse_buy_days"))
            pulse_sell = _day_count(_pick(row, "today_pulse_sell_days"))
            alignment = _enum(
                _pick(row, "broker_alignment", "Broker_Period_Alignment"),
                "INSUFFICIENT",
                upper=True,
            )
            pulse_text = (
                f"Today Pulse {pulse_status} {pulse_date}: net {pulse_net}, "
                f"Buy/Sell {pulse_buy}/{pulse_sell}; alignment {alignment}."
            )

    text = " ".join(
        part
        for part in (
            opening,
            primary_text,
            broker_text,
            participant_text,
            cost_text,
            pulse_text,
            action,
        )
        if part
    )
    text = re.sub(r"\s+", " ", text).strip()
    return html.escape(text, quote=False)


def _technical_status(row: Mapping[str, Any]) -> str:
    direct = _pick(row, "technical_status", "technical_state", "Plan_Status")
    if _raw(direct):
        return _enum(direct, "N/A", lower=True)
    decision = _pick(row, "decision")
    if _raw(decision):
        return _enum(decision, "N/A", upper=True)
    return _enum(_pick(row, "trend"), "N/A", lower=True)


def format_watchlist_detail(row: Mapping[str, Any]) -> str:
    """Compact Final Watchlist card with a deterministic interpretive Reason."""
    symbol = html.escape(
        _enum(_pick(row, "symbol", "Symbol"), "N/A", upper=True), quote=False
    )
    setup = html.escape(
        _enum(_pick(row, "setup", "Setup_Type"), "N/A", upper=True), quote=False
    )
    analysis_date = html.escape(
        _enum(_pick(row, "analysis_date", "trade_date", "Trade_Date"), "N/A"),
        quote=False,
    )
    current = _price(_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = _price(_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = _price(_pick(row, "entry_high", "Entry_Zone_High"))
    stop = _price(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = _price(_pick(row, "target_1", "Target_1"))
    tp2 = _price(_pick(row, "target_2", "Target_2"))
    rr = _rr(_pick(row, "risk_reward", "Target_2_RR", "Target_1_RR"))
    technical_status = html.escape(_technical_status(row), quote=False)
    confidence = _confidence(_pick(row, "confidence", "Final_Score", "Final_Score_V3"))

    broker_signal = html.escape(
        _enum(
            _pick(
                row,
                "broker_status",
                "broker_signal",
                "Broker_Confirmation",
                "broker_direction",
            ),
            "INSUFFICIENT",
            upper=True,
        ),
        quote=False,
    )
    broker_score = _score(_pick(row, "broker_score", "Broker_Score"))
    net_flow = _money(_pick(row, "broker_net_flow", "net_flow", "cumulative_net_value"))
    buy_days = _day_count(_pick(row, "buy_days"))
    sell_days = _day_count(_pick(row, "sell_days"))
    buyer_concentration = _pct(_pick(row, "buyer_concentration"), concentration=True)
    seller_concentration = _pct(_pick(row, "seller_concentration"), concentration=True)

    trend = html.escape(
        _enum(_pick(row, "trend", "Technical_Regime"), "N/A", lower=True),
        quote=False,
    )
    phase = html.escape(
        _enum(
            _pick(row, "phase", "execution_state", "Execution_Status"),
            "N/A",
            upper=True,
        ),
        quote=False,
    )
    support = _price(_pick(row, "support", "Support_Level"))
    resistance = _price(
        _pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance")
    )
    fib_status = html.escape(
        _enum(
            _pick(row, "fib_status", "Fibonacci_Status", "Fib_Status"),
            "ENGINE NOT AVAILABLE V1 7",
            upper=True,
        ),
        quote=False,
    )

    period_type = _enum(
        _pick(row, "broker_period_type", "Broker_Period_Type", "primary_window"),
        "",
        upper=True,
    )
    period_lines: list[str] = []
    if period_type:
        start = _pick(row, "broker_period_start", "Broker_Period_Start")
        end = _pick(row, "broker_period_end", "Broker_Period_End")
        period_range = html.escape(_period_range_text(start, end), quote=False)
        period_lines.extend([
            "",
            "<b>🏦 BROKER PRIMARY</b>",
            f"{period_type} | {period_range} | Coverage {_period_coverage(row)}",
        ])
        if _period_is_multi(period_type):
            pulse_status = html.escape(
                _enum(
                    _pick(row, "today_pulse_status"),
                    "NOT_AVAILABLE",
                    upper=True,
                ),
                quote=False,
            )
            alignment = html.escape(
                _enum(
                    _pick(row, "broker_alignment", "Broker_Period_Alignment"),
                    "INSUFFICIENT",
                    upper=True,
                ),
                quote=False,
            )
            period_lines.append(f"Today Pulse: {pulse_status} | {alignment}")

    lines = [
        "<b>📈 SDE SWING — FINAL WATCHLIST</b>",
        _SEPARATOR,
        f"📌 <b>{symbol} | {setup}</b>",
        f"🕒 {analysis_date}",
        _SEPARATOR,
        "",
        "<b>🎯 TRADE SETUP</b>",
        f"💰 {current} | Entry {entry_low}–{entry_high}",
        f"🛑 SL {stop} | 🎯 TP1 {tp1} | TP2 {tp2}",
        f"⚖️ RR 1:{rr}",
        f"📊 {technical_status} | 🧠 Confidence {confidence}",
        "",
        "<b>🏦 BROKER SUMMARY</b>",
        f"📌 {broker_signal} | Score {broker_score}/100",
        f"💵 Net Flow {net_flow} | Buy/Sell {buy_days}/{sell_days}",
        f"🎯 Concentration B {buyer_concentration} | S {seller_concentration}",
        "",
        "<b>📌 SETUP CONTEXT</b>",
        f"📈 {trend} | {phase}",
        f"🟢 Support {support} | 🔴 Resistance {resistance}",
        f"📐 Fibonacci {fib_status}",
        "",
        "<b>🧠 REASON</b>",
        _interpretive_reason(row),
    ]
    if period_lines:
        lines[5:5] = period_lines
    return "\n".join(lines).strip()


__all__ = ["format_watchlist_detail"]
