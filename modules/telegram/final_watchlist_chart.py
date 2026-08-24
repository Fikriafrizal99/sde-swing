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
_ENGINE_MISSING = {"", "nan", "none", "null", "engine_data_not_available", "data_not_available"}
_FINAL_WATCHLIST_CAPTION_LIMIT = 1024


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip().lower() in _ENGINE_MISSING:
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
        if value is not None and str(value).strip().lower() not in _ENGINE_MISSING:
            return value
    return None


def _price_label(value: Any) -> str:
    rounded = round_idx_price(value)
    if rounded is None:
        return "ENGINE_DATA_NOT_AVAILABLE"
    return f"{rounded:,.0f}".replace(",", ".")


def _shorten_reason_to_limit(compact: str, limit: int) -> str | None:
    for marker in ("<b>Reason:</b>", "Reason:"):
        marker_pos = compact.rfind(marker)
        if marker_pos < 0:
            continue
        head = compact[: marker_pos + len(marker)].rstrip()
        reason = compact[marker_pos + len(marker):].strip()
        room = limit - len(head) - 2
        if room <= 1:
            continue
        shortened = reason[:room].rstrip(" ;,.")
        if len(shortened) < len(reason):
            shortened = shortened.rstrip() + "…"
        candidate = f"{head} {shortened}"
        if len(candidate) <= limit:
            return candidate
    return None


def compact_final_watchlist_caption(text: Any, limit: int = _FINAL_WATCHLIST_CAPTION_LIMIT) -> str:
    """Keep a chart + FINAL WATCHLIST card inside one Telegram photo message.

    Telegram photo captions are capped at 1024 characters. The formatter first
    removes presentation-only whitespace and abbreviates verbose labels while
    preserving all engine facts. If more space is still required, only the tail
    of Reason is shortened. Bold markup is kept whenever possible.
    """
    compact = str(text or "").strip()
    if len(compact) <= limit:
        return compact

    while "\n\n" in compact:
        compact = compact.replace("\n\n", "\n")

    replacements = (
        ("ENGINE DATA NOT AVAILABLE", "N/A"),
        ("ENGINE_DATA_NOT_AVAILABLE", "N/A"),
        ("INSUFFICIENT DATA", "INSUFFICIENT"),
        ("Confidence", "Conf."),
        ("Concentration", "Conc."),
        ("Persistence", "Persist."),
        ("Fibonacci", "Fib"),
        ("<b>Reason:</b>\n", "<b>Reason:</b> "),
        ("Reason:\n", "Reason: "),
    )
    for old, new in replacements:
        compact = compact.replace(old, new)
    if len(compact) <= limit:
        return compact

    shortened = _shorten_reason_to_limit(compact, limit)
    if shortened is not None:
        return shortened

    # Last resort for an unusually verbose non-Reason card: remove cosmetic
    # bold markup before trimming. Core engine facts remain ahead of Reason.
    compact = compact.replace("<b>", "").replace("</b>", "")
    if len(compact) <= limit:
        return compact
    shortened = _shorten_reason_to_limit(compact, limit)
    if shortened is not None:
        return shortened
    return compact[: limit - 1].rstrip() + "…"


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


def _pivot_indices(frame: pd.DataFrame, column: str, *, mode: str, radius: int = 2) -> list[int]:
    """Return confirmed local pivot indices for chart-only swing selection."""
    values = [float(value) for value in frame[column].tolist()]
    pivots: list[int] = []
    for idx in range(radius, len(values) - radius):
        center = values[idx]
        window = values[idx - radius: idx + radius + 1]
        if mode == "high" and center == max(window):
            pivots.append(idx)
        elif mode == "low" and center == min(window):
            pivots.append(idx)
    return pivots


def _resolve_chart_swing(row: Mapping[str, Any], frame: pd.DataFrame) -> tuple[float, float, str] | None:
    """Resolve a presentation-only Fibonacci swing without touching engine logic.

    Engine-provided swing anchors win when both are valid. Otherwise the chart
    derives the latest confirmed structural swing from the same closed candles
    already plotted. The fallback is intentionally local so an old extreme does
    not make the current swing Fibonacci unreadably wide.
    """
    engine_high = _number(_pick(row, "swing_high", "Swing_High", "Valid_Swing_High"))
    engine_low = _number(_pick(row, "swing_low", "Swing_Low", "Valid_Swing_Low"))
    trend_text = str(
        _pick(row, "trend", "Technical_Regime", "technical_status", "technical_state") or ""
    ).upper()
    direction = "bearish" if "BEAR" in trend_text else "bullish"
    if engine_high is not None and engine_low is not None and engine_high > engine_low > 0:
        return float(engine_low), float(engine_high), direction

    recent = frame.tail(min(50, len(frame))).reset_index(drop=True)
    if len(recent) < 8:
        return None
    high_pivots = _pivot_indices(recent, "High", mode="high", radius=2)
    low_pivots = _pivot_indices(recent, "Low", mode="low", radius=2)

    def valid_pair(low_idx: int, high_idx: int) -> tuple[float, float, str] | None:
        low_value = float(recent.iloc[low_idx]["Low"])
        high_value = float(recent.iloc[high_idx]["High"])
        if low_value <= 0 or high_value <= low_value:
            return None
        # Ignore micro-swings that would create visually meaningless levels.
        if (high_value - low_value) / low_value < 0.05:
            return None
        return low_value, high_value, direction

    if direction == "bullish":
        for high_idx in reversed(high_pivots):
            candidates = [idx for idx in low_pivots if idx < high_idx and high_idx - idx <= 30]
            if candidates:
                resolved = valid_pair(candidates[-1], high_idx)
                if resolved is not None:
                    return resolved
        fallback = recent.tail(min(35, len(recent))).reset_index(drop=True)
        high_idx = int(fallback["High"].idxmax())
        if high_idx > 0:
            low_idx = int(fallback.loc[: high_idx - 1, "Low"].idxmin())
            low_value = float(fallback.iloc[low_idx]["Low"])
            high_value = float(fallback.iloc[high_idx]["High"])
            if low_value > 0 and high_value > low_value and (high_value - low_value) / low_value >= 0.05:
                return low_value, high_value, direction
    else:
        for low_idx in reversed(low_pivots):
            candidates = [idx for idx in high_pivots if idx < low_idx and low_idx - idx <= 30]
            if candidates:
                resolved = valid_pair(low_idx, candidates[-1])
                if resolved is not None:
                    return resolved
        fallback = recent.tail(min(35, len(recent))).reset_index(drop=True)
        low_idx = int(fallback["Low"].idxmin())
        if low_idx > 0:
            high_idx = int(fallback.loc[: low_idx - 1, "High"].idxmax())
            low_value = float(fallback.iloc[low_idx]["Low"])
            high_value = float(fallback.iloc[high_idx]["High"])
            if low_value > 0 and high_value > low_value and (high_value - low_value) / low_value >= 0.05:
                return low_value, high_value, direction
    return None


def _chart_fibonacci_levels(swing_low: float, swing_high: float, direction: str) -> list[tuple[str, float]]:
    """Calculate standard retracement/extension overlays for chart presentation only."""
    span = float(swing_high) - float(swing_low)
    if span <= 0:
        return []
    if direction == "bearish":
        return [
            ("FIB 38.2%", swing_low + span * 0.382),
            ("FIB 50%", swing_low + span * 0.500),
            ("FIB 61.8%", swing_low + span * 0.618),
            ("FIB 78.6%", swing_low + span * 0.786),
            ("FIB 127.2%", swing_low - span * 0.272),
            ("FIB 161.8%", swing_low - span * 0.618),
        ]
    return [
        ("FIB 38.2%", swing_high - span * 0.382),
        ("FIB 50%", swing_high - span * 0.500),
        ("FIB 61.8%", swing_high - span * 0.618),
        ("FIB 78.6%", swing_high - span * 0.786),
        ("FIB 127.2%", swing_high + span * 0.272),
        ("FIB 161.8%", swing_high + span * 0.618),
    ]


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

    # Fibonacci is a presentation overlay only. It never feeds Entry/SL/TP,
    # scoring, confidence, broker state, or the Final Watchlist decision.
    swing = _resolve_chart_swing(row, frame)
    if swing is not None:
        swing_low, swing_high, fib_direction = swing
        swing_low_display = round_idx_price(swing_low)
        swing_high_display = round_idx_price(swing_high)
        x_fib = len(frame) + 5.2
        if swing_high_display is not None:
            ax.axhline(swing_high_display, linestyle="-.", linewidth=0.8, alpha=0.42)
            ax.text(x_fib, swing_high_display, f"FIB HIGH {_price_label(swing_high_display)}", va="center", fontsize=7.4, clip_on=False)
        if swing_low_display is not None:
            ax.axhline(swing_low_display, linestyle="-.", linewidth=0.8, alpha=0.42)
            ax.text(x_fib, swing_low_display, f"FIB LOW {_price_label(swing_low_display)}", va="center", fontsize=7.4, clip_on=False)
        for label, raw_value in _chart_fibonacci_levels(swing_low, swing_high, fib_direction):
            value = round_idx_price(raw_value)
            if value is None or value <= 0:
                continue
            ax.axhline(value, linestyle=":", linewidth=0.72, alpha=0.38)
            ax.text(x_fib, value, f"{label} {_price_label(value)}", va="center", fontsize=7.2, clip_on=False)

    setup = str(_pick(row, "setup", "Setup_Type") or "SETUP").replace("_", " ")
    date = str(_pick(row, "analysis_date", "trade_date", "Trade_Date") or "")
    ax.set_title(f"{symbol} — SDE SWING FINAL WATCHLIST | {setup} | {date}", loc="left", fontsize=13, fontweight="bold")
    ax.set_ylabel("Price")
    ax.grid(alpha=0.16)
    ax.legend(loc="upper left", frameon=False, ncol=3, fontsize=8.5)
    ax.tick_params(axis="x", labelbottom=False)
    ax.set_xlim(-1, len(frame) + 14)

    tick_positions = list(range(0, len(frame), max(1, len(frame) // 8)))
    vol.set_xticks(tick_positions)
    vol.set_xticklabels([frame.iloc[i]["Date"].strftime("%d-%b") for i in tick_positions], rotation=0, fontsize=8)
    vol.set_ylabel("Volume")
    vol.grid(alpha=0.12)

    fig.subplots_adjust(left=0.07, right=0.76, top=0.93, bottom=0.08, hspace=0.04)
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"Chart output was not created: {output_path}")
    return output_path
