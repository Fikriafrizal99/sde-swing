from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .analytics import metric, num
from .styling import NAVY, SLATE, WHITE, status_fill

BRAND_NAME = "FTJ Performance Setup"
MUTED, BORDER = "64748B", "CBD5E1"
BLUE, RED, AMBER, GREEN = "#4F81BD", "#C0504D", "#F2B134", "#9BBB59"


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


def _setup_data(writer: pd.ExcelWriter) -> tuple[list[str], list[float], list[float]]:
    source = writer.sheets.get("Setup Snapshot")
    if source is None:
        return [], [], []
    setup_col = _column(source, "Setup")
    avg_col = _column(source, "Average_Return_Pct")
    wr_col = _column(source, "Win_Rate_Pct")
    if not setup_col:
        return [], [], []
    labels: list[str] = []
    avg_values: list[float] = []
    wr_values: list[float] = []
    for row in range(2, source.max_row + 1):
        setup = source.cell(row, setup_col).value
        if setup in (None, ""):
            continue
        labels.append(str(setup))
        avg_values.append(num(source.cell(row, avg_col).value) if avg_col else 0.0)
        wr_values.append(num(source.cell(row, wr_col).value) if wr_col else 0.0)
    return labels, avg_values, wr_values


def _score_data(writer: pd.ExcelWriter) -> tuple[list[str], list[float]]:
    source = writer.sheets.get("Score Analysis")
    if source is None:
        return [], []
    bucket_col = _column(source, "Score_Bucket")
    value_col = _column(source, "Average_Return_Pct")
    if not bucket_col or not value_col:
        return [], []
    labels: list[str] = []
    values: list[float] = []
    for row in range(2, source.max_row + 1):
        bucket = source.cell(row, bucket_col).value
        value = source.cell(row, value_col).value
        if bucket in (None, "") or not isinstance(value, (int, float)):
            continue
        labels.append(str(bucket))
        values.append(float(value))
    return labels, values


def _equity_data(writer: pd.ExcelWriter) -> tuple[list[str], list[float]]:
    source = writer.sheets.get("Equity Curve")
    if source is None:
        return [], []
    date_col = _column(source, "Exit_Date")
    idx_col = _column(source, "Exploratory_Index")
    if not date_col or not idx_col:
        return [], []
    dates: list[str] = []
    values: list[float] = []
    for row in range(2, source.max_row + 1):
        date_value = source.cell(row, date_col).value
        index_value = source.cell(row, idx_col).value
        if date_value in (None, "") or not isinstance(index_value, (int, float)):
            continue
        dates.append(str(date_value))
        values.append(float(index_value))
    return dates, values


def _style_axes(ax) -> None:
    ax.grid(axis="y", alpha=0.18, linewidth=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CBD5E1")
    ax.spines["bottom"].set_color("#CBD5E1")
    ax.tick_params(axis="both", colors="#475569", labelsize=8)
    ax.title.set_color("#0F172A")


def _add_figure(ws, fig, anchor: str, *, width: int, height: int) -> None:
    buffer = BytesIO()
    fig.savefig(buffer, format="png", dpi=135, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buffer.seek(0)
    image = XLImage(buffer)
    image.width = width
    image.height = height
    ws.add_image(image, anchor)


def _plot_outcomes(ws, row: pd.Series) -> None:
    labels = ["Win", "Loss", "Ambiguous"]
    values = [num(metric(row, label)) for label in labels]
    fig, ax = plt.subplots(figsize=(4.8, 3.1))
    if sum(values) > 0:
        ax.pie(
            values,
            labels=labels,
            autopct=lambda pct: f"{pct:.0f}%" if pct >= 3 else "",
            startangle=90,
            colors=[BLUE, RED, GREEN],
            textprops={"fontsize": 8},
        )
    else:
        ax.text(0.5, 0.5, "Belum ada closed outcome", ha="center", va="center", color="#64748B")
        ax.axis("off")
    ax.set_title("Canonical closed outcomes", fontsize=11, pad=10)
    fig.tight_layout()
    _add_figure(ws, fig, "A16", width=455, height=270)


def _plot_setup_return(ws, labels: list[str], values: list[float]) -> None:
    fig, ax = plt.subplots(figsize=(5.7, 3.1))
    if labels:
        ax.bar(labels, values, color=BLUE)
        ax.axhline(0, linewidth=0.8, color="#94A3B8")
        ax.tick_params(axis="x", rotation=20)
        _style_axes(ax)
    else:
        ax.text(0.5, 0.5, "Setup data belum tersedia", ha="center", va="center", color="#64748B")
        ax.axis("off")
    ax.set_title("Average return by setup", fontsize=11, pad=10)
    ax.set_ylabel("Return (%)", fontsize=8)
    fig.tight_layout()
    _add_figure(ws, fig, "H16", width=535, height=270)


def _plot_setup_wr(ws, labels: list[str], values: list[float]) -> None:
    fig, ax = plt.subplots(figsize=(5.7, 3.1))
    if labels:
        ax.bar(labels, values, color=BLUE)
        ax.set_ylim(0, max(100, max(values) * 1.1 if values else 100))
        ax.tick_params(axis="x", rotation=20)
        _style_axes(ax)
    else:
        ax.text(0.5, 0.5, "Setup data belum tersedia", ha="center", va="center", color="#64748B")
        ax.axis("off")
    ax.set_title("Win rate by setup", fontsize=11, pad=10)
    ax.set_ylabel("Win rate (%)", fontsize=8)
    fig.tight_layout()
    _add_figure(ws, fig, "A31", width=535, height=270)


def _plot_score(ws, labels: list[str], values: list[float]) -> None:
    fig, ax = plt.subplots(figsize=(5.7, 3.1))
    if labels:
        ax.barh(labels, values, color=BLUE)
        ax.axvline(0, linewidth=0.8, color="#94A3B8")
        _style_axes(ax)
    else:
        ax.text(0.5, 0.5, "Score bucket belum tersedia", ha="center", va="center", color="#64748B")
        ax.axis("off")
    ax.set_title("Exploratory return by score bucket", fontsize=11, pad=10)
    ax.set_xlabel("Average return (%)", fontsize=8)
    fig.tight_layout()
    _add_figure(ws, fig, "H31", width=535, height=270)


def _plot_equity(ws, dates: list[str], values: list[float]) -> None:
    fig, ax = plt.subplots(figsize=(11.7, 3.2))
    if len(values) >= 2:
        x = list(range(len(values)))
        ax.plot(x, values, linewidth=1.8, color=BLUE)
        step = max(1, len(x) // 7)
        ticks = x[::step]
        ax.set_xticks(ticks)
        ax.set_xticklabels([dates[i] for i in ticks], rotation=20, ha="right")
        _style_axes(ax)
    else:
        ax.text(0.5, 0.5, "Equity curve belum memiliki cukup data", ha="center", va="center", color="#64748B")
        ax.axis("off")
    ax.set_title("Exploratory raw-ledger equity curve (index 100)", fontsize=11, pad=10)
    ax.set_ylabel("Index", fontsize=8)
    fig.tight_layout()
    _add_figure(ws, fig, "A46", width=1085, height=265)


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

    setup_labels, setup_avg, setup_wr = _setup_data(writer)
    score_labels, score_values = _score_data(writer)
    equity_dates, equity_values = _equity_data(writer)

    # Render charts as PNG drawings instead of native Open XML chart parts.
    # This keeps the dashboard visual while avoiding Excel desktop failures
    # observed with xl/charts/*.xml generated by openpyxl on the production data.
    _plot_outcomes(ws, row)
    _plot_setup_return(ws, setup_labels, setup_avg)
    _plot_setup_wr(ws, setup_labels, setup_wr)
    _plot_score(ws, score_labels, score_values)
    _plot_equity(ws, equity_dates, equity_values)

    ws.merge_cells("A61:N62")
    ws["A61"] = (
        "Catatan: KPI utama berasal dari canonical PERFORMANCE_SUMMARY. Score Analysis, Time Analysis, dan Equity Curve "
        "berasal dari raw closed ledger dan bersifat exploratory; bukan pengganti canonical performance atau actual portfolio P&L. "
        "Grafik dashboard dirender sebagai gambar statis untuk kompatibilitas Excel desktop."
    )
    ws["A61"].font = Font(color=MUTED, italic=True, size=9)
    ws["A61"].alignment = Alignment(wrap_text=True)
