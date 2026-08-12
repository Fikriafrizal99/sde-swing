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
        return "0"
    try:
        return f"{int(float(value)):,}".replace(",", ".")
    except Exception:
        return escape(str(value).strip())


def _pct(value: Any, *, signed: bool = False, decimals: int = 1) -> str:
    number = _number(value)
    if number is None:
        return ""
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:.{decimals}f}%".replace(".", ",")


def _coverage_pct(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    if 0 <= abs(number) <= 1:
        number *= 100.0
    return _pct(number, decimals=1)


def _metric(icon: str, label: str, value: str) -> str:
    return f"{icon} {label:<13}: <b>{escape(value)}</b>"


def _counts(data: Mapping[str, Any]) -> tuple[int, int, int]:
    def integer(*keys: str) -> int:
        for key in keys:
            if not _present(data.get(key)):
                continue
            try:
                return max(0, int(float(data.get(key))))
            except Exception:
                continue
        return 0

    return (
        integer("technical_bullish_count", "bullish_count", "bullish_symbols"),
        integer("technical_neutral_count", "neutral_count", "neutral_symbols"),
        integer("technical_bearish_count", "bearish_count", "bearish_symbols"),
    )


def _directional_share(data: Mapping[str, Any]) -> tuple[float | None, float | None, int]:
    bullish, _neutral, bearish = _counts(data)
    directional = bullish + bearish
    if directional <= 0:
        return None, None, 0
    return 100.0 * bullish / directional, 100.0 * bearish / directional, directional


def _pulse_bar(bullish_pct: float | None, bearish_pct: float | None, width: int = 20) -> str:
    if bullish_pct is None or bearish_pct is None:
        return ""
    green = int(round(width * bullish_pct / 100.0))
    green = max(0, min(width, green))
    red = width - green
    return "🟢" * green + "🔴" * red


def _breadth_label(data: Mapping[str, Any]) -> str:
    bullish_pct, bearish_pct, _ = _directional_share(data)
    if bullish_pct is None or bearish_pct is None:
        raw = _upper(data.get("breadth"))
        return raw or "INSUFFICIENT DATA"
    if bullish_pct >= 60:
        return "BULLISH DOMINANT"
    if bearish_pct >= 60:
        return "BEARISH DOMINANT"
    return "MIXED"


def _market_label(data: Mapping[str, Any]) -> str:
    regime = _upper(data.get("market_regime"))
    breadth = _breadth_label(data)
    if "DATA NOT CURRENT" in regime:
        return "SELECTIVE"
    if "BULL" in regime and breadth != "BEARISH DOMINANT":
        return "RISK-ON"
    if "BEAR" in regime and breadth != "BULLISH DOMINANT":
        return "RISK-OFF"
    if breadth == "BULLISH DOMINANT":
        return "RISK-ON"
    if breadth == "BEARISH DOMINANT":
        return "RISK-OFF"
    return "SELECTIVE"


def _sector_line(data: Mapping[str, Any]) -> str:
    leading = data.get("leading") if isinstance(data.get("leading"), (list, tuple)) else []
    rotating = data.get("rotating_in") if isinstance(data.get("rotating_in"), (list, tuple)) else []
    values = [str(item).strip() for item in [*leading, *rotating] if str(item).strip()]
    return ", ".join(list(dict.fromkeys(values))[:4])


def _guidance(data: Mapping[str, Any]) -> str:
    market = _market_label(data)
    if market == "RISK-ON":
        return (
            "Pasar ditutup dengan bias positif. Fokus pada saham dengan technical score tinggi, "
            "setup matang, dan broker confirmation yang mendukung. Hindari mengejar harga yang "
            "sudah terlalu jauh dari area entry."
        )
    if market == "RISK-OFF":
        return (
            "Pasar ditutup defensif. Utamakan proteksi modal dan hanya pertahankan kandidat dengan "
            "setup sangat kuat serta risk/reward layak. Hindari entry agresif sebelum tekanan pasar mereda."
        )
    return (
        "Pasar masih selektif. Fokus pada kandidat dengan confluence teknikal paling kuat, "
        "entry yang efisien, dan broker confirmation yang mendukung."
    )


def _status_icon(value: Any) -> str:
    status = _upper(value)
    if any(token in status for token in ("FAILED", "INVALID", "ERROR", "BLOCKED", "NOT READY")):
        return "🔴"
    if any(token in status for token in ("WARNING", "WAITING", "EMPTY", "PARTIAL", "UNAVAILABLE")):
        return "🟡"
    if any(token in status for token in ("SUCCESS", "READY", "VALID", "CURRENT")):
        return "🟢"
    return "🟡"


def _issues(data: Mapping[str, Any]) -> tuple[int, list[str]]:
    total = 0
    notes: list[str] = []
    for key, label in (
        ("symbols_not_loaded", "tidak dimuat"),
        ("symbols_invalid", "gagal validasi"),
        ("symbols_skipped", "dilewati"),
    ):
        try:
            count = int(float(data.get(key) or 0))
        except Exception:
            count = 0
        if count <= 0:
            continue
        total += count
        notes.append(f"{count} saham {label}")
    return total, notes


def _setup_items(data: Mapping[str, Any]) -> list[tuple[str, int]]:
    raw = data.get("setup_distribution")
    if not isinstance(raw, Mapping):
        return []
    items: list[tuple[str, int]] = []
    for label, value in raw.items():
        try:
            count = int(float(value))
        except Exception:
            continue
        if count > 0:
            items.append((str(label).replace("_", " ").upper(), count))
    return sorted(items, key=lambda item: item[1], reverse=True)[:5]


def _join(lines: list[str]) -> str:
    result: list[str] = []
    for line in lines:
        if line == "" and (not result or result[-1] == ""):
            continue
        result.append(line)
    return "\n".join(result).strip()


def format_post_market(data: dict[str, Any]) -> str:
    bullish, neutral, bearish = _counts(data)
    bullish_pct, bearish_pct, directional = _directional_share(data)
    breadth_label = _breadth_label(data)
    market_label = _market_label(data)

    lines = ["<b>🌆 SDE SWING — POST MARKET</b>"]
    trade_date = _date(data.get("trade_date"), long=True)
    if trade_date:
        lines.append(f"📅 {escape(trade_date)}")
    finished = _time(data.get("finished_at") or data.get("generated_at") or data.get("completed_at"))
    if finished:
        lines.append(f"🕒 {escape(finished)} WIB")
    lines += [SEPARATOR, "", "<b>📊 MARKET PULSE</b>"]

    if directional > 0 and bullish_pct is not None and bearish_pct is not None:
        lines.append(
            f"🟢 BULLISH <b>{bullish_pct:.0f}%</b>  vs  🔴 BEARISH <b>{bearish_pct:.0f}%</b>"
        )
        bar = _pulse_bar(bullish_pct, bearish_pct)
        if bar:
            lines.append(bar)
        if neutral > 0:
            lines.append(f"🟡 Neutral: <b>{_int_text(neutral)} saham</b> — tidak dipaksa ke sisi bullish/bearish")
    else:
        lines.append("🟡 Arah teknikal: <b>DATA BELUM CUKUP</b>")

    ihsg_status = _upper(data.get("ihsg_status"))
    ihsg_change = _pct(data.get("ihsg_change"), signed=True, decimals=2)
    if ihsg_status == "CURRENT SESSION" and ihsg_change:
        change = _number(data.get("ihsg_change")) or 0.0
        icon = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
        lines.append(f"{icon} IHSG        : <b>{escape(ihsg_change)}</b>")
    elif ihsg_change and not ihsg_status:
        change = _number(data.get("ihsg_change")) or 0.0
        icon = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
        lines.append(f"{icon} IHSG        : <b>{escape(ihsg_change)}</b>")
    else:
        lines.append("🟡 IHSG        : <b>DATA SESI TERKINI TIDAK TERSEDIA</b>")

    lines.append(f"🧭 Market      : <b>{escape(market_label)}</b>")
    lines.append(f"📊 Breadth     : <b>{escape(breadth_label)}</b>")
    sector = _sector_line(data)
    if sector:
        lines.append(f"🔥 Sektor kuat : <b>{escape(sector)}</b>")

    lines += [
        "",
        "<b>📈 TECHNICAL BREADTH</b>",
        f"✅ Valid       : <b>{_int_text(data.get('symbols_valid'))} saham</b>",
    ]
    if bullish + neutral + bearish > 0:
        lines += [
            f"🟢 Bullish     : <b>{_int_text(bullish)}</b>",
            f"🟡 Neutral     : <b>{_int_text(neutral)}</b>",
            f"🔴 Bearish     : <b>{_int_text(bearish)}</b>",
        ]

    setups = _setup_items(data)
    if setups:
        lines += ["", "<b>🔥 SETUP DISTRIBUTION</b>"]
        lines += [f"• {escape(label)}: <b>{count}</b>" for label, count in setups]

    lines += ["", "<b>🧭 ARAHAN BESOK</b>", escape(_guidance(data))]

    broker_status = _upper(data.get("broker_status") or data.get("stockbit_status"))
    if broker_status:
        lines += ["", "<b>🏦 BROKER STATUS</b>", _metric(_status_icon(broker_status), "Stockbit", broker_status)]
        if "READY" in broker_status and "NOT READY" not in broker_status:
            lines.append("Broker siap dipakai sebagai konfirmasi pada Final Watchlist.")
        elif "WAIT" in broker_status:
            lines.append("Final Watchlist menunggu broker context yang dipilih sebelum keputusan final.")

    issue_total, issue_notes = _issues(data)
    coverage = _coverage_pct(data.get("coverage"))
    impact = _upper(data.get("data_impact"))
    health: list[str] = []
    if coverage:
        coverage_number = _number(data.get("coverage")) or 0.0
        if 0 <= coverage_number <= 1:
            coverage_number *= 100.0
        health.append(_metric("🟢" if coverage_number >= 90 else "🟡", "Coverage", coverage))
    if issue_total:
        health.append(_metric("⚠️", "Data issue", f"{issue_total} saham"))
        health.append(escape(" • ".join(issue_notes)))
    if impact:
        health.append(_metric("🎯", "Impact", impact))
    yahoo_status = _upper(data.get("historical_status") or data.get("yahoo_status"))
    if yahoo_status:
        health.append(_metric(_status_icon(yahoo_status), "Yahoo", yahoo_status))
    technical_status = _upper(data.get("technical_status"))
    if technical_status:
        health.append(_metric(_status_icon(technical_status), "Technical", technical_status))
    candidate_status = _upper(data.get("candidate_status"))
    if candidate_status:
        health.append(_metric(_status_icon(candidate_status), "Screening", candidate_status))
    if ihsg_status and ihsg_status != "CURRENT SESSION":
        date_text = str(data.get("ihsg_data_date") or "tidak tersedia")
        health.append(f"⚠️ IHSG session : <b>{escape(ihsg_status)}</b> ({escape(date_text)})")

    if health:
        lines += ["", "<b>📦 SYSTEM HEALTH</b>", *health]

    lines += [
        "",
        "<b>🎯 NEXT — FINAL WATCHLIST</b>",
        "Final Watchlist akan menentukan kandidat prioritas, kesiapan entry, broker confirmation, Entry/SL/TP, serta alasan utama dan risiko.",
        "",
        "📌 Post Market menggambarkan kondisi pasar dan hasil screening. Keputusan trading final tetap menunggu Final Watchlist.",
    ]
    if _present(data.get("run_id")):
        lines += ["", f"<code>{escape(str(data['run_id']))}</code>"]
    return _join(lines)
