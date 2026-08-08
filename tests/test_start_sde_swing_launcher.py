from __future__ import annotations

import re
import unittest
from pathlib import Path

from run_sde_job_integrated import SUPPORTED_JOBS, _expected_nonreportable_warning


ROOT = Path(__file__).resolve().parents[1]
BAT_PATH = ROOT / "RUN_SDE.bat"


class UnifiedSdeLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = BAT_PATH.read_text(encoding="utf-8-sig")
        cls.lower = cls.text.lower()

    def test_launcher_uses_repository_root_and_shared_python_command(self) -> None:
        self.assertIn('cd /d "%~dp0"', self.lower)
        self.assertIn("call tools\\set_python_cmd.bat", self.lower)
        self.assertIn("%sde_python_cmd%", self.lower)
        self.assertNotRegex(self.text, r"(?i)[a-z]:\\users\\")
        self.assertFalse((ROOT / "START_SDE_SWING.bat").exists())

    def test_engine_submenus_use_integrated_runner_and_valid_jobs(self) -> None:
        files = [
            ROOT / "RUN_MARKET_OUTLOOK.bat",
            ROOT / "RUN_POST_MARKET.bat",
            ROOT / "RUN_FINAL_WATCHLIST.bat",
            ROOT / "maintenance" / "DAILY_OPERATIONS_MENU.bat",
            ROOT / "maintenance" / "BROKER_MENU.bat",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8-sig") for path in files)
        jobs = re.findall(r"--job\s+([a-z_]+)", combined, flags=re.IGNORECASE)
        self.assertTrue(jobs)
        self.assertTrue(set(jobs).issubset(set(SUPPORTED_JOBS)))
        self.assertIn("run_sde_job_integrated.py", combined)
        self.assertNotIn("run_sde_job.py --job", combined.lower())
        self.assertNotIn("master_pipeline.py", combined.lower())

    def test_referenced_runtime_files_exist(self) -> None:
        for relative in (
            "run_sde_job_integrated.py",
            "tools/print_job_status.py",
            "modules/telegram/telegram_bot.py",
            "maintenance/DAILY_OPERATIONS_MENU.bat",
            "maintenance/BROKER_MENU.bat",
            "maintenance/PORTFOLIO_MENU.bat",
            "maintenance/SYSTEM_MENU.bat",
        ):
            self.assertTrue((ROOT / relative).exists(), relative)

    def test_preview_existing_uses_canonical_flag_and_disables_delivery(self) -> None:
        market = (ROOT / "RUN_MARKET_OUTLOOK.bat").read_text(encoding="utf-8-sig").lower()
        post = (ROOT / "RUN_POST_MARKET.bat").read_text(encoding="utf-8-sig").lower()
        final = (ROOT / "RUN_FINAL_WATCHLIST.bat").read_text(encoding="utf-8-sig").lower()
        for source in (market, post, final):
            self.assertIn("--preview-existing", source)
            self.assertIn("--no-telegram", source)

    def test_telegram_test_is_separate_from_engine(self) -> None:
        source = (ROOT / "maintenance" / "TEST_TELEGRAM.bat").read_text(encoding="utf-8-sig")
        self.assertIn("telegram_bot.py", source)
        self.assertNotIn("run_sde_job_integrated.py", source)

    def test_status_helper_keeps_split_engine_report_delivery_states(self) -> None:
        status_helper = (ROOT / "tools/print_job_status.py").read_text(encoding="utf-8").lower()
        for token in ("engine_status=", "report_status=", "delivery_status=", "overall_status="):
            self.assertIn(token, status_helper)

    def test_invalid_menu_returns_safely(self) -> None:
        self.assertIn("Pilihan tidak valid", self.text)
        self.assertIn("goto MENU", self.text)

    def test_full_manual_prepares_data_before_market_report_stage(self) -> None:
        source = (ROOT / "run_sde_job.py").read_text(encoding="utf-8")
        stage_block = source.split("stage_jobs = (", 1)[1].split("stage_results", 1)[0]
        self.assertLess(stage_block.index('(\"global_market_preparation\"'), stage_block.index('(\"post_market\"'))
        self.assertLess(stage_block.index('(\"post_market\"'), stage_block.index('(\"market_outlook\"'))
        self.assertIn("_prepared_global_snapshot", source)

    def test_engine_early_exit_records_split_statuses(self) -> None:
        source = (ROOT / "run_sde_job_integrated.py").read_text(encoding="utf-8")
        self.assertIn('"report_status": "NOT_RUN_ENGINE_EXIT"', source)
        self.assertIn('"delivery_status": "SKIPPED_ENGINE_NOT_SUCCESSFUL"', source)

    def test_telegram_missing_credentials_use_skipped_exit(self) -> None:
        source = (ROOT / "modules/telegram/telegram_bot.py").read_text(encoding="utf-8")
        self.assertIn('append_log(log_path, "test", "SKIPPED_NOT_CONFIGURED"', source)
        self.assertIn("return 10", source)

    def test_empty_report_payload_never_calls_delivery(self) -> None:
        source = (ROOT / "run_sde_job_integrated.py").read_text(encoding="utf-8")
        self.assertIn("if not payloads:", source)
        self.assertIn("REPORT_PAYLOAD_EMPTY_TELEGRAM_NOT_CALLED", source)

    def test_missing_sector_metadata_is_warning_not_runtime_failure(self) -> None:
        errors = ["SECTOR_ROTATION_STATUS:INSUFFICIENT_DATA", "FIELD_EMPTY:sector_rotation.coverage"]
        self.assertTrue(_expected_nonreportable_warning("market_outlook", "SUCCESS_WITH_WARNING", errors))
        self.assertFalse(_expected_nonreportable_warning("market_outlook", "SUCCESS", errors))


if __name__ == "__main__":
    unittest.main()
