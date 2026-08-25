from __future__ import annotations

from pathlib import Path


def test_final_watchlist_resend_uses_exact_delivery_copy_only() -> None:
    source = Path("tools/resend_final_watchlist.py").read_text(encoding="utf-8")

    assert "find_existing_delivery" in source
    assert "save_preview_selection" in source
    assert "load_preview_selection" in source
    assert "copy_existing_delivery" in source
    assert "TELEGRAM_COPY_EXACT" in source
    assert "enhanced_final_watchlist_payloads" not in source
    assert "final_watchlist_payloads" not in source
    assert "write_payloads" not in source
    assert "deliver(ctx" not in source


def test_final_watchlist_menu_describes_exact_preview_receipt() -> None:
    source = Path("RUN_FINAL_WATCHLIST.bat").read_text(encoding="utf-8-sig")

    assert "Preview exact pesan terakhir - kunci source run" in source
    assert "Kirim exact preview terakhir - Telegram copy" in source
    assert "tools\\resend_final_watchlist.py --trade-date !PREVIEW_DATE! --preview-only" in source
    assert "tools\\resend_final_watchlist.py --trade-date !RESEND_DATE!" in source
