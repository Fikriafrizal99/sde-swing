from __future__ import annotations

import html
from datetime import date
from typing import Any, Mapping


def _date_label(value: Any) -> str:
    text = str(value or "").strip()[:10]
    try:
        parsed = date.fromisoformat(text)
    except Exception:
        return text or "N/A"
    months = (
        "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    )
    return f"{parsed.day} {months[parsed.month - 1]} {parsed.year}"


def format_watchlist_ai_interpretation(result: Mapping[str, Any]) -> str:
    symbol = html.escape(str(result.get("symbol") or "N/A").strip().upper(), quote=False)
    trade_date = html.escape(_date_label(result.get("trade_date")), quote=False)
    decision = html.escape(str(result.get("decision") or "N/A").strip().upper(), quote=False)
    analysis = html.escape(str(result.get("analysis") or "").strip(), quote=False)
    conclusion = html.escape(str(result.get("conclusion") or "").strip(), quote=False)
    return (
        f"🤖 <b>AI VIEW — {symbol}</b>\n"
        f"📅 {trade_date} | SDE: <b>{decision}</b>\n\n"
        f"{analysis}\n\n"
        f"<b>Kesimpulan:</b> {conclusion}"
    ).strip()


def format_watchlist_ai_failure(
    *,
    trade_date: str,
    symbols: list[str],
    reason: str = "",
) -> str:
    date_label = html.escape(_date_label(trade_date), quote=False)
    listed = ", ".join(html.escape(str(item).upper(), quote=False) for item in symbols if str(item).strip())
    if not listed:
        listed = "N/A"
    reason_text = html.escape(str(reason or "Semua provider AI gagal menghasilkan interpretasi valid.").strip(), quote=False)
    return (
        "⚠️ <b>WATCHLIST AI — INFORMASI</b>\n"
        f"📅 {date_label}\n\n"
        f"Interpretasi AI gagal untuk: <b>{listed}</b>.\n"
        f"{reason_text}\n\n"
        "Final Watchlist resmi tetap berhasil dan tidak ada keputusan/level SDE yang diubah."
    ).strip()


__all__ = [
    "format_watchlist_ai_interpretation",
    "format_watchlist_ai_failure",
]
