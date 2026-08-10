from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from modules.decision.exchange_status import apply_exchange_status_to_decisions


def test_exchange_status_accepts_text_when_empty_columns_were_inferred_float(tmp_path: Path) -> None:
    decision_path = tmp_path / "decision.csv"
    enrichment_path = tmp_path / "exchange.json"

    pd.DataFrame(
        {
            "Symbol": ["BBCA"],
            "Decision_Status_Final": [float("nan")],
            "Exchange_Status": [float("nan")],
            "Risk_Flags": [float("nan")],
            "Exchange_Veto": [float("nan")],
            "Veto": [float("nan")],
            "Veto_Reason": [float("nan")],
            "Exchange_History_Candles": [float("nan")],
            "Technical_Score": [72.5],
        }
    ).to_csv(decision_path, index=False)
    enrichment_path.write_text(
        json.dumps(
            {
                "symbols": {
                    "BBCA": {
                        "status": "SUSPENDED",
                        "risk_flags": ["SUSPENDED"],
                        "history_candle_count": 123,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = apply_exchange_status_to_decisions(decision_path, enrichment_path)

    assert result["status"] == "APPLIED"
    saved = pd.read_csv(decision_path, low_memory=False)
    row = saved.iloc[0]
    assert row["Decision_Status_Final"] == "BLOCKED"
    assert row["Exchange_Status"] == "SUSPENDED"
    assert row["Risk_Flags"] == "SUSPENDED"
    assert row["Exchange_Veto"] == "SUSPENDED"
    assert row["Veto"] == "SUSPENDED"
    assert row["Veto_Reason"] == "SUSPENDED"
    assert int(row["Exchange_History_Candles"]) == 123
    assert float(row["Technical_Score"]) == 72.5


def test_uma_downgrade_does_not_change_numeric_scores(tmp_path: Path) -> None:
    decision_path = tmp_path / "decision.csv"
    enrichment_path = tmp_path / "exchange.json"

    pd.DataFrame(
        {
            "Symbol": ["TLKM"],
            "Decision_Status_Final": ["BUY READY"],
            "Risk_Flags": [float("nan")],
            "Veto": [float("nan")],
            "Technical_Score": [81.25],
            "Broker_Score": [67.0],
        }
    ).to_csv(decision_path, index=False)
    enrichment_path.write_text(
        json.dumps(
            {
                "symbols": {
                    "TLKM": {
                        "status": "NORMAL",
                        "risk_flags": ["UMA"],
                        "history_candle_count": 400,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    apply_exchange_status_to_decisions(decision_path, enrichment_path)

    saved = pd.read_csv(decision_path, low_memory=False)
    row = saved.iloc[0]
    assert row["Decision_Status_Final"] == "BUY CANDIDATE"
    assert row["Risk_Flags"] == "UMA"
    assert float(row["Technical_Score"]) == 81.25
    assert float(row["Broker_Score"]) == 67.0
