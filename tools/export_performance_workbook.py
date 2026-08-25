#!/usr/bin/env python3
from __future__ import annotations

"""Build one downloadable Excel workbook from SDE Swing performance artifacts.

The exporter does not recalculate trading outcomes. It packages CSV artifacts
already produced by the canonical performance, Broker Period, and Exit
Efficiency modules so the workbook remains a presentation/export layer only.
"""

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.styles import Alignment, Font


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data/output/analytics/performance"
DEFAULT_OUTPUT = DEFAULT_INPUT / "exports"

REPORT_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("PERFORMANCE_SUMMARY.csv", "Performance Summary", True),
    ("PERFORMANCE_BY_SETUP.csv", "By Setup", False),
    ("PERFORMANCE_BY_SIGNAL_TYPE.csv", "By Signal", False),
    ("PERFORMANCE_BY_BROKER_CONFIDENCE.csv", "By Broker Confidence", False),
    ("PERFORMANCE_BY_MARKET_REGIME.csv", "By Market Regime", False),
    ("BROKER_PERIOD_PERFORMANCE.csv", "Broker Period", False),
    ("BROKER_CONFIDENCE_BY_PERIOD.csv", "Broker Confidence Period", False),
    ("EXIT_EFFICIENCY_SUMMARY.csv", "Exit Efficiency", False),
    ("EXIT_EFFICIENCY_BY_SETUP.csv", "Exit Efficiency Setup", False),
    ("EXIT_EFFICIENCY_TRADES.csv", "Exit Trades", False),
    ("EXIT_EFFICIENCY_INTEGRITY.csv", "Integrity Flags", False),
    ("SIGNAL_OUTCOME_LEDGER.csv", "Signal Ledger", False),
    ("ACTIVE_RECOMMENDATIONS.csv", "Active Recommendations", False),
    ("LIFECYCLE_EVENTS.csv", "Lifecycle Events", False),
    ("SIGNAL_RECOMMENDATION_HISTORY.csv", "Recommendation History", False),
    ("PORTFOLIO_POSITIONS.csv", "Portfolio Positions", False),
)


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _overview(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame(columns=["Metric", "Value"])
    row = summary.iloc[0]
    return pd.DataFrame(
        [{"Metric": str(column), "Value": row[column]} for column in summary.columns]
    )


def _autosize_and_style(writer: pd.ExcelWriter) -> None:
    for worksheet in writer.sheets.values():
        worksheet.freeze_panes = "A2"
        if worksheet.max_row >= 1 and worksheet.max_column >= 1:
            worksheet.auto_filter.ref = worksheet.dimensions
        for cell in worksheet[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="top", wrap_text=True)

        for column_cells in worksheet.iter_cols():
            letter = column_cells[0].column_letter
            max_length = 0
            for cell in column_cells[: min(len(column_cells), 250)]:
                value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(value))
            worksheet.column_dimensions[letter].width = min(max(max_length + 2, 12), 42)


def build_workbook(
    input_dir: Path,
    output_dir: Path,
    *,
    output_path: Path | None = None,
) -> tuple[Path, pd.DataFrame]:
    input_dir = input_dir.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()

    core_summary = input_dir / "PERFORMANCE_SUMMARY.csv"
    if not core_summary.exists():
        raise FileNotFoundError(
            f"Core performance belum tersedia: {core_summary}. "
            "Jalankan Update outcome terlebih dahulu."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"SDE_SWING_PERFORMANCE_FULL_{stamp}.xlsx"
    else:
        output_path = output_path.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = _read_csv(core_summary)
    manifest_rows: list[dict[str, Any]] = []

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        _overview(summary).to_excel(writer, sheet_name="Overview", index=False)

        for filename, sheet_name, required in REPORT_SPECS:
            path = input_dir / filename
            if not path.exists():
                manifest_rows.append(
                    {
                        "Source_File": filename,
                        "Sheet": sheet_name,
                        "Status": "MISSING_REQUIRED" if required else "MISSING_OPTIONAL",
                        "Rows": 0,
                    }
                )
                if required:
                    raise FileNotFoundError(f"Required performance artifact tidak ditemukan: {path}")
                continue

            frame = summary if filename == "PERFORMANCE_SUMMARY.csv" else _read_csv(path)
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
            manifest_rows.append(
                {
                    "Source_File": filename,
                    "Sheet": sheet_name,
                    "Status": "INCLUDED",
                    "Rows": int(len(frame)),
                }
            )

        manifest = pd.DataFrame(manifest_rows)
        manifest.to_excel(writer, sheet_name="Manifest", index=False)
        _autosize_and_style(writer)

    return output_path, pd.DataFrame(manifest_rows)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export seluruh SDE Swing performance artifacts ke satu workbook Excel"
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-file", type=Path, default=None)
    return parser


def main() -> int:
    args = make_parser().parse_args()
    path, manifest = build_workbook(
        args.input_dir,
        args.output_dir,
        output_path=args.output_file,
    )
    included = int((manifest["Status"] == "INCLUDED").sum()) if not manifest.empty else 0
    optional_missing = (
        int((manifest["Status"] == "MISSING_OPTIONAL").sum()) if not manifest.empty else 0
    )
    print("Performance workbook berhasil dibuat.")
    print(f"Included sections : {included}")
    print(f"Optional missing  : {optional_missing}")
    print(f"File              : {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
