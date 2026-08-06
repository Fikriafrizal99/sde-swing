"""Shared, human-readable Telegram formatting primitives.

The report builders intentionally keep their own layouts, but they all use
these functions for values that must be consistent across reports.  No raw
Python containers, internal enum tokens, or unbounded floating point values
should cross the Telegram boundary.
"""
from __future__ import annotations

import html
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any


MISSING = "data tidak tersedia"


def escape_html(value: Any) -> str:
    """Escape dynamic text for Telegram HTML parse mode."""
    return html.escape(str(value), quote=False)


def _number(value: Any) -> float | None:
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null", "nat"}:
            return None
        result = float(text)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def format_number(value: Any, decimals: int = 0, fallback: str = MISSING) -> str:
    number = _number(value)
    if number is None:
        return fallback
    decimals = max(0, min(int(decimals), 2))
    rendered = f"{number:,.{decimals}f}"
    integer, dot, fraction = rendered.partition(".")
    integer = integer.replace(",", ".")
    return integer if not dot else f"{integer},{fraction}"


def format_price(value: Any, fallback: str = MISSING) -> str:
    number = _number(value)
    if number is None or number <= 0:
        return fallback
    return f"Rp{format_number(number, 0)}"


def format_percent(
    value: Any,
    decimals: int = 1,
    *,
    signed: bool = False,
    ratio_aware: bool = False,
    fallback: str = MISSING,
) -> str:
    number = _number(value)
    if number is None:
        return fallback
    if ratio_aware and abs(number) <= 1.5:
        number *= 100.0
    decimals = max(0, min(int(decimals), 2))
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{decimals}f}%".replace(".", ",")


def format_money(value: Any, fallback: str = MISSING) -> str:
    number = _number(value)
    if number is None:
        return fallback
    absolute = abs(number)
    sign = "-" if number < 0 else "+" if number > 0 else ""
    if absolute >= 1_000_000_000_000:
        return f"{sign}Rp{absolute / 1_000_000_000_000:.2f} triliun".replace(".", ",")
    if absolute >= 1_000_000_000:
        return f"{sign}Rp{absolute / 1_000_000_000:.2f} miliar".replace(".", ",")
    if absolute >= 1_000_000:
        return f"{sign}Rp{absolute / 1_000_000:.2f} juta".replace(".", ",")
    return f"{sign}Rp{absolute:,.0f}".replace(",", ".")


STATUS_LABELS = {
    "BUY": "BUY CANDIDATE",
    "BUY READY": "BUY READY",
    "BUY CONFIRMED": "BUY READY",
    "BUY ON TRIGGER": "BUY CANDIDATE",
    "STRONG BUY": "BUY CANDIDATE",
    "BUY CANDIDATE": "BUY CANDIDATE",
    "WAIT": "WAITING",
    "WAITING": "WAITING",
    "WATCH HIGH": "WATCH",
    "WATCH": "WATCH",
    "SPECULATIVE": "WATCH",
    "HOLD": "WATCH",
    "TAKE PROFIT": "TAKE PROFIT",
    "AVOID": "AVOID",
    "BLOCKED": "BLOCKED",
    "SUSPENDED": "SUSPENDED",
    "UMA": "UMA",
    "RELISTING": "RELISTING",
}


def human_status(value: Any, fallback: str = "WATCH") -> str:
    text = str(value or "").strip().upper().replace("_", " ")
    if not text:
        return fallback
    return STATUS_LABELS.get(text, text)


ENUM_LABELS = {
    "ENTRY_NOT_TRIGGERED": "entry belum terpicu",
    "PENDING_TRIGGER": "menunggu trigger entry",
    "WAIT_FOR_ENTRY_TRIGGER": "menunggu trigger entry",
    "WAIT_FOR_ENTRY_ZONE": "menunggu harga masuk area entry",
    "PRICE_EXTENDED": "harga terlalu jauh dari area entry",
    "OVEREXTENDED_FROM_MA20": "harga terlalu jauh dari MA20",
    "NEAREST_RESISTANCE_BELOW_MIN_RR": "R:R ke resistance terdekat belum layak",
    "NO_VALID_RESISTANCE_PATH": "belum ada jalur target dengan R:R layak",
    "MINOR_RESISTANCE_NEAR": "resistance minor masih dekat",
    "RELISTING_HISTORY_INSUFFICIENT": "riwayat candle relisting belum cukup",
    "ZAPI_DEGRADED": "data aktivitas Bursa sedang terdegradasi",
    "ZAPI_NOT_CONFIGURED": "Zapi belum terkonfigurasi",
    "DATA_NOT_AVAILABLE": MISSING,
}


def human_enum(value: Any, fallback: str = MISSING) -> str:
    """Turn internal enum-like values into short Indonesian labels."""
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null", "nat"}:
        return fallback
    key = re.sub(r"[ -]+", "_", text.upper())
    if key in ENUM_LABELS:
        return ENUM_LABELS[key]
    # Do not expose a list/dict representation if a caller accidentally passes
    # a structured value to a single-value field.
    if isinstance(value, Mapping):
        return fallback
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip().lower()


def clean_items(value: Any, limit: int = 3, fallback: str = MISSING) -> list[str]:
    """Normalize reason/risk/waiting collections to at most ``limit`` lines."""
    if value is None or value == "":
        return []
    if isinstance(value, Mapping):
        values: list[Any] = []
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = re.split(r"[\n;]+", str(value))
    result: list[str] = []
    flattened: list[Any] = []
    for item in values:
        if isinstance(item, (list, tuple, set)):
            flattened.extend(item)
        else:
            flattened.append(item)
    for item in flattened:
        text = str(item or "").strip(" \t-•")
        if not text or text.lower() in {"nan", "none", "null", "n/a", "na"}:
            continue
        if isinstance(item, (int, float)):
            text = format_number(item, 2)
        elif re.fullmatch(r"[A-Z][A-Z0-9_ -]{2,}", text):
            text = human_enum(text)
        if text not in result:
            result.append(text)
        if len(result) >= max(1, int(limit)):
            break
    return result or ([] if fallback == "" else [fallback])


def format_momentum(rsi: Any = None, macd: Any = None, *, macd_signal: Any = None, macd_hist: Any = None) -> str:
    """Map RSI/MACD into the user-facing momentum states."""
    rsi_value = _number(rsi)
    macd_value = _number(macd_hist if macd_hist is not None else macd)
    if macd_value is None and macd_signal is not None and macd is not None:
        macd_value = (_number(macd) or 0.0) - (_number(macd_signal) or 0.0)
    if rsi_value is None and macd_value is None:
        return MISSING
    if rsi_value is not None and rsi_value >= 75:
        state = "OVERBOUGHT"
    elif rsi_value is not None and rsi_value >= 65:
        state = "KUAT"
    elif rsi_value is not None and rsi_value < 40:
        state = "LEMAH"
    elif rsi_value is not None and 50 <= rsi_value <= 65 and macd_value is not None and macd_value > 0:
        state = "SEHAT"
    elif macd_value is not None and macd_value < 0:
        state = "LEMAH"
    elif macd_value is not None and macd_value > 0:
        state = "SEHAT"
    else:
        state = "NETRAL"
    return f"{state} — RSI {format_number(rsi_value, 1)}" if rsi_value is not None else state


def risk_reward(
    entry_low: Any,
    entry_high: Any,
    target: Any,
    stop: Any,
    *,
    entry_reference: Any = None,
) -> tuple[str, bool]:
    """Calculate displayed TP1 risk/reward from the displayed levels."""
    low = _number(entry_low)
    high = _number(entry_high)
    entry = _number(entry_reference)
    if entry is None:
        if low is not None and high is not None:
            entry = (low + high) / 2.0
        else:
            entry = low if low is not None else high
    tp = _number(target)
    sl = _number(stop)
    if entry is None or tp is None or sl is None or entry <= sl or tp <= entry:
        return "R:R belum valid", False
    value = (tp - entry) / (entry - sl)
    if not math.isfinite(value) or value <= 0:
        return "R:R belum valid", False
    return f"1:{value:.2f}".replace(".", ","), True


def format_status_line(value: Any) -> str:
    return human_status(value)


def exchange_warnings(
    exchange_status: Any = "NORMAL",
    risk_flags: Any = None,
    veto: Any = None,
    *,
    limit: int = 3,
) -> list[str]:
    """Return short, user-facing Bursa warnings without exposing raw enums."""
    status = str(exchange_status or "NORMAL").strip().upper().replace("_", " ")
    if isinstance(risk_flags, str):
        raw_flags = re.split(r"[,;\n]+", risk_flags)
    elif isinstance(risk_flags, (list, tuple, set)):
        raw_flags = list(risk_flags)
    else:
        raw_flags = []
    flags = {str(item or "").strip().upper().replace("_", " ") for item in raw_flags if str(item or "").strip()}
    if status in {"UMA", "RELISTING", "SUSPENDED"}:
        flags.add(status)
    veto_text = str(veto or "").strip().upper().replace("_", " ")
    warnings: list[str] = []
    if status in {"SUSPENDED", "BLOCKED"} or "SUSPENDED" in flags or veto_text == "SUSPENDED":
        warnings.append("SUSPENDED — saham diblokir dari watchlist dan tidak boleh dieksekusi.")
    if "UMA" in flags:
        warnings.append("UMA — pergerakan saham sedang dalam pengawasan Bursa. Gunakan risiko lebih konservatif.")
    if "RELISTING" in flags:
        if veto_text == "RELISTING HISTORY INSUFFICIENT":
            warnings.append("RELISTING — histori perdagangan terbatas dan candle belum memenuhi kebutuhan indikator.")
        else:
            warnings.append("RELISTING — histori perdagangan mungkin masih terbatas. Validasi kecukupan candle sebelum digunakan.")
    if veto_text and veto_text not in {"SUSPENDED", "RELISTING HISTORY INSUFFICIENT"} and not any(veto_text in item for item in warnings):
        warnings.append(f"Veto Bursa — {human_enum(veto_text)}.")
    return warnings[: max(1, int(limit))]


__all__ = [
    "MISSING",
    "clean_items",
    "escape_html",
    "format_money",
    "format_momentum",
    "format_number",
    "format_percent",
    "format_price",
    "format_status_line",
    "exchange_warnings",
    "human_enum",
    "human_status",
    "risk_reward",
]
