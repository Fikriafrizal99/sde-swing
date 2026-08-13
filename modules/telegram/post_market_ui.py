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
    short = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    if not long:
        return f"{parsed.day:02d} {short[parsed.month]} {parsed.year}"
    days = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    months = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{days[parsed.weekday()]}, {parsed.day} {months[parsed.month]} {parsed.year}"


def _time(value: Any) -> str:
    parsed = _dt(value)
    return parsed.strftime("%H:%M") if parsed else ""


def _upper(value: Any) -> str:
    return str(value or "").strip().replace("_", " ").upper() if _present(value) else ""


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _int(value: Any) -> int:
    number = _number(value)
    return max(0, int(number)) if number is not None else 0


def _int_text(value: Any) -> str:
    return f"{_int(value):,}".replace(",", ".")


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
        number *= 100
    return _pct(number, decimals=1)


def _counts(data: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        _int(data.get("technical_bullish_count", data.get("bullish_count", 0))),
        _int(data.get("technical_neutral_count", data.get("neutral_count", 0))),
        _int(data.get("technical_bearish_count", data.get("bearish_count", 0))),
    )


def _direction(data: Mapping[str, Any]) -> tuple[float | None, float | None]:
    bullish, _neutral, bearish = _counts(data)
    total = bullish + bearish
    if total <= 0:
        return None, None
    return bullish * 100 / total, bearish * 100 / total


def _breadth(data: Mapping[str, Any]) -> str:
    bullish, bearish = _direction(data)
    if bullish is None or bearish is None:
        return _upper(data.get("breadth")) or "INSUFFICIENT DATA"
    if bullish >= 60:
        return "BULLISH DOMINANT"
    if bearish >= 60:
        return "BEARISH DOMINANT"
    return "MIXED"


def _market(data: Mapping[str, Any]) -> str:
    regime = _upper(data.get("market_regime"))
    breadth = _breadth(data)
    if "NOT CURRENT" in regime:
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


def _pulse_bar(bullish: float | None, width: int = 20) -> str:
    if bullish is None:
        return ""
    green = max(0, min(width, int(round(width * bullish / 100))))
    return "🟢" * green + "🔴" * (width - green)


def _setup_items(data: Mapping[str, Any]) -> list[tuple[str, int]]:
    raw = data.get("setup_distribution")
    if not isinstance(raw, Mapping):
        return []
    output: list[tuple[str, int]] = []
    for key, value in raw.items():
        count = _int(value)
        if count:
            output.append((str(key).replace("_", " ").upper(), count))
    return sorted(output, key=lambda item: item[1], reverse=True)[:5]


def _sectors(data: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("leading", "rotating_in"):
        raw = data.get(key)
        if isinstance(raw, (list, tuple)):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return list(dict.fromkeys(values))[:5]


def _join(lines: list[str]) -> str:
    result: list[str] = []
    for line in lines:
        if line == "" and (not result or result[-1] == ""):
            continue
        result.append(line)
    return "\n".join(result).strip()


def _ihsg_lines(data: Mapping[str, Any]) -> list[str]:
    status = _upper(data.get("ihsg_status"))
    change = _pct(data.get("ihsg_change"), signed=True, decimals=2)
    data_date = str(data.get("ihsg_data_date") or "")[:10]
    if status in {"CURRENT SESSION", "CURRENT"} and change:
        return [f"IHSG        : <b>{escape(change)}</b>"]
    if change and not status:
        return [f"IHSG        : <b>{escape(change)}</b>"]
    line = "IHSG        : <b>DATA SESI TERKINI TIDAK TERSEDIA</b>"
    session = status or "NOT CURRENT SESSION"
    detail = f"IHSG session : <b>{escape(session)}</b>"
    if data_date:
        detail += f" ({escape(data_date)})"
    return [line, detail]


def _guidance(data: Mapping[str, Any]) -> list[str]:
    market = _market(data)
    if market == "RISK-OFF":
        return ["Pasar defensif; prioritaskan proteksi modal dan setup paling kuat.", "Hindari entry agresif sebelum tekanan pasar mereda."]
    if market == "RISK-ON":
        return ["Pasar positif; fokus pada setup matang dengan broker confirmation mendukung.", "Hindari mengejar harga yang sudah jauh dari area entry."]
    return ["Pasar selektif; fokus pada setup matang dan konfirmasi yang lengkap.", "Hindari mengejar harga yang sudah jauh dari area entry."]


def _format_current_v2(data: dict[str, Any]) -> str:
    funnel = data.get("candidate_funnel") if isinstance(data.get("candidate_funnel"), Mapping) else {}
    source = data.get("source_status") if isinstance(data.get("source_status"), Mapping) else {}
    top = data.get("top_screening_watchlist") if isinstance(data.get("top_screening_watchlist"), (list, tuple)) else []
    sectors = _sectors(data)
    ihsg_status = _upper(data.get("ihsg_status"))
    ihsg_date = str(data.get("ihsg_data_date") or "")[:10]
    ihsg_change = _pct(data.get("ihsg_change"), signed=True, decimals=2)
    ihsg = ihsg_change if ihsg_status in {"CURRENT SESSION", "CURRENT"} and ihsg_change else f"NOT CURRENT ({ihsg_date or 'UNKNOWN'})"

    lines = [
        "<b>🌆 SDE SWING — POST MARKET</b>",
        SEPARATOR,
        "<b>⚙️ PROCESS STATUS</b>",
        f"Status: <b>{escape(_upper(data.get('process_status')) or 'UNKNOWN')}</b>",
        "",
        "<b>📊 MARKET SUMMARY</b>",
        f"IHSG: <b>{escape(ihsg)}</b>",
        f"Regime: <b>{escape(_upper(data.get('market_regime')) or 'UNKNOWN')}</b>",
        "",
        "<b>🔥 SECTOR BIAS</b>",
        *(f"• {escape(item)}" for item in sectors),
        "",
        "<b>🧪 DATA QUALITY</b>",
        f"Technical: <b>{escape(_upper(data.get('technical_status')) or 'UNKNOWN')}</b>",
        f"Coverage: <b>{escape(_coverage(data.get('coverage')) or 'N/A')}</b>",
        "",
        "<b>🔎 CANDIDATE FUNNEL</b>",
        f"Technical rows: <b>{_int_text(funnel.get('technical_rows'))}</b>",
        f"Candidate rows: <b>{_int_text(funnel.get('candidate_rows'))}</b>",
        f"Pass/Ready/Developing/Avoid: <b>{_int_text(funnel.get('pass_rows'))}/{_int_text(funnel.get('ready_rows'))}/{_int_text(funnel.get('developing_rows'))}/{_int_text(funnel.get('avoid_rows'))}</b>",
        "",
        "<b>🧱 FILTER DOMINAN</b>",
        escape(str(data.get("dominant_filter_reason") or "N/A").replace("_", " ")),
        "",
        "<b>✅ SCREENING RESULT</b>",
        f"Status: <b>{escape(_upper(data.get('screening_result')) or 'UNKNOWN')}</b>",
        "<b>POST MARKET TOP WATCHLIST (MAX 5)</b>",
    ]
    for index, item in enumerate(top[:5], start=1):
        if not isinstance(item, Mapping):
            continue
        symbol = escape(str(item.get("symbol") or "-").upper())
        setup = escape(str(item.get("setup") or "-").replace("_", " ").upper())
        score = item.get("score")
        readiness = item.get("entry_readiness")
        score_text = f" | Score {float(score):.0f}" if _number(score) is not None else ""
        ready_text = f" | Entry {float(readiness):.0f}" if _number(readiness) is not None else ""
        lines.append(f"{index}. <b>{symbol}</b> | {setup}{score_text}{ready_text}")
    lines += [
        "",
        "<b>🚦 PIPELINE STATUS</b>",
        f"{escape(_upper(data.get('pipeline_status')) or 'UNKNOWN')}",
        "",
        "<b>📡 SOURCE STATUS</b>",
    ]
    for key, value in source.items():
        lines.append(f"{escape(str(key).replace('_', ' ').upper())}: <b>{escape(_upper(value) or 'UNKNOWN')}</b>")
    lines += [
        "",
        "<b>🎯 NEXT PROCESS</b>",
        "Final Watchlist menentukan kandidat prioritas, broker confirmation, Entry, SL, TP, serta keputusan final.",
    ]
    return _join(lines)


def format_post_market(data: dict[str, Any]) -> str:
    if _upper(data.get("post_market_report_version")) == "CURRENT V2":
        return _format_current_v2(data)

    bullish, neutral, bearish = _counts(data)
    bull_pct, bear_pct = _direction(data)
    lines = ["<b>🌆 SDE SWING — POST MARKET</b>"]
    trade_date = _date(data.get("trade_date"), long=True)
    if trade_date:
        lines.append(f"📅 {escape(trade_date)}")
    finished = _time(data.get("finished_at") or data.get("generated_at") or data.get("completed_at"))
    if finished:
        lines.append(f"🕒 {escape(finished)} WIB")
    lines += [SEPARATOR, "", "<b>📊 MARKET PULSE</b>"]
    if bull_pct is not None and bear_pct is not None:
        lines.append(f"🟢 BULLISH <b>{bull_pct:.0f}%</b>  vs  🔴 BEARISH <b>{bear_pct:.0f}%</b>")
        bar = _pulse_bar(bull_pct)
        if bar:
            lines.append(bar)
        lines.append(f"Neutral: <b>{_int_text(neutral)} saham</b>")
    else:
        lines.append("Arah teknikal: <b>DATA BELUM CUKUP</b>")
    lines += _ihsg_lines(data)
    lines += [
        f"Market      : <b>{escape(_market(data))}</b>",
        f"Breadth     : <b>{escape(_breadth(data))}</b>",
    ]

    sectors = _sectors(data)
    if sectors:
        lines += ["", "<b>🔥 Sektor kuat</b>", *(f"• {escape(item)}" for item in sectors)]

    lines += ["", "<b>📈 TECHNICAL BREADTH</b>", f"✅ Valid       : <b>{_int_text(data.get('symbols_valid'))} saham</b>"]
    if bullish + neutral + bearish:
        lines += [
            f"🟢 Bullish     : <b>{_int_text(bullish)}</b>",
            f"🟡 Neutral     : <b>{_int_text(neutral)}</b>",
            f"🔴 Bearish     : <b>{_int_text(bearish)}</b>",
        ]
    setups = _setup_items(data)
    if setups:
        lines += ["", "<b>🔥 SETUP DISTRIBUTION</b>", *(f"• {escape(label)}: <b>{count}</b>" for label, count in setups)]

    lines += ["", "<b>🧭 ARAHAN BESOK</b>", *_guidance(data)]

    broker = _upper(data.get("broker_status") or data.get("stockbit_status"))
    if broker:
        lines += ["", "<b>🏦 BROKER STATUS</b>", f"Stockbit     : <b>{escape(broker)}</b>"]

    requested = _int(data.get("symbols_requested"))
    loaded = _int(data.get("symbols_loaded"))
    valid = _int(data.get("symbols_valid"))
    issue_count = max(0, requested - loaded) + max(0, loaded - valid)
    if not issue_count and _present(data.get("symbols_failed")):
        issue_count = _int(data.get("symbols_failed")) * 2
    coverage = _coverage(data.get("coverage"))
    lines += ["", "<b>📦 SYSTEM HEALTH</b>"]
    if coverage:
        lines.append(f"Coverage     : <b>{escape(coverage)}</b>")
    if requested or loaded or valid or _present(data.get("symbols_failed")):
        lines.append(f"Data issue   : <b>{issue_count} saham</b>")
        impact = _upper(data.get("data_impact"))
        if not impact:
            coverage_num = _number(data.get("coverage")) or 0
            if 0 <= coverage_num <= 1:
                coverage_num *= 100
            impact = "TIDAK MATERIAL" if coverage_num >= 98 else "MATERIAL"
        lines.append(f"Impact       : <b>{escape(impact)}</b>")
    historical = _upper(data.get("historical_status"))
    if historical:
        lines.append(f"Yahoo        : <b>{escape(historical)}</b>")
    technical = _upper(data.get("technical_status"))
    if technical:
        lines.append(f"Technical    : <b>{escape(technical)}</b>")
    screening = _upper(data.get("screening_result") or data.get("candidate_status"))
    if screening:
        lines.append(f"Screening    : <b>{escape(screening)}</b>")

    # Compatibility diagnostics are emitted only for diagnostic payloads that
    # do not carry the normal market-first context. Normal Post Market UI hides ZAPI.
    diagnostic_mode = not _present(data.get("trade_date")) and not _present(data.get("market_regime"))
    if diagnostic_mode:
        if historical:
            lines.append(f"Yahoo Technical : <b>{escape(historical)}</b>")
        zapi = _upper(data.get("zapi_status"))
        if zapi:
            lines.append(f"ZAPI IDX         : <b>{escape(zapi)}</b>")

    lines += [
        "",
        "<b>🎯 NEXT — FINAL WATCHLIST</b>",
        "Final Watchlist menentukan kandidat prioritas, broker confirmation, Entry, SL, TP, serta keputusan final.",
        "Post Market hanya menggambarkan kondisi pasar setelah penutupan.",
    ]
    if _present(data.get("run_id")):
        lines += ["", f"<code>{escape(str(data['run_id']))}</code>"]
    return _join(lines)


__all__ = ["format_post_market"]
