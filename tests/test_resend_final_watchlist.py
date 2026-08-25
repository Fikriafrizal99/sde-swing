from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.job_runner.existing_delivery import ExistingDelivery
from tools import resend_final_watchlist as resend


def _source(entries: list[dict]) -> ExistingDelivery:
    return ExistingDelivery(
        requested_job="final_watchlist",
        trade_date="2026-08-25",
        source_run_id="SOURCE-RUN",
        source_job="final_watchlist",
        source_time="2026-08-25T16:00:00+07:00",
        entries=tuple(entries),
        preview_paths=(Path("approved-preview.txt"),),
        preview_manifest=Path("approved-manifest.json"),
        signature="raw-source-signature",
    )


def _event(report_type: str, sequence: int, message_ids: list[int], **extra) -> dict:
    return {
        "report_type": report_type,
        "delivery_sequence": sequence,
        "telegram_message_ids": message_ids,
        "status": "SENT",
        "time": f"2026-08-25T16:00:{sequence:02d}+07:00",
        **extra,
    }


def test_final_watchlist_resend_reuses_exact_delivery_without_formatter() -> None:
    source = Path("tools/resend_final_watchlist.py").read_text(encoding="utf-8")

    assert "find_existing_delivery" in source
    assert "save_preview_selection" in source
    assert "load_preview_selection" in source
    assert "_preflight_exact_bundle(source)" in source
    assert "copy_existing_delivery(ctx, source)" in source
    assert "ATOMIC_MEDIA_PREFLIGHT_PASSED" in source
    assert "enhanced_final_watchlist_payloads" not in source
    assert "final_watchlist_payloads" not in source
    assert "write_payloads" not in source
    assert "deliver(ctx" not in source


def test_modern_final_watchlist_family_wins_over_legacy_rows() -> None:
    raw = _source([
        _event("final_watchlist_summary", 1, [101]),
        _event("final_watchlist_detail", 2, [102], idempotency_key="DETAIL:BBCA", signature="A"),
        _event("final_watchlist_csv", 3, [103]),
        _event("final_watchlist", 4, [201]),
        _event("signal_detail", 5, [202], idempotency_key="LEGACY:BBCA", signature="B"),
    ])

    canonical, family, dropped = resend._canonicalize_final_watchlist_source(raw)

    assert family == "MODERN"
    assert dropped == 0
    assert [entry["report_type"] for entry in canonical.entries] == [
        "final_watchlist_summary",
        "final_watchlist_detail",
        "final_watchlist_csv",
    ]
    assert canonical.message_count == 3


def test_duplicate_source_message_is_replayed_only_once() -> None:
    raw = _source([
        _event("final_watchlist_summary", 1, [101], idempotency_key="SUMMARY", signature="S"),
        _event("final_watchlist_summary", 2, [101], idempotency_key="SUMMARY", signature="S"),
        _event("final_watchlist_detail", 3, [102], idempotency_key="DETAIL:BBCA", signature="D"),
    ])

    canonical, family, dropped = resend._canonicalize_final_watchlist_source(raw)

    assert family == "MODERN"
    assert dropped == 1
    assert [entry["telegram_message_ids"] for entry in canonical.entries] == [[101], [102]]
    assert canonical.message_count == 2


def test_canonicalization_keeps_immutable_preview_receipt_for_exact_fallback() -> None:
    raw = _source([
        _event("final_watchlist_summary", 1, [101]),
    ])

    canonical, _, _ = resend._canonicalize_final_watchlist_source(raw)

    assert canonical.entries[0]["report_type"] == "final_watchlist_summary"
    assert canonical.preview_paths == raw.preview_paths
    assert canonical.preview_manifest == raw.preview_manifest
    assert canonical.signature == raw.signature


def test_atomic_preflight_blocks_legacy_media_before_any_send(tmp_path: Path) -> None:
    preview = tmp_path / "SOURCE_final_watchlist_detail_ADRO.txt"
    preview.write_text("approved detail", encoding="utf-8")
    legacy_manifest = tmp_path / "SOURCE_preview_manifest.json"
    legacy_manifest.write_text(json.dumps({
        "run_id": "SOURCE-RUN",
        "job": "final_watchlist",
        "trade_date": "2026-08-25",
        "files": [str(preview)],
        "report_types": ["final_watchlist_detail"],
    }), encoding="utf-8")
    source = ExistingDelivery(
        requested_job="final_watchlist",
        trade_date="2026-08-25",
        source_run_id="SOURCE-RUN",
        source_job="final_watchlist",
        source_time="2026-08-25T16:00:00+07:00",
        entries=({
            "report_type": "final_watchlist_detail",
            "delivery_sequence": 1,
            "telegram_message_ids": [101],
            "attachment_path": "output/final_watchlist/ADRO_setup.png",
        },),
        preview_paths=(preview,),
        preview_manifest=legacy_manifest,
        signature="signature",
    )

    with pytest.raises(resend.ExactDeliveryError, match="ATOMIC_RESEND_BLOCKED_LEGACY_MEDIA"):
        resend._preflight_exact_bundle(source)


def test_atomic_preflight_accepts_hash_locked_media_bundle(tmp_path: Path, monkeypatch) -> None:
    preview = tmp_path / "SOURCE_final_watchlist_detail_ADRO.txt"
    preview.write_text("approved detail", encoding="utf-8")
    image = tmp_path / "SOURCE_attachments" / "001" / "ADRO_setup.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"immutable-adro-chart")
    manifest = tmp_path / "SOURCE_preview_manifest.json"
    manifest.write_text(json.dumps({
        "schema": "SDE_DELIVERY_PREVIEW_BUNDLE_V1",
        "payloads": [{
            "sequence": 1,
            "report_type": "final_watchlist_detail",
            "run_scoped_preview": str(preview),
            "attachment_archive": str(image),
            "attachment_sha256": resend.file_sha256(image),
            "telegram_parts": [{"kind": "photo", "text": "approved caption"}],
        }],
    }), encoding="utf-8")
    source = ExistingDelivery(
        requested_job="final_watchlist",
        trade_date="2026-08-25",
        source_run_id="SOURCE-RUN",
        source_job="final_watchlist",
        source_time="2026-08-25T16:00:00+07:00",
        entries=({
            "report_type": "final_watchlist_detail",
            "delivery_sequence": 1,
            "telegram_message_ids": [101],
            "attachment_path": str(image),
        },),
        preview_paths=(preview, image),
        preview_manifest=manifest,
        signature="signature",
    )
    monkeypatch.setattr(resend, "resolve", lambda value: Path(value))

    result = resend._preflight_exact_bundle(source)

    assert result == {"media_entries": 1, "verified_media_entries": 1}


def test_final_watchlist_menu_describes_exact_preview_receipt() -> None:
    source = Path("RUN_FINAL_WATCHLIST.bat").read_text(encoding="utf-8-sig")

    assert "Preview exact pesan terakhir - kunci source run" in source
    assert "Kirim exact preview terakhir ke Telegram" in source
    assert "set \"STATUS_VIEW=--delivery\"" in source
    assert "tools\\resend_final_watchlist.py --trade-date !PREVIEW_DATE! --preview-only" in source
    assert "tools\\resend_final_watchlist.py --trade-date !RESEND_DATE!" in source
