from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

from modules.job_runner.core import broker_readiness
from modules.job_runner.delivery import deliver, normalize_telegram_text
from modules.job_runner.reports import ReportPayload, write_payloads
from modules.job_runner.runtime import RunnerContext, trading_day_status


def make_ctx(tmp: Path, **overrides) -> RunnerContext:
    scheduler_config = {
        "paths": {
            "preview_root": str(tmp / "previews"),
            "job_status_root": str(tmp / "status"),
            "state_root": str(tmp / "state"),
            "job_log": str(tmp / "job.log"),
        },
        "locks": {"stale_after_minutes": 10},
        "final_watchlist": {"missing_broker_policy": "skip_final_watchlist", "broker_retry_seconds": 0, "cutoff_time": "18:30"},
        "broker_readiness": {
            "minimum_broker_records": 1,
            "minimum_broker_coverage_ratio": 0.0,
            "stable_file_check_seconds": 0,
            "reject_sample_data": True,
            "required_broker_columns": ["EMITEN", "TO_DATE", "TOTAL_BUY", "TOTAL_SELL"],
        },
        "delivery": {
            "idempotency_index": str(tmp / "state/idempotency.json"),
            "delivery_log": str(tmp / "state/delivery.jsonl"),
            "failed_root": str(tmp / "failed"),
            "topic_routing": {},
        },
    }
    config = {
        "paths": {
            "candidate_output_dir": str(tmp / "candidates"),
            "broker_summary_latest": str(tmp / "broker/BROKER_SUMMARY_LATEST.csv"),
            "telegram_config": str(tmp / "telegram.json"),
        }
    }
    data = {
        "job": "market_outlook",
        "config_path": ROOT / "config/pipeline.json",
        "scheduler_config_path": ROOT / "config/scheduler.json",
        "trade_date": date(2026, 7, 24),
        "run_id": "TEST-RUN",
        "dry_run": False,
        "preview_existing": False,
        "no_telegram": False,
        "force": False,
        "debug": False,
        "config": config,
        "scheduler_config": scheduler_config,
        "calendar_config": {"holidays": [], "special_trading_days": []},
    }
    data.update(overrides)
    return RunnerContext(**data)


class SdeJobRunnerTests(unittest.TestCase):
    def test_trading_calendar_skips_weekend_and_allows_special_day(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            saturday = make_ctx(tmp, trade_date=date(2026, 7, 25))
            ok, reason = trading_day_status(saturday)
            self.assertFalse(ok)
            self.assertEqual(reason, "SKIPPED_NON_TRADING_DAY")

            special = make_ctx(
                tmp,
                trade_date=date(2026, 7, 25),
                calendar_config={"holidays": [], "special_trading_days": ["2026-07-25"]},
            )
            ok, reason = trading_day_status(special)
            self.assertTrue(ok)
            self.assertEqual(reason, "SPECIAL_TRADING_DAY")

    def test_preview_writer_adds_run_metadata_and_run_scoped_copy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            paths = write_payloads(ctx, [ReportPayload("market_outlook", "market_outlook.txt", "Body")])
            self.assertEqual(len(paths), 1)
            self.assertTrue(paths[0].exists())
            text = paths[0].read_text(encoding="utf-8")
            self.assertIn("Run ID: TEST-RUN", text)
            self.assertTrue((paths[0].parent / "TEST-RUN_market_outlook.txt").exists())

    def test_delivery_idempotency_suppresses_second_send(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            payload = ReportPayload("market_outlook", "market_outlook.txt", "same payload")
            with patch("modules.job_runner.delivery._send_telegram", return_value={"ok": True, "result": {"message_id": 10}}):
                first = deliver(ctx, [payload])
                second = deliver(ctx, [payload])
            self.assertEqual(first[0]["status"], "SENT")
            self.assertEqual(second[0]["status"], "DUPLICATE_SUPPRESSED")

    def test_dry_run_never_calls_telegram_sender(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp, dry_run=True)
            payload = ReportPayload("market_outlook", "market_outlook.txt", "payload")
            with patch("modules.job_runner.delivery._send_telegram") as sender:
                result = deliver(ctx, [payload])
            sender.assert_not_called()
            self.assertEqual(result[0]["status"], "DRY_RUN")

    def test_delivery_normalizes_spacing_and_records_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            payloads = [
                ReportPayload("market_outlook", "a.txt", "Header\n\n\n\nBody   \n"),
                ReportPayload("post_market", "b.txt", "Second"),
            ]
            sent_texts: list[str] = []

            def capture(_ctx, _payload, text=None, **_kwargs):
                sent_texts.append(text or "")
                return {"ok": True, "result": {"message_id": len(sent_texts)}}

            with patch("modules.job_runner.delivery._send_telegram", side_effect=capture):
                result = deliver(ctx, payloads)

            self.assertEqual(sent_texts[0], "Header\n\nBody")
            self.assertEqual(result[0]["delivery_sequence"], 1)
            self.assertEqual(result[1]["delivery_sequence"], 2)
            self.assertEqual(result[1]["delivery_total"], 2)
            self.assertEqual(normalize_telegram_text("A\r\n\r\n\r\nB"), "A\n\nB")

    def test_broker_readiness_uses_technical_snapshot_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp, job="final_watchlist")
            (tmp / "candidates").mkdir(parents=True)
            (tmp / "broker").mkdir(parents=True)
            pd.DataFrame({
                "Symbol": ["BBCA"],
                "Technical_Data_Date": ["2026-07-24"],
            }).to_csv(tmp / "candidates/technical_candidates_top30.csv", index=False)
            snap_dir = tmp / "snapshots/2026-07-24/SWING-TECH-SNAPSHOT-TEST"
            snap_dir.mkdir(parents=True)
            snap_manifest = {
                "snapshot_id": "SWING-TECH-SNAPSHOT-TEST",
                "trade_date": "2026-07-24",
                "output_paths": {"technical_candidates": str(tmp / "candidates/technical_candidates_top30.csv")},
            }
            (tmp / "data/output/snapshots").mkdir(parents=True, exist_ok=True)
            # core.resolve() is project-root based, so use scheduler output location under project root in integration tests.
            project_snapshot = ROOT / "data/output/snapshots/2026-07-24/latest_snapshot.json"
            project_snapshot.parent.mkdir(parents=True, exist_ok=True)
            project_snapshot.write_text(__import__("json").dumps(snap_manifest), encoding="utf-8")
            pd.DataFrame({
                "EMITEN": ["BBCA"],
                "TO_DATE": ["2026-07-24"],
                "TOTAL_BUY": [100],
                "TOTAL_SELL": [50],
            }).to_csv(tmp / "broker/BROKER_SUMMARY_LATEST.csv", index=False)
            try:
                ready, detail = broker_readiness(ctx)
                self.assertTrue(ready)
                self.assertEqual(detail["broker_date"], "2026-07-24")
            finally:
                project_snapshot.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
