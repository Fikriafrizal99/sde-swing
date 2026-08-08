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


def _source_block(data: dict[str, Any], *, detail: bool = False) -> str:
    rows: list[str] = []
    for label, value in (
        ("Yahoo Technical", data.get("yahoo_status") or data.get("historical_status")),
        ("ZAPI IDX", data.get("zapi_status") or data.get("reconciliation_status")),
        ("Stockbit Broker", data.get("broker_status") or data.get("stockbit_status")),
        ("Global Market", data.get("global_market_status")),
    ):
        if value:
            rows.append(f"• {label:<16}: {_upper(value)}")
    if data.get("zapi_coverage") not in (None, ""):
        value = float(data["zapi_coverage"])
        rows.append(f"• ZAPI Coverage   : {_pct(value * 100 if value <= 1 else value)}")
    if detail and data.get("zapi_freshness_days") not in (None, ""):
        rows.append(f"• ZAPI Freshness  : {data['zapi_freshness_days']} hari")
    if data.get("degraded_reason"):
        rows.append(f"• Catatan         : {data['degraded_reason']}")
    return "\n\n📡 SOURCE PROVENANCE\n" + "\n".join(rows) if rows else ""


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


def format_market_outlook(data: dict[str, Any]) -> str:
    sentiment = data.get("global_sentiment") if isinstance(data.get("global_sentiment"), dict) else {}
    instruments = [item for item in data.get("global_instruments", []) or [] if isinstance(item, dict)]
    regime = _upper(data.get("market_regime"))
    icon = "🟢" if "BULL" in regime else "🔴" if "BEAR" in regime else "🟡"
    coverage = data.get("global_coverage") if data.get("global_coverage") not in (None, "") else data.get("coverage")
    tone = str(sentiment.get("sentiment_state") or data.get("global_tone") or "").upper()
    created_at = data.get("snapshot_created_at") or data.get("created_at")

    lines = ["🌅 SDE SWING — MARKET OUTLOOK", f"📅 {_date(data.get('trade_date'), long=True)}"]
    if _time(created_at):
        lines.append(f"🕒 Snapshot diperbarui: {_time(created_at)} WIB")
    lines += [SEPARATOR, "", f"{icon} REGIME: {regime}", f"📊 Bias Market : {_upper(data.get('execution_mode'))}"]
    if tone:
        lines.append(f"🌍 Sentimen    : {tone} — coverage {_pct(coverage)}")
    if data.get("broker_flow_context"):
        lines.append(f"🌊 Broker Flow : {data['broker_flow_context']}")
    if _market_reason(data):
        lines.append(f"📈 IHSG Trend  : {_market_reason(data)}")
    validation = []
    if data.get("ihsg_data_date"):
        validation.append(f"data {_date(data['ihsg_data_date'])}")
    if data.get("confidence_pct") not in (None, ""):
        validation.append(f"confidence {_pct(data['confidence_pct'], 0)}")
    if validation:
        lines.append(f"📍 Validasi IHSG: {' | '.join(validation)}")

    lines += [
        "", "🧭 RENCANA BESOK",
        str(data.get("focus_tomorrow") or "Cari breakout valid atau pullback sehat dengan volume dan broker flow mendukung."),
        "", "🎯 FOKUS UTAMA",
        _items(data.get("focus_points") or [
            "Setup teknikal dengan trend dan momentum yang selaras.",
            "Harga masih dekat area entry atau membentuk pullback sehat.",
            "Broker flow menunjukkan akumulasi dengan risk/reward layak.",
        ]),
        "", "⚠️ RISIKO UTAMA",
        _items(data.get("risk_points") or [
            str(data.get("avoid_guidance") or "Jangan mengejar harga di luar area entry."),
            "Waspadai profit taking setelah kenaikan cepat.",
            "Validasi broker flow sebelum menaikkan ukuran posisi.",
        ]),
    ]

    if instruments:
        lines += ["", SEPARATOR, "🌍 GLOBAL MARKET", ""]
        lines += [_global_line(item) for item in instruments]
        counts = [
            len(sentiment.get("positive_instruments", []) or []),
            len(sentiment.get("negative_instruments", []) or []),
            len(sentiment.get("neutral_instruments", []) or []),
            len(sentiment.get("missing_instruments", []) or []),
        ]
        lines += [
            "", "📝 RINGKASAN GLOBAL",
            f"{counts[0]} mendukung | {counts[1]} menekan | {counts[2]} netral | {counts[3]} tidak tersedia",
            "", "🧭 INTERPRETASI",
            str(data.get("global_interpretation") or sentiment.get("reason") or "Sentimen global tetap memerlukan konfirmasi IHSG dan likuiditas domestik."),
        ]

    groups = [
        ("🔥 LEADING", data.get("leading", [])),
        ("🟢 ROTATING IN", data.get("rotating_in", [])),
        ("🟡 WEAKENING", data.get("weakening", [])),
        ("🔴 ROTATING OUT", data.get("rotating_out", [])),
    ]
    if any(values for _, values in groups):
        lines += ["", SEPARATOR, "🔄 ROTASI SEKTOR", ""]
        for title, values in groups:
            if values:
                lines += [title, _items(values), ""]

    lines += [
        SEPARATOR, "📌 ARAHAN SDE",
        "Prioritaskan Technical Quality tinggi, Entry Readiness memadai, sektor mendukung, dan broker accumulation dengan confidence kuat.",
        "", "BUY CANDIDATE tetap menunggu trigger atau harga masuk ke area entry.",
        "", "📡 SOURCE PROVENANCE",
        f"• Yahoo IHSG    : {_upper(data.get('provider') or 'VALID')}",
        f"• Global Market : {_upper(data.get('global_market_status') or 'VALID')} — coverage {_pct(coverage)}",
    ]
    if data.get("snapshot_id"):
        lines += ["", f"Global Market Snapshot: {data['snapshot_id']}"]
    lines += ["", "⚠️ Sentimen global digunakan sebagai konteks Market Outlook dan belum mengubah scoring saham secara langsung."]
    return "\n".join(lines).strip()


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


def format_post_market(data: dict[str, Any]) -> str:
    status = _upper(data.get("process_status"))
    icon = "✅" if status == "SUCCESS" else "🟡" if status in {"PARTIAL", "SUCCESS WITH WARNING"} else "🔴"
    requested = int(data.get("symbols_requested") or 0)
    loaded = int(data.get("symbols_loaded") or 0)
    valid = int(data.get("symbols_valid") or 0)
    skipped = int(data.get("symbols_skipped") or 0)
    not_loaded = int(data.get("symbols_not_loaded") if data.get("symbols_not_loaded") not in (None, "") else max(0, requested - loaded))
    invalid = int(data.get("symbols_invalid") if data.get("symbols_invalid") not in (None, "") else max(0, loaded - valid))
    impact = str(data.get("data_impact") or ("TIDAK MATERIAL" if float(data.get("coverage") or 0) >= 90 else "MATERIAL")).upper()
    finished_at = data.get("finished_at") or data.get("generated_at") or data.get("finished_time")

    lines = [
        "🌆 SDE SWING — POST MARKET",
        f"📅 {_date(data.get('trade_date'), long=True)}",
    ]
    if _time(finished_at):
        lines.append(f"🕒 Proses selesai: {_time(finished_at)} WIB")
    lines += [
        SEPARATOR,
        "",
        f"{icon} PROCESS STATUS",
        status,
        "",
        "📊 MARKET SUMMARY",
    ]
    market_rows = _section_values(data, [
        ("Regime", "market_regime"),
        ("Execution Mode", "execution_mode"),
        ("Global Tone", "global_tone"),
        ("IHSG Trend", "ihsg_trend"),
        ("Breadth", "breadth"),
    ])
    lines += market_rows or ["• Belum tersedia dari artifact engine"]

    sector_groups = [
        ("Leading", data.get("leading", [])),
        ("Rotating In", data.get("rotating_in", [])),
        ("Weakening", data.get("weakening", [])),
        ("Lagging", data.get("lagging", data.get("rotating_out", []))),
    ]
    lines += ["", "🔄 SECTOR BIAS"]
    has_sector = False
    for label, values in sector_groups:
        clean = [str(value).strip() for value in values or [] if str(value).strip()]
        if not clean:
            continue
        has_sector = True
        lines += [f"• {label}", f"  {', '.join(clean)}", ""]
    if lines[-1] == "":
        lines.pop()
    if not has_sector:
        lines.append("• Belum tersedia dari artifact engine")

    lines += [
        "",
        SEPARATOR,
        "📦 DATA QUALITY",
        "",
        f"• Universe        : {requested} saham",
        f"• Loaded          : {loaded} saham",
        f"• Not Loaded      : {not_loaded} saham",
        f"• Valid           : {valid} saham",
        f"• Invalid         : {invalid} saham",
        f"• Skipped         : {skipped} saham",
        f"• Usable Coverage : {_pct(data.get('coverage'))}",
        f"• Impact          : {impact}",
        "",
        "⚠️ DATA NOTE",
        str(data.get("data_note") or "Coverage masih digunakan sesuai guardrail engine."),
        "",
        SEPARATOR,
        "🔎 CANDIDATE FUNNEL",
        "",
    ]
    funnel = [
        ("Universe awal", data.get("funnel_universe", requested if requested else None)),
        ("Lolos likuiditas", data.get("funnel_liquidity")),
        ("Lolos Technical Quality", data.get("funnel_technical")),
        ("Setup valid", data.get("funnel_setup")),
        ("Entry Readiness layak", data.get("funnel_entry_ready")),
        ("Broker data tersedia", data.get("funnel_broker")),
        ("Siap Final Watchlist", data.get("funnel_final")),
    ]
    available_funnel = [(name, value) for name, value in funnel if value not in (None, "")]
    lines += [f"• {name:<24}: {value}" for name, value in available_funnel]
    if len(available_funnel) <= 1:
        lines.append("• Tahap rinci belum tersedia dari artifact engine")

    lines += ["", "🚧 FILTER DOMINAN"]
    dominant_filters = [item for item in data.get("dominant_filters", []) or [] if isinstance(item, Mapping)]
    if dominant_filters:
        for item in dominant_filters[:3]:
            label = item.get("label") or item.get("reason") or item.get("name")
            count = item.get("count")
            if label not in (None, "") and count not in (None, ""):
                lines.append(f"• {label}: {count}")
    else:
        lines.append("• Belum tersedia dari artifact engine")

    lines += ["", SEPARATOR, "📊 SCREENING RESULT", ""]
    screening = [
        ("BUY READY", data.get("buy_ready_count")),
        ("BUY CANDIDATE", data.get("buy_candidate_count")),
        ("WATCH", data.get("watch_count")),
        ("WAIT", data.get("wait_count")),
        ("AVOID", data.get("avoid_count")),
        ("Final-ready", data.get("final_ready_count")),
    ]
    available_screening = [(name, value) for name, value in screening if value not in (None, "")]
    lines += [f"• {name:<14}: {value}" for name, value in available_screening]
    if not available_screening:
        lines.append("• Belum tersedia dari artifact engine")

    lines += [
        "",
        "📌 INTERPRETASI",
        str(data.get("screening_interpretation") or "Hasil screening akan diteruskan ke Final Watchlist setelah seluruh artifact keputusan tersedia."),
        "",
        "Prioritas Final Watchlist:",
        "• Technical Quality kuat",
        "• Entry Readiness memadai",
        "• Harga belum terlalu jauh dari area entry",
        "• Broker flow mendukung",
        "• Risk/reward masih layak",
        "",
        SEPARATOR,
        "📈 PIPELINE STATUS",
        "",
        f"• Technical Snapshot : {data.get('technical_status') or 'NOT AVAILABLE'}",
        f"• Universe Selection : {data.get('universe_status') or 'NOT AVAILABLE'}",
        f"• Candidate Screening: {data.get('candidate_status') or 'NOT AVAILABLE'}",
        f"• Broker Dependency  : {data.get('broker_status') or 'NOT AVAILABLE'}",
        f"• Market Outlook     : {data.get('market_outlook_status') or ('READY' if data.get('market_regime') else 'NOT AVAILABLE')}",
        f"• Final Watchlist    : {data.get('final_watchlist_status') or ('READY TO RUN' if data.get('funnel_final') not in (None, '', 0, '0') else 'WAITING')}",
        "",
        SEPARATOR,
        "📡 SOURCE STATUS",
        "",
    ]
    source_lines = _source_status_lines(data)
    lines += source_lines or ["• Belum tersedia dari artifact engine"]

    lines += [
        "",
        SEPARATOR,
        "🎯 NEXT PROCESS",
        "",
        "Final Watchlist akan menentukan:",
        "",
        "• saham prioritas;",
        "• status keputusan dan eksekusi;",
        "• area entry;",
        "• trigger yang harus terpenuhi;",
        "• target dan stop loss;",
        "• alasan utama;",
        "• risiko dan invalidation.",
    ]
    if data.get("run_id"):
        lines += ["", f"Run ID: {data['run_id']}"]
    return "\n".join(lines).strip()


def format_broker_summary(data: dict[str, Any]) -> str:
    top_acc = data.get("top_accumulation", []) or []
    top_dist = data.get("top_distribution", []) or []
    acc = [f"{i}. {row.get('symbol')} — {_label(row.get('broker_state', row.get('state')))}" for i, row in enumerate(top_acc[:3], 1)]
    dist = [f"{i}. {row.get('symbol')} — {_label(row.get('broker_state', row.get('state')))}" for i, row in enumerate(top_dist[:3], 1)]
    return "\n".join([
        "🏦 SDE BROKER SUMMARY", SEPARATOR, f"📅 {_date(data.get('trade_date'))} | POST MARKET", "",
        f"{'✅' if _upper(data.get('process_status')) == 'SUCCESS' else ''} PROCESS STATUS", _upper(data.get("process_status")), "",
        "📊 BROKER FLOW", f"• Accumulation : {data.get('accumulation_count')} saham", f"• Neutral      : {data.get('neutral_count')} saham",
        f"• Distribution : {data.get('distribution_count')} saham", f"• No Data      : {data.get('no_data_count')} saham", "",
        "🔥 TOP ACCUMULATION", "\n".join(acc) if acc else "Tidak ada", "", "🔴 TOP DISTRIBUTION", "\n".join(dist) if dist else "Tidak ada", "",
        "📎 CSV ringkasan broker terlampir.", "", f"📡 {data.get('provider', '')} | {data.get('source_mode', '')} | Coverage {_pct(data.get('coverage'))}",
    ])


def format_broker_multiday(data: dict[str, Any]) -> str:
    def rows(items: list[dict[str, Any]]) -> str:
        values = [f"{i}. {row.get('symbol')} — {_label(row.get('state_1d'))} | {_label(row.get('state_3d'))} | {_label(row.get('state_5d'))}" for i, row in enumerate(items[:3], 1)]
        return "\n".join(values) if values else "Tidak ada"
    status = _upper(data.get("process_status"))
    return "\n".join([
        "📚 SDE BROKER MULTI-DAY", SEPARATOR, f"📅 {_date(data.get('trade_date'))} | POST MARKET", "", f"{'✅' if status == 'SUCCESS' else ''} {status}", "",
        "🔥 Akumulasi Konsisten", rows(data.get("top_accumulation", []) or []), "", "🔴 Distribusi Konsisten", rows(data.get("top_distribution", []) or []), "",
        "📎 CSV multi-day terlampir.", f"📡 {data.get('provider', '')} | {data.get('source_mode', '')} | Coverage {_pct(data.get('coverage'))}",
    ])


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


def format_final_watchlist_summary(data: dict[str, Any]) -> str:
    rows = [row for row in data.get("rows", []) or [] if isinstance(row, dict)]
    counts = data.get("decision_counts") if isinstance(data.get("decision_counts"), dict) else {}
    top = data.get("top_priority") or rows[:3]
    created_at = data.get("generated_at") or data.get("created_at")
    lines = [
        "🎯 SDE SWING — FINAL WATCHLIST",
        f"📅 {_date(data.get('trade_date'), long=True)}",
    ]
    if _time(created_at):
        lines.append(f"🕒 Dibuat: {_time(created_at)} WIB")
    lines += [
        SEPARATOR,
        "",
        "🟢 MARKET",
        f"{_upper(data.get('market_regime') or 'MARKET CONTEXT READY')} | {_upper(data.get('execution_mode') or 'SELECTIVE')}",
        "",
        "📊 HASIL FINAL",
        f"• BUY READY      : {counts.get('BUY_READY', 0)}",
        f"• BUY CANDIDATE  : {counts.get('BUY_CANDIDATE', 0)}",
        f"• WATCH          : {counts.get('WATCH', 0)}",
        f"• WAIT           : {counts.get('WAIT', 0)}",
        f"• AVOID          : {counts.get('AVOID', 0)}",
        f"• Total aktif    : {len(rows)}",
        "",
        "🎯 PRIORITAS EKSEKUSI",
    ]
    for index, row in enumerate(top[:3], 1):
        confidence = f" — {_number(row.get('confidence'), 1)}%" if row.get("confidence") not in (None, "") else ""
        lines.append(f"{index}. {str(row.get('symbol', '')).upper()} — {_ui_decision(row.get('decision'))}{confidence}")
    if not top:
        lines.append("Belum ada saham aktif dalam Final Watchlist.")
    lines += [
        "",
        "Prioritas ditentukan berdasarkan kesiapan eksekusi,",
        "bukan hanya final score.",
        "",
        "📎 CSV lengkap dilampirkan:",
        str(data.get("csv_filename") or "sde-final-watchlist.csv"),
    ]
    return "\n".join(lines).strip()


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
    if items:
        return items
    return _text_items(data.get("reason_items") or data.get("main_reason"))


def _risk_items(data: dict[str, Any]) -> list[str]:
    return _text_items(data.get("risk_items") or data.get("main_risk"))


def format_watchlist_detail(data: dict[str, Any]) -> str:
    raw_decision = _decision(data.get("decision"))
    decision = _ui_decision(raw_decision)
    confidence = f" | {_number(data.get('confidence'), 1)}%" if data.get("confidence") not in (None, "") else ""
    low, high = _price(data.get("entry_low")), _price(data.get("entry_high"))
    entry = f"{low}–{high}" if low and high else low or high or "PENDING_TRIGGER"
    created_at = data.get("generated_at") or data.get("created_at")
    rank = _rank_marker(data.get("rank"))

    lines = [
        SEPARATOR,
        f"{rank} {str(data.get('symbol', '')).upper()} | {_decision_icon(raw_decision)} {decision}{confidence}",
        f"📅 {_date(data.get('trade_date'), long=True)}" + (f" | 🕒 {_time(created_at)} WIB" if _time(created_at) else ""),
        "",
        "📈 TEKNIKAL",
    ]
    _detail_line(lines, "Setup", _upper(data.get("setup")))
    _detail_line(lines, "Trend", _upper(data.get("trend")))
    _detail_line(lines, "Quality", data.get("technical_quality", data.get("technical_score")), _pct)
    _detail_line(lines, "Readiness", data.get("entry_readiness"), _pct)

    momentum = str(data.get("momentum_status") or "").strip()
    rsi = _number(data.get("rsi"), 1) if data.get("rsi") not in (None, "") else ""
    if momentum or rsi:
        _detail_line(lines, "Momentum", f"{momentum or 'N/A'}" + (f" — RSI {rsi}" if rsi else ""))

    volume_description = str(data.get("volume_description") or "").strip()
    volume_ratio = _number(data.get("volume_ratio_ma20"), 2) if data.get("volume_ratio_ma20") not in (None, "") else ""
    if volume_description or volume_ratio:
        _detail_line(lines, "Volume", f"{volume_description or 'N/A'}" + (f" — {volume_ratio}x MA20" if volume_ratio else ""))

    lines += ["", "🌊 BROKER SUMMARY"]
    _detail_line(lines, "Status", _upper(data.get("broker_status") or data.get("broker_state")))
    _detail_line(lines, "Direction", _upper(data.get("broker_direction")))
    _detail_line(lines, "Confidence", data.get("broker_confidence", data.get("broker_score")), _pct)
    _detail_line(lines, "Net Flow", data.get("broker_net_flow"), _money)
    if data.get("broker_buy_ratio") not in (None, "") or data.get("broker_sell_ratio") not in (None, ""):
        _detail_line(
            lines,
            "Buy / Sell",
            f"{_pct(data.get('broker_buy_ratio'), ratio_aware=True)} / {_pct(data.get('broker_sell_ratio'), ratio_aware=True)}",
        )
    _detail_line(lines, "Flow Status", _upper(data.get("broker_alignment")))

    lines += ["", "🟢 TOP BUYER"]
    buyers = _as_list(data.get("top_buyers"))
    lines += [_participant_line(index, item) for index, item in enumerate(buyers[:3], 1)] if buyers else ["• Belum tersedia dari artifact engine"]

    lines += ["", "🔴 TOP SELLER"]
    sellers = _as_list(data.get("top_sellers"))
    lines += [_participant_line(index, item) for index, item in enumerate(sellers[:3], 1)] if sellers else ["• Belum tersedia dari artifact engine"]

    lines += ["", "💰 POSISI BROKER"]
    position_start = len(lines)
    _detail_line(lines, "Avg Buyer", data.get("avg_buyer_price"), lambda value: f"{_price(value)} — weighted")
    _detail_line(lines, "Avg Seller", data.get("avg_seller_price"), lambda value: f"{_price(value)} — weighted")
    _detail_line(lines, "Harga terakhir", data.get("last_price"), _price)
    _detail_line(lines, "Jarak buy avg", data.get("distance_to_buyer_avg_pct"), _pct)
    _detail_line(lines, "Raw coverage", data.get("broker_raw_coverage"), lambda value: _pct(value, ratio_aware=True))
    if len(lines) == position_start:
        lines.append("• Belum tersedia dari artifact engine")

    lines += [
        "",
        "🎯 RENCANA",
        f"Status : {_execution(data, raw_decision)}",
        f"Entry  : {entry}",
        f"Trigger: {data.get('trigger_description') or 'PENDING_TRIGGER'}",
        f"TP1    : {_price(data.get('target_1')) or 'PENDING_TRIGGER'}",
        f"TP2    : {_price(data.get('target_2')) or 'PENDING_TRIGGER'}",
        f"SL     : {_price(data.get('stop_loss')) or 'PENDING_TRIGGER'}",
        f"R:R    : {_rr(data.get('risk_reward'))}",
        "",
        "🔔 YANG DITUNGGU",
    ]
    waiting = [str(item).strip() for item in _as_list(data.get("waiting_triggers")) if str(item).strip()]
    lines += [f"• {item}" for item in waiting[:3]] if waiting else ["• Belum ada trigger rinci pada artifact engine"]

    lines += ["", "✅ ALASAN UTAMA"]
    reasons = _reason_items(data)
    lines += [f"• {item}" for item in reasons[:3]] if reasons else ["• Belum tersedia dari artifact engine"]

    lines += ["", "⚠️ RISIKO DAN INVALIDATION"]
    risks = _risk_items(data)
    lines += [f"• {item}" for item in risks[:2]] if risks else ["• Belum tersedia dari artifact engine"]
    if data.get("invalidation"):
        invalidation = str(data["invalidation"]).strip().rstrip(".")
        lines.append(f"• Setup batal jika {invalidation}.")
    lines.append("• Hindari entry jika harga membuka terlalu jauh di atas area entry.")

    lines += [
        "",
        "🧭 EKSEKUSI",
        str(data.get("execution_note") or (
            "Boleh dieksekusi sesuai trade plan setelah trigger valid."
            if raw_decision in {"BUY", "BUY READY", "BUY CONFIRMED"}
            else "Masukkan watchlist prioritas. Belum boleh entry sebelum area dan trigger terkonfirmasi."
        )),
    ]
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# v1.7 presentation contract
# ---------------------------------------------------------------------------
# The legacy builders above remain import-compatible for older integrations.
# These definitions are intentionally last so the runtime uses one stable
# human-facing contract without changing the engine-owned artifacts.

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


def format_broker_multiday(data: dict[str, Any]) -> str:
    def rows(items: Any) -> list[str]:
        result = []
        for index, row in enumerate(items or [], 1):
            if isinstance(row, Mapping):
                result.append(
                    f"{index}. {_safe(str(row.get('symbol') or '').upper(), 'emiten')} — "
                    f"1D {_human(row.get('state_1d'))} | 3D {_human(row.get('state_3d'))} | 5D {_human(row.get('state_5d'))}"
                )
        return result or ["• Data tidak tersedia"]

    return "\n".join([
        "📚 <b>SDE SWING — BROKER MULTI-DAY</b>",
        SEPARATOR,
        f"📅 {escape_html(_date(data.get('trade_date')) or FORMAT_MISSING)} | POST MARKET",
        f"<b>STATUS: {_status(data.get('process_status') or 'WAITING')}</b>",
        "",
        "<b>🔥 AKUMULASI KONSISTEN</b>",
        *rows(data.get("top_accumulation")),
        "",
        "<b>🔴 DISTRIBUSI KONSISTEN</b>",
        *rows(data.get("top_distribution")),
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
    lines += [
        "",
        SEPARATOR,
        "📌 5 kartu berikut adalah 5 saham terbaik berdasarkan status eksekusi dan Final Score.",
        "📎 CSV tetap memuat seluruh saham aktif.",
    ]
    return "\n".join(lines).strip()


def format_watchlist_detail(data: dict[str, Any]) -> str:
    raw_status = str(data.get("decision") or "WATCH").upper().replace("_", " ")
    status = shared_human_status(raw_status)
    rr_text, rr_valid = _detail_rr(data)
    if status == "BUY READY" and not rr_valid:
        status = "WAITING"
    confidence = shared_format_percent(data.get("confidence"), 1) if data.get("confidence") not in (None, "") else FORMAT_MISSING
    symbol = _safe(str(data.get("symbol") or "").upper(), "emiten")
    entry = _detail_entry(data)
    if entry == FORMAT_MISSING:
        entry = "menunggu konfirmasi"
    trigger = _human(data.get("trigger_description"), "menunggu trigger entry")
    exchange_status = str(data.get("exchange_status") or "NORMAL").upper()
    risk_flags = shared_clean_items(data.get("risk_flags"), 3, "")
    exchange_warning_lines = shared_exchange_warnings(
        exchange_status,
        data.get("risk_flags"),
        data.get("exchange_veto"),
    )
    waiting = shared_clean_items(data.get("waiting_triggers"), 3, "")
    reasons = shared_clean_items(
        [data.get("main_reason_technical"), data.get("main_reason_broker"), data.get("main_reason_entry"), data.get("main_reason")],
        3,
        "",
    )
    risks = shared_clean_items(data.get("risk_items") or data.get("main_risk"), 3, "")
    if exchange_status != "NORMAL":
        risks.insert(0, f"Status Bursa {exchange_status}")
    risks = risks[:3]
    momentum = shared_format_momentum(
        data.get("rsi"),
        data.get("macd_hist") if data.get("macd_hist") not in (None, "") else data.get("macd"),
        macd_signal=data.get("macd_signal"),
    )
    if momentum == FORMAT_MISSING and data.get("momentum_status"):
        momentum = shared_human_enum(data.get("momentum_status"))
    top_buyers = data.get("top_buyers") if isinstance(data.get("top_buyers"), (list, tuple)) else []
    top_sellers = data.get("top_sellers") if isinstance(data.get("top_sellers"), (list, tuple)) else []
    plan_status = "BUY READY" if status == "BUY READY" and rr_valid else status
    lines = [
        SEPARATOR,
        f"<b>#{escape_html(str(data.get('rank') or '—'))} {symbol} | {escape_html(status)} | {escape_html(confidence)}</b>",
        f"📅 {escape_html(_date(data.get('trade_date'), long=True) or FORMAT_MISSING)} | 🕒 {escape_html(_time(data.get('generated_at') or data.get('created_at')) or FORMAT_MISSING)} WIB",
        "",
        * _section("📈 TEKNIKAL",
            f"Setup: {_human(data.get('setup'), 'data tidak tersedia')}",
            f"Trend: {_human(data.get('trend'), 'data tidak tersedia')}",
            f"Quality: {escape_html(shared_format_percent(data.get('technical_quality', data.get('technical_score')), 1))}",
            f"Readiness: {escape_html(shared_format_percent(data.get('entry_readiness'), 1))}",
            f"Momentum: {escape_html(momentum)}",
            f"Volume: {_human(data.get('volume_description'), 'data tidak tersedia')}" + (f" — {escape_html(shared_format_number(data.get('volume_ratio_ma20'), 2))}x MA20" if data.get('volume_ratio_ma20') not in (None, "") else ""),
        ),
    ]
    if exchange_status != "NORMAL" or risk_flags or exchange_warning_lines:
        lines += [
            "",
            *_section(
                "🏛️ STATUS BURSA",
                f"Status: {escape_html(exchange_status)}",
                *(f"• {escape_html(item)}" for item in exchange_warning_lines),
                f"Risiko: {escape_html(', '.join(risk_flags))}" if risk_flags else "",
                f"Veto: {_human(data.get('exchange_veto'), '')}" if data.get('exchange_veto') else "",
            ),
        ]
    lines += [
        "",
        *_section("🌊 BROKER SUMMARY",
            f"Status: {_human(data.get('broker_status') or data.get('broker_state'), 'data tidak tersedia')}",
            f"Direction: {_human(data.get('broker_direction'), 'data tidak tersedia')}",
            f"Confidence: {escape_html(shared_format_percent(data.get('broker_confidence', data.get('broker_score')), 1))}",
            f"Net Flow: {escape_html(shared_format_money(data.get('broker_net_flow')))}",
            f"Buy / Sell: {escape_html(shared_format_percent(data.get('broker_buy_ratio'), 1, ratio_aware=True))} / {escape_html(shared_format_percent(data.get('broker_sell_ratio'), 1, ratio_aware=True))}",
            f"Flow: {_human(data.get('broker_alignment'), 'data tidak tersedia')}",
        ),
        "",
        *_section("🟢 TOP BUYER", *_participant_lines(top_buyers)),
        "",
        *_section("🔴 TOP SELLER", *_participant_lines(top_sellers)),
        "",
        *_section("💰 POSISI BROKER",
            f"Avg buyer: {escape_html(shared_format_price(data.get('avg_buyer_price')))}",
            f"Avg seller: {escape_html(shared_format_price(data.get('avg_seller_price')))}",
            f"Harga terakhir: {escape_html(shared_format_price(data.get('last_price')))}",
            f"Jarak buy avg: {escape_html(shared_format_percent(data.get('distance_to_buyer_avg_pct'), 1, signed=True))}",
            f"Raw coverage: {escape_html(shared_format_percent(data.get('broker_raw_coverage'), 1, ratio_aware=True))}",
        ),
        "",
        *_section("🎯 RENCANA",
            f"Status: {escape_html(plan_status)}",
            f"Entry: {escape_html(entry)}",
            f"Trigger: {escape_html(trigger)}",
            f"TP1: {escape_html(shared_format_price(data.get('target_1')))}",
            f"TP2: {escape_html(shared_format_price(data.get('target_2')))}",
            f"SL: {escape_html(shared_format_price(data.get('stop_loss')))}",
            f"R:R TP1: {escape_html(rr_text)}",
        ),
        "",
        *_section("🔔 YANG DITUNGGU", *(f"• {escape_html(item)}" for item in waiting) or ("• Menunggu trigger entry yang valid",)),
        "",
        *_section("✅ ALASAN UTAMA", *(f"• {escape_html(item)}" for item in reasons) or ("• Data alasan belum tersedia",)),
        "",
        *_section("⚠️ RISIKO & INVALIDASI", *(f"• {escape_html(item)}" for item in risks) or ("• Data risiko belum tersedia",)),
        "",
        *_section("🧭 EKSEKUSI", _safe(data.get("execution_note"), "Belum boleh entry sebelum area dan trigger terkonfirmasi.")),
    ]
    if any(data.get(key) not in (None, "") for key in ("yahoo_status", "zapi_status", "reconciliation_status", "broker_status")):
        lines += [
            "",
            "<b>📡 SOURCE STATUS</b>",
            f"Yahoo: {_source_status(data.get('yahoo_status') or 'VALID')}",
            f"ZAPI IDX: {_source_status(data.get('zapi_status') or data.get('reconciliation_status') or 'DEGRADED')}",
            f"Stockbit: {_source_status(data.get('broker_status') or 'WAITING')}",
        ]
    return "\n".join(lines).strip()

# FINAL_WATCHLIST_PRESENTATION_V2
# Presentation-only override. All values are read from engine/report artifacts.
import json as _fw_json
from html import escape as _fw_escape


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
    return _fw_escape(text.replace("_", " "))


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
    lines = []
    for index in range(3):
        item = items[index] if index < len(items) and isinstance(items[index], dict) else {}
        broker = _fw_text(item.get("broker"))
        avg = _fw_price(item.get("avg_price"))
        lines.append(f"{broker} @ {avg}")
    return lines


def format_watchlist_detail(row):
    """Canonical compact FINAL WATCHLIST card requested for Telegram."""
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
    net_flow = _fw_money(_fw_pick(row, "broker_net_flow", "net_flow", "cumulative_net_value"))
    buy_days = _fw_text(_fw_pick(row, "buy_days"))
    sell_days = _fw_text(_fw_pick(row, "sell_days"))
    buyer_concentration = _fw_pct(_fw_pick(row, "buyer_concentration"), concentration=True)
    seller_concentration = _fw_pct(_fw_pick(row, "seller_concentration"), concentration=True)
    top_buy = _fw_participants(_fw_pick(row, "top_buyers", default=[]))
    top_sell = _fw_participants(_fw_pick(row, "top_sellers", default=[]))
    broker_pattern = _fw_text(_fw_pick(row, "broker_pattern", "Broker_MultiDay_Context"))
    buy_cost = _fw_price(_fw_pick(row, "bandar_buy_cost", "avg_buyer_price", "weighted_broker_buy_cost"))
    vs_cost = _fw_pct(_fw_pick(row, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct"))
    flow = _fw_text(_fw_pick(row, "multi_day_flow", "Broker_MultiDay_Context"))
    persistence = _fw_text(_fw_pick(row, "flow_persistence", "Broker_Context_Alignment"))

    trend = _fw_text(_fw_pick(row, "trend", "Technical_Regime"))
    phase = _fw_text(_fw_pick(row, "phase", "execution_state", "Execution_Status"))
    support = _fw_price(_fw_pick(row, "support", "Support_Level"))
    resistance = _fw_price(_fw_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    fib_status = _fw_text(_fw_pick(row, "fib_status", "Fibonacci_Status", "Fib_Status"))
    reason = _fw_text(_fw_pick(row, "engine_final_reason", "main_reason", "Final_Reason"))

    lines = [
        "<b>📈 SDE SWING — FINAL WATCHLIST</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>{symbol} | {setup}</b>",
        f"🕒 {analysis_date}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>🎯 TRADE SETUP</b>",
        f"💰 Current {current} | Entry {entry_low}–{entry_high}",
        f"🛑 SL {stop} | 🎯 TP1 {tp1} | 🚀 TP2 {tp2}",
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
        f"📊 Pattern {broker_pattern}",
        f"💰 Buy Cost {buy_cost} | Vs Cost {vs_cost}",
        f"🌊 Flow {flow} | Persistence {persistence}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "<b>📌 SETUP CONTEXT</b>",
        f"📈 {trend} | {phase}",
        f"🟢 Support {support} | 🔴 Resistance {resistance}",
        f"📐 Fibonacci {fib_status}",
        "",
        "<b>Reason:</b>",
        reason,
    ]
    return "\n".join(lines).strip()
