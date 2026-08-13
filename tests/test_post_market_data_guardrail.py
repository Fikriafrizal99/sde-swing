from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from modules.historical_downloader.historical_downloader import DownloadResult, REPAIR_OVERLAP
from modules.historical_downloader.post_market_resilient_downloader import summarize_manifest
from modules.technical_feature_engine.post_market_validated_runner import (
    build_validated_input,
    select_current_symbols,
)


EXPECTED = date(2026, 8, 10)


def downloader_args() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="TEST-POST-MARKET",
        data_source="LIVE",
        exclude_symbols="",
        batch_size=50,
        max_workers=4,
        start=None,
        end=None,
        allow_partial_daily_candle=False,
        daily_candle_policy="LAST_CLOSED_CANDLE",
        yahoo_failure_policy="STOP",
    )


def current_result(symbol: str) -> DownloadResult:
    return DownloadResult(
        symbol=symbol,
        ticker=f"{symbol}.JK",
        status="UNCHANGED_ALREADY_CURRENT",
        refresh_action=REPAIR_OVERLAP,
        local_latest_valid_date=EXPECTED.isoformat(),
        latest_date_before=EXPECTED.isoformat(),
        latest_date_after=EXPECTED.isoformat(),
        latest_valid_close_date=EXPECTED.isoformat(),
        expected_closed_date=EXPECTED.isoformat(),
        provider_status="success",
        network_request_performed=True,
        live_response_received=True,
        rows_before=400,
        rows_after=400,
        file_exists=True,
    )


def failed_result(symbol: str) -> DownloadResult:
    return DownloadResult(
        symbol=symbol,
        ticker=f"{symbol}.JK",
        status="FAILED",
        refresh_action=REPAIR_OVERLAP,
        expected_closed_date=EXPECTED.isoformat(),
        provider_status="failed",
        network_request_performed=True,
        live_response_received=False,
        retry_count=3,
        rows_before=400,
        rows_after=0,
        file_exists=True,
        message="Yahoo mengembalikan data kosong",
    )


class PostMarketCoverageGuardrailTests(unittest.TestCase):
    def summarize(self, results: list[DownloadResult], root: Path) -> dict:
        input_path = root / "universe.csv"
        input_path.write_text("Symbol\nBBCA\n", encoding="utf-8")
        return summarize_manifest(
            downloader_args(),
            input_path,
            root,
            results,
            EXPECTED,
            False,
            False,
            "",
            "2026-08-10T17:00:00+07:00",
            "2026-08-10T17:01:00+07:00",
            60.0,
            2,
        )

    def test_one_percent_failure_continues_as_partial_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            results = [current_result(f"S{i:03d}") for i in range(99)] + [failed_result("FAIL")]
            manifest = self.summarize(results, Path(temp))
        self.assertEqual(manifest["Data_Quality_Status"], "PARTIAL_COVERAGE")
        self.assertEqual(manifest["Refresh_Status"], "SUCCESS_WITH_WARNING")
        self.assertEqual(manifest["Valid_Closed_Symbol_Count"], 99)
        self.assertEqual(manifest["Valid_Symbol_Coverage_Ratio"], 0.99)
        self.assertTrue(manifest["Failure_Tolerance_Applied"])
        self.assertIn("FAIL", manifest["Warning"])

    def test_three_percent_failure_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            results = [current_result(f"S{i:03d}") for i in range(97)] + [
                failed_result("F1"), failed_result("F2"), failed_result("F3")
            ]
            manifest = self.summarize(results, Path(temp))
        self.assertEqual(manifest["Data_Quality_Status"], "PROVIDER_FAILED")
        self.assertFalse(manifest["Failure_Tolerance_Applied"])
        self.assertEqual(manifest["Valid_Symbol_Coverage_Ratio"], 0.97)


class ValidatedTechnicalInputTests(unittest.TestCase):
    def manifest(self) -> dict:
        return {
            "Data_Quality_Status": "PARTIAL_COVERAGE",
            "Latest_Expected_Trading_Date": "2026-08-10",
            "Valid_Symbol_Coverage_Ratio": 2 / 3,
            "Symbol_Plans": [
                {"symbol": "BBCA", "status": "UPDATED", "local_last_date_after": "2026-08-10"},
                {"symbol": "TLKM", "status": "ALREADY_CURRENT", "local_last_date_after": "2026-08-10"},
                {"symbol": "ASII", "status": "FAILED", "local_last_date_after": ""},
            ],
        }

    def test_selector_keeps_only_current_successful_symbols(self) -> None:
        self.assertEqual(select_current_symbols(self.manifest()), ["BBCA", "TLKM"])

    def test_validated_input_ignores_old_files_not_in_current_universe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            input_dir = root / "by_symbol"
            manifest_dir = root / "manifests"
            input_dir.mkdir()
            manifest_dir.mkdir()
            canonical_row = "Date,Open,High,Low,Close,Volume\n2026-08-10,100,105,95,102,1000000\n"
            for symbol in ("BBCA", "TLKM", "ASII", "OLDX"):
                (input_dir / f"{symbol}.csv").write_text(canonical_row, encoding="utf-8")

            validated, audit = build_validated_input(
                input_dir,
                self.manifest(),
                "TEST-RUN",
                manifest_dir,
            )
            files = sorted(path.stem for path in validated.glob("*.csv"))

        self.assertEqual(files, ["BBCA", "TLKM"])
        self.assertEqual(audit["Omitted_Not_Current"], ["ASII"])
        self.assertEqual(audit["Ignored_Not_In_Current_Universe"], ["OLDX"])


if __name__ == "__main__":
    unittest.main()
