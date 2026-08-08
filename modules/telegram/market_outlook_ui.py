from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Iterable, Mapping


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


def _pct(value: Any, decimals: int = 1, *, signed: bool = False, ratio_aware: bool = False) -> str:
    number = _number(value)
    if number is None:
        return ""
    if ratio_aware and 0 <= abs(number) <= 1:
        number *= 100.0
    prefix = "+" if signed and number > 0 else ""
    return (prefix + f"{number:.{decimals}f}%").replace(".", ",")


def _smart_pct(value: Any, *, ratio_aware: bool = False) -> str:
    number = _number(value)
    if number is None:
        return ""
    if ratio_aware and 0 <= abs(number) <= 1:
        number *= 100.0
    decimals = 0 if abs(number - round(number)) < 1e-9 else 1
    return f"{number:.{decimals}f}%".replace(".", ",")


def _global_price(value: Any) -> str:
    number = _number(value)
    if number is None:
        return ""
    decimals = 0 if abs(number) >= 1000 else 2
    rendered = f"{number:,.{decimals}f}"
    return rendered.replace(",", "X").replace(".", ",").replace("X", ".")


def _clean_items(value: Any, limit: int = 4) -> list[str]:
    if not _present(value):
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


def _human_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
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


def _regime_icon(regime: str) -> str:
    if "BULL" in regime:
        return "🟢"
    if "BEAR" in regime:
        return "🔴"
    return "🟡"


def _metric_line(icon: str, label: str, value: str) -> str:
    return f"{icon} {label:<10}: <b>{escape(value)}</b>"


def _normalise(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


_GLOBAL_SPECS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("brent", "🛢️", "Brent Oil", ("brentcrude", "brentcrudeoil", "bzf")),
    ("dow", "🇺🇸", "Dow Jones", ("dowjones", "dowjonesindustrialaverage", "dji")),
    ("dxy", "🇺🇸", "Dollar Index", ("dxy", "dollarindex", "usddollarindex", "dxynyb")),
    ("usdidr", "🇺🇸🇮🇩", "USD/IDR", ("usdidr", "idr=x", "idrx")),
    ("gold", "🥇", "Gold", ("gold", "gcf")),
    ("hangseng", "🇭🇰", "Hang Seng", ("hangseng", "hangsengindex", "hsi")),
    ("kospi", "🇰🇷", "KOSPI", ("kospi", "kospicompositeindex", "ks11")),
    ("nasdaq", "🇺🇸", "Nasdaq", ("nasdaq", "nasdaqcomposite", "ixic")),
    ("naturalgas", "🔥", "Natural Gas", ("naturalgas", "ngf")),
    ("nikkei", "🇯🇵", "Nikkei 225", ("nikkei225", "nikkei", "n225")),
)


def _instrument_tokens(item: Mapping[str, Any]) -> set[str]:
    values = (
        item.get("key"),
        item.get("instrument"),
        item.get("display_name"),
        item.get("name"),
        item.get("symbol"),
    )
    return {_normalise(value) for value in values if _present(value)}


def _find_global(instruments: list[Mapping[str, Any]], aliases: tuple[str, ...]) -> Mapping[str, Any] | None:
    wanted = {_normalise(alias) for alias in aliases}
    for item in instruments:
        tokens = _instrument_tokens(item)
        if tokens & wanted:
            return item
        if any(any(alias in token or token in alias for alias in wanted) for token in tokens):
            return item
    return None


def _global_rows(instruments: list[Mapping[str, Any]]) -> list[str]:
    selected: list[tuple[str, str, str, float]] = []
    for key, icon, display, aliases in _GLOBAL_SPECS:
        item = _find_global(instruments, aliases)
        if item is None:
            continue
        status = str(item.get("freshness_status") or "").upper()
        if status and status not in {"VALID", "DELAYED_ACCEPTED"}:
            continue
        close = _global_price(item.get("close"))
        change_value = _number(item.get("change_pct"))
        if not close or change_value is None:
            continue
        selected.append((key, icon, display, change_value))

    width = max((len(display) for _, _, display, _ in selected), default=0)
    result: list[str] = []
    for key, icon, display, change_value in selected:
        item = _find_global(instruments, next(spec[3] for spec in _GLOBAL_SPECS if spec[0] == key))
        if item is None:
            continue
        close = _global_price(item.get("close"))
        # USD/IDR is the exception agreed for an IHSG-centric report:
        # a rising pair means rupiah weakness, so the impact marker is red.
        if key == "usdidr":
            marker = "🔴" if change_value > 0 else "🟢" if change_value < 0 else "⚪"
        else:
            marker = "🟢" if change_value > 0 else "🔴" if change_value < 0 else "⚪"
        change = _pct(change_value, 2, signed=True)
        result.append(f"{icon} {display:<{width}} : {close}  {marker} <b>{escape(change)}</b>")
    return result


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
            f"🟢 Bias        : BULLISH — {execution}" if execution else "🟢 Bias        : BULLISH",
            "Agresif hanya pada setup berkualitas.",
            "Tidak semua saham ikut dibeli hanya karena market sedang bullish.",
        )
    if "BEAR" in regime:
        return (
            f"🔴 Bias        : BEARISH — {execution}" if execution else "🔴 Bias        : BEARISH",
            "Defensif dan kurangi agresivitas entry.",
            "Prioritaskan proteksi modal dan hindari memaksakan setup.",
        )
    return (
        f"🟡 Bias        : SELECTIVE — {execution}" if execution else "🟡 Bias        : SELECTIVE",
        "Tetap selektif dan tunggu konfirmasi yang lengkap.",
        "Hindari memaksakan entry saat struktur market belum dominan.",
    )


def format_market_outlook(data: dict[str, Any]) -> str:
    sentiment = data.get("global_sentiment") if isinstance(data.get("global_sentiment"), Mapping) else {}
    instruments = [item for item in data.get("global_instruments", []) or [] if isinstance(item, Mapping)]
    regime = _upper(data.get("market_regime"))
    execution = _upper(data.get("execution_mode"))
    tone = _upper(sentiment.get("sentiment_state") or data.get("global_tone"))
    coverage = data.get("global_coverage") if _present(data.get("global_coverage")) else data.get("coverage")
    created_at = data.get("snapshot_created_at") or data.get("created_at")

    lines = ["<b>🌅 SDE SWING — MARKET OUTLOOK</b>"]
    trade_date = _date(data.get("trade_date"), long=True)
    if trade_date:
        lines.append(f"📅 {escape(trade_date)}")
    snapshot_time = _time(created_at)
    if snapshot_time:
        lines.append(f"🕒 Snapshot diperbarui: {escape(snapshot_time)} WIB")

    condition_rows: list[str] = []
    if regime:
        condition_rows.append(_metric_line(_regime_icon(regime), "Regime", regime))
    if execution:
        condition_rows.append(_metric_line("🎯", "Execution", execution))
    if tone:
        global_value = tone
        coverage_text = _smart_pct(coverage, ratio_aware=True)
        if coverage_text:
            global_value += f" | Coverage {coverage_text}"
        condition_rows.append(_metric_line("🌍", "Global", global_value))
    ihsg_trend = _upper(data.get("ihsg_trend"))
    if ihsg_trend:
        condition_rows.append(_metric_line("📈", "IHSG", ihsg_trend))
    momentum = _upper(data.get("ihsg_momentum"))
    if momentum:
        condition_rows.append(_metric_line("⚡", "Momentum", momentum))
    breadth = _upper(data.get("breadth"))
    if breadth:
        condition_rows.append(_metric_line("📊", "Breadth", breadth))

    if condition_rows:
        lines += [SEPARATOR, "", "<b>📊 MARKET CONDITION</b>"]
        for row in condition_rows:
            lines += ["", row]
        reason = _human_reason(data.get("ihsg_reason") or data.get("reason"))
        if reason:
            lines += ["", escape(reason)]

    global_rows = _global_rows(instruments)
    if global_rows:
        lines += ["", SEPARATOR, "", "<b>🌍 GLOBAL MARKET</b>"]
        for row in global_rows:
            lines += ["", row]
        interpretation = str(data.get("global_interpretation") or "").strip()
        if not interpretation:
            interpretation = _global_interpretation(tone or "NEUTRAL", regime)
        if interpretation:
            lines += ["", "<b>📌 INTERPRETASI</b>", "", escape(interpretation)]

    sector_groups = [
        ("🔥 LEADING", data.get("leading", [])),
        ("🟢 ROTATING IN", data.get("rotating_in", [])),
        ("🟡 WEAKENING", data.get("weakening", [])),
        ("🔴 ROTATING OUT", data.get("rotating_out") or data.get("lagging", [])),
    ]
    available_groups = [(title, _clean_items(values, 4)) for title, values in sector_groups]
    available_groups = [(title, values) for title, values in available_groups if values]
    if available_groups:
        lines += ["", SEPARATOR, "", "<b>🔄 ROTASI SEKTOR</b>"]
        for title, values in available_groups:
            lines += ["", f"<b>{escape(title)}</b>", ""]
            lines.extend(f"• {escape(item)}" for item in values)

    lines += [
        "",
        SEPARATOR,
        "",
        "<b>🎯 TRADING PLAN</b>",
        "",
        "<b>Prioritas:</b>",
        "",
        "• Cari setup dari sektor <b>LEADING / ROTATING IN</b>.",
        "• Technical Quality dan Entry Readiness harus kuat.",
        "• Utamakan broker accumulation / confirmation.",
        "• BUY CANDIDATE tetap menunggu trigger.",
        "• Entry hanya di area yang sudah ditentukan.",
        "",
        "<b>⚠️ Hindari:</b>",
        "",
        "• Chase harga.",
        "• Setup dengan RR buruk.",
        "• Broker distribution kuat.",
        "• Saham sektor WEAKENING tanpa katalis kuat.",
    ]

    if regime or execution:
        lines += ["", SEPARATOR, "", "<b>📌 SDE BIAS BESOK</b>", ""]
        bias_head, bias_line_1, bias_line_2 = _bias_lines(regime, execution)
        lines += [f"<b>{escape(bias_head)}</b>", "", escape(bias_line_1), escape(bias_line_2)]

    data_status = _upper(data.get("global_market_status"))
    coverage_text = _smart_pct(coverage, ratio_aware=True)
    ihsg_date = _date(data.get("ihsg_data_date") or data.get("trade_date"))
    status_rows: list[str] = []
    if data_status:
        status_rows.append(_metric_line("✅", "Status", data_status))
    if coverage_text:
        status_rows.append(_metric_line("🌍", "Global", f"Coverage {coverage_text}"))
    if ihsg_date:
        status_rows.append(_metric_line("📈", "IHSG", ihsg_date))
    if status_rows:
        lines += ["", "<b>📡 DATA STATUS</b>"]
        for row in status_rows:
            lines += ["", row]

    lines += [
        "",
        "⚠️ Sentimen global adalah konteks Market Outlook dan tidak mengubah scoring saham secara langsung.",
    ]
    return "\n".join(line for line in lines if line is not None).strip()
