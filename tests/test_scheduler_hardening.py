from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_sde_job
from generate_task_scheduler_xml import main as generate_xml_main
from modules.job_runner.core import (
    _post_market_evaluation_datetime,
    broker_readiness,
    try_import_existing_broker_export,
    validate_broker_summary,
)
from modules.job_runner.delivery import deliver, split_telegram_text
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import FileLock, ResourceLocked, RunnerContext


def make_ctx(tmp: Path, **overrides) -> RunnerContext:
    scheduler_config = {
        "paths": {
            "preview_root": str(tmp / "previews"),
            "job_status_root": str(tmp / "status"),
            "state_root": str(tmp / "state"),
            "job_log": str(tmp / "job.log"),
        },
        "locks": {"stale_after_minutes": 60, "global_resource_lock_name": "sde_pipeline_write.lock"},
        "runtime": {"global_resource_lock_enabled": True},
        "post_market": {"allow_broker_fusion": False, "allow_final_decision": False},
        "final_watchlist": {
            "missing_broker_policy": "skip_final_watchlist",
            "broker_retry_seconds": 0,
            "cutoff_time": "18:30",
            "max_detail_symbols": 5,
        },
        "broker_readiness": {
            "minimum_broker_records": 1,
            "minimum_broker_coverage_ratio": 0.8,
            "stable_file_check_seconds": 0,
            "reject_sample_data": True,
            "required_broker_columns": ["EMITEN", "TO_DATE", "TOTAL_BUY", "TOTAL_SELL"],
        },
        "telegram": {"maximum_message_length": 120},
        "delivery": {
            "idempotency_index": str(tmp / "state/idempotency.json"),
            "delivery_log": str(tmp / "state/delivery.jsonl"),
            "failed_root": str(tmp / "failed"),
            "topic_routing": {},
        },
    }
    config = {
        "candidate": {"top": 30},
        "paths": {
            "candidate_output_dir": str(tmp / "candidates"),
            "broker_summary_latest": str(tmp / "broker/BROKER_SUMMARY_LATEST.csv"),
            "telegram_config": str(tmp / "telegram.json"),
        },
    }
    payload = {
        "job": "final_watchlist",
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
    payload.update(overrides)
    return RunnerContext(**payload)


def write_snapshot(tmp: Path, trade_date: str, symbols: list[str] | None = None) -> Path:
    symbols = symbols or ["BBCA", "TLKM"]
    candidates = tmp / "candidates/technical_candidates_top30.csv"
    candidates.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"Symbol": symbols, "Technical_Data_Date": [trade_date] * len(symbols)}).to_csv(candidates, index=False)
    snapshot = {
        "snapshot_id": f"SWING-TECH-SNAPSHOT-{trade_date}",
        "trade_date": trade_date,
        "output_paths": {"technical_candidates": str(candidates)},
    }
    path = ROOT / f"data/output/snapshots/2026-07-24/latest_snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return path


def write_broker(tmp: Path, broker_date: str, symbols: list[str] | None = None, **extra) -> Path:
    symbols = symbols or ["BBCA", "TLKM"]
    broker = tmp / "broker/BROKER_SUMMARY_LATEST.csv"
    broker.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({
        "EMITEN": symbols,
        "TO_DATE": [broker_date] * len(symbols),
        "TOTAL_BUY": [1000] * len(symbols),
        "TOTAL_SELL": [500] * len(symbols),
    })
    for key, value in extra.items():
        frame[key] = value
    frame.to_csv(broker, index=False)
    return broker


class SchedulerHardeningTests(unittest.TestCase):
    def test_duplicate_delivery_keeps_engine_terminal_and_dependency_safe(self) -> None:
        delivery = [{"status": "DUPLICATE_SUPPRESSED", "report_type": "post_market"}]
        self.assertEqual(run_sde_job._status_after_delivery(delivery), "SUCCESS_WITH_WARNING")
        self.assertEqual(run_sde_job._exit_after_delivery(make_ctx(Path(tempfile.mkdtemp())), delivery), 0)

    def test_dependency_view_reconciles_current_snapshot_over_stale_status_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(
                Path(td),
                config_provenance={"config_version": "1.7.0-multisource"},
            )
            snapshot = {
                "status": "VALID",
                "snapshot_id": "SNAP-CURRENT",
                "trade_date": "2026-07-24",
                "config_version": "1.7.0-multisource",
                "data_quality_status": "VALID_WITH_ZAPI_WARNING",
            }
            payloads = {
                "technical_snapshot": {
                    "status": "SUCCESS",
                    "trade_date": "2026-07-23",
                    "config_version": "1.7.0-multisource",
                },
                "post_market": {
                    "status": "SKIPPED",
                    "trade_date": "2026-07-24",
                    "config_version": "1.7.0-multisource",
                    "snapshot_id": "SNAP-CURRENT",
                    "snapshot_trade_date": "2026-07-24",
                    "details": {
                        "snapshot_id": "SNAP-CURRENT",
                        "snapshot_trade_date": "2026-07-24",
                        "delivery": [{"status": "DUPLICATE_SUPPRESSED"}],
                    },
                },
            }
            with patch("run_sde_job.load_technical_snapshot", return_value=snapshot), \
                 patch("run_sde_job.read_json", side_effect=lambda path: payloads.get(Path(path).name.replace("_latest.json", ""), {})):
                statuses = run_sde_job._integrated_statuses(ctx)
            self.assertEqual(statuses["technical_snapshot"]["status"], "SUCCESS_WITH_WARNING")
            self.assertEqual(statuses["technical_snapshot"]["trade_date"], "2026-07-24")
            self.assertEqual(statuses["post_market"]["status"], "SUCCESS_WITH_WARNING")

    def test_existing_download_export_is_imported_without_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            downloads = tmp / "Downloads"
            downloads.mkdir()
            (downloads / "BROKER_SUMMARY_COMBINED_2026-07-24.csv").write_text("placeholder", encoding="utf-8")
            test_config = dict(make_ctx(tmp).config)
            test_config["paths"] = {
                **test_config.get("paths", {}),
                "manifest_dir": str(tmp / "manifests"),
                "broker_summary_latest": str(tmp / "broker" / "BROKER_SUMMARY_LATEST.csv"),
                "broker_raw_latest": str(tmp / "broker" / "BROKER_RAW_LATEST.csv"),
            }
            ctx = make_ctx(
                tmp,
                config={**test_config, "broker": {"downloads_dir": str(downloads)}},
            )
            snapshot = {
                "status": "VALID",
                "snapshot_id": "SNAP-CURRENT",
                "trade_date": "2026-07-24",
                "broker_navigator_path": str(tmp / "BROKER_NAVIGATOR_SYMBOLS.csv"),
            }
            Path(snapshot["broker_navigator_path"]).write_text("Symbol\nBBCA\n", encoding="utf-8")
            with patch("modules.job_runner.core.load_technical_snapshot", return_value=snapshot), \
                 patch("modules.job_runner.core.broker_readiness", side_effect=[
                     (False, {"status": "DATE_MISMATCH"}),
                     (True, {"status": "READY", "broker_date": "2026-07-24"}),
                 ]), \
                 patch("modules.job_runner.core.run_command") as command:
                ready, detail = try_import_existing_broker_export(ctx)
            self.assertTrue(ready)
            self.assertEqual(detail["broker_import_source"], "DOWNLOADS_AUTO_IMPORT")
            command.assert_called_once()
            args = command.call_args.args[2]
            self.assertIn("--include-existing", args)
            self.assertIn("--timeout", args)
            self.assertEqual(args[args.index("--timeout") + 1], "1")

    def test_post_market_evaluation_uses_actual_start_time_before_close(self) -> None:
        ctx = make_ctx(
            Path(tempfile.mkdtemp()),
            trade_date=date(2026, 8, 5),
            started_at=datetime(2026, 8, 5, 16, 6, tzinfo=ZoneInfo("Asia/Jakarta")),
        )
        self.assertEqual(
            _post_market_evaluation_datetime(ctx, {"market_close": "16:15"}),
            "2026-08-05T16:06:00+07:00",
        )

    def test_post_market_historical_evaluation_remains_deterministic(self) -> None:
        ctx = make_ctx(
            Path(tempfile.mkdtemp()),
            trade_date=date(2026, 8, 4),
            started_at=datetime(2026, 8, 5, 16, 6, tzinfo=ZoneInfo("Asia/Jakarta")),
        )
        self.assertEqual(
            _post_market_evaluation_datetime(ctx, {"market_close": "16:15"}),
            "2026-08-04T16:15:00+07:00",
        )

    def test_post_market_dry_run_calls_technical_stage_not_master_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td), job="post_market", dry_run=True)
            manifest = {"Run_ID": ctx.run_id, "Snapshot_ID": "SNAP", "Snapshot_Manifest": "", "Technical_Date": "2026-07-24"}
            with patch("run_sde_job.run_post_market_technical_stage", return_value=manifest) as technical, \
                 patch("run_sde_job.run_master_pipeline") as master, \
                 patch("run_sde_job.post_market_payloads", return_value=[ReportPayload("closing_bell", "closing_bell.txt", "ok")]), \
                 patch("run_sde_job.write_payloads", return_value=[]), \
                 patch("run_sde_job.deliver", return_value=[]):
                code = run_sde_job.job_post_market(ctx)
            self.assertEqual(code, 0)
            technical.assert_called_once()
            master.assert_not_called()

    def test_post_market_delivery_failure_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td), job="post_market")
            manifest = {"Run_ID": ctx.run_id, "Snapshot_ID": "SNAP", "Snapshot_Manifest": "", "Technical_Date": "2026-07-24"}
            failed = [{"status": "FAILED", "report_type": "closing_bell", "error": "telegram failed", "part_count": 1}]
            with patch("run_sde_job.run_post_market_technical_stage", return_value=manifest), \
                 patch("run_sde_job.post_market_payloads", return_value=[ReportPayload("closing_bell", "closing_bell.txt", "ok")]), \
                 patch("run_sde_job.write_payloads", return_value=[]), \
                 patch("run_sde_job.deliver", return_value=failed):
                code = run_sde_job.job_post_market(ctx)
            self.assertEqual(code, 50)

    def test_post_market_refresh_failure_falls_back_to_current_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td), job="post_market", dry_run=True)
            snapshot = {
                "status": "VALID",
                "snapshot_id": "SNAP-CURRENT",
                "trade_date": "2026-07-24",
                "manifest_path": "",
                "output_paths": {},
                "data_quality_status": "VALID",
            }
            with patch("run_sde_job.run_post_market_technical_stage", side_effect=RuntimeError("Yahoo unavailable")), \
                 patch("run_sde_job.load_technical_snapshot", return_value=snapshot), \
                 patch("run_sde_job.post_market_payloads", return_value=[ReportPayload("closing_bell", "closing_bell.txt", "ok")]) as reports, \
                 patch("run_sde_job.write_payloads", return_value=[]), \
                 patch("run_sde_job.deliver", return_value=[{"status": "DRY_RUN", "report_type": "closing_bell", "part_count": 1}]):
                code = run_sde_job.job_post_market(ctx)
            self.assertEqual(code, 0)
            manifest = reports.call_args.args[1]
            self.assertEqual(manifest["Snapshot_ID"], "SNAP-CURRENT")
            self.assertEqual(manifest["Data_Quality_Status"], "VALID_WITH_REFRESH_FALLBACK")
            self.assertIn("REFRESH_FAILED_USING_EXISTING_SNAPSHOT", manifest["Warnings"][0])

    def test_preview_existing_does_not_run_technical_stage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = make_ctx(Path(td), job="post_market", preview_existing=True)
            with patch("run_sde_job.run_post_market_technical_stage") as technical, \
                 patch("run_sde_job.load_technical_snapshot", return_value={
                     "status": "VALID",
                     "snapshot_id": "SNAP",
                     "trade_date": "2026-07-24",
                     "manifest_path": "",
                     "output_paths": {},
                 }), \
                 patch("run_sde_job.post_market_payloads", return_value=[ReportPayload("closing_bell", "closing_bell.txt", "ok")]), \
                 patch("run_sde_job.write_payloads", return_value=[]), \
                 patch("run_sde_job.deliver", return_value=[]):
                code = run_sde_job.job_post_market(ctx)
            self.assertEqual(code, 0)
            technical.assert_not_called()

    def test_stale_technical_snapshot_is_rejected_even_if_broker_same_old_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            snap = write_snapshot(tmp, "2026-07-23")
            write_broker(tmp, "2026-07-23")
            try:
                ready, detail = broker_readiness(ctx)
                self.assertFalse(ready)
                self.assertEqual(detail["status"], "STALE_TECHNICAL_SNAPSHOT")
            finally:
                snap.unlink(missing_ok=True)

    def test_broker_old_date_waits_even_when_snapshot_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            snap = write_snapshot(tmp, "2026-07-24")
            write_broker(tmp, "2026-07-23")
            try:
                ready, detail = broker_readiness(ctx)
                self.assertFalse(ready)
                self.assertEqual(detail["status"], "DATE_MISMATCH")
            finally:
                snap.unlink(missing_ok=True)

    def test_current_snapshot_and_current_broker_are_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            snap = write_snapshot(tmp, "2026-07-24")
            write_broker(tmp, "2026-07-24")
            try:
                ready, detail = broker_readiness(ctx)
                self.assertTrue(ready)
                self.assertEqual(detail["status"], "READY")
            finally:
                snap.unlink(missing_ok=True)

    def test_broker_validator_rejects_schema_sample_duplicate_and_invalid_numeric(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            write_broker(tmp, "2026-07-24")
            self.assertEqual(validate_broker_summary(ctx, ["BBCA", "TLKM"])["status"], "READY")
            pd.DataFrame({"EMITEN": ["BBCA"]}).to_csv(tmp / "broker/BROKER_SUMMARY_LATEST.csv", index=False)
            self.assertEqual(validate_broker_summary(ctx, ["BBCA"])["status"], "SCHEMA_INVALID")
            write_broker(tmp, "2026-07-24", symbols=["BBCA", "BBCA"])
            self.assertEqual(validate_broker_summary(ctx, ["BBCA"])["status"], "DUPLICATE_DATA")
            write_broker(tmp, "2026-07-24", symbols=["BBCA"], TOTAL_BUY=["sample"])
            self.assertEqual(validate_broker_summary(ctx, ["BBCA"])["status"], "SAMPLE_DATA_DETECTED")
            pd.DataFrame({"EMITEN": ["BBCA"], "TO_DATE": ["2026-07-24"], "TOTAL_BUY": ["abc"], "TOTAL_SELL": [1]}).to_csv(tmp / "broker/BROKER_SUMMARY_LATEST.csv", index=False)
            self.assertEqual(validate_broker_summary(ctx, ["BBCA"])["status"], "INVALID_NUMERIC_DATA")

    def test_missing_broker_policy_skip_and_preliminary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            detail = {"status": "WAITING_DATA_TIMEOUT", "reason": "BROKER_CUTOFF_REACHED"}
            with patch("run_sde_job.wait_for_broker_ready", return_value=(False, detail)), \
                 patch("run_sde_job.broker_waiting_payload", return_value=[ReportPayload("data_warning", "warning.txt", "warn")]), \
                 patch("run_sde_job.write_payloads", return_value=[]), \
                 patch("run_sde_job.deliver", return_value=[]):
                code = run_sde_job.job_final_watchlist(ctx)
            self.assertEqual(code, 20)

            ctx.scheduler_config["final_watchlist"]["missing_broker_policy"] = "send_preliminary_watchlist"
            with patch("run_sde_job.wait_for_broker_ready", return_value=(False, detail)), \
                 patch("run_sde_job.broker_waiting_payload", return_value=[ReportPayload("data_warning", "warning.txt", "warn")]), \
                 patch("run_sde_job.preliminary_watchlist_payloads", return_value=[ReportPayload("preliminary_watchlist", "pre.txt", "PRELIMINARY")]), \
                 patch("run_sde_job.write_payloads", return_value=[]), \
                 patch("run_sde_job.deliver", return_value=[]):
                code = run_sde_job.job_final_watchlist(ctx)
            self.assertEqual(code, 0)

    def test_interactive_broker_can_recover_broker_dependency_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(
                tmp,
                interactive_broker=True,
                config_provenance={"config_version": "1.7.0-multisource"},
            )
            dependency = {
                "required": ["market_outlook", "post_market", "broker_summary", "broker_multi_day"],
                "valid": False,
                "dependencies": {
                    "market_outlook": {
                        "status": "SUCCESS", "fresh": True, "date_match": True, "config_match": True,
                    },
                    "post_market": {
                        "status": "SUCCESS", "fresh": True, "date_match": True, "config_match": True,
                    },
                    "broker_summary": {
                        "status": "FAILED", "fresh": False, "date_match": True, "config_match": True,
                    },
                    "broker_multi_day": {
                        "status": "SKIPPED", "fresh": False, "date_match": True, "config_match": True,
                    },
                },
            }
            with patch("run_sde_job._require_integrated_dependencies", return_value=dependency), \
                 patch("run_sde_job.broker_readiness", return_value=(False, {"status": "DATE_MISMATCH"})), \
                 patch("run_sde_job.run_interactive_broker_break", return_value=(False, {"status": "WAITING_DATA"})) as manual_break, \
                 patch("run_sde_job.broker_waiting_payload", return_value=[]), \
                 patch("run_sde_job.write_payloads", return_value=[]), \
                 patch("run_sde_job.deliver", return_value=[]):
                code = run_sde_job.job_final_watchlist(ctx)
            self.assertEqual(code, 20)
            manual_break.assert_called_once()
            events = [line for line in ctx.log_path.read_text(encoding="utf-8").splitlines() if line]
            self.assertTrue(any("INTERACTIVE_BROKER_DEPENDENCY_RECOVERY" in line for line in events))

    def test_interactive_broker_does_not_bypass_invalid_market_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(
                tmp,
                interactive_broker=True,
                config_provenance={"config_version": "1.7.0-multisource"},
            )
            dependency = {
                "required": ["market_outlook", "post_market", "broker_summary", "broker_multi_day"],
                "valid": False,
                "dependencies": {
                    "market_outlook": {
                        "status": "FAILED", "fresh": False, "date_match": True, "config_match": True,
                    },
                    "post_market": {
                        "status": "SUCCESS", "fresh": True, "date_match": True, "config_match": True,
                    },
                },
            }
            with patch("run_sde_job._require_integrated_dependencies", return_value=dependency), \
                 patch("run_sde_job.run_interactive_broker_break") as manual_break:
                code = run_sde_job.job_final_watchlist(ctx)
            self.assertEqual(code, 10)
            manual_break.assert_not_called()

    def test_global_resource_lock_blocks_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            first = make_ctx(tmp, job="post_market")
            second = make_ctx(tmp, job="final_watchlist")
            with FileLock(first, "sde_pipeline_write.lock", kind="global_resource"):
                with self.assertRaises(ResourceLocked):
                    with FileLock(second, "sde_pipeline_write.lock", kind="global_resource"):
                        pass

    def test_idempotency_is_by_report_type_not_payload_hash_and_force_resends(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp, job="market_outlook")
            with patch("modules.job_runner.delivery._send_telegram", return_value={"ok": True, "result": {"message_id": 1}}):
                first = deliver(ctx, [ReportPayload("market_outlook", "a.txt", "payload A")])
                second = deliver(ctx, [ReportPayload("market_outlook", "a.txt", "payload B")])
            self.assertEqual(first[0]["status"], "SENT")
            self.assertEqual(second[0]["status"], "DUPLICATE_SUPPRESSED")
            forced = make_ctx(tmp, job="market_outlook", force=True)
            with patch("modules.job_runner.delivery._send_telegram", return_value={"ok": True, "result": {"message_id": 2}}):
                third = deliver(forced, [ReportPayload("market_outlook", "a.txt", "payload B")])
            self.assertEqual(third[0]["status"], "SENT")
            self.assertTrue(third[0]["force_resend"])

    def test_telegram_splitter_keeps_all_content(self) -> None:
        text = "\n\n".join([f"BLOCK-{i} " + ("A&B<C>" * 20) for i in range(12)])
        parts = split_telegram_text(text, max_len=120)
        self.assertGreater(len(parts), 1)
        cleaned = []
        for part in parts:
            if part.startswith("Bagian "):
                cleaned.append(part.split("\n\n", 1)[1])
            else:
                cleaned.append(part)
        self.assertEqual("".join(cleaned).replace("\n\n", ""), text.replace("\n\n", ""))

    def test_generated_xml_is_valid_and_has_no_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "xml"
            argv = ["generate_task_scheduler_xml.py", "--project-dir", str(ROOT), "--output-dir", str(out)]
            with patch.object(sys, "argv", argv):
                self.assertEqual(generate_xml_main(), 0)
            for path in out.glob("*.xml"):
                ET.parse(path)
                raw = path.read_bytes()
                encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
                text = raw.decode(encoding)
                self.assertNotIn("__PROJECT_DIR__", text)
                self.assertIn(str(ROOT), text)


if __name__ == "__main__":
    unittest.main()
