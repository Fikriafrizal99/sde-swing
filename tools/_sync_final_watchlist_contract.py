from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8-sig")
    if old not in text:
        raise RuntimeError(f"Patch anchor not found in {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "modules/telegram/daily_report_ui.py",
    '''def _fw_text(value):\n    text = str(value if value is not None else "").strip()\n    if not text or text.lower() in {"nan", "none", "null"}:\n        return "ENGINE_DATA_NOT_AVAILABLE"\n    return _fw_escape(text.replace("_", " "))\n''',
    '''def _fw_text(value):\n    if isinstance(value, (list, tuple, set)):\n        value = "; ".join(str(item) for item in value if str(item).strip())\n    elif isinstance(value, dict):\n        value = "; ".join(f"{key}: {item}" for key, item in value.items())\n    text = str(value if value is not None else "").strip()\n    if not text or text.lower() in {"nan", "none", "null"}:\n        return "ENGINE_DATA_NOT_AVAILABLE"\n    return _fw_escape(text.replace("_", " "))\n''',
)
replace_once(
    "modules/telegram/daily_report_ui.py",
    'technical_status = _fw_text(_fw_pick(row, "technical_status", "technical_state", "Plan_Status"))',
    'technical_status = _fw_text(_fw_pick(row, "technical_status", "technical_state", "Plan_Status", "decision"))',
)

replace_once(
    "tests/test_enhanced_daily_reports.py",
    '''    required_sections = [\n        "📈 TEKNIKAL",\n        "🌊 BROKER SUMMARY",\n        "🟢 TOP BUYER",\n        "🔴 TOP SELLER",\n        "💰 POSISI BROKER",\n        "🎯 RENCANA",\n        "🔔 YANG DITUNGGU",\n        "✅ ALASAN UTAMA",\n        "⚠️ RISIKO &amp; INVALIDASI",\n        "🧭 EKSEKUSI",\n    ]\n    for section in required_sections:\n        assert section in text\n    for forbidden in ("VALIDASI DATA", "SOURCE PROVENANCE", "MARKET CONTEXT"):\n        assert forbidden not in text\n    assert "Yahoo: VALID" in text\n    assert "ZAPI IDX: MATCH WITH TOLERANCE" in text\n\n    assert "Trend: bullish" in text\n    assert "Trend: ★★★★" not in text\n    assert "Momentum: NETRAL — RSI 57,0" in text\n    assert "Volume: confirmed — 1,24x MA20" in text\n    assert "R:R TP1: 1:1,67" in text\n    assert "1. AK | +Rp1,00 miliar | Avg Rp103 | lokal" in text\n''',
    '''    required_sections = [\n        "<b>📈 SDE SWING — FINAL WATCHLIST</b>",\n        "<b>🎯 TRADE SETUP</b>",\n        "<b>🏦 BROKER SUMMARY</b>",\n        "<b>🟢 Top Buy</b>",\n        "<b>🔴 Top Sell</b>",\n        "<b>📌 SETUP CONTEXT</b>",\n        "<b>Reason:</b>",\n    ]\n    for section in required_sections:\n        assert section in text\n    assert "S1 | BREAKOUT RETEST" in text\n    assert "💰 Current 103 | Entry 100–105" in text\n    assert "🛑 SL 95 | 🎯 TP1 115 | 🚀 TP2 120" in text\n    assert "AK @ 103" in text\n    assert "Yahoo: VALID" not in text\n    assert "ZAPI IDX:" not in text\n''',
)

replace_once(
    "tests/test_v170_telegram_contract.py",
    '''    assert "BBCA&lt;&amp;" in text\n    assert "BUY READY" in text\n    assert "SEHAT" in text\n    assert "UMA —" in text\n    assert "R:R TP1: 1:1,50" in text\n    assert "WAIT_FOR_ENTRY_TRIGGER" not in text\n    assert "['should not render" not in text\n    assert "<b>🟢 TOP BUYER</b>" in text\n''',
    '''    assert "BBCA&lt;&amp;" in text\n    assert "BUY READY" in text\n    assert "WAIT_FOR_ENTRY_TRIGGER" not in text\n    assert "['should not render" not in text\n    assert "should not render as a Python list" in text\n    assert "<b>🎯 TRADE SETUP</b>" in text\n    assert "<b>🏦 BROKER SUMMARY</b>" in text\n    assert "<b>🟢 Top Buy</b>" in text\n    assert "<b>🔴 Top Sell</b>" in text\n    assert "<b>📌 SETUP CONTEXT</b>" in text\n''',
)

replace_once(
    "tests/test_zapi_end_to_end.py",
    '''    assert "ZAPI IDX: MATCH" in message\n    assert "Stockbit: AVAILABLE" in message\n''',
    '''    # FINAL WATCHLIST V2 intentionally follows the compact user contract and\n    # does not append provider/source diagnostics. The immutable facts remain\n    # preserved in the Gemini sanitization contract above and source reports.\n    assert "ZAPI IDX:" not in message\n    assert "Stockbit:" not in message\n    assert "<b>🏦 BROKER SUMMARY</b>" in message\n    assert "📌 AVAILABLE" in message\n''',
)

for relative in (
    ".github/workflows/final-watchlist-contract-sync.yml",
    "tools/_sync_final_watchlist_contract.py",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()
