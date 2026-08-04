from __future__ import annotations

import re
import unittest
from pathlib import Path

from run_sde_job_integrated import SUPPORTED_JOBS, _expected_nonreportable_warning


ROOT = Path(__file__).resolve().parents[1]
BAT_PATH = ROOT / "START_SDE_SWING.bat"


class StartSdeSwingLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = BAT_PATH.read_text(encoding="utf-8-sig")
        cls.lower = cls.text.lower()

    def test_launcher_uses_repository_root_and_one_python_command(self) -> None:
        self.assertIn('cd /d "%~dp0"', self.lower)
        self.assertIn('set "python_cmd=%sde_python_cmd%"', self.lower)
        self.assertNotRegex(self.text, r"(?i)[a-z]:\\users\\")

    def test_all_engine_menus_use_integrated_runner_and_valid_jobs(self) -> None:
        jobs = re.findall(r'call\s+:RUN_JOB\s+"([a-z_]+)"', self.text, flags=re.IGNORECASE)
        self.assertTrue(jobs)
        self.assertTrue(set(jobs).issubset(set(SUPPORTED_JOBS)))
        self.assertIn("run_sde_job_integrated.py --job !JOB_NAME!", self.text)
        self.assertNotIn("master_pipeline.py", self.lower)
        self.assertNotIn("run_sde_job.py --job", self.lower)

    def test_referenced_runtime_files_exist(self) -> None:
        for relative in (
            "run_sde_job_integrated.py",
            "tools/print_job_status.py",
            "modules/telegram/telegram_bot.py",
        ):
            self.assertTrue((ROOT / relative).exists(), relative)

    def test_exit_code_mapping_does_not_call_expected_states_failed(self) -> None:
        expected = {
            "0": "[OK] SUCCESS",
            "10": "[SKIPPED]",
            "20": "[WAITING_DATA]",
            "30": "[DUPLICATE]",
            "40": "[LOCKED]",
            "50": "[DELIVERY_FAILED]",
        }
        for code, label in expected.items():
            self.assertIn(f'if "%RESULT_CODE%"=="{code}"', self.text)
            self.assertIn(label, self.text)
        self.assertIn("[OK WITH WARNING]", self.text)

    def test_preview_existing_uses_canonical_flag_and_disables_delivery(self) -> None:
        self.assertIn('"--preview-existing --no-telegram"', self.lower)

    def test_telegram_test_is_separate_from_engine(self) -> None:
        section = self.text.split(":TELEGRAM_TEST", 1)[1].split(":RUN_JOB", 1)[0]
        self.assertIn("modules\\telegram\\telegram_bot.py", section)
        self.assertNotIn("run_sde_job_integrated.py", section)
        self.assertIn("Engine tidak dijalankan ulang", section)
        self.assertIn("[SKIPPED_NOT_CONFIGURED]", section)

    def test_logging_records_command_branch_python_exit_and_status(self) -> None:
        for token in ("selected_menu=", "command=", "branch=", "python=", "exit_code=", "latest_manifest="):
            self.assertIn(token, self.lower)
        status_helper = (ROOT / "tools/print_job_status.py").read_text(encoding="utf-8").lower()
        for token in ("engine_status=", "report_status=", "delivery_status=", "overall_status="):
            self.assertIn(token, status_helper)
        self.assertIn("logs\\start_sde_swing.log", self.lower)

    def test_invalid_menu_returns_safely(self) -> None:
        self.assertIn("status=INVALID_SELECTION", self.text)
        self.assertIn("goto MENU", self.text)

    def test_full_manual_prepares_data_before_market_report_stage(self) -> None:
        source = (ROOT / "run_sde_job.py").read_text(encoding="utf-8")
        stage_block = source.split("stage_jobs = (", 1)[1].split("stage_results", 1)[0]
        self.assertLess(stage_block.index('("global_market_preparation"'), stage_block.index('("post_market"'))
        self.assertLess(stage_block.index('("post_market"'), stage_block.index('("market_outlook"'))
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
