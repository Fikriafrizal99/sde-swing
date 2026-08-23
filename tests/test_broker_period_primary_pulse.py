from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from modules.broker_bridge.broker_period_context import (
    custom_period_spec,
    fixed_period_spec,
    list_reusable_snapshots,
    persist_internal_rollup_snapshot,
    persist_snapshot,
    primary_context_metadata,
    primary_pulse_alignment,
    today_pulse_from_rows,
)
from modules.data_sources.broker_history import (
    connect,
    ingest_daily_capture_files,
    init_schema,
    read_daily_capture_rows,
)
from modules.data_sources.broker_multiday_engine import compute_multiday_context
from modules.data_sources.decision_bridge import attach_multiday_context
from modules.telegram.final_watchlist_ui import format_watchlist_detail
from tools.run_final_watchlist_broker_period import (
    active_sidecar_payload,
    archive_daily_raw,
    choose_primary_fallback,
)


def _summary(path: Path, start: str, end: str) -> None:
    pd.DataFrame([
        {
            "FROM_DATE": start,
            "TO_DATE": end,
            "EMITEN": "AAA",
            "TOTAL_BUY": 100,
            "TOTAL_SELL": 50,
            "NET_FLOW": 50,
            "TOP_BUYER_1": "AA",
            "TOP_SELLER_1": "BB",
            "BUYER_CONCENTRATION": 0.5,
            "SELLER_CONCENTRATION": 0.5,
        }
    ]).to_csv(path, index=False)


def _raw(path: Path, market_date: str, *, start: str | None = None) -> None:
    start = start or market_date
    pd.DataFrame([
        {
            "SYMBOL": "AAA",
            "FROM_DATE": start,
            "TO_DATE": market_date,
            "SIDE": "BUY",
            "RANK": 1,
            "BROKER_CODE": "AA",
            "BROKER_TYPE": "DOMESTIK",
            "NET_VALUE": 100.0,
            "NET_LOT": 1.0,
            "GROSS_VALUE": 100.0,
            "GROSS_LOT": 1.0,
            "FREQUENCY": 1,
            "AVG_PRICE": 100.0,
        },
        {
            "SYMBOL": "AAA",
            "FROM_DATE": start,
            "TO_DATE": market_date,
            "SIDE": "SELL",
            "RANK": 1,
            "BROKER_CODE": "BB",
            "BROKER_TYPE": "DOMESTIK",
            "NET_VALUE": 20.0,
            "NET_LOT": 1.0,
            "GROSS_VALUE": 20.0,
            "GROSS_LOT": 1.0,
            "FREQUENCY": 1,
            "AVG_PRICE": 100.0,
        },
    ]).to_csv(path, index=False)


def _daily_manifest(tmp_path: Path, market_date: str = "2026-08-12") -> dict:
    summary = tmp_path / f"SUMMARY_{market_date}.csv"
    raw = tmp_path / f"RAW_{market_date}.csv"
    _summary(summary, market_date, market_date)
    _raw(raw, market_date)
    return persist_snapshot(
        summary,
        raw,
        fixed_period_spec("1D", market_date),
        {"rows": 1, "matched": 1, "expected": 1, "coverage": 1.0},
        snapshot_root=tmp_path / "snapshots",
        selected_by="DAILY_CAPTURE",
    )


@pytest.mark.parametrize("period_type", ["3D", "5D"])
def test_complete_daily_primary_uses_internal_rollup_provenance(tmp_path: Path, period_type: str):
    daily = _daily_manifest(tmp_path)
    spec = fixed_period_spec(period_type, "2026-08-12")
    primary = persist_internal_rollup_snapshot(daily, spec, snapshot_root=tmp_path / "snapshots")

    assert primary["broker_period_source"] == "INTERNAL_DAILY_ROLLUP"
    assert primary["broker_period_type"] == period_type
    assert primary["broker_session_dates"] == list(spec.session_dates)
    assert primary["aggregate_snapshot"] is True
    assert primary["daily_history_eligible"] is False
    assert primary["daily_source_snapshot_id"] == daily["snapshot_id"]
    assert primary["broker_period_complete"] is True


def test_complete_custom_daily_primary_uses_internal_rollup(tmp_path: Path):
    daily = _daily_manifest(tmp_path)
    spec = custom_period_spec("2026-08-10", "2026-08-12")
    primary = persist_internal_rollup_snapshot(daily, spec, snapshot_root=tmp_path / "snapshots")
    assert primary["broker_period_source"] == "INTERNAL_DAILY_ROLLUP"
    assert primary["broker_trading_days"] == 3


@pytest.mark.parametrize("period_type", ["3D", "5D", "CUSTOM"])
def test_stockbit_aggregate_primary_is_not_daily_history_eligible(tmp_path: Path, period_type: str):
    spec = (
        custom_period_spec("2026-08-10", "2026-08-12")
        if period_type == "CUSTOM"
        else fixed_period_spec(period_type, "2026-08-12")
    )
    summary = tmp_path / f"AGGREGATE_{period_type}.csv"
    _summary(summary, spec.period_start, spec.period_end)
    aggregate = persist_snapshot(
        summary,
        None,
        spec,
        {"rows": 1, "matched": 1, "expected": 1, "coverage": 1.0},
        snapshot_root=tmp_path / "snapshots",
        selected_by="FINAL_WATCHLIST_AGGREGATE_FALLBACK",
    )
    assert aggregate["broker_period_source"] == "STOCKBIT_AGGREGATE_EXPORT"
    assert aggregate["aggregate_snapshot"] is True
    assert aggregate["daily_history_eligible"] is False


def test_missing_session_fallback_is_interactive_and_fail_closed(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    assert choose_primary_fallback(
        period_type="3D",
        missing_sessions=["2026-08-11"],
        daily_manifest=None,
    ) == "AGGREGATE"

    monkeypatch.setattr("builtins.input", lambda _prompt: "2")
    with pytest.raises(RuntimeError, match="TODAY_1D_NOT_AVAILABLE"):
        choose_primary_fallback(
            period_type="3D",
            missing_sessions=["2026-08-11"],
            daily_manifest=None,
        )


def test_sidecar_separates_primary_aggregate_from_daily_pulse_and_archive_is_idempotent(tmp_path: Path):
    primary_raw = tmp_path / "PRIMARY_AGGREGATE_RAW.csv"
    daily_raw = tmp_path / "REAL_1D_RAW.csv"
    primary_raw.write_text("aggregate", encoding="utf-8")
    daily_raw.write_text("daily", encoding="utf-8")
    archive = archive_daily_raw(daily_raw, tmp_path / "archive", "2026-08-12")
    assert archive == archive_daily_raw(daily_raw, tmp_path / "archive", "2026-08-12")

    payload = active_sidecar_payload(
        {
            "snapshot_id": "PRIMARY-3D",
            "broker_period_type": "3D",
            "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
            "broker_period_start": "2026-08-10",
            "broker_period_end": "2026-08-12",
            "broker_trading_days": 3,
            "broker_session_dates": ["2026-08-10", "2026-08-11", "2026-08-12"],
            "broker_missing_sessions": [],
            "broker_period_complete": True,
            "summary_snapshot_path": str(primary_raw),
            "raw_snapshot_path": str(primary_raw),
        },
        "RUN-1",
        daily_manifest={
            "snapshot_id": "DAILY-1D",
            "broker_period_type": "1D",
            "broker_period_end": "2026-08-12",
            "raw_snapshot_path": str(daily_raw),
        },
        daily_archive_path=archive,
    )
    assert payload["broker_period_source"] == "STOCKBIT_AGGREGATE_EXPORT"
    assert payload["primary_raw_snapshot_path"] == str(primary_raw)
    assert payload["daily_capture_raw_snapshot_path"] == str(daily_raw)
    assert payload["today_pulse_snapshot_id"] == "DAILY-1D"
    assert payload["today_pulse_source"] == "STOCKBIT_1D"


def test_daily_history_accepts_real_1d_and_rejects_aggregate(tmp_path: Path):
    daily = tmp_path / "BROKER_RAW_2026-08-12.csv"
    aggregate = tmp_path / "BROKER_RAW_2026-08-10_2026-08-12.csv"
    _raw(daily, "2026-08-12")
    _raw(aggregate, "2026-08-12", start="2026-08-10")

    rows, dates = read_daily_capture_rows(daily)
    aggregate_rows, aggregate_dates = read_daily_capture_rows(aggregate)
    assert rows and dates == ["2026-08-12"]
    assert all(row["source"] == "STOCKBIT_1D" for row in rows)
    assert aggregate_rows == []
    assert aggregate_dates == []


def test_daily_history_exact_window_reports_missing_middle_without_shift(tmp_path: Path):
    archive = tmp_path / "archive"
    archive.mkdir()
    for day in ("2026-08-10", "2026-08-12"):
        path = archive / f"BROKER_RAW_{day}.csv"
        _raw(path, day)
    conn = connect(tmp_path / "history.db")
    init_schema(conn)
    try:
        ingest_daily_capture_files(
            conn,
            sorted(archive.glob("*.csv")),
            as_of_date="2026-08-12",
        )
        sessions = [row[0] for row in conn.execute("SELECT market_date FROM trading_sessions ORDER BY market_date")]
    finally:
        conn.close()
    assert sessions == ["2026-08-10", "2026-08-12"]


def test_today_pulse_requires_current_real_1d_and_never_uses_stale_rows():
    rows = [{"market_date": "2026-08-11", "side": "BUY", "net_value": 100.0}]
    stale = today_pulse_from_rows(rows, pulse_date="2026-08-12", snapshot_id="STALE")
    current = today_pulse_from_rows(
        rows + [{"market_date": "2026-08-12", "side": "BUY", "net_value": 75.0}],
        pulse_date="2026-08-12",
        snapshot_id="CURRENT",
    )
    assert stale["today_pulse_available"] is False
    assert stale["today_pulse_status"] == "NOT_AVAILABLE"
    assert current["today_pulse_available"] is True
    assert current["today_pulse_date"] == "2026-08-12"
    assert current["today_pulse_source"] == "STOCKBIT_1D"


@pytest.mark.parametrize(
    ("primary", "pulse", "expected"),
    [
        (100, 50, "ALIGNED_POSITIVE"),
        (-100, -50, "ALIGNED_NEGATIVE"),
        (100, -50, "NEGATIVE_DIVERGENCE"),
        (-100, 50, "POSITIVE_DIVERGENCE"),
    ],
)
def test_primary_pulse_alignment_is_deterministic(primary: float, pulse: float, expected: str):
    assert primary_pulse_alignment(primary, pulse) == expected


def test_pulse_changes_no_multiday_score_confidence_or_protected_trade_fields():
    spec = fixed_period_spec("3D", "2026-08-12")
    rows = [
        {"market_date": day, "side": "BUY", "net_value": 100.0, "broker_code": "AA", "broker_type": "DOMESTIK"}
        for day in spec.session_dates
    ]
    metadata = primary_context_metadata(spec, snapshot_id="PRIMARY", source="INTERNAL_DAILY_ROLLUP")
    positive = compute_multiday_context(
        "AAA", spec.period_end, rows, primary_window="3D", period_metadata=metadata,
        today_pulse=today_pulse_from_rows(rows, pulse_date=spec.period_end, snapshot_id="PULSE"),
    )
    negative_pulse = dict(positive.today_pulse)
    negative_pulse.update({"today_pulse_net_flow": -100.0, "today_pulse_direction": "NEGATIVE"})
    negative = compute_multiday_context(
        "AAA", spec.period_end, rows, primary_window="3D", period_metadata=metadata,
        today_pulse=negative_pulse,
    )
    assert positive.broker_multiday_score == negative.broker_multiday_score
    assert positive.broker_multiday_confidence == negative.broker_multiday_confidence

    frame = pd.DataFrame([{
        "Symbol": "AAA",
        "Decision_Status_Final": "BUY",
        "Decision_V3": "BUY READY",
        "Final_Score_V3": 88,
        "Entry_Zone_Low": 100,
        "Initial_Stop": 90,
        "Target_1": 115,
        "Target_2": 125,
    }])
    result = attach_multiday_context(frame, {"AAA": positive})
    for column in ("Decision_Status_Final", "Decision_V3", "Final_Score_V3", "Entry_Zone_Low", "Initial_Stop", "Target_1", "Target_2"):
        assert result.frame.loc[0, column] == frame.loc[0, column]


def test_internal_snapshot_committed_reuse_is_same_date_only(tmp_path: Path):
    daily = _daily_manifest(tmp_path)
    primary = persist_internal_rollup_snapshot(
        daily,
        fixed_period_spec("3D", "2026-08-12"),
        snapshot_root=tmp_path / "snapshots",
    )
    path = Path(primary["manifest_path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update({
        "snapshot_state": "COMMITTED",
        "final_watchlist_run_id": "RUN-PRIMARY",
        "committed_at": "2026-08-12T18:00:00+07:00",
    })
    path.write_text(json.dumps(payload), encoding="utf-8")
    reusable = list_reusable_snapshots("2026-08-12", snapshot_root=tmp_path / "snapshots")
    assert [item["snapshot_id"] for item in reusable] == [primary["snapshot_id"]]
    assert list_reusable_snapshots("2026-08-11", snapshot_root=tmp_path / "snapshots") == []


def test_final_watchlist_primary_1d_has_no_pulse_and_multiday_has_pulse_and_reason():
    base = {
        "symbol": "AAA", "setup": "BREAKOUT", "trade_date": "2026-08-12",
        "last_price": 100, "entry_low": 95, "entry_high": 105,
        "active_stop_loss": 90, "target_1": 115, "target_2": 125,
        "risk_reward": 2, "technical_status": "VALID_SETUP", "confidence": 80,
        "broker_status": "ACCUMULATION", "broker_score": 70, "broker_net_flow": 1000,
        "buy_days": 2, "sell_days": 1, "top_buyers": [], "top_sellers": [],
        "broker_period_start": "2026-08-10", "broker_period_end": "2026-08-12",
        "broker_trading_days": 3, "broker_session_dates": ["2026-08-10", "2026-08-11", "2026-08-12"],
        "broker_snapshot_id": "PRIMARY", "broker_period_source": "INTERNAL_DAILY_ROLLUP",
        "broker_coverage_text": "3/3", "broker_period_complete": True,
        "today_pulse_available": True, "today_pulse_date": "2026-08-12",
        "today_pulse_snapshot_id": "PULSE", "today_pulse_source": "STOCKBIT_1D",
        "today_pulse_status": "AVAILABLE", "today_pulse_net_flow": 500,
        "today_pulse_buy_days": 1, "today_pulse_sell_days": 0,
        "broker_alignment": "ALIGNED_POSITIVE", "trend": "UPTREND",
        "phase": "WAIT_TRIGGER", "fib_status": "ENGINE_NOT_AVAILABLE_V1_7",
    }
    one_day = format_watchlist_detail(dict(base, broker_period_type="1D", broker_period_source="STOCKBIT_1D"))
    multi = format_watchlist_detail(dict(base, broker_period_type="3D"))
    assert "TODAY PULSE" not in one_day
    assert "Alignment" not in one_day
    assert "TODAY PULSE" not in multi
    assert "STOCKBIT 1D" not in multi
    assert "ALIGNED_POSITIVE" not in multi
    assert "Net +Rp1,00K" in multi
    assert len(multi) <= 1024
