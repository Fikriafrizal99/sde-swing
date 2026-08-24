from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

from modules.telegram.formatters import (
    MISSING as FORMAT_MISSING,
    clean_items as shared_clean_items,
    escape_html,
    exchange_warnings as shared_exchange_warnings,
    format_money as shared_format_money,
    format_momentum as shared_format_momentum,
    format_number as shared_format_number,
    format_percent as shared_format_percent,
    format_price as shared_format_price,
    human_enum as shared_human_enum,
    human_status as shared_human_status,
    risk_reward as shared_risk_reward,
)

SEPARATOR = "━━━━━━━━━━━━━━━━━━━"


def _label(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").title() if text else fallback


def _upper(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").upper() if text else fallback


def _pct(value: Any, decimals: int = 1, *, ratio_aware: bool = False) -> str:
    try:
        number = float(value)
        if ratio_aware and 0 <= abs(number) <= 1:
            number *= 100.0
        return f"{number:.{decimals}f}%".replace(".", ",")
    except Exception:
        return ""


def _signed_pct(value: Any, decimals: int = 2) -> str:
    try:
        return f"{float(value):+.{decimals}f}%".replace(".", ",")
    except Exception:
        return ""


def _price(value: Any) -> str:
    try:
        return f"{float(value):,.0f}".replace(",", ".")
    except Exception:
        return ""


def _number(value: Any, decimals: int = 2) -> str:
    try:
        text = f"{float(value):.{decimals}f}".rstrip("0").rstrip(".")
        return text.replace(".", ",")
    except Exception:
        return str(value or "").strip()


def _money(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", ""))
    except Exception:
        return str(value).strip()
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    if absolute >= 1_000_000_000_000:
        rendered = f"{absolute / 1_000_000_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}Rp{rendered.replace('.', ',')} triliun"
    if absolute >= 1_000_000_000:
        rendered = f"{absolute / 1_000_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}Rp{rendered.replace('.', ',')} miliar"
    if absolute >= 1_000_000:
        rendered = f"{absolute / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"{sign}Rp{rendered.replace('.', ',')} juta"
    return f"{sign}Rp{absolute:,.0f}".replace(",", ".")


def _rr(value: Any) -> str:
    if value in (None, ""):
        return "PENDING_TRIGGER"
    text = str(value).strip().replace(",", ".")
    if ":" in text:
        text = text.split(":")[-1].strip()
    try:
        number = float(text)
    except Exception:
        return str(value).replace(".", ",")
    rendered = f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"1:{rendered}"


def _dt(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _date(value: Any, *, long: bool = False) -> str:
    parsed = _dt(value)
    if parsed is None:
        return ""
    months = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    if not long:
        return f"{parsed.day:02d} {months[parsed.month]} {parsed.year}"
    days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    full_months = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{days[parsed.weekday()]}, {parsed.day} {full_months[parsed.month]} {parsed.year}"


def _time(value: Any) -> str:
    parsed = _dt(value)
    return parsed.strftime("%H:%M") if parsed else ""


def _items(values: Iterable[Any]) -> str:
    clean = [str(value).strip() for value in values if str(value).strip()]
    return "\n".join(f"• {value}" for value in clean) if clean else "• Tidak ada"


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item not in (None, "")]
    return [value]


def _market_reason(data: dict[str, Any]) -> str:
    if data.get("ihsg_reason") or data.get("reason"):
        return str(data.get("ihsg_reason") or data.get("reason"))
    parts = [
        f"trend {_label(data.get('ihsg_trend')).lower()}" if data.get("ihsg_trend") else "",
        f"momentum {_label(data.get('ihsg_momentum')).lower()}" if data.get("ihsg_momentum") else "",
        f"breadth {_label(data.get('breadth')).lower()}" if data.get("breadth") else "",
    ]
    return "; ".join(part for part in parts if part) + "."


def _global_line(row: dict[str, Any]) -> str:
    name = str(row.get("display_name") or row.get("instrument") or "").strip()
    status = str(row.get("freshness_status") or "").upper()
    try:
        change = float(row.get("change_pct") or 0)
    except Exception:
        change = 0.0
    effective = -change if row.get("inverse_sentiment") else change
    icon = "🟢" if effective > 0.15 else "🔴" if effective < -0.15 else "⚪"
    close = _price(row.get("close"))
    if status not in {"VALID", "DELAYED_ACCEPTED"} or not close:
        return f"⚪ {name}: tidak tersedia ({status or 'UNKNOWN'})"
    return f"{icon} {name}: {close} ({_signed_pct(change)})"


def _section_values(data: dict[str, Any], fields: list[tuple[str, str]]) -> list[str]:
    rows: list[str] = []
    for label, key in fields:
        value = data.get(key)
        if value not in (None, ""):
            rows.append(f"• {label:<14}: {_upper(value)}")
    return rows


def _source_status_lines(data: dict[str, Any]) -> list[str]:
    groups = [
        (
            "Yahoo Technical",
            data.get("historical_status") or data.get("yahoo_status"),
            data.get("yahoo_data_date") or data.get("technical_data_date"),
            data.get("coverage"),
            "",
        ),
        (
            "ZAPI IDX",
            data.get("zapi_status") or data.get("reconciliation_status"),
            data.get("zapi_data_date"),
            data.get("zapi_coverage"),
            data.get("zapi_note") or data.get("degraded_reason"),
        ),
        (
            "Stockbit Broker",
            data.get("stockbit_status") or data.get("broker_status"),
            data.get("stockbit_data_date"),
            data.get("stockbit_coverage"),
            "",
        ),
        (
            "Global Market",
            data.get("global_market_status"),
            data.get("global_data_date"),
            data.get("global_coverage"),
            "",
        ),
    ]
    lines: list[str] = []
    for label, status, data_date, coverage, note in groups:
        if all(value in (None, "") for value in (status, data_date, coverage, note)):
            continue
        if lines:
            lines.append("")
        lines.append(f"• {label}")
        if status not in (None, ""):
            lines.append(f"  Status    : {_upper(status)}")
        if data_date not in (None, ""):
            lines.append(f"  Data date : {_date(data_date)}")
        if coverage not in (None, ""):
            lines.append(f"  Coverage  : {_pct(coverage, ratio_aware=True)}")
        if note not in (None, ""):
            lines.append(f"  Note      : {note}")
    return lines


def _decision(value: Any) -> str:
    return str(value or "").upper().replace("_", " ").strip()


def _ui_decision(value: Any) -> str:
    decision = _decision(value)
    return "BUY CANDIDATE" if decision == "BUY ON TRIGGER" else decision


def _decision_icon(value: str) -> str:
    return {
        "BUY": "🟢",
        "BUY READY": "🟢",
        "BUY CONFIRMED": "🟢",
        "BUY ON TRIGGER": "🟠",
        "BUY CANDIDATE": "🟠",
        "WATCH HIGH": "🟡",
        "WATCH": "🔵",
        "WAIT": "🟠",
        "AVOID": "🔴",
    }.get(value, "")


def _rank_marker(value: Any) -> str:
    try:
        rank = int(value)
    except Exception:
        rank = 0
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}" if rank > 0 else "📌")


def _execution(data: dict[str, Any], decision: str) -> str:
    if data.get("execution_state"):
        return _upper(data.get("execution_state"))
    if decision in {"BUY", "BUY READY", "BUY CONFIRMED"}:
        return "READY — TUNGGU TRIGGER VALID"
    if decision in {"BUY CANDIDATE", "BUY ON TRIGGER"}:
        return "WAITING — TUNGGU AREA ENTRY / TRIGGER"
    return "MONITORING" if decision in {"WATCH", "WATCH HIGH"} else decision


def _detail_line(lines: list[str], label: str, value: Any, transform=None) -> None:
    if value in (None, ""):
        return
    rendered = transform(value) if transform else str(value)
    if rendered:
        lines.append(f"{label:<14}: {rendered}")


def _participant_line(index: int, item: Any) -> str:
    if isinstance(item, Mapping):
        broker = str(item.get("broker") or item.get("code") or item.get("name") or "").strip()
        value = _money(item.get("value") or item.get("net_value") or item.get("amount"))
        average = _price(item.get("avg_price") or item.get("average_price") or item.get("avg"))
        classification = str(item.get("classification") or item.get("origin") or item.get("foreign_local") or "").strip()
        parts = [broker or "UNKNOWN"]
        if value:
            parts.append(value)
        if average:
            parts.append(f"Avg {average}")
        if classification:
            parts.append(classification)
        head, *tail = parts
        return f"{index}. {head}" + (f" — {' | '.join(tail)}" if tail else "")
    return f"{index}. {str(item).strip()}"


def _text_items(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        item.strip(" •-\t")
        for item in str(value).replace("\r", "").split("\n")
        for item in item.split(";")
        if item.strip(" •-\t")
    ]


def _reason_items(data: dict[str, Any]) -> list[str]:
    structured = [
        data.get("main_reason_technical"),
        data.get("main_reason_broker"),
        data.get("main_reason_entry"),
    ]
    items = [str(item).strip() for item in structured if str(item or "").strip()]
    # If PRIMARY is multi-day, include today pulse interpretation and alignment
    primary = str((data.get("broker_period_type") or data.get("primary_window") or "")).upper()
    if primary and primary not in ("1D", "1DAY", "DAY"):
        today_pulse = data.get("today_pulse_interpretation") or data.get("today_pulse_reason")
        alignment = data.get("broker_alignment") or data.get("broker_flow_alignment") or data.get("alignment")
        if today_pulse and str(today_pulse).strip():
            items.append(str(today_pulse).strip())
        if alignment and str(alignment).strip():
            # Map or normalize known alignment labels to presentation-safe strings
            label = str(alignment).strip()
            items.append(str(label))
    if items:
        return items
    return _text_items(data.get("reason_items") or data.get("main_reason"))


def _risk_items(data: dict[str, Any]) -> list[str]:
    return _text_items(data.get("risk_items") or data.get("main_risk"))


def _safe(value: Any, fallback: str = FORMAT_MISSING) -> str:
    text = str(value or "").strip()
    return escape_html(text) if text else fallback


def _human(value: Any, fallback: str = FORMAT_MISSING) -> str:
    return escape_html(shared_human_enum(value, fallback))


def _status(value: Any) -> str:
    return escape_html(shared_human_status(value))


def _source_status(value: Any, fallback: str = "DATA TIDAK TERSEDIA") -> str:
    text = str(value or "").strip()
    return escape_html(text.replace("_", " ").upper() if text else fallback)


def _section(title: str, *lines: str) -> list[str]:
    return [f"<b>{escape_html(title)}</b>", *[line for line in lines if line != ""]]


def _detail_entry(data: Mapping[str, Any]) -> str:
    low = shared_format_price(data.get("entry_low"))
    high = shared_format_price(data.get("entry_high"))
    if low != FORMAT_MISSING and high != FORMAT_MISSING:
        return f"{low}–{high}"
    return low if low != FORMAT_MISSING else high


def _detail_rr(data: Mapping[str, Any]) -> tuple[str, bool]:
    return shared_risk_reward(
        data.get("entry_low"),
        data.get("entry_high"),
        data.get("target_1"),
        data.get("stop_loss"),
        entry_reference=data.get("entry_reference"),
    )


def _detail_participant(index: int, item: Any) -> str:
    if isinstance(item, Mapping):
        broker = str(item.get("broker") or item.get("code") or item.get("name") or "").strip().upper()
        value = shared_format_money(item.get("value") or item.get("net_value") or item.get("amount"))
        average = shared_format_price(item.get("avg_price") or item.get("average_price") or item.get("avg"))
        classification = shared_human_enum(item.get("classification") or item.get("origin") or "", "")
        parts = [escape_html(broker or "broker belum tersedia")]
        if value != FORMAT_MISSING:
            parts.append(escape_html(value))
        if average != FORMAT_MISSING:
            parts.append(f"Avg {escape_html(average)}")
        if classification:
            parts.append(escape_html(classification))
        return f"{index}. " + " | ".join(parts)
    return f"{index}. {escape_html(shared_human_enum(item))}"


def _participant_lines(items: Any) -> list[str]:
    values = list(items or []) if isinstance(items, (list, tuple)) else []
    return [_detail_participant(index, item) for index, item in enumerate(values[:3], 1)] or ["• Data tidak tersedia"]


def _runtime_zapi_lines(data: Mapping[str, Any]) -> list[str]:
    request_count = data.get("zapi_request_count")
    request_cap = data.get("zapi_request_cap", 5)
    if request_count in (None, "") and not data.get("zapi_status"):
        return []
    degraded = bool(data.get("zapi_degraded")) or str(data.get("zapi_status") or "").upper() in {"DEGRADED", "UNAVAILABLE"}
    lines = [
        "<b>🔌 ZAPI ENRICHMENT</b>",
        f"Request Zapi: {escape_html(shared_format_number(request_count, 0))}/{escape_html(shared_format_number(request_cap, 0))}",
        f"Metadata cache: {_safe(data.get('metadata_cache_status'))} | {escape_html(str(data.get('metadata_cache_date') or 'tanggal belum tersedia'))}",
        f"Market activity cache: {_safe(data.get('market_activity_cache_status'))}",
        f"Suspend: {escape_html(shared_format_number(data.get('suspended_count'), 0))} | UMA: {escape_html(shared_format_number(data.get('uma_count'), 0))} | Relisting: {escape_html(shared_format_number(data.get('relisting_count'), 0))}",
        f"Mode: <b>{'DEGRADED' if degraded else 'NORMAL'}</b>",
    ]
    reason = data.get("degraded_reason") or data.get("zapi_note")
    if reason:
        lines.append(f"Catatan: {_human(reason)}")
    return lines


def format_market_outlook(data: dict[str, Any]) -> str:
    sentiment = data.get("global_sentiment") if isinstance(data.get("global_sentiment"), Mapping) else {}
    regime = str(data.get("market_regime") or "UNKNOWN").replace("_", " ").upper()
    tone = str(sentiment.get("sentiment_state") or data.get("global_tone") or "DATA TIDAK TERSEDIA").replace("_", " ").upper()
    coverage = data.get("global_coverage", data.get("coverage"))
    focus = shared_clean_items(data.get("focus_points") or data.get("focus_tomorrow"), 3, "")
    risks = shared_clean_items(data.get("risk_points") or data.get("avoid_guidance"), 3, "")
    lines = [
        "<b>🌅 SDE SWING — MARKET OUTLOOK</b>",
        f"📅 {escape_html(_date(data.get('trade_date'), long=True) or FORMAT_MISSING)}",
        f"🕒 Snapshot diperbarui: {escape_html(_time(data.get('snapshot_created_at') or data.get('created_at')) or FORMAT_MISSING)} WIB",
        SEPARATOR,
        f"<b>📊 REGIME: {escape_html(regime)}</b>",
        f"Bias market: <b>{_human(data.get('execution_mode'), 'selektif')}</b>",
        f"Sentimen global: <b>{escape_html(tone)}</b> — coverage {escape_html(shared_format_percent(coverage, ratio_aware=True))}",
        f"Broker flow: {_human(data.get('broker_flow_context'), 'data tidak tersedia')}",
        f"IHSG: {_human(data.get('ihsg_reason') or data.get('reason'), 'data tidak tersedia')}",
        "",
        * _section("🧭 RENCANA BESOK", _safe(data.get("focus_tomorrow"), "Cari setup dengan entry dan broker flow yang mendukung.")),
        "",
        *_section("🎯 FOKUS UTAMA", *(f"• {escape_html(item)}" for item in focus)),
        "",
        *_section("⚠️ RISIKO UTAMA", *(f"• {escape_html(item)}" for item in risks)),
    ]
    instruments = [item for item in data.get("global_instruments", []) or [] if isinstance(item, Mapping)]
    if instruments:
        lines += ["", SEPARATOR, "<b>🌍 GLOBAL MARKET</b>"]
        for item in instruments[:9]:
            name = _safe(item.get("display_name") or item.get("instrument"), "instrumen")
            status = str(item.get("freshness_status") or "").upper()
            close = shared_format_price(item.get("close"))
            change = shared_format_percent(item.get("change_pct"), 2, signed=True)
            if status not in {"VALID", "DELAYED_ACCEPTED"} or close == FORMAT_MISSING:
                lines.append(f"• {name}: data tidak tersedia")
            else:
                lines.append(f"• {name}: {escape_html(close)} ({escape_html(change)})")
    if any(data.get(key) for key in ("leading", "rotating_in", "weakening", "rotating_out", "lagging")):
        lines += ["", SEPARATOR, "<b>🔄 ROTASI SEKTOR</b>"]
        for title, key in (("🔥 LEADING", "leading"), ("🟢 ROTATING IN", "rotating_in"), ("🟡 WEAKENING", "weakening"), ("🔴 ROTATING OUT", "rotating_out")):
            values = shared_clean_items(data.get(key), 3, "")
            if values:
                lines += [f"<b>{title}</b>", *[f"• {escape_html(item)}" for item in values]]
    lines += [
        "", SEPARATOR,
        "<b>📌 ARAHAN SDE</b>",
        "Prioritaskan Technical Quality, Entry Readiness, dan broker flow yang selaras.",
        "BUY CANDIDATE tetap menunggu trigger; jangan mengejar harga.",
        "",
        *_runtime_zapi_lines(data),
        "",
        "⚠️ Sentimen global digunakan sebagai konteks Market Outlook dan tidak mengubah scoring saham langsung.",
    ]
    return "\n".join(line for line in lines if line is not None).strip()


def format_post_market(data: dict[str, Any]) -> str:
    status = str(data.get("process_status") or "WAITING").replace("_", " ").upper()
    requested = int(float(data.get("symbols_requested") or 0))
    loaded = int(float(data.get("symbols_loaded") or 0))
    valid = int(float(data.get("symbols_valid") or 0))
    skipped = int(float(data.get("symbols_skipped") or 0))
    coverage = data.get("coverage")
    not_loaded = int(float(data.get("symbols_not_loaded") if data.get("symbols_not_loaded") not in (None, "") else max(0, requested - loaded)))
    invalid = int(float(data.get("symbols_invalid") if data.get("symbols_invalid") not in (None, "") else max(0, loaded - valid)))
    impact = str(data.get("data_impact") or ("TIDAK MATERIAL" if float(coverage or 0) >= 90 else "MATERIAL")).upper()
    lines = [
        "<b>🌆 SDE SWING — POST MARKET</b>",
        f"📅 {escape_html(_date(data.get('trade_date'), long=True) or FORMAT_MISSING)}",
        f"🕒 Proses selesai: {escape_html(_time(data.get('finished_at') or data.get('generated_at')) or FORMAT_MISSING)} WIB",
        SEPARATOR,
        f"<b>✅ PROCESS STATUS: {escape_html(status)}</b>",
        "",
        "<b>📊 MARKET SUMMARY</b>",
        f"Regime: {_human(data.get('market_regime'), 'data tidak tersedia')}",
        f"IHSG trend: {_human(data.get('ihsg_trend') or data.get('reason'), 'data tidak tersedia')}",
        f"Breadth: {_human(data.get('breadth'), 'data tidak tersedia')}",
        "",
        "<b>🔄 SECTOR BIAS</b>",
        f"Leading: {_human(', '.join(str(item) for item in data.get('leading', []) or []), 'data tidak tersedia')}",
        f"Rotating in: {_human(', '.join(str(item) for item in data.get('rotating_in', []) or []), 'data tidak tersedia')}",
        f"Weakening: {_human(', '.join(str(item) for item in data.get('weakening', []) or []), 'data tidak tersedia')}",
        f"Lagging: {_human(', '.join(str(item) for item in data.get('lagging', []) or []), 'data tidak tersedia')}",
        "",
        "<b>📦 DATA QUALITY</b>",
        f"• Universe awal           : {requested}",
        f"• Loaded                  : {loaded}",
        f"• Valid                   : {valid}",
        f"• Skipped                : {skipped}",
        f"• Universe: {requested} | Loaded: {loaded} | Valid: {valid} | Skipped: {skipped}",
        f"• Not Loaded      : {not_loaded} saham",
        f"• Invalid         : {invalid} saham",
        f"Coverage: {escape_html(shared_format_percent(coverage, ratio_aware=True))}",
        f"• Impact          : {escape_html(impact)}",
        f"Impact: {_human(data.get('data_impact'), 'data tidak tersedia')}",
        "",
        "<b>🔎 CANDIDATE FUNNEL</b>",
    ]
    funnel = [
        ("Universe awal", data.get("funnel_universe", requested)),
        ("Lolos likuiditas", data.get("funnel_liquidity")),
        ("Lolos teknikal", data.get("funnel_technical")),
        ("Setup valid", data.get("funnel_setup")),
        ("Entry Readiness", data.get("funnel_entry_ready")),
        ("Broker tersedia", data.get("funnel_broker")),
        ("Final Watchlist", data.get("funnel_final")),
    ]
    available_funnel = [(label, value) for label, value in funnel if value not in (None, "")]
    for label, value in available_funnel:
        if value not in (None, ""):
            lines.append(f"• {escape_html(label):<24}: {escape_html(shared_format_number(value, 0))}")
    if len(available_funnel) <= 1:
        lines.append("• Tahap rinci belum tersedia dari artifact engine")
    lines += [
        "",
        "<b>🚧 FILTER DOMINAN</b>",
        *(
            f"• {escape_html(str(item.get('label') or item.get('reason') or item.get('name')))}: {escape_html(shared_format_number(item.get('count'), 0))}"
            for item in (data.get('dominant_filters', []) or [])[:3]
            if isinstance(item, Mapping)
        ),
        "• Belum tersedia dari artifact engine" if not data.get("dominant_filters") else "",
        "",
        "<b>📊 SCREENING RESULT</b>",
        f"• BUY READY     : {escape_html(shared_format_number(data.get('buy_ready_count'), 0))}",
        f"• BUY CANDIDATE : {escape_html(shared_format_number(data.get('buy_candidate_count'), 0))}",
        f"• WATCH         : {escape_html(shared_format_number(data.get('watch_count'), 0))}",
        f"• WAITING       : {escape_html(shared_format_number(data.get('wait_count'), 0))}",
        f"• AVOID         : {escape_html(shared_format_number(data.get('avoid_count'), 0))}",
        "",
        "<b>📌 INTERPRETASI</b>",
        _safe(data.get('screening_interpretation'), "Hasil screening menunggu artifact keputusan lengkap."),
        "",
        "<b>📈 PIPELINE STATUS</b>",
        f"Technical Snapshot: {_status(data.get('technical_status') or 'NOT AVAILABLE')}",
        f"Candidate Screening: {_status(data.get('candidate_status') or 'NOT AVAILABLE')}",
        f"Final Watchlist: {_status(data.get('final_watchlist_status') or 'WAITING')}",
        "",
        "<b>📡 SOURCE STATUS</b>",
        f"Yahoo: {_source_status(data.get('historical_status') or data.get('yahoo_status') or 'VALID')}",
        f"ZAPI IDX: {_source_status(data.get('zapi_status') or 'DEGRADED')}",
        f"Stockbit: {_source_status(data.get('stockbit_status') or 'WAITING')}",
        "",
        "<b>🎯 NEXT PROCESS</b>",
        "Final Watchlist memeriksa status keputusan, entry, trigger, target, stop loss, alasan, dan risiko.",
    ]
    if data.get("run_id"):
        lines.append(f"Run ID: {escape_html(data['run_id'])}")
    lines += [
        "",
        *_runtime_zapi_lines(data),
    ]
    if data.get("data_note") or data.get("degraded_reason"):
        lines += ["", f"⚠️ Catatan: {_human(data.get('data_note') or data.get('degraded_reason'))}"]
    return "\n".join(lines).strip()


def format_broker_summary(data: dict[str, Any]) -> str:
    def top_lines(items: Any) -> list[str]:
        result = []
        for index, row in enumerate(items or [], 1):
            if not isinstance(row, Mapping):
                continue
            result.append(f"{index}. {_safe(str(row.get('symbol') or '').upper(), 'emiten')} — {_human(row.get('broker_state') or row.get('state'))}")
        return result or ["• Data tidak tersedia"]

    return "\n".join([
        "🏦 <b>SDE SWING — BROKER SUMMARY</b>",
        SEPARATOR,
        f"📅 {escape_html(_date(data.get('trade_date')) or FORMAT_MISSING)} | POST MARKET",
        f"<b>STATUS: {_status(data.get('process_status') or 'WAITING')}</b>",
        "",
        "<b>🌊 BROKER FLOW</b>",
        f"Akumulasi: {escape_html(shared_format_number(data.get('accumulation_count'), 0))}",
        f"Netral: {escape_html(shared_format_number(data.get('neutral_count'), 0))}",
        f"Distribusi: {escape_html(shared_format_number(data.get('distribution_count'), 0))}",
        f"Data tidak tersedia: {escape_html(shared_format_number(data.get('no_data_count'), 0))}",
        "",
        "<b>🔥 TOP ACCUMULATION</b>",
        *top_lines(data.get("top_accumulation")),
        "",
        "<b>🔴 TOP DISTRIBUTION</b>",
        *top_lines(data.get("top_distribution")),
        "",
        f"📡 Sumber: {_status(data.get('provider') or 'STOCKBIT')}",
    ]).strip()


def format_final_watchlist_summary(data: dict[str, Any]) -> str:
    rows = [row for row in data.get("rows", []) or [] if isinstance(row, Mapping)]
    counts = data.get("decision_counts") if isinstance(data.get("decision_counts"), Mapping) else {}
    top = data.get("top_priority") or rows[:3]
    lines = [
        "<b>🎯 SDE SWING — FINAL WATCHLIST</b>",
        f"📅 {escape_html(_date(data.get('trade_date'), long=True) or FORMAT_MISSING)}",
        f"🕒 Dibuat: {escape_html(_time(data.get('generated_at') or data.get('created_at')) or FORMAT_MISSING)} WIB",
        SEPARATOR,
        "<b>📊 HASIL FINAL</b>",
        f"• BUY READY      : {escape_html(shared_format_number(counts.get('BUY_READY', counts.get('BUY READY')), 0))}",
        f"• BUY CANDIDATE  : {escape_html(shared_format_number(counts.get('BUY_CANDIDATE', counts.get('BUY CANDIDATE')), 0))}",
        f"• WATCH          : {escape_html(shared_format_number(counts.get('WATCH'), 0))}",
        f"• WAITING        : {escape_html(shared_format_number(counts.get('WAIT'), 0))}",
        f"• AVOID          : {escape_html(shared_format_number(counts.get('AVOID'), 0))}",
        f"Total aktif: {len(rows)}",
        "",
        "<b>🏆 TOP 5 PRIORITAS</b>",
    ]
    for index, row in enumerate(top[:5], 1):
        confidence = shared_format_percent(row.get("confidence"), 1) if row.get("confidence") not in (None, "") else FORMAT_MISSING
        lines.append(f"{index}. {_safe(str(row.get('symbol') or '').upper(), 'emiten')} — {_status(row.get('decision'))} | {escape_html(confidence)}")
    if not top:
        lines.append("• Belum ada saham aktif dalam Final Watchlist.")
    # Show PRIMARY info when available and not 1D
    primary = str((data.get("broker_period_type") or data.get("primary_window") or "")).upper()
    if primary and primary not in ("1D", "1DAY", "DAY"):
        source = data.get("broker_period_source") or data.get("broker_period_provenance") or "UNKNOWN"
        lines += ["", f"• Broker PRIMARY : {escape_html(primary)} — {escape_html(str(source))}"]
        # compact today pulse counts if present
        tp_dir = data.get("today_pulse_direction") or data.get("today_pulse_state")
        tp_conf = data.get("today_pulse_confidence") or data.get("today_pulse_broker_confidence")
        if tp_dir or tp_conf:
            tp_line = "• TODAY PULSE (1D) : " + (str(tp_dir).upper() if tp_dir else "")
            if tp_conf not in (None, ""):
                try:
                    tp_line += f" | {_pct(float(tp_conf), 1)}"
                except Exception:
                    tp_line += f" | {str(tp_conf)}"
            lines.append(tp_line)
    lines += [
        "",
        SEPARATOR,
        "📌 5 kartu berikut adalah 5 saham terbaik berdasarkan status eksekusi dan Final Score.",
        "📎 CSV tetap memuat seluruh saham aktif.",
    ]
    return "\n".join(lines).strip()


def _fw_pick(row, *keys, default="ENGINE_DATA_NOT_AVAILABLE"):
    lookup = {str(k).strip().lower(): v for k, v in dict(row or {}).items()}
    for key in keys:
        value = lookup.get(str(key).strip().lower())
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null"}:
            return value
    return default


def _fw_text(value):
    if isinstance(value, (list, tuple, set)):
        value = "; ".join(str(item) for item in value if str(item).strip())
    elif isinstance(value, dict):
        value = "; ".join(f"{key}: {item}" for key, item in value.items())
    text = str(value if value is not None else "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return "ENGINE_DATA_NOT_AVAILABLE"
    return escape_html(text.replace("_", " "))


def _fw_num(value):
    try:
        text = str(value).strip()
        if ":" in text:
            text = text.rsplit(":", 1)[-1].strip()
        return float(text.replace(",", ""))
    except Exception:
        return None


def _fw_price(value):
    number = _fw_num(value)
    if number is None:
        return _fw_text(value)
    if abs(number - round(number)) < 1e-8:
        return f"{number:,.0f}".replace(",", ".")
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fw_score(value):
    number = _fw_num(value)
    return f"{number:.0f}" if number is not None else _fw_text(value)


def _fw_confidence(value):
    number = _fw_num(value)
    if number is None:
        return _fw_text(value)
    if 0 <= number <= 1:
        number *= 100
    return f"{number:.0f}%"


def _fw_pct(value, concentration=False):
    number = _fw_num(value)
    if number is None:
        text = str(value or "").strip()
        return _fw_text(text)
    if concentration and abs(number) <= 1:
        number *= 100
    return f"{number:+.2f}%" if not concentration else f"{number:.2f}%"


def _fw_money(value):
    number = _fw_num(value)
    if number is None:
        return _fw_text(value)
    sign = "+" if number > 0 else "-" if number < 0 else ""
    amount = abs(number)
    if amount >= 1_000_000_000:
        label = f"Rp{amount / 1_000_000_000:.2f} miliar"
    elif amount >= 1_000_000:
        label = f"Rp{amount / 1_000_000:.2f} juta"
    elif amount >= 1_000:
        label = f"Rp{amount / 1_000:.2f} ribu"
    else:
        label = f"Rp{amount:,.0f}"
    return sign + label.replace(".", ",")


def _fw_rr(value):
    number = _fw_num(value)
    return f"{number:.2f}" if number is not None else _fw_text(value)


def _fw_participants(value):
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                value = _fw_json.loads(raw)
            except Exception:
                value = []
        else:
            value = []
    items = list(value or []) if isinstance(value, (list, tuple)) else []
    lines: list[str] = []
    for index, item in enumerate(items[:3], start=1):
        if not isinstance(item, dict):
            continue
        broker = _fw_text(item.get("broker") or item.get("code") or item.get("name"))
        if broker == "ENGINE_DATA_NOT_AVAILABLE":
            continue
        details: list[str] = []
        value_label = _fw_money(item.get("value") or item.get("net_value") or item.get("amount"))
        if value_label != "ENGINE_DATA_NOT_AVAILABLE":
            details.append(value_label)
        average = _fw_price(item.get("avg_price") or item.get("average_price") or item.get("avg"))
        if average != "ENGINE_DATA_NOT_AVAILABLE":
            details.append(f"Avg Rp{average}")
        classification = _fw_text(
            item.get("classification") or item.get("broker_type") or item.get("type")
        )
        if classification != "ENGINE_DATA_NOT_AVAILABLE":
            details.append(classification.replace("_", " ").title())
        suffix = f" — {' | '.join(details)}" if details else ""
        lines.append(f"{index}. {broker}{suffix}")
    return lines or ["• Data broker PRIMARY belum tersedia"]


_FW_GENERIC_TRIGGER_CODES = {
    "ENTRY_NOT_TRIGGERED",
    "TRIGGER_NOT_MET",
    "WAIT_FOR_CONFIRMATION",
    "WAIT_FOR_ENTRY_TRIGGER",
    "WAIT_FOR_ENTRY_ZONE",
}


def _fw_trigger_text(value):
    candidate = str(value or "").strip().rstrip(" .")
    if not candidate:
        return ""
    normalized = candidate.replace("-", "_").replace(" ", "_").upper()
    if normalized in _FW_GENERIC_TRIGGER_CODES or normalized.startswith("ENGINE_DATA_"):
        return ""
    return candidate


def _fw_execution_guidance(row):
    """Keep the visible action tied to explicit engine facts or entry range."""
    direct = _fw_trigger_text(_fw_pick(
        row,
        "trigger_description",
        "Trigger_Description",
        "Entry_Trigger",
        "Execution_Trigger",
        default="",
    ))
    if direct:
        return f"⚠️ Trigger: {escape_html(direct)}. Jangan chase."

    pending = _fw_pick(row, "waiting_triggers", "Waiting_Triggers", default=[])
    if isinstance(pending, str) and pending.strip().startswith("["):
        try:
            pending = _fw_json.loads(pending)
        except Exception:
            pending = []
    if not isinstance(pending, (list, tuple)):
        pending = []
    for item in pending:
        if isinstance(item, dict):
            item = item.get("description") or item.get("trigger") or item.get("condition")
        trigger = _fw_trigger_text(item)
        if trigger:
            return f"⚠️ Trigger: {escape_html(trigger)}. Jangan chase."

    current = _fw_num(_fw_pick(row, "last_price", "current_price", "Reference_Close", default=""))
    low = _fw_num(_fw_pick(row, "entry_low", "Entry_Zone_Low", default=""))
    high = _fw_num(_fw_pick(row, "entry_high", "Entry_Zone_High", default=""))
    phase = str(_fw_pick(row, "phase", "execution_state", "Execution_Status", default="")).upper()
    waiting = any(token in phase for token in ("WAIT", "NOT READY", "CONDITIONAL", "MONITOR"))
    if current is not None and high is not None and current > high and low is not None:
        return f"⚠️ Jangan chase. Tunggu pullback ke {_fw_price(low)}–{_fw_price(high)}."
    if waiting and low is not None and high is not None:
        return f"⚠️ Tunggu trigger valid di area {_fw_price(low)}–{_fw_price(high)}. Jangan chase."
    if low is not None and high is not None:
        return f"⚠️ Entry hanya di area {_fw_price(low)}–{_fw_price(high)}. Jangan chase."
    return "⚠️ Tunggu setup tetap valid sebelum entry."


def format_watchlist_detail(row):
    """Render one Final Watchlist card from PRIMARY facts and optional TODAY pulse."""
    symbol = _fw_text(_fw_pick(row, "symbol", "Symbol"))
    setup = _fw_text(_fw_pick(row, "setup", "Setup_Type"))
    analysis_date = _fw_text(_fw_pick(row, "analysis_date", "trade_date", "Trade_Date"))
    current = _fw_price(_fw_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = _fw_price(_fw_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = _fw_price(_fw_pick(row, "entry_high", "Entry_Zone_High"))
    stop = _fw_price(_fw_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = _fw_price(_fw_pick(row, "target_1", "Target_1"))
    tp2 = _fw_price(_fw_pick(row, "target_2", "Target_2"))
    rr = _fw_rr(_fw_pick(row, "risk_reward", "Target_2_RR", "Target_1_RR"))
    technical_status = _fw_text(_fw_pick(row, "technical_status", "technical_state", "Plan_Status", "decision"))
    confidence = _fw_confidence(_fw_pick(row, "confidence", "Final_Score", "Final_Score_V3"))

    broker_signal = _fw_text(_fw_pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"))
    broker_score = _fw_score(_fw_pick(row, "broker_score", "Broker_Score"))
    net_flow = _fw_money(_fw_pick(row, "broker_net_flow", "net_flow"))
    buyer_concentration = _fw_pct(_fw_pick(row, "buyer_concentration"), concentration=True)
    seller_concentration = _fw_pct(_fw_pick(row, "seller_concentration"), concentration=True)
    top_buy = _fw_participants(_fw_pick(row, "top_buyers", default=[]))
    top_sell = _fw_participants(_fw_pick(row, "top_sellers", default=[]))
    buy_cost = _fw_price(_fw_pick(row, "bandar_buy_cost", "avg_buyer_price", "weighted_broker_buy_cost"))
    vs_cost = _fw_pct(_fw_pick(row, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct"))

    primary_period = _fw_text(_fw_pick(row, "broker_period_type", default=""))
    primary_source = _fw_text(_fw_pick(row, "broker_period_source", default=""))
    primary_line = " | ".join(part for part in (primary_period, primary_source) if part and part != "ENGINE_DATA_NOT_AVAILABLE")
    today_status = _fw_text(_fw_pick(row, "today_pulse_status", default=""))
    today_available = str(today_status).upper() == "AVAILABLE"
    today_flow = _fw_money(_fw_pick(row, "today_pulse_net_flow", default=""))
    today_state = _fw_text(_fw_pick(row, "today_pulse_direction", "today_pulse_broker_state", default=""))
    alignment = _fw_text(_fw_pick(row, "broker_alignment", default=""))

    trend = _fw_text(_fw_pick(row, "trend", "Technical_Regime"))
    phase = _fw_text(_fw_pick(row, "phase", "execution_state", "Execution_Status"))
    support = _fw_price(_fw_pick(row, "support", "Support_Level"))
    resistance = _fw_price(_fw_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    reason_raw = _fw_pick(row, "engine_final_reason", "main_reason", "Final_Reason")
    reason = (
        _fw_text(reason_raw)
        if not isinstance(reason_raw, (list, tuple, set, dict))
        else "ENGINE_DATA_NOT_AVAILABLE"
    )
    action = _fw_execution_guidance(row)

    lines = [
        "<b>📈 SDE SWING — FINAL WATCHLIST</b>",
        SEPARATOR,
        f"📌 <b>{symbol} | {setup}</b>",
        f"🕒 {analysis_date}",
        SEPARATOR,
        "",
        "<b>🎯 TRADE SETUP</b>",
        f"💰 Current {current} | Entry {entry_low}–{entry_high}",
        f"🛑 SL {stop} | 🎯 TP1 {tp1} | 🚀 TP2 {tp2}",
        f"⚖️ RR 1:{rr}",
        f"📊 {technical_status} | 🧠 Confidence {confidence}",
        "",
        "<b>🏦 BROKER PRIMARY</b>",
        f"📌 {broker_signal} | Score {broker_score}/100",
        f"💵 Net Flow {net_flow}",
        f"🎯 Concentration B {buyer_concentration} | S {seller_concentration}",
        f"💰 Buy Cost {buy_cost} | Jarak Buy Avg {vs_cost}",
    ]
    if primary_line:
        lines.append(f"🗂 PRIMARY {primary_line}")
    lines.extend(["", "<b>🟢 Top Buy</b>", *top_buy, "", "<b>🔴 Top Sell</b>", *top_sell])
    if today_available:
        lines.extend([
            "",
            "<b>📍 TODAY PULSE (Exact 1D)</b>",
            f"💵 Net Flow {today_flow} | {today_state}",
        ])
        if alignment and alignment != "ENGINE_DATA_NOT_AVAILABLE":
            lines.append(f"↔️ Alignment PRIMARY vs TODAY: {alignment}")
    elif primary_period.upper() in {"1D", "1DAY", "DAY"}:
        lines.append("📍 TODAY pulse tidak dipisahkan: sama dengan PRIMARY 1D.")

    lines.extend([
        "",
        "<b>📌 SETUP CONTEXT</b>",
        f"📈 {trend} | {phase}",
        f"🟢 Support {support} | 🔴 Resistance {resistance}",
        "",
        "<b>Action:</b>",
        action,
        "",
        "<b>Reason:</b>",
        reason,
    ])
    return "\n".join(lines).strip()


__all__ = ["format_watchlist_detail"]
