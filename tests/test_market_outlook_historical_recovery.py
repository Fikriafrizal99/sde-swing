from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from modules.global_market import historical_global_market_snapshot as historical
from modules.global_market.yahoo_global_market_provider import YahooFetchResult
from tools import recover_market_outlook as recovery


class FakeRangeProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def download_batch_range(self, symbols, *, start, end, interval, timeout, threads=True):
        self.calls.append({
            "symbols": list(symbols),
            "start": start,
            "end": end,
            "interval": interval,
            "timeout": timeout,
            "threads": threads,
        })
        frame = pd.DataFrame({
            "Date": pd.to_datetime(["2026-08-19", "2026-08-20"]),
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Previous_Close": [100.0, 101.0],
            "Volume": [1000, 1100],
            "Yahoo_Symbol": [symbols[0], symbols[0]],
        })
        return {symbol: YahooFetchResult(symbol, frame.assign(Yahoo_Symbol=symbol), "SUCCESS") for symbol in symbols}


def test_historical_global_snapshot_bounds_transport_to_expected_session(tmp_path: Path, monkeypatch) -> None:
    registry = tmp_path / "global_market.json"
    registry.write_text(json.dumps({
        "provider": "YAHOO",
        "enabled": True,
        "source_mode": "LIVE",
        "interval": "1d",
        "request_timeout_seconds": 5,
        "batch_fetch_enabled": True,
        "minimum_sentiment_coverage_ratio": 0.5,
        "historical_recovery_lookback_days": 20,
        "freshness": {
            "US_INDEX": {
                "timezone": "America/New_York",
                "market_close": "16:00",
                "accepted_delay_days": 1,
                "max_age_days": 5,
            }
        },
        "instruments": [{
            "key": "sp500",
            "name": "S&P 500",
            "category": "US_INDEX",
            "symbol": "^GSPC",
            "enabled": True,
            "weight": 1.0,
        }],
    }), encoding="utf-8")

    output_root = tmp_path / "global_market"
    monkeypatch.setattr(
        historical,
        "resolve",
        lambda value: output_root if str(value) == "data/output/global_market" else Path(value),
    )
    provider = FakeRangeProvider()
    ctx = SimpleNamespace(trade_date=date(2026, 8, 21), run_id="RECOVERY-TEST")
    as_of = datetime(2026, 8, 21, 7, 30, tzinfo=ZoneInfo("Asia/Jakarta"))

    snapshot = historical.build_historical_global_market_snapshot(
        ctx,
        as_of_at=as_of,
        registry_path=registry,
        provider=provider,
    )

    assert provider.calls
    # Friday 07:30 WIB is Thursday evening in New York after the US close, so
    # Thursday 2026-08-20 is the latest knowable US session. Yahoo end is exclusive.
    assert provider.calls[0]["end"] == "2026-08-21"
    assert snapshot["source_mode"] == "HISTORICAL_AS_OF"
    assert snapshot["recovery_as_of"].startswith("2026-08-21T07:30:00")
    assert snapshot["instruments"][0]["market_date"] == "2026-08-20"
    assert snapshot["source_metadata"]["point_in_time_safe"] is True


def test_recovery_context_uses_outlook_time_and_previous_idx_session() -> None:
    scheduler = {"timezone": "Asia/Jakarta", "market_outlook": {"time": "07:30"}}
    calendar = {"holidays": [], "special_trading_days": []}
    target = date(2026, 8, 21)

    as_of = recovery._historical_as_of(target, scheduler)
    previous = recovery._previous_idx_session(target, calendar)

    assert as_of.isoformat(timespec="minutes") == "2026-08-21T07:30+07:00"
    assert previous == date(2026, 8, 20)


def test_market_outlook_launcher_exposes_separate_recovery_without_changing_normal_path() -> None:
    text = Path("RUN_MARKET_OUTLOOK.bat").read_text(encoding="utf-8")
    assert "[9] Recovery sesi terakhir terlewat - historical/as-of, tanpa Telegram" in text
    assert "tools\\recover_market_outlook.py" in text

    recovery_block = text.split(":RECOVER_LAST_SESSION", 1)[1].split(":NORMAL_WITH_NEWS", 1)[0]
    assert "run_sde_job_integrated.py" not in recovery_block
    assert "recover_market_outlook.py" in recovery_block
    assert "--job market_outlook" not in recovery_block

    # Normal mode remains the existing canonical live runner.
    assert "%SDE_PYTHON_CMD% -u run_sde_job_integrated.py --job market_outlook" in text
