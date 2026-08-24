from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd

from modules.broker_bridge.broker_period_context import fixed_period_spec
from tools import run_final_watchlist_broker_period as runner


def _write_summary(path: Path, start: str, end: str) -> None:
    pd.DataFrame([
        {
            "FROM_DATE": start,
            "TO_DATE": end,
            "EMITEN": "AAA",
            "TOTAL_BUY": 300.0,
            "TOTAL_SELL": 120.0,
            "NET_FLOW": 180.0,
            "TOP_BUYER_1": "AA",
            "TOP_SELLER_1": "BB",
            "BUYER_CONCENTRATION": 0.60,
            "SELLER_CONCENTRATION": 0.40,
        }
    ]).to_csv(path, index=False)


def _write_raw(path: Path, start: str, end: str) -> None:
    pd.DataFrame([
        {
            "SYMBOL": "AAA",
            "FROM_DATE": start,
            "TO_DATE": end,
            "SIDE": "BUY",
            "RANK": 1,
            "BROKER_CODE": "AA",
            "BROKER_TYPE": "DOMESTIK",
            "NET_VALUE": 300.0,
            "NET_LOT": 3.0,
            "GROSS_VALUE": 300.0,
            "GROSS_LOT": 3.0,
            "FREQUENCY": 3,
            "AVG_PRICE": 100.0,
        },
        {
            "SYMBOL": "AAA",
            "FROM_DATE": start,
            "TO_DATE": end,
            "SIDE": "SELL",
            "RANK": 1,
            "BROKER_CODE": "BB",
            "BROKER_TYPE": "DOMESTIK",
            "NET_VALUE": -120.0,
            "NET_LOT": -1.2,
            "GROSS_VALUE": -120.0,
            "GROSS_LOT": -1.2,
            "FREQUENCY": 2,
            "AVG_PRICE": 100.0,
        },
    ]).to_csv(path, index=False)


def test_multiday_primary_persists_exact_stockbit_aggregate(monkeypatch, tmp_path: Path):
    spec = fixed_period_spec("3D", "2026-08-14")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    summary = downloads / "BROKER_SUMMARY_COMBINED_TEST.csv"
    raw = downloads / "BROKER_RAW_COMBINED_TEST.csv"
    _write_summary(summary, spec.period_start, spec.period_end)
    _write_raw(raw, spec.period_start, spec.period_end)

    def fake_wait(downloads_arg, expected_symbols, min_coverage, spec_arg, *, timeout_seconds, poll_seconds):
        assert downloads_arg == downloads
        assert expected_symbols == ["AAA"]
        assert min_coverage == 1.0
        assert spec_arg == spec
        assert timeout_seconds == 30
        assert poll_seconds == 0.5
        return summary, {
            "rows": 1,
            "matched": 1,
            "expected": 1,
            "coverage": 1.0,
            "missing_symbols": [],
            "unexpected_symbols": [],
        }

    monkeypatch.setattr(runner, "wait_for_matching_export", fake_wait)
    primary = runner.capture_exact_aggregate_primary(
        downloads=downloads,
        expected_symbols=["AAA"],
        min_coverage=1.0,
        spec=spec,
        timeout_seconds=30,
        poll_seconds=0.5,
        snapshot_root=tmp_path / "snapshots",
    )

    assert primary["broker_period_source"] == "STOCKBIT_AGGREGATE_EXPORT"
    assert primary["broker_period_type"] == "3D"
    assert primary["broker_period_start"] == spec.period_start
    assert primary["broker_period_end"] == spec.period_end
    assert primary["aggregate_snapshot"] is True
    assert primary["daily_history_eligible"] is False

    persisted_summary = pd.read_csv(primary["summary_snapshot_path"])
    persisted_raw = pd.read_csv(primary["raw_snapshot_path"])
    assert set(persisted_summary["FROM_DATE"].astype(str)) == {spec.period_start}
    assert set(persisted_summary["TO_DATE"].astype(str)) == {spec.period_end}
    assert set(persisted_raw["FROM_DATE"].astype(str)) == {spec.period_start}
    assert set(persisted_raw["TO_DATE"].astype(str)) == {spec.period_end}


def test_reuse_blocks_legacy_internal_rollup(monkeypatch):
    legacy_internal = {
        "snapshot_id": "BROKER-3D-LEGACY-INTERNAL",
        "broker_period_type": "3D",
        "broker_period_start": "2026-08-12",
        "broker_period_end": "2026-08-14",
        "broker_period_source": "INTERNAL_DAILY_ROLLUP",
        "coverage_ratio": 1.0,
    }
    exact_aggregate = {
        "snapshot_id": "BROKER-3D-EXACT",
        "broker_period_type": "3D",
        "broker_period_start": "2026-08-12",
        "broker_period_end": "2026-08-14",
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
        "coverage_ratio": 1.0,
    }
    monkeypatch.setattr(
        runner,
        "list_reusable_snapshots",
        lambda _trade_date: [legacy_internal, exact_aggregate],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    selected = runner.select_reuse("2026-08-14")
    assert selected["snapshot_id"] == "BROKER-3D-EXACT"
    assert selected["broker_period_source"] == "STOCKBIT_AGGREGATE_EXPORT"


def test_main_no_longer_builds_scored_primary_from_internal_daily_copy():
    source = inspect.getsource(runner.main)
    capture_source = inspect.getsource(runner.capture_exact_aggregate_primary)
    assert "persist_internal_rollup_snapshot" not in source
    assert "capture_exact_aggregate_primary(" in source
    assert "STOCKBIT_AGGREGATE_EXPORT" in capture_source
