from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Mapping


SEPARATOR = "━━━━━━━━━━━━━━━━━━━"


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "nan", "none", "null"}
    return True


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


def _upper(value: Any) -> str:
    if not _present(value):
        return ""
    return str(value).strip().replace("_", " ").upper()


def _int_text(value: Any) -> str:
    if not _present(value):
        return ""
    try:
        return f"{int(float(value)):,}".replace(",", ".")
    except Exception:
        return escape(str(value).strip())


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _smart_pct(value: Any, *, ratio_aware: bool = False) -> str:
    number = _number(value)
    if number is None:
        return ""
    if ratio_aware and 0 <= abs(number) <= 1:
        number *= 100.0
    decimals = 0 if abs(number - round(number)) < 1e-9 else 1
    return f"{number:.{decimals}f}%".replace(".", ",")


def _metric_line(icon: str, label: str, value: str) -> str:
    return f"{icon} {label:<16}: <b>{escape(value)}</b>"


def _status_icon(value: Any, *, neutral: str = "🟡") -> str:
    status = _upper(value)
    if not status:
        return neutral
    if any(token in status for token in ("FAILED", "INVALID", "ERROR", "BLOCKED")):
        return "🔴"
    if any(token in status for token in ("WARNING", "WAITING", "EMPTY", "PARTIAL", "UNAVAILABLE", "NOT READY")):
        return "🟡"
    if any(token in status for token in ("SUCCESS", "READY", "VALID", "ACTIVE")):
        return "🟢"
    return neutral


def _issue_counts(data: Mapping[str, Any]) -> tuple[int, list[str]]:
    total = 0
    notes: list[str] = []

    if _present(data.get("symbols_not_loaded")):
        value = int(float(data.get("symbols_not_loaded") or 0))
        if value > 0:
            total += value
            notes.append(f"{value} saham tidak berhasil dimuat")

    if _present(data.get("symbols_invalid")):
        value = int(float(data.get("symbols_invalid") or 0))
        if value > 0:
            total += value
            notes.append(f"{value} saham gagal validasi")

    if _present(data.get("symbols_skipped")):
        value = int(float(data.get("symbols_skipped") or 0))
        if value > 0:
            total += value
            notes.append(f"{value} saham dilewati")

    return total, notes


def _screening_rows(data: Mapping[str, Any]) -> list[str]:
    specs = (
        ("🟢", "BUY READY", "buy_ready_count"),
        ("🟠", "BUY CANDIDATE", "buy_candidate_count"),
        ("🔵", "WATCH", "watch_count"),
        ("🟡", "WAITING", "wait_count"),
        ("🔴", "AVOID", "avoid_count"),
    )
    rows: list[str] = []
    for icon, label, key in specs:
        if not _present(data.get(key)):
            continue
        value = _int_text(data.get(key))
        if value:
            rows.append(_metric_line(icon, label, value))
    return rows


def _source_rows(data: Mapping[str, Any]) -> list[str]:
    specs = (
        ("Yahoo Technical", data.get("historical_status") or data.get("yahoo_status")),
        ("ZAPI IDX", data.get("zapi_status") or data.get("reconciliation_status")),
        ("Stockbit Broker", data.get("stockbit_status")),
    )
    rows: list[str] = []
    for label, value in specs:
        status = _upper(value)
        if not status:
            continue
        rows.append(_metric_line(_status_icon(status), label, status))
    return rows


def _pipeline_rows(data: Mapping[str, Any]) -> list[str]:
    specs = (
        ("📈", "Technical Snapshot", data.get("technical_status")),
        ("🔍", "Candidate Screening", data.get("candidate_status")),
        ("🏦", "Broker Dependency", data.get("broker_status")),
        ("🎯", "Final Watchlist", data.get("final_watchlist_status")),
    )
    rows: list[str] = []
    for icon, label, value in specs:
        status = _upper(value)
        if status:
            rows.append(_metric_line(icon, label, status))
    return rows


def _post_status_rows(data: Mapping[str, Any]) -> list[str]:
    rows: list[str] = []
    technical = _upper(data.get("technical_status"))
    if technical:
        rows.append(_metric_line(_status_icon(technical), "Technical Data", technical))

    coverage = _smart_pct(data.get("coverage"), ratio_aware=True)
    if coverage:
        rows.append(_metric_line("🟢" if (_number(data.get("coverage")) or 0) >= 90 else "🟡", "Coverage", coverage))

    screening = _upper(data.get("candidate_status"))
    if screening:
        rows.append(_metric_line(_status_icon(screening), "Screening", screening))

    broker = _upper(data.get("broker_status") or data.get("stockbit_status"))
    if broker:
        rows.append(_metric_line(_status_icon(broker), "Broker", broker))

    final = _upper(data.get("final_watchlist_status"))
    if final:
        rows.append(_metric_line(_status_icon(final), "Final Watchlist", final))
    return rows


def format_post_market(data: dict[str, Any]) -> str:
    status = _upper(data.get("process_status"))
    lines = ["<b>🌆 SDE SWING — POST MARKET</b>"]

    trade_date = _date(data.get("trade_date"), long=True)
    if trade_date:
        lines.append(f"📅 {escape(trade_date)}")

    finished_time = _time(data.get("finished_at") or data.get("generated_at") or data.get("completed_at"))
    if finished_time:
        lines.append(f"🕒 Proses selesai: {escape(finished_time)} WIB")

    if status:
        lines += ["", SEPARATOR, "", "<b>✅ PROCESS STATUS</b>", ""]
        lines.append(_metric_line(_status_icon(status), "Status", status))

        issue_total, issue_notes = _issue_counts(data)
        if issue_total > 0:
            lines += ["", _metric_line("⚠️", "Warning", f"{issue_total} data bermasalah")]
            lines += ["", escape(" dan ".join(issue_notes) + ".")]

        impact = _upper(data.get("data_impact"))
        if impact:
            if issue_total > 0:
                lines.append(f"Dampaknya terhadap hasil keseluruhan <b>{escape(impact)}</b>.")
            else:
                lines += ["", f"Impact proses: <b>{escape(impact)}</b>."]

    quality_rows: list[str] = []
    if _present(data.get("symbols_requested")):
        quality_rows.append(_metric_line("🌐", "Universe", f"{_int_text(data.get('symbols_requested'))} saham"))
    if _present(data.get("symbols_loaded")):
        quality_rows.append(_metric_line("📥", "Loaded", f"{_int_text(data.get('symbols_loaded'))} saham"))
    if _present(data.get("symbols_valid")):
        quality_rows.append(_metric_line("✅", "Valid", f"{_int_text(data.get('symbols_valid'))} saham"))
    if _present(data.get("symbols_invalid")) and _present(data.get("symbols_loaded")) and _present(data.get("symbols_valid")):
        quality_rows.append(_metric_line("⚠️", "Invalid", f"{_int_text(data.get('symbols_invalid'))} saham"))
    if _present(data.get("symbols_not_loaded")) and _present(data.get("symbols_requested")) and _present(data.get("symbols_loaded")):
        quality_rows.append(_metric_line("❌", "Not Loaded", f"{_int_text(data.get('symbols_not_loaded'))} saham"))
    if _present(data.get("symbols_skipped")):
        skipped = _number(data.get("symbols_skipped"))
        if skipped is not None and skipped > 0:
            quality_rows.append(_metric_line("⏭️", "Skipped", f"{_int_text(data.get('symbols_skipped'))} saham"))
    coverage = _smart_pct(data.get("coverage"), ratio_aware=True)
    if coverage:
        quality_rows.append(_metric_line("📊", "Coverage", coverage))
    impact = _upper(data.get("data_impact"))
    if impact:
        quality_rows.append(_metric_line("🎯", "Impact", impact))

    if quality_rows:
        lines += ["", SEPARATOR, "", "<b>📦 DATA QUALITY</b>"]
        for row in quality_rows:
            lines += ["", row]
        if coverage and impact == "TIDAK MATERIAL":
            lines += ["", "Coverage tetap memadai sehingga data teknikal masih layak digunakan untuk proses berikutnya."]

    pipeline_rows = _pipeline_rows(data)
    if pipeline_rows:
        lines += ["", SEPARATOR, "", "<b>🔎 PIPELINE READINESS</b>"]
        for row in pipeline_rows:
            lines += ["", row]

    screening_rows = _screening_rows(data)
    lines += ["", SEPARATOR, "", "<b>📊 SCREENING RESULT</b>"]
    if screening_rows:
        for row in screening_rows:
            lines += ["", row]
    else:
        lines += ["", "Belum tersedia dari artifact keputusan.", "", "Klasifikasi final akan ditentukan oleh <b>Final Watchlist</b>."]

    source_rows = _source_rows(data)
    if source_rows:
        lines += ["", SEPARATOR, "", "<b>📡 SOURCE STATUS</b>"]
        for row in source_rows:
            lines += ["", row]
        source_note = data.get("degraded_reason") or data.get("zapi_note")
        if _present(source_note):
            lines += ["", escape(str(source_note).strip())]

    lines += [
        "",
        SEPARATOR,
        "",
        "<b>🎯 NEXT PROCESS</b>",
        "",
        "Final Watchlist akan menentukan:",
        "",
        "• saham prioritas;",
        "• status keputusan dan kesiapan eksekusi;",
        "• area entry dan trigger;",
        "• target dan stop loss;",
        "• broker confirmation;",
        "• alasan utama dan risiko.",
    ]

    status_rows = _post_status_rows(data)
    if status_rows:
        lines += ["", SEPARATOR, "", "<b>📌 POST MARKET STATUS</b>"]
        for row in status_rows:
            lines += ["", row]

        final_status = _upper(data.get("final_watchlist_status"))
        candidate_status = _upper(data.get("candidate_status"))
        broker_status = _upper(data.get("broker_status") or data.get("stockbit_status"))
        if final_status and "READY" in final_status and "NOT READY" not in final_status:
            lines += ["", "➡️ <b>Final Watchlist siap dilanjutkan.</b>"]
        elif any(token in f"{candidate_status} {broker_status} {final_status}" for token in ("WAITING", "EMPTY", "NOT READY")):
            lines += ["", "➡️ Sistem menunggu dependency yang dibutuhkan sebelum Final Watchlist."]

    if _present(data.get("run_id")):
        lines += ["", f"<b>Run ID:</b> <code>{escape(str(data['run_id']))}</code>"]

    return "\n".join(line for line in lines if line is not None).strip()
