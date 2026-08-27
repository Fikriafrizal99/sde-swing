from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from swing_utils import file_sha256
from modules.job_runner.daily_report_recovery import (
    find_recoverable_daily_report,
    load_recovery_selection,
    save_recovery_selection,
    source_ack_ambiguous,
    source_recovery_mode,
)
from modules.job_runner.existing_delivery import ExactDeliveryError


def _ctx(tmp_path: Path, job: str):
    return SimpleNamespace(
        job=job,
        run_id=f"PREVIEW-{job}",
        trade_date=date(2026, 8, 27),
        previews_root=tmp_path / "previews",
        state_root=tmp_path / "state",
        scheduler_config={"delivery": {"delivery_log": str(tmp_path / "delivery.jsonl")}},
    )


def _write_failed_bundle(
    ctx,
    *,
    run_id: str,
    report_types: list[str],
    statuses: list[str] | None = None,
    message_ids: list[list[int]] | None = None,
    errors: list[str] | None = None,
) -> None:
    statuses = statuses or ["FAILED"] * len(report_types)
    message_ids = message_ids or [[] for _ in report_types]
    errors = errors or ["connection failed"] * len(report_types)
    folder = ctx.previews_root / ctx.trade_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    payloads = []
    events = []
    for sequence, report_type in enumerate(report_types, start=1):
        preview = folder / f"{run_id}_{report_type}.txt"
        preview.write_text(
            f"Run ID: {run_id}\nTrade Date: {ctx.trade_date.isoformat()}\n"
            f"Report Type: {report_type}\nEXACT {report_type}",
            encoding="utf-8",
        )
        failed = folder / f"{run_id}_{report_type}_failed.txt"
        failed.write_text(f"EXACT {report_type}", encoding="utf-8")
        signature = f"sig-{sequence}"
        payloads.append({
            "sequence": sequence,
            "report_type": report_type,
            "signature": signature,
            "run_scoped_preview": str(preview),
            "preview_sha256": file_sha256(preview),
            "telegram_parts": [{"kind": "text", "text": f"EXACT {report_type}"}],
            "attachment_archive": "",
            "attachment_sha256": "",
        })
        events.append({
            "time": f"2026-08-27T16:30:0{sequence}+07:00",
            "run_id": run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "report_type": report_type,
            "status": statuses[sequence - 1],
            "force_resend": False,
            "delivery_sequence": sequence,
            "delivery_total": len(report_types),
            "message_thread_id": "9",
            "telegram_message_ids": message_ids[sequence - 1],
            "sent_parts_before_failure": len(message_ids[sequence - 1]),
            "signature": signature,
            "error": errors[sequence - 1],
            "failed_payload": str(failed),
            "failed_payload_sha256": file_sha256(failed),
            "attachment_path": "",
        })

    (folder / f"{run_id}_preview_manifest.json").write_text(
        json.dumps({
            "schema": "SDE_DELIVERY_PREVIEW_BUNDLE_V1",
            "run_id": run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "state": "DELIVERY_INCOMPLETE",
            "delivery_complete": False,
            "payload_count": len(payloads),
            "payloads": payloads,
        }),
        encoding="utf-8",
    )
    log = Path(ctx.scheduler_config["delivery"]["delivery_log"])
    log.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def test_market_outlook_failed_delivery_can_be_preview_selected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "market_outlook")
    _write_failed_bundle(
        ctx,
        run_id="OUTLOOK-FAILED",
        report_types=["market_outlook"],
        errors=["HTTPSConnectionPool: Read timed out. (read timeout=30)"],
    )

    source = find_recoverable_daily_report(ctx, "market_outlook")
    assert source.source_run_id == "OUTLOOK-FAILED"
    assert source.message_count == 0
    assert source_ack_ambiguous(source) is True
    assert source_recovery_mode(source) == "RECOVERABLE_FAILED"
    selection = save_recovery_selection(ctx, source)
    assert selection.exists()
    loaded = load_recovery_selection(ctx, "market_outlook")
    assert loaded.source_run_id == source.source_run_id
    assert loaded.signature == source.signature


def test_post_market_no_telegram_recovery_can_be_preview_selected(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "post_market")
    _write_failed_bundle(
        ctx,
        run_id="POST-RECOVERY-NO-TELEGRAM",
        report_types=["post_market_heatmap", "post_market"],
        statuses=["NO_TELEGRAM", "NO_TELEGRAM"],
        errors=["", ""],
    )

    source = find_recoverable_daily_report(ctx, "post_market")
    assert source.source_run_id == "POST-RECOVERY-NO-TELEGRAM"
    assert source.message_count == 0
    assert source_ack_ambiguous(source) is False
    assert source_recovery_mode(source) == "RECOVERABLE_NO_TELEGRAM"
    selection = save_recovery_selection(ctx, source)
    assert selection.exists()
    loaded = load_recovery_selection(ctx, "post_market")
    assert loaded.source_run_id == source.source_run_id
    assert source_recovery_mode(loaded) == "RECOVERABLE_NO_TELEGRAM"


def test_daily_recovery_rejects_partial_telegram_ack(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "market_outlook")
    _write_failed_bundle(
        ctx,
        run_id="OUTLOOK-PARTIAL",
        report_types=["market_outlook"],
        message_ids=[[501]],
    )

    with pytest.raises(ExactDeliveryError, match="BLOCKED_PARTIAL_TELEGRAM_ACK"):
        find_recoverable_daily_report(ctx, "market_outlook")


def test_post_market_recovery_rejects_mixed_sent_and_failed_bundle(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, "post_market")
    _write_failed_bundle(
        ctx,
        run_id="POST-PARTIAL",
        report_types=["post_market_heatmap", "post_market"],
        statuses=["SENT", "FAILED"],
        message_ids=[[601], []],
    )

    with pytest.raises(ExactDeliveryError, match="BLOCKED_PARTIAL_TELEGRAM_ACK"):
        find_recoverable_daily_report(ctx, "post_market")


def test_daily_resend_keeps_existing_menu_and_requires_preview_selection() -> None:
    source = Path("tools/resend_daily_report.py").read_text(encoding="utf-8")
    market = Path("RUN_MARKET_OUTLOOK.bat").read_text(encoding="utf-8-sig")
    post = Path("RUN_POST_MARKET.bat").read_text(encoding="utf-8-sig")

    assert "find_recoverable_daily_report" in source
    assert "save_recovery_selection" in source
    assert "has_current_recovery_selection" in source
    assert "replay_recoverable_daily_report" in source
    assert "RECOVERABLE_NO_TELEGRAM" in source
    assert "if args.preview_only:" in source
    assert "write_payloads" not in source
    assert "deliver(ctx" not in source
    for launcher in (market, post):
        assert "[2] Preview existing" in launcher
        assert "[3] Kirim ulang" in launcher
        assert "[10]" not in launcher
