from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Mapping


_PRICE_FIELDS = {
    "last_price", "entry_low", "entry_high", "stop_loss", "active_stop_loss",
    "target_1", "target_2", "support", "resistance", "swing_high", "swing_low",
    "bandar_buy_cost", "avg_buyer_price", "avg_seller_price",
}
_MONEY_FIELDS = {
    "broker_net_flow", "today_pulse_net_flow", "foreign_buy", "foreign_sell", "foreign_net",
}
_PERCENT_RATIO_FIELDS = {
    "broker_buy_ratio", "broker_sell_ratio", "buyer_concentration", "seller_concentration",
    "broker_coverage", "broker_period_coverage", "coverage",
}
_PERCENT_VALUE_FIELDS = {
    "entry_distance_pct", "distance_to_buy_cost", "distance_to_buyer_avg_pct",
}
_SCORE_FIELDS = {
    "confidence", "technical_quality", "technical_score", "entry_readiness",
    "broker_confidence", "broker_score",
}

_ENUMS = {
    "ENTRY_NOT_TRIGGERED": "trigger entry belum terpenuhi",
    "ENTRY_TRIGGERED": "trigger entry sudah terpenuhi",
    "ALIGNED_POSITIVE": "broker searah positif",
    "ALIGNED_NEGATIVE": "broker searah negatif",
    "INSUFFICIENT_DATA": "data belum cukup",
    "ACCUMULATION": "akumulasi",
    "DISTRIBUTION": "distribusi",
    "BIG_ACC": "akumulasi besar",
    "BIG_DIST": "distribusi besar",
    "BULL": "bullish",
    "BEAR": "bearish",
    "BULLISH": "bullish",
    "BEARISH": "bearish",
    "BUY_ON_TRIGGER": "BUY ON TRIGGER",
    "BUY_CANDIDATE": "BUY CANDIDATE",
    "BUY_READY": "BUY READY",
    "WATCH_HIGH": "WATCH HIGH",
    "NO_DATA": "data belum tersedia",
    "NOT_AVAILABLE": "data belum tersedia",
    "AVAILABLE": "tersedia",
    "NORMAL": "normal",
}


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _idx_tick(price: float) -> float:
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
    number = _number(value)
    if number is None:
        return None
    tick = _idx_tick(number)
    return math.floor(number / tick + 0.5) * tick


def _id_number(value: float, decimals: int = 2) -> str:
    rendered = f"{value:,.{decimals}f}"
    rendered = rendered.replace(",", "_").replace(".", ",").replace("_", ".")
    if "," in rendered:
        rendered = rendered.rstrip("0").rstrip(",")
    return rendered


def _price(value: Any) -> str:
    rounded = _round_idx_price(value)
    if rounded is None:
        return ""
    return _id_number(rounded, 0)


def _money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 1_000_000_000_000:
        return f"{sign}Rp{_id_number(absolute / 1_000_000_000_000, 2)} triliun"
    if absolute >= 1_000_000_000:
        return f"{sign}Rp{_id_number(absolute / 1_000_000_000, 2)} miliar"
    if absolute >= 1_000_000:
        return f"{sign}Rp{_id_number(absolute / 1_000_000, 2)} juta"
    return f"{sign}Rp{_id_number(absolute, 0)}"


def _percent(value: Any, *, ratio: bool) -> str:
    number = _number(value)
    if number is None:
        return ""
    if ratio and abs(number) <= 1.0:
        number *= 100.0
    return f"{_id_number(number, 2)}%"


def _score(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    return f"{_id_number(number, 1)}/100"


def _rr(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    return f"1:{_id_number(number, 2)}"


def _date_label(value: Any) -> str:
    text = str(value or "").strip()[:10]
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text)
    except Exception:
        return text
    months = (
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    )
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


def _human_enum(value: Any) -> Any:
    if isinstance(value, list):
        return [_human_enum(item) for item in value]
    if isinstance(value, tuple):
        return [_human_enum(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        key_aliases = {
            "broker": "broker",
            "code": "broker",
            "name": "broker",
            "value": "nilai",
            "net_value": "nilai",
            "amount": "nilai",
            "avg_price": "harga rata-rata",
            "average_price": "harga rata-rata",
            "classification": "klasifikasi",
            "broker_type": "klasifikasi",
            "type": "klasifikasi",
            "origin": "klasifikasi",
        }
        for key, item in value.items():
            label = key_aliases.get(str(key), str(key).replace("_", " "))
            if label == "nilai":
                result[label] = _money(item) or _human_enum(item)
            elif label == "harga rata-rata":
                result[label] = _price(item) or _human_enum(item)
            else:
                result[label] = _human_enum(item)
        return result
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    upper = text.upper().replace(" ", "_")
    if upper in _ENUMS:
        return _ENUMS[upper]
    if "_" in text and text.upper() == text:
        return text.replace("_", " ").lower()
    return text


def _put(section: dict[str, Any], label: str, value: Any) -> None:
    if value not in (None, "", [], {}):
        section[label] = value


def build_presentation_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Return a human-readable prompt context while preserving raw facts elsewhere.

    The caller should retain the original context for validation/audit. This
    representation is presentation-only and may use executable IDX tick rounding,
    percent/money abbreviations, and translated enum labels.
    """
    facts = dict(context.get("facts") or {})
    identity = dict(context.get("identity") or {})
    final_result = dict(context.get("final_result") or {})

    presentation: dict[str, Any] = {
        "aturan": (
            "Semua angka di bawah adalah representasi presentation dari fakta SDE resmi. "
            "Gunakan nilainya apa adanya; jangan membuat level atau angka baru dan jangan menyebut nama field internal."
        ),
        "identitas": {
            "emiten": str(identity.get("symbol") or facts.get("symbol") or "").upper(),
            "tanggal analisis": _date_label(identity.get("trade_date") or facts.get("trade_date")),
        },
    }

    result: dict[str, Any] = {}
    _put(result, "keputusan SDE", _human_enum(final_result.get("decision") or facts.get("decision")))
    _put(result, "skor final", _score(final_result.get("final_score") or facts.get("confidence")))
    presentation["hasil SDE"] = result

    technical: dict[str, Any] = {}
    _put(technical, "setup", _human_enum(facts.get("setup")))
    _put(technical, "tren", _human_enum(facts.get("trend")))
    _put(technical, "kondisi teknikal", _human_enum(facts.get("technical_state") or facts.get("technical_status")))
    _put(technical, "technical score", _score(facts.get("technical_score") or facts.get("technical_quality")))
    rsi = _number(facts.get("rsi"))
    _put(technical, "RSI", _id_number(rsi, 2) if rsi is not None else "")
    _put(technical, "momentum", _human_enum(facts.get("momentum_status")))
    volume_ratio = _number(facts.get("volume_ratio_ma20"))
    _put(technical, "volume vs MA20", f"{_id_number(volume_ratio, 2)}x" if volume_ratio is not None else "")
    _put(technical, "fase", _human_enum(facts.get("phase")))
    _put(technical, "support", _price(facts.get("support")))
    _put(technical, "resistance", _price(facts.get("resistance")))
    presentation["teknikal"] = technical

    plan: dict[str, Any] = {}
    _put(plan, "harga terakhir", _price(facts.get("last_price")))
    low = _price(facts.get("entry_low"))
    high = _price(facts.get("entry_high"))
    _put(plan, "area entry", f"{low}–{high}" if low and high else low or high)
    _put(plan, "stop loss", _price(facts.get("active_stop_loss") or facts.get("stop_loss")))
    _put(plan, "TP1", _price(facts.get("target_1")))
    _put(plan, "TP2", _price(facts.get("target_2")))
    _put(plan, "risk-reward", _rr(facts.get("risk_reward")))
    _put(plan, "jarak ke entry", _percent(facts.get("entry_distance_pct"), ratio=False))
    _put(plan, "trigger", _human_enum(facts.get("trigger_description")))
    _put(plan, "trigger yang masih ditunggu", _human_enum(facts.get("waiting_triggers")))
    _put(plan, "invalidation", _human_enum(facts.get("invalidation")))
    presentation["trade plan"] = plan

    broker: dict[str, Any] = {}
    _put(broker, "arah", _human_enum(facts.get("broker_direction") or facts.get("broker_state")))
    _put(broker, "status", _human_enum(facts.get("broker_status")))
    _put(broker, "broker score", _score(facts.get("broker_score") or facts.get("broker_confidence")))
    _put(broker, "net flow", _money(facts.get("broker_net_flow")))
    _put(broker, "buyer concentration", _percent(facts.get("buyer_concentration"), ratio=True))
    _put(broker, "seller concentration", _percent(facts.get("seller_concentration"), ratio=True))
    _put(broker, "buy ratio", _percent(facts.get("broker_buy_ratio"), ratio=True))
    _put(broker, "sell ratio", _percent(facts.get("broker_sell_ratio"), ratio=True))
    _put(broker, "bandar buy cost", _price(facts.get("bandar_buy_cost") or facts.get("avg_buyer_price")))
    _put(broker, "jarak ke buy cost", _percent(facts.get("distance_to_buy_cost") or facts.get("distance_to_buyer_avg_pct"), ratio=False))
    _put(broker, "alignment", _human_enum(facts.get("broker_alignment")))
    _put(broker, "multi-day flow", _human_enum(facts.get("multi_day_flow")))
    _put(broker, "persistence", _human_enum(facts.get("flow_persistence")))
    _put(broker, "buy days", _human_enum(facts.get("buy_days")))
    _put(broker, "sell days", _human_enum(facts.get("sell_days")))
    _put(broker, "periode", _human_enum(facts.get("broker_period_type")))
    _put(broker, "cakupan periode", _human_enum(facts.get("broker_period_coverage") or facts.get("broker_coverage_text")))
    _put(broker, "status cakupan", _human_enum(facts.get("broker_coverage_status")))
    _put(broker, "today pulse", _human_enum(facts.get("today_pulse_status")))
    _put(broker, "top buyers", _human_enum(facts.get("top_buyers")))
    _put(broker, "top sellers", _human_enum(facts.get("top_sellers")))
    presentation["broker"] = broker

    market: dict[str, Any] = {}
    _put(market, "market regime", _human_enum(facts.get("market_regime")))
    _put(market, "sector", _human_enum(facts.get("sector_state")))
    _put(market, "risk flags", _human_enum(facts.get("risk_flags")))
    _put(market, "exchange status", _human_enum(facts.get("exchange_status")))
    _put(market, "alasan utama", _human_enum(facts.get("engine_final_reason") or facts.get("main_reason")))
    _put(market, "risiko utama", _human_enum(facts.get("main_risk") or facts.get("risk_items")))
    presentation["konteks pasar dan risiko"] = market

    presentation["chart"] = {
        "tersedia": bool(context.get("chart")),
        "fungsi": "konteks visual; angka resmi tetap berasal dari fakta SDE presentation di atas",
    }
    return presentation


__all__ = ["build_presentation_context"]
