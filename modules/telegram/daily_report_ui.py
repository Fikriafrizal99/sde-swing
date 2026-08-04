from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


def _label(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").title() if text else fallback


def _upper(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").upper() if text else fallback


def _pct(value: Any, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}%".replace(".", ",")
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


def format_post_market(data: dict[str, Any]) -> str:
    status = _upper(data.get("process_status"))
    icon = "✅" if status == "SUCCESS" else "🟡" if status in {"PARTIAL", "SUCCESS WITH WARNING"} else "🔴"
    requested = int(data.get("symbols_requested") or 0)
    loaded = int(data.get("symbols_loaded") or 0)
    valid = int(data.get("symbols_valid") or 0)
    skipped = int(data.get("symbols_skipped") or 0)
    not_loaded = max(0, requested - loaded)
    invalid = max(0, loaded - valid)
    impact = "Tidak material" if float(data.get("coverage") or 0) >= 90 else "Perlu ditinjau"

    lines = ["🌆 SDE SWING — POST MARKET", f"📅 {_date(data.get('trade_date'), long=True)}", SEPARATOR, "", f"{icon} PROCESS STATUS", status]
    if data.get("market_regime") or data.get("execution_mode"):
        lines += ["", "📊 MARKET SUMMARY"]
        for label, value in (
            ("Regime", data.get("market_regime")), ("Execution Mode", data.get("execution_mode")),
            ("Global Tone", data.get("global_tone")), ("IHSG Trend", data.get("ihsg_trend")),
            ("Breadth", data.get("breadth")),
        ):
            if value:
                lines.append(f"• {label:<14}: {_upper(value)}")

    groups = [("Leading", data.get("leading", [])), ("Rotating In", data.get("rotating_in", [])), ("Weakening", data.get("weakening", []))]
    if any(values for _, values in groups):
        lines += ["", "🔄 SECTOR BIAS"]
        for label, values in groups:
            if values:
                lines.append(f"• {label:<11}: {', '.join(str(value) for value in values)}")

    lines += [
        "", SEPARATOR, "📦 DATA QUALITY",
        f"• Universe        : {requested} saham",
        f"• Loaded          : {loaded} saham",
        f"• Not Loaded      : {not_loaded} saham",
        f"• Valid           : {valid} saham",
        f"• Invalid         : {invalid} saham",
        f"• Skipped         : {skipped} saham",
        f"• Usable Coverage : {_pct(data.get('coverage'))}",
        f"• Impact          : {impact}",
        "", "⚠️ DATA NOTE",
        str(data.get("data_note") or "Coverage masih digunakan sesuai guardrail engine."),
    ]

    funnel = [
        ("Universe awal", data.get("funnel_universe")), ("Lolos likuiditas", data.get("funnel_liquidity")),
        ("Lolos Technical Quality", data.get("funnel_technical")), ("Setup valid", data.get("funnel_setup")),
        ("Entry Readiness layak", data.get("funnel_entry_ready")), ("Broker data tersedia", data.get("funnel_broker")),
        ("Siap Final Watchlist", data.get("funnel_final")),
    ]
    funnel = [(name, value) for name, value in funnel if value not in (None, "")]
    if funnel:
        lines += ["", SEPARATOR, "🔎 CANDIDATE FUNNEL"] + [f"• {name:<24}: {value}" for name, value in funnel]

    screening = [
        ("BUY READY", data.get("buy_ready_count")), ("BUY CANDIDATE", data.get("buy_candidate_count")),
        ("WATCH", data.get("watch_count")), ("WAIT", data.get("wait_count")),
        ("AVOID", data.get("avoid_count")), ("Final-ready", data.get("final_ready_count")),
    ]
    screening = [(name, value) for name, value in screening if value not in (None, "")]
    if screening:
        lines += ["", SEPARATOR, "📊 SCREENING RESULT"] + [f"• {name:<14}: {value}" for name, value in screening]

    market_outlook = data.get("market_outlook_status") or ("READY" if data.get("market_regime") else "NOT ATTACHED")
    lines += [
        "", SEPARATOR, "📈 PIPELINE STATUS",
        f"• Technical Snapshot : {data.get('technical_status') or ''}",
        f"• Universe Selection : {data.get('universe_status') or ''}",
        f"• Candidate Screening: {data.get('candidate_status') or ''}",
        f"• Broker Dependency  : {data.get('broker_status') or ''}",
        f"• Market Outlook     : {market_outlook}",
        "", SEPARATOR, "📡 SOURCE STATUS",
        f"• Yahoo Technical : {_upper(data.get('historical_status') or 'VALID')} — coverage {_pct(data.get('coverage'))}",
        f"• ZAPI IDX        : {_upper(data.get('zapi_status'))} — coverage {_pct(data.get('zapi_coverage'))}",
        f"• Stockbit Broker : {_upper(data.get('stockbit_status') or data.get('broker_status'))}",
    ]
    if data.get("global_market_status"):
        lines.append(f"• Global Market   : {_upper(data.get('global_market_status'))}")
    lines += [
        "", SEPARATOR, "🎯 NEXT PROCESS",
        str(data.get("next_process") or "Final Watchlist menentukan saham prioritas, status eksekusi, area entry, trigger, target, stop loss, risiko, dan invalidation."),
    ]
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


def _decision_icon(value: str) -> str:
    return {"BUY": "🟢", "BUY READY": "🟢", "BUY CONFIRMED": "🟢", "BUY ON TRIGGER": "🟠", "BUY CANDIDATE": "🟠", "WATCH HIGH": "🟡", "WATCH": "🔵", "WAIT": "🟠", "AVOID": "🔴"}.get(value, "")


def format_final_watchlist_summary(data: dict[str, Any]) -> str:
    rows = [row for row in data.get("rows", []) or [] if isinstance(row, dict)]
    counts = data.get("decision_counts") if isinstance(data.get("decision_counts"), dict) else {}
    top = data.get("top_priority") or rows[:3]
    lines = [
        "🎯 SDE SWING — FINAL WATCHLIST", f"📅 {_date(data.get('trade_date'), long=True)}", SEPARATOR, "",
        "🟢 MARKET", f"{_upper(data.get('market_regime') or 'MARKET CONTEXT READY')} | {_upper(data.get('execution_mode') or 'SELECTIVE')}", "",
        "📊 HASIL FINAL", f"• BUY READY      : {counts.get('BUY_READY', 0)}", f"• BUY CANDIDATE  : {counts.get('BUY_CANDIDATE', 0)}",
        f"• WATCH          : {counts.get('WATCH', 0)}", f"• WAIT           : {counts.get('WAIT', 0)}", f"• AVOID          : {counts.get('AVOID', 0)}",
        f"• Total aktif    : {len(rows)}", "", "🎯 PRIORITAS EKSEKUSI",
    ]
    for index, row in enumerate(top[:3], 1):
        confidence = f" — {row.get('confidence')}%" if row.get("confidence") not in (None, "") else ""
        lines.append(f"{index}. {str(row.get('symbol', '')).upper()} — {_decision(row.get('decision'))}{confidence}")
    if not top:
        lines.append("Belum ada saham aktif dalam Final Watchlist.")
    lines += ["", "Prioritas ditentukan berdasarkan kesiapan eksekusi, bukan hanya final score.", "", f"📎 CSV lengkap dilampirkan: {data.get('csv_filename') or 'sde-final-watchlist.csv'}"]
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
        lines.append(f"{label:<12}: {rendered}")


def format_watchlist_detail(data: dict[str, Any]) -> str:
    decision = _decision(data.get("decision"))
    confidence = f" | {data.get('confidence')}%" if data.get("confidence") not in (None, "") else ""
    low, high = _price(data.get("entry_low")), _price(data.get("entry_high"))
    entry = f"{low}–{high}" if low and high else low or high or "Menunggu area valid"
    rr = data.get("risk_reward")
    rr_text = f"1:{str(rr).replace('.', ',')}" if rr not in (None, "") else "PENDING_TRIGGER"
    lines = [
        f"📊 {str(data.get('symbol', '')).upper()} | {_decision_icon(decision)} {decision}{confidence}",
        f"⏳ Execution: {_execution(data, decision)}", f"📅 {_date(data.get('trade_date'), long=True)}", SEPARATOR, "", "📈 TEKNIKAL",
    ]
    _detail_line(lines, "Setup", _label(data.get("setup")))
    _detail_line(lines, "Trend", _label(data.get("technical_state")))
    _detail_line(lines, "Quality", data.get("technical_score"), _pct)
    _detail_line(lines, "Readiness", data.get("entry_readiness"), _pct)
    _detail_line(lines, "Momentum", data.get("momentum_status"))
    _detail_line(lines, "RSI", data.get("rsi"))
    if data.get("volume_ratio_ma20") not in (None, ""):
        _detail_line(lines, "Volume", f"{data['volume_ratio_ma20']}x MA20")

    lines += ["", "🌊 BROKER SUMMARY"]
    _detail_line(lines, "Status", _label(data.get("broker_state")))
    _detail_line(lines, "Confidence", data.get("broker_score"), _pct)
    _detail_line(lines, "Net Flow", data.get("broker_net_flow"))
    if data.get("broker_buy_ratio") not in (None, "") or data.get("broker_sell_ratio") not in (None, ""):
        _detail_line(lines, "Buy / Sell", f"{_pct(data.get('broker_buy_ratio'))} / {_pct(data.get('broker_sell_ratio'))}")
    _detail_line(lines, "Flow Status", _label(data.get("broker_alignment")))

    if data.get("sector_state") or data.get("market_regime"):
        lines += ["", "🧭 MARKET CONTEXT"]
        _detail_line(lines, "Sector", _label(data.get("sector_state")))
        _detail_line(lines, "Market", _label(data.get("market_regime")))

    lines += ["", "📊 VALIDASI DATA"]
    _detail_line(lines, "Yahoo", _upper(data.get("yahoo_status")))
    _detail_line(lines, "ZAPI IDX", _upper(data.get("zapi_status")))
    if data.get("zapi_freshness_days") not in (None, ""):
        _detail_line(lines, "Freshness", f"{data['zapi_freshness_days']} hari")
    _detail_line(lines, "Data Status", _upper(data.get("data_status")))
    _detail_line(lines, "Conflict", _upper(data.get("data_conflict")))

    lines += [
        "", "🎯 RENCANA", f"Status      : {_execution(data, decision)}", f"Entry       : {entry}",
        f"Trigger     : {data.get('trigger_description') or data.get('execution_note') or 'Ikuti trigger engine dan jangan mengejar harga.'}",
        f"TP1         : {_price(data.get('target_1')) or 'PENDING_TRIGGER'}", f"TP2         : {_price(data.get('target_2')) or 'PENDING_TRIGGER'}",
        f"SL          : {_price(data.get('stop_loss')) or 'PENDING_TRIGGER'}", f"R:R         : {rr_text}", "",
        "✅ ALASAN UTAMA", str(data.get("main_reason") or ""), "", "⚠️ RISIKO DAN INVALIDATION",
        str(data.get("main_risk") or "Entry hanya dilakukan setelah trigger valid; hindari mengejar harga di luar zona entry."), "", "🧭 EKSEKUSI",
        str(data.get("execution_note") or ("Boleh dieksekusi sesuai trade plan setelah trigger valid." if decision in {"BUY", "BUY READY", "BUY CONFIRMED"} else "Masukkan watchlist prioritas. Belum boleh entry sebelum area dan trigger terkonfirmasi.")),
    ]
    return "\n".join(lines).strip() + _source_block(data, detail=True)
