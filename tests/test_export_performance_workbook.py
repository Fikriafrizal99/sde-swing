from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from openpyxl import load_workbook

from tools.export_performance_workbook import build_workbook


def test_build_workbook_creates_ftj_dashboard_and_analysis(tmp_path: Path):
    source = tmp_path / "performance"
    export_dir = tmp_path / "exports"
    source.mkdir()

    pd.DataFrame([{
        "Group": "OVERALL",
        "Signals": 10,
        "Triggered": 8,
        "Trigger_Rate_Pct": 80.0,
        "Closed": 6,
        "Win": 4,
        "Loss": 2,
        "Ambiguous": 0,
        "Win_Rate_Pct": 66.67,
        "Average_Return_Pct": 2.5,
        "Profit_Factor": 2.0,
        "Expectancy_Pct": 2.5,
        "Average_Holding_Days": 4.0,
        "Canonical_Excluded_Episodes": 1,
        "Start_Date": "2026-08-01",
        "End_Date": "2026-08-25",
        "Replay_Fidelity": "PRICE_LIFECYCLE_ONLY",
        "Full_Live_Replay": False,
    }]).to_csv(source / "PERFORMANCE_SUMMARY.csv", index=False)

    pd.DataFrame([
        {
            "Group": "PULLBACK",
            "Signals": 5,
            "Triggered": 4,
            "Trigger_Rate_Pct": 80.0,
            "Closed": 3,
            "Win": 2,
            "Loss": 1,
            "Ambiguous": 0,
            "Win_Rate_Pct": 66.67,
            "Average_Return_Pct": 3.2,
            "Profit_Factor": 2.5,
            "Expectancy_Pct": 3.2,
        },
        {
            "Group": "DEVELOPING",
            "Signals": 5,
            "Triggered": 4,
            "Trigger_Rate_Pct": 80.0,
            "Closed": 3,
            "Win": 1,
            "Loss": 2,
            "Ambiguous": 0,
            "Win_Rate_Pct": 33.33,
            "Average_Return_Pct": -2.1,
            "Profit_Factor": 0.5,
            "Expectancy_Pct": -2.1,
        },
    ]).to_csv(source / "PERFORMANCE_BY_SETUP.csv", index=False)

    pd.DataFrame([
        {
            "signal_id": "A",
            "symbol": "AAA",
            "signal_date": "2026-08-01",
            "exit_date": "2026-08-03",
            "current_status": "CLOSED",
            "final_outcome": "WIN",
            "setup_type": "PULLBACK",
            "score": 72,
            "realized_return_pct": 5.0,
        },
        {
            "signal_id": "B",
            "symbol": "BBB",
            "signal_date": "2026-08-02",
            "exit_date": "2026-08-04",
            "current_status": "CLOSED",
            "final_outcome": "LOSS",
            "setup_type": "DEVELOPING",
            "score": 68,
            "realized_return_pct": -3.0,
        },
    ]).to_csv(source / "SIGNAL_OUTCOME_LEDGER.csv", index=False)

    pd.DataFrame([{
        "position_id": "P1",
        "symbol": "AAA",
        "current_status": "CLOSED",
        "realized_return_pct": 2.0,
    }]).to_csv(source / "PORTFOLIO_POSITIONS.csv", index=False)

    output, manifest = build_workbook(source, export_dir)

    assert output.exists()
    assert output.parent == export_dir.resolve()
    assert output.name.startswith("FTJ_PERFORMANCE_SETUP_")
    assert "MISSING_OPTIONAL" in set(manifest["Status"])

    workbook = pd.ExcelFile(output)
    assert workbook.sheet_names[0] == "Dashboard"
    for expected in (
        "Overview",
        "Universe Summary",
        "Setup Snapshot",
        "Score Analysis",
        "Model vs Portfolio",
        "Data Quality",
        "Performance Summary",
        "Setup Performance",
        "Manifest",
    ):
        assert expected in workbook.sheet_names

    overview = pd.read_excel(output, sheet_name="Overview")
    values = dict(zip(overview["Metric"], overview["Value"]))
    assert int(values["Win"]) == 4
    assert int(values["Loss"]) == 2

    snapshot = pd.read_excel(output, sheet_name="Setup Snapshot")
    assert {"Setup", "Status", "Win_Rate_Pct", "Average_Return_Pct", "Profit_Factor"}.issubset(snapshot.columns)

    score = pd.read_excel(output, sheet_name="Score Analysis")
    assert set(score["Score_Bucket"]) == {"65-69", "70-74"}

    xlsx = load_workbook(output)
    assert xlsx["Dashboard"]["A1"].value == "FTJ Performance Setup"
    assert len(xlsx["Dashboard"]._charts) >= 4


def test_build_workbook_requires_core_summary(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Core performance belum tersedia"):
        build_workbook(tmp_path / "missing", tmp_path / "exports")
