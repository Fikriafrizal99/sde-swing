from __future__ import annotations

"""Single-message Post Market presentation contract.

This formatter is intentionally presentation-only. It keeps the exact compact
Telegram structure approved for SDE Swing while failing closed when current
session lineage is unavailable.
"""

from datetime import date, datetime
from html import escape
from math import floor
from typing import Any, Mapping


SEPARATOR = "━━━━━━━━━━━━━━━━━━━"
PULSE_WIDTH = 10


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "nan", "none", "null"}
    return True


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _iso_date(value: Any) -> str:
    parsed = _dt(value)
    return parsed.date().isoformat() if parsed else ""


def _date(value: Any) -> str:
    parsed = _dt(value)
    if parsed is None:
        return ""
    days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    months = [
        "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    ]
    return f"{days[parsed.weekday()]}, {parsed.day} {months[parsed.month]} {parsed.year}"


def _time(value: Any) -> str:
    parsed = _dt(value)
    return parsed.strftime("%H:%M") if parsed else ""


def _upper(value: Any) -> str:
    if not _present(value):
        return ""
    return str(value).strip().replace("_", " ").upper()


def _text(value: Any, fallback: str = "") -> str:
    if not _present(value):
        return fallback
    return escape(str(value), quote=False)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _integer(value: Any) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _int_text(value: Any) -> str:
    return f"{_integer(value):,}".replace(",", ".")


def _pct(value: Any, *, signed: bool = False, decimals: int = 1) -> str:
    number = _number(value)
    if number is None:
        return ""
    prefix = "+" if signed and number > 0 else ""
    return f"{prefix}{number:.{decimals}f}%".replace(".", ",")


def _counts(data: Mapping[str, Any]) -> tuple[int, int, int]:
    def value(*keys: str) -> int:
        for key in keys:
            if _present(data.get(key)):
                return _integer(data.get(key))
        return 0

    return (
        value("technical_bullish_count", "bullish_count", "bullish_symbols"),
        value("technical_neutral_count", "neutral_count", "neutral_symbols"),
        value("technical_bearish_count", "bearish_count", "bearish_symbols"),
    )


def _allocate_units(counts: tuple[int, int, int], units: int) -> tuple[int, int, int]:
    total = sum(max(0, int(value)) for value in counts)
    if total <= 0 or units <= 0:
        return (0, 0, 0)
    raw = [units * max(0, int(value)) / total for value in counts]
    allocated = [floor(value) for value in raw]
    remaining = units - sum(allocated)
    order = sorted(range(len(raw)), key=lambda index: (-(raw[index] - allocated[index]), index))
    for index in order[:remaining]:
        allocated[index] += 1
    return tuple(allocated)  # type: ignore[return-value]


def _technical_current(data: Mapping[str, Any]) -> bool:
    status = _upper(data.get("breadth_status"))
    if status in {
        "NOT CURRENT", "STALE", "UNAVAILABLE", "NOT AVAILABLE", "NOT READY",
        "FAILED", "INVALID", "ERROR",
    }:
        return False
    trade_date = _iso_date(data.get("trade_date"))
    technical_date = _iso_date(data.get("technical_data_date"))
    return (not trade_date and not technical_date) or (
        bool(trade_date and technical_date) and trade_date == technical_date
    )


def _ihsg_current(data: Mapping[str, Any]) -> bool:
    status = _upper(data.get("ihsg_status"))
    if status not in {"CURRENT SESSION", "CURRENT"}:
        return False
    trade_date = _iso_date(data.get("trade_date"))
    data_date = _iso_date(data.get("ihsg_data_date"))
    return bool(data_date) and (not trade_date or trade_date == data_date)


def _candidate_current(data: Mapping[str, Any]) -> bool:
    status = _upper(data.get("candidate_status"))
    if status in {
        "NOT CURRENT", "STALE", "UNAVAILABLE", "NOT AVAILABLE", "NOT READY",
        "FAILED", "INVALID", "ERROR",
    }:
        return False
    trade_date = _iso_date(data.get("trade_date"))
    candidate_date = _iso_date(
        data.get("candidate_data_date")
        or data.get("candidate_ranking_trade_date")
        or data.get("candidate_trade_date")
    )
    return (not trade_date and not candidate_date) or (
        bool(trade_date and candidate_date) and trade_date == candidate_date
    )


def _true_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "ready", "current", "valid"}


def _broker_presentation_status(data: Mapping[str, Any]) -> str:
    """Fail closed; used only to decide whether guidance waits for broker data."""
    declared = _upper(data.get("broker_status") or data.get("stockbit_status"))
    upstream = _upper(
        data.get("broker_upstream_status")
        or data.get("broker_data_status")
        or data.get("broker_freshness_status")
    )
    reason = _upper(data.get("broker_readiness_reason"))
    combined = " ".join(value for value in (declared, upstream, reason) if value)

    failure_tokens = (
        "FAILED", "ERROR", "INVALID", "FILE NOT FOUND", "EMPTY DATA",
        "PARSE FAILED", "SCHEMA INVALID", "UNAVAILABLE", "BLOCKED",
        "NOT AVAILABLE", "NOT READY",
    )
    if any(token in combined for token in failure_tokens):
        return "NOT READY"

    available = _true_flag(data.get("broker_artifact_available"))
    verified = _true_flag(data.get("broker_data_verified"))
    current = _true_flag(data.get("broker_data_current"))
    if not available:
        return "NOT READY"

    if any(token in combined for token in ("STALE", "NOT CURRENT", "DATE MISMATCH", "FILE WRITING")):
        return "WAITING"

    trade_date = _iso_date(data.get("trade_date"))
    broker_date = _iso_date(
        data.get("broker_data_date") or data.get("broker_date") or data.get("broker_trade_date")
    )
    if trade_date and (not broker_date or broker_date != trade_date):
        return "WAITING"
    if not verified or not current or declared != "READY":
        return "WAITING"
    return "READY"


def _daily_sector_payload(data: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = data.get("daily_sector")
    return payload if isinstance(payload, Mapping) else {}


def _daily_sector_current(data: Mapping[str, Any]) -> bool:
    payload = _daily_sector_payload(data)
    status = _upper(data.get("daily_sector_status") or payload.get("status"))
    if status not in {"VALID", "CURRENT", "READY"}:
        return False
    trade_date = _iso_date(data.get("trade_date"))
    sector_date = _iso_date(data.get("daily_sector_trade_date") or payload.get("trade_date"))
    data_date = _iso_date(payload.get("data_date"))
    return bool(trade_date and sector_date and data_date) and trade_date == sector_date == data_date


def _daily_sector_rows(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not _daily_sector_current(data):
        return []
    raw = _daily_sector_payload(data).get("sectors")
    if not isinstance(raw, list):
        return []
    rows = [row for row in raw if isinstance(row, Mapping) and _present(row.get("sector"))]
    return sorted(rows, key=lambda row: _integer(row.get("rank")) or 999)


def _daily_sector_lines(data: Mapping[str, Any]) -> list[str]:
    rows = _daily_sector_rows(data)
    if not rows:
        return []
    strongest = rows[:3]
    weakest = list(reversed(rows[-3:])) if len(rows) > 3 else []
    lines: list[str] = []

    def render(icon: str, row: Mapping[str, Any]) -> str:
        sector = _text(row.get("sector"))
        ret = _pct(row.get("median_return_1d"), signed=True, decimals=2)
        breadth = _number(row.get("positive_breadth"))
        breadth_text = _pct((breadth or 0.0) * 100.0, decimals=0) if breadth is not None else ""
        suffix = " · ".join(item for item in (ret, f"Breadth {breadth_text}" if breadth_text else "") if item)
        return f"{icon} {sector}{f'  {suffix}' if suffix else ''}"

    lines.extend(render("🔥", row) for row in strongest)
    if weakest:
        lines.append("")
        lines.extend(render("🔻", row) for row in weakest)
    return lines


def _breadth_label(data: Mapping[str, Any]) -> str:
    if not _technical_current(data):
        return "DATA NOT CURRENT"
    bullish, neutral, bearish = _counts(data)
    total = bullish + neutral + bearish
    if total <= 0:
        return "INSUFFICIENT DATA"
    if bullish / total >= 0.60:
        return "BULLISH DOMINANT"
    if bearish / total >= 0.60:
        return "BEARISH DOMINANT"
    if neutral / total >= 0.50:
        return "NEUTRAL DOMINANT"
    return "MIXED"


def _market_label(data: Mapping[str, Any]) -> str:
    breadth = _breadth_label(data)
    if breadth in {"DATA NOT CURRENT", "INSUFFICIENT DATA"} or not _ihsg_current(data):
        return "SELECTIVE"

    regime = _upper(data.get("market_regime"))
    ihsg = _number(data.get("ihsg_change"))
    explicit = regime.replace("-", " ")
    if explicit == "RISK ON" and breadth == "BULLISH DOMINANT":
        return "RISK-ON"
    if explicit == "RISK OFF" and breadth == "BEARISH DOMINANT":
        return "RISK-OFF"
    if explicit == "SELECTIVE":
        return "SELECTIVE"
    if breadth == "BULLISH DOMINANT" and "BULL" in regime and (ihsg is None or ihsg >= 0):
        return "RISK-ON"
    if breadth == "BEARISH DOMINANT" and "BEAR" in regime and (ihsg is None or ihsg <= 0):
        return "RISK-OFF"
    return "SELECTIVE"


def _pulse_bar(data: Mapping[str, Any]) -> str:
    green, yellow, red = _allocate_units(_counts(data), PULSE_WIDTH)
    if green + yellow + red != PULSE_WIDTH:
        return ""
    return "🟩" * green + "🟨" * yellow + "🟥" * red


def _percentages(data: Mapping[str, Any]) -> tuple[int, int, int] | None:
    counts = _counts(data)
    if sum(counts) <= 0:
        return None
    return _allocate_units(counts, 100)


def _setup_items(data: Mapping[str, Any]) -> list[tuple[str, int]]:
    if not _technical_current(data) or not _candidate_current(data):
        return []
    raw = data.get("setup_distribution")
    if not isinstance(raw, Mapping):
        return []
    items: list[tuple[str, int]] = []
    for label, value in raw.items():
        count = _integer(value)
        if count > 0:
            items.append((str(label).replace("_", " ").upper(), count))
    return sorted(items, key=lambda item: (-item[1], item[0]))[:5]


def _market_breadth_sentence(data: Mapping[str, Any]) -> str:
    breadth = _breadth_label(data)
    ihsg = _number(data.get("ihsg_change")) if _ihsg_current(data) else None

    if breadth == "DATA NOT CURRENT":
        return "Data technical breadth sesi berjalan belum tersedia."
    if breadth == "INSUFFICIENT DATA":
        return "Breadth sesi berjalan belum cukup untuk menyimpulkan kondisi pasar."

    if ihsg is None:
        if breadth == "BULLISH DOMINANT":
            return "Breadth sesi berjalan didominasi saham bullish; data IHSG current belum tersedia."
        if breadth == "BEARISH DOMINANT":
            return "Breadth sesi berjalan didominasi saham bearish; data IHSG current belum tersedia."
        if breadth == "NEUTRAL DOMINANT":
            return "Breadth sesi berjalan cenderung netral; data IHSG current belum tersedia."
        return "Breadth sesi berjalan menunjukkan kondisi campuran; data IHSG current belum tersedia."

    direction = "menguat" if ihsg > 0 else "melemah" if ihsg < 0 else "datar"
    subject = "Pasar" if ihsg != 0 else "IHSG"
    if breadth == "BULLISH DOMINANT":
        if ihsg < 0:
            return "Pasar ditutup melemah, namun breadth masih didominasi saham bullish."
        return f"{subject} ditutup {direction} dengan breadth didominasi saham bullish."
    if breadth == "BEARISH DOMINANT":
        if ihsg > 0:
            return "IHSG ditutup menguat, namun breadth pasar masih didominasi saham bearish."
        return f"{subject} ditutup {direction} dengan breadth didominasi saham bearish."
    if breadth == "NEUTRAL DOMINANT":
        return f"{subject} ditutup {direction} dengan breadth cenderung netral."
    return f"{subject} ditutup {direction} dengan breadth campuran."


def _guidance(data: Mapping[str, Any], setups: list[tuple[str, int]]) -> list[str]:
    breadth = _breadth_label(data)
    market = _market_label(data)
    lines = [_market_breadth_sentence(data)]

    if market == "RISK-OFF" or breadth == "BEARISH DOMINANT":
        lines.extend([
            "Prioritaskan proteksi modal dan batasi kandidat pada setup",
            "dengan technical quality terbaik dan konfirmasi yang lengkap.",
        ])
    elif market == "RISK-ON" or breadth == "BULLISH DOMINANT":
        lines.extend([
            "Fokus pada setup matang yang masih berada di area entry",
            "dan memiliki konfirmasi yang lengkap.",
        ])
    else:
        lines.append("Fokus pada saham dengan setup matang dan konfirmasi yang lengkap.")

    if _broker_presentation_status(data) != "READY":
        lines.append("Tunggu data broker sesi berjalan sebelum menggunakannya sebagai konfirmasi.")
    if not setups:
        lines.append("Setup current belum tersedia untuk sesi ini.")
    lines.extend(["", "Hindari mengejar saham yang sudah terlalu jauh dari area entry."])
    return lines


def _ihsg_line(data: Mapping[str, Any]) -> str:
    if _ihsg_current(data):
        change = _number(data.get("ihsg_change"))
        rendered = _pct(change, signed=True, decimals=2)
        icon = "🟢" if change is not None and change > 0 else "🔴" if change is not None and change < 0 else "🟡"
        return f"{icon} IHSG    : {rendered or '0,00%'}"
    status = _upper(data.get("ihsg_status")) or "NOT CURRENT SESSION"
    data_date = _iso_date(data.get("ihsg_data_date"))
    suffix = f" ({data_date})" if data_date else ""
    return f"🟡 IHSG    : DATA SESI TERKINI TIDAK TERSEDIA [{escape(status, quote=False)}{suffix}]"


def format_post_market(data: dict[str, Any]) -> str:
    """Render the approved compact Post Market Telegram message exactly."""
    trade_date = _iso_date(data.get("trade_date"))
    percentages = _percentages(data) if _technical_current(data) else None
    breadth = _breadth_label(data)
    market = _market_label(data)
    daily_sector_lines = _daily_sector_lines(data)
    setups = _setup_items(data)

    lines = [
        "🌆 SDE SWING — POST MARKET",
        f"📅 {_text(_date(trade_date), 'DATA TIDAK TERSEDIA')}",
    ]
    finished = _time(data.get("finished_at") or data.get("generated_at") or data.get("completed_at"))
    if finished:
        lines.append(f"🕒 {finished} WIB")
    lines += [SEPARATOR, "", "📊 MARKET PULSE"]

    if percentages is None:
        lines.append("Data technical sesi berjalan tidak tersedia.")
    else:
        bullish, neutral, bearish = percentages
        lines.append(_pulse_bar(data))
        lines.append(f"Bullish {bullish}% · Neutral {neutral}% · Bearish {bearish}%")
    lines += [_ihsg_line(data), f"🧭 Market  : {market}", f"📊 Breadth : {breadth}"]

    lines += ["", "🔄 ROTASI SEKTOR HARI INI"]
    if daily_sector_lines:
        lines.extend(daily_sector_lines)
    else:
        lines.append("⚠️ Data sektor sesi berjalan belum cukup.")

    lines += ["", "📈 TECHNICAL BREADTH"]
    if _technical_current(data) and sum(_counts(data)) > 0:
        bullish, neutral, bearish = _counts(data)
        lines += [
            f"✅ Valid   : {_int_text(data.get('symbols_valid'))} saham",
            f"🟢 Bullish : {_int_text(bullish)}",
            f"🟡 Neutral : {_int_text(neutral)}",
            f"🔴 Bearish : {_int_text(bearish)}",
        ]
    else:
        lines.append("⚠️ Data technical breadth sesi berjalan tidak tersedia.")

    lines += ["", "🔥 SETUP DISTRIBUTION"]
    if setups:
        lines.extend(f"• {_text(label)} : {count}" for label, count in setups)
    else:
        lines.append("⚠️ Data setup current tidak tersedia.")

    lines += ["", "🧭 ARAHAN BESOK", *_guidance(data, setups)]

    if _present(data.get("run_id")):
        lines += ["", _text(data.get("run_id"))]
    return "\n".join(lines).strip()


__all__ = ["format_post_market"]
