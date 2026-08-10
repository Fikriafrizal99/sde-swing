from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from modules.broker_bridge.broker_period_context import (
    fixed_period_spec,
    inspect_summary_export,
    list_reusable_snapshots,
    persist_snapshot,
)
from modules.data_sources.broker_windows import BrokerDay, compute_window_features
from modules.data_sources.decision_bridge import build_contexts_for_symbols
from tools.run_final_watchlist_broker_period import (
    BridgeRunLock,
    CanonicalTransaction,
    recover_unfinished_transactions,
)


def _broker_row(code: str = "AA", value: float = 1_000_000.0) -> dict:
    return {
        "broker_code": code,
        "side": "BUY",
        "net_value": value,
        "net_lot": value / 1000.0,
        "avg_price": 1000.0,
        "gross_value": value,
    }


def _summary_frame(start: str, end: str) -> pd.DataFrame:
    rows = []
    for symbol in ("BBCA", "TLKM"):
        rows.append({
            "FROM_DATE": start,
            "TO_DATE": end,
            "EMITEN": symbol,
            "TOTAL_BUY": 10_000_000,
            "TOTAL_SELL": 6_000_000,
            "NET_FLOW": 4_000_000,
            "TOP_BUYER_1": "AA",
            "TOP_SELLER_1": "ZZ",
            "BUYER_CONCENTRATION": 0.4,
            "SELLER_CONCENTRATION": 0.3,
        })
    return pd.DataFrame(rows)


def test_fixed_3d_and_5d_use_actual_idx_sessions():
    three = fixed_period_spec("3D", "2026-08-10")
    five = fixed_period_spec("5D", "2026-08-10")

    assert three.period_start == "2026-08-06"
    assert three.period_end == "2026-08-10"
    assert list(three.session_dates) == ["2026-08-06", "2026-08-07", "2026-08-10"]
    assert three.trading_sessions == 3

    assert five.period_start == "2026-08-04"
    assert list(five.session_dates) == [
        "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07", "2026-08-10",
    ]
    assert five.trading_sessions == 5


def test_explicit_idx_window_does_not_compress_missing_session():
    days = [
        BrokerDay("2026-08-05", [_broker_row("AA")]),
        BrokerDay("2026-08-06", [_broker_row("BB")]),
        # 2026-08-07 intentionally missing
        BrokerDay("2026-08-10", [_broker_row("CC")]),
    ]

    production = compute_window_features(days, "3D", as_of_date="2026-08-10")
    assert production.available_sessions == 2
    assert production.coverage_ratio == 2 / 3
    assert production.window_complete is False

    # Backward-compatible helper path remains the old N-observation behaviour.
    legacy = compute_window_features(days, "3D")
    assert legacy.available_sessions == 3
    assert legacy.window_complete is True


def test_summary_export_must_match_selected_range(tmp_path: Path):
    spec = fixed_period_spec("3D", "2026-08-10")
    correct = tmp_path / "BROKER_SUMMARY_COMBINED_ok.csv"
    wrong = tmp_path / "BROKER_SUMMARY_COMBINED_wrong.csv"
    _summary_frame("2026-08-06", "2026-08-10").to_csv(correct, index=False)
    _summary_frame("2026-08-07", "2026-08-10").to_csv(wrong, index=False)

    valid, message, info = inspect_summary_export(correct, ["BBCA", "TLKM"], 0.8, spec)
    assert valid is True
    assert message == "OK"
    assert info["coverage"] == 1.0

    valid, message, _ = inspect_summary_export(wrong, ["BBCA", "TLKM"], 0.8, spec)
    assert valid is False
    assert message.startswith("PERIOD_MISMATCH_ROWS:")


def test_summary_export_rejects_invalid_rows_and_unexpected_symbols(tmp_path: Path):
    spec = fixed_period_spec("3D", "2026-08-10")
    invalid = _summary_frame(spec.period_start, spec.period_end)
    invalid.loc[1, "FROM_DATE"] = "not-a-date"
    invalid_path = tmp_path / "BROKER_SUMMARY_COMBINED_invalid.csv"
    invalid.to_csv(invalid_path, index=False)

    valid, message, _ = inspect_summary_export(invalid_path, ["BBCA", "TLKM"], 0.8, spec)
    assert valid is False
    assert message.startswith("INVALID_PERIOD_DATES_ROWS:")

    unexpected = _summary_frame(spec.period_start, spec.period_end)
    unexpected.loc[len(unexpected)] = unexpected.iloc[0].copy()
    unexpected.loc[len(unexpected) - 1, "EMITEN"] = "UNEXPECTED"
    unexpected_path = tmp_path / "BROKER_SUMMARY_COMBINED_unexpected.csv"
    unexpected.to_csv(unexpected_path, index=False)

    valid, message, _ = inspect_summary_export(unexpected_path, ["BBCA", "TLKM"], 0.8, spec)
    assert valid is False
    assert message == "UNEXPECTED_SYMBOLS:UNEXPECTED"


def test_aggregate_snapshot_is_not_daily_history_eligible(tmp_path: Path):
    spec = fixed_period_spec("3D", "2026-08-10")
    summary = tmp_path / "BROKER_SUMMARY_COMBINED_test.csv"
    _summary_frame(spec.period_start, spec.period_end).to_csv(summary, index=False)
    info = {
        "source_hash": "test",
        "rows": 2,
        "matched": 2,
        "expected": 2,
        "coverage": 1.0,
        "missing_symbols": [],
        "unexpected_symbols": [],
    }
    manifest = persist_snapshot(
        summary,
        None,
        spec,
        info,
        snapshot_root=tmp_path / "snapshots",
    )

    assert manifest["aggregate_snapshot"] is True
    assert manifest["daily_history_eligible"] is False
    assert manifest["scoring_adjustment_applied"] is False
    assert manifest["freshness_adjustment_applied"] is False
    assert manifest["persistence_adjustment_applied"] is False
    assert Path(manifest["summary_snapshot_path"]).exists()


def test_unfinished_canonical_transaction_auto_recovers_old_files(tmp_path: Path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    summary = canonical / "BROKER_SUMMARY_LATEST.csv"
    sidecar = canonical / "BROKER_SUMMARY_LATEST.manifest.json"
    raw = canonical / "BROKER_RAW_LATEST.csv"
    summary.write_text("OLD_SUMMARY", encoding="utf-8")
    sidecar.write_text('{"old": true}', encoding="utf-8")
    raw.write_text("OLD_RAW", encoding="utf-8")

    replacement = tmp_path / "NEW_SUMMARY.csv"
    replacement.write_text("NEW_SUMMARY", encoding="utf-8")
    recovery_root = tmp_path / "recovery"
    transaction = CanonicalTransaction(
        recovery_root,
        "TEST-RUN",
        {"summary": summary, "sidecar": sidecar, "raw": raw},
    )
    transaction.begin()
    transaction.activate("summary", replacement)
    transaction.mask("raw")
    sidecar.write_text('{"new": true}', encoding="utf-8")

    # Simulate a killed process: no explicit rollback/commit, next run recovers.
    assert recover_unfinished_transactions(recovery_root) == 1
    assert summary.read_text(encoding="utf-8") == "OLD_SUMMARY"
    assert sidecar.read_text(encoding="utf-8") == '{"old": true}'
    assert raw.read_text(encoding="utf-8") == "OLD_RAW"

    state = json.loads((recovery_root / "TEST-RUN" / "transaction.json").read_text(encoding="utf-8"))
    assert state["state"] == "AUTO_RECOVERED"


def test_production_bridge_keeps_explicit_trade_date_anchor(tmp_path: Path):
    rows = {
        "BBCA": [
            {
                **_broker_row("AA"),
                "market_date": "2026-08-07",
                "broker_type": "DOMESTIK",
            }
        ]
    }

    contexts = build_contexts_for_symbols(
        rows,
        primary_window="3D",
        as_of_date="2026-08-10",
    )
    context = contexts["BBCA"]
    assert context.market_date == "2026-08-10"
    assert context.windows["3D"].available_sessions == 1
    assert context.windows["3D"].coverage_ratio == 1 / 3
    assert context.windows["3D"].window_complete is False


def test_snapshot_requires_commit_marker_for_reuse_and_separates_raw_identity(tmp_path: Path):
    spec = fixed_period_spec("3D", "2026-08-10")
    summary = tmp_path / "BROKER_SUMMARY_COMBINED_test.csv"
    raw_one = tmp_path / "BROKER_RAW_COMBINED_one.csv"
    raw_two = tmp_path / "BROKER_RAW_COMBINED_two.csv"
    _summary_frame(spec.period_start, spec.period_end).to_csv(summary, index=False)
    raw_one.write_text("raw-one", encoding="utf-8")
    raw_two.write_text("raw-two", encoding="utf-8")
    info = {
        "rows": 2,
        "matched": 2,
        "expected": 2,
        "coverage": 1.0,
        "missing_symbols": [],
        "unexpected_symbols": [],
    }
    snapshot_root = tmp_path / "snapshots"
    first = persist_snapshot(summary, raw_one, spec, info, snapshot_root=snapshot_root)
    assert first["snapshot_state"] == "PENDING"
    assert list_reusable_snapshots(spec.period_end, snapshot_root=snapshot_root) == []

    manifest_path = Path(first["manifest_path"])
    committed = json.loads(manifest_path.read_text(encoding="utf-8"))
    committed.update({
        "final_watchlist_run_id": "RUN-1",
        "activated_at": "2026-08-10T18:00:00+07:00",
    })
    committed.pop("snapshot_state", None)
    committed.pop("committed_at", None)
    manifest_path.write_text(json.dumps(committed), encoding="utf-8")
    reusable = list_reusable_snapshots(spec.period_end, snapshot_root=snapshot_root)
    assert len(reusable) == 1

    same = persist_snapshot(summary, raw_one, spec, info, snapshot_root=snapshot_root)
    assert same["snapshot_id"] == first["snapshot_id"]
    assert same["snapshot_state"] == "COMMITTED"

    second = persist_snapshot(summary, raw_two, spec, info, snapshot_root=snapshot_root)
    assert second["snapshot_id"] != first["snapshot_id"]
    assert Path(second["raw_snapshot_path"]).read_text(encoding="utf-8") == "raw-two"


def test_bridge_run_lock_rejects_concurrent_process(tmp_path: Path):
    first = BridgeRunLock(tmp_path)
    second = BridgeRunLock(tmp_path)
    first.acquire()
    try:
        try:
            second.acquire()
        except RuntimeError as exc:
            assert str(exc).startswith("BROKER_BRIDGE_ALREADY_RUNNING:")
        else:
            raise AssertionError("second bridge lock unexpectedly acquired")
    finally:
        first.release()
