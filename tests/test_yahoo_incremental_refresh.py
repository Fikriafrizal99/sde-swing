from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from modules.historical_downloader.historical_downloader import (
    ALREADY_CURRENT,
    FULL_BACKFILL,
    MISSING_ONLY,
    REPAIR_OVERLAP,
    build_refresh_plan,
    build_result,
    group_plans_by_request,
    histories_equal,
    merge_history,
    should_write_history,
)


HOLIDAYS = ["2026-08-17"]


def args(**overrides: object) -> SimpleNamespace:
    values = {
        "force_refresh": False,
        "repair": False,
        "full_backfill": False,
        "start": None,
        "end": None,
        "repair_overlap_sessions": 5,
        "incremental_overlap_days": 5,
        "market_holiday": HOLIDAYS,
        "special_trading_day": [],
        "market_close": "16:15",
        "evaluation_datetime": "2026-08-04T12:00:00+07:00",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def history(symbol: str, dates: list[str], closes: list[float] | None = None) -> pd.DataFrame:
    closes = closes or [100.0] * len(dates)
    return pd.DataFrame({
        "Symbol": [symbol] * len(dates),
        "Ticker": [f"{symbol}.JK"] * len(dates),
        "Date": dates,
        "Open": closes,
        "High": [value + 2 for value in closes],
        "Low": [value - 2 for value in closes],
        "Close": closes,
        "Adj Close": closes,
        "Volume": [1_000_000] * len(dates),
    })


class YahooIncrementalRefreshTests(unittest.TestCase):
    def plan(self, root: Path, symbol: str, dates: list[str], **overrides: object):
        destination = root / f"{symbol}.csv"
        history(symbol, dates).to_csv(destination, index=False)
        return build_refresh_plan(symbol, destination, args(**overrides), date(2026, 8, 4))

    def test_local_h_minus_one_requests_only_expected_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(Path(temp), "BBCA", ["2026-08-03"])
        self.assertEqual(plan.refresh_action, MISSING_ONLY)
        self.assertEqual(plan.first_missing_session, "2026-08-04")
        self.assertEqual(plan.download_start_date, "2026-08-04")
        self.assertEqual(plan.download_end_date, "2026-08-05")

    def test_current_symbol_performs_no_request_before_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(Path(temp), "BBCA", ["2026-08-04"])
        self.assertEqual(plan.refresh_action, ALREADY_CURRENT)
        self.assertEqual(plan.download_start_date, "")
        self.assertEqual(plan.download_end_date, "")

    def test_current_symbol_revalidates_same_session_after_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(
                Path(temp),
                "BBCA",
                ["2026-08-04"],
                evaluation_datetime="2026-08-04T16:45:00+07:00",
            )
        self.assertEqual(plan.refresh_action, REPAIR_OVERLAP)
        self.assertEqual(plan.download_start_date, "2026-08-04")
        self.assertEqual(plan.download_end_date, "2026-08-05")
        self.assertEqual(plan.refresh_reason, "LATEST_SESSION_REVALIDATION")

    def test_same_date_ohlcv_revision_is_reported_updated(self) -> None:
        existing = history("BBCA", ["2026-08-04"], [101.0])
        fresh = history("BBCA", ["2026-08-04"], [105.0])
        combined = merge_history(existing, fresh)
        result = build_result(
            "BBCA",
            existing,
            fresh,
            combined,
            date(2026, 8, 4),
            "success",
            True,
            True,
            False,
        )
        self.assertEqual(result.status, "UPDATED_VALID")
        self.assertEqual(result.rows_inserted, 0)
        self.assertEqual(result.rows_updated, 1)

    def test_same_date_identical_ohlcv_remains_unchanged(self) -> None:
        existing = history("BBCA", ["2026-08-04"], [101.0])
        fresh = history("BBCA", ["2026-08-04"], [101.0])
        combined = merge_history(existing, fresh)
        result = build_result(
            "BBCA",
            existing,
            fresh,
            combined,
            date(2026, 8, 4),
            "success",
            True,
            True,
            False,
        )
        self.assertEqual(result.status, "UNCHANGED_ALREADY_CURRENT")
        self.assertEqual(result.rows_updated, 0)

    def test_three_missing_sessions_start_at_first_missing_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(Path(temp), "BBCA", ["2026-07-30"])
        self.assertEqual(plan.missing_market_sessions, 3)
        self.assertEqual(plan.download_start_date, "2026-07-31")

    def test_weekend_and_idx_holiday_are_not_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "BBCA.csv"
            history("BBCA", ["2026-08-14"]).to_csv(destination, index=False)
            plan = build_refresh_plan("BBCA", destination, args(), date(2026, 8, 18))
        self.assertEqual(plan.missing_market_sessions, 1)
        self.assertEqual(plan.first_missing_session, "2026-08-18")

    def test_lagging_symbol_does_not_change_other_symbol_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self.plan(root, "BBCA", ["2026-08-03"])
            lagging = self.plan(root, "TLKM", ["2026-07-30"])
        self.assertEqual(current.download_start_date, "2026-08-04")
        self.assertEqual(lagging.download_start_date, "2026-07-31")

    def test_batch_groups_are_partitioned_by_request_start(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plans = [
                self.plan(root, "BBCA", ["2026-08-03"]),
                self.plan(root, "BMRI", ["2026-08-03"]),
                self.plan(root, "TLKM", ["2026-07-30"]),
                self.plan(root, "ASII", ["2026-08-04"]),
            ]
        groups = group_plans_by_request(plans)
        starts = sorted((key[1], len(group)) for key, group in groups.items())
        self.assertEqual(starts, [("2026-07-31", 1), ("2026-08-04", 2)])

    def test_corrupt_file_full_backfills_only_that_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "BAD.csv").write_text("broken,data\n1,2\n", encoding="utf-8")
            history("BBCA", ["2026-08-04"]).to_csv(root / "BBCA.csv", index=False)
            bad = build_refresh_plan("BAD", root / "BAD.csv", args(), date(2026, 8, 4))
            good = build_refresh_plan("BBCA", root / "BBCA.csv", args(), date(2026, 8, 4))
        self.assertEqual(bad.refresh_action, FULL_BACKFILL)
        self.assertEqual(good.refresh_action, ALREADY_CURRENT)

    def test_internal_gap_uses_repair_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(Path(temp), "BBCA", ["2026-07-31", "2026-08-04"], repair_overlap_sessions=2)
        self.assertEqual(plan.refresh_action, REPAIR_OVERLAP)
        self.assertEqual(plan.internal_gaps, 1)
        self.assertEqual(plan.first_internal_gap, "2026-08-03")

    def test_daily_mode_never_applies_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(Path(temp), "BBCA", ["2026-08-03"], repair_overlap_sessions=20)
        self.assertEqual(plan.refresh_action, MISSING_ONLY)
        self.assertEqual(plan.download_start_date, "2026-08-04")

    def test_explicit_repair_uses_configured_trading_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(Path(temp), "BBCA", ["2026-08-04"], repair=True, repair_overlap_sessions=5)
        self.assertEqual(plan.refresh_action, REPAIR_OVERLAP)
        self.assertEqual(plan.download_start_date, "2026-07-28")

    def test_yahoo_end_date_is_exclusive_expected_plus_one_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            plan = self.plan(Path(temp), "BBCA", ["2026-08-03"])
        self.assertEqual(plan.download_end_date, "2026-08-05")

    def test_merge_deduplicates_and_fresh_overlap_wins(self) -> None:
        existing = history("BBCA", ["2026-08-03", "2026-08-04"], [100.0, 101.0])
        fresh = history("BBCA", ["2026-08-04"], [105.0])
        merged = merge_history(existing, fresh)
        self.assertEqual(len(merged), 2)
        self.assertEqual(float(merged.loc[merged["Date"].dt.strftime("%Y-%m-%d").eq("2026-08-04"), "Close"].iloc[0]), 105.0)

    def test_current_history_is_not_selected_for_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = self.plan(root, "BBCA", ["2026-08-04"])
            self.assertFalse(should_write_history(plan, plan.existing.copy()))

    def test_final_history_matches_full_baseline(self) -> None:
        existing = history("BBCA", ["2026-07-31", "2026-08-03"], [99.0, 100.0])
        fresh = history("BBCA", ["2026-08-04"], [102.0])
        incremental = merge_history(existing, fresh)
        baseline = history("BBCA", ["2026-07-31", "2026-08-03", "2026-08-04"], [99.0, 100.0, 102.0])
        self.assertTrue(histories_equal(incremental, baseline))


if __name__ == "__main__":
    unittest.main()
