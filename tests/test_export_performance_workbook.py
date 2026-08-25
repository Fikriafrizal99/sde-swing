from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tools.export_performance_workbook import build_workbook


def test_build_workbook_combines_available_reports(tmp_path: Path):
    source = tmp_path / "performance"
    export_dir = tmp_path / "exports"
    source.mkdir()

    pd.DataFrame([{
        "Signals": 10,
        "Triggered": 8,
        "Closed": 6,
        "Win": 4,
        "Loss": 2,
        "Win_Rate_Pct": 66.67,
    }]).to_csv(source / "PERFORMANCE_SUMMARY.csv", index=False)

    pd.DataFrame([{
        "Group": "BREAKOUT",
        "Signals": 5,
        "Closed": 3,
        "Win": 2,
        "Loss": 1,
    }]).to_csv(source / "PERFORMANCE_BY_SETUP.csv", index=False)

    output, manifest = build_workbook(source, export_dir)

    assert output.exists()
    assert output.parent == export_dir.resolve()
    assert "MISSING_OPTIONAL" in set(manifest["Status"])

    workbook = pd.ExcelFile(output)
    assert "Overview" in workbook.sheet_names
    assert "Performance Summary" in workbook.sheet_names
    assert "By Setup" in workbook.sheet_names
    assert "Manifest" in workbook.sheet_names

    overview = pd.read_excel(output, sheet_name="Overview")
    values = dict(zip(overview["Metric"], overview["Value"]))
    assert int(values["Win"]) == 4
    assert int(values["Loss"]) == 2


def test_build_workbook_requires_core_summary(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Core performance belum tersedia"):
        build_workbook(tmp_path / "missing", tmp_path / "exports")
