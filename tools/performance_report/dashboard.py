from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .analytics import metric, num
from .styling import NAVY, SLATE, WHITE, status_fill

BRAND_NAME = "FTJ Performance Setup"
MUTED, BORDER = "64748B", "CBD5E1"


def _column(ws, name: str) -> int | None:
    for cell in ws[1]:
        if str(cell.value or "") == name:
            return cell.column
    return None


def _status(summary: pd.DataFrame) -> tuple[str, str]:
    if summary.empty:
        return "NO DATA", "Core performance summary kosong."
    row = summary.iloc[0]
    closed = int(num(metric(row, "Closed", "Closed_Outcomes")))
    wr = num(metric(row, "Win_Rate_Pct"))
    avg = num(metric(row, "Average_Return_Pct"))
    pf = num(metric(row, "Profit_Factor"))
    if closed < 20:
        return "LOW SAMPLE", "Edge belum cukup sampel untuk dinilai stabil."
    if avg > 0 and pf >= 1.3 and wr >= 50:
        if closed < 100:
            return "PROMISING EDGE", "Positif, tetapi masih butuh sampel dan market regime tambahan."
        return "POSITIVE EDGE", "Canonical sample sudah lebih matang; tetap monitor regime dan integrity."
    if avg < 0 or (pf and pf < 1):
        return "NEEDS REVIEW", "Canonical expectancy belum sehat pada periode report."
    return "WATCH", "Belum ada sinyal statistik yang cukup kuat untuk klasifikasi lebih tinggi."


def _card(ws, start: int, end: int, row: int, label: str, value: Any, fmt: str) -> None:
    ws.merge_cells(start_row=row, start_column=start, end_row=row, end_column=end)
    ws.merge_cells(start_row=row + 1, start_column=start, end_row=row + 2, end_column=end)
    title, data = ws.cell(row, start), ws.cell(row + 1, start)
    title.value, data.value = label, value
    title.fill = PatternFill("solid", fgColor=SLATE)
    data.fill = PatternFill("solid", fgColor="F8FAFC")
    title.font = Font(color=WHITE, bold=True, size=10)
    data.font = Font(color=NAVY, bold=True, size=18)
    title.alignment = data.alignment = Alignment(horizontal="center", vertical="center")
    data.number_format = fmt
    thin = Side(style="thin", color=BORDER)
    for r in range(row, row + 3):
        for c in range(start, end + 1):
            ws.cell(r, c).border = Border(left=thin, right=thin, top=thin, bottom=thin)


def _write_setup_helper(writer: pd.ExcelWriter, dashboard) -> int:
    """Copy chart-ready setup values onto Dashboard hidden helper columns.

    Keeping chart formulas on the same worksheet avoids cross-sheet chart
    relationships, which are more fragile across desktop Excel versions.
    Returns the last helper row written (including header).
    """
    source = writer.sheets.get("Setup Snapshot")
    dashboard["S1"], dashboard["T1"], dashboard["U1"] = "Setup", "Average_Return_Pct", "Win_Rate_Pct"
    if source is None:
        return 1
    setup_col = _column(source, "Setup")
    avg_col = _column(source, "Average_Return_Pct")
    wr_col = _column(source, "Win_Rate_Pct")
    if not setup_col:
        return 1
    target = 2
    for row in range(2, source.max_row + 1):
        setup = source.cell(row, setup_col).value
        if setup in (None, ""):
            continue
        avg = source.cell(row, avg_col).value if avg_col else None
        wr = source.cell(row, wr_col).value if wr_col else None
        dashboard.cell(target, 19, setup)
        dashboard.cell(target, 20, avg)
        dashboard.cell(target, 21, wr)
        target += 1
    return target - 1


def _write_score_helper(writer: pd.ExcelWriter, dashboard) -> int:
    source = writer.sheets.get("Score Analysis")
    dashboard["W1"], dashboard["X1"] = "Score_Bucket", "Average_Return_Pct"
    if source is None:
        return 1
    bucket_col = _column(source, "Score_Bucket")
    value_col = _column(source, "Average_Return_Pct")
    if not bucket_col or not value_col:
        return 1
    target = 2
    for row in range(2, source.max_row + 1):
        bucket = source.cell(row, bucket_col).value
        value = source.cell(row, value_col).value
        if bucket in (None, "") or not isinstance(value, (int, float)):
            continue
        dashboard.cell(target, 23, bucket)
        dashboard.cell(target, 24, value)
        target += 1
    return target - 1


def _write_equity_helper(writer: pd.ExcelWriter, dashboard) -> int:
    source = writer.sheets.get("Equity Curve")
    dashboard["Z1"], dashboard["AA1"] = "Exit_Date", "Exploratory_Index"
    if source is None:
        return 1
    date_col = _column(source, "Exit_Date")
    idx_col = _column(source, "Exploratory_Index")
    if not date_col or not idx_col:
        return 1
    target = 2
    for row in range(2, source.max_row + 1):
        date_value = source.cell(row, date_col).value
        index_value = source.cell(row, idx_col).value
        if date_value in (None, "") or not isinstance(index_value, (int, float)):
            continue
        dashboard.cell(target, 26, date_value)
        dashboard.cell(target, 27, index_value)
        target += 1
    return target - 1


def _bar_local(ws, category_col: int, value_col: int, max_row: int, title: str, horizontal: bool = False) -> BarChart | None:
    if max_row < 2:
        return None
    chart = BarChart()
    chart.type = "bar" if horizontal else "col"
    chart.style = 10
    chart.height, chart.width, chart.title = 7.2, 14.2, title
    chart.add_data(Reference(ws, min_col=value_col, min_row=1, max_row=max_row), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=category_col, min_row=2, max_row=max_row))
    chart.legend = None
    return chart


def build_dashboard(writer: pd.ExcelWriter, summary: pd.DataFrame) -> None:
    ws = writer.sheets["Dashboard"]
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A5"
    for col in range(1, 15):
        ws.column_dimensions[get_column_letter(col)].width = 12

    ws.merge_cells("A1:N2")
    ws["A1"] = BRAND_NAME
    ws["A1"].font = Font(size=24, bold=True, color=WHITE)
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A1"].alignment = Alignment(vertical="center")

    row = summary.iloc[0] if not summary.empty else pd.Series(dtype=object)
    ws.merge_cells("A3:N3")
    ws["A3"] = (
        f"Canonical KPI dashboard | Periode {metric(row, 'Start_Date', default='-')} s.d. "
        f"{metric(row, 'End_Date', default='-')} | Generated {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}"
    )
    ws["A3"].font = Font(color=MUTED, italic=True, size=10)

    cards = [
        ("Closed", metric(row, "Closed", "Closed_Outcomes"), "0"),
        ("Win Rate", metric(row, "Win_Rate_Pct"), '0.00"%"'),
        ("Avg Return", metric(row, "Average_Return_Pct"), '0.00"%"'),
        ("Profit Factor", metric(row, "Profit_Factor"), "0.00"),
        ("Expectancy", metric(row, "Expectancy_Pct"), '0.00"%"'),
        ("Trigger Rate", metric(row, "Trigger_Rate_Pct"), '0.00"%"'),
        ("Avg Hold", metric(row, "Average_Holding_Days"), '0.00" d"'),
        ("Excluded", metric(row, "Canonical_Excluded_Episodes"), "0"),
    ]
    spans = [(1, 3), (4, 6), (7, 9), (10, 12)]
    for idx, (label, value, fmt) in enumerate(cards):
        _card(ws, *spans[idx % 4], 5 if idx < 4 else 9, label, value, fmt)

    status, note = _status(summary)
    ws.merge_cells("A13:N13")
    ws["A13"] = f"REPORT STATUS: {status}"
    ws["A13"].font = Font(size=14, bold=True, color=NAVY)
    ws["A13"].fill = PatternFill("solid", fgColor=status_fill(status))
    ws["A13"].alignment = Alignment(horizontal="center")
    ws.merge_cells("A14:N14")
    ws["A14"] = note
    ws["A14"].font = Font(color=SLATE, italic=True)
    ws["A14"].alignment = Alignment(horizontal="center")

    # All chart sources live on Dashboard hidden helper columns. This avoids
    # fragile cross-sheet chart formula relationships in desktop Excel.
    ws["P1"], ws["Q1"] = "Outcome", "Count"
    for r, name in enumerate(("Win", "Loss", "Ambiguous"), 2):
        ws.cell(r, 16, name)
        ws.cell(r, 17, metric(row, name))

    setup_last = _write_setup_helper(writer, ws)
    score_last = _write_score_helper(writer, ws)
    equity_last = _write_equity_helper(writer, ws)
    for col in ("P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AA"):
        ws.column_dimensions[col].hidden = True

    pie = PieChart()
    pie.title, pie.style, pie.height, pie.width = "Canonical closed outcomes", 10, 7.2, 12
    pie.add_data(Reference(ws, min_col=17, min_row=1, max_row=4), titles_from_data=True)
    pie.set_categories(Reference(ws, min_col=16, min_row=2, max_row=4))
    ws.add_chart(pie, "A16")

    chart = _bar_local(ws, 19, 20, setup_last, "Average return by setup")
    if chart:
        ws.add_chart(chart, "H16")
    chart = _bar_local(ws, 19, 21, setup_last, "Win rate by setup")
    if chart:
        ws.add_chart(chart, "A31")
    chart = _bar_local(ws, 23, 24, score_last, "Exploratory return by score bucket", horizontal=True)
    if chart:
        ws.add_chart(chart, "H31")

    if equity_last >= 3:
        chart = LineChart()
        chart.title, chart.style, chart.height, chart.width = "Exploratory raw-ledger equity curve (index 100)", 13, 7, 28.8
        chart.y_axis.title, chart.x_axis.title = "Index", "Exit sequence"
        chart.add_data(Reference(ws, min_col=27, min_row=1, max_row=equity_last), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=26, min_row=2, max_row=equity_last))
        chart.legend = None
        ws.add_chart(chart, "A46")

    ws.merge_cells("A61:N62")
    ws["A61"] = (
        "Catatan: KPI utama berasal dari canonical PERFORMANCE_SUMMARY. Score Analysis, Time Analysis, dan Equity Curve "
        "berasal dari raw closed ledger dan bersifat exploratory; bukan pengganti canonical performance atau actual portfolio P&L."
    )
    ws["A61"].font = Font(color=MUTED, italic=True, size=9)
    ws["A61"].alignment = Alignment(wrap_text=True)
