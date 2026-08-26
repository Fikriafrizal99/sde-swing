from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from modules.analytics import outcome_tracker_baseline as tracker
from modules.portfolio import position_management_engine as base_engine
from modules.portfolio.position_management_runtime_integrity import technical_snapshot_as_of


def _signal_record(
    symbol: str,
    signal_date: str,
    *,
    decision: str,
    setup: str,
    entry_low: float,
    entry_high: float,
    reference: float,
    stop: float,
    tp1: float,
    tp2: float,
):
    decision_row = {
        "Symbol": symbol,
        "Technical_Data_Date": signal_date,
        "Decision_V3": decision,
        "Data_Quality_Status": "VALID",
        "Final_Score_V3": 80,
    }
    plan = {
        "Plan_Status": "ACCEPT",
        "Setup_Type": setup,
        "Entry_Zone_Low": entry_low,
        "Entry_Zone_High": entry_high,
        "Reference_Close": reference,
        "Initial_Stop": stop,
        "Target_1": tp1,
        "Target_2": tp2,
        "Max_Hold_Days": 10,
    }
    record = tracker.build_signal_record(
        decision_row,
        plan,
        run_id=f"RUN-{signal_date}-{setup}",
        default_signal_date=signal_date,
    )
    assert record is not None
    return record


def test_waiting_confirmed_upgrade_updates_setup_with_executable_plan(tmp_path: Path) -> None:
    conn = tracker.connect(tmp_path / "history.db")
    try:
        first = _signal_record(
            "TPIA",
            "2026-08-24",
            decision="BUY CANDIDATE",
            setup="PULLBACK",
            entry_low=2400,
            entry_high=2440,
            reference=2420,
            stop=2320,
            tp1=2550,
            tp2=2650,
        )
        assert tracker.upsert_signal(conn, first) == "INSERTED"

        confirmed = _signal_record(
            "TPIA",
            "2026-08-26",
            decision="BUY",
            setup="BREAKOUT",
            entry_low=2500,
            entry_high=2540,
            reference=2520,
            stop=2430,
            tp1=2700,
            tp2=2850,
        )
        assert tracker.upsert_signal(conn, confirmed) == "UPDATED_ACTIVE"

        row = conn.execute(
            "SELECT * FROM signal_outcome_ledger WHERE symbol='TPIA' AND current_status='WAITING_TRIGGER'"
        ).fetchone()
        assert row is not None
        assert row["setup_type"] == "BREAKOUT"
        assert row["entry_zone_low"] == 2500
        assert row["entry_zone_high"] == 2540
        assert row["stop_loss"] == 2430
        assert row["take_profit_1"] == 2700
        assert row["take_profit_2"] == 2850
    finally:
        conn.close()


def test_actual_buy_resolves_signal_as_of_buy_date_not_latest_active(tmp_path: Path) -> None:
    conn = tracker.connect(tmp_path / "history.db")
    try:
        historical = _signal_record(
            "MDKA",
            "2026-08-10",
            decision="BUY",
            setup="PULLBACK",
            entry_low=2400,
            entry_high=2450,
            reference=2420,
            stop=2320,
            tp1=2600,
            tp2=2750,
        )
        assert tracker.upsert_signal(conn, historical) == "INSERTED"
        conn.execute(
            "UPDATE signal_outcome_ledger SET current_status='CLOSED', exit_date='2026-08-20' WHERE signal_id=?",
            (historical["signal_id"],),
        )
        conn.commit()

        future_active = _signal_record(
            "MDKA",
            "2026-08-15",
            decision="BUY",
            setup="BREAKOUT",
            entry_low=2600,
            entry_high=2640,
            reference=2620,
            stop=2500,
            tp1=2800,
            tp2=2950,
        )
        assert tracker.upsert_signal(conn, future_active) == "INSERTED"

        position_id = tracker.record_portfolio_buy(
            conn,
            symbol="MDKA",
            quantity=1000,
            buy_price=2430,
            buy_date="2026-08-12",
        )
        position = conn.execute(
            "SELECT signal_id FROM portfolio_positions WHERE position_id=?",
            (position_id,),
        ).fetchone()
        assert position is not None
        assert position["signal_id"] == historical["signal_id"]

        with pytest.raises(ValueError, match="SIGNAL_ID_NOT_VALID_AS_OF_BUY_DATE"):
            tracker.record_portfolio_buy(
                conn,
                symbol="MDKA",
                quantity=500,
                buy_price=2440,
                buy_date="2026-08-12",
                signal_id=future_active["signal_id"],
            )
    finally:
        conn.close()


def test_sell_by_symbol_fails_closed_when_multiple_open_lots_exist(tmp_path: Path) -> None:
    conn = tracker.connect(tmp_path / "history.db")
    try:
        first = tracker.record_portfolio_buy(
            conn,
            symbol="BBCA",
            quantity=100,
            buy_price=8900,
            buy_date="2026-08-10",
        )
        second = tracker.record_portfolio_buy(
            conn,
            symbol="BBCA",
            quantity=200,
            buy_price=9200,
            buy_date="2026-08-12",
        )
        assert first != second

        with pytest.raises(ValueError, match="MULTIPLE_OPEN_POSITIONS_USE_POSITION_ID"):
            tracker.record_portfolio_sell(
                conn,
                symbol="BBCA",
                sell_price=9300,
                sell_date="2026-08-20",
            )

        closed = tracker.record_portfolio_sell(
            conn,
            position_id=first,
            sell_price=9300,
            sell_date="2026-08-20",
        )
        assert closed == first
        statuses = {
            row["position_id"]: row["current_status"]
            for row in conn.execute(
                "SELECT position_id,current_status FROM portfolio_positions WHERE symbol='BBCA'"
            ).fetchall()
        }
        assert statuses[first] == "CLOSED"
        assert statuses[second] == "OPEN"
    finally:
        conn.close()


def test_technical_snapshot_is_capped_at_analysis_date(monkeypatch, tmp_path: Path) -> None:
    pd.DataFrame(
        [
            {"Date": "2026-08-24", "Open": 100, "High": 106, "Low": 98, "Close": 104, "Volume": 1000},
            {"Date": "2026-08-26", "Open": 110, "High": 150, "Low": 105, "Close": 145, "Volume": 5000},
        ]
    ).to_csv(tmp_path / "TEST.csv", index=False)

    def fake_features(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        out = raw.copy()
        out["SMA_20"] = 100.0
        out["SMA_50"] = 95.0
        out["ATR_14"] = 3.0
        out["RSI_14"] = 55.0
        out["Slope_SMA20_10"] = 1.0
        out["Technical_Regime"] = "BULLISH"
        out["Above_SMA20"] = 1
        out["Above_SMA50"] = 1
        out["SMA20_Above_SMA50"] = 1
        out["MACD_Bullish"] = 1
        out["Distance_SMA_20_Pct"] = 1.0
        out["Volume_Ratio_20"] = 1.2
        return out

    monkeypatch.setattr(base_engine, "compute_features", fake_features)
    snapshot = technical_snapshot_as_of(
        tmp_path,
        "TEST",
        "2026-08-20",
        "2026-08-24",
    )

    assert snapshot["status"] == "VALID"
    assert snapshot["analysis_cutoff"] == "2026-08-24"
    assert snapshot["data_date"] == "2026-08-24"
    assert snapshot["current_price"] == 104
    assert snapshot["max_high_since_buy"] == 106
    assert snapshot["min_low_since_buy"] == 98


def test_position_management_launcher_uses_integrity_runtime() -> None:
    source = Path("maintenance/RUN_POSITION_MANAGEMENT.bat").read_text(encoding="utf-8-sig")
    assert "position_management_runtime_integrity.py" in source
    assert "position_management_runtime.py --config" not in source
