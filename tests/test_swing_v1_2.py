from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules.historical_downloader.historical_downloader as hist_dl
from modules.historical_downloader.historical_downloader import (
    FULL_BACKFILL,
    INCREMENTAL_UPDATE,
    REPAIR_OVERLAP,
    SKIP_ALREADY_CURRENT,
    build_refresh_plan,
    build_result,
    current_candle_written_before_close,
    fetch_batch,
    fetch_live_group_with_fallback,
    merge_history,
    should_write_history,
)
from modules.broker_bridge.wait_for_broker_export import inspect as inspect_broker
from modules.broker_bridge.broker_navigator_export import export_symbols, write_symbol_csv
from modules.backtesting.backtest_engine import load_signals
from modules.candidate_selector.technical_candidate_selector import find_column as find_candidate_column, score_candidates
from modules.exit_engine.exit_engine import build_entry_plan, load_decisions, update_active_trade
from modules.database.swing_history_db import archive_prices, connect, init_schema
from modules.telegram.swing_report_builder import build_all_reports, chunk_blocks, write_messages


PYTHON = sys.executable


def broker_summary(symbols: list[str], broker_date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "FROM_DATE": [broker_date] * len(symbols),
            "TO_DATE": [broker_date] * len(symbols),
            "EMITEN": symbols,
            "TOTAL_BUY": [1000] * len(symbols),
            "TOTAL_SELL": [500] * len(symbols),
            "NET_FLOW": [500] * len(symbols),
            "TOP_BUYER_1": ["YP"] * len(symbols),
            "TOP_SELLER_1": ["PD"] * len(symbols),
            "BUYER_CONCENTRATION": [0.4] * len(symbols),
            "SELLER_CONCENTRATION": [0.2] * len(symbols),
        }
    )


def downloader_args(**overrides: object) -> SimpleNamespace:
    defaults = {
        "force_refresh": False,
        "repair": False,
        "full_backfill": False,
        "incremental_overlap_days": 5,
        "repair_overlap_sessions": 5,
        "market_holiday": [],
        "special_trading_day": [],
        "start": None,
        "end": None,
        "period": "2y",
        "interval": "1d",
        "retries": 1,
        "batch_enabled": True,
        "batch_size": 50,
        "max_workers": 4,
        "request_delay_seconds": 0,
        "pause": 0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def ohlcv_rows(symbol: str, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Symbol": [symbol] * len(dates),
            "Ticker": [f"{symbol}.JK"] * len(dates),
            "Date": dates,
            "Open": [9000] * len(dates),
            "High": [9100] * len(dates),
            "Low": [8900] * len(dates),
            "Close": [9050] * len(dates),
            "Adj Close": [9050] * len(dates),
            "Volume": [1000000] * len(dates),
        }
    )


def fake_batch_frame(tickers: list[str]) -> pd.DataFrame:
    fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    columns = pd.MultiIndex.from_tuples((ticker, field) for ticker in tickers for field in fields)
    values = []
    for _ticker in tickers:
        values.extend([9000, 9100, 8900, 9050, 9050, 1000000])
    return pd.DataFrame([values], index=pd.to_datetime(["2026-07-20"]), columns=columns)


class SwingV12Tests(unittest.TestCase):
    def test_static_fixture_directories_are_present(self) -> None:
        required = [
            "tests/fixtures/yahoo/valid_closed_candle",
            "tests/fixtures/yahoo/partial_latest_candle",
            "tests/fixtures/yahoo/provider_failed",
            "tests/fixtures/yahoo/no_new_valid_candle",
            "tests/fixtures/yahoo/stale_data",
            "tests/fixtures/broker/complete_30",
            "tests/fixtures/broker/partial_15_part_1",
            "tests/fixtures/broker/partial_15_part_2",
            "tests/fixtures/broker/date_mismatch",
            "tests/fixtures/broker/missing_symbols",
            "tests/fixtures/broker/unexpected_symbols",
            "tests/fixtures/broker/duplicate_symbols",
            "tests/fixtures/broker/incompatible_batch",
        ]
        for rel in required:
            self.assertTrue((ROOT / rel).exists(), rel)

    def test_historical_universe_excludes_non_equity_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "universe.csv"
            pd.DataFrame({"Symbol": ["BBCA", "IHSG", "BRENT", "OIL", "XAU", "TLKM"]}).to_csv(source, index=False)
            symbols = hist_dl.load_symbols(source, None, None, "IHSG,BRENT,OIL,XAU")
            self.assertEqual(symbols, ["BBCA", "TLKM"])

    def test_yahoo_first_run_plans_full_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "BBCA.csv"
            plan = build_refresh_plan("BBCA", destination, downloader_args(), date(2026, 7, 20))
            self.assertEqual(plan.refresh_action, FULL_BACKFILL)
            self.assertEqual(plan.download_start_date, "")
            self.assertEqual(plan.download_end_date, "2026-07-21")
            self.assertEqual(plan.rows_before, 0)

    def test_yahoo_already_current_skips_without_network_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "BBCA.csv"
            ohlcv_rows("BBCA", ["2026-07-20"]).to_csv(destination, index=False)
            plan = build_refresh_plan("BBCA", destination, downloader_args(), date(2026, 7, 20))
            self.assertEqual(plan.refresh_action, SKIP_ALREADY_CURRENT)
            self.assertEqual(plan.download_start_date, "")
            self.assertEqual(plan.download_end_date, "")

    def test_yahoo_future_candle_is_ignored_until_expected_session_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "INCO.csv"
            ohlcv_rows("INCO", ["2026-08-04", "2026-08-05"]).to_csv(destination, index=False)
            plan = build_refresh_plan("INCO", destination, downloader_args(), date(2026, 8, 4))
            self.assertEqual(plan.local_latest_valid_date, "2026-08-04")
            self.assertTrue(plan.history_sanitized)
            self.assertEqual(plan.existing["Date"].dt.date.max().isoformat(), "2026-08-04")
            self.assertTrue(should_write_history(plan, plan.existing))

    def test_yahoo_current_candle_written_before_close_is_repaired(self) -> None:
        self.assertTrue(
            current_candle_written_before_close(
                "2026-08-05",
                date(2026, 8, 5),
                "2026-08-05T09:26:57",
                "16:15",
            )
        )
        self.assertFalse(
            current_candle_written_before_close(
                "2026-08-05",
                date(2026, 8, 5),
                "2026-08-05T16:20:00",
                "16:15",
            )
        )

    def test_yahoo_daily_incremental_requests_first_missing_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "BBCA.csv"
            ohlcv_rows("BBCA", ["2026-07-17"]).to_csv(destination, index=False)
            plan = build_refresh_plan("BBCA", destination, downloader_args(), date(2026, 7, 20))
            self.assertEqual(plan.refresh_action, INCREMENTAL_UPDATE)
            self.assertEqual(plan.local_latest_valid_date, "2026-07-17")
            self.assertEqual(plan.download_start_date, "2026-07-20")
            self.assertEqual(plan.download_end_date, "2026-07-21")

    def test_yahoo_multiple_run_same_day_second_run_skips_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            universe = tmp_path / "universe.csv"
            pd.DataFrame({"Symbol": ["BBCA"]}).to_csv(universe, index=False)
            output = tmp_path / "historical"
            manifests = tmp_path / "manifests"
            base_cmd = [
                PYTHON,
                str(ROOT / "modules/historical_downloader/historical_downloader.py"),
                str(universe),
                "--output",
                str(output),
                "--manifest-dir",
                str(manifests),
                "--data-source",
                "FIXTURE",
                "--test-fixture",
                "--fixture-dir",
                str(ROOT / "tests/fixtures/yahoo/valid_closed_candle"),
                "--market-close",
                "23:59",
                "--evaluation-datetime",
                "2026-07-20T23:59:00",
                "--pause",
                "0",
            ]
            first = subprocess.run(base_cmd + ["--run-id", "SWING-SAMEDAY-1"], cwd=ROOT, capture_output=True, text=True)
            second = subprocess.run(base_cmd + ["--run-id", "SWING-SAMEDAY-2"], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            manifest = json.loads((manifests / "YAHOO_REFRESH_MANIFEST_SWING-SAMEDAY-2.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["Refresh_Mode"], SKIP_ALREADY_CURRENT)
            self.assertEqual(manifest["Skipped_Count"], 1)
            self.assertEqual(manifest["Network_Request_Symbol_Count"], 0)

    def test_yahoo_retry_only_problem_symbols_are_planned_for_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ohlcv_rows("BBCA", ["2026-07-20"]).to_csv(tmp_path / "BBCA.csv", index=False)
            plans = [
                build_refresh_plan("BBCA", tmp_path / "BBCA.csv", downloader_args(), date(2026, 7, 20)),
                build_refresh_plan("TLKM", tmp_path / "TLKM.csv", downloader_args(), date(2026, 7, 20)),
            ]
            self.assertEqual(plans[0].refresh_action, SKIP_ALREADY_CURRENT)
            self.assertEqual(plans[1].refresh_action, FULL_BACKFILL)
            self.assertEqual([p.symbol for p in plans if p.refresh_action != SKIP_ALREADY_CURRENT], ["TLKM"])

    def test_yahoo_partial_candle_retries_incremental_not_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "BBCA.csv"
            partial = ohlcv_rows("BBCA", ["2026-07-17", "2026-07-20"])
            partial.loc[1, "Close"] = pd.NA
            partial.loc[1, "Adj Close"] = pd.NA
            partial.to_csv(destination, index=False)
            plan = build_refresh_plan("BBCA", destination, downloader_args(repair_overlap_sessions=3), date(2026, 7, 20))
            self.assertEqual(plan.refresh_action, REPAIR_OVERLAP)
            self.assertEqual(plan.download_start_date, "2026-07-14")

    def test_yahoo_batch_download_splits_multi_symbol_response(self) -> None:
        class FakeYF:
            def __init__(self) -> None:
                self.calls: list[dict[str, object]] = []

            def download(self, **kwargs: object) -> pd.DataFrame:
                self.calls.append(kwargs)
                return fake_batch_frame(str(kwargs["tickers"]).split())

        original_yf = hist_dl.yf
        fake = FakeYF()
        hist_dl.yf = fake
        try:
            data_by_symbol, status, request_performed, live_received, retry_count, batch_count, message = fetch_batch(
                ["BBCA", "BMRI"],
                downloader_args(max_workers=2),
                start="2026-07-15",
                end="2026-07-21",
            )
        finally:
            hist_dl.yf = original_yf
        self.assertEqual(status, "success", message)
        self.assertTrue(request_performed)
        self.assertTrue(live_received)
        self.assertEqual(retry_count, 0)
        self.assertEqual(batch_count, 1)
        self.assertEqual(set(data_by_symbol), {"BBCA", "BMRI"})
        self.assertEqual(fake.calls[0]["threads"], 2)

    def test_yahoo_batch_failure_falls_back_to_individual(self) -> None:
        class FailingBatchYF:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def download(self, **kwargs: object) -> pd.DataFrame:
                tickers = str(kwargs["tickers"]).split()
                self.calls.append(str(kwargs["tickers"]))
                if len(tickers) > 1:
                    raise RuntimeError("batch boom")
                return pd.DataFrame(
                    [[9000, 9100, 8900, 9050, 9050, 1000000]],
                    index=pd.to_datetime(["2026-07-20"]),
                    columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"],
                )

        args = downloader_args(retries=1)
        plans = [
            hist_dl.RefreshPlan("BBCA", "BBCA.JK", Path("BBCA.csv"), pd.DataFrame(), False, "", "2026-07-20", FULL_BACKFILL),
            hist_dl.RefreshPlan("BMRI", "BMRI.JK", Path("BMRI.csv"), pd.DataFrame(), False, "", "2026-07-20", FULL_BACKFILL),
        ]
        original_yf = hist_dl.yf
        fake = FailingBatchYF()
        hist_dl.yf = fake
        try:
            payloads, batch_count = fetch_live_group_with_fallback(plans, args, "", "2026-07-21", "2y")
        finally:
            hist_dl.yf = original_yf
        self.assertEqual(batch_count, 1)
        self.assertEqual(set(payloads), {"BBCA", "BMRI"})
        self.assertTrue(all(payload.provider_status == "success" for payload in payloads.values()))
        self.assertEqual(len(fake.calls), 3)

    def test_yahoo_force_refresh_is_explicit_repair_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "BBCA.csv"
            ohlcv_rows("BBCA", ["2026-07-20"]).to_csv(destination, index=False)
            plan = build_refresh_plan("BBCA", destination, downloader_args(force_refresh=True), date(2026, 7, 20))
            self.assertEqual(plan.refresh_action, REPAIR_OVERLAP)
            self.assertEqual(plan.download_start_date, "2026-07-13")

    def test_yahoo_full_backfill_requires_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "BBCA.csv"
            ohlcv_rows("BBCA", ["2026-07-20"]).to_csv(destination, index=False)
            default_plan = build_refresh_plan("BBCA", destination, downloader_args(), date(2026, 7, 20))
            full_plan = build_refresh_plan("BBCA", destination, downloader_args(full_backfill=True), date(2026, 7, 20))
            self.assertEqual(default_plan.refresh_action, SKIP_ALREADY_CURRENT)
            self.assertEqual(full_plan.refresh_action, FULL_BACKFILL)

    def test_yahoo_merged_history_keeps_canonical_columns(self) -> None:
        existing = ohlcv_rows("BBCA", ["2026-07-17"])
        fresh = ohlcv_rows("BBCA", ["2026-07-20"])
        combined = merge_history(existing, fresh)
        self.assertTrue({"Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"}.issubset(combined.columns))
        self.assertEqual(combined["Date"].dt.date.max().isoformat(), "2026-07-20")

    def test_downloader_fixture_mode_is_explicitly_not_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            universe = tmp_path / "universe.csv"
            pd.DataFrame({"Symbol": ["BBCA"]}).to_csv(universe, index=False)
            output = tmp_path / "historical"
            manifests = tmp_path / "manifests"
            cmd = [
                PYTHON,
                str(ROOT / "modules/historical_downloader/historical_downloader.py"),
                str(universe),
                "--output",
                str(output),
                "--run-id",
                "SWING-TEST-FIXTURE",
                "--manifest-dir",
                str(manifests),
                "--data-source",
                "FIXTURE",
                "--test-fixture",
                "--fixture-dir",
                str(ROOT / "tests/fixtures/yahoo/valid_closed_candle"),
                "--market-close",
                "23:59",
                "--evaluation-datetime",
                "2026-07-20T23:59:00",
                "--pause",
                "0",
            ]
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            manifest = json.loads((manifests / "YAHOO_REFRESH_MANIFEST_SWING-TEST-FIXTURE.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["Provider_Mode"], "FIXTURE")
            self.assertEqual(manifest["Data_Source"], "OFFLINE_FIXTURE")
            self.assertFalse(manifest["Network_Request_Performed"])
            self.assertFalse(manifest["Live_Response_Received"])

    def test_downloader_provider_failed_fixture_records_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            universe = tmp_path / "universe.csv"
            pd.DataFrame({"Symbol": ["BBCA"]}).to_csv(universe, index=False)
            output = tmp_path / "historical"
            manifests = tmp_path / "manifests"
            cmd = [
                PYTHON,
                str(ROOT / "modules/historical_downloader/historical_downloader.py"),
                str(universe),
                "--output",
                str(output),
                "--run-id",
                "SWING-TEST-PROVIDER-FAILED",
                "--manifest-dir",
                str(manifests),
                "--data-source",
                "FIXTURE",
                "--test-fixture",
                "--fixture-dir",
                str(ROOT / "tests/fixtures/yahoo/provider_failed"),
                "--pause",
                "0",
            ]
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            manifest = json.loads((manifests / "YAHOO_REFRESH_MANIFEST_SWING-TEST-PROVIDER-FAILED.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["Provider_Mode"], "FIXTURE")
            self.assertEqual(manifest["Data_Source"], "OFFLINE_FIXTURE")
            self.assertEqual(manifest["Data_Quality_Status"], "PROVIDER_FAILED")

    def test_partial_yahoo_candle_is_not_valid_refresh(self) -> None:
        existing = pd.DataFrame(
            {
                "Symbol": ["BBCA"],
                "Ticker": ["BBCA.JK"],
                "Date": ["2026-07-17"],
                "Open": [9000],
                "High": [9100],
                "Low": [8900],
                "Close": [9050],
                "Adj Close": [9050],
                "Volume": [1000000],
            }
        )
        fresh = pd.DataFrame(
            {
                "Symbol": ["BBCA"],
                "Ticker": ["BBCA.JK"],
                "Date": ["2026-07-20"],
                "Open": [9100],
                "High": [9200],
                "Low": [9000],
                "Close": [pd.NA],
                "Adj Close": [pd.NA],
                "Volume": [1200000],
            }
        )
        combined = merge_history(existing, fresh)
        result = build_result("BBCA", existing, fresh, combined, date(2026, 7, 20), "success", True, True, False)
        self.assertEqual(result.status, "PARTIAL_CANDLE_IGNORED")
        self.assertEqual(result.latest_valid_close_date, "2026-07-17")
        self.assertEqual(result.latest_partial_date, "2026-07-20")

    def test_candidate_unchanged_hash_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            technical = tmp_path / "latest_technical_features.csv"
            df = pd.DataFrame(
                [
                    {
                        "Symbol": "BBCA",
                        "Date": "2026-07-20",
                        "Close": 1000,
                        "SMA_5": 990,
                        "SMA_20": 950,
                        "SMA_50": 900,
                        "SMA_200": 800,
                        "EMA_20": 970,
                        "RSI_14": 60,
                        "MACD": 5,
                        "MACD_Signal": 3,
                        "MACD_Hist": 2,
                        "Volume": 2000,
                        "Volume_MA_20": 1000,
                        "ATR_14": 20,
                        "ATR_14_Pct": 2,
                        "Return_5D": 2,
                        "Return_20D": 5,
                        "Return_60D": 10,
                        "Breakout_20D": 1,
                        "Technical_Regime": "bullish",
                    }
                ]
            )
            df.to_csv(technical, index=False)
            out = tmp_path / "candidates"
            cmd = [
                PYTHON,
                str(ROOT / "modules/candidate_selector/technical_candidate_selector.py"),
                str(technical),
                "--top",
                "1",
                "--min-score",
                "60",
                "--output-dir",
                str(out),
                "--run-id",
                "SWING-TEST-UNCHANGED",
                "--manifest-dir",
                str(tmp_path / "manifests"),
            ]
            first = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            second = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            manifest = json.loads((tmp_path / "manifests" / "CANDIDATE_MANIFEST_SWING-TEST-UNCHANGED.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["Candidate_Changed"])
            self.assertEqual(manifest["Reason"], "RESULT_IDENTICAL_AFTER_FRESH_RECALCULATION")

    def test_broker_coverage_accepts_complete_15_plus_15_union(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BROKER_SUMMARY_COMBINED_20260720.csv"
            symbols = [f"AA{i:02d}" for i in range(15)] + [f"BB{i:02d}" for i in range(15)]
            broker_summary(symbols, "2026-07-20").to_csv(path, index=False)
            ok, message, info = inspect_broker(path, symbols, 1.0)
            self.assertTrue(ok, message)
            self.assertEqual(info["matched"], 30)
            self.assertEqual(info["coverage"], 1.0)

    def test_static_broker_edge_fixtures_validate_expected_findings(self) -> None:
        expected = ["BBCA", "BMRI"]
        missing = ROOT / "tests/fixtures/broker/missing_symbols/BROKER_SUMMARY_COMBINED_20260720.csv"
        ok, message, info = inspect_broker(missing, expected, 1.0)
        self.assertFalse(ok)
        self.assertIn("coverage", message)
        self.assertEqual(info["missing_symbols"], ["BMRI"])

        unexpected = ROOT / "tests/fixtures/broker/unexpected_symbols/BROKER_SUMMARY_COMBINED_20260720.csv"
        ok, message, info = inspect_broker(unexpected, expected, 1.0)
        self.assertTrue(ok, message)
        self.assertIn("ZZZZ", info["unexpected_symbols"])

        duplicate = ROOT / "tests/fixtures/broker/duplicate_symbols/BROKER_SUMMARY_COMBINED_20260720.csv"
        ok, message, info = inspect_broker(duplicate, expected, 1.0)
        self.assertTrue(ok, message)
        self.assertIn("BBCA", info["duplicate_symbols"])

        bad = ROOT / "tests/fixtures/broker/incompatible_batch/BROKER_SUMMARY_BAD_COLUMNS.csv"
        ok, message, info = inspect_broker(bad, expected, 1.0)
        self.assertFalse(ok)
        self.assertIn("kolom wajib", message)

    def test_broker_navigator_locked_output_uses_alternate_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / "BROKER_NAVIGATOR_SYMBOLS.csv"
            calls: list[Path] = []

            def locked_once(path: Path, symbols: list[str]) -> None:
                calls.append(path)
                if path == output:
                    raise PermissionError("simulated Windows lock")
                write_symbol_csv(path, symbols)

            actual, warning = export_symbols(output, ["BBCA"], "SWING-LOCK-TEST", writer=locked_once)
            alternate = tmp_path / "BROKER_NAVIGATOR_SYMBOLS_SWING-LOCK-TEST.csv"
            self.assertEqual(actual, alternate)
            self.assertTrue(alternate.exists())
            self.assertTrue(warning)
            self.assertEqual(calls, [output, alternate])

    def test_manual_broker_file_records_date_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            symbols = ["BBCA", "BMRI"]
            symbols_path = tmp_path / "broker_symbols.csv"
            pd.DataFrame({"Symbol": symbols}).to_csv(symbols_path, index=False)
            manual = tmp_path / "BROKER_SUMMARY_COMBINED_20260718.csv"
            broker_summary(symbols, "2026-07-18").to_csv(manual, index=False)
            output = tmp_path / "BROKER_SUMMARY_LATEST.csv"
            manifests = tmp_path / "manifests"
            cmd = [
                PYTHON,
                str(ROOT / "modules/broker_bridge/wait_for_broker_export.py"),
                "--symbols",
                str(symbols_path),
                "--downloads",
                str(tmp_path),
                "--output",
                str(output),
                "--broker-date-policy",
                "manual",
                "--manual-broker-file",
                str(manual),
                "--expected-broker-date",
                "2026-07-20",
                "--run-id",
                "SWING-TEST-BROKER",
                "--manifest-dir",
                str(manifests),
            ]
            result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            manifest = json.loads((manifests / "BROKER_MANIFEST_SWING-TEST-BROKER.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["BROKER_DATE_OVERRIDE"])
            self.assertEqual(manifest["BROKER_DATE_SELECTED"], "2026-07-18")

    def test_database_deduplicates_market_prices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            hist = tmp_path / "historical"
            hist.mkdir()
            pd.DataFrame(
                {
                    "Symbol": ["BBCA"],
                    "Ticker": ["BBCA.JK"],
                    "Date": ["2026-07-20"],
                    "Open": [9000],
                    "High": [9100],
                    "Low": [8900],
                    "Close": [9050],
                    "Adj Close": [9050],
                    "Volume": [1000000],
                }
            ).to_csv(hist / "BBCA.csv", index=False)
            conn = connect(tmp_path / "sde_swing_history.db")
            init_schema(conn)
            archive_prices(conn, hist)
            archive_prices(conn, hist)
            count = conn.execute("SELECT COUNT(*) FROM market_prices_daily").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)

    def test_telegram_dry_run_files_and_no_emoji_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            decisions = pd.DataFrame(
                {
                    "Symbol": ["BBCA"],
                    "Decision_V3": ["STRONG BUY"],
                    "Final_Score_V3": [88.5],
                    "Technical_Score_Final": [86],
                    "Broker_Score": [82],
                    "Broker_Confirmation": ["STRONG ACCUMULATION"],
                }
            )
            manifest = {
                "Run_ID": "SWING-TEST-TELEGRAM",
                "Pipeline_Status": "SUCCESS",
                "Technical_Date": "2026-07-20",
                "Candidate_Count": 1,
                "Candidate_Changed": True,
                "Broker_Date": "2026-07-20",
                "Broker_Coverage": "1/1 - 100%",
                "Data_Quality_Status": "VALID",
            }
            messages = build_all_reports(manifest, decisions, pd.DataFrame(), {}, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), use_emoji=False)
            paths = write_messages(messages, tmp_path / "preview")
            self.assertTrue(paths)
            content = paths[0].read_text(encoding="utf-8")
            self.assertIn("SDE SWING - PIPELINE SELESAI", content)
            self.assertNotIn("✅", content)
            self.assertIn("Belum tersedia", "\n".join(p.read_text(encoding="utf-8") for p in paths))

    def test_chunking_keeps_stock_blocks_intact(self) -> None:
        blocks = ["BBCA\nFinal Score: 88", "BMRI\nFinal Score: 87", "TLKM\nFinal Score: 86"]
        chunks = chunk_blocks("REKAP WATCHLIST", ["Ringkasan"], blocks, limit=60, use_emoji=False)
        joined = "\n---\n".join(chunks)
        self.assertIn("BBCA\nFinal Score: 88", joined)
        self.assertIn("BMRI\nFinal Score: 87", joined)
        self.assertIn("TLKM\nFinal Score: 86", joined)

    def test_backtest_load_signals_handles_decision_column_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FINAL_DECISION_V3.csv"
            pd.DataFrame(
                {
                    "Rank_V3": [1],
                    "Symbol": ["BBCA"],
                    "Decision_V3": ["STRONG BUY"],
                    "Decision": ["BUY"],
                    "Final_Score_V3": [88.5],
                    "Date": ["2026-07-20"],
                }
            ).to_csv(path, index=False)
            signals = load_signals(path)
            self.assertEqual(signals["Decision"].iloc[0], "STRONG BUY")
            self.assertEqual(signals["Symbol"].iloc[0], "BBCA")


    def test_candidate_contract_prefers_252_day_high_and_close_slope(self) -> None:
        df = pd.DataFrame({
            "Symbol": ["BBCA"],
            "Distance_High_52_Pct": [-5],
            "Distance_High_252_Pct": [-25],
            "Slope_Close_20": [2],
            "Slope_SMA20_10": [-2],
        })
        self.assertEqual(find_candidate_column(df, "dist_high"), "Distance_High_252_Pct")
        self.assertEqual(find_candidate_column(df, "trend_slope"), "Slope_Close_20")

    def test_candidate_full_ranking_places_pass_before_filtered(self) -> None:
        df = pd.DataFrame([
            {"Symbol": "PASS", "Close": 100, "SMA_5": 101, "SMA_20": 99, "SMA_50": 90, "SMA_200": 80,
             "EMA_20": 99, "RSI_14": 60, "MACD": 2, "MACD_Signal": 1, "MACD_Hist": 1,
             "Volume": 2000, "Volume_MA_20": 1000, "ATR_14_Pct": 2, "Turnover_MA_20": 2e9},
            {"Symbol": "FAIL", "Close": 100, "SMA_5": 101, "SMA_20": 99, "SMA_50": 90, "SMA_200": 80,
             "EMA_20": 99, "RSI_14": 90, "MACD": 2, "MACD_Signal": 1, "MACD_Hist": 1,
             "Volume": 2000, "Volume_MA_20": 1000, "ATR_14_Pct": 2, "Turnover_MA_20": 2e9},
        ])
        ranked = score_candidates(df, 1e9)
        self.assertEqual(ranked.iloc[0]["Candidate_Status"], "PASS")
        self.assertEqual(ranked.iloc[-1]["Candidate_Status"], "FILTERED")

    def test_exit_loader_uses_v3_columns_without_duplicate_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "decision.csv"
            pd.DataFrame({
                "Symbol": ["BBCA"],
                "Decision_V3": ["STRONG BUY"],
                "Decision": ["AVOID"],
                "Final_Score_V3": [88.5],
                "Final_Score": [10],
                "Technical_Score_Final": [86],
                "Technical_Score": [20],
            }).to_csv(path, index=False)
            loaded = load_decisions(path)
            self.assertFalse(loaded.columns.duplicated().any())
            self.assertEqual(loaded.loc[0, "Decision"], "STRONG BUY")
            self.assertEqual(float(loaded.loc[0, "Final_Score"]), 88.5)
            self.assertEqual(float(loaded.loc[0, "Technical_Score"]), 86)

    def test_exit_plan_marks_bear_market_as_conditional_trigger(self) -> None:
        dates = pd.date_range("2026-06-01", periods=30, freq="B")
        px = pd.DataFrame({
            "Date": dates,
            "Open": range(100, 130),
            "High": [x + 1 for x in range(100, 130)],
            "Low": [x - 1 for x in range(100, 130)],
            "Close": range(100, 130),
            "ATR14": [2.0] * 30,
            "EMA20": range(99, 129),
        })
        row = pd.Series({"Symbol": "BBCA", "Decision": "BUY", "Market_Regime": "BEAR", "Liquidity_Class": "LIQUID"})
        plan = build_entry_plan(row, px, 1.0, 2.0, 7.0, 20)
        self.assertEqual(plan["Plan_Status"], "CONDITIONAL")
        self.assertEqual(plan["Decision_Status_Final"], "BUY ON TRIGGER")
        self.assertEqual(plan["Rejection_Reason"], "BEAR_MARKET_TRIGGER_REQUIRED")
        self.assertEqual(plan["Final_Decision_Owner"], "DECISION_ENGINE")
        self.assertEqual(plan["Max_Hold_Days"], 20)

    def test_decision_downgrade_closes_active_trade(self) -> None:
        trade = pd.Series({
            "Symbol": "BBCA", "Entry_Price": 100, "Initial_Stop": 90, "Current_Stop": 90,
            "Target_1": 120, "Target_2": 130, "Highest_Close": 105, "Holding_Days": 2,
        })
        decision = pd.Series({"Decision": "SPECULATIVE", "Broker_Confirmation": "NEUTRAL"})
        px = pd.DataFrame([{"Date": pd.Timestamp("2026-07-20"), "Close": 105, "Low": 103, "High": 107, "EMA20": 100, "ATR14": 2}])
        updated, alert = update_active_trade(trade, decision, px, 20)
        self.assertEqual(updated["Status"], "CLOSED")
        self.assertIsNotNone(alert)
        self.assertIn("DECISION_DOWNGRADE_SPECULATIVE", updated["Exit_Reason"])

    def test_stop_has_conservative_priority_when_target_hits_same_bar(self) -> None:
        trade = pd.Series({
            "Symbol": "BBCA", "Entry_Price": 100, "Initial_Stop": 95, "Current_Stop": 95,
            "Target_1": 105, "Target_2": 110, "Highest_Close": 100, "Holding_Days": 0,
        })
        px = pd.DataFrame([{"Date": pd.Timestamp("2026-07-20"), "Close": 102, "Low": 94, "High": 111, "EMA20": 100, "ATR14": 2}])
        updated, _ = update_active_trade(trade, pd.Series({"Decision": "BUY", "Broker_Confirmation": "NEUTRAL"}), px, 20)
        self.assertEqual(float(updated["Exit_Price"]), 95)
        self.assertIn("AMBIGUOUS_BAR_STOP_PRIORITY", updated["Exit_Reason"])

    def test_decision_engine_v12_formula_and_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "v2.csv"
            pd.DataFrame([{
                "Symbol": "BBCA", "Turnover_MA_20": 20_000_000_000, "Volume_MA_20": 10_000_000,
                "Turnover_Value": 2_000_000_000, "Volume": 2_500_000,
                "Technical_Score_Final": 80, "Broker_Score": 70,
                "Broker_Confirmation": "ACCUMULATION", "Synergy_Bonus": 3, "Risk_Penalty": 0,
            }]).to_csv(source, index=False)
            out = tmp_path / "decision"
            result = subprocess.run([
                PYTHON, str(ROOT / "modules/decision_engine/decision_engine.py"), str(source), str(out),
                "--run-id", "SWING-DECISION-TEST",
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            row = pd.read_csv(out / "FINAL_DECISION_V3.csv").iloc[0]
            self.assertAlmostEqual(float(row["Liquidity_Score"]), 66.0, places=2)
            self.assertAlmostEqual(float(row["Final_Score_V3"]), 77.2, places=2)
            self.assertEqual(row["Decision_V3"], "BUY")


if __name__ == "__main__":
    unittest.main()
