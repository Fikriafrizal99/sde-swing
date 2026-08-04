from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


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
