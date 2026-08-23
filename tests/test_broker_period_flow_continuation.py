from __future__ import annotations

import pandas as pd
import pytest

from modules.broker_bridge.broker_period_context import (
    custom_period_spec,
    fixed_period_spec,
    period_source_for,
    primary_context_metadata,
    primary_pulse_alignment,
    session_coverage,
    today_pulse_from_rows,
)
from modules.data_sources.broker_history import (
    connect as connect_broker_history,
    init_schema as init_broker_history_schema,
    select_daily_period_rows,
    upsert_broker_rows,
)
from modules.data_sources.broker_multiday_engine import compute_multiday_context
from modules.data_sources.decision_bridge import attach_multiday_context
from modules.database import swing_history_db
from modules.telegram.final_watchlist_ui import format_watchlist_detail


def _daily_rows(dates: list[str], *, value: float = 100.0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, market_date in enumerate(dates, 1):
        rows.extend([
            {
                "symbol": "AAA",
                "market_date": market_date,
                "broker_code": f"B{index}",
                "broker_type": "DOMESTIK",
                "side": "BUY",
                "net_value": value,
                "net_lot": 1,
                "gross_value": value,
                "gross_lot": 1,
                "frequency": 1,
                "avg_price": 100,
                "rank": 1,
                "source": "STOCKBIT",
                "quality_status": "VALIDATED",
                "received_at": f"2026-08-{index:02d}T09:00:00+07:00",
            },
            {
                "symbol": "AAA",
                "market_date": market_date,
                "broker_code": f"S{index}",
                "broker_type": "DOMESTIK",
                "side": "SELL",
                "net_value": value / 4,
                "net_lot": 1,
                "gross_value": value / 4,
                "gross_lot": 1,
                "frequency": 1,
                "avg_price": 100,
                "rank": 1,
                "source": "STOCKBIT",
                "quality_status": "VALIDATED",
                "received_at": f"2026-08-{index:02d}T09:00:00+07:00",
            },
        ])
    return rows


def test_period_specs_and_sources_use_idx_sessions():
    five = fixed_period_spec("5D", "2026-08-11")
    custom = custom_period_spec(five.period_start, five.period_end)

    assert five.session_dates == custom.session_dates
    assert five.trading_sessions == 5
    assert period_source_for("1D") == "STOCKBIT_1D"
    assert period_source_for("3D") == "STOCKBIT_AGGREGATE_EXPORT"
    assert period_source_for("CUSTOM", internal_rollup=True) == "INTERNAL_DAILY_ROLLUP"


def test_missing_session_is_incomplete_without_shifting_or_synthesizing():
    expected = ["2026-08-07", "2026-08-10", "2026-08-11"]
    selected = select_daily_period_rows(
        _daily_rows([expected[0], expected[2]]),
        expected,
    )

    assert selected["coverage"] == pytest.approx(2 / 3)
    assert selected["coverage_text"] == "2/3"
    assert selected["missing_session_dates"] == [expected[1]]
    assert selected["status"] == "INCOMPLETE"
    assert {row["market_date"] for row in selected["rows"]} == {expected[0], expected[2]}

    coverage = session_coverage(expected, [expected[0], expected[2]])
    assert coverage["broker_coverage_text"] == "2/3"
    assert coverage["broker_coverage_status"] == "INCOMPLETE"


def test_daily_history_is_immutable_and_preserves_duplicate_revision_lineage(tmp_path):
    conn = connect_broker_history(tmp_path / "broker.db")
    init_broker_history_schema(conn)
    try:
        row = _daily_rows(["2026-08-11"])[0]
        assert upsert_broker_rows(conn, [row]) == 1

        duplicate = dict(row)
        duplicate["received_at"] = "2026-08-11T10:00:00+07:00"
        assert upsert_broker_rows(conn, [duplicate]) == 1
        assert conn.execute("SELECT COUNT(*) FROM broker_daily").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM broker_daily_revisions WHERE revision_type='DUPLICATE'"
        ).fetchone()[0] == 1

        revision = dict(row)
        revision["net_value"] = 999.0
        with pytest.raises(RuntimeError, match="BROKER_DAILY_CONFLICT"):
            upsert_broker_rows(conn, [revision])
        assert conn.execute(
            "SELECT net_value FROM broker_daily WHERE symbol='AAA'"
        ).fetchone()[0] == row["net_value"]
        assert conn.execute(
            "SELECT COUNT(*) FROM broker_daily_revisions WHERE revision_type='REVISION'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_generic_history_archive_rejects_unsafe_sql_identifiers(tmp_path):
    conn = swing_history_db.connect(tmp_path / "history.db")
    try:
        conn.execute("CREATE TABLE safe_table (key_id TEXT PRIMARY KEY, value TEXT)")
        with pytest.raises(ValueError, match="UNSAFE_SQL_IDENTIFIER"):
            swing_history_db.insert_if_missing(
                conn,
                "safe_table; DROP TABLE safe_table",
                {"key_id": "a", "value": "b"},
                ["key_id"],
            )
    finally:
        conn.close()


def test_custom_primary_reuses_engine_formula_and_pulse_is_context_only():
    spec = custom_period_spec("2026-08-05", "2026-08-11")
    metadata = primary_context_metadata(
        spec,
        snapshot_id="SNAP-CUSTOM",
        source="INTERNAL_DAILY_ROLLUP",
    )
    rows = _daily_rows(list(spec.session_dates))
    positive_pulse = today_pulse_from_rows(
        rows,
        pulse_date=spec.period_end,
        snapshot_id="SNAP-1D",
    )
    negative_pulse = dict(positive_pulse)
    negative_pulse.update({
        "today_pulse_net_flow": -1000.0,
        "today_pulse_direction": "NEGATIVE",
    })

    positive = compute_multiday_context(
        "AAA",
        spec.period_end,
        rows,
        primary_window="CUSTOM",
        period_metadata=metadata,
        today_pulse=positive_pulse,
    )
    negative = compute_multiday_context(
        "AAA",
        spec.period_end,
        rows,
        primary_window="CUSTOM",
        period_metadata=metadata,
        today_pulse=negative_pulse,
    )

    assert positive.windows["CUSTOM"].expected_sessions == spec.trading_sessions
    assert positive.windows["CUSTOM"].available_sessions == spec.trading_sessions
    assert positive.broker_multiday_score == negative.broker_multiday_score
    assert positive.broker_multiday_confidence == negative.broker_multiday_confidence
    assert positive.to_context_dict()["broker_alignment"] == "ALIGNED_POSITIVE"
    assert negative.to_context_dict()["broker_alignment"] == "NEGATIVE_DIVERGENCE"
    for key in (
        "Broker_MultiDay_Score",
        "Broker_MultiDay_Confidence",
        "Broker_MultiDay_Context",
        "Broker_MultiDay_Penalty",
        "Broker_MultiDay_Blocker",
    ):
        assert positive.to_context_dict()[key] == negative.to_context_dict()[key]


def test_one_day_primary_does_not_publish_pulse_or_alignment():
    spec = fixed_period_spec("1D", "2026-08-11")
    context = compute_multiday_context(
        "AAA",
        spec.period_end,
        _daily_rows(list(spec.session_dates)),
        primary_window="1D",
        period_metadata=primary_context_metadata(spec, snapshot_id="SNAP-1D"),
        today_pulse={
            "today_pulse_status": "AVAILABLE",
            "today_pulse_net_flow": 100.0,
        },
    )
    payload = context.to_context_dict()
    assert payload["broker_period_type"] == "1D"
    assert "today_pulse_status" not in payload
    assert "broker_alignment" not in payload


def test_bridge_keeps_protected_decision_columns_unchanged():
    spec = fixed_period_spec("3D", "2026-08-11")
    context = compute_multiday_context(
        "AAA",
        spec.period_end,
        _daily_rows(list(spec.session_dates)),
        primary_window="3D",
        period_metadata=primary_context_metadata(spec, snapshot_id="SNAP-3D"),
        today_pulse=today_pulse_from_rows(_daily_rows(list(spec.session_dates)), pulse_date=spec.period_end),
    )
    frame = pd.DataFrame([{
        "Symbol": "AAA",
        "Decision_Status_Final": "BUY",
        "Decision_V3": "BUY CANDIDATE",
        "Final_Score_V3": 88.0,
    }])
    result = attach_multiday_context(frame, {"AAA": context})
    assert result.protected_intact is True
    assert result.frame.loc[0, "Decision_Status_Final"] == "BUY"
    assert result.frame.loc[0, "Final_Score_V3"] == 88.0
    assert result.frame.loc[0, "broker_period_type"] == "3D"


def test_final_watchlist_primary_contract_distinguishes_1d_and_multiday():
    base = {
        "symbol": "AAA",
        "setup": "BREAKOUT",
        "trade_date": "2026-08-11",
        "last_price": 100,
        "entry_low": 95,
        "entry_high": 105,
        "active_stop_loss": 90,
        "target_1": 115,
        "target_2": 125,
        "risk_reward": 2,
        "technical_status": "VALID_SETUP",
        "confidence": 80,
        "broker_status": "ACCUMULATION",
        "broker_score": 70,
        "broker_net_flow": 1000,
        "buy_days": 2,
        "sell_days": 1,
        "top_buyers": [],
        "top_sellers": [],
        "broker_period_start": "2026-08-07",
        "broker_period_end": "2026-08-11",
        "broker_trading_days": 3,
        "broker_session_dates": ["2026-08-07", "2026-08-10", "2026-08-11"],
        "broker_snapshot_id": "SNAP-TEST",
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
        "broker_coverage_text": "3/3",
        "today_pulse_date": "2026-08-11",
        "today_pulse_snapshot_id": "SNAP-1D",
        "today_pulse_status": "AVAILABLE",
        "today_pulse_net_flow": 500,
        "today_pulse_buy_days": 1,
        "today_pulse_sell_days": 0,
        "broker_alignment": "ALIGNED_POSITIVE",
        "trend": "UPTREND",
        "phase": "WAIT_TRIGGER",
        "fib_status": "ENGINE_NOT_AVAILABLE_V1_7",
    }
    multi = dict(base, broker_period_type="3D")
    one_day = dict(base, broker_period_type="1D", broker_period_source="STOCKBIT_1D")

    multi_text = format_watchlist_detail(multi)
    one_day_text = format_watchlist_detail(one_day)
    # Period and pulse provenance stays in the engine/artifact contract; the
    # Telegram card is intentionally compact and does not expose those fields.
    assert "Primary" not in multi_text
    assert "TODAY PULSE" not in multi_text
    assert "ALIGNED_POSITIVE" not in multi_text
    assert "TODAY PULSE" not in one_day_text
    assert "ALIGNED_POSITIVE" not in one_day_text
    assert "Net +Rp1,00K" in multi_text
    assert "Net +Rp1,00K" in one_day_text
    assert len(multi_text) <= 1024
