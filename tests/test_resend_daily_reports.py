from __future__ import annotations

from pathlib import Path


def test_market_and_post_market_resend_use_exact_delivery_copy_only() -> None:
    source = Path("tools/resend_daily_report.py").read_text(encoding="utf-8")

    assert "find_existing_delivery" in source
    assert "save_preview_selection" in source
    assert "load_preview_selection" in source
    assert "copy_existing_delivery" in source
    assert "TELEGRAM_EXACT_REPLAY" in source
    assert "enhanced_market_outlook_payloads" not in source
    assert "enhanced_post_market_payloads" not in source
    assert "write_payloads" not in source
    assert "deliver(ctx" not in source
    assert "global_market_snapshot.json" not in source
    assert "SWING_RUN_MANIFEST_" not in source


def test_market_and_post_market_launchers_pin_resend_to_exact_preview() -> None:
    market = Path("RUN_MARKET_OUTLOOK.bat").read_text(encoding="utf-8-sig")
    post = Path("RUN_POST_MARKET.bat").read_text(encoding="utf-8-sig")

    for source, job in ((market, "market_outlook"), (post, "post_market")):
        assert "Preview existing - exact pesan terakhir" in source
        assert "Kirim ulang - delivery-only exact preview terakhir ke Telegram" in source
        assert "set \"STATUS_VIEW=--delivery\"" in source
        assert "print_job_status.py --job" in source
        assert f"tools\\resend_daily_report.py --job {job}" in source
        preview_block = source.split(":PREVIEW_EXISTING", 1)[1].split(":", 1)[0]
        assert "--preview-only" in preview_block


def test_preview_only_flag_is_supported_for_daily_reports() -> None:
    source = Path("tools/resend_daily_report.py").read_text(encoding="utf-8")

    assert '"--preview-only"' in source
    assert "EXACT_PREVIEW_READ_ONLY" in source
    assert "if args.preview_only:" in source
    preview_branch = source.split("if args.preview_only:", 1)[1].split(
        "return EXIT_SUCCESS", 1
    )[0]
    assert "copy_existing_delivery" not in preview_branch
