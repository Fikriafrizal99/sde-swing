from __future__ import annotations

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


def _date(value: Any, *, long: bool = False) -> str:
    parsed = _dt(value)
    if parsed is None:
        return ""
    if not long:
        months = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
        return f"{parsed.day:02d} {months[parsed.month]} {parsed.year}"
    days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    months = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{days[parsed.weekday()]}, {parsed.day} {months[parsed.month]} {parsed.year}"


def _full_date(value: Any) -> str:
    parsed = _dt(value)
    if parsed is None:
        return ""
    months = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{parsed.day} {months[parsed.month]} {parsed.year}"


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


def _coverage(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    if 0 <= number <= 1:
        number *= 100.0
    return _pct(number, decimals=1)


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
    """Allocate integer display units by largest remainder, deterministically."""
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


def _percentages(data: Mapping[str, Any]) -> tuple[int, int, int] | None:
    counts = _counts(data)
    if sum(counts) <= 0:
        return None
    return _allocate_units(counts, 100)


def _technical_current(data: Mapping[str, Any]) -> bool:
    status = _upper(data.get("breadth_status"))
    if status in {"NOT CURRENT", "STALE", "UNAVAILABLE", "FAILED"}:
        return False
    trade_date = _iso_date(data.get("trade_date"))
    technical_date = _iso_date(data.get("technical_data_date"))
    # The normal runtime always carries both dates.  Once a trade date is
    # present, missing lineage is as unsafe as a mismatch and fails closed.
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
    if status in {"NOT CURRENT", "STALE", "UNAVAILABLE", "FAILED"}:
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


def _sector_payload(data: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = data.get("sector_rotation")
    return payload if isinstance(payload, Mapping) else data


def _sector_current(data: Mapping[str, Any]) -> bool:
    payload = _sector_payload(data)
    status = _upper(data.get("sector_rotation_status") or payload.get("status"))
    if status and status not in {"VALID", "CURRENT", "READY"}:
        return False
    trade_date = _iso_date(data.get("trade_date"))
    rotation_date = _iso_date(
        data.get("sector_rotation_trade_date")
        or payload.get("trade_date")
    )
    return (not trade_date and not rotation_date) or (
        bool(trade_date and rotation_date) and trade_date == rotation_date
    )


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
    if breadth in {"DATA NOT CURRENT", "INSUFFICIENT DATA"}:
        return "SELECTIVE"

    regime = _upper(data.get("market_regime"))
    ihsg = _number(data.get("ihsg_change")) if _ihsg_current(data) else None
    explicit = regime.replace("-", " ")
    if explicit in {"RISK ON", "RISK OFF", "SELECTIVE"}:
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


def _sector_values(data: Mapping[str, Any]) -> list[str]:
    if not _sector_current(data):
        return []
    payload = _sector_payload(data)
    values: list[str] = []
    for key in ("leading", "rotating_in", "improving"):
        raw = payload.get(key)
        if isinstance(raw, (list, tuple)):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return list(dict.fromkeys(values))


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


def _status_icon(value: Any) -> str:
    status = _upper(value)
    if any(token in status for token in ("FAILED", "INVALID", "ERROR", "BLOCKED", "UNAVAILABLE", "NOT CURRENT", "NOT READY")):
        return "🔴"
    if any(token in status for token in ("WARNING", "WAITING", "EMPTY", "PARTIAL", "DEGRADED")):
        return "🟡"
    if any(token in status for token in ("READY", "VALID", "CURRENT", "SUCCESS")):
        return "🟢"
    return "🟡"


def _guidance(data: Mapping[str, Any], setups: list[tuple[str, int]], broker: str) -> list[str]:
    breadth = _breadth_label(data)
    market = _market_label(data)
    ihsg = _number(data.get("ihsg_change")) if _ihsg_current(data) else None
    broker_ready = broker == "READY"

    if ihsg is not None and ihsg < 0 and breadth == "BEARISH DOMINANT":
        return [
            "Pasar ditutup melemah dengan breadth didominasi saham bearish.",
            "Prioritaskan proteksi modal dan batasi kandidat pada setup",
            "dengan technical quality dan broker confirmation terbaik.",
        ]
    if ihsg is not None and ihsg > 0 and breadth == "BULLISH DOMINANT":
        return [
            "Pasar ditutup menguat dengan breadth didominasi saham bullish.",
            "Fokus pada setup matang yang masih berada di area entry",
            "dan mendapat broker confirmation yang mendukung.",
        ]
    if ihsg is not None and ihsg < 0:
        lines = [
            "Pasar ditutup melemah dengan breadth cenderung netral.",
            "Fokus pada saham dengan setup matang, technical score tinggi,",
            "dan broker confirmation yang mendukung." if broker_ready else "dan tunggu broker confirmation yang mendukung.",
        ]
    elif ihsg is not None and ihsg > 0:
        lines = [
            "Pasar ditutup menguat dengan breadth cenderung selektif.",
            "Fokus pada saham dengan setup matang yang masih berada di area entry",
            "dan broker confirmation yang mendukung." if broker_ready else "dan tunggu broker confirmation yang mendukung.",
        ]
    elif market == "RISK-OFF" or breadth == "BEARISH DOMINANT":
        lines = [
            "Pasar berada dalam kondisi defensif dengan breadth bearish.",
            "Prioritaskan proteksi modal dan batasi kandidat pada setup",
            "dengan technical quality dan broker confirmation terbaik.",
        ]
    elif market == "RISK-ON" or breadth == "BULLISH DOMINANT":
        lines = [
            "Pasar berada dalam kondisi positif dengan breadth bullish.",
            "Fokus pada setup matang yang masih berada di area entry",
            "dan mendapat broker confirmation yang mendukung.",
        ]
    else:
        lines = [
            "Pasar berada dalam kondisi selektif dengan breadth campuran.",
            "Fokus pada saham dengan setup matang dan konfirmasi yang lengkap.",
        ]

    if not setups:
        lines.append("Setup current belum tersedia untuk sesi ini.")
    lines.extend(["", "Hindari mengejar saham yang sudah terlalu jauh dari area entry."])
    return lines


def _health_lines(data: Mapping[str, Any], trade_date: str) -> list[str]:
    coverage_number = _number(data.get("coverage"))
    if coverage_number is not None and 0 <= coverage_number <= 1:
        coverage_number *= 100.0
    if coverage_number is None:
        coverage_icon, coverage_text = "🔴", "DATA TIDAK TERSEDIA"
    else:
        coverage_icon = "🟢" if coverage_number >= 90 else "🟡"
        coverage_text = _coverage(coverage_number)

    technical_status = _upper(data.get("technical_status"))
    if not _technical_current(data):
        technical_status = "NOT CURRENT"
    if not technical_status:
        technical_status = "UNAVAILABLE"

    screening_status = _upper(data.get("screening_result") or data.get("candidate_status"))
    if not screening_status:
        screening_status = "WAITING"

    return [
        f"{coverage_icon} Coverage  : {coverage_text or 'DATA TIDAK TERSEDIA'}",
        f"{_status_icon(technical_status)} Technical : {escape(technical_status, quote=False)}",
        f"{_status_icon(screening_status)} Screening : {escape(screening_status, quote=False)}",
        f"📅 Data      : {_text(_full_date(trade_date), 'DATA TIDAK TERSEDIA')}",
    ]


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
    """Render the single market-first Telegram Post Market contract.

    ``CURRENT_V2`` remains accepted as a runtime/schema field, but it never
    selects the retired UI.  This formatter is presentation-only and refuses
    to render explicitly stale section inputs as current-session facts.
    """
    trade_date = _iso_date(data.get("trade_date"))
    percentages = _percentages(data) if _technical_current(data) else None
    breadth = _breadth_label(data)
    market = _market_label(data)
    sectors = _sector_values(data)
    setups = _setup_items(data)
    broker = _upper(data.get("broker_status") or data.get("stockbit_status")) or "WAITING"

    lines = [
        "🌆 SDE SWING — POST MARKET",
        f"📅 {_text(_date(trade_date, long=True), 'DATA TIDAK TERSEDIA')}",
    ]
    finished = _time(data.get("finished_at") or data.get("generated_at") or data.get("completed_at"))
    if finished:
        lines.append(f"🕒 {finished} WIB")
    lines += [SEPARATOR, "", "📊 MARKET PULSE"]

    if percentages is None:
        lines.append("Data technical sesi berjalan tidak tersedia.")
    else:
        buy, neutral, sell = percentages
        lines.append(_pulse_bar(data))
        lines.append(f"Buy {buy}% · Neutral {neutral}% · Sell {sell}%")
    lines += [_ihsg_line(data), f"🧭 Market  : {market}", f"📊 Breadth : {breadth}"]

    lines += ["", "🔥 Sektor kuat"]
    if sectors:
        lines.extend(f"• {_text(sector)}" for sector in sectors)
    else:
        lines.append("⚠️ Data sektor current tidak tersedia.")

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

    lines += ["", "🧭 ARAHAN BESOK", *_guidance(data, setups, broker)]
    lines += [
        "",
        "🏦 BROKER STATUS",
        f"{_status_icon(broker)} Stockbit : {_text(broker)}",
    ]
    if broker == "READY":
        lines.append("Broker siap digunakan sebagai konfirmasi di Final Watchlist.")
    elif broker in {"WAITING", "NOT AVAILABLE", "UNAVAILABLE"}:
        lines.append("Broker belum siap digunakan sebagai konfirmasi di Final Watchlist.")
    else:
        lines.append("Status broker mengikuti artifact runtime yang tersedia.")

    lines += ["", "📦 SYSTEM HEALTH", *_health_lines(data, trade_date)]
    lines += [
        "",
        "🎯 NEXT — FINAL WATCHLIST",
        "Final Watchlist akan menentukan kandidat prioritas,",
        "broker confirmation, Entry, SL, TP, serta keputusan final.",
        "",
        "📌 Post Market hanya menggambarkan kondisi pasar setelah penutupan.",
        "Keputusan trading tetap ditentukan pada Final Watchlist.",
    ]
    if _present(data.get("run_id")):
        lines += ["", _text(data.get("run_id"))]
    return "\n".join(lines).strip()


__all__ = ["format_post_market"]
