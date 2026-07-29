from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_sde_job
from modules.global_market.global_market_registry import enabled_instruments, load_registry
from modules.global_market.global_market_snapshot import build_global_market_snapshot
from modules.global_market.yahoo_global_market_provider import YahooFetchResult
from modules.job_runner.reports import market_outlook_payload
from modules.job_runner.runtime import RunnerContext


FIXED_NOW = datetime(2026, 7, 24, 7, 30, tzinfo=ZoneInfo("Asia/Jakarta"))


def make_ctx(tmp: Path, **overrides) -> RunnerContext:
    scheduler_config = {
        "paths": {
            "preview_root": str(tmp / "previews"),
            "job_status_root": str(tmp / "status"),
            "state_root": str(tmp / "state"),
            "job_log": str(tmp / "job.log"),
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
            "ihsg_csv": str(tmp / "IHSG.csv"),
            "decision_output_dir": str(tmp / "decision"),
            "candidate_output_dir": str(tmp / "candidates"),
            "broker_summary_latest": str(tmp / "broker/BROKER_SUMMARY_LATEST.csv"),
            "telegram_config": str(tmp / "telegram.json"),
        }
    }
    payload = {
        "job": "market_outlook",
        "config_path": ROOT / "config/pipeline.json",
        "scheduler_config_path": ROOT / "config/scheduler.json",
        "trade_date": date(2026, 7, 24),
        "run_id": "TEST-MARKET-OUTLOOK",
        "dry_run": True,
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


def write_registry(tmp: Path, instruments: list[dict], **overrides) -> Path:
    payload = {
        "provider": "YAHOO",
        "enabled": True,
        "source_mode": "LIVE",
        "period": "10d",
        "interval": "1d",
        "retry_count": 1,
        "retry_delay_seconds": 0,
        "request_timeout_seconds": 1,
        "cache_enabled": False,
        "cache_max_age_minutes": 30,
        "minimum_sentiment_coverage_ratio": 0.5,
        "batch_fetch_enabled": True,
        "freshness": {
            "US_INDEX": {"timezone": "America/New_York", "market_close": "16:00", "accepted_delay_days": 1, "max_age_days": 5},
            "ASIA_INDEX": {"timezone": "Asia/Jakarta", "market_close": "16:00", "accepted_delay_days": 1, "max_age_days": 5},
            "CURRENCY": {"timezone": "UTC", "market_close": "17:00", "accepted_delay_days": 1, "max_age_days": 5},
            "COMMODITY": {"timezone": "America/New_York", "market_close": "17:00", "accepted_delay_days": 1, "max_age_days": 5},
        },
        "instruments": instruments,
    }
    payload.update(overrides)
    path = tmp / "global_market.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def instrument(key: str, symbol: str, category: str = "US_INDEX", **extra) -> dict:
    payload = {"key": key, "name": key.upper(), "category": category, "symbol": symbol, "enabled": True, "weight": 1.0}
    payload.update(extra)
    return payload


def frame(close: float | None, previous: float | None = 100.0, market_date: str = "2026-07-23") -> pd.DataFrame:
    return pd.DataFrame({
        "Date": [market_date],
        "Open": [previous],
        "High": [close],
        "Low": [previous],
        "Close": [close],
        "Previous_Close": [previous],
        "Volume": [1000],
    })


class FakeProvider:
    def __init__(self, attempts: list[dict[str, pd.DataFrame | str]]):
        self.attempts = attempts
        self.calls = 0

    def download_batch(self, symbols, period, interval, timeout, threads=True):
        current = self.attempts[min(self.calls, len(self.attempts) - 1)] if self.attempts else {}
        self.calls += 1
        results = {}
        for symbol in symbols:
            value = current.get(symbol, "failed")
            if isinstance(value, pd.DataFrame) and not value.empty:
                results[symbol] = YahooFetchResult(symbol, value, "SUCCESS")
            else:
                results[symbol] = YahooFetchResult(symbol, pd.DataFrame(), "FETCH_FAILED", str(value))
        return results


def tmp_resolver(tmp: Path):
    def _resolve(value):
        path = Path(value)
        return path if path.is_absolute() else tmp / path
    return _resolve


class GlobalMarketTests(unittest.TestCase):
    def test_registry_requires_yahoo_and_enabled_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            path = write_registry(tmp, [instrument("sp500", "^GSPC")])
            registry = load_registry(path)
            self.assertEqual(registry["provider"], "YAHOO")
            self.assertEqual(enabled_instruments(registry)[0]["symbol"], "^GSPC")
            bad = tmp / "bad.json"
            bad.write_text(json.dumps({"provider": "FRED", "source_mode": "LIVE", "instruments": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_registry(bad)

    def test_snapshot_batch_fetch_success_is_yahoo_live_and_saved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [instrument("sp500", "^GSPC"), instrument("nasdaq", "^IXIC")])
            provider = FakeProvider([{"^GSPC": frame(101), "^IXIC": frame(102)}])
            with patch("modules.global_market.global_market_snapshot.resolve", tmp_resolver(tmp)), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                snapshot = build_global_market_snapshot(ctx, reg, provider=provider)
            self.assertEqual(snapshot["provider"], "YAHOO")
            self.assertEqual(snapshot["source_mode"], "LIVE")
            self.assertTrue(all(not row["is_fallback"] for row in snapshot["instruments"]))
            self.assertEqual(snapshot["global_sentiment"]["sentiment_state"], "RISK_ON")
            self.assertTrue((tmp / "data/output/global_market/2026-07-24/global_market_snapshot.json").exists())

    def test_one_symbol_failure_does_not_fail_entire_market_outlook(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [instrument("sp500", "^GSPC"), instrument("vix", "^VIX", inverse_sentiment=True)])
            provider = FakeProvider([{"^GSPC": frame(101), "^VIX": "network error"}])
            with patch("modules.global_market.global_market_snapshot.resolve", tmp_resolver(tmp)), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                snapshot = build_global_market_snapshot(ctx, reg, provider=provider)
            self.assertEqual(len(snapshot["instruments"]), 2)
            self.assertIn("VIX: FETCH_FAILED", "\n".join(snapshot["warnings"]))
            payload = market_outlook_payload(ctx, snapshot)[0].text
            self.assertIn("Global Market Snapshot:", payload)
            self.assertIn("data tidak tersedia", payload)
            self.assertIn("belum mengubah scoring saham", payload)

    def test_all_symbols_fail_yields_insufficient_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [instrument("sp500", "^GSPC"), instrument("nasdaq", "^IXIC")])
            provider = FakeProvider([{"^GSPC": "failed", "^IXIC": "failed"}])
            with patch("modules.global_market.global_market_snapshot.resolve", tmp_resolver(tmp)), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                snapshot = build_global_market_snapshot(ctx, reg, provider=provider)
            self.assertEqual(snapshot["global_sentiment"]["sentiment_state"], "INSUFFICIENT_DATA")
            self.assertEqual(snapshot["coverage_ratio"], 0.0)

    def test_failed_refresh_reuses_valid_existing_snapshot_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [instrument("sp500", "^GSPC")])
            resolver = tmp_resolver(tmp)
            with patch("modules.global_market.global_market_snapshot.resolve", resolver), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                first = build_global_market_snapshot(ctx, reg, provider=FakeProvider([{"^GSPC": frame(101)}]))
                second = build_global_market_snapshot(
                    ctx,
                    reg,
                    provider=FakeProvider([{"^GSPC": "network failed"}]),
                    fallback_to_existing_on_failure=True,
                )
            self.assertEqual(second["snapshot_id"], first["snapshot_id"])
            self.assertTrue(second["fallback_used"])
            saved = json.loads((tmp / "data/output/global_market/2026-07-24/global_market_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["snapshot_id"], first["snapshot_id"])

    def test_market_outlook_delivery_failure_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp, dry_run=False)
            snapshot = {
                "snapshot_id": "GLOBAL-MARKET-TEST",
                "provider": "YAHOO",
                "source_mode": "LIVE",
                "coverage_ratio": 1.0,
                "minimum_required_coverage_ratio": 0.5,
                "warnings": [],
                "errors": [],
                "instruments": [],
                "global_sentiment": {"sentiment_state": "NEUTRAL", "sentiment_score": 0.0, "coverage_ratio": 1.0, "reason": "test"},
            }
            failed = [{"status": "FAILED", "report_type": "market_outlook", "error": "telegram failed", "part_count": 1}]
            with patch("run_sde_job.build_global_market_snapshot", return_value=snapshot), \
                 patch("run_sde_job.deliver", return_value=failed):
                code = run_sde_job.job_market_outlook(ctx)
            self.assertEqual(code, 50)
            status = json.loads((tmp / "status/market_outlook_latest.json").read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "DELIVERY_FAILED")
            self.assertEqual(status["telegram_status"], "FAILED")

    def test_invalid_close_previous_close_and_stale_data_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [
                instrument("empty_close", "A"),
                instrument("empty_prev", "B"),
                instrument("stale", "C"),
            ])
            provider = FakeProvider([{
                "A": frame(None),
                "B": frame(101, previous=None),
                "C": frame(101, market_date="2026-07-10"),
            }])
            with patch("modules.global_market.global_market_snapshot.resolve", tmp_resolver(tmp)), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                snapshot = build_global_market_snapshot(ctx, reg, provider=provider)
            statuses = {row["instrument"]: row["freshness_status"] for row in snapshot["instruments"]}
            self.assertEqual(statuses["empty_close"], "INVALID_PRICE")
            self.assertEqual(statuses["empty_prev"], "INVALID_PRICE")
            self.assertEqual(statuses["stale"], "STALE")

    def test_cache_valid_is_used_and_cache_stale_is_not_used(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [instrument("sp500", "^GSPC")], cache_enabled=True)
            with patch("modules.global_market.global_market_snapshot.resolve", tmp_resolver(tmp)), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                first_provider = FakeProvider([{"^GSPC": frame(101)}])
                first = build_global_market_snapshot(ctx, reg, provider=first_provider)
                second_provider = FakeProvider([{"^GSPC": "should not fetch"}])
                second = build_global_market_snapshot(ctx, reg, provider=second_provider)
                cache_file = tmp / "data/cache/global_market/sp500.json"
                old = (FIXED_NOW - timedelta(hours=1)).timestamp()
                os.utime(cache_file, (old, old))
                stale_provider = FakeProvider([{"^GSPC": "fetch after stale cache"}])
                third = build_global_market_snapshot(ctx, reg, provider=stale_provider)
            self.assertEqual(first["cache"]["saved_count"], 1)
            self.assertEqual(second_provider.calls, 0)
            self.assertEqual(second["cache"]["used_count"], 1)
            self.assertEqual(stale_provider.calls, 1)
            self.assertEqual(third["instruments"][0]["freshness_status"], "FETCH_FAILED")

    def test_retry_success_and_failure_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [instrument("sp500", "^GSPC"), instrument("nasdaq", "^IXIC")], retry_count=2)
            provider = FakeProvider([
                {"^GSPC": "temporary", "^IXIC": "temporary"},
                {"^GSPC": frame(101), "^IXIC": "still failed"},
            ])
            with patch("modules.global_market.global_market_snapshot.resolve", tmp_resolver(tmp)), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                snapshot = build_global_market_snapshot(ctx, reg, provider=provider)
            rows = {row["instrument"]: row for row in snapshot["instruments"]}
            self.assertEqual(rows["sp500"]["freshness_status"], "VALID")
            self.assertEqual(rows["sp500"]["retry_count"], 1)
            self.assertEqual(rows["nasdaq"]["freshness_status"], "FETCH_FAILED")
            self.assertEqual(provider.calls, 2)

    def test_sentiment_states_risk_off_neutral_and_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp)
            reg = write_registry(tmp, [instrument("sp500", "^GSPC"), instrument("nasdaq", "^IXIC")])
            with patch("modules.global_market.global_market_snapshot.resolve", tmp_resolver(tmp)), \
                 patch("modules.global_market.global_market_snapshot.now_wib", return_value=FIXED_NOW):
                off = build_global_market_snapshot(ctx, reg, provider=FakeProvider([{"^GSPC": frame(99), "^IXIC": frame(98)}]))
                neutral = build_global_market_snapshot(ctx, reg, provider=FakeProvider([{"^GSPC": frame(100.05), "^IXIC": frame(99.95)}]))
                reg_insufficient = write_registry(tmp, [instrument("sp500", "^GSPC"), instrument("nasdaq", "^IXIC")], minimum_sentiment_coverage_ratio=0.75)
                insufficient = build_global_market_snapshot(ctx, reg_insufficient, provider=FakeProvider([{"^GSPC": frame(101), "^IXIC": "fail"}]))
            self.assertEqual(off["global_sentiment"]["sentiment_state"], "RISK_OFF")
            self.assertEqual(neutral["global_sentiment"]["sentiment_state"], "NEUTRAL")
            self.assertEqual(insufficient["global_sentiment"]["sentiment_state"], "INSUFFICIENT_DATA")

    def test_job_market_outlook_writes_global_snapshot_id_to_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp, dry_run=True)
            snapshot = {
                "snapshot_id": "GLOBAL-MARKET-TEST",
                "provider": "YAHOO",
                "source_mode": "LIVE",
                "coverage_ratio": 1.0,
                "warnings": [],
                "errors": [],
                "instruments": [],
                "global_sentiment": {"sentiment_state": "NEUTRAL", "sentiment_score": 0.0, "coverage_ratio": 1.0, "reason": "test"},
            }
            with patch("run_sde_job.build_global_market_snapshot", return_value=snapshot):
                code = run_sde_job.job_market_outlook(ctx)
            self.assertEqual(code, 0)
            status = json.loads((tmp / "status/market_outlook_latest.json").read_text(encoding="utf-8"))
            self.assertEqual(status["global_market_snapshot_id"], "GLOBAL-MARKET-TEST")
            self.assertEqual(status["global_sentiment_state"], "NEUTRAL")


if __name__ == "__main__":
    unittest.main()
