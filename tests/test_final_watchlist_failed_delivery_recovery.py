from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from swing_utils import file_sha256
from modules.job_runner.existing_delivery import ExactDeliveryError
from modules.job_runner.final_watchlist_recovery import (
    find_recoverable_final_watchlist,
    has_current_recovery_selection,
    load_recovery_selection,
    replay_recoverable_final_watchlist,
    save_recovery_selection,
)
from modules.job_runner.runtime import RunnerContext


ROOT = Path(__file__).resolve().parents[1]


def make_ctx(tmp: Path) -> RunnerContext:
    scheduler_config = {
        "paths": {
            "preview_root": str(tmp / "previews"),
            "job_status_root": str(tmp / "status"),
            "state_root": str(tmp / "state"),
            "job_log": str(tmp / "job.log"),
        },
        "delivery": {
            "delivery_log": str(tmp / "state" / "delivery.jsonl"),
            "idempotency_index": str(tmp / "state" / "idempotency.json"),
            "failed_root": str(tmp / "failed"),
            "topic_routing": {},
        },
        "telegram": {"maximum_message_length": 4000},
    }
    return RunnerContext(
        job="final_watchlist",
        config_path=ROOT / "config" / "pipeline.json",
        scheduler_config_path=ROOT / "config" / "scheduler.json",
        trade_date=date(2026, 8, 26),
        run_id="RECOVERY-TEST",
        force=False,
        config={"paths": {"telegram_config": str(tmp / "telegram.json")}},
        scheduler_config=scheduler_config,
        calendar_config={"holidays": [], "special_trading_days": []},
    )


def write_failed_bundle(tmp: Path, *, partial_ack: bool = False) -> str:
    run_id = "SDE-FINAL-WATCHLIST-20260826-190000-source"
    trade_date = "2026-08-26"
    folder = tmp / "previews" / trade_date
    folder.mkdir(parents=True, exist_ok=True)
    attachment = folder / f"{run_id}_attachments" / "003" / "final_watchlist.csv"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    attachment.write_text("Symbol,Decision\nBBNI,BUY READY\n", encoding="utf-8")

    definitions = [
        (1, "final_watchlist_summary", "Summary body", ""),
        (2, "final_watchlist_detail", "Detail body", ""),
        (3, "final_watchlist_csv", "CSV caption", str(attachment)),
    ]
    records = []
    events = []
    for sequence, report_type, body, attachment_path in definitions:
        preview = folder / f"{run_id}_{report_type}.txt"
        preview.write_text(
            f"Run ID: {run_id}\nTrade Date: {trade_date}\nReport Type: {report_type}\n\n{body}\n",
            encoding="utf-8",
        )
        telegram_parts = [
            {"kind": "document" if attachment_path else "text", "text": body}
        ]
        records.append({
            "sequence": sequence,
            "report_type": report_type,
            "filename": f"{report_type}.txt",
            "signature": f"sig-{sequence}",
            "run_scoped_preview": str(preview),
            "preview_sha256": file_sha256(preview),
            "telegram_parts": telegram_parts,
            "attachment_archive": attachment_path,
            "attachment_sha256": file_sha256(Path(attachment_path)) if attachment_path else "",
        })
        event = {
            "time": f"2026-08-26T19:00:0{sequence}+07:00",
            "run_id": run_id,
            "job": "final_watchlist",
            "trade_date": trade_date,
            "report_type": report_type,
            "status": "FAILED",
            "delivery_sequence": sequence,
            "delivery_total": 3,
            "signature": f"sig-{sequence}",
            "attachment_path": attachment_path,
            "message_thread_id": "9",
            "force_resend": False,
            "telegram_message_ids": [901] if partial_ack and sequence == 1 else [],
        }
        events.append(event)

    manifest = {
        "schema": "SDE_DELIVERY_PREVIEW_BUNDLE_V1",
        "run_id": run_id,
        "job": "final_watchlist",
        "trade_date": trade_date,
        "state": "DELIVERY_INCOMPLETE",
        "delivery_complete": False,
        "payload_count": 3,
        "payloads": records,
    }
    (folder / f"{run_id}_preview_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log = tmp / "state" / "delivery.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events),
        encoding="utf-8",
    )
    return run_id


class FinalWatchlistFailedDeliveryRecoveryTests(unittest.TestCase):
    def test_failed_without_any_telegram_ack_is_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            run_id = write_failed_bundle(tmp)
            source, family, dropped = find_recoverable_final_watchlist(ctx)

            self.assertEqual(source.source_run_id, run_id)
            self.assertEqual(family, "MODERN")
            self.assertEqual(dropped, 0)
            self.assertEqual(source.message_count, 0)
            self.assertEqual(len(source.entries), 3)
            self.assertTrue(all(path.exists() for path in source.preview_paths))

    def test_any_partial_telegram_ack_blocks_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            write_failed_bundle(tmp, partial_ack=True)
            with self.assertRaisesRegex(
                ExactDeliveryError,
                "FINAL_WATCHLIST_RECOVERY_BLOCKED_PARTIAL_TELEGRAM_ACK",
            ):
                find_recoverable_final_watchlist(ctx)

    def test_recovery_selection_is_date_and_hash_locked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            write_failed_bundle(tmp)
            source, family, _ = find_recoverable_final_watchlist(ctx)
            save_recovery_selection(ctx, source, family)

            self.assertTrue(has_current_recovery_selection(ctx))
            loaded, loaded_family, _ = load_recovery_selection(ctx)
            self.assertEqual(loaded.source_run_id, source.source_run_id)
            self.assertEqual(loaded_family, family)

            source.preview_paths[0].write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(
                ExactDeliveryError,
                "FINAL_WATCHLIST_RECOVERY_PREVIEW_INTEGRITY_FAILED|FINAL_WATCHLIST_RECOVERY_SELECTION_FILES_CHANGED",
            ):
                load_recovery_selection(ctx)

    def test_recovery_replays_archive_directly_and_records_sent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            write_failed_bundle(tmp)
            source, _, _ = find_recoverable_final_watchlist(ctx)
            calls: list[str] = []

            def fake_send(_ctx, entry, _spec, message_ids=None):
                calls.append(str(entry.get("report_type")))
                message_ids.append(1000 + len(calls))
                return message_ids

            with patch(
                "modules.job_runner.existing_delivery._send_archived_entry",
                side_effect=fake_send,
            ):
                result = replay_recoverable_final_watchlist(ctx, source)

            self.assertEqual(calls, [
                "final_watchlist_summary",
                "final_watchlist_detail",
                "final_watchlist_csv",
            ])
            self.assertTrue(all(item["status"] == "SENT" for item in result))
            self.assertTrue(all(item["copy_mode"] == "ARCHIVED_PREVIEW_EXACT_RECOVERY" for item in result))

    def test_windows_menu_routes_preview_and_resend_through_recovery_wrapper(self) -> None:
        source = (ROOT / "RUN_FINAL_WATCHLIST.bat").read_text(encoding="utf-8-sig")
        self.assertEqual(source.count("tools\\resend_final_watchlist_recovery.py"), 2)
        self.assertIn("Preview exact/recovery source - kunci source run", source)


if __name__ == "__main__":
    unittest.main()
