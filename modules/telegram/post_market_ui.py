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


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _int_text(value: Any) -> str:
    if not _present(value):
        return ""
    try:
        return f"{int(float(value)):,}".replace(",", ".")
    except Exception:
        return escape(str(value).strip())


def _smart_pct(value: Any, *, ratio_aware: bool = False, signed: bool = False) -> str:
    number = _number(value)
    if number is None:
        return ""
    if ratio_aware and 0 <= abs(number) <= 1:
        number *= 100.0
    decimals = 0 if abs(number - round(number)) < 1e-9 else 2 if signed else 1
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:.{decimals}f}%".replace(".", ",")


def _metric_line(icon: str, label: str, value: str) -> str:
    return f"{icon} {label:<14}: <b>{escape(value)}</b>"


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


def _market_icon(*values: Any) -> str:
    text = " ".join(_upper(value) for value in values if _present(value))
    if any(token in text for token in ("STRONG BULL", "BULLISH", "RISK ON", "RISK-ON", "POSITIVE")):
        return "🟢"
    if any(token in text for token in ("STRONG BEAR", "BEARISH", "RISK OFF", "RISK-OFF", "NEGATIVE")):
        return "🔴"
    return "🟡"


def _issue_counts(data: Mapping[str, Any]) -> tuple[int, list[str]]:
    total = 0
    notes: list[str] = []
    specs = (
        ("symbols_not_loaded", "tidak dimuat"),
        ("symbols_invalid", "gagal validasi"),
        ("symbols_skipped", "dilewati"),
    )
    for key, label in specs:
        if not _present(data.get(key)):
            continue
        try:
            value = int(float(data.get(key) or 0))
        except Exception:
            continue
        if value <= 0:
            continue
        total += value
        notes.append(f"{value} saham {label}")
    return total, notes


def _first(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if _present(value):
            return value
    return ""


def _breadth_counts(data: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    aliases = (
        ("technical_bullish_count", "bullish_count", "bullish_symbols"),
        ("technical_neutral_count", "neutral_count", "neutral_symbols"),
        ("technical_bearish_count", "bearish_count", "bearish_symbols"),
    )
    result: list[int | None] = []
    for group in aliases:
        value = _first(data, *group)
        if not _present(value):
            result.append(None)
            continue
        try:
            result.append(int(float(value)))
        except Exception:
            result.append(None)
    return result[0], result[1], result[2]


def _technical_breadth_rows(data: Mapping[str, Any]) -> list[str]:
    bullish, neutral, bearish = _breadth_counts(data)
    rows: list[str] = []
    if bullish is not None or neutral is not None or bearish is not None:
        b = bullish or 0
        n = neutral or 0
        r = bearish or 0
        total = b + n + r
        if total > 0:
            rows.append(
                f"🟢 Bullish <b>{100 * b / total:.0f}%</b>  |  "
                f"🟡 Neutral <b>{100 * n / total:.0f}%</b>  |  "
                f"🔴 Bearish <b>{100 * r / total:.0f}%</b>"
            )
            rows.append(f"{b} bullish | {n} netral | {r} bearish")

    if _present(data.get("symbols_valid")):
        rows.append(_metric_line("✅", "Valid", f"{_int_text(data.get('symbols_valid'))} saham"))
    if _present(data.get("funnel_technical")):
        rows.append(_metric_line("📈", "Lolos teknikal", f"{_int_text(data.get('funnel_technical'))} saham"))
    if _present(data.get("funnel_setup")):
        rows.append(_metric_line("🎯", "Setup valid", f"{_int_text(data.get('funnel_setup'))} saham"))
    if _present(data.get("funnel_entry_ready")):
        rows.append(_metric_line("⚡", "Entry ready", f"{_int_text(data.get('funnel_entry_ready'))} saham"))
    return rows


def _setup_distribution(data: Mapping[str, Any]) -> list[tuple[str, int]]:
    raw = _first(data, "setup_distribution", "setup_counts", "setup_breakdown")
    items: list[tuple[str, int]] = []
    if isinstance(raw, Mapping):
        for label, value in raw.items():
            try:
                count = int(float(value))
            except Exception:
                continue
            if count > 0:
                items.append((str(label).replace("_", " ").upper(), count))
    if items:
        return sorted(items, key=lambda item: item[1], reverse=True)[:5]

    fields = (
        ("BREAKOUT", ("breakout_count", "setup_breakout_count")),
        ("PULLBACK", ("pullback_count", "setup_pullback_count")),
        ("TREND CONTINUATION", ("trend_continuation_count", "setup_trend_continuation_count")),
        ("DEVELOPING", ("developing_count", "setup_developing_count")),
        ("OVEREXTENDED", ("overextended_count", "setup_overextended_count")),
    )
    for label, keys in fields:
        value = _first(data, *keys)
        if not _present(value):
            continue
        try:
            count = int(float(value))
        except Exception:
            continue
        if count > 0:
            items.append((label, count))
    return items


def _sector_line(data: Mapping[str, Any]) -> str:
    leading = data.get("leading") if isinstance(data.get("leading"), (list, tuple)) else []
    rotating = data.get("rotating_in") if isinstance(data.get("rotating_in"), (list, tuple)) else []
    values = [str(item).strip() for item in [*leading, *rotating] if str(item).strip()]
    values = list(dict.fromkeys(values))[:4]
    return ", ".join(values)


def _guidance(data: Mapping[str, Any]) -> str:
    regime = _upper(data.get("market_regime"))
    breadth = _upper(data.get("breadth"))
    change = _number(data.get("ihsg_change"))
    context = f"{regime} {breadth}"

    positive = (
        any(token in context for token in ("BULL", "RISK ON", "RISK-ON", "POSITIVE"))
        or (change is not None and change >= 0.5)
    )
    negative = (
        any(token in context for token in ("BEAR", "RISK OFF", "RISK-OFF", "NEGATIVE"))
        or (change is not None and change <= -0.5)
    )

    if positive and not negative:
        return (
            "Pasar ditutup dengan bias positif. Fokus pada saham dengan technical score tinggi, "
            "setup matang, dan broker confirmation yang mendukung. Hindari mengejar harga yang "
            "sudah terlalu jauh dari area entry."
        )
    if negative and not positive:
        return (
            "Pasar ditutup defensif. Utamakan proteksi modal dan hanya pertahankan kandidat dengan "
            "setup sangat kuat serta risk/reward layak. Hindari entry agresif sebelum tekanan pasar mereda."
        )
    return (
        "Pasar masih selektif/campuran. Fokus pada kandidat dengan confluence teknikal paling kuat, "
        "entry yang masih efisien, dan broker confirmation yang mendukung."
    )


def _join_compact(lines: list[str]) -> str:
    result: list[str] = []
    for line in lines:
        if line == "" and (not result or result[-1] == ""):
            continue
        result.append(line)
    return "\n".join(result).strip()


def format_post_market(data: dict[str, Any]) -> str:
    lines = ["<b>🌆 SDE SWING — POST MARKET</b>"]

    trade_date = _date(data.get("trade_date"), long=True)
    if trade_date:
        lines.append(f"📅 {escape(trade_date)}")

    finished_time = _time(data.get("finished_at") or data.get("generated_at") or data.get("completed_at"))
    if finished_time:
        lines.append(f"🕒 {escape(finished_time)} WIB")
    lines.append(SEPARATOR)

    regime = _upper(data.get("market_regime"))
    breadth = _upper(data.get("breadth"))
    execution = _upper(data.get("execution_mode"))
    market_icon = _market_icon(regime, breadth)

    lines += ["", "<b>📊 MARKET PULSE</b>"]
    if regime:
        lines.append(f"{market_icon} Regime      : <b>{escape(regime)}</b>")
    ihsg_change = _smart_pct(data.get("ihsg_change"), signed=True)
    if ihsg_change:
        change_number = _number(data.get("ihsg_change")) or 0.0
        change_icon = "🟢" if change_number > 0 else "🔴" if change_number < 0 else "⚪"
        lines.append(f"{change_icon} IHSG        : <b>{escape(ihsg_change)}</b>")
    if breadth:
        lines.append(f"📈 Breadth     : <b>{escape(breadth)}</b>")
    if execution:
        lines.append(f"🧭 Bias        : <b>{escape(execution)}</b>")

    sector = _sector_line(data)
    if sector:
        lines.append(f"🔥 Sektor kuat : <b>{escape(sector)}</b>")

    technical_rows = _technical_breadth_rows(data)
    if technical_rows:
        lines += ["", "<b>📈 TECHNICAL BREADTH</b>", *technical_rows]

    setup_items = _setup_distribution(data)
    if setup_items:
        lines += ["", "<b>🔥 SETUP DISTRIBUTION</b>"]
        for label, count in setup_items:
            lines.append(f"• {escape(label)}: <b>{count}</b>")

    lines += [
        "",
        "<b>🧭 ARAHAN BESOK</b>",
        escape(_guidance(data)),
    ]

    broker_status = _upper(data.get("broker_status") or data.get("stockbit_status"))
    if broker_status:
        lines += ["", "<b>🏦 BROKER STATUS</b>"]
        lines.append(_metric_line(_status_icon(broker_status), "Stockbit", broker_status))
        if "READY" in broker_status and "NOT READY" not in broker_status:
            lines.append("Broker siap dipakai sebagai konfirmasi pada Final Watchlist.")
        elif "WAIT" in broker_status:
            lines.append("Final Watchlist menunggu broker context yang dipilih sebelum keputusan final.")

    issue_total, issue_notes = _issue_counts(data)
    coverage = _smart_pct(data.get("coverage"), ratio_aware=True)
    impact = _upper(data.get("data_impact"))
    yahoo_status = _upper(data.get("historical_status") or data.get("yahoo_status"))
    technical_status = _upper(data.get("technical_status"))
    candidate_status = _upper(data.get("candidate_status"))

    health_rows: list[str] = []
    if coverage:
        coverage_number = _number(data.get("coverage")) or 0.0
        health_rows.append(_metric_line("🟢" if coverage_number >= 90 else "🟡", "Coverage", coverage))
    if issue_total > 0:
        health_rows.append(_metric_line("⚠️", "Data issue", f"{issue_total} saham"))
        health_rows.append(escape(" • ".join(issue_notes)))
    if impact:
        health_rows.append(_metric_line("🎯", "Impact", impact))
    if yahoo_status:
        health_rows.append(_metric_line(_status_icon(yahoo_status), "Yahoo", yahoo_status))
    if technical_status:
        health_rows.append(_metric_line(_status_icon(technical_status), "Technical", technical_status))
    if candidate_status:
        health_rows.append(_metric_line(_status_icon(candidate_status), "Screening", candidate_status))

    if health_rows:
        lines += ["", "<b>📦 SYSTEM HEALTH</b>", *health_rows]

    lines += [
        "",
        "<b>🎯 NEXT — FINAL WATCHLIST</b>",
        "Final Watchlist akan menentukan kandidat prioritas, kesiapan entry, broker confirmation, Entry/SL/TP, serta alasan utama dan risiko.",
        "",
        "📌 Post Market menggambarkan kondisi pasar dan hasil screening. Keputusan trading final tetap menunggu Final Watchlist.",
    ]

    if _present(data.get("run_id")):
        lines += ["", f"<code>{escape(str(data['run_id']))}</code>"]

    return _join_compact(lines)
