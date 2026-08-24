from __future__ import annotations

import math
import re
from datetime import date
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
    "broker_coverage", "coverage",
}
_PERCENT_VALUE_FIELDS = {
    "entry_distance_pct", "distance_to_buy_cost", "distance_to_buyer_avg_pct",
}
_SCORE_FIELDS = {
    "confidence", "technical_quality", "technical_score", "entry_readiness",
    "broker_confidence", "broker_score",
}
_MISSING_BROKER_STATUS = {
    "INSUFFICIENT_DATA", "NO_DATA", "NOT_AVAILABLE", "MISSING", "UNKNOWN",
}

_ENUMS = {
    "ENTRY_NOT_TRIGGERED": "trigger entry belum terpenuhi",
    "ENTRY_TRIGGERED": "trigger entry sudah terpenuhi",
    "ALIGNED_POSITIVE": "PRIMARY dan TODAY 1D searah positif",
    "ALIGNED_NEGATIVE": "PRIMARY dan TODAY 1D searah negatif",
    "POSITIVE_DIVERGENCE": "PRIMARY negatif, tetapi TODAY 1D positif",
    "NEGATIVE_DIVERGENCE": "PRIMARY positif, tetapi TODAY 1D negatif",
    "INSUFFICIENT": "belum dapat dibandingkan",
    "INSUFFICIENT_DATA": "data belum cukup",
    "ACCUMULATION": "akumulasi",
    "DISTRIBUTION": "distribusi",
    "ACC": "akumulasi",
    "DIST": "distribusi",
    "NORMAL_ACC": "akumulasi normal",
    "NORMAL_DIST": "distribusi normal",
    "BIG_ACC": "akumulasi besar",
    "BIG_DIST": "distribusi besar",
    "POSITIVE": "positif",
    "NEGATIVE": "negatif",
    "NEUTRAL": "netral",
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
    "NOT_APPLICABLE": "tidak berlaku",
    "AVAILABLE": "tersedia",
    "CURRENT": "terkini",
    "NORMAL": "normal",
}
_FIELD_TOKENS = {
    "target_1": "TP1",
    "target_2": "TP2",
    "stop_loss": "stop loss",
    "active_stop_loss": "stop loss aktif",
    "entry_low": "batas bawah entry",
    "entry_high": "batas atas entry",
    "broker_net_flow": "net flow broker",
    "broker_alignment": "keselarasan broker",
    "risk_reward": "risk-reward",
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
    return _id_number(rounded, 0) if rounded is not None else ""


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
    return f"{_id_number(number, 1)}/100" if number is not None else ""


def _rr(value: Any) -> str:
    number = _number(value)
    return f"1:{_id_number(number, 2)}" if number is not None else ""


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
            "broker": "broker", "code": "broker", "name": "broker",
            "value": "nilai", "net_value": "nilai", "amount": "nilai",
            "avg_price": "harga rata-rata", "average_price": "harga rata-rata",
            "classification": "klasifikasi", "broker_type": "klasifikasi",
            "type": "klasifikasi", "origin": "klasifikasi",
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


def _formatted_fact(field: str, value: Any) -> str:
    if field in _PRICE_FIELDS:
        return _price(value)
    if field in _MONEY_FIELDS:
        return _money(value)
    if field in _PERCENT_RATIO_FIELDS:
        return _percent(value, ratio=True)
    if field in _PERCENT_VALUE_FIELDS:
        return _percent(value, ratio=False)
    if field in _SCORE_FIELDS:
        return _score(value)
    if field == "risk_reward":
        return _rr(value)
    if field == "rsi":
        number = _number(value)
        return _id_number(number, 2) if number is not None else ""
    if field == "volume_ratio_ma20":
        number = _number(value)
        return f"{_id_number(number, 2)}x" if number is not None else ""
    return ""


def _human_note(value: Any, facts: Mapping[str, Any]) -> Any:
    if value in (None, "", [], {}):
        return value
    if isinstance(value, list):
        return [_human_note(item, facts) for item in value]
    if not isinstance(value, str):
        return _human_enum(value)

    text = value.strip()
    if not text:
        return text

    replacements: list[tuple[str, str]] = []
    for field, raw in facts.items():
        rendered = _formatted_fact(str(field), raw)
        raw_text = str(raw).strip()
        if rendered and raw_text:
            replacements.append((raw_text, rendered))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for raw, rendered in replacements:
        text = text.replace(raw, rendered)

    for token, rendered in _FIELD_TOKENS.items():
        text = re.sub(rf"\b{re.escape(token)}\b", rendered, text, flags=re.IGNORECASE)
    for token, rendered in _ENUMS.items():
        text = re.sub(rf"\b{re.escape(token)}\b", rendered, text, flags=re.IGNORECASE)

    text = re.sub(
        r"\b([A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+)\b",
        lambda match: match.group(1).replace("_", " ").lower(),
        text,
    )
    return text


def _put(section: dict[str, Any], label: str, value: Any) -> None:
    if value not in (None, "", [], {}):
        section[label] = value


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _today_available(facts: Mapping[str, Any]) -> bool:
    status = str(facts.get("today_pulse_status") or "").strip().upper().replace(" ", "_")
    if status in {"AVAILABLE", "VALID", "CURRENT"}:
        return True
    if status in {"NOT_APPLICABLE", "NOT_AVAILABLE", "MISSING"}:
        return False
    return _flag(facts.get("today_pulse_available"))


def build_presentation_context(context: Mapping[str, Any]) -> dict[str, Any]:
    """Build provider-facing facts while keeping the original context authoritative.

    Raw facts stay outside this object for audit and response validation. This
    layer uses the same presentation principles as the Telegram watchlist: IDX
    executable tick display, compact money/percent notation, and human status text.
    Broker facts are emitted once as PRIMARY plus an optional TODAY 1D pulse;
    operational coverage/provenance remains in the raw audit context instead of
    becoming trading evidence in the provider prompt.
    """
    facts = dict(context.get("facts") or {})
    identity = dict(context.get("identity") or {})
    final_result = dict(context.get("final_result") or {})

    presentation: dict[str, Any] = {
        "aturan": (
            "Semua angka berikut adalah representasi presentation dari fakta SDE resmi. "
            "Gunakan apa adanya; jangan membuat level/angka baru dan jangan menyebut nama field internal."
        ),
        "identitas": {
            "emiten": str(identity.get("symbol") or facts.get("symbol") or "").upper(),
            "tanggal analisis": _date_label(identity.get("trade_date") or facts.get("trade_date")),
        },
    }

    result: dict[str, Any] = {}
    _put(result, "keputusan SDE", _human_enum(_first_present(final_result.get("decision"), facts.get("decision"))))
    _put(result, "skor final", _score(_first_present(final_result.get("final_score"), facts.get("confidence"))))
    presentation["hasil SDE"] = result

    technical: dict[str, Any] = {}
    _put(technical, "setup", _human_enum(facts.get("setup")))
    _put(technical, "tren", _human_enum(facts.get("trend")))
    _put(technical, "kondisi teknikal", _human_enum(_first_present(facts.get("technical_state"), facts.get("technical_status"))))
    _put(technical, "skor teknikal", _score(_first_present(facts.get("technical_score"), facts.get("technical_quality"))))
    rsi = _number(facts.get("rsi"))
    _put(technical, "RSI", _id_number(rsi, 2) if rsi is not None else "")
    _put(technical, "momentum", _human_enum(facts.get("momentum_status")))
    volume_ratio = _number(facts.get("volume_ratio_ma20"))
    _put(technical, "volume dibanding MA20", f"{_id_number(volume_ratio, 2)}x" if volume_ratio is not None else "")
    _put(technical, "fase", _human_enum(facts.get("phase")))
    _put(technical, "support", _price(facts.get("support")))
    _put(technical, "resistance", _price(facts.get("resistance")))
    presentation["teknikal"] = technical

    plan: dict[str, Any] = {}
    _put(plan, "harga terakhir", _price(facts.get("last_price")))
    low = _price(facts.get("entry_low"))
    high = _price(facts.get("entry_high"))
    _put(plan, "area entry", f"{low}–{high}" if low and high else low or high)
    _put(plan, "stop loss", _price(_first_present(facts.get("active_stop_loss"), facts.get("stop_loss"))))
    _put(plan, "TP1", _price(facts.get("target_1")))
    _put(plan, "TP2", _price(facts.get("target_2")))
    _put(plan, "risk-reward", _rr(facts.get("risk_reward")))
    _put(plan, "jarak ke entry", _percent(facts.get("entry_distance_pct"), ratio=False))
    _put(plan, "trigger", _human_note(facts.get("trigger_description"), facts))
    _put(plan, "trigger yang masih ditunggu", _human_note(facts.get("waiting_triggers"), facts))
    _put(plan, "invalidation", _human_note(facts.get("invalidation"), facts))
    presentation["trade plan"] = plan

    primary: dict[str, Any] = {}
    _put(primary, "periode", _human_enum(facts.get("broker_period_type")))
    start = _date_label(facts.get("broker_period_start"))
    end = _date_label(facts.get("broker_period_end"))
    if start and end:
        _put(primary, "rentang", start if start == end else f"{start} s/d {end}")
    _put(primary, "sesi perdagangan", facts.get("broker_trading_days"))
    _put(primary, "arah", _human_enum(_first_present(facts.get("broker_direction"), facts.get("broker_state"))))
    broker_status_raw = _first_present(facts.get("broker_status"), facts.get("broker_state"))
    _put(primary, "status", _human_enum(broker_status_raw))
    broker_score_raw = _first_present(facts.get("broker_score"), facts.get("broker_confidence"))
    broker_score_num = _number(broker_score_raw)
    broker_status_code = str(broker_status_raw or "").strip().upper().replace(" ", "_")
    if not (broker_score_num == 0 and broker_status_code in _MISSING_BROKER_STATUS):
        _put(primary, "skor broker", _score(broker_score_raw))
    _put(primary, "net flow", _money(facts.get("broker_net_flow")))
    _put(primary, "pola Acc/Dist", _human_enum(_first_present(facts.get("broker_pattern"), facts.get("avg_accdist"))))
    _put(primary, "konsentrasi buyer", _percent(facts.get("buyer_concentration"), ratio=True))
    _put(primary, "konsentrasi seller", _percent(facts.get("seller_concentration"), ratio=True))
    _put(primary, "buy ratio", _percent(facts.get("broker_buy_ratio"), ratio=True))
    _put(primary, "sell ratio", _percent(facts.get("broker_sell_ratio"), ratio=True))
    _put(primary, "rata-rata biaya buyer", _price(_first_present(facts.get("bandar_buy_cost"), facts.get("avg_buyer_price"))))
    _put(primary, "jarak harga ke biaya buyer", _percent(_first_present(facts.get("distance_to_buy_cost"), facts.get("distance_to_buyer_avg_pct")), ratio=False))
    _put(primary, "top buyers", _human_enum(facts.get("top_buyers")))
    _put(primary, "top sellers", _human_enum(facts.get("top_sellers")))

    broker: dict[str, Any] = {"PRIMARY": primary}
    if _today_available(facts):
        today: dict[str, Any] = {}
        _put(today, "tanggal", _date_label(facts.get("today_pulse_date")))
        _put(today, "status", _human_enum(facts.get("today_pulse_status")))
        _put(today, "net flow", _money(facts.get("today_pulse_net_flow")))
        _put(today, "arah", _human_enum(_first_present(facts.get("today_pulse_direction"), facts.get("today_pulse_broker_state"))))
        _put(today, "pola Acc/Dist", _human_enum(facts.get("today_pulse_avg_accdist")))
        _put(today, "konsentrasi buyer", _percent(facts.get("today_pulse_buyer_concentration"), ratio=True))
        _put(today, "konsentrasi seller", _percent(facts.get("today_pulse_seller_concentration"), ratio=True))
        _put(today, "top buyers", _human_enum(facts.get("today_pulse_top_buyers")))
        _put(today, "top sellers", _human_enum(facts.get("today_pulse_top_sellers")))
        broker["TODAY 1D"] = today

        alignment_code = str(facts.get("broker_alignment") or "").strip().upper().replace(" ", "_")
        if alignment_code in {
            "ALIGNED_POSITIVE", "ALIGNED_NEGATIVE", "POSITIVE_DIVERGENCE", "NEGATIVE_DIVERGENCE",
        }:
            broker["ALIGNMENT"] = {"status": _human_enum(alignment_code)}
    presentation["broker"] = broker

    market: dict[str, Any] = {}
    _put(market, "kondisi pasar", _human_enum(facts.get("market_regime")))
    _put(market, "sektor", _human_enum(facts.get("sector_state")))
    _put(market, "risk flags", _human_note(facts.get("risk_flags"), facts))
    _put(market, "status bursa", _human_enum(facts.get("exchange_status")))
    _put(market, "alasan utama SDE", _human_note(_first_present(facts.get("engine_final_reason"), facts.get("main_reason")), facts))
    _put(market, "risiko utama SDE", _human_note(_first_present(facts.get("main_risk"), facts.get("risk_items")), facts))
    presentation["konteks pasar dan risiko"] = market

    presentation["chart"] = {
        "tersedia": bool(context.get("chart")),
        "fungsi": "konteks visual; angka resmi tetap berasal dari fakta presentation di atas",
    }
    return presentation


__all__ = ["build_presentation_context"]