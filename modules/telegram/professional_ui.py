#!/usr/bin/env python3
"""Centralized professional Telegram UI formatter for SDE Swing.

This module renders the final SDE decisions and validated entry plans.
BUY CONFIRMED is shown only when a BUY/STRONG BUY decision also has a READY plan.
"""
from __future__ import annotations

import html
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from modules.broker_bridge.broker_raw import normalize_broker_code, normalize_broker_raw_frame

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━"
MISSING = "data tidak tersedia"

PUBLIC_STATUS_MAP = {
    "BUY READY": "BUY CONFIRMED",
    "BUY ON TRIGGER": "BUY CANDIDATE",
    "STRONG BUY": "BUY CANDIDATE",
    "BUY": "BUY CANDIDATE",
    "BUY CANDIDATE": "BUY CANDIDATE",
    "WATCH HIGH": "WATCH HIGH",
    "WATCH": "WATCH",
    "SPECULATIVE": "WATCH",
    "AVOID": "AVOID",
    "HOLD": "HOLD",
    "TAKE PROFIT": "TAKE PROFIT",
}

STATUS_ICON_MAP = {
    "BUY CONFIRMED": "🟢",
    "BUY CANDIDATE": "🟠",
    "WATCH HIGH": "🟡",
    "WATCH": "🔵",
    "HOLD": "🟢",
    "TAKE PROFIT": "🟡",
    "AVOID": "🔴",
    "DATA WARNING": "⚠️",
}

INDONESIAN_MONTHS = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
    5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
    9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}
INDONESIAN_DAYS = {
    0: "Senin", 1: "Selasa", 2: "Rabu", 3: "Kamis",
    4: "Jumat", 5: "Sabtu", 6: "Minggu",
}


@dataclass(frozen=True)
class UiConfig:
    report_title: str = "SDE SWING"
    separator: str = SEPARATOR
    max_watchlist_items: int = 9
    max_buy_confirmed: int = 3
    max_buy_candidate: int = 3
    max_watch_high: int = 3
    max_post_market_candidates: int = 5
    max_broker_detail_rows: int = 3
    max_message_length: int = 4000
    show_disclaimer: bool = True
    show_global_sentiment: bool = True
    show_broker_flow: bool = True
    show_entry_setup: bool = True
    show_score: bool = True
    parse_mode: str = "HTML"
    timezone: str = "Asia/Jakarta"
    topic_routing: dict[str, str] = field(default_factory=dict)
    compact_mode: bool = True
    debug_mode: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "UiConfig":
        raw = data or {}
        valid = {field for field in cls.__dataclass_fields__}
        kwargs = {key: value for key, value in raw.items() if key in valid}
        return cls(**kwargs)


def esc(value: Any) -> str:
    return html.escape(str(value), quote=False)


def normalize_text(value: Any, fallback: str = MISSING) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "nat", "data_not_available", "belum tersedia"}:
        return fallback
    return text


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null"}:
            return None
        number = float(text)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def fmt_number(value: Any, decimals: int = 0, fallback: str = MISSING) -> str:
    number = to_float(value)
    if number is None:
        return fallback
    formatted = f"{number:,.{decimals}f}"
    integer, dot, fraction = formatted.partition(".")
    integer = integer.replace(",", ".")
    return integer if decimals == 0 else f"{integer},{fraction}"


def fmt_pct(value: Any, decimals: int = 2, signed: bool = True, fallback: str = MISSING) -> str:
    number = to_float(value)
    if number is None:
        return fallback
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.{decimals}f}%".replace(".", ",")


def fmt_money(value: Any, fallback: str = MISSING) -> str:
    number = to_float(value)
    if number is None:
        return fallback
    absolute = abs(number)
    sign = "-" if number < 0 else "+" if number > 0 else ""
    if absolute >= 1_000_000_000:
        return f"{sign}Rp{absolute / 1_000_000_000:.2f} miliar".replace(".", ",")
    if absolute >= 1_000_000:
        return f"{sign}Rp{absolute / 1_000_000:.2f} juta".replace(".", ",")
    return f"{sign}Rp{absolute:,.0f}".replace(",", ".")


def fmt_date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return normalize_text(value)
    dt = parsed.to_pydatetime()
    return f"{INDONESIAN_DAYS[dt.weekday()]}, {dt.day} {INDONESIAN_MONTHS[dt.month]} {dt.year}"


def fmt_time(value: Any = None) -> str:
    jakarta = ZoneInfo("Asia/Jakarta")
    if value is None:
        return datetime.now(jakarta).strftime("%H:%M WIB")
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return "--:-- WIB"
    dt = parsed.to_pydatetime()
    if dt.tzinfo is not None:
        dt = dt.astimezone(jakarta)
    return dt.strftime("%H:%M WIB")


def norm_col(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def find_col(df: pd.DataFrame, *aliases: str) -> str | None:
    if df.empty and not len(df.columns):
        return None
    mapping = {norm_col(col): col for col in df.columns}
    for alias in aliases:
        found = mapping.get(norm_col(alias))
        if found is not None:
            return found
    return None


def row_value(row: pd.Series | dict[str, Any], *aliases: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        lowered = {norm_col(key): value for key, value in row.items()}
        for alias in aliases:
            current = lowered.get(norm_col(alias))
            if current is not None and normalize_text(current, ""):
                return current
        return default
    mapping = {norm_col(col): col for col in row.index}
    for alias in aliases:
        col = mapping.get(norm_col(alias))
        if col is not None:
            current = row[col]
            if pd.notna(current) and normalize_text(current, ""):
                return current
    return default


def public_status(raw: Any) -> str:
    decision = normalize_text(raw, "WATCH").upper()
    return PUBLIC_STATUS_MAP.get(decision, decision)


def status_icon(status: Any) -> str:
    return STATUS_ICON_MAP.get(public_status(status), "🔵")


def effective_public_status(raw: Any, plan: pd.Series | None = None) -> str:
    """Combine engine decision and validated entry plan for final presentation."""
    base = public_status(raw)
    current_plan = plan if plan is not None else pd.Series(dtype=object)
    if not current_plan.empty:
        final_status = normalize_text(row_value(current_plan, "Decision_Status_Final"), "").upper()
        if final_status:
            return public_status(final_status)
    readiness, _ = plan_readiness(current_plan) if not current_plan.empty else ("WAITING", "")
    raw_decision = normalize_text(raw, "WATCH").upper()
    if raw_decision in {"BUY READY"}:
        return "BUY CONFIRMED"
    if raw_decision in {"BUY ON TRIGGER"}:
        return "BUY CANDIDATE"
    if raw_decision in {"STRONG BUY", "BUY"} and readiness == "READY":
        return "BUY CONFIRMED"
    if raw_decision in {"STRONG BUY", "BUY"} and readiness != "READY":
        return "BUY CANDIDATE"
    return base


def regime_icon(regime: Any) -> str:
    state = normalize_text(regime, "UNKNOWN").upper()
    if state in {"STRONG BULLISH", "BULL", "BULLISH", "RISK_ON"}:
        return "🟢"
    if state == "EARLY BULLISH":
        return "🟢"
    if state in {"STRONG BEARISH", "BEAR", "BEARISH", "RISK_OFF"}:
        return "🔴"
    if state == "EARLY BEARISH":
        return "🟠"
    if state in {"SIDEWAYS", "NEUTRAL", "MIXED"}:
        return "🟡"
    return "⚪"


def unique_final_decisions(decisions: pd.DataFrame) -> pd.DataFrame:
    if decisions.empty:
        return decisions.copy()
    result = decisions.copy()
    symbol_col = find_col(result, "Symbol", "EMITEN", "Ticker")
    rank_col = find_col(result, "Rank_V3", "Rank")
    score_col = find_col(result, "Final_Score_V3", "Final_Score")
    if rank_col:
        result[rank_col] = pd.to_numeric(result[rank_col], errors="coerce")
        result = result.sort_values(rank_col, na_position="last")
    elif score_col:
        result[score_col] = pd.to_numeric(result[score_col], errors="coerce")
        result = result.sort_values(score_col, ascending=False, na_position="last")
    if symbol_col:
        result[symbol_col] = result[symbol_col].astype(str).str.upper().str.strip()
        result = result[result[symbol_col].ne("")].drop_duplicates(symbol_col, keep="first")
    return result.reset_index(drop=True)


def public_counts(decisions: pd.DataFrame, entry_plans: pd.DataFrame | None = None) -> dict[str, int]:
    work = unique_final_decisions(decisions)
    plans = entry_plans if entry_plans is not None else pd.DataFrame()
    counts = {"BUY CONFIRMED": 0, "BUY CANDIDATE": 0, "WATCH HIGH": 0, "WATCH": 0, "AVOID": 0}
    decision_col = find_col(work, "Decision_Status_Final", "Decision_Status", "Decision_V3", "Decision")
    symbol_col = find_col(work, "Symbol", "EMITEN", "Ticker")
    if not decision_col:
        return counts
    for _, row in work.iterrows():
        plan = plan_for_symbol(plans, str(row[symbol_col])) if symbol_col else pd.Series(dtype=object)
        status = effective_public_status(row[decision_col], plan)
        if status in counts:
            counts[status] += 1
        elif status in {"HOLD", "TAKE PROFIT"}:
            counts["WATCH"] += 1
    return counts


def signal_bar(counts: dict[str, int], width: int = 20) -> str:
    total = sum(max(0, int(value)) for value in counts.values())
    if total <= 0:
        return "⚪" * min(width, 10)
    positive = counts.get("BUY CONFIRMED", 0)
    candidate = counts.get("BUY CANDIDATE", 0)
    watch_high = counts.get("WATCH HIGH", 0)
    watch = counts.get("WATCH", 0)
    avoid = counts.get("AVOID", 0)
    raw = [positive, candidate + watch_high + watch, avoid]
    slots = [int(round(value / total * width)) for value in raw]
    diff = width - sum(slots)
    if diff:
        largest = max(range(len(raw)), key=lambda idx: raw[idx])
        slots[largest] += diff
    return "🟢" * max(slots[0], 0) + "🟡" * max(slots[1], 0) + "🔴" * max(slots[2], 0)


def broker_flow_summary(decisions: pd.DataFrame) -> str:
    work = unique_final_decisions(decisions)
    col = find_col(work, "Broker_Confirmation")
    if not col:
        return MISSING
    values = work[col].astype(str).str.upper()
    positive = values.str.contains("ACCUMULATION|ACCUMULAT|BUY", regex=True).sum()
    negative = values.str.contains("DISTRIBUTION|DISTRIB|SELL", regex=True).sum()
    if positive > negative * 1.5 and positive > 0:
        return "mendukung akumulasi"
    if negative > positive and negative > 0:
        return "cenderung distribusi"
    if positive or negative:
        return "campuran / selektif"
    return "netral"


def market_breadth(technical: pd.DataFrame) -> str:
    if technical.empty:
        return MISSING
    above_col = find_col(technical, "Above_SMA20")
    if not above_col:
        return MISSING
    values = pd.to_numeric(technical[above_col], errors="coerce").dropna()
    if values.empty:
        return MISSING
    pct = float(values.mean() * 100)
    state = "positif" if pct >= 60 else "lemah" if pct < 40 else "campuran"
    return f"{pct:.0f}% di atas SMA20 — {state}"


def market_direction_narrative(counts: dict[str, int], regime: Any) -> str:
    positive = counts.get("BUY CONFIRMED", 0) + counts.get("BUY CANDIDATE", 0) + counts.get("WATCH HIGH", 0)
    watch = counts.get("WATCH", 0)
    avoid = counts.get("AVOID", 0)
    state = normalize_text(regime, "UNKNOWN").upper()
    if state in {"BEAR", "BEARISH", "RISK_OFF"} and positive:
        return "Market masih defensif. Hanya pertimbangkan kandidat terbaik dengan ukuran posisi terbatas dan stop loss disiplin."
    if positive > max(watch, avoid):
        return "Sinyal swing didominasi kandidat positif. Cari entry secara selektif pada setup terkonfirmasi dan broker flow yang mendukung."
    if avoid > max(positive, watch):
        return "Tekanan risiko masih tinggi dan mayoritas setup belum layak dieksekusi. Kurangi agresivitas dan prioritaskan perlindungan modal."
    return "Mayoritas setup masih dalam tahap pembentukan. Prioritaskan observasi dan tunggu konfirmasi sebelum melakukan entry."


def execution_guidance(raw_status: Any, plan_status: Any = "") -> str:
    status = public_status(raw_status)
    plan = normalize_text(plan_status, "").upper()
    if plan == "REJECT":
        return "Setup belum layak dieksekusi karena rencana entry gagal memenuhi guardrail. Tetap pantau, jangan memaksakan entry."
    if plan in {"ACCEPT", "ACCEPTED", "APPROVED", "VALID", "READY", "ACTIVE"}:
        return "Rencana entry sudah valid. Eksekusi hanya di area entry, gunakan stop loss yang ditetapkan, dan jangan mengejar harga."
    if status == "BUY CONFIRMED":
        return "Setup dan entry plan sudah terkonfirmasi. Entry bertahap hanya di area yang ditentukan; jangan mengejar harga."
    if status == "BUY CANDIDATE":
        return "Kualitas, timing, dan broker cukup mendukung. Tunggu area entry atau trigger yang ditetapkan sebelum eksekusi."
    if status == "WATCH HIGH":
        return "Kandidat berkualitas tinggi, tetapi masih membutuhkan pemicu tambahan. Masukkan ke prioritas pantauan."
    if status == "WATCH":
        return "Belum layak dieksekusi. Pantau trend, volume, broker flow, dan area konfirmasi."
    if status == "HOLD":
        return "Posisi dapat dipertahankan selama harga berada di atas level invalidation atau stop loss."
    if status == "TAKE PROFIT":
        return "Target atau kondisi profit-taking terpenuhi. Pertimbangkan realisasi profit sesuai rencana."
    if status == "AVOID":
        return "Tidak disarankan entry karena setup lemah, invalid, terlalu berisiko, atau tertekan distribusi."
    return "Tunggu data dan konfirmasi yang lebih lengkap sebelum mengambil keputusan."


def trend_state(row: pd.Series) -> str:
    explicit = normalize_text(row_value(row, "Technical_Regime", "Trend_State"), "")
    if explicit:
        return explicit.upper()
    above20 = to_float(row_value(row, "Above_SMA20"))
    above50 = to_float(row_value(row, "Above_SMA50"))
    if above20 == 1 and above50 == 1:
        return "BULLISH"
    if above20 == 0 and above50 == 0:
        return "BEARISH"
    return "SIDEWAYS / MIXED"


def momentum_state(row: pd.Series) -> str:
    rsi = to_float(row_value(row, "RSI_14"))
    macd = to_float(row_value(row, "MACD_Hist"))
    if rsi is None and macd is None:
        return MISSING
    if rsi is not None and rsi >= 70:
        return f"kuat tetapi overbought — RSI {rsi:.1f}"
    if rsi is not None and rsi < 40:
        return f"lemah — RSI {rsi:.1f}"
    if macd is not None and macd > 0:
        return f"positif — RSI {rsi:.1f}" if rsi is not None else "positif"
    return f"netral — RSI {rsi:.1f}" if rsi is not None else "netral"


def volume_state(row: pd.Series) -> str:
    ratio = to_float(row_value(row, "Volume_Ratio_20"))
    if ratio is None:
        return MISSING
    if ratio >= 1.5:
        return f"tinggi — {ratio:.2f}x MA20"
    if ratio >= 1.0:
        return f"mendukung — {ratio:.2f}x MA20"
    return f"di bawah rata-rata — {ratio:.2f}x MA20"


def broker_state(row: pd.Series) -> str:
    confirmation = normalize_text(row_value(row, "Broker_Confirmation"), MISSING)
    net_flow = row_value(row, "NET_FLOW", "Net_Flow")
    net = fmt_money(net_flow)
    confidence = fmt_number(row_value(row, "Broker_Confidence_Final", "Broker_Confidence"), 0)
    divergence = broker_flow_divergence(row)
    parts = [f"Pola {confirmation}"]
    if net != MISSING:
        parts.append(f"net {net}")
    if confidence != MISSING:
        parts.append(f"confidence {confidence}%")
    if divergence:
        parts.append("⚠️ DIVERGENCE")
    return " | ".join(parts)


def fmt_money_compact(value: Any, fallback: str = MISSING) -> str:
    """Compact rupiah notation used only in the watchlist overview."""
    number = to_float(value)
    if number is None:
        return fallback
    absolute = abs(number)
    sign = "-" if number < 0 else "+" if number > 0 else ""
    if absolute >= 1_000_000_000:
        return f"{sign}Rp{absolute / 1_000_000_000:.2f} M".replace(".", ",")
    if absolute >= 1_000_000:
        return f"{sign}Rp{absolute / 1_000_000:.2f} Jt".replace(".", ",")
    return f"{sign}Rp{absolute:,.0f}".replace(",", ".")


def compact_broker_state(row: pd.Series) -> str:
    confirmation = normalize_text(row_value(row, "Broker_Confirmation"), MISSING).upper()
    labels = {
        "STRONG ACCUMULATION": "Strong Acc",
        "ACCUMULATION": "Accumulation",
        "NEUTRAL": "Neutral",
        "DISTRIBUTION": "Distribution",
        "STRONG DISTRIBUTION": "Strong Dist",
    }
    label = labels.get(confirmation, confirmation.title() if confirmation != MISSING else MISSING)
    if broker_flow_divergence(row):
        label = "Divergence"
    confidence = fmt_number(row_value(row, "Broker_Confidence_Final", "Broker_Confidence"), 0)
    net = fmt_money_compact(row_value(row, "NET_FLOW", "Net_Flow"))
    parts = [label]
    if confidence != MISSING:
        parts.append(f"{confidence}%")
    if net != MISSING:
        parts.append(net)
    return " | ".join(parts)


def compact_setup_label(row: pd.Series) -> str:
    return setup_type_text(row).title()


def compact_trigger_text(status: str, entry: str) -> str:
    value = normalize_text(entry, "menunggu konfirmasi")
    lower = value.lower()
    if lower.startswith("tunggu close >"):
        level = value.split(">", 1)[1].strip()
        return f"Close > {level if level.startswith('Rp') else 'Rp' + level}"
    if lower.startswith("pantau area"):
        area = value[len("pantau area"):].strip()
        return f"Area {area if area.startswith('Rp') else 'Rp' + area}"
    if lower in {"menunggu konfirmasi", "menunggu trigger entry", "tunggu breakout valid"}:
        return value[:1].upper() + value[1:]
    prefix = "Entry" if status == "BUY CONFIRMED" else "Area"
    return f"{prefix} {value if value.startswith('Rp') else 'Rp' + value}"


def compact_watch_issue(row: pd.Series, readiness_reason: str) -> str:
    setup = setup_type_text(row)
    divergence = broker_flow_divergence(row)
    reason = normalize_text(readiness_reason, "")
    lowered = reason.lower()
    if divergence:
        return "Broker masih divergence"
    if setup == "DEVELOPING":
        return "Setup belum matang"
    if "resistance minor" in lowered:
        return "Resistance masih dekat"
    if "terlalu jauh" in lowered or "extended" in lowered:
        return "Harga terlalu extended"
    if ";" in reason:
        reason = reason.split(";", 1)[0]
    if not reason:
        return "Menunggu konfirmasi"
    return reason[:1].upper() + reason[1:]


def compact_watch_trigger(entry: str) -> tuple[str, str]:
    """Return a short label and value for an adaptive WATCH HIGH block."""
    rendered = compact_trigger_text("WATCH HIGH", entry)
    lowered = rendered.lower()
    if lowered.startswith("close >"):
        return "Trigger", rendered
    if lowered.startswith("area "):
        return "Area", rendered[5:].strip()
    if lowered.startswith("entry "):
        return "Area", rendered[6:].strip()
    return "Trigger", rendered


def compact_watch_blocker(row: pd.Series, readiness: str, readiness_reason: str) -> str:
    """Explain why WATCH HIGH is not yet a BUY without inventing a blocker."""
    issue = compact_watch_issue(row, readiness_reason)
    if readiness != "READY" or issue.lower() != "rencana entry valid":
        return issue

    divergence = broker_flow_divergence(row)
    if divergence:
        return "Broker masih divergence"

    confirmation = normalize_text(row_value(row, "Broker_Confirmation"), "").upper()
    if not confirmation or confirmation == "NEUTRAL":
        return "Broker belum memberi konfirmasi akumulasi"
    if "DISTRIBUTION" in confirmation:
        return "Broker flow belum mendukung"
    return "Skor atau konfirmasi final belum cukup untuk status BUY"


def compact_watch_status(row: pd.Series, readiness: str) -> str:
    if readiness == "READY":
        return "Plan teknikal valid, tetapi keputusan BUY belum terkonfirmasi"
    if readiness == "NOT READY":
        return "Belum layak entry"
    if setup_type_text(row) == "DEVELOPING":
        return "Pantau; jangan entry"
    return "Pantau; tunggu trigger dan konfirmasi"


def broker_flow_divergence(row: pd.Series) -> str:
    """Explain when the broker pattern and aggregate net flow point opposite ways.

    Broker_Confirmation is intentionally preserved because it is produced by the
    broker fusion engine from several signals. The UI only makes a contradictory
    aggregate NET_FLOW explicit instead of presenting both values as one flow.
    """
    confirmation = normalize_text(row_value(row, "Broker_Confirmation"), "").upper()
    net_flow = to_float(row_value(row, "NET_FLOW", "Net_Flow"))
    if net_flow is None or net_flow == 0:
        return ""
    if "ACCUMULATION" in confirmation and net_flow < 0:
        return "pola broker akumulasi, tetapi net flow agregat negatif"
    if "DISTRIBUTION" in confirmation and net_flow > 0:
        return "pola broker distribusi, tetapi net flow agregat positif"
    return ""


def broker_detail_lines(row: pd.Series) -> list[str]:
    confirmation = normalize_text(row_value(row, "Broker_Confirmation"), MISSING)
    net = fmt_money(row_value(row, "NET_FLOW", "Net_Flow"))
    divergence = broker_flow_divergence(row)
    confidence = fmt_number(row_value(row, "Broker_Confidence_Final", "Broker_Confidence"), 0)
    lines = [
        f"Broker Pattern : {confirmation}",
        f"Net Flow       : {net}",
        f"Confidence     : {confidence}%" if confidence != MISSING else "Confidence     : data tidak tersedia",
    ]
    if divergence:
        lines.append(f"Flow Status    : DIVERGENCE — {divergence}")
    else:
        lines.append("Flow Status    : SELARAS")
    return lines



def fmt_price(value: Any, fallback: str = MISSING) -> str:
    number = to_float(value)
    if number is None or number <= 0:
        return fallback
    return f"Rp{fmt_number(number, 0)}"


def fmt_money_abs(value: Any, fallback: str = MISSING) -> str:
    number = to_float(value)
    if number is None:
        return fallback
    return fmt_money(abs(number), fallback=fallback).lstrip("+")


def fmt_ratio_percent(value: Any, decimals: int = 0, fallback: str = MISSING) -> str:
    number = to_float(value)
    if number is None:
        return fallback
    if abs(number) <= 1.5:
        number *= 100
    return f"{number:.{decimals}f}%".replace(".", ",")


def _raw_value(item: pd.Series | dict[str, Any]) -> Any:
    value = row_value(item, "NET_VALUE")
    number = to_float(value)
    if number is not None and number != 0:
        return number
    return row_value(item, "GROSS_VALUE")


def broker_raw_rows(
    broker_raw: pd.DataFrame | None,
    symbol: str,
    side: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if broker_raw is None or broker_raw.empty:
        return []
    work = normalize_broker_raw_frame(broker_raw)
    if work.empty:
        return []
    normalized_symbol = normalize_text(symbol, "").upper().replace(".JK", "")
    work = work[(work["SYMBOL"] == normalized_symbol) & (work["SIDE"] == side.upper())].copy()
    if work.empty:
        return []
    work["__rank"] = pd.to_numeric(work["RANK"], errors="coerce").fillna(10_000)
    work["__value"] = pd.to_numeric(work["NET_VALUE"], errors="coerce").fillna(
        pd.to_numeric(work["GROSS_VALUE"], errors="coerce")
    ).abs().fillna(0)
    work = work.sort_values(["__rank", "__value"], ascending=[True, False], na_position="last")
    rows: list[dict[str, Any]] = []
    for _, item in work.head(max(1, limit)).iterrows():
        rows.append({
            "broker": normalize_broker_code(row_value(item, "BROKER_CODE", "BROKER")),
            "type": normalize_text(row_value(item, "BROKER_TYPE"), ""),
            "value": _raw_value(item),
            "avg_price": row_value(item, "AVG_PRICE"),
            "frequency": row_value(item, "FREQUENCY"),
            "rank": row_value(item, "RANK"),
            "raw_matched": True,
        })
    return rows


def broker_fallback_rows(row: pd.Series, side: str, limit: int = 3) -> list[dict[str, Any]]:
    prefix = "TOP_BUYER" if side.upper() == "BUY" else "TOP_SELLER"
    result: list[dict[str, Any]] = []
    for index in range(1, limit + 1):
        broker = normalize_broker_code(row_value(row, f"{prefix}_{index}"))
        if broker:
            result.append({
                "broker": broker, "type": "", "value": None, "avg_price": None,
                "frequency": None, "rank": index, "raw_matched": False,
            })
    return result


def broker_party_rows(
    row: pd.Series,
    broker_raw: pd.DataFrame | None,
    side: str,
    limit: int = 3,
) -> list[dict[str, Any]]:
    symbol = normalize_text(row_value(row, "Symbol", "EMITEN", "Ticker"), "?").upper()
    raw_rows = broker_raw_rows(broker_raw, symbol, side, max(limit, 100))
    expected = broker_fallback_rows(row, side, min(limit, 3))
    if not expected:
        return raw_rows[:limit]

    raw_by_broker = {normalize_broker_code(item.get("broker")): item for item in raw_rows}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fallback in expected:
        code = normalize_broker_code(fallback.get("broker"))
        result.append(raw_by_broker.get(code, fallback))
        if code:
            seen.add(code)
    if len(result) < limit:
        for item in raw_rows:
            code = normalize_broker_code(item.get("broker"))
            if not code or code in seen:
                continue
            result.append(item)
            seen.add(code)
            if len(result) >= limit:
                break
    return result[:limit]


def weighted_broker_average(rows: list[dict[str, Any]]) -> float | None:
    weighted = 0.0
    weight_sum = 0.0
    simple: list[float] = []
    for item in rows:
        price = to_float(item.get("avg_price"))
        if price is None or price <= 0:
            continue
        simple.append(price)
        weight = abs(to_float(item.get("value")) or 0.0)
        if weight > 0:
            weighted += price * weight
            weight_sum += weight
    if weight_sum > 0:
        return weighted / weight_sum
    return sum(simple) / len(simple) if simple else None


def broker_raw_coverage(
    row: pd.Series,
    broker_raw: pd.DataFrame | None,
    limit_per_side: int = 3,
) -> dict[str, int]:
    parties = broker_party_rows(row, broker_raw, "BUY", limit_per_side) + broker_party_rows(
        row, broker_raw, "SELL", limit_per_side
    )
    total = len(parties)
    matched = sum(bool(item.get("raw_matched")) for item in parties)
    avg = sum((to_float(item.get("avg_price")) or 0) > 0 for item in parties)
    value = sum(to_float(item.get("value")) is not None for item in parties)
    return {"total": total, "matched": matched, "avg": avg, "value": value}


def broker_raw_coverage_text(
    row: pd.Series,
    broker_raw: pd.DataFrame | None,
    limit_per_side: int = 3,
) -> str:
    coverage = broker_raw_coverage(row, broker_raw, limit_per_side)
    total = coverage["total"]
    if total <= 0:
        return "Raw coverage: data tidak tersedia"
    return (
        f"Raw coverage: {coverage['matched']}/{total} broker | "
        f"nilai {coverage['value']}/{total} | avg {coverage['avg']}/{total}"
    )


def broker_party_summary(
    row: pd.Series,
    broker_raw: pd.DataFrame | None,
    side: str,
    limit: int = 3,
) -> str:
    rows = broker_party_rows(row, broker_raw, side, limit)
    if not rows:
        return MISSING
    parts: list[str] = []
    for item in rows:
        broker = normalize_text(item.get("broker"), "?").upper()
        value = fmt_money_abs(item.get("value"))
        avg = fmt_price(item.get("avg_price"))
        details = [broker]
        if value != MISSING:
            details.append(value)
        details.append(f"@{avg}" if avg != MISSING else "@avg belum tersedia")
        parts.append(" ".join(details))
    return "; ".join(parts)


def broker_party_detail_lines(
    row: pd.Series,
    broker_raw: pd.DataFrame | None,
    side: str,
    limit: int = 3,
) -> list[str]:
    rows = broker_party_rows(row, broker_raw, side, limit)
    if not rows:
        return ["• Data broker tidak tersedia"]
    lines: list[str] = []
    for index, item in enumerate(rows, 1):
        broker = normalize_text(item.get("broker"), "?").upper()
        broker_type = normalize_text(item.get("type"), "")
        value = fmt_money_abs(item.get("value"))
        avg = fmt_price(item.get("avg_price"))
        details = []
        details.append(value if value != MISSING else "Nilai belum tersedia")
        details.append(f"Avg {avg}" if avg != MISSING else "Avg belum tersedia")
        if broker_type:
            details.append(broker_type)
        lines.append(f"{index}. {broker} — " + " | ".join(details))
    return lines


def broker_position_lines(row: pd.Series, broker_raw: pd.DataFrame | None) -> list[str]:
    buyers = broker_party_rows(row, broker_raw, "BUY", 100)
    sellers = broker_party_rows(row, broker_raw, "SELL", 100)
    buy_avg = weighted_broker_average(buyers)
    sell_avg = weighted_broker_average(sellers)
    close = to_float(row_value(row, "Close", "Reference_Close", "Current_Price"))
    distance = ((close - buy_avg) / buy_avg * 100) if close is not None and buy_avg not in (None, 0) else None
    return [
        f"Avg Buyer     : {fmt_price(buy_avg)} (weighted)",
        f"Avg Seller    : {fmt_price(sell_avg)} (weighted)",
        f"Harga terakhir: {fmt_price(close)}",
        f"Jarak buy avg : {fmt_pct(distance, 2) if distance is not None else MISSING}",
        broker_raw_coverage_text(row, broker_raw, 3),
    ]


def broker_overview_lines(row: pd.Series) -> list[str]:
    direction = normalize_text(row_value(row, "Broker_Direction_Final", "Broker_Direction", "Broker_Confirmation"), MISSING).upper()
    confirmation = normalize_text(row_value(row, "Broker_Confirmation"), direction).upper()
    confidence = fmt_number(row_value(row, "Broker_Confidence_Final", "Broker_Confidence"), 0)
    net = fmt_money(row_value(row, "NET_FLOW", "Net_Flow"))
    buy_conc = fmt_ratio_percent(row_value(row, "BUYER_CONCENTRATION"), 0)
    sell_conc = fmt_ratio_percent(row_value(row, "SELLER_CONCENTRATION"), 0)
    divergence = broker_flow_divergence(row)
    return [
        f"Status     : {confirmation}",
        f"Direction  : {direction}",
        f"Confidence : {confidence}%" if confidence != MISSING else "Confidence : data tidak tersedia",
        f"Net Flow   : {net}",
        f"Buy / Sell : {buy_conc} / {sell_conc}",
        f"Flow Status: DIVERGENCE — {divergence}" if divergence else "Flow Status: SELARAS",
    ]


def setup_type_text(row: pd.Series) -> str:
    value = normalize_text(row_value(row, "Setup_Type", "Setup_Label"), "DEVELOPING")
    return value.upper().replace("_", " ")


def _effective_status_series(ranked: pd.DataFrame, entry_plans: pd.DataFrame) -> pd.Series:
    symbol_col = find_col(ranked, "Symbol", "EMITEN", "Ticker")
    decision_col = find_col(ranked, "Decision_V3", "Decision")
    if not symbol_col or not decision_col:
        return pd.Series(["WATCH"] * len(ranked), index=ranked.index)
    return ranked.apply(
        lambda item: effective_public_status(item[decision_col], plan_for_symbol(entry_plans, str(item[symbol_col]))),
        axis=1,
    )


def select_final_watchlist_rows(decisions: pd.DataFrame, entry_plans: pd.DataFrame, cfg: UiConfig) -> pd.DataFrame:
    ranked = rank_watchlist(decisions, entry_plans)
    if ranked.empty:
        return ranked
    ranked = ranked.copy()
    ranked["__report_status"] = _effective_status_series(ranked, entry_plans)
    groups = []
    limits = {
        "BUY CONFIRMED": cfg.max_buy_confirmed,
        "BUY CANDIDATE": cfg.max_buy_candidate,
        "WATCH HIGH": cfg.max_watch_high,
    }
    for status, limit in limits.items():
        group = ranked[ranked["__report_status"] == status].head(max(0, int(limit)))
        if not group.empty:
            groups.append(group)
    return pd.concat(groups, ignore_index=True) if groups else ranked.head(0)


def short_reason(row: pd.Series, max_chars: int = 170) -> str:
    text = normalize_text(row_value(row, "Decision_Reasons", "Candidate_Reason", "Broker_Reasons"), MISSING)
    # Net flow is already displayed in a dedicated, localized field. Remove the
    # raw integer duplicate from narrative reasons to keep Telegram concise.
    text = re.sub(
        r"(?i)(?:^|;\s*)net\s*flow\s*[+-]?\s*[\d.,]+(?:\s*(?:juta|miliar|million|billion))?",
        "",
        text,
    )
    # Broker pattern and confidence already have dedicated fields. Remove terse
    # raw tokens such as "Acc; Big Acc" from the human-readable conclusion.
    text = re.sub(
        r"(?i)(?:^|;\s*)(?:big|normal|small)?\s*(?:acc|dist|accumulation|distribution)(?=;|$)",
        "",
        text,
    )
    text = re.sub(r"(?i)(?:^|;\s*)confidence\s*\d+(?:[.,]\d+)?%?", "", text)
    text = re.sub(r"\s*;\s*;\s*", "; ", text)
    text = re.sub(r"\s+", " ", text).strip(" ;")
    if not text:
        text = "setup teknikal dan broker sedang dievaluasi"
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def plan_for_symbol(entry_plans: pd.DataFrame, symbol: str) -> pd.Series:
    if entry_plans.empty:
        return pd.Series(dtype=object)
    col = find_col(entry_plans, "Symbol", "EMITEN")
    if not col:
        return pd.Series(dtype=object)
    found = entry_plans[entry_plans[col].astype(str).str.upper().str.strip() == symbol.upper()]
    return found.iloc[0] if not found.empty else pd.Series(dtype=object)


def valid_plan(plan: pd.Series) -> bool:
    if plan.empty:
        return False
    status = normalize_text(row_value(plan, "Plan_Status"), "").upper()
    levels = [row_value(plan, "Entry_Zone_Low"), row_value(plan, "Entry_Zone_High"), row_value(plan, "Initial_Stop"), row_value(plan, "Target_1")]
    return status in {"ACCEPT", "ACCEPTED", "APPROVED", "VALID", "READY", "ACTIVE"} and all(to_float(value) is not None for value in levels)


def plan_readiness(plan: pd.Series) -> tuple[str, str]:
    if plan.empty:
        return "WAITING", "rencana entry belum tersedia"
    status = normalize_text(row_value(plan, "Plan_Status"), "WAITING").upper()
    reason_code = normalize_text(row_value(plan, "Rejection_Reason"), "")
    reason_map = {
        "NEAREST_RESISTANCE_BELOW_MIN_RR": "risk reward ke resistance terdekat belum layak",
        "NO_VALID_RESISTANCE_PATH": "belum ada jalur target yang memenuhi risk reward",
        "MINOR_RESISTANCE_NEAR": "resistance minor masih dekat; tunggu konfirmasi",
        "WAIT_FOR_ENTRY_TRIGGER": "menunggu harga masuk area entry atau trigger",
        "WAIT_FOR_ENTRY_ZONE": "harga masih di atas area entry; tunggu pullback",
        "ENTRY_READINESS_BELOW_MINIMUM": "kesiapan entry masih rendah",
        "PRICE_EXTENDED": "harga sudah terlalu jauh dari area ideal",
        "ENTRY_HARD_BLOCKER": "terdapat hard blocker pada setup",
        "BEARISH_MARKET_REGIME": "market regime bearish",
        "MISSING_PRICE_DATA": "data harga belum lengkap",
        "INVALID_ENTRY_ZONE": "area entry tidak valid",
        "LOW_LIQUIDITY": "likuiditas belum memenuhi guardrail",
        "LIQUIDITY_GATE": "likuiditas belum memenuhi guardrail",
    }
    if status == "REJECT":
        return "NOT READY", reason_map.get(reason_code.upper(), reason_code.replace("_", " ").lower() or "guardrail entry belum terpenuhi")
    if status == "CONDITIONAL":
        return "WAITING", reason_map.get(reason_code.upper(), reason_code.replace("_", " ").lower() or "menunggu trigger entry")
    if valid_plan(plan):
        return "READY", "rencana entry valid"
    return "WAITING", "menunggu validasi rencana entry"


def entry_setup_lines(plan: pd.Series) -> tuple[str, str, str]:
    if plan.empty:
        return "menunggu konfirmasi", "belum ditetapkan", "berdasarkan invalidation setup"
    status = normalize_text(row_value(plan, "Plan_Status"), "").upper()
    reason = normalize_text(row_value(plan, "Rejection_Reason"), "").upper()
    if status == "CONDITIONAL":
        if reason == "MINOR_RESISTANCE_NEAR":
            trigger = fmt_number(row_value(plan, "Minor_Resistance", "Nearest_Resistance"), 0)
            entry = f"tunggu close > {trigger}" if trigger != MISSING else "tunggu breakout valid"
        else:
            low = fmt_number(row_value(plan, "Entry_Zone_Low"), 0)
            high = fmt_number(row_value(plan, "Entry_Zone_High"), 0)
            entry = f"pantau area {low}–{high}" if MISSING not in {low, high} else "menunggu trigger entry"
        return entry, "ditetapkan setelah trigger", "ditetapkan setelah trigger"
    if not valid_plan(plan):
        return "menunggu konfirmasi", "belum ditetapkan", "berdasarkan invalidation setup"
    low = fmt_number(row_value(plan, "Entry_Zone_Low"), 0)
    high = fmt_number(row_value(plan, "Entry_Zone_High"), 0)
    tp1 = fmt_number(row_value(plan, "Target_1"), 0)
    tp2 = fmt_number(row_value(plan, "Target_2"), 0)
    stop = fmt_number(row_value(plan, "Initial_Stop"), 0)
    targets = tp1 if tp2 == MISSING else f"{tp1} / {tp2}"
    return f"{low}–{high}", targets, stop


def rank_watchlist(decisions: pd.DataFrame, entry_plans: pd.DataFrame) -> pd.DataFrame:
    work = unique_final_decisions(decisions)
    if work.empty:
        return work
    decision_col = find_col(work, "Decision_V3", "Decision")
    symbol_col = find_col(work, "Symbol", "EMITEN", "Ticker")
    score_col = find_col(work, "Final_Score_V3", "Final_Score")
    broker_col = find_col(work, "Broker_Confirmation")
    if decision_col:
        work = work[~work[decision_col].astype(str).str.upper().eq("AVOID")].copy()
        # Rank by the public status shown to the user. WATCH and SPECULATIVE are
        # both displayed as WATCH, so hidden internal status must not create an
        # apparently inconsistent ordering.
        public_priority = {"BUY CONFIRMED": 0, "BUY CANDIDATE": 1, "WATCH HIGH": 2, "WATCH": 3}
        if symbol_col:
            work["__effective_status"] = work.apply(
                lambda row: effective_public_status(row[decision_col], plan_for_symbol(entry_plans, str(row[symbol_col]))), axis=1
            )
        else:
            work["__effective_status"] = work[decision_col].apply(public_status)
        work["__decision_priority"] = work["__effective_status"].map(public_priority).fillna(9)
    else:
        work["__decision_priority"] = 9
    approved: dict[str, int] = {}
    if symbol_col:
        for symbol in work[symbol_col].astype(str):
            readiness, _ = plan_readiness(plan_for_symbol(entry_plans, symbol))
            approved[symbol.upper()] = {"READY": 0, "WAITING": 1, "NOT READY": 2}.get(readiness, 3)
        work["__plan_priority"] = work[symbol_col].astype(str).str.upper().map(approved).fillna(1)
    else:
        work["__plan_priority"] = 1
    if broker_col:
        def broker_priority(row: pd.Series) -> int:
            if broker_flow_divergence(row):
                return 2
            value = normalize_text(row.get(broker_col), "").upper()
            if "ACCUMULATION" in value:
                return 0
            if "DISTRIBUTION" in value:
                return 3
            return 1

        work["__broker_priority"] = work.apply(broker_priority, axis=1)
    else:
        work["__broker_priority"] = 1
    if score_col:
        work["__score"] = pd.to_numeric(work[score_col], errors="coerce").fillna(-1)
    else:
        work["__score"] = -1
    return work.sort_values(
        ["__plan_priority", "__decision_priority", "__broker_priority", "__score"],
        ascending=[True, True, True, False],
    ).reset_index(drop=True)


def instrument(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    for row in snapshot.get("instruments", []) if snapshot else []:
        if str(row.get("instrument", "")).lower() == key.lower():
            return row
    return {}


def _instrument_impact(snapshot: dict[str, Any], key: str) -> tuple[str, str]:
    row = instrument(snapshot, key)
    status = normalize_text(row.get("freshness_status"), "DATA_NOT_AVAILABLE").upper() if row else "DATA_NOT_AVAILABLE"
    change = to_float(row.get("change_pct")) if row else None
    if not row or status not in {"VALID", "DELAYED_ACCEPTED"} or to_float(row.get("close")) is None or change is None:
        return "⚪", "unavailable"

    normalized = key.lower()
    if normalized == "vix":
        impact = "support" if change < 0 else "pressure" if change > 0 else "neutral"
    elif normalized == "usd_idr":
        impact = "support" if change < 0 else "pressure" if change > 0 else "neutral"
    elif normalized == "dxy":
        impact = "neutral" if abs(change) < 0.10 else "support" if change < 0 else "pressure"
    elif normalized == "gold":
        impact = "neutral"
    else:
        impact = "support" if change > 0 else "pressure" if change < 0 else "neutral"
    icon = {"support": "🟢", "pressure": "🔴", "neutral": "⚪", "unavailable": "⚪"}[impact]
    return icon, impact


def instrument_line(snapshot: dict[str, Any], key: str, label: str) -> str:
    row = instrument(snapshot, key)
    status = normalize_text(row.get("freshness_status"), "DATA_NOT_AVAILABLE").upper() if row else "DATA_NOT_AVAILABLE"
    if not row or status not in {"VALID", "DELAYED_ACCEPTED"} or to_float(row.get("close")) is None:
        return f"⚪ {esc(label)}: data tidak tersedia"
    change = to_float(row.get("change_pct"))
    icon, _impact = _instrument_impact(snapshot, key)
    return f"{icon} {esc(label)}: {esc(fmt_number(row.get('close'), 2))} ({esc(fmt_pct(change))})"


def global_market_impact_summary(snapshot: dict[str, Any]) -> dict[str, int]:
    result = {"support": 0, "pressure": 0, "neutral": 0, "unavailable": 0}
    for key in ("sp500", "nasdaq", "vix", "nikkei225", "hang_seng", "usd_idr", "dxy", "gold", "wti_crude"):
        _icon, impact = _instrument_impact(snapshot, key)
        result[impact] += 1
    return result


def global_market_interpretation(snapshot: dict[str, Any]) -> str:
    sentiment = snapshot.get("global_sentiment", {}) if snapshot else {}
    state = normalize_text(sentiment.get("sentiment_state"), "INSUFFICIENT_DATA").upper()
    counts = global_market_impact_summary(snapshot)
    if state == "RISK_ON":
        opening = "Sentimen global masih cenderung mendukung risk-on"
    elif state == "RISK_OFF":
        opening = "Sentimen global cenderung risk-off dan menuntut sikap defensif"
    elif state == "NEUTRAL":
        opening = "Sentimen global masih netral dan belum memberi arah yang dominan"
    else:
        opening = "Data global belum cukup untuk memberi arah yang kuat"
    if counts["support"] > counts["pressure"]:
        balance = "dukungan positif lebih banyak, tetapi tetap perlu konfirmasi dari IHSG dan likuiditas domestik"
    elif counts["pressure"] > counts["support"]:
        balance = "tekanan eksternal lebih dominan sehingga seleksi saham perlu diperketat"
    else:
        balance = "faktor pendukung dan penekan relatif berimbang"
    return f"{opening}; {balance}."


def global_sentiment_block(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    sentiment = snapshot.get("global_sentiment", {}) if snapshot else {}
    state = normalize_text(sentiment.get("sentiment_state"), "INSUFFICIENT_DATA").upper()
    coverage = to_float(sentiment.get("coverage_ratio"))
    reason = normalize_text(sentiment.get("reason"), MISSING)
    coverage_text = f"{coverage * 100:.0f}%" if coverage is not None else MISSING
    return state, coverage_text, reason


def ihsg_metrics(ihsg: pd.DataFrame) -> dict[str, Any]:
    if ihsg.empty:
        return {"date": MISSING, "open": None, "close": None, "change_point": None, "change_pct": None, "intraday": None}
    date_col = find_col(ihsg, "Date")
    close_col = find_col(ihsg, "Close")
    open_col = find_col(ihsg, "Open")
    if date_col:
        work = ihsg.copy()
        work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
        work = work.sort_values(date_col).dropna(subset=[date_col])
    else:
        work = ihsg.copy()
    if work.empty:
        return {"date": MISSING, "open": None, "close": None, "change_point": None, "change_pct": None, "intraday": None}
    latest = work.iloc[-1]
    previous = work.iloc[-2] if len(work) > 1 else pd.Series(dtype=object)
    close = to_float(latest[close_col]) if close_col else None
    open_price = to_float(latest[open_col]) if open_col else None
    previous_close = to_float(previous[close_col]) if close_col and not previous.empty else None
    change_point = close - previous_close if close is not None and previous_close is not None else None
    change_pct = change_point / previous_close * 100 if change_point is not None and previous_close else None
    intraday = close - open_price if close is not None and open_price is not None else None
    return {
        "date": latest[date_col] if date_col else MISSING,
        "open": open_price,
        "close": close,
        "change_point": change_point,
        "change_pct": change_pct,
        "intraday": intraday,
    }


def format_market_outlook(
    trade_date: Any,
    market_status: dict[str, Any],
    global_snapshot: dict[str, Any],
    broker_flow: str,
    plan_summary: str,
    focus: Iterable[str],
    risks: Iterable[str],
    generated_at: Any = None,
    config: UiConfig | None = None,
) -> str:
    cfg = config or UiConfig()
    regime = normalize_text(market_status.get("market_regime"), "UNKNOWN").upper()
    global_state, coverage, _global_reason = global_sentiment_block(global_snapshot)
    if regime in {"STRONG BULLISH", "BULL", "BULLISH"} and global_state != "RISK_OFF":
        market_bias = "SELEKTIF AGRESIF"
    elif regime == "EARLY BULLISH" and global_state != "RISK_OFF":
        market_bias = "SELEKTIF PROAKTIF"
    elif regime in {"STRONG BEARISH", "BEAR", "BEARISH"} or global_state == "RISK_OFF":
        market_bias = "DEFENSIF"
    elif regime == "EARLY BEARISH":
        market_bias = "DEFENSIF SELEKTIF"
    else:
        market_bias = "SELEKTIF"

    trend = normalize_text(market_status.get("reason"), "Data IHSG belum tersedia.")
    confidence = to_float(market_status.get("confidence_pct"))
    confidence_text = f"{confidence:.0f}%" if confidence is not None else "belum tersedia"
    data_date = market_status.get("data_date")
    parsed_data_date = pd.to_datetime(data_date, errors="coerce")
    if pd.isna(parsed_data_date):
        data_date_text = "belum tersedia"
    else:
        dt = parsed_data_date.to_pydatetime()
        data_date_text = f"{dt.day} {INDONESIAN_MONTHS[dt.month]} {dt.year}"
    freshness_note = " — data terlambat" if data_date and bool(market_status.get("is_stale")) else ""
    counts = global_market_impact_summary(global_snapshot)

    lines = [
        "🌅 <b>SDE SWING — MARKET OUTLOOK</b>",
        f"📅 {esc(fmt_date(trade_date))}",
        f"🕒 Snapshot diperbarui: {esc(fmt_time(generated_at))}",
        cfg.separator,
        "",
        f"{regime_icon(regime)} <b>REGIME: {esc(regime)}</b>",
        f"📊 Bias Market : <b>{esc(market_bias)}</b>",
        f"🌍 Sentimen    : <b>{esc(global_state)}</b> — coverage {esc(coverage)}",
        f"🌊 Broker Flow : {esc(broker_flow)}",
        f"📈 IHSG Trend  : {esc(trend)}",
        f"📍 Validasi IHSG: data {esc(data_date_text)} | confidence {esc(confidence_text)}{esc(freshness_note)}",
        "",
        "🧭 <b>RENCANA HARI INI</b>",
        esc(plan_summary),
        "",
        "🎯 <b>FOKUS UTAMA</b>",
        *[f"• {esc(item)}" for item in focus if normalize_text(item, "")],
        "",
        "⚠️ <b>RISIKO UTAMA</b>",
        *[f"• {esc(item)}" for item in risks if normalize_text(item, "")],
        "",
        cfg.separator,
        "🌍 <b>GLOBAL MARKET</b>",
        "",
        instrument_line(global_snapshot, "sp500", "S&P 500"),
        instrument_line(global_snapshot, "nasdaq", "Nasdaq"),
        instrument_line(global_snapshot, "vix", "VIX"),
        instrument_line(global_snapshot, "nikkei225", "Nikkei 225"),
        instrument_line(global_snapshot, "hang_seng", "Hang Seng"),
        instrument_line(global_snapshot, "usd_idr", "USD/IDR"),
        instrument_line(global_snapshot, "dxy", "DXY"),
        instrument_line(global_snapshot, "gold", "Gold"),
        instrument_line(global_snapshot, "wti_crude", "WTI Oil"),
        "",
        "📝 <b>RINGKASAN GLOBAL</b>",
        f"{counts['support']} mendukung | {counts['pressure']} menekan | {counts['neutral']} netral | {counts['unavailable']} tidak tersedia",
        "",
        "🧭 <b>INTERPRETASI</b>",
        esc(global_market_interpretation(global_snapshot)),
        "",
        cfg.separator,
        "📌 <b>ARAHAN SDE</b>",
        "Prioritaskan saham dengan Technical Quality tinggi, Entry Readiness memadai, dan broker accumulation yang memiliki confidence kuat.",
        "",
        "Hindari mengejar harga. Kandidat yang masih berstatus BUY CANDIDATE tetap menunggu trigger atau masuk ke area entry yang ditentukan.",
        "",
        f"Global Market Snapshot: {esc(global_snapshot.get('snapshot_id', 'DATA_NOT_AVAILABLE') if global_snapshot else 'DATA_NOT_AVAILABLE')}",
        "",
        "⚠️ Global sentiment hanya digunakan sebagai konteks Market Outlook dan belum mengubah scoring saham secara langsung.",
    ]
    return "\n".join(lines)


def format_daily_signal_recap(
    trade_date: Any,
    decisions: pd.DataFrame,
    market_status: dict[str, Any],
    technical: pd.DataFrame,
    generated_at: Any = None,
    partial: bool = False,
    config: UiConfig | None = None,
    entry_plans: pd.DataFrame | None = None,
) -> str:
    cfg = config or UiConfig()
    counts = public_counts(decisions, entry_plans)
    total = sum(counts.values())
    positive = counts["BUY CONFIRMED"] + counts["BUY CANDIDATE"] + counts["WATCH HIGH"] + counts["WATCH"]
    positive_pct = positive / total * 100 if total else 0
    avoid_pct = counts["AVOID"] / total * 100 if total else 0
    regime = normalize_text(market_status.get("market_regime"), "UNKNOWN").upper()
    lines = [
        "📊 <b>SDE SWING — REKAP SINYAL HARIAN</b>",
        f"📅 {esc(fmt_date(trade_date))}",
        f"🕒 {esc(fmt_time(generated_at))}",
        cfg.separator,
        f"🟢 BUY / WATCH <b>{positive_pct:.0f}%</b>  VS  🔴 AVOID <b>{avoid_pct:.0f}%</b>",
        signal_bar(counts),
        "",
        "<pre>" + "\n".join([
            f"BUY CONFIRMED : {counts['BUY CONFIRMED']:>3} emiten",
            f"BUY CANDIDATE : {counts['BUY CANDIDATE']:>3} emiten",
            f"WATCH HIGH    : {counts['WATCH HIGH']:>3} emiten",
            f"WATCH         : {counts['WATCH']:>3} emiten",
            f"AVOID         : {counts['AVOID']:>3} emiten",
        ]) + "</pre>",
        cfg.separator,
        f"{regime_icon(regime)} <b>REGIME: {esc(regime)}</b>",
        f"📈 IHSG hari ini: {esc(normalize_text(market_status.get('reason'), MISSING))}",
        f"🌊 Market breadth: {esc(market_breadth(technical))}",
        f"💰 Broker flow: {esc(broker_flow_summary(decisions))}",
        "",
        "🧭 <b>ARAHAN SDE</b>",
        esc(market_direction_narrative(counts, regime)),
        "",
        "<b>Prioritaskan:</b>",
        "• BUY CONFIRMED dan WATCH HIGH terbaik",
        "• Harga masih dekat area entry",
        "• Broker flow mendukung",
        "• Risk reward masih layak",
        "",
        "<b>Hindari:</b>",
        "• Harga sudah terlalu jauh dari entry",
        "• Risk reward tidak layak",
        "• Broker flow distribusi",
        "• Setup sudah invalid",
    ]
    if partial:
        lines += ["", "⚠️ Rekap berdasarkan data parsial."]
    if cfg.show_disclaimer:
        lines += ["", "⚠️ Statistik sistem, bukan ajakan beli atau jual. Gunakan stop loss dan manajemen risiko."]
    return "\n".join(lines)



def format_post_market_summary(
    trade_date: Any,
    technical: pd.DataFrame,
    candidates: pd.DataFrame,
    market_status: dict[str, Any],
    ihsg: pd.DataFrame,
    generated_at: Any = None,
    config: UiConfig | None = None,
) -> str:
    cfg = config or UiConfig()
    ihsg_work = ihsg.copy()
    ihsg_date_col = find_col(ihsg_work, "Date")
    target_date = pd.to_datetime(trade_date, errors="coerce")
    if ihsg_date_col and not pd.isna(target_date):
        parsed_dates = pd.to_datetime(ihsg_work[ihsg_date_col], errors="coerce")
        eligible_dates = parsed_dates.le(target_date)
        if eligible_dates.any():
            ihsg_work = ihsg_work[eligible_dates].copy()
    metrics = ihsg_metrics(ihsg_work)
    regime = normalize_text(market_status.get("market_regime"), "UNKNOWN").upper()
    market_status_date = pd.to_datetime(market_status.get("date"), errors="coerce")
    work = candidates.copy()
    quality_col = find_col(work, "Technical_Quality_Score", "Technical_Score")
    readiness_col = find_col(work, "Entry_Readiness_PreScore")
    readiness_class_col = find_col(work, "Entry_Readiness_Class")
    warning_col = find_col(work, "Entry_Soft_Warning")
    status_col = find_col(work, "Candidate_Status")
    symbol_col = find_col(work, "Symbol", "EMITEN", "Ticker")
    setup_col = find_col(work, "Setup_Type", "Setup_Label")

    if status_col:
        eligible = work[work[status_col].astype(str).str.upper().eq("PASS")].copy()
    else:
        eligible = work.copy()
    if not pd.isna(target_date) and (pd.isna(market_status_date) or market_status_date.date() != target_date.date()):
        above_col = find_col(technical, "Above_SMA20")
        if above_col:
            breadth_values = pd.to_numeric(technical[above_col], errors="coerce").dropna()
            breadth_pct = float(breadth_values.mean() * 100) if not breadth_values.empty else 50.0
            regime = "BULLISH" if breadth_pct >= 60 else "BEARISH" if breadth_pct < 40 else "SIDEWAYS"
        else:
            regime = "UNKNOWN"

    if quality_col:
        eligible[quality_col] = pd.to_numeric(eligible[quality_col], errors="coerce")
    if readiness_col:
        eligible[readiness_col] = pd.to_numeric(eligible[readiness_col], errors="coerce")
    sort_cols = [col for col in (readiness_col, quality_col) if col]
    if sort_cols:
        eligible = eligible.sort_values(sort_cols, ascending=[False] * len(sort_cols), na_position="last")

    ready_count = 0
    developing_count = 0
    if readiness_class_col:
        states = eligible[readiness_class_col].astype(str).str.upper()
        ready_count = int(states.eq("READY_ZONE").sum())
        developing_count = int(states.eq("DEVELOPING").sum())
    elif readiness_col:
        values = pd.to_numeric(eligible[readiness_col], errors="coerce")
        ready_count = int(values.ge(75).sum())
        developing_count = int(values.between(60, 74.999, inclusive="both").sum())
    extended_count = 0
    if warning_col:
        extended_count = int(eligible[warning_col].astype(str).str.upper().eq("PRICE_EXTENDED").sum())

    top_lines: list[str] = []
    for index, (_, row) in enumerate(eligible.head(cfg.max_post_market_candidates).iterrows(), 1):
        symbol = normalize_text(row[symbol_col] if symbol_col else "?", "?").upper()
        quality = fmt_number(row[quality_col], 1) if quality_col else MISSING
        readiness = fmt_number(row[readiness_col], 1) if readiness_col else MISSING
        quality_label = f"{quality}%" if quality != MISSING else quality
        readiness_label = f"{readiness}%" if readiness != MISSING else readiness
        setup = normalize_text(row[setup_col] if setup_col else "DEVELOPING", "DEVELOPING").upper().replace("_", " ")
        entry_class = normalize_text(row[readiness_class_col] if readiness_class_col else "", "").upper().replace("_", " ")
        warning = normalize_text(row[warning_col] if warning_col else "", "").upper().replace("_", " ")
        state = warning or entry_class or "MENUNGGU BROKER"
        top_lines.extend([
            f"{index}. <b>{esc(symbol)}</b> — Quality {esc(quality_label)} | Entry {esc(readiness_label)}",
            f"   Setup: {esc(setup)} | {esc(state)}",
        ])
    if not top_lines:
        top_lines = ["⚪ Belum ada kandidat teknikal yang lolos filter."]

    change = metrics.get("change_pct")
    change_icon = "📈" if (change or 0) > 0 else "📉" if (change or 0) < 0 else "➖"
    lines = [
        "📊 <b>SDE SWING — POST MARKET</b>",
        f"📅 {esc(fmt_date(trade_date))}",
        f"🕒 {esc(fmt_time(generated_at))}",
        cfg.separator,
        "📈 <b>KONDISI PASAR</b>",
        f"{change_icon} IHSG : {esc(fmt_number(metrics.get('close'), 2))} ({esc(fmt_pct(change))})",
        f"{regime_icon(regime)} Regime: <b>{esc(regime)}</b>",
        f"🌐 Breadth: {esc(market_breadth(technical))}",
        "",
        "🔍 <b>HASIL SCAN TEKNIKAL</b>",
        "<pre>" + "\n".join([
            f"Saham diproses : {len(technical)}",
            f"Kandidat lolos : {len(eligible)}",
            f"Ready Zone     : {ready_count}",
            f"Developing     : {developing_count}",
            f"Extended       : {extended_count}",
        ]) + "</pre>",
        "⭐ <b>KANDIDAT TEKNIKAL UTAMA</b>",
        *top_lines,
        "",
        "🧭 <b>KESIMPULAN</b>",
        f"{ready_count} kandidat berada dekat zona entry. Keputusan final tetap menunggu Broker Summary valid.",
    ]
    if cfg.show_disclaimer:
        lines += ["", "⚠️ Post Market adalah screening teknikal awal, bukan sinyal entry final."]
    return "\n".join(lines)


def format_closing_bell(
    trade_date: Any,
    ihsg: pd.DataFrame,
    market_status: dict[str, Any],
    global_snapshot: dict[str, Any],
    decisions: pd.DataFrame,
    entry_plans: pd.DataFrame,
    generated_at: Any = None,
    config: UiConfig | None = None,
) -> str:
    cfg = config or UiConfig()
    metrics = ihsg_metrics(ihsg)
    counts = public_counts(decisions, entry_plans)
    regime = normalize_text(market_status.get("market_regime"), "UNKNOWN").upper()
    trend_icon = "📈" if (metrics.get("change_point") or 0) > 0 else "📉" if (metrics.get("change_point") or 0) < 0 else "➖"
    global_state, _, _ = global_sentiment_block(global_snapshot)
    usd = instrument(global_snapshot, "usd_idr")
    dxy = instrument(global_snapshot, "dxy")
    watch = rank_watchlist(decisions, entry_plans).head(cfg.max_watchlist_items)
    symbol_col = find_col(watch, "Symbol", "EMITEN", "Ticker")
    decision_col = find_col(watch, "Decision_V3", "Decision")
    score_col = find_col(watch, "Final_Score_V3", "Final_Score")
    top_lines: list[str] = []
    for index, (_, row) in enumerate(watch.iterrows(), 1):
        symbol = normalize_text(row[symbol_col] if symbol_col else "?", "?").upper()
        status = public_status(row[decision_col] if decision_col else "WATCH")
        score = fmt_number(row[score_col], 1) if score_col else MISSING
        top_lines.append(f"{index}. <b>{esc(symbol)}</b> — {status_icon(status)} {esc(status)} — {esc(score)}%")
    if not top_lines:
        top_lines = ["⚪ Belum ada kandidat yang memenuhi prioritas watchlist."]
    lines = [
        "🔔 <b>SDE SWING — CLOSING BELL</b>",
        "🏁 <b>LAPORAN PENUTUPAN MARKET</b>",
        f"📅 {esc(fmt_date(trade_date))}",
        cfg.separator,
        "📊 <b>HASIL PENUTUPAN IHSG</b>",
        "<pre>" + "\n".join([
            f"Open         : {fmt_number(metrics.get('open'), 2)}",
            f"Close        : {fmt_number(metrics.get('close'), 2)}",
            f"Daily Change : {fmt_number(metrics.get('change_point'), 2)} poin ({fmt_pct(metrics.get('change_pct'))})",
            f"Intraday     : {fmt_number(metrics.get('intraday'), 2)} poin",
        ]) + "</pre>",
        f"{trend_icon} <b>TREND: {esc(regime)}</b>",
        esc(normalize_text(market_status.get("reason"), MISSING)),
        "",
        cfg.separator,
        "🌍 <b>SENTIMEN AKHIR</b>",
        f"{regime_icon(global_state)} Global: <b>{esc(global_state)}</b>",
        f"💵 USD/IDR : {esc(fmt_number(usd.get('close'), 2))} ({esc(fmt_pct(usd.get('change_pct')))})" if usd else "⚪ USD/IDR: DATA_NOT_AVAILABLE",
        f"🟡 DXY     : {esc(fmt_number(dxy.get('close'), 2))} ({esc(fmt_pct(dxy.get('change_pct')))})" if dxy else "⚪ DXY: DATA_NOT_AVAILABLE",
        "",
        cfg.separator,
        "📋 <b>HASIL SDE SWING</b>",
        f"🟢 BUY CONFIRMED : {counts['BUY CONFIRMED']} emiten",
        f"🟠 BUY CANDIDATE : {counts['BUY CANDIDATE']} emiten",
        f"🟡 WATCH HIGH    : {counts['WATCH HIGH']} emiten",
        f"🔵 WATCH         : {counts['WATCH']} emiten",
        f"🔴 AVOID         : {counts['AVOID']} emiten",
        "",
        "🏆 <b>TOP WATCHLIST BESOK</b>",
        *top_lines,
        "",
        cfg.separator,
        "📝 <b>ULASAN SORE</b>",
        esc(market_direction_narrative(counts, regime)),
        "",
        "💡 <b>EVALUASI POSISI</b>",
        "• Pertahankan kandidat hanya selama setup belum invalid.",
        "• Hindari mengejar harga yang sudah jauh dari area entry.",
        "• Gunakan ukuran posisi terbatas saat regime belum kuat.",
        "• Tunggu broker summary valid sebelum final eksekusi besok.",
        "",
        "🤖 Automated post-market report by SDE Swing",
    ]
    return "\n".join(lines)


def format_watchlist(
    trade_date: Any,
    decisions: pd.DataFrame,
    entry_plans: pd.DataFrame,
    broker_raw: pd.DataFrame | None = None,
    generated_at: Any = None,
    config: UiConfig | None = None,
    preliminary: bool = False,
) -> str:
    cfg = config or UiConfig()
    if preliminary:
        ranked = rank_watchlist(decisions, entry_plans).head(cfg.max_watchlist_items).copy()
        ranked["__report_status"] = _effective_status_series(ranked, entry_plans) if not ranked.empty else []
    else:
        ranked = select_final_watchlist_rows(decisions, entry_plans, cfg)

    symbol_col = find_col(ranked, "Symbol", "EMITEN", "Ticker")
    decision_col = find_col(ranked, "Decision_V3", "Decision")
    score_col = find_col(ranked, "Final_Score_V3", "Final_Score", "Technical_Quality_Score", "Technical_Score")
    title = "📋 <b>SDE SWING — PRELIMINARY WATCHLIST</b>" if preliminary else "📋 <b>SDE SWING — FINAL WATCHLIST</b>"
    lines = [
        title,
        f"📅 {esc(fmt_date(trade_date))} | {esc(fmt_time(generated_at))}",
        cfg.separator,
    ]
    if preliminary:
        lines += ["⚠️ Belum broker-confirmed. Gunakan hanya untuk pemantauan teknikal.", ""]

    counts: dict[str, int] = {}
    if not ranked.empty and "__report_status" in ranked.columns:
        counts = ranked["__report_status"].value_counts().to_dict()
    no_buy_mode = (
        not preliminary
        and counts.get("BUY CONFIRMED", 0) == 0
        and counts.get("BUY CANDIDATE", 0) == 0
        and counts.get("WATCH HIGH", 0) > 0
    )

    if ranked.empty:
        lines += ["", "⚪ Belum ada BUY CONFIRMED, BUY CANDIDATE, atau WATCH HIGH yang memenuhi filter."]
    else:
        if no_buy_mode:
            lines += [
                "",
                "ℹ️ <b>BELUM ADA SINYAL BUY</b>",
                "Belum ada kandidat yang memenuhi kombinasi teknikal, broker confirmation, dan kesiapan entry.",
            ]

        actionable_counter = 0
        watch_counter = 0
        group_order = ("BUY CONFIRMED", "BUY CANDIDATE", "WATCH HIGH")
        group_titles = {
            "BUY CONFIRMED": "✅ <b>BUY CONFIRMED</b>",
            "BUY CANDIDATE": "🟠 <b>BUY CANDIDATE</b>",
            "WATCH HIGH": "🟡 <b>WATCH HIGH</b>",
        }
        for status in group_order:
            group = ranked[ranked["__report_status"] == status] if "__report_status" in ranked.columns else ranked.iloc[0:0]
            if group.empty:
                continue
            lines += ["", group_titles[status], ""]
            for _, row in group.iterrows():
                symbol = normalize_text(row[symbol_col] if symbol_col else "?", "?").upper()
                raw_status = row[decision_col] if decision_col else status
                plan = plan_for_symbol(entry_plans, symbol)
                effective_status = effective_public_status(raw_status, plan)
                if "__report_status" in row.index and normalize_text(row.get("__report_status"), ""):
                    effective_status = normalize_text(row.get("__report_status"), effective_status)
                score = fmt_number(row[score_col], 1) if score_col else MISSING
                entry, targets, stop = entry_setup_lines(plan)
                readiness, readiness_reason = plan_readiness(plan)

                if status in {"BUY CONFIRMED", "BUY CANDIDATE"}:
                    actionable_counter += 1
                    lines += [
                        f"{actionable_counter}. <b>{esc(symbol)}</b> — {esc(score)}%",
                        f"   {esc(compact_setup_label(row))}",
                        f"   🎯 {esc(compact_trigger_text(effective_status, entry))}",
                    ]
                    if status == "BUY CONFIRMED":
                        target_text = normalize_text(targets, "belum ditetapkan")
                        stop_text = normalize_text(stop, "belum ditetapkan")
                        if target_text != "belum ditetapkan":
                            target_text = " / ".join(
                                value if value.startswith("Rp") else f"Rp{value}"
                                for value in target_text.split(" / ")
                            )
                        if stop_text != "belum ditetapkan" and not stop_text.startswith("Rp"):
                            stop_text = f"Rp{stop_text}"
                        lines += [
                            f"   💰 TP {esc(target_text)}",
                            f"   🛡️ SL {esc(stop_text)}",
                        ]
                    lines += [
                        f"   🌊 {esc(compact_broker_state(row))}",
                        "",
                    ]
                elif no_buy_mode:
                    watch_counter += 1
                    trigger_label, trigger_text = compact_watch_trigger(entry)
                    blocker = compact_watch_blocker(row, readiness, readiness_reason)
                    watch_status = compact_watch_status(row, readiness)
                    lines += [
                        f"{watch_counter}. <b>{esc(symbol)}</b> — {esc(score)}%",
                        f"   Setup  : {esc(compact_setup_label(row))}",
                        f"   {esc(trigger_label):<7}: {esc(trigger_text)}",
                        f"   Broker : {esc(compact_broker_state(row))}",
                        f"   Kendala: {esc(blocker)}",
                        f"   Status : {esc(watch_status)}",
                        "",
                    ]
                else:
                    lines += [
                        f"• <b>{esc(symbol)}</b> — {esc(score)}% | {esc(compact_setup_label(row))}",
                        f"  {esc(compact_watch_issue(row, readiness_reason))}",
                        "",
                    ]
            while lines and lines[-1] == "":
                lines.pop()

    summary_parts = []
    if no_buy_mode:
        summary_parts = [
            "0 Buy Confirmed",
            "0 Buy Candidate",
            f"{counts.get('WATCH HIGH', 0)} Watch High",
        ]
    else:
        if counts.get("BUY CONFIRMED", 0):
            summary_parts.append(f"{counts['BUY CONFIRMED']} Buy Confirmed")
        if counts.get("BUY CANDIDATE", 0):
            summary_parts.append(f"{counts['BUY CANDIDATE']} Buy Candidate")
        if counts.get("WATCH HIGH", 0):
            summary_parts.append(f"{counts['WATCH HIGH']} Watch High")
    if summary_parts:
        lines += ["", cfg.separator, " | ".join(summary_parts)]
    if no_buy_mode:
        lines += [
            "",
            "🧭 <b>ARAHAN</b>",
            "Belum ada prioritas transaksi. Fokus memantau trigger, broker flow, dan perubahan status pada sesi berikutnya.",
            "",
        ]
    if cfg.show_disclaimer:
        lines += ["⚠️ Watchlist sistem, bukan rekomendasi transaksi."]
    return "\n".join(lines)


def format_signal_detail(
    trade_date: Any,
    row: pd.Series,
    entry_plan: pd.Series,
    broker_raw: pd.DataFrame | None = None,
    generated_at: Any = None,
    config: UiConfig | None = None,
) -> str:
    cfg = config or UiConfig()
    symbol = normalize_text(row_value(row, "Symbol", "EMITEN", "Ticker"), "?").upper()
    raw_status = row_value(row, "Decision_V3", "Decision", default="WATCH")
    status = effective_public_status(raw_status, entry_plan)
    score = fmt_number(row_value(row, "Final_Score_V3", "Final_Score"), 1)
    plan_status = normalize_text(row_value(entry_plan, "Plan_Status") if not entry_plan.empty else "", "")
    readiness, readiness_reason = plan_readiness(entry_plan)
    entry, targets, stop = entry_setup_lines(entry_plan)
    tp1, tp2 = (targets.split(" / ", 1) + ["belum ditetapkan"])[:2] if " / " in targets else (targets, "belum ditetapkan")
    risk_note = "Divergence broker terdeteksi; gunakan konfirmasi tambahan." if broker_flow_divergence(row) else "Disiplin pada area entry dan level invalidation."
    buy_lines = broker_party_detail_lines(row, broker_raw, "BUY", cfg.max_broker_detail_rows)
    sell_lines = broker_party_detail_lines(row, broker_raw, "SELL", cfg.max_broker_detail_rows)
    lines = [
        f"📊 <b>{esc(symbol)} | {status_icon(status)} {esc(status)} | {esc(score)}%</b>",
        f"📅 {esc(fmt_date(trade_date))} | 🕒 {esc(fmt_time(generated_at))}",
        cfg.separator,
        "📈 <b>TEKNIKAL</b>",
        "<pre>" + "\n".join([
            f"Setup      : {esc(setup_type_text(row))}",
            f"Trend      : {esc(trend_state(row))}",
            f"Quality    : {esc((lambda x: x + '%' if x != MISSING else x)(fmt_number(row_value(row, 'Technical_Quality_Score_Final', 'Technical_Quality_Score', 'Technical_Score_Final', 'Technical_Score'), 1)))}",
            f"Readiness  : {esc((lambda x: x + '%' if x != MISSING else x)(fmt_number(row_value(entry_plan, 'Entry_Readiness_Final', 'Entry_Readiness_PreScore') if not entry_plan.empty else row_value(row, 'Entry_Readiness_PreScore_Final', 'Entry_Readiness_PreScore'), 1)))}",
            f"Momentum   : {esc(momentum_state(row))}",
            f"Volume     : {esc(volume_state(row))}",
        ]) + "</pre>",
        "🌊 <b>BROKER SUMMARY</b>",
        "<pre>" + "\n".join(esc(line) for line in broker_overview_lines(row)) + "</pre>",
        "🟢 <b>TOP BUYER</b>",
        *[esc(line) for line in buy_lines],
        "",
        "🔴 <b>TOP SELLER</b>",
        *[esc(line) for line in sell_lines],
        "",
        "💰 <b>POSISI BROKER</b>",
        "<pre>" + "\n".join(esc(line) for line in broker_position_lines(row, broker_raw)) + "</pre>",
        "🎯 <b>RENCANA</b>",
        "<pre>" + "\n".join([
            f"Status: {esc(readiness)} — {esc(readiness_reason)}",
            f"Entry : {esc(entry)}",
            f"TP1   : {esc(tp1)}",
            f"TP2   : {esc(tp2)}",
            f"SL    : {esc(stop)}",
        ]) + "</pre>",
        "🧭 <b>EKSEKUSI</b>",
        esc(execution_guidance(raw_status, plan_status)),
        "",
        f"⚠️ {esc(risk_note)}",
    ]
    return "\n".join(lines)


def human_warning(quality: str, warnings: list[str], fallback: bool, broker_override: bool, data_source: str = "") -> str:
    state = quality.upper()
    if data_source.upper() == "OFFLINE_FIXTURE":
        return "Data sample atau fixture terdeteksi. Laporan ini tidak boleh dianggap sebagai hasil live dan tidak boleh menjadi dasar entry."
    if "STALE" in state or any("STALE" in item.upper() for item in warnings):
        return "Data yang digunakan bukan data terbaru sesuai tanggal evaluasi. Hasil hanya dapat digunakan sebagai referensi terbatas dan tidak memiliki validitas penuh."
    if broker_override:
        return "Tanggal broker tidak sama dengan tanggal teknikal, tetapi diproses melalui override. Konfirmasi broker harus dianggap terbatas."
    if fallback:
        return "Sistem menggunakan fallback karena sumber utama tidak tersedia. Validitas keputusan lebih rendah daripada kondisi data normal."
    return "Sebagian data tidak memenuhi guardrail kualitas. Periksa detail sebelum menggunakan hasil analisis."


def warning_impact(quality: str, fallback: bool, broker_override: bool) -> str:
    if quality.upper() == "VALID" and not fallback and not broker_override:
        return "Tidak ada dampak material terhadap keputusan."
    return "Jangan gunakan hasil ini sebagai dasar entry sampai kualitas data kembali valid. Kandidat tidak boleh dinaikkan menjadi BUY CONFIRMED tanpa override yang sah."


def format_data_warning(
    run_id: Any,
    expected_date: Any,
    latest_valid_date: Any,
    broker_date: Any,
    fallback_used: bool,
    broker_override: bool,
    data_status: Any,
    warnings: Iterable[Any] = (),
    data_source: str = "",
    config: UiConfig | None = None,
) -> str:
    cfg = config or UiConfig()
    warning_list = [normalize_text(item, "") for item in warnings if normalize_text(item, "")]
    quality = normalize_text(data_status, "UNKNOWN").upper()
    explanation = human_warning(quality, warning_list, fallback_used, broker_override, data_source)
    impact = warning_impact(quality, fallback_used, broker_override)
    lines = [
        "⚠️ <b>SDE SWING — DATA WARNING</b>",
        cfg.separator,
        "<pre>" + "\n".join([
            f"Run ID          : {esc(normalize_text(run_id))}",
            f"Expected Date   : {esc(normalize_text(expected_date))}",
            f"Latest Valid    : {esc(normalize_text(latest_valid_date))}",
            f"Broker Date     : {esc(normalize_text(broker_date))}",
            f"Fallback Used   : {'Ya' if fallback_used else 'Tidak'}",
            f"Broker Override : {'Ya' if broker_override else 'Tidak'}",
        ]) + "</pre>",
        f"⚠️ <b>STATUS: {esc(quality)}</b>",
        "",
        "📝 <b>KETERANGAN</b>",
        esc(explanation),
    ]
    if warning_list:
        lines += ["", *[f"• {esc(item)}" for item in warning_list[:5]]]
    lines += ["", "🧭 <b>DAMPAK</b>", esc(impact)]
    return "\n".join(lines)


def format_position_evaluation(
    trade_date: Any,
    active_positions: pd.DataFrame,
    exit_alerts: pd.DataFrame,
    decisions: pd.DataFrame,
    entry_plans: pd.DataFrame,
    config: UiConfig | None = None,
) -> str:
    cfg = config or UiConfig()
    ranked = rank_watchlist(decisions, entry_plans).head(3)
    symbol_col = find_col(ranked, "Symbol", "EMITEN", "Ticker")
    decision_col = find_col(ranked, "Decision_V3", "Decision")
    lines = [
        "🧭 <b>SDE SWING — EVALUASI POSISI / WATCHLIST BESOK</b>",
        f"📅 {esc(fmt_date(trade_date))}",
        cfg.separator,
        "💼 <b>POSISI AKTIF</b>",
    ]
    if active_positions.empty:
        lines += ["⚪ Data posisi aktif tidak tersedia. Sistem tidak mengarang posisi."]
    else:
        lines += [f"• Posisi aktif: {len(active_positions)}", f"• Exit alert: {len(exit_alerts)}"]
    lines += ["", "🏆 <b>FOKUS BESOK</b>"]
    if ranked.empty:
        lines += ["⚪ Belum ada kandidat prioritas."]
    else:
        for _, row in ranked.iterrows():
            symbol = normalize_text(row[symbol_col] if symbol_col else "?", "?").upper()
            status = public_status(row[decision_col] if decision_col else "WATCH")
            lines.append(f"• <b>{esc(symbol)}</b> — {status_icon(status)} {esc(status)}")
    lines += [
        "",
        "📝 <b>RENCANA</b>",
        "• Gunakan final watchlist terbaru.",
        "• Entry hanya pada area yang valid.",
        "• Jangan mengejar harga.",
        "• Evaluasi ulang jika broker flow berubah menjadi distribusi.",
    ]
    return "\n".join(lines)


def format_pipeline_status(
    run_manifest: dict[str, Any],
    decisions: pd.DataFrame,
    exit_alerts: pd.DataFrame,
    config: UiConfig | None = None,
    entry_plans: pd.DataFrame | None = None,
) -> str:
    cfg = config or UiConfig()
    status = normalize_text(run_manifest.get("Pipeline_Status"), "UNKNOWN").upper()
    icon = "✅" if status in {"SUCCESS", "COMPLETED", "OK"} else "⚠️"
    counts = public_counts(decisions, entry_plans)
    return "\n".join([
        f"{icon} <b>SDE SWING - PIPELINE SELESAI</b>",
        cfg.separator,
        "<pre>" + "\n".join([
            f"Run ID       : {esc(normalize_text(run_manifest.get('Run_ID')))}",
            f"Status       : {esc(status)}",
            f"Technical    : {esc(normalize_text(run_manifest.get('Technical_Date')))}",
            f"Broker Date  : {esc(normalize_text(run_manifest.get('Broker_Date')))}",
            f"Data Quality : {esc(normalize_text(run_manifest.get('Data_Quality_Status'), 'UNKNOWN'))}",
        ]) + "</pre>",
        "📊 <b>RINGKASAN</b>",
        f"🟢 BUY CONFIRMED : {counts['BUY CONFIRMED']}",
        f"🟠 BUY CANDIDATE : {counts['BUY CANDIDATE']}",
        f"🟡 WATCH HIGH    : {counts['WATCH HIGH']}",
        f"🔵 WATCH         : {counts['WATCH']}",
        f"🔴 AVOID         : {counts['AVOID']}",
        f"🚪 Exit Alert    : {0 if exit_alerts.empty else len(exit_alerts)}",
    ])


def format_exit_alert(row: pd.Series, generated_at: Any = None, config: UiConfig | None = None) -> str:
    cfg = config or UiConfig()
    symbol = normalize_text(row_value(row, "Symbol", "EMITEN"), "?").upper()
    return "\n".join([
        "🚪 <b>SDE SWING — EXIT ALERT</b>",
        cfg.separator,
        f"📌 <b>{esc(symbol)}</b>",
        f"🕒 {esc(fmt_time(generated_at))}",
        f"🔴 Status : {esc(normalize_text(row_value(row, 'Alert', 'Exit_Status'), 'EXIT'))}",
        f"📝 Alasan : {esc(normalize_text(row_value(row, 'Reason', 'Exit_Reason')))}",
        f"💰 Return : {esc(fmt_pct(row_value(row, 'Return_Pct')))}",
        "",
        "🧭 Evaluasi posisi dan ikuti level exit yang sudah ditetapkan. Jangan menunda exit hanya karena berharap harga berbalik.",
    ])
