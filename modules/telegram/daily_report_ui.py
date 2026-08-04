from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"


def _label(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ").title() if text else fallback


def _pct(value: Any, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}%".replace(".", ",")
    except Exception:
        return ""


def _signed_pct(value: Any, decimals: int = 2) -> str:
    try:
        number = float(value)
        return f"{number:+.{decimals}f}%".replace(".", ",")
    except Exception:
        return ""


def _price(value: Any) -> str:
    try:
        return f"{float(value):,.0f}".replace(",", ".")
    except Exception:
        return ""


def _date(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value)).date()
    except Exception:
        return ""
    months = ["", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    return f"{parsed.day:02d} {months[parsed.month]} {parsed.year}"


def _items(lines: Iterable[str]) -> str:
    values = [str(item).strip() for item in lines if str(item).strip()]
    return "\n".join(f"• {item}" for item in values) if values else "• Tidak ada"


def _source_block(data: dict[str, Any], *, detail: bool = False) -> str:
    yahoo = data.get("yahoo_status") or data.get("historical_status") or ""
    zapi = data.get("zapi_status") or data.get("reconciliation_status") or ""
    broker = data.get("broker_status") or data.get("stockbit_status") or ""
    lines = ["", "SOURCE PROVENANCE"]
    if yahoo:
        lines.append(f"- Yahoo: {yahoo}")
    if zapi:
        lines.append(f"- ZAPI IDX: {zapi}")
    if broker:
        lines.append(f"- Stockbit: {broker}")
    if data.get("zapi_coverage") not in (None, ""):
        value = float(data.get("zapi_coverage"))
        lines.append(f"- ZAPI coverage: {_pct(value * 100 if value <= 1 else value)}")
    if detail and data.get("zapi_freshness_days") not in (None, ""):
        lines.append(f"- Freshness: {data.get('zapi_freshness_days')} day(s)")
    if data.get("degraded_reason"):
        lines.append(f"- Degraded: {data.get('degraded_reason')}")
    return "\n".join(lines) if len(lines) > 2 else ""


def format_market_outlook(data: dict[str, Any]) -> str:
    blocks = [
        "🌐 SDE MARKET OUTLOOK",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | POST MARKET",
        "",
        f"{data.get('regime_icon') or ''} MARKET REGIME",
        str(data.get("market_regime", "")).replace("_", " "),
        "",
        "🎯 EXECUTION MODE",
        str(data.get("execution_mode", "")).replace("_", " "),
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
        str(data.get("focus_tomorrow") or ""),
        "",
        "⚠️ HINDARI",
        str(data.get("avoid_guidance") or ""),
        "",
        f"📡 {data.get('provider', '')} | {data.get('source_mode', '')} | Coverage {_pct(data.get('coverage'))}",
    ])
    return "\n".join(blocks).strip()


def format_post_market(data: dict[str, Any]) -> str:
    status = str(data.get("process_status") or "").upper()
    icon = {"SUCCESS": "✅", "PARTIAL": "🟡", "FAILED": "🔴"}.get(status, "")
    message = "\n".join([
        "📊 SDE POST MARKET",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | {data.get('finished_time') or ''}",
        "",
        f"{icon} PROCESS STATUS",
        status,
        "",
        "📦 DATA COVERAGE",
        f"• Universe   : {data.get('symbols_requested')} saham",
        f"• Loaded     : {data.get('symbols_loaded')}",
        f"• Valid      : {data.get('symbols_valid')}",
        f"• Failed     : {data.get('symbols_failed')}",
        f"• Skipped    : {data.get('symbols_skipped')}",
        f"• Coverage   : {_pct(data.get('coverage'))}",
        "",
        "📈 MARKET PROCESS",
        f"• Technical Snapshot : {data.get('technical_status') or ''}",
        f"• Universe Selection : {data.get('universe_status') or ''}",
        f"• Candidate Screening: {data.get('candidate_status') or ''}",
        f"• Broker Dependency  : {data.get('broker_status') or ''}",
        "",
        "⚠️ DATA NOTE",
        str(data.get("data_note") or ""),
        "",
        "🎯 NEXT PROCESS",
        str(data.get("next_process") or ""),
        "",
        f"📡 {data.get('provider', '')} | {data.get('source_mode', '')} | Coverage {_pct(data.get('coverage'))}",
    ])
    return message + _source_block(data)


def format_broker_summary(data: dict[str, Any]) -> str:
    top_acc = data.get("top_accumulation", []) or []
    top_dist = data.get("top_distribution", []) or []
    acc_lines = [
        f"{idx}. {row.get('symbol')} — {_label(row.get('broker_state', row.get('state')))}"
        for idx, row in enumerate(top_acc[:3], 1)
    ]
    dist_lines = [
        f"{idx}. {row.get('symbol')} — {_label(row.get('broker_state', row.get('state')))}"
        for idx, row in enumerate(top_dist[:3], 1)
    ]
    return "\n".join([
        "🏦 SDE BROKER SUMMARY",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | POST MARKET",
        "",
        f"{'✅' if str(data.get('process_status') or '').upper() == 'SUCCESS' else ''} PROCESS STATUS",
        str(data.get("process_status") or "").upper(),
        "",
        "📊 BROKER FLOW",
        f"• Accumulation : {data.get('accumulation_count')} saham",
        f"• Neutral      : {data.get('neutral_count')} saham",
        f"• Distribution : {data.get('distribution_count')} saham",
        f"• No Data      : {data.get('no_data_count')} saham",
        "",
        "🔥 TOP ACCUMULATION",
        "\n".join(acc_lines) if acc_lines else "Tidak ada",
        "",
        "🔴 TOP DISTRIBUTION",
        "\n".join(dist_lines) if dist_lines else "Tidak ada",
        "",
        "📎 CSV ringkasan broker terlampir.",
        "",
        f"📡 {data.get('provider', '')} | {data.get('source_mode', '')} | Coverage {_pct(data.get('coverage'))}",
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

    status = str(data.get("process_status") or "").upper()
    return "\n".join([
        "📚 SDE BROKER MULTI-DAY",
        SEPARATOR,
        f"📅 {_date(data.get('trade_date'))} | POST MARKET",
        "",
        f"{'✅' if status == 'SUCCESS' else ''} {status}",
        "",
        "🔥 Akumulasi Konsisten",
        rows(data.get("top_accumulation", []) or []),
        "",
        "🔴 Distribusi Konsisten",
        rows(data.get("top_distribution", []) or []),
        "",
        "📎 CSV multi-day terlampir.",
        f"📡 {data.get('provider', '')} | {data.get('source_mode', '')} | Coverage {_pct(data.get('coverage'))}",
    ])


def format_watchlist_detail(data: dict[str, Any]) -> str:
    decision = str(data.get("decision", "")).upper().replace("_", " ")
    icon = {
        "BUY": "🟢", "BUY READY": "🟢", "BUY CONFIRMED": "🟢", "BUY ON TRIGGER": "🟠", "BUY CANDIDATE": "🟠",
        "WATCH HIGH": "🟡", "WATCH": "🔵", "WAIT": "🟠", "AVOID": "🔴",
    }.get(decision, "")
    entry = f"{_price(data.get('entry_low'))}–{_price(data.get('entry_high'))}"
    rr = data.get("risk_reward")
    rr_text = f"1:{str(rr).replace('.', ',')}" if rr not in (None, "") else ""
    message = "\n".join([
        f"📌 {str(data.get('symbol', '')).upper()} | {icon} {decision} | {data.get('confidence', '')}%",
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
        str(data.get("main_reason") or ""),
        "",
        "⚠️ RISIKO",
        str(data.get("main_risk") or ""),
        "",
        f"📡 {data.get('provider', '')} | Coverage {_pct(data.get('coverage'))}",
    ])
    return message + _source_block(data, detail=True)
