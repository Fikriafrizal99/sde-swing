from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd


_REQUIRED_OHLCV = ("Date", "Open", "High", "Low", "Close", "Volume")
IDX_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"  # exactly 20 characters, no indentation


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null", "engine_data_not_available"}:
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def idx_tick_size(price: float) -> float:
    """Return the IDX regular-market tick size for a positive stock price.

    Presentation follows the five BEI price fractions:
    <200=1, 200-<500=2, 500-<2000=5, 2000-<5000=10, >=5000=25.
    """
    value = abs(float(price))
    if value < 200:
        return 1.0
    if value < 500:
        return 2.0
    if value < 2_000:
        return 5.0
    if value < 5_000:
        return 10.0
    return 25.0


def round_idx_price(value: Any) -> float | None:
    """Round a display price to the nearest executable IDX tick.

    Engine-owned values are not mutated; this helper is presentation-only.
    Half ticks are rounded upward rather than using Python bankers rounding.
    """
    number = _number(value)
    if number is None:
        return None
    tick = idx_tick_size(number)
    rounded = math.floor(number / tick + 0.5) * tick
    return float(rounded)


def _pick(row: Mapping[str, Any], *keys: str) -> Any:
    lookup = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        value = lookup.get(key.lower())
        if value is not None and str(value).strip().lower() not in {"", "nan", "none", "null"}:
            return value
    return None


def _price_label(value: Any) -> str:
    rounded = round_idx_price(value)
    if rounded is None:
        return "ENGINE_DATA_NOT_AVAILABLE"
    return f"{rounded:,.0f}".replace(",", ".")


def _install_telegram_idx_price_formatter() -> None:
    """Keep Telegram FINAL WATCHLIST prices aligned with the chart display.

    ``daily_report_ui`` owns the card text while this module owns the chart.
    The enhanced report builder imports this module at its presentation hook,
    so installing the formatter here keeps one IDX display rule without
    touching engine artifacts or recomputing the trade plan.
    """
    try:
        from modules.telegram import daily_report_ui as daily_ui

        def _telegram_idx_price(value: Any) -> str:
            rounded = round_idx_price(value)
            if rounded is None:
                return daily_ui._fw_text(value)
            return f"{rounded:,.0f}".replace(",", ".")

        daily_ui._fw_price = _telegram_idx_price
        # Legacy formatters use the shared constant; canonical FINAL WATCHLIST
        # already contains the same literal separator. Keep both at 20 chars.
        daily_ui.SEPARATOR = IDX_SEPARATOR
    except Exception:
        # Chart generation must remain import-safe even in isolated tooling.
        pass


_install_telegram_idx_price_formatter()


def _historical_file(historical_dir: Path, symbol: str) -> Path:
    direct = [historical_dir / f"{symbol}.csv", historical_dir / f"{symbol}.JK.csv"]
    for path in direct:
        if path.exists() and path.is_file():
            return path
    matches = sorted(historical_dir.glob(f"{symbol}*.csv"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Historical candle file not found for {symbol} in {historical_dir}")


def _load_candles(path: Path, candle_limit: int) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    aliases = {str(col).strip().lower(): col for col in frame.columns}
    rename: dict[str, str] = {}
    for required in _REQUIRED_OHLCV:
        source = aliases.get(required.lower())
        if source is None:
            raise ValueError(f"Missing OHLCV column {required} in {path}")
        rename[source] = required
    frame = frame.rename(columns=rename)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    for col in _REQUIRED_OHLCV[1:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=list(_REQUIRED_OHLCV)).sort_values("Date").drop_duplicates("Date", keep="last")
    if len(frame) < 50:
        raise ValueError(f"Insufficient candle history for chart: {len(frame)} rows")
    # MA20/MA50 are presentation overlays calculated from the same closed candles
    # used by the technical engine. Trade-plan levels are never recalculated here.
    frame["MA20"] = frame["Close"].rolling(20).mean()
    frame["MA50"] = frame["Close"].rolling(50).mean()
    return frame.tail(max(60, min(int(candle_limit), 100))).reset_index(drop=True)


def generate_final_watchlist_chart(
    row: Mapping[str, Any],
    *,
    historical_dir: str | Path,
    output_dir: str | Path = "output/final_watchlist",
    candle_limit: int = 80,
) -> Path:
    symbol = str(_pick(row, "symbol", "Symbol") or "").strip().upper().replace(".JK", "")
    if not symbol:
        raise ValueError("FINAL WATCHLIST chart requires symbol")

    # Round only the visual/executable levels. The source row and engine files
    # retain their original precision for audit and calculations.
    current = round_idx_price(_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = round_idx_price(_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = round_idx_price(_pick(row, "entry_high", "Entry_Zone_High"))
    stop = round_idx_price(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = round_idx_price(_pick(row, "target_1", "Target_1"))
    tp2 = round_idx_price(_pick(row, "target_2", "Target_2"))
    support = round_idx_price(_pick(row, "support", "Support_Level"))
    resistance = round_idx_price(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    required_levels = {
        "current": current, "entry_low": entry_low, "entry_high": entry_high,
        "stop": stop, "tp1": tp1, "tp2": tp2,
        "support": support, "resistance": resistance,
    }
    missing = [key for key, value in required_levels.items() if value is None]
    if missing:
        raise ValueError("FINAL WATCHLIST chart missing engine levels: " + ", ".join(missing))

    historical_dir = Path(historical_dir)
    frame = _load_candles(_historical_file(historical_dir, symbol), candle_limit)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{symbol}_setup.png"

    fig = plt.figure(figsize=(13.5, 8.2), constrained_layout=False)
    grid = fig.add_gridspec(5, 1, hspace=0.04)
    ax = fig.add_subplot(grid[:4, 0])
    vol = fig.add_subplot(grid[4, 0], sharex=ax)

    width = 0.62
    for idx, candle in frame.iterrows():
        open_price = float(candle["Open"])
        high = float(candle["High"])
        low = float(candle["Low"])
        close = float(candle["Close"])
        up = close >= open_price
        color = "#159a68" if up else "#d94b4b"
        ax.vlines(idx, low, high, color=color, linewidth=0.9, zorder=2)
        body_low = min(open_price, close)
        height = max(abs(close - open_price), max(close * 0.0005, 0.01))
        ax.add_patch(Rectangle((idx - width / 2, body_low), width, height, facecolor=color, edgecolor=color, linewidth=0.7, zorder=3))
        vol.bar(idx, float(candle["Volume"]), width=width, color=color, alpha=0.75)

    ax.plot(frame.index, frame["MA20"], linewidth=1.25, label="MA20")
    ax.plot(frame.index, frame["MA50"], linewidth=1.25, label="MA50")

    ax.axhspan(float(entry_low), float(entry_high), alpha=0.13, label=f"ENTRY {_price_label(entry_low)}–{_price_label(entry_high)}")
    levels = [
        (current, f"CURRENT {_price_label(current)}", "--", 1.2),
        (stop, f"SL {_price_label(stop)}", "-", 1.2),
        (tp1, f"TP1 {_price_label(tp1)}", "-", 1.1),
        (tp2, f"TP2 {_price_label(tp2)}", "-", 1.1),
        (support, f"SUPPORT {_price_label(support)}", ":", 1.0),
        (resistance, f"RESISTANCE {_price_label(resistance)}", ":", 1.0),
    ]
    x_label = len(frame) + 0.4
    for value, label, style, lw in levels:
        ax.axhline(float(value), linestyle=style, linewidth=lw, alpha=0.85)
        ax.text(x_label, float(value), label, va="center", fontsize=8.5, clip_on=False)

    swing_high = round_idx_price(_pick(row, "swing_high", "Swing_High"))
    swing_low = round_idx_price(_pick(row, "swing_low", "Swing_Low"))
    if swing_high is not None:
        ax.axhline(swing_high, linestyle="-.", linewidth=0.85, alpha=0.55)
        ax.text(x_label, swing_high, f"SWING HIGH {_price_label(swing_high)}", va="center", fontsize=8, clip_on=False)
    if swing_low is not None:
        ax.axhline(swing_low, linestyle="-.", linewidth=0.85, alpha=0.55)
        ax.text(x_label, swing_low, f"SWING LOW {_price_label(swing_low)}", va="center", fontsize=8, clip_on=False)

    fib_status = str(_pick(row, "fib_status", "Fibonacci_Status", "Fib_Status") or "").upper()
    if "VALID" in fib_status and "NOT" not in fib_status:
        for key, label in (("fib_382", "FIB 38.2%"), ("fib_500", "FIB 50%"), ("fib_618", "FIB 61.8%"), ("fib_1618", "FIB 161.8%")):
            value = round_idx_price(_pick(row, key, key.upper()))
            if value is not None:
                ax.axhline(value, linestyle=":", linewidth=0.75, alpha=0.45)
                ax.text(x_label, value, f"{label} {_price_label(value)}", va="center", fontsize=7.5, clip_on=False)

    setup = str(_pick(row, "setup", "Setup_Type") or "SETUP").replace("_", " ")
    date = str(_pick(row, "analysis_date", "trade_date", "Trade_Date") or "")
    ax.set_title(f"{symbol} — SDE SWING FINAL WATCHLIST | {setup} | {date}", loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.16)
    ax.legend(loc="upper left", frameon=False, ncol=3, fontsize=8.5)
    ax.tick_params(axis="x", labelbottom=False)
    ax.set_xlim(-1, len(frame) + 12)

    tick_positions = list(range(0, len(frame), max(1, len(frame) // 8)))
    vol.set_xticks(tick_positions)
    vol.set_xticklabels([frame.iloc[i]["Date"].strftime("%d-%b") for i in tick_positions], rotation=0, fontsize=8)
    vol.set_ylabel("Volume")
    vol.grid(alpha=0.12)

    fig.subplots_adjust(left=0.07, right=0.78, top=0.93, bottom=0.08, hspace=0.04)
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"Chart output was not created: {output_path}")
    return output_path
