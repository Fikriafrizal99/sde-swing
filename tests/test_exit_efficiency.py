from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pandas as pd

from modules.analytics.execution_integrity import (
    economic_outcome,
    outcome_consistency,
    validate_plan_geometry,
)
from modules.analytics.exit_efficiency import run_analysis
from modules.analytics.outcome_tracker import (
    connect,
    pending_lifecycle_events,
    register_decision_file,
    update_outcomes,
)


def _write_close_above_signal(root: Path) -> tuple[Path, Path]:
    decisions = root / "FINAL_DECISION_V3.csv"
    plans = root / "ENTRY_PLANS.csv"
    pd.DataFrame([
        {
            "Symbol": "ICBP",
            "Decision_V3": "BUY",
            "Technical_Data_Date": "2026-08-01",
            "Final_Score_V3": 80,
            "Data_Quality_Status": "VALID",
            "Setup_Type": "BREAKOUT",
        }
    ]).to_csv(decisions, index=False)
    pd.DataFrame([
        {
            "Symbol": "ICBP",
            "Plan_Status": "CONDITIONAL",
            "Rejection_Reason": "MINOR_RESISTANCE_NEAR",
            "Setup_Type": "BREAKOUT",
            "Reference_Close": 99,
            "Minor_Resistance": 100,
            "Initial_Stop": 90,
            "Target_1": 105,
            "Target_2": 110,
            "Max_Hold_Days": 5,
        }
    ]).to_csv(plans, index=False)
    return decisions, plans


def _write_symbol_prices(root: Path, symbol: str, rows: list[dict[str, float | str]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    frame["Volume"] = 1000
    frame.to_csv(root / f"{symbol}.csv", index=False)


def test_new_trigger_is_invalidated_when_actual_entry_is_above_targets(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    historical = tmp_path / "prices"
    decisions, plans = _write_close_above_signal(tmp_path)
    _write_symbol_prices(
        historical,
        "ICBP",
        [
            {"Date": "2026-08-04", "Open": 111, "High": 114, "Low": 110, "Close": 112},
            {"Date": "2026-08-05", "Open": 112, "High": 116, "Low": 109, "Close": 113},
        ],
    )

    conn = connect(db)
    register_decision_file(conn, decisions, plans, "RUN-1", "2026-08-01")
    update_outcomes(conn, historical)
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()

    assert row["current_status"] == "INVALIDATED_BEFORE_ENTRY"
    assert row["final_outcome"] == "INVALIDATED"
    assert row["entry_date"] is None
    assert row["entry_price"] is None
    assert row["exit_reason"].startswith("PLAN_GEOMETRY_INVALID_AT_ENTRY:")

    events = [item["event_type"] for item in pending_lifecycle_events(conn)]
    assert "INVALIDATED_BEFORE_ENTRY" in events
    assert "ENTRY_TRIGGERED" not in events
    assert "TP1_HIT" not in events
    assert "TP2_HIT" not in events
    conn.close()


def test_legacy_open_ignores_stale_tp1_but_can_close_on_valid_tp2(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    historical = tmp_path / "prices"
    conn = connect(db)
    conn.execute(
        """
        INSERT INTO signal_outcome_ledger (
            signal_id, run_id, symbol, signal_date, setup_type,
            data_quality_status, plan_status, trigger_type, trigger_expiry_days,
            stop_loss, take_profit_1, take_profit_2, max_hold_days,
            current_status, trigger_date, entry_date, entry_price,
            created_at, updated_at, source_json
        ) VALUES (
            'LEGACY-MDKA', 'RUN-OLD', 'MDKA', '2026-08-01', 'PULLBACK',
            'VALID', 'ACCEPT', 'ENTRY_ZONE_TOUCH', 7,
            95, 99, 110, 5,
            'OPEN', '2026-08-04', '2026-08-04', 100,
            '2026-08-04T18:00:00+07:00', '2026-08-04T18:00:00+07:00', '{}'
        )
        """
    )
    conn.commit()

    _write_symbol_prices(
        historical,
        "MDKA",
        [
            {"Date": "2026-08-04", "Open": 100, "High": 103, "Low": 98, "Close": 101},
            {"Date": "2026-08-05", "Open": 101, "High": 105, "Low": 99, "Close": 104},
        ],
    )
    update_outcomes(conn, historical)
    row = conn.execute("SELECT * FROM signal_outcome_ledger WHERE signal_id='LEGACY-MDKA'").fetchone()
    assert row["current_status"] == "OPEN"
    assert not row["tp1_hit"]
    assert row["final_outcome"] is None

    _write_symbol_prices(
        historical,
        "MDKA",
        [
            {"Date": "2026-08-04", "Open": 100, "High": 103, "Low": 98, "Close": 101},
            {"Date": "2026-08-05", "Open": 101, "High": 105, "Low": 99, "Close": 104},
            {"Date": "2026-08-06", "Open": 105, "High": 112, "Low": 102, "Close": 111},
        ],
    )
    update_outcomes(conn, historical)
    row = conn.execute("SELECT * FROM signal_outcome_ledger WHERE signal_id='LEGACY-MDKA'").fetchone()
    assert row["current_status"] == "CLOSED"
    assert row["exit_reason"] == "TP2_HIT"
    assert row["final_outcome"] == "WIN"
    assert row["realized_return_pct"] > 0
    conn.close()


def test_integrity_helpers_follow_actual_economic_result() -> None:
    valid = validate_plan_geometry(100, 95, 105, 110)
    stale = validate_plan_geometry(112, 90, 105, 110)
    assert valid.valid is True
    assert stale.valid is False
    assert {"TP1_NOT_ABOVE_ENTRY", "TP2_NOT_ABOVE_ENTRY"} <= set(stale.reasons)
    assert economic_outcome(1.5) == "WIN"
    assert economic_outcome(-1.5) == "LOSS"
    assert outcome_consistency("WIN", -1.5) == "REPORTED_WIN_ECONOMIC_LOSS"


def _make_exit_efficiency_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE signal_outcome_ledger (
            signal_id TEXT PRIMARY KEY,
            run_id TEXT,
            symbol TEXT,
            signal_date TEXT,
            setup_type TEXT,
            data_quality_status TEXT,
            current_status TEXT,
            trigger_type TEXT,
            entry_date TEXT,
            entry_price REAL,
            reference_price REAL,
            stop_loss REAL,
            take_profit_1 REAL,
            take_profit_2 REAL,
            max_hold_days INTEGER,
            exit_date TEXT,
            exit_price REAL,
            exit_reason TEXT,
            final_outcome TEXT,
            realized_return_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            tp1_hit INTEGER,
            tp2_hit INTEGER,
            sl_hit INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE market_prices_daily (
            symbol TEXT,
            price_date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            source TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO signal_outcome_ledger VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        [
            (
                "GOOD", "RUN-1", "GOOD", "2026-08-01", "BREAKOUT", "VALID", "CLOSED",
                "ENTRY_ZONE_TOUCH", "2026-08-04", 100.0, 100.0, 95.0, 105.0, 110.0,
                5, "2026-08-05", 110.0, "TP2_HIT", "WIN", 10.0, 12.0, -2.0, 1, 1, 0,
            ),
            (
                "BAD", "RUN-2", "BAD", "2026-08-01", "PULLBACK", "VALID", "CLOSED",
                "CLOSE_ABOVE", "2026-08-04", 120.0, 100.0, 90.0, 105.0, 110.0,
                5, "2026-08-05", 110.0, "TP2_HIT", "WIN", -8.333333, 1.0, -3.0, 1, 1, 0,
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO market_prices_daily VALUES (?,?,?,?,?,?,?)",
        [
            ("GOOD", "2026-08-04", 100, 104, 98, 102, "TEST"),
            ("GOOD", "2026-08-05", 103, 112, 102, 110, "TEST"),
            ("GOOD", "2026-08-06", 110, 113, 108, 112, "TEST"),
            ("GOOD", "2026-08-07", 112, 115, 111, 114, "TEST"),
            ("GOOD", "2026-08-10", 114, 116, 112, 115, "TEST"),
            ("BAD", "2026-08-04", 120, 123, 117, 121, "TEST"),
            ("BAD", "2026-08-05", 118, 122, 108, 110, "TEST"),
        ],
    )
    conn.commit()
    conn.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_exit_efficiency_is_read_only_and_flags_historical_conflict(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    output = tmp_path / "performance"
    _make_exit_efficiency_db(db)
    before = _sha256(db)

    trades, by_setup, summary = run_analysis(db, output)

    after = _sha256(db)
    assert before == after
    assert summary["Triggered"] == 2
    assert summary["Geometry_Valid"] == 1
    assert summary["Geometry_Invalid"] == 1
    assert summary["Closed"] == 2
    assert summary["Clean_Closed"] == 1
    assert summary["Outcome_Inconsistencies"] == 1
    assert summary["Median_TP2_Distance_Pct"] == 10.0
    assert summary["Post_Exit_3D_Full_Eligible"] == 1

    bad = trades[trades["Signal_ID"] == "BAD"].iloc[0]
    assert bad["Plan_Geometry_Valid"] == False  # noqa: E712
    assert bad["Outcome_Consistency"] == "REPORTED_WIN_ECONOMIC_LOSS"
    assert bool(bad["Clean_Closed"]) is False

    assert not by_setup.empty
    for filename in (
        "EXIT_EFFICIENCY_TRADES.csv",
        "EXIT_EFFICIENCY_SUMMARY.csv",
        "EXIT_EFFICIENCY_BY_SETUP.csv",
        "EXIT_EFFICIENCY_INTEGRITY.csv",
    ):
        assert (output / filename).exists()


def test_performance_menu_exposes_exit_efficiency_without_renumbering() -> None:
    source = (Path(__file__).resolve().parents[1] / "maintenance/PERFORMANCE_MENU.bat").read_text(
        encoding="utf-8-sig"
    )
    assert "[14] Evaluasi Broker Period + Confidence x Period" in source
    assert "[15] Exit Efficiency + Data Integrity" in source
    assert 'if "%PERF_CHOICE%"=="15" goto SHOW_EXIT_EFFICIENCY' in source
    assert "modules\\analytics\\exit_efficiency.py" in source
