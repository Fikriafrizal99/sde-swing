from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from modules.analytics.lifecycle_contract import (
    evaluate_trade_path,
    lifecycle_state_dict,
    prepare_lifecycle_bars,
)
from modules.analytics.outcome_tracker import connect, export_reports
from modules.analytics.replay_contract import (
    EXPECTED_CONTEXTUAL_DIVERGENCE,
    FULL_LIVE_REPLAY,
    PRICE_DERIVED_CONTEXT,
    PRICE_LIFECYCLE_ONLY,
    PRICE_REPRODUCIBLE_LIFECYCLE,
    REPLAY_CONTRACT_VERSION,
    RUNTIME_CONTEXT_OVERLAY,
    classify_exit_reasons,
    historical_replay_metadata,
)
from modules.backtesting.backtest_engine import evaluate_signal
from modules.exit_engine.exit_engine import update_active_trade


def _price_bars() -> pd.DataFrame:
    return prepare_lifecycle_bars(pd.DataFrame([
        {"Date": "2026-08-04", "Open": 100, "High": 106, "Low": 99, "Close": 104},
        {"Date": "2026-08-05", "Open": 104, "High": 111, "Low": 103, "Close": 109},
        {"Date": "2026-08-06", "Open": 109, "High": 112, "Low": 107, "Close": 110},
    ]))


def test_price_only_lifecycle_replay_is_deterministic() -> None:
    bars = _price_bars()
    first = evaluate_trade_path(
        bars,
        entry_price=100,
        initial_stop=95,
        target_1=110,
        target_2=120,
        max_hold_days=20,
    )
    second = evaluate_trade_path(
        bars.copy(deep=True),
        entry_price=100,
        initial_stop=95,
        target_1=110,
        target_2=120,
        max_hold_days=20,
    )

    assert lifecycle_state_dict(first) == lifecycle_state_dict(second)
    assert first.tp1_hit is True
    assert first.current_status == "OPEN"


def test_runtime_overlay_divergence_is_explicitly_classified() -> None:
    historical = evaluate_trade_path(
        prepare_lifecycle_bars(pd.DataFrame([{
            "Date": "2026-08-05",
            "Open": 100,
            "High": 102,
            "Low": 99,
            "Close": 101,
        }])),
        entry_price=100,
        initial_stop=90,
        target_1=120,
        target_2=130,
        max_hold_days=20,
    )
    trade = pd.Series({
        "Symbol": "BBCA",
        "Entry_Price": 100,
        "Initial_Stop": 90,
        "Current_Stop": 90,
        "Target_1": 120,
        "Target_2": 130,
        "Highest_Close": 100,
        "Holding_Days": 0,
        "Last_Update": "2026-08-04",
        "Status": "ACTIVE",
    })
    live_prices = pd.DataFrame([{
        "Date": "2026-08-05",
        "Open": 100,
        "High": 102,
        "Low": 99,
        "Close": 101,
        "EMA20": 95,
        "ATR14": 2,
    }])
    decision = pd.Series({"Decision": "BUY", "Broker_Confirmation": "DISTRIBUTION"})

    live, alert = update_active_trade(trade, decision, live_prices, 20)
    classification = classify_exit_reasons(live["Exit_Reason"])
    replay = historical_replay_metadata()

    assert historical.current_status == "OPEN"
    assert alert is not None
    assert live["Status"] == "CLOSED"
    assert classification[RUNTIME_CONTEXT_OVERLAY] == ["BROKER_DISTRIBUTION"]
    assert replay["replay_scope"] == PRICE_REPRODUCIBLE_LIFECYCLE
    assert replay["replay_fidelity"] == PRICE_LIFECYCLE_ONLY
    assert replay["runtime_context_available"] is False
    assert replay["full_live_replay"] is False
    assert replay["replay_fidelity"] != FULL_LIVE_REPLAY
    assert replay["divergence_classification"] == EXPECTED_CONTEXTUAL_DIVERGENCE

    mixed = classify_exit_reasons(
        "CLOSE_BELOW_EMA20 | DECISION_DOWNGRADE_AVOID | MAX_HOLD_20D"
    )
    assert mixed[PRICE_DERIVED_CONTEXT] == ["CLOSE_BELOW_EMA20"]
    assert mixed[RUNTIME_CONTEXT_OVERLAY] == ["DECISION_DOWNGRADE_AVOID"]
    assert mixed[PRICE_REPRODUCIBLE_LIFECYCLE] == ["MAX_HOLD_20D"]


def test_backtest_and_performance_artifacts_do_not_claim_full_live_replay(
    tmp_path: Path,
) -> None:
    signal = pd.Series({
        "Signal_Date": pd.Timestamp("2026-08-01"),
        "Symbol": "BBCA",
        "Gated_Decision": "BUY ON TRIGGER",
        "Plan_Status": "ACCEPT",
        "Setup_Type": "BREAKOUT",
        "Entry_Zone_Low": 100,
        "Entry_Zone_High": 105,
        "Initial_Stop": 95,
        "Target_1": 110,
        "Target_2": 120,
        "Max_Hold_Days": 20,
        "Estimated_Slippage_Pct": 0,
    })
    result = evaluate_signal(signal, _price_bars(), [1], "next_open", 20)

    assert result["Replay_Contract_Version"] == REPLAY_CONTRACT_VERSION
    assert result["Replay_Scope"] == PRICE_REPRODUCIBLE_LIFECYCLE
    assert result["Replay_Fidelity"] == PRICE_LIFECYCLE_ONLY
    assert result["Full_Live_Replay"] is False

    output = tmp_path / "performance"
    conn = connect(tmp_path / "history.db")
    payload = export_reports(conn, output, tmp_path / "prices")
    conn.close()

    persisted = json.loads(
        (output / "PERFORMANCE_SUMMARY.json").read_text(encoding="utf-8")
    )
    summary = pd.read_csv(output / "PERFORMANCE_SUMMARY.csv")

    for report in (payload, persisted):
        replay = report["replay_contract"]
        assert replay["replay_contract_version"] == REPLAY_CONTRACT_VERSION
        assert replay["replay_fidelity"] == PRICE_LIFECYCLE_ONLY
        assert replay["full_live_replay"] is False
        assert RUNTIME_CONTEXT_OVERLAY in replay["excluded_exit_families"]
    assert summary.loc[0, "Replay_Fidelity"] == PRICE_LIFECYCLE_ONLY
    assert bool(summary.loc[0, "Full_Live_Replay"]) is False
