from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


def _label(value: Any, fallback: str = "DATA_NOT_AVAILABLE") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").title() if text else fallback


def _pct(value: Any, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}%".replace(".", ",")
    except Exception:
        return "DATA_NOT_AVAILABLE"


def _signed_pct(value: Any, decimals: int = 2) -> str:
    try:
        number = float(value)
        return f"{number:+.{decimals}f}%".replace(".", ",")
    except Exception:
        return "DATA_NOT_AVAILABLE"


def _price(value: Any) -> str:
    try:
        return f"{float(value):,.0f}".replace(",", ".")
    except Exception:
        return "DATA_NOT_AVAILABLE"


def _date(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value)).date()
    except Exception:
        parsed = value if isinstance(value, date) else date.today()
    months = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    return f"{parsed.day:02d} {months[parsed.month]} {parsed.year}"


def _items(lines: Iterable[str]) -> str:
    values = [str(item).strip() for item in lines if str(item).strip()]
    return "\n".join(f"• {item}" for item in values) if values else "• Tidak ada"


def format_market_outlook(data: dict[str, Any]) -> str:
    blocks = [
        "🌐 SDE MARKET OUTLOOK",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | POST MARKET",
        "",
        f"{data.get('regime_icon', '🟡')} MARKET REGIME",
        str(data.get("market_regime", "UNKNOWN")).replace("_", " "),
        "",
        "🎯 EXECUTION MODE",
        str(data.get("execution_mode", "SELECTIVE")).replace("_", " "),
        "",
        "📊 IHSG",
        f"• Change    : {_signed_pct(data.get('ihsg_change'))}",
        f"• Trend     : {_label(data.get('ihsg_trend'))}",
        f"• Momentum  : {_label(data.get('ihsg_momentum'))}",
        f"• Breadth   : {_label(data.get('breadth'))}",
        "",
        "🔄 ROTASI SEKTOR",
    ]
    categories = [
        ("🟢 Rotating In", data.get("rotating_in", [])),
        ("🔥 Leading", data.get("leading", [])),
        ("🟡 Weakening", data.get("weakening", [])),
        ("🔴 Rotating Out", data.get("rotating_out", [])),
    ]
    for title, values in categories:
        cleaned = [str(item).strip() for item in values or [] if str(item).strip()]
        if cleaned:
            blocks.extend([title, _items(cleaned), ""])
    blocks.extend([
        "🎯 FOKUS BESOK",
        str(data.get("focus_tomorrow") or "Prioritaskan saham dengan setup valid dan broker flow mendukung."),
        "",
        "⚠️ HINDARI",
        str(data.get("avoid_guidance") or "Hindari mengejar harga di luar area entry."),
        "",
        f"📡 {data.get('provider', 'NOT_CONFIGURED')} | {data.get('source_mode', 'NOT_CONFIGURED')} | Coverage {_pct(data.get('coverage'))}",
    ])
    return "\n".join(blocks).strip()


def format_post_market(data: dict[str, Any]) -> str:
    status = str(data.get("process_status", "PARTIAL")).upper()
    icon = {"SUCCESS": "✅", "PARTIAL": "🟡", "FAILED": "🔴"}.get(status, "🟡")
    return "\n".join([
        "📊 SDE POST MARKET",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | {data.get('finished_time', '--:-- WIB')}",
        "",
        f"{icon} PROCESS STATUS",
        status,
        "",
        "📦 DATA COVERAGE",
        f"• Universe   : {data.get('symbols_requested', 0)} saham",
        f"• Loaded     : {data.get('symbols_loaded', 0)}",
        f"• Valid      : {data.get('symbols_valid', 0)}",
        f"• Failed     : {data.get('symbols_failed', 0)}",
        f"• Skipped    : {data.get('symbols_skipped', 0)}",
        f"• Coverage   : {_pct(data.get('coverage'))}",
        "",
        "📈 MARKET PROCESS",
        f"• Technical Snapshot : {data.get('technical_status', 'READY')}",
        f"• Universe Selection : {data.get('universe_status', 'READY')}",
        f"• Candidate Screening: {data.get('candidate_status', 'READY')}",
        f"• Broker Dependency  : {data.get('broker_status', 'READY')}",
        "",
        "⚠️ DATA NOTE",
        str(data.get("data_note") or "Tidak ada catatan tambahan."),
        "",
        "🎯 NEXT PROCESS",
        str(data.get("next_process") or "Data siap digunakan untuk Final Watchlist."),
        "",
        f"📡 {data.get('provider', 'NOT_CONFIGURED')} | {data.get('source_mode', 'NOT_CONFIGURED')} | Coverage {_pct(data.get('coverage'))}",
    ])


def format_broker_summary(data: dict[str, Any]) -> str:
    top_acc = data.get("top_accumulation", []) or []
    top_dist = data.get("top_distribution", []) or []
    acc_lines = [f"{idx}. {row.get('symbol')} — {_label(row.get('state'))}" for idx, row in enumerate(top_acc[:3], 1)]
    dist_lines = [f"{idx}. {row.get('symbol')} — {_label(row.get('state'))}" for idx, row in enumerate(top_dist[:3], 1)]
    return "\n".join([
        "🏦 SDE BROKER SUMMARY",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | POST MARKET",
        "",
        f"{'✅' if str(data.get('process_status')).upper() == 'SUCCESS' else '🟡'} PROCESS STATUS",
        str(data.get("process_status", "PARTIAL")).upper(),
        "",
        "📊 BROKER FLOW",
        f"• Accumulation : {data.get('accumulation_count', 0)} saham",
        f"• Neutral      : {data.get('neutral_count', 0)} saham",
        f"• Distribution : {data.get('distribution_count', 0)} saham",
        f"• No Data      : {data.get('no_data_count', 0)} saham",
        "",
        "🔥 TOP ACCUMULATION",
        "\n".join(acc_lines) if acc_lines else "Tidak ada",
        "",
        "🔴 TOP DISTRIBUTION",
        "\n".join(dist_lines) if dist_lines else "Tidak ada",
        "",
        "📎 CSV ringkasan broker terlampir.",
        "",
        f"📡 {data.get('provider', 'NOT_CONFIGURED')} | {data.get('source_mode', 'NOT_CONFIGURED')} | Coverage {_pct(data.get('coverage'))}",
    ])


def format_broker_multiday(data: dict[str, Any]) -> str:
    def rows(items: list[dict[str, Any]]) -> str:
        output = []
        for idx, row in enumerate(items[:3], 1):
            output.append(
                f"{idx}. {row.get('symbol')} — {_label(row.get('state_1d'))} | "
                f"{_label(row.get('state_3d'))} | {_label(row.get('state_5d'))}"
            )
        return "\n".join(output) if output else "Tidak ada"

    status = str(data.get("process_status", "PARTIAL")).upper()
    return "\n".join([
        "📚 SDE BROKER MULTI-DAY",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | POST MARKET",
        "",
        f"{'✅' if status == 'SUCCESS' else '🟡'} {status}",
        "",
        "🔥 Akumulasi Konsisten",
        rows(data.get("top_accumulation", []) or []),
        "",
        "🔴 Distribusi Konsisten",
        rows(data.get("top_distribution", []) or []),
        "",
        "📎 CSV multi-day terlampir.",
        f"📡 {data.get('provider', 'NOT_CONFIGURED')} | {data.get('source_mode', 'NOT_CONFIGURED')} | Coverage {_pct(data.get('coverage'))}",
    ])


def format_watchlist_detail(data: dict[str, Any]) -> str:
    decision = str(data.get("decision", "WATCH")).upper().replace("_", " ")
    icon = {
        "BUY": "🟢", "BUY CONFIRMED": "🟢", "BUY CANDIDATE": "🟠",
        "WATCH HIGH": "🟡", "WATCH": "🔵", "WAIT": "🟠", "AVOID": "🔴",
    }.get(decision, "🔵")
    entry = f"{_price(data.get('entry_low'))}–{_price(data.get('entry_high'))}"
    rr = data.get("risk_reward")
    rr_text = f"1:{str(rr).replace('.', ',')}" if rr not in (None, "") else "DATA_NOT_AVAILABLE"
    return "\n".join([
        f"📌 {str(data.get('symbol', '?')).upper()} | {icon} {decision} | {int(float(data.get('confidence', 0)))}%",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | POST MARKET",
        "",
        "📊 SETUP",
        _label(data.get("setup")),
        "",
        "💰 TRADE PLAN",
        f"• Entry : {entry}",
        f"• Stop  : {_price(data.get('stop_loss'))}",
        f"• TP1   : {_price(data.get('target_1'))}",
        f"• TP2   : {_price(data.get('target_2'))}",
        f"• R:R   : {rr_text}",
        "",
        "📈 KONFIRMASI",
        f"• Technical : {_label(data.get('technical_state'))}",
        f"• Broker    : {_label(data.get('broker_state'))}",
        f"• Sector    : {_label(data.get('sector_state'))}",
        f"• Market    : {_label(data.get('market_regime'))}",
        "",
        "🧠 ALASAN",
        str(data.get("main_reason") or "Menunggu interpretasi data yang valid."),
        "",
        "⚠️ RISIKO",
        str(data.get("main_risk") or "Disiplin terhadap level invalidasi dan stop loss."),
        "",
        f"📡 {data.get('provider', 'NOT_CONFIGURED')} | Coverage {_pct(data.get('coverage'))}",
    ])
