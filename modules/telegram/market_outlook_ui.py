from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Iterable, Mapping


SEPARATOR = "━━━━━━━━━━━━━━━━━━━"
_MISSING = "data tidak tersedia"


def _dt(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _date(value: Any, *, long: bool = False) -> str:
    parsed = _dt(value)
    if parsed is None:
        return "data tidak tersedia"
    months = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    if not long:
        return f"{parsed.day:02d} {months[parsed.month]} {parsed.year}"
    days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    full_months = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{days[parsed.weekday()]}, {parsed.day} {full_months[parsed.month]} {parsed.year}"


def _time(value: Any) -> str:
    parsed = _dt(value)
    return parsed.strftime("%H:%M") if parsed else ""


def _upper(value: Any, fallback: str = _MISSING) -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").upper() if text else fallback.upper()


def _pct(value: Any, decimals: int = 1, *, signed: bool = False, ratio_aware: bool = False) -> str:
    try:
        number = float(value)
    except Exception:
        return _MISSING
    if ratio_aware and 0 <= abs(number) <= 1:
        number *= 100.0
    prefix = "+" if signed and number > 0 else ""
    return (prefix + f"{number:.{decimals}f}%").replace(".", ",")


def _global_price(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return _MISSING
    absolute = abs(number)
    decimals = 0 if absolute >= 1000 else 2
    rendered = f"{number:,.{decimals}f}"
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def _clean_items(value: Any, limit: int = 4) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw = value.replace("\r", "\n").replace(";", "\n").split("\n")
    elif isinstance(value, Iterable) and not isinstance(value, Mapping):
        raw = list(value)
    else:
        raw = [value]
    result: list[str] = []
    for item in raw:
        text = str(item or "").strip(" •-\t")
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _aligned_block(rows: list[tuple[str, str]]) -> str:
    clean = [(str(label), str(value)) for label, value in rows if str(value).strip()]
    if not clean:
        return ""
    width = max(len(label) for label, _ in clean)
    body = "\n".join(f"{label:<{width}} : {value}" for label, value in clean)
    return f"<pre>{escape(body)}</pre>"


def _human_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Data IHSG belum tersedia."
    replacements = {
        "ihsg": "IHSG",
        "ma20": "MA20",
        "ma50": "MA50",
        "ma200": "MA200",
        "sma20": "SMA20",
        "sma50": "SMA50",
        "rsi": "RSI",
        "macd": "MACD",
    }
    for old, new in replacements.items():
        text = text.replace(old, new).replace(old.upper(), new)
    return text[:1].upper() + text[1:]


def _short_global_name(value: Any) -> str:
    name = str(value or "Instrumen").strip()
    aliases = {
        "Brent Crude Oil": "Brent Oil",
        "Dow Jones Industrial Average": "Dow Jones",
        "US Dollar Index": "Dollar Index",
        "U.S. Dollar Index": "Dollar Index",
        "NASDAQ Composite": "Nasdaq",
        "Nasdaq Composite": "Nasdaq",
        "Nikkei 225": "Nikkei 225",
        "Hang Seng Index": "Hang Seng",
        "KOSPI Composite Index": "KOSPI",
    }
    return aliases.get(name, name)


def _global_rows(instruments: list[Mapping[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in instruments[:9]:
        name = _short_global_name(item.get("display_name") or item.get("instrument"))
        status = str(item.get("freshness_status") or "").upper()
        if status not in {"VALID", "DELAYED_ACCEPTED"}:
            rows.append((name, _MISSING))
            continue
        try:
            change = float(item.get("change_pct") or 0.0)
        except Exception:
            change = 0.0
        marker = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
        rows.append((name, f"{_global_price(item.get('close'))}  {marker} {_pct(change, 2, signed=True)}"))
    return rows


def _market_condition_rows(data: Mapping[str, Any], tone: str, coverage: Any) -> list[tuple[str, str]]:
    global_value = tone
    coverage_text = _pct(coverage, 1, ratio_aware=True)
    if coverage_text != _MISSING:
        global_value = f"{tone} | Coverage {coverage_text}"
    return [
        ("Regime", _upper(data.get("market_regime"))),
        ("Execution", _upper(data.get("execution_mode"), "SELECTIVE")),
        ("Global", global_value),
        ("IHSG", _upper(data.get("ihsg_trend"))),
        ("Momentum", _upper(data.get("ihsg_momentum"))),
        ("Breadth", _upper(data.get("breadth"))),
    ]


def _global_interpretation(tone: str, regime: str) -> str:
    market_bias = "bias IHSG"
    if "BULL" in regime:
        market_bias = "bias bullish IHSG"
    elif "BEAR" in regime:
        market_bias = "bias bearish IHSG"
    if any(token in tone for token in ("RISK ON", "BULL", "POSITIVE")):
        return f"Global cenderung mendukung. Tetap gunakan {market_bias} sebagai konteks, bukan sinyal entry langsung."
    if any(token in tone for token in ("RISK OFF", "BEAR", "NEGATIVE")):
        return f"Global memberi tekanan. Perketat seleksi dan konfirmasi sebelum mengikuti {market_bias}."
    return f"Global masih netral. Belum ada tekanan eksternal yang cukup kuat untuk membatalkan {market_bias}."


def _bias_lines(regime: str, execution: str) -> tuple[str, str, str]:
    if "BULL" in regime:
        return (
            f"🟢 Bias: BULLISH — {execution}",
            "Agresif hanya pada setup berkualitas.",
            "Tidak semua saham ikut dibeli hanya karena market sedang bullish.",
        )
    if "BEAR" in regime:
        return (
            f"🔴 Bias: BEARISH — {execution}",
            "Defensif dan kurangi agresivitas entry.",
            "Prioritaskan proteksi modal dan hindari memaksakan setup.",
        )
    return (
        f"🟡 Bias: SELECTIVE — {execution}",
        "Tetap selektif dan tunggu konfirmasi yang lengkap.",
        "Hindari memaksakan entry saat struktur market belum dominan.",
    )


def format_market_outlook(data: dict[str, Any]) -> str:
    sentiment = data.get("global_sentiment") if isinstance(data.get("global_sentiment"), Mapping) else {}
    instruments = [item for item in data.get("global_instruments", []) or [] if isinstance(item, Mapping)]
    regime = _upper(data.get("market_regime"), "UNKNOWN")
    execution = _upper(data.get("execution_mode"), "SELECTIVE")
    tone = _upper(sentiment.get("sentiment_state") or data.get("global_tone"), "NEUTRAL")
    coverage = data.get("global_coverage") if data.get("global_coverage") not in (None, "") else data.get("coverage")
    created_at = data.get("snapshot_created_at") or data.get("created_at")

    lines = [
        "<b>🌅 SDE SWING — MARKET OUTLOOK</b>",
        f"📅 {escape(_date(data.get('trade_date'), long=True))}",
    ]
    if _time(created_at):
        lines.append(f"🕒 Snapshot diperbarui: {escape(_time(created_at))} WIB")

    lines += [
        SEPARATOR,
        "",
        "<b>📊 MARKET CONDITION</b>",
        _aligned_block(_market_condition_rows(data, tone, coverage)),
        escape(_human_reason(data.get("ihsg_reason") or data.get("reason"))),
    ]

    if instruments:
        lines += [
            "",
            SEPARATOR,
            "<b>🌍 GLOBAL MARKET</b>",
            _aligned_block(_global_rows(instruments)),
            escape(_global_interpretation(tone, regime)),
        ]

    sector_groups = [
        ("🔥 LEADING", data.get("leading", [])),
        ("🟢 ROTATING IN", data.get("rotating_in", [])),
        ("🟡 WEAKENING", data.get("weakening", [])),
        ("🔴 ROTATING OUT", data.get("rotating_out") or data.get("lagging", [])),
    ]
    if any(_clean_items(values, 4) for _, values in sector_groups):
        lines += ["", SEPARATOR, "<b>🔄 SECTOR ROTATION</b>"]
        for title, values in sector_groups:
            items = _clean_items(values, 4)
            if not items:
                continue
            lines.append(f"<b>{escape(title)}</b>")
            lines.extend(f"• {escape(item)}" for item in items)
            lines.append("")
        if lines[-1] == "":
            lines.pop()

    lines += [
        "",
        SEPARATOR,
        "<b>🎯 TRADING PLAN</b>",
        "<b>Prioritas:</b>",
        "• Cari setup dari sektor LEADING / ROTATING IN.",
        "• Technical Quality dan Entry Readiness harus kuat.",
        "• Utamakan broker accumulation / confirmation.",
        "• BUY CANDIDATE tetap menunggu trigger.",
        "• Entry hanya di area yang sudah ditentukan.",
        "",
        "<b>⚠️ Hindari:</b>",
        "• Chase harga.",
        "• Setup dengan RR buruk.",
        "• Broker distribution kuat.",
        "• Saham sektor weakening tanpa katalis kuat.",
        "",
        SEPARATOR,
        "<b>📌 SDE BIAS BESOK</b>",
    ]
    bias_head, bias_line_1, bias_line_2 = _bias_lines(regime, execution)
    lines += [f"<b>{escape(bias_head)}</b>", escape(bias_line_1), escape(bias_line_2)]

    data_status = _upper(data.get("global_market_status"), "VALID")
    ihsg_date = _date(data.get("ihsg_data_date") or data.get("trade_date"))
    coverage_text = _pct(coverage, 1, ratio_aware=True)
    data_parts = [f"Global {coverage_text}" if coverage_text != _MISSING else "", f"IHSG {ihsg_date}" if ihsg_date else ""]
    data_parts = [item for item in data_parts if item]
    lines += [
        "",
        f"<b>📡 Data: {escape(data_status)}</b>",
        escape(" | ".join(data_parts)),
        "",
        "⚠️ Sentimen global adalah konteks Market Outlook dan tidak mengubah scoring saham langsung.",
    ]
    return "\n".join(line for line in lines if line is not None).strip()
