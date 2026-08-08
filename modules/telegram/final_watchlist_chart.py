from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd

from modules.broker_bridge.broker_raw import broker_raw_trade_date, read_normalized_broker_raw


_REQUIRED_OHLCV = ("Date", "Open", "High", "Low", "Close", "Volume")
IDX_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"  # exactly 20 characters, no indentation
_ENGINE_MISSING = {"", "nan", "none", "null", "engine_data_not_available", "data_not_available"}
_FINAL_WATCHLIST_CAPTION_LIMIT = 1024


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip().lower() in _ENGINE_MISSING:
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return str(value).strip().lower() in _ENGINE_MISSING


def _norm_key(value: Any) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in str(value or "")).strip("_")


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


def compact_final_watchlist_caption(text: Any, limit: int = _FINAL_WATCHLIST_CAPTION_LIMIT) -> str:
    """Keep a chart + FINAL WATCHLIST card inside one Telegram photo message.

    Telegram photo captions are capped at 1024 characters.  The formatter first
    removes presentation-only whitespace and abbreviates verbose labels while
    preserving all engine facts.  Only if a pathological Reason still exceeds
    the limit is the Reason tail shortened.  No second Telegram message is
    created for FINAL WATCHLIST details.
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

    # Bold tags are cosmetic. Removing them is preferable to splitting one
    # stock card into two separate Telegram messages.
    compact = compact.replace("<b>", "").replace("</b>", "")
    if len(compact) <= limit:
        return compact

    marker = "Reason:"
    marker_pos = compact.rfind(marker)
    if marker_pos >= 0:
        head = compact[: marker_pos + len(marker)].rstrip()
        reason = compact[marker_pos + len(marker):].strip()
        room = limit - len(head) - 2
        if room > 1:
            shortened = reason[:room].rstrip(" ;,.")
            if len(shortened) < len(reason):
                shortened = shortened.rstrip() + "…"
            return f"{head} {shortened}"[:limit]

    return compact[: limit - 1].rstrip() + "…"


def _parse_participant_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except Exception:
                return []
        else:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _raw_broker_side_map(symbol: str, trade_date: str) -> dict[str, dict[str, dict[str, Any]]]:
    path = Path("data/input/broker/BROKER_RAW_LATEST.csv")
    if not path.exists() or path.stat().st_size <= 0:
        return {"BUY": {}, "SELL": {}}
    try:
        frame = read_normalized_broker_raw(path)
    except Exception:
        return {"BUY": {}, "SELL": {}}
    if frame.empty:
        return {"BUY": {}, "SELL": {}}
    raw_date = broker_raw_trade_date(frame)
    if trade_date and raw_date and raw_date != trade_date:
        return {"BUY": {}, "SELL": {}}
    subset = frame[frame["SYMBOL"].astype(str).str.upper().eq(symbol)].copy()
    result: dict[str, dict[str, dict[str, Any]]] = {"BUY": {}, "SELL": {}}
    for _, raw in subset.iterrows():
        side = str(raw.get("SIDE") or "").upper()
        broker = str(raw.get("BROKER_CODE") or "").strip().upper()
        if side not in result or not broker:
            continue
        result[side][broker] = {
            "broker": broker,
            "value": raw.get("NET_VALUE"),
            "avg_price": raw.get("AVG_PRICE"),
            "classification": raw.get("BROKER_TYPE"),
        }
    return result


def _merge_engine_participants(existing: Any, raw_map: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    items = _parse_participant_items(existing)
    if not items and raw_map:
        return [dict(item) for item in list(raw_map.values())[:3]]
    merged: list[dict[str, Any]] = []
    for item in items[:3]:
        current = dict(item)
        broker = str(current.get("broker") or current.get("code") or current.get("name") or "").strip().upper()
        source = raw_map.get(broker, {}) if broker else {}
        if source:
            if _is_missing(current.get("value")) and not _is_missing(source.get("value")):
                current["value"] = source.get("value")
            if _is_missing(current.get("avg_price")) and not _is_missing(source.get("avg_price")):
                current["avg_price"] = source.get("avg_price")
            if all(_is_missing(current.get(key)) for key in ("classification", "broker_type", "type", "origin", "foreign_local")):
                if not _is_missing(source.get("classification")):
                    current["classification"] = source.get("classification")
        merged.append(current)
    return merged


def _artifact_value_for_symbol(
    path: Path,
    symbol: str,
    trade_date: str,
    aliases: tuple[str, ...],
) -> Any:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        frame = pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        frame = pd.read_csv(path, low_memory=False, encoding="latin-1")
    except Exception:
        return None
    if frame.empty:
        return None
    columns = {_norm_key(col): col for col in frame.columns}
    symbol_col = next((columns.get(_norm_key(name)) for name in ("symbol", "emiten", "ticker", "code") if columns.get(_norm_key(name))), None)
    if symbol_col is None:
        return None
    rows = frame[frame[symbol_col].astype(str).str.upper().str.replace(".JK", "", regex=False).eq(symbol)]
    if rows.empty:
        return None

    date_col = next((columns.get(_norm_key(name)) for name in ("trade_date", "to_date", "broker_data_date", "technical_date") if columns.get(_norm_key(name))), None)
    if date_col is not None and trade_date:
        dates = pd.to_datetime(rows[date_col], errors="coerce").dt.date.astype("string")
        matching = rows[dates.eq(trade_date)]
        if not matching.empty:
            rows = matching
        elif dates.notna().any():
            return None

    row = rows.iloc[-1]
    for alias in aliases:
        col = columns.get(_norm_key(alias))
        if col is None:
            continue
        value = row.get(col)
        if not _is_missing(value):
            return value
    return None


def _enrich_final_watchlist_broker_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Fill presentation-only broker fields from already-produced engine artifacts.

    This never recomputes broker metrics.  It only restores fields that were
    dropped by an older Final Watchlist row/CSV before Telegram rendering.
    """
    enriched = dict(row or {})
    symbol = str(_pick(enriched, "symbol", "Symbol") or "").strip().upper().replace(".JK", "")
    trade_date = str(_pick(enriched, "analysis_date", "trade_date", "Trade_Date") or "").strip()
    if not symbol:
        return enriched

    raw_map = _raw_broker_side_map(symbol, trade_date)
    enriched["top_buyers"] = _merge_engine_participants(enriched.get("top_buyers"), raw_map.get("BUY", {}))
    enriched["top_sellers"] = _merge_engine_participants(enriched.get("top_sellers"), raw_map.get("SELL", {}))

    current_distance = _pick(enriched, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct")
    if _is_missing(current_distance):
        aliases = (
            "DISTANCE_TO_BUY_COST",
            "DISTANCE_TO_BUY_COST_PCT",
            "DISTANCE_TO_BUYER_AVG_PCT",
            "DISTANCE_TO_BUY_AVG_PCT",
            "JARAK_BUY_AVG",
        )
        sources = (
            Path("data/output/broker_multiday/BROKER_WINDOW_COMPARISON.csv"),
            Path("data/input/FINAL_DECISION_V2.csv"),
        )
        for path in sources:
            value = _artifact_value_for_symbol(path, symbol, trade_date, aliases)
            if not _is_missing(value):
                enriched["distance_to_buy_cost"] = value
                break
    return enriched


def _install_telegram_idx_price_formatter() -> None:
    """Install FINAL WATCHLIST-only display helpers.

    The engine artifacts stay untouched. Telegram/card/chart prices are shown
    on executable IDX ticks, participant rows expose the value/average/type
    already carried by broker artifacts, and historical missing fields are
    restored from same-date engine outputs before rendering.
    """
    try:
        from modules.telegram import daily_report_ui as daily_ui

        def _telegram_idx_price(value: Any) -> str:
            rounded = round_idx_price(value)
            if rounded is None:
                return daily_ui._fw_text(value)
            return f"{rounded:,.0f}".replace(",", ".")

        def _telegram_broker_type(value: Any) -> str:
            text = str(value or "").strip()
            if not text or text.lower() in _ENGINE_MISSING:
                return ""
            normalized = text.replace("_", " ").strip().upper()
            aliases = {
                "FOREIGN": "Asing",
                "ASING": "Asing",
                "GOVERNMENT": "Pemerintah",
                "PEMERINTAH": "Pemerintah",
                "LOCAL": "Lokal",
                "LOKAL": "Lokal",
                "DOMESTIC": "Domestik",
                "DOMESTIK": "Domestik",
            }
            return aliases.get(normalized, text.replace("_", " ").title())

        def _telegram_broker_participants(value: Any) -> list[str]:
            items = _parse_participant_items(value)
            lines: list[str] = []
            for index, item in enumerate(items[:3], start=1):
                broker = str(item.get("broker") or item.get("code") or item.get("name") or "").strip().upper()
                if not broker:
                    continue
                details: list[str] = []
                transaction_value = item.get("value") or item.get("net_value") or item.get("amount")
                number = _number(transaction_value)
                if number is not None:
                    money = daily_ui._fw_money(abs(number))
                    if money and money != "ENGINE_DATA_NOT_AVAILABLE":
                        details.append(money[1:] if money.startswith("+") else money)
                average = item.get("avg_price") or item.get("average_price") or item.get("avg")
                if average not in (None, ""):
                    rendered_avg = _telegram_idx_price(average)
                    if rendered_avg and rendered_avg != "ENGINE_DATA_NOT_AVAILABLE":
                        details.append(f"Avg Rp{rendered_avg}")
                broker_type = _telegram_broker_type(
                    item.get("classification")
                    or item.get("broker_type")
                    or item.get("type")
                    or item.get("origin")
                    or item.get("foreign_local")
                )
                if broker_type:
                    details.append(broker_type)
                suffix = f" — {' | '.join(details)}" if details else ""
                lines.append(f"{index}. {broker}{suffix}")
            return lines or ["• Data broker belum tersedia"]

        original_formatter = daily_ui.format_watchlist_detail

        def _telegram_final_watchlist_formatter(row: Mapping[str, Any]) -> str:
            enriched = _enrich_final_watchlist_broker_row(row)
            text = original_formatter(enriched)
            text = text.replace(" | Vs Cost ", " | Jarak Buy Avg ")
            return compact_final_watchlist_caption(text)

        daily_ui._fw_price = _telegram_idx_price
        daily_ui._fw_participants = _telegram_broker_participants
        daily_ui.SEPARATOR = IDX_SEPARATOR
        daily_ui.format_watchlist_detail = _telegram_final_watchlist_formatter

        # enhanced_daily_reports imports the formatter by name before this
        # module is loaded. Replace that bound reference too, but only for the
        # FINAL WATCHLIST builder path.
        report_module = sys.modules.get("modules.job_runner.enhanced_daily_reports")
        if report_module is not None:
            report_module.format_watchlist_detail = _telegram_final_watchlist_formatter
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
