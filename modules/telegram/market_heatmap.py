from __future__ import annotations

"""Presentation-only market heatmap for the SDE Swing Post Market report.

The renderer reads the already-produced EOD technical snapshot. It does not
run screening, broker fusion, decision, entry-plan, or portfolio logic and it
never writes back into an engine artifact.
"""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd


_BG = "#070B10"
_PANEL = "#0C1218"
_PANEL_EDGE = "#27313A"
_TEXT = "#F2F5F7"
_MUTED = "#AEB8C2"
_BORDER = "#05080B"
_GREEN_STRONG = "#087A49"
_GREEN = "#119861"
_NEUTRAL = "#41494F"
_RED = "#B83333"
_RED_STRONG = "#980D18"


@dataclass(frozen=True)
class HeatmapItem:
    symbol: str
    change_pct: float
    turnover: float


@dataclass(frozen=True)
class HeatmapRect:
    item: HeatmapItem
    x: float
    y: float
    w: float
    h: float


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {_norm(column): str(column) for column in frame.columns}
    for alias in aliases:
        found = mapping.get(_norm(alias))
        if found:
            return found
    return None


def _trade_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def _technical_path(ctx: Any) -> Path:
    return Path(ctx.path("technical_output_dir", "data/output/technical")) / "latest_technical_features.csv"


def _heatmap_config(ctx: Any) -> dict[str, Any]:
    post_market = ctx.scheduler_config.get("post_market", {}) if isinstance(ctx.scheduler_config, dict) else {}
    raw = post_market.get("market_heatmap", {}) if isinstance(post_market, dict) else {}
    return raw if isinstance(raw, dict) else {}


def heatmap_enabled(ctx: Any) -> bool:
    raw = _heatmap_config(ctx).get("enabled", True)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _load_items(ctx: Any) -> tuple[list[HeatmapItem], Path]:
    path = _technical_path(ctx)
    if not path.exists() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"Technical snapshot tidak ditemukan: {path}")

    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        raise ValueError("Technical snapshot kosong")

    symbol_col = _column(frame, "Symbol", "Ticker", "Emiten")
    date_col = _column(frame, "Date", "Latest Valid Candle Date", "Technical Data Date")
    change_col = _column(frame, "Return_1D", "Change_Pct", "Change %", "Daily Return")
    turnover_col = _column(frame, "Turnover_Value", "Trading_Value", "Transaction_Value")
    close_col = _column(frame, "Close", "Last Price", "Current Price")
    volume_col = _column(frame, "Volume")

    missing = [
        label
        for label, value in (("SYMBOL", symbol_col), ("DATE", date_col), ("CHANGE", change_col))
        if value is None
    ]
    if missing:
        raise ValueError("Heatmap source columns missing: " + ",".join(missing))

    assert symbol_col and date_col and change_col
    parsed_dates = pd.to_datetime(frame[date_col], errors="coerce")
    target = _trade_date(ctx.trade_date)
    current_mask = parsed_dates.dt.date.eq(target)
    current = frame.loc[current_mask].copy()
    if current.empty:
        latest = parsed_dates.dropna().max()
        latest_text = latest.date().isoformat() if pd.notna(latest) else "MISSING"
        raise ValueError(f"HEATMAP_DATA_NOT_CURRENT:{latest_text}")

    symbols = current[symbol_col].astype(str).str.strip().str.upper().str.replace(".JK", "", regex=False)
    changes = pd.to_numeric(current[change_col], errors="coerce")
    if turnover_col:
        turnover = pd.to_numeric(current[turnover_col], errors="coerce")
    elif close_col and volume_col:
        turnover = (
            pd.to_numeric(current[close_col], errors="coerce")
            * pd.to_numeric(current[volume_col], errors="coerce")
        )
    else:
        turnover = pd.Series(1.0, index=current.index)

    prepared = pd.DataFrame({"symbol": symbols, "change": changes, "turnover": turnover})
    prepared = prepared[
        prepared["symbol"].ne("")
        & prepared["change"].notna()
        & prepared["turnover"].notna()
        & prepared["turnover"].gt(0)
    ].copy()
    prepared = prepared.drop_duplicates("symbol", keep="last")
    if prepared.empty:
        raise ValueError("Tidak ada saham aktif dengan data change dan turnover valid")

    max_symbols = int(_heatmap_config(ctx).get("max_symbols", 0) or 0)
    prepared = prepared.sort_values(["turnover", "symbol"], ascending=[False, True])
    if max_symbols > 0:
        prepared = prepared.head(max_symbols)

    items = [
        HeatmapItem(str(row.symbol), float(row.change), float(row.turnover))
        for row in prepared.itertuples(index=False)
    ]
    return items, path


def _balanced_index(items: list[HeatmapItem]) -> int:
    if len(items) <= 1:
        return 1
    total = sum(max(item.turnover, 0.0) for item in items)
    if total <= 0:
        return max(1, len(items) // 2)
    target = total / 2.0
    running = 0.0
    best_index = 1
    best_error = float("inf")
    for index in range(1, len(items)):
        running += max(items[index - 1].turnover, 0.0)
        error = abs(target - running)
        if error <= best_error:
            best_index = index
            best_error = error
        else:
            break
    return max(1, min(len(items) - 1, best_index))


def _layout_recursive(
    items: list[HeatmapItem],
    x: float,
    y: float,
    w: float,
    h: float,
    output: list[HeatmapRect],
) -> None:
    if not items or w <= 0 or h <= 0:
        return
    if len(items) == 1:
        output.append(HeatmapRect(items[0], x, y, w, h))
        return

    split = _balanced_index(items)
    left = items[:split]
    right = items[split:]
    left_weight = sum(item.turnover for item in left)
    total_weight = left_weight + sum(item.turnover for item in right)
    ratio = left_weight / total_weight if total_weight > 0 else len(left) / len(items)
    ratio = min(0.95, max(0.05, ratio))

    if w >= h:
        first_w = w * ratio
        _layout_recursive(left, x, y, first_w, h, output)
        _layout_recursive(right, x + first_w, y, w - first_w, h, output)
    else:
        first_h = h * ratio
        _layout_recursive(left, x, y, w, first_h, output)
        _layout_recursive(right, x, y + first_h, w, h - first_h, output)


def _layout(items: Iterable[HeatmapItem]) -> list[HeatmapRect]:
    ordered = sorted(items, key=lambda item: (-item.turnover, item.symbol))
    output: list[HeatmapRect] = []
    _layout_recursive(ordered, 0.0, 0.0, 1.0, 1.0, output)
    return output


def _color(change_pct: float) -> str:
    if change_pct > 3.0:
        return _GREEN_STRONG
    if change_pct >= 1.0:
        return _GREEN
    if change_pct > -1.0:
        return _NEUTRAL
    if change_pct >= -3.0:
        return _RED
    return _RED_STRONG


def _signed_pct(value: float) -> str:
    return f"{value:+.2f}%"


def _compact_money(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000_000:
        return f"Rp {value / 1_000_000_000_000:.2f} T"
    if absolute >= 1_000_000_000:
        return f"Rp {value / 1_000_000_000:.1f} B"
    if absolute >= 1_000_000:
        return f"Rp {value / 1_000_000:.1f} M"
    return f"Rp {value:,.0f}".replace(",", ".")


def _ihsg_change(ctx: Any) -> float | None:
    try:
        path = Path(ctx.path("ihsg_csv", "data/input/IHSG.csv"))
        if not path.exists() or path.stat().st_size <= 0:
            return None
        frame = pd.read_csv(path, low_memory=False)
        date_col = _column(frame, "Date", "Tanggal")
        close_col = _column(frame, "Close", "Adj Close")
        if not date_col or not close_col:
            return None
        frame = frame.copy()
        frame[date_col] = pd.to_datetime(frame[date_col], errors="coerce")
        frame[close_col] = pd.to_numeric(frame[close_col], errors="coerce")
        frame = frame.dropna(subset=[date_col, close_col]).sort_values(date_col)
        target = _trade_date(ctx.trade_date)
        eligible = frame[frame[date_col].dt.date <= target]
        if len(eligible) < 2 or eligible.iloc[-1][date_col].date() != target:
            return None
        previous = float(eligible.iloc[-2][close_col])
        current = float(eligible.iloc[-1][close_col])
        return ((current / previous) - 1.0) * 100.0 if previous else None
    except Exception:
        return None


def _date_label(value: Any) -> str:
    parsed = _trade_date(value)
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    return f"{parsed.day:02d} {months[parsed.month - 1]} {parsed.year} • EOD"


def _metric_card(ax: Any, x: float, y: float, w: float, h: float, label: str, value: str) -> None:
    ax.add_patch(Rectangle((x, y), w, h, facecolor=_PANEL, edgecolor=_PANEL_EDGE, linewidth=1.0))
    ax.text(x + 0.018 * w, y + h * 0.68, label, color=_MUTED, fontsize=10, va="center", ha="left")
    ax.text(x + 0.018 * w, y + h * 0.32, value, color=_TEXT, fontsize=17, fontweight="bold", va="center", ha="left")


def render_market_heatmap(ctx: Any) -> Path:
    """Render one EOD PNG from the existing technical snapshot.

    Raises on stale/missing source data. The caller is expected to treat this as
    non-blocking so Post Market text can still be delivered.
    """
    items, source_path = _load_items(ctx)
    output_dir = Path(ctx.path("post_market_output_dir", "data/output/post_market")) / _trade_date(ctx.trade_date).isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "market_heatmap.png"

    advances = sum(1 for item in items if item.change_pct > 0)
    declines = sum(1 for item in items if item.change_pct < 0)
    total_turnover = sum(item.turnover for item in items)
    ihsg = _ihsg_change(ctx)

    fig = plt.figure(figsize=(16, 10), dpi=120, facecolor=_BG)
    canvas = fig.add_axes([0, 0, 1, 1])
    canvas.set_xlim(0, 1)
    canvas.set_ylim(0, 1)
    canvas.axis("off")
    canvas.set_facecolor(_BG)

    canvas.text(0.03, 0.965, "SDE SWING — MARKET HEATMAP", color=_TEXT, fontsize=23, fontweight="bold", va="top")
    canvas.text(0.03, 0.925, _date_label(ctx.trade_date), color=_MUTED, fontsize=11, va="top")
    canvas.text(0.97, 0.965, "POST MARKET", color=_TEXT, fontsize=16, fontweight="bold", ha="right", va="top")
    canvas.text(0.97, 0.928, "EOD MARKET BREADTH", color=_MUTED, fontsize=9, ha="right", va="top")

    card_y, card_h = 0.815, 0.085
    gap = 0.012
    total_w = 0.94
    card_w = (total_w - 3 * gap) / 4
    ihsg_text = _signed_pct(ihsg) if ihsg is not None else "N/A"
    _metric_card(canvas, 0.03, card_y, card_w, card_h, "IHSG", ihsg_text)
    _metric_card(canvas, 0.03 + card_w + gap, card_y, card_w, card_h, "ADV / DEC", f"{advances} / {declines}")
    _metric_card(canvas, 0.03 + 2 * (card_w + gap), card_y, card_w, card_h, "DAILY TURNOVER", _compact_money(total_turnover))
    _metric_card(canvas, 0.03 + 3 * (card_w + gap), card_y, card_w, card_h, "ACTIVE SYMBOLS", str(len(items)))

    canvas.text(0.03, 0.785, "HEATMAP BY: % CHANGE", color=_MUTED, fontsize=12, fontweight="bold", va="top")
    canvas.text(0.97, 0.785, "Size by: Daily Turnover", color=_MUTED, fontsize=10, ha="right", va="top")

    heat_ax = fig.add_axes([0.03, 0.12, 0.94, 0.64])
    heat_ax.set_xlim(0, 1)
    heat_ax.set_ylim(0, 1)
    heat_ax.axis("off")
    heat_ax.set_facecolor(_BG)

    for rect in _layout(items):
        pad = 0.0012
        x = rect.x + pad
        y = rect.y + pad
        w = max(0.0, rect.w - 2 * pad)
        h = max(0.0, rect.h - 2 * pad)
        if w <= 0 or h <= 0:
            continue
        heat_ax.add_patch(Rectangle((x, y), w, h, facecolor=_color(rect.item.change_pct), edgecolor=_BORDER, linewidth=0.55))
        area = w * h
        minimum_side = min(w, h)
        if area < 0.0010 or minimum_side < 0.018:
            continue
        fontsize = 7
        if area >= 0.012:
            fontsize = 15
        elif area >= 0.006:
            fontsize = 12
        elif area >= 0.003:
            fontsize = 9
        symbol_y = y + h * (0.57 if area >= 0.0022 else 0.50)
        heat_ax.text(x + w / 2, symbol_y, rect.item.symbol, color=_TEXT, fontsize=fontsize, fontweight="bold", ha="center", va="center", clip_on=True)
        if area >= 0.0022 and h >= 0.055:
            heat_ax.text(x + w / 2, y + h * 0.38, _signed_pct(rect.item.change_pct), color=_TEXT, fontsize=max(6, fontsize - 1), fontweight="bold", ha="center", va="center", clip_on=True)

    legend_y = 0.065
    canvas.text(0.03, legend_y + 0.018, "% CHANGE", color=_MUTED, fontsize=9, va="center")
    legend = [
        (_GREEN_STRONG, "> +3%"),
        (_GREEN, "+1% to +3%"),
        (_NEUTRAL, "-1% to +1%"),
        (_RED, "-3% to -1%"),
        (_RED_STRONG, "< -3%"),
    ]
    start_x = 0.12
    for index, (color, label) in enumerate(legend):
        x = start_x + index * 0.13
        canvas.add_patch(Rectangle((x, legend_y + 0.007), 0.014, 0.022, facecolor=color, edgecolor=_PANEL_EDGE, linewidth=0.5))
        canvas.text(x + 0.020, legend_y + 0.018, label, color=_MUTED, fontsize=8.5, va="center")

    canvas.text(0.97, 0.085, "SDE SWING", color=_TEXT, fontsize=10, fontweight="bold", ha="right")
    canvas.text(0.97, 0.055, f"Source: {source_path.name} • Color: daily % change", color=_MUTED, fontsize=8, ha="right")

    fig.savefig(output_path, facecolor=fig.get_facecolor(), bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return output_path


__all__ = ["heatmap_enabled", "render_market_heatmap"]
