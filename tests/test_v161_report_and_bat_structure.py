from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

from generate_task_scheduler_xml import NS, TASKS, build_task
from modules.job_runner.reports import load_broker_raw
from modules.job_runner.runtime import RunnerContext


def make_ctx(tmp: Path, downloads: Path, *, dry_run: bool = False) -> RunnerContext:
    config = {
        "paths": {
            "broker_raw_latest": str(tmp / "input" / "BROKER_RAW_LATEST.csv"),
            "telegram_config": str(ROOT / "config" / "telegram.json"),
        },
        "broker": {"downloads_dir": str(downloads)},
    }
    scheduler_config = {
        "paths": {
            "preview_root": str(tmp / "previews"),
            "job_status_root": str(tmp / "status"),
            "state_root": str(tmp / "state"),
            "job_log": str(tmp / "job.log"),
        },
        "delivery": {"topic_routing": {}},
    }
    return RunnerContext(
        job="final_watchlist",
        config_path=ROOT / "config" / "pipeline.json",
        scheduler_config_path=ROOT / "config" / "scheduler.json",
        trade_date=date(2026, 7, 27),
        run_id="TEST-V161",
        dry_run=dry_run,
        preview_existing=False,
        no_telegram=False,
        force=False,
        debug=False,
        config=config,
        scheduler_config=scheduler_config,
        calendar_config={"holidays": [], "special_trading_days": []},
    )


class V161ReportAndBatStructureTests(unittest.TestCase):
    def test_broker_raw_is_loaded_and_copied_from_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            downloads = tmp / "Downloads"
            downloads.mkdir()
            source = downloads / "BROKER_RAW_COMBINED_2026-07-27.csv"
            pd.DataFrame(
                {
                    "SYMBOL": ["AKRA", "AKRA"],
                    "TO_DATE": ["2026-07-27", "2026-07-27"],
                    "SIDE": ["BUY", "SELL"],
                    "RANK": [1, 1],
                    "BROKER_CODE": ["YP", "PD"],
                    "NET_VALUE": [8_000_000_000, -4_000_000_000],
                    "AVG_PRICE": [1410, 1430],
                }
            ).to_csv(source, index=False)

            ctx = make_ctx(tmp, downloads)
            frame = load_broker_raw(ctx, "2026-07-27")

            self.assertEqual(len(frame), 2)
            self.assertTrue((tmp / "input" / "BROKER_RAW_LATEST.csv").exists())
            archives = list((tmp / "input" / "archive").glob("BROKER_RAW_2026-07-27_*.csv"))
            self.assertEqual(len(archives), 1)

    def test_broker_raw_dry_run_does_not_copy_download(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            downloads = tmp / "Downloads"
            downloads.mkdir()
            pd.DataFrame(
                {
                    "SYMBOL": ["AKRA"],
                    "TO_DATE": ["2026-07-27"],
                    "SIDE": ["BUY"],
                    "RANK": [1],
                    "BROKER_CODE": ["YP"],
                    "NET_VALUE": [8_000_000_000],
                    "AVG_PRICE": [1410],
                }
            ).to_csv(downloads / "BROKER_RAW_COMBINED_2026-07-27.csv", index=False)

            ctx = make_ctx(tmp, downloads, dry_run=True)
            frame = load_broker_raw(ctx, "2026-07-27")

            self.assertEqual(len(frame), 1)
            self.assertFalse((tmp / "input" / "BROKER_RAW_LATEST.csv").exists())

    def test_root_only_contains_daily_bat_files_and_v17_launcher(self) -> None:
        expected = {
            "RUN_SDE.bat",
            "RUN_MARKET_OUTLOOK.bat",
            "RUN_POST_MARKET.bat",
            "RUN_FINAL_WATCHLIST.bat",
            "CHECK_SDE_STATUS.bat",
            "START_SDE_SWING.bat",
        }
        actual = {path.name for path in ROOT.glob("*.bat")}
        self.assertEqual(actual, expected)

    def test_control_panel_uses_blocked_if_statements(self) -> None:
        text = (ROOT / "RUN_SDE.bat").read_text(encoding="utf-8-sig")
        self.assertNotIn("& goto MENU", text)
        self.assertIn('if "%MENU_CHOICE%"=="1" (', text)
        maintenance = (ROOT / "maintenance" / "MAINTENANCE_MENU.bat").read_text(encoding="utf-8-sig")
        self.assertNotIn("& goto MENU", maintenance)

    def test_scheduler_xml_targets_noninteractive_scheduler_launchers(self) -> None:
        project = Path(r"C:\\SDE_SWING")
        for _filename, description, start, limit, bat_name in TASKS:
            tree = build_task(project, description, start, limit, bat_name)
            command = tree.getroot().find(f".//{{{NS}}}Command")
            arguments = tree.getroot().find(f".//{{{NS}}}Arguments")
            workdir = tree.getroot().find(f".//{{{NS}}}WorkingDirectory")
            self.assertIsNotNone(command)
            self.assertIsNotNone(workdir)
            action_text = " ".join(filter(None, [command.text, arguments.text if arguments is not None else None]))
            self.assertIn("scheduler", action_text)
            self.assertIn("SCHEDULE_", action_text)
            self.assertEqual(workdir.text, str(project))


if __name__ == "__main__":
    unittest.main()
