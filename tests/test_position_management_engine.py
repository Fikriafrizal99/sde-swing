from __future__ import annotations

import sqlite3

from modules.broker_bridge.broker_navigator_export import merge_symbols, read_open_portfolio_symbols
from modules.portfolio.position_management_engine import choose_management, connect, persist_result


def _plan():
    return {
        "position_id": "p1",
        "symbol": "TEST",
        "buy_date": "2026-08-01",
        "buy_price": 1000.0,
        "initial_stop_loss": 950.0,
        "initial_tp1": 1100.0,
        "initial_tp2": 1200.0,
    }


def _bullish_tech(**overrides):
    data = {
        "status": "VALID",
        "current_price": 1250.0,
        "max_high_since_buy": 1260.0,
        "min_low_since_buy": 980.0,
        "strong_bullish": True,
        "bearish": False,
        "overextended": False,
        "atr": 30.0,
        "sma20": 1180.0,
    }
    data.update(overrides)
    return data


def test_tp2_hit_can_continue_without_rewriting_initial_target():
    plan = _plan()
    result = choose_management(
        plan=plan,
        tech=_bullish_tech(),
        broker={"state": "ACCUMULATION"},
        sector="LEADING",
        market="BULLISH",
        previous={},
    )

    assert result["milestone"] == "TP2_HIT"
    assert result["action"] == "HOLD_AFTER_TP2"
    assert result["extended_target"] > plan["initial_tp2"]
    assert result["active_stop_loss"] >= plan["buy_price"]
    assert plan["initial_tp2"] == 1200.0


def test_initial_stop_breach_forces_exit_recommendation_but_does_not_close_portfolio(tmp_path):
    db = tmp_path / "history.db"
    conn = connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE portfolio_positions (
                position_id TEXT PRIMARY KEY,
                symbol TEXT,
                buy_date TEXT,
                quantity REAL,
                buy_price REAL,
                current_status TEXT,
                signal_id TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO portfolio_positions VALUES ('p1','TEST','2026-08-01',10,1000,'OPEN','')"
        )
        conn.commit()

        result = choose_management(
            plan=_plan(),
            tech=_bullish_tech(min_low_since_buy=940.0),
            broker={"state": "ACCUMULATION"},
            sector="LEADING",
            market="BULLISH",
            previous={},
        )
        assert result["action"] == "EXIT"

        persisted = {
            "position_id": "p1",
            "symbol": "TEST",
            "analysis_date": "2026-08-08",
            "data_date": "2026-08-08",
            "current_price": 1250.0,
            "pnl_pct": 25.0,
            "milestone": result["milestone"],
            "technical_state": "BULLISH",
            "broker_state": "ACCUMULATION",
            "sector_state": "LEADING",
            "market_state": "BULLISH",
            "management_action": result["action"],
            "active_stop_loss": result["active_stop_loss"],
            "extended_target": result["extended_target"],
            "reason": result["reason"],
            "data_quality_status": result["data_quality_status"],
        }
        persist_result(conn, persisted, {})
        status = conn.execute(
            "SELECT current_status FROM portfolio_positions WHERE position_id='p1'"
        ).fetchone()[0]
        assert status == "OPEN"
    finally:
        conn.close()


def test_broker_bridge_appends_open_portfolio_symbols_without_reordering_candidates(tmp_path):
    db = tmp_path / "history.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE portfolio_positions (symbol TEXT, buy_date TEXT, current_status TEXT)"
        )
        conn.executemany(
            "INSERT INTO portfolio_positions VALUES (?, ?, ?)",
            [
                ("TINS", "2026-08-01", "OPEN"),
                ("ANTM", "2026-08-02", "OPEN"),
                ("PGAS", "2026-08-03", "CLOSED"),
            ],
        )
        conn.commit()
    finally:
        conn.close()

    portfolio = read_open_portfolio_symbols(db)
    merged = merge_symbols(["BBCA", "ANTM", "MDKA"], portfolio)

    assert portfolio == ["TINS", "ANTM"]
    assert merged == ["BBCA", "ANTM", "MDKA", "TINS"]
