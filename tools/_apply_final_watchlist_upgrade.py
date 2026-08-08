from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def append_once(path: str, marker: str, block: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8-sig")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8-sig")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"Patch anchor not found in {path}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) Expose the already-computed support level from the exit engine.
#    No scoring/target/decision formula is changed.
# ---------------------------------------------------------------------------
replace_once(
    "modules/exit_engine/exit_engine.py",
    '        "Initial_Stop": stop,\n',
    '        "Support_Level": support,\n        "Initial_Stop": stop,\n',
)

# ---------------------------------------------------------------------------
# 2) Exact FINAL WATCHLIST Telegram formatter.
# ---------------------------------------------------------------------------
append_once(
    "modules/telegram/daily_report_ui.py",
    "# FINAL_WATCHLIST_PRESENTATION_V2",
    r'''
# FINAL_WATCHLIST_PRESENTATION_V2
# Presentation-only override. All values are read from engine/report artifacts.
import json as _fw_json
from html import escape as _fw_escape


def _fw_pick(row, *keys, default="ENGINE_DATA_NOT_AVAILABLE"):
    lookup = {str(k).strip().lower(): v for k, v in dict(row or {}).items()}
    for key in keys:
        value = lookup.get(str(key).strip().lower())
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null"}:
            return value
    return default


def _fw_text(value):
    text = str(value if value is not None else "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return "ENGINE_DATA_NOT_AVAILABLE"
    return _fw_escape(text.replace("_", " "))


def _fw_num(value):
    try:
        text = str(value).strip()
        if ":" in text:
            text = text.rsplit(":", 1)[-1].strip()
        return float(text.replace(",", ""))
    except Exception:
        return None


def _fw_price(value):
    number = _fw_num(value)
    if number is None:
        return _fw_text(value)
    if abs(number - round(number)) < 1e-8:
        return f"{number:,.0f}".replace(",", ".")
    return f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fw_score(value):
    number = _fw_num(value)
    return f"{number:.0f}" if number is not None else _fw_text(value)


def _fw_confidence(value):
    number = _fw_num(value)
    if number is None:
        return _fw_text(value)
    if 0 <= number <= 1:
        number *= 100
    return f"{number:.0f}%"


def _fw_pct(value, concentration=False):
    number = _fw_num(value)
    if number is None:
        text = str(value or "").strip()
        return _fw_text(text)
    if concentration and abs(number) <= 1:
        number *= 100
    return f"{number:+.2f}%" if not concentration else f"{number:.2f}%"


def _fw_money(value):
    number = _fw_num(value)
    if number is None:
        return _fw_text(value)
    sign = "+" if number > 0 else "-" if number < 0 else ""
    amount = abs(number)
    if amount >= 1_000_000_000:
        label = f"Rp{amount / 1_000_000_000:.2f} miliar"
    elif amount >= 1_000_000:
        label = f"Rp{amount / 1_000_000:.2f} juta"
    elif amount >= 1_000:
        label = f"Rp{amount / 1_000:.2f} ribu"
    else:
        label = f"Rp{amount:,.0f}"
    return sign + label.replace(".", ",")


def _fw_rr(value):
    number = _fw_num(value)
    return f"{number:.2f}" if number is not None else _fw_text(value)


def _fw_participants(value):
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                value = _fw_json.loads(raw)
            except Exception:
                value = []
        else:
            value = []
    items = list(value or []) if isinstance(value, (list, tuple)) else []
    lines = []
    for index in range(3):
        item = items[index] if index < len(items) and isinstance(items[index], dict) else {}
        broker = _fw_text(item.get("broker"))
        avg = _fw_price(item.get("avg_price"))
        lines.append(f"{broker} @ {avg}")
    return lines


def format_watchlist_detail(row):
    """Canonical compact FINAL WATCHLIST card requested for Telegram."""
    symbol = _fw_text(_fw_pick(row, "symbol", "Symbol"))
    setup = _fw_text(_fw_pick(row, "setup", "Setup_Type"))
    analysis_date = _fw_text(_fw_pick(row, "analysis_date", "trade_date", "Trade_Date"))
    current = _fw_price(_fw_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = _fw_price(_fw_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = _fw_price(_fw_pick(row, "entry_high", "Entry_Zone_High"))
    stop = _fw_price(_fw_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = _fw_price(_fw_pick(row, "target_1", "Target_1"))
    tp2 = _fw_price(_fw_pick(row, "target_2", "Target_2"))
    rr = _fw_rr(_fw_pick(row, "risk_reward", "Target_2_RR", "Target_1_RR"))
    technical_status = _fw_text(_fw_pick(row, "technical_status", "technical_state", "Plan_Status"))
    confidence = _fw_confidence(_fw_pick(row, "confidence", "Final_Score", "Final_Score_V3"))

    broker_signal = _fw_text(_fw_pick(row, "broker_status", "broker_signal", "Broker_Confirmation", "broker_direction"))
    broker_score = _fw_score(_fw_pick(row, "broker_score", "Broker_Score"))
    net_flow = _fw_money(_fw_pick(row, "broker_net_flow", "net_flow", "cumulative_net_value"))
    buy_days = _fw_text(_fw_pick(row, "buy_days"))
    sell_days = _fw_text(_fw_pick(row, "sell_days"))
    buyer_concentration = _fw_pct(_fw_pick(row, "buyer_concentration"), concentration=True)
    seller_concentration = _fw_pct(_fw_pick(row, "seller_concentration"), concentration=True)
    top_buy = _fw_participants(_fw_pick(row, "top_buyers", default=[]))
    top_sell = _fw_participants(_fw_pick(row, "top_sellers", default=[]))
    broker_pattern = _fw_text(_fw_pick(row, "broker_pattern", "Broker_MultiDay_Context"))
    buy_cost = _fw_price(_fw_pick(row, "bandar_buy_cost", "avg_buyer_price", "weighted_broker_buy_cost"))
    vs_cost = _fw_pct(_fw_pick(row, "distance_to_buy_cost", "distance_to_buyer_avg_pct", "distance_to_buy_cost_pct"))
    flow = _fw_text(_fw_pick(row, "multi_day_flow", "Broker_MultiDay_Context"))
    persistence = _fw_text(_fw_pick(row, "flow_persistence", "Broker_Context_Alignment"))

    trend = _fw_text(_fw_pick(row, "trend", "Technical_Regime"))
    phase = _fw_text(_fw_pick(row, "phase", "execution_state", "Execution_Status"))
    support = _fw_price(_fw_pick(row, "support", "Support_Level"))
    resistance = _fw_price(_fw_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
    fib_status = _fw_text(_fw_pick(row, "fib_status", "Fibonacci_Status", "Fib_Status"))
    reason = _fw_text(_fw_pick(row, "engine_final_reason", "main_reason", "Final_Reason"))

    lines = [
        "<b>📈 SDE SWING — FINAL WATCHLIST</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>{symbol} | {setup}</b>",
        f"🕒 {analysis_date}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>🎯 TRADE SETUP</b>",
        f"💰 Current {current} | Entry {entry_low}–{entry_high}",
        f"🛑 SL {stop} | 🎯 TP1 {tp1} | 🚀 TP2 {tp2}",
        f"⚖️ RR 1:{rr}",
        f"📊 {technical_status} | 🧠 Confidence {confidence}",
        "",
        "<b>🏦 BROKER SUMMARY</b>",
        f"📌 {broker_signal} | Score {broker_score}/100",
        f"💵 Net Flow {net_flow} | 📅 Buy/Sell {buy_days}/{sell_days}",
        f"🎯 Concentration B {buyer_concentration} | S {seller_concentration}",
        "",
        "<b>🟢 Top Buy</b>",
        *top_buy,
        "",
        "<b>🔴 Top Sell</b>",
        *top_sell,
        "",
        f"📊 Pattern {broker_pattern}",
        f"💰 Buy Cost {buy_cost} | Vs Cost {vs_cost}",
        f"🌊 Flow {flow} | Persistence {persistence}",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "<b>📌 SETUP CONTEXT</b>",
        f"📈 {trend} | {phase}",
        f"🟢 Support {support} | 🔴 Resistance {resistance}",
        f"📐 Fibonacci {fib_status}",
        "",
        "<b>Reason:</b>",
        reason,
    ]
    return "\n".join(lines).strip()
''',
)

# ---------------------------------------------------------------------------
# 3) Chart renderer. Uses the same configured historical_dir as technical.
# ---------------------------------------------------------------------------
chart_path = ROOT / "modules/telegram/final_watchlist_chart.py"
chart_path.write_text(r'''from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import pandas as pd


_REQUIRED_OHLCV = ("Date", "Open", "High", "Low", "Close", "Volume")


def _number(value: Any) -> float | None:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null", "engine_data_not_available"}:
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _pick(row: Mapping[str, Any], *keys: str) -> Any:
    lookup = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        value = lookup.get(key.lower())
        if value is not None and str(value).strip().lower() not in {"", "nan", "none", "null"}:
            return value
    return None


def _price_label(value: float) -> str:
    if abs(value - round(value)) < 1e-8:
        return f"{value:,.0f}".replace(",", ".")
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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

    current = _number(_pick(row, "last_price", "current_price", "Reference_Close"))
    entry_low = _number(_pick(row, "entry_low", "Entry_Zone_Low"))
    entry_high = _number(_pick(row, "entry_high", "Entry_Zone_High"))
    stop = _number(_pick(row, "active_stop_loss", "stop_loss", "Initial_Stop"))
    tp1 = _number(_pick(row, "target_1", "Target_1"))
    tp2 = _number(_pick(row, "target_2", "Target_2"))
    support = _number(_pick(row, "support", "Support_Level"))
    resistance = _number(_pick(row, "resistance", "Nearest_Resistance", "Minor_Resistance"))
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

    swing_high = _number(_pick(row, "swing_high", "Swing_High"))
    swing_low = _number(_pick(row, "swing_low", "Swing_Low"))
    if swing_high is not None:
        ax.axhline(swing_high, linestyle="-.", linewidth=0.85, alpha=0.55)
        ax.text(x_label, swing_high, f"SWING HIGH {_price_label(swing_high)}", va="center", fontsize=8, clip_on=False)
    if swing_low is not None:
        ax.axhline(swing_low, linestyle="-.", linewidth=0.85, alpha=0.55)
        ax.text(x_label, swing_low, f"SWING LOW {_price_label(swing_low)}", va="center", fontsize=8, clip_on=False)

    fib_status = str(_pick(row, "fib_status", "Fibonacci_Status", "Fib_Status") or "").upper()
    if "VALID" in fib_status and "NOT" not in fib_status:
        for key, label in (("fib_382", "FIB 38.2%"), ("fib_500", "FIB 50%"), ("fib_618", "FIB 61.8%"), ("fib_1618", "FIB 161.8%")):
            value = _number(_pick(row, key, key.upper()))
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
''', encoding="utf-8")

# ---------------------------------------------------------------------------
# 4) Enrich FINAL WATCHLIST only from already-produced engine artifacts,
#    attach charts to every selected item, and preserve CSV as the last artifact.
# ---------------------------------------------------------------------------
append_once(
    "modules/job_runner/enhanced_daily_reports.py",
    "# FINAL_WATCHLIST_PRESENTATION_ENRICHMENT_V2",
    r'''
# FINAL_WATCHLIST_PRESENTATION_ENRICHMENT_V2
import hashlib as _fw_hashlib
import logging as _fw_logging

from modules.broker_bridge.broker_raw import read_normalized_broker_raw as _fw_read_broker_raw
from modules.telegram.final_watchlist_chart import generate_final_watchlist_chart as _fw_generate_chart

_FW_EXTRA_FINAL_COLUMNS = [
    "analysis_date", "active_stop_loss", "technical_status",
    "buy_days", "sell_days", "buyer_concentration", "seller_concentration",
    "broker_pattern", "bandar_buy_cost", "distance_to_buy_cost",
    "multi_day_flow", "flow_persistence", "phase", "support", "resistance",
    "fib_status", "swing_high", "swing_low", "engine_final_reason",
]
for _fw_column in _FW_EXTRA_FINAL_COLUMNS:
    if _fw_column not in FINAL_WATCHLIST_COLUMNS:
        FINAL_WATCHLIST_COLUMNS.append(_fw_column)


def _fw_primary_multiday_map(builder):
    path = builder.output_root / "broker_multiday" / "BROKER_WINDOW_COMPARISON.csv"
    result = {}
    for row in builder._read_csv_optional(path):
        symbol = builder._symbol(_value(row, "Symbol", "symbol"))
        if not symbol:
            continue
        primary = str(_value(row, "Primary_Window", default="5D") or "5D").upper()
        window = str(_value(row, "Window", "window", default="")).upper()
        if window == primary:
            result[symbol] = row
    return result


def _fw_raw_participant_map(builder):
    cache = getattr(builder, "_fw_raw_participant_cache", None)
    if cache is not None:
        return cache
    path = builder.output_root.parent / "input" / "broker" / "BROKER_RAW_LATEST.csv"
    result = {}
    try:
        frame = _fw_read_broker_raw(path)
        if not frame.empty:
            for symbol, group in frame.groupby("SYMBOL"):
                symbol_key = builder._symbol(symbol)
                side_map = {}
                for side in ("BUY", "SELL"):
                    subset = group[group["SIDE"].eq(side)].copy()
                    if subset.empty:
                        side_map[side] = []
                        continue
                    subset = subset.sort_values(["RANK", "NET_VALUE"], ascending=[True, False], na_position="last")
                    items = []
                    for _, raw in subset.head(3).iterrows():
                        broker = str(raw.get("BROKER_CODE") or "").strip().upper()
                        avg = raw.get("AVG_PRICE")
                        if broker and avg is not None and str(avg).lower() != "nan":
                            items.append({"broker": broker, "avg_price": float(avg), "value": raw.get("NET_VALUE")})
                    side_map[side] = items
                result[symbol_key] = side_map
    except Exception as exc:
        _fw_logging.getLogger(__name__).warning("FINAL WATCHLIST broker raw presentation enrichment failed: %s", exc)
    builder._fw_raw_participant_cache = result
    return result


def _fw_merge_participants(existing, fallback):
    items = []
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, dict) and _present(item.get("broker")):
                items.append(dict(item))
    by_broker = {str(item.get("broker", "")).upper(): item for item in items}
    for item in fallback or []:
        broker = str(item.get("broker", "")).upper()
        if not broker:
            continue
        if broker in by_broker:
            if not _present(by_broker[broker].get("avg_price")) and _present(item.get("avg_price")):
                by_broker[broker]["avg_price"] = item.get("avg_price")
        else:
            items.append(dict(item))
            by_broker[broker] = items[-1]
    usable = [item for item in items if _present(item.get("broker")) and _present(item.get("avg_price"))]
    return usable[:3]


def _fw_fill(current, sources, target, *aliases):
    if _present(current.get(target)):
        return
    found = EnhancedDailyReportBuilder._artifact_pick(sources, *aliases)
    if _present(found):
        current[target] = found


_fw_original_enrich_watchlist_rows = EnhancedDailyReportBuilder._enrich_watchlist_rows


def _fw_enrich_watchlist_rows(self, rows):
    enriched = _fw_original_enrich_watchlist_rows(self, rows)
    decision_map = self._symbol_map(self.output_root / "decision" / "FINAL_DECISION_V3.csv")
    entry_map = self._symbol_map(self.output_root / "exit" / "ENTRY_PLANS.csv")
    broker_map = self._symbol_map(self.output_root.parent / "input" / "FINAL_DECISION_V2.csv")
    multiday_detail = self._symbol_map(self.output_root / "broker_multiday" / "BROKER_MULTIDAY_DETAIL.csv")
    multiday_primary = _fw_primary_multiday_map(self)
    raw_participants = _fw_raw_participant_map(self)

    for current in enriched:
        symbol = self._symbol(current.get("symbol"))
        if not _present(current.get("engine_final_reason")):
            current["engine_final_reason"] = current.get("main_reason", "")
        current["analysis_date"] = current.get("trade_date", "")
        sources = [
            current,
            entry_map.get(symbol, {}),
            decision_map.get(symbol, {}),
            broker_map.get(symbol, {}),
            multiday_detail.get(symbol, {}),
            multiday_primary.get(symbol, {}),
        ]

        _fw_fill(current, sources, "active_stop_loss", "Active_Stop_Loss", "activeStopLoss", "Initial_Stop", "Stop_Loss")
        if not _present(current.get("active_stop_loss")):
            current["active_stop_loss"] = current.get("stop_loss", "")
        _fw_fill(current, sources, "technical_status", "Technical_Status", "Plan_Status", "Execution_Status", "Technical_State")
        if not _present(current.get("technical_status")):
            current["technical_status"] = current.get("technical_state") or current.get("execution_state")

        _fw_fill(current, sources, "buyer_concentration", "BUYER_CONCENTRATION", "Buyer_Concentration", "buyer_concentration")
        _fw_fill(current, sources, "seller_concentration", "SELLER_CONCENTRATION", "Seller_Concentration", "seller_concentration")
        _fw_fill(current, sources, "broker_pattern", "BROKER_PATTERN", "Broker_Pattern", "Divergence_Label", "Broker_MultiDay_Context", "Classification")
        _fw_fill(current, sources, "bandar_buy_cost", "Bandar_Buy_Cost", "AVG_BUYER_PRICE", "weighted_broker_buy_cost")
        if not _present(current.get("bandar_buy_cost")):
            current["bandar_buy_cost"] = current.get("avg_buyer_price", "")
        _fw_fill(current, sources, "distance_to_buy_cost", "DISTANCE_TO_BUY_COST", "Distance_To_Buy_Cost_Pct", "distance_to_buy_cost_pct")
        if not _present(current.get("distance_to_buy_cost")):
            current["distance_to_buy_cost"] = current.get("distance_to_buyer_avg_pct", "")
        _fw_fill(current, sources, "multi_day_flow", "Broker_MultiDay_Context", "Context", "Classification", "Broker_Context_Primary")
        _fw_fill(current, sources, "phase", "Phase", "Setup_Phase", "Execution_Status", "Plan_Status")
        if not _present(current.get("phase")):
            current["phase"] = current.get("execution_state") or current.get("setup")
        _fw_fill(current, sources, "support", "Support_Level", "Support", "Technical_Support")
        _fw_fill(current, sources, "resistance", "Nearest_Resistance", "Minor_Resistance", "Resistance_Level", "Resistance")
        _fw_fill(current, sources, "fib_status", "Fibonacci_Status", "Fib_Status", "FIB_STATUS", "Target_Fib_Status")
        if not _present(current.get("fib_status")):
            # The inspected v1.7 branch has no Fibonacci target artifact. Keep the
            # card explicit instead of fabricating a level or silently leaving it blank.
            current["fib_status"] = "ENGINE_NOT_AVAILABLE_V1_7"
        _fw_fill(current, sources, "swing_high", "Swing_High", "Valid_Swing_High")
        _fw_fill(current, sources, "swing_low", "Swing_Low", "Valid_Swing_Low")
        if not _present(current.get("trend")):
            _fw_fill(current, sources, "trend", "Trend", "Technical_Regime", "Trend_State")

        primary = multiday_primary.get(symbol, {})
        available = _float(_value(primary, "available_sessions", "Available_Sessions", default=0), 0.0)
        positive_ratio = _float(_value(primary, "positive_day_ratio", "Positive_Day_Ratio", default=0), 0.0)
        negative_ratio = _float(_value(primary, "negative_day_ratio", "Negative_Day_Ratio", default=0), 0.0)
        if available > 0:
            current["buy_days"] = int(round(positive_ratio * available))
            current["sell_days"] = int(round(negative_ratio * available))

        net_flow = _float(current.get("broker_net_flow"), 0.0)
        persistence_source = multiday_detail.get(symbol, {})
        if net_flow >= 0:
            persistence = _value(persistence_source, "Buyer_Rotation_Status", default="")
        else:
            persistence = _value(persistence_source, "Seller_Rotation_Status", default="")
        if not _present(persistence):
            persistence = _value(persistence_source, "Broker_Context_Alignment", "Alignment", default="")
        current["flow_persistence"] = persistence

        fallback = raw_participants.get(symbol, {})
        current["top_buyers"] = _fw_merge_participants(current.get("top_buyers"), fallback.get("BUY", []))
        current["top_sellers"] = _fw_merge_participants(current.get("top_sellers"), fallback.get("SELL", []))

        if not _present(current.get("broker_status")):
            current["broker_status"] = current.get("broker_direction") or current.get("multi_day_flow")
        if not _present(current.get("broker_net_flow")):
            _fw_fill(current, sources, "broker_net_flow", "NET_FLOW", "Net_Flow", "cumulative_net_value")

    return enriched


EnhancedDailyReportBuilder._enrich_watchlist_rows = _fw_enrich_watchlist_rows


def _fw_material_signature(row):
    payload = "|".join(str(row.get(key, "")) for key in (
        "symbol", "trade_date", "setup", "entry_low", "entry_high",
        "active_stop_loss", "stop_loss", "target_1", "target_2",
    ))
    return _fw_hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


_fw_original_build_final_watchlist = EnhancedDailyReportBuilder.build_final_watchlist


def _fw_build_final_watchlist(self, data):
    # User contract: every FINAL WATCHLIST item gets a card/chart; CSV remains last.
    original_limit = self.max_watchlist_messages
    self.max_watchlist_messages = 10000
    try:
        artifacts = _fw_original_build_final_watchlist(self, data)
    finally:
        self.max_watchlist_messages = original_limit

    trade_date = str(data.get("trade_date", ""))
    csv_path = self.output_root / "final_watchlist" / f"sde-final-watchlist-{trade_date}.csv"
    rows = self._read_csv_optional(csv_path)
    row_map = {self._symbol(row.get("symbol")): row for row in rows if self._symbol(row.get("symbol"))}
    historical_dir = Path(getattr(self, "historical_dir", "data/output/historical/by_symbol"))
    chart_output_root = Path(getattr(self, "chart_output_root", "output/final_watchlist"))

    patched = []
    for artifact in artifacts:
        if artifact.report_type != "final_watchlist_detail" or not artifact.symbol:
            patched.append(artifact)
            continue
        row = row_map.get(self._symbol(artifact.symbol), {})
        material_signature = _fw_material_signature(row)
        details = dict(artifact.validation_details or {})
        details["material_signature"] = material_signature
        chart = None
        try:
            chart = _fw_generate_chart(
                row,
                historical_dir=historical_dir,
                output_dir=chart_output_root,
                candle_limit=80,
            )
            details["chart_status"] = "GENERATED"
            details["chart_path"] = str(chart)
        except Exception as exc:
            details["chart_status"] = "FAILED_TEXT_FALLBACK"
            details["chart_error"] = str(exc)
            _fw_logging.getLogger(__name__).warning(
                "Chart generation failed for %s: %s", artifact.symbol, exc
            )

        full_text = artifact.text.strip()
        marker = "<b>🏦 BROKER SUMMARY</b>"
        caption = full_text.split(marker, 1)[0].strip() if marker in full_text else full_text[:900]
        patched.append(DailyReportArtifact(
            report_type=artifact.report_type,
            text=artifact.text,
            topic=artifact.topic,
            symbol=artifact.symbol,
            attachment_path=chart,
            caption=caption,
            input_paths=artifact.input_paths,
            source_of_truth=artifact.source_of_truth,
            row_count=artifact.row_count,
            validation_details=details,
        ))
    return patched


EnhancedDailyReportBuilder.build_final_watchlist = _fw_build_final_watchlist
''',
)

# ---------------------------------------------------------------------------
# 5) Runtime bridge: point chart renderer to the exact configured historical_dir
#    and carry material signature through lineage/idempotency.
# ---------------------------------------------------------------------------
append_once(
    "modules/job_runner/enhanced_runtime_bridge.py",
    "# FINAL_WATCHLIST_RUNTIME_BRIDGE_V2",
    r'''
# FINAL_WATCHLIST_RUNTIME_BRIDGE_V2
_fw_original_builder = _builder


def _builder(ctx: RunnerContext) -> EnhancedDailyReportBuilder:
    builder = _fw_original_builder(ctx)
    cfg = ctx.scheduler_config.get("enhanced_reporting", {})
    builder.historical_dir = ctx.path("historical_dir", "data/output/historical/by_symbol")
    builder.chart_output_root = resolve(cfg.get("final_watchlist_chart_output_root", "output/final_watchlist"))
    return builder


_fw_original_artifact_payload = _artifact_payload


def _artifact_payload(artifact: DailyReportArtifact) -> ReportPayload:
    payload = _fw_original_artifact_payload(artifact)
    details = dict(artifact.validation_details or {})
    material = str(details.get("material_signature") or "").strip()
    if material:
        payload.material_signature = material
        payload.signal_version = material
    return payload


_fw_original_artifact_with_lineage = _artifact_with_lineage


def _artifact_with_lineage(
    artifact: DailyReportArtifact,
    *,
    input_paths: Iterable[str | Path],
    source_of_truth: Iterable[str | Path],
    row_count: int | None = None,
    validation_details: dict[str, Any] | None = None,
) -> DailyReportArtifact:
    merged = dict(artifact.validation_details or {})
    merged.update(dict(validation_details or {}))
    return DailyReportArtifact(
        report_type=artifact.report_type,
        text=artifact.text,
        topic=artifact.topic,
        symbol=artifact.symbol,
        attachment_path=artifact.attachment_path,
        caption=artifact.caption,
        input_paths=tuple(str(path) for path in input_paths),
        source_of_truth=tuple(str(path) for path in source_of_truth),
        row_count=row_count,
        validation_details=merged,
    )
''',
)

# ---------------------------------------------------------------------------
# 6) Telegram delivery: PNG/JPG use sendPhoto, split long cards after the Trade
#    Setup caption, fallback to full text if photo delivery fails. CSV remains
#    sendDocument and follows artifact order.
# ---------------------------------------------------------------------------
append_once(
    "modules/job_runner/delivery.py",
    "# FINAL_WATCHLIST_PHOTO_DELIVERY_V2",
    r'''
# FINAL_WATCHLIST_PHOTO_DELIVERY_V2
_PHOTO_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_fw_legacy_idempotency_key = _idempotency_key


def _is_photo_attachment(path: Path | None) -> bool:
    return path is not None and path.suffix.lower() in _PHOTO_SUFFIXES


def _idempotency_key(ctx: RunnerContext, payload: ReportPayload) -> str:
    attachment = _attachment_path(payload)
    report = payload.report_type.upper()
    if _is_photo_attachment(attachment) and report == "FINAL_WATCHLIST_DETAIL":
        symbol = (payload.symbol or "UNKNOWN").upper()
        material = payload.material_signature or payload.signal_version or payload.signature[:24]
        return f"{ctx.trade_date.isoformat()}:{report}:{symbol}:{material}"
    if attachment is None and report == "FINAL_WATCHLIST_DETAIL" and (payload.material_signature or payload.signal_version):
        symbol = (payload.symbol or "UNKNOWN").upper()
        material = payload.material_signature or payload.signal_version
        return f"{ctx.trade_date.isoformat()}:{report}:{symbol}:{material}"
    return _fw_legacy_idempotency_key(ctx, payload)


def _send_photo(ctx: RunnerContext, payload: ReportPayload, caption: str) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang. Jalankan maintenance\\INSTALL_REQUIREMENTS.bat.")
    path = _attachment_path(payload)
    if path is None or not path.exists() or not path.is_file():
        raise RuntimeError(f"Photo attachment tidak ditemukan: {path}")
    token, chat_id = _credentials(ctx)
    data: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption[:1024]
        data["parse_mode"] = "HTML"
    topic_id = _topic_id(ctx, payload)
    if topic_id:
        data["message_thread_id"] = topic_id
    with path.open("rb") as handle:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=data,
            files={"photo": (path.name, handle, "image/png")},
            timeout=60,
        )
    return _response_json(response)


def _photo_parts(payload: ReportPayload, max_len: int) -> tuple[str, list[str]]:
    full = normalize_telegram_text(payload.text)
    if len(full) <= 1024:
        return full, []
    caption = normalize_telegram_text(_attachment_caption(payload))
    if not caption:
        caption = full[:900]
    caption = caption[:1024]
    if full.startswith(caption):
        remainder = full[len(caption):].strip()
    else:
        remainder = full
    return caption, split_telegram_text(remainder, max_len=max_len) if remainder else []


def deliver(ctx: RunnerContext, payloads: list[ReportPayload]) -> list[dict[str, Any]]:
    index_path, log_path = _state_paths(ctx)
    index = read_json(index_path)
    results: list[dict[str, Any]] = []
    failed_root = resolve(ctx.scheduler_config.get("delivery", {}).get("failed_root", "data/output/failed_delivery"))
    delivery_total = len(payloads)
    credentials_ready = telegram_configured(ctx)
    provenance = getattr(ctx, "config_provenance", {}) or {}
    official_runtime = str(provenance.get("config_version", "")) == "1.7.0-multisource"

    for delivery_sequence, payload in enumerate(payloads, start=1):
        allowed, reason = should_send(ctx, payload)
        key = _idempotency_key(ctx, payload)
        attachment = _attachment_path(payload)
        is_photo = _is_photo_attachment(attachment)
        max_len = int(ctx.scheduler_config.get("telegram", {}).get("maximum_message_length", 4000))
        normalized_text = normalize_telegram_text(payload.text)
        photo_caption, photo_followups = _photo_parts(payload, max_len) if is_photo else ("", [])
        parts = [] if attachment is not None else split_telegram_text(normalized_text, max_len=max_len)
        expected_parts = (1 + len(photo_followups)) if is_photo else (1 if attachment is not None else len(parts))
        base = {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "report_type": payload.report_type,
            "signature": payload.signature,
            "idempotency_key": key,
            "part_count": expected_parts,
            "delivery_sequence": delivery_sequence,
            "delivery_total": delivery_total,
            "force_resend": bool(ctx.force),
            "attachment_path": str(attachment) if attachment else "",
            **telegram_route(ctx, payload),
            "telegram_message_id": "",
        }
        if not allowed:
            event = {**base, "status": reason}
            append_jsonl(log_path, event)
            results.append(event)
            continue
        if not credentials_ready and official_runtime:
            event = {**base, "status": "SKIPPED_NOT_CONFIGURED", "reason": "TELEGRAM_CREDENTIALS_EMPTY"}
            append_jsonl(log_path, event)
            results.append(event)
            continue

        message_ids: list[Any] = []
        part_events: list[dict[str, Any]] = []
        try:
            if is_photo:
                try:
                    response = _send_photo(ctx, payload, photo_caption)
                except Exception as photo_exc:
                    fallback_parts = split_telegram_text(normalized_text, max_len=max_len)
                    for idx, part in enumerate(fallback_parts, start=1):
                        response = _send_telegram(ctx, payload, text=part, part_index=idx, part_count=len(fallback_parts))
                        message_id = response.get("result", {}).get("message_id", "")
                        message_ids.append(message_id)
                        part_event = {**base, "status": "SENT_FALLBACK_PART", "part_index": idx, "telegram_message_id": message_id}
                        append_jsonl(log_path, part_event)
                        part_events.append(part_event)
                    event = {
                        **base,
                        "status": "SENT_WITH_TEXT_FALLBACK",
                        "photo_error": str(photo_exc),
                        "telegram_message_ids": message_ids,
                        "parts": part_events,
                    }
                    index[key] = event
                    write_json(index_path, index)
                    append_jsonl(log_path, event)
                    results.append(event)
                    continue

                message_id = response.get("result", {}).get("message_id", "")
                message_ids.append(message_id)
                photo_event = {**base, "status": "SENT_PHOTO", "part_index": 1, "telegram_message_id": message_id}
                append_jsonl(log_path, photo_event)
                part_events.append(photo_event)
                for idx, part in enumerate(photo_followups, start=2):
                    response = _send_telegram(ctx, payload, text=part, part_index=idx, part_count=expected_parts)
                    message_id = response.get("result", {}).get("message_id", "")
                    message_ids.append(message_id)
                    part_event = {**base, "status": "SENT_PART", "part_index": idx, "telegram_message_id": message_id}
                    append_jsonl(log_path, part_event)
                    part_events.append(part_event)
            elif attachment is not None:
                response = _send_document(ctx, payload)
                message_id = response.get("result", {}).get("message_id", "")
                message_ids.append(message_id)
                part_event = {**base, "status": "SENT_PART", "part_index": 1, "telegram_message_id": message_id}
                append_jsonl(log_path, part_event)
                part_events.append(part_event)
            else:
                for idx, part in enumerate(parts, start=1):
                    response = _send_telegram(ctx, payload, text=part, part_index=idx, part_count=len(parts))
                    message_id = response.get("result", {}).get("message_id", "")
                    message_ids.append(message_id)
                    part_event = {**base, "status": "SENT_PART", "part_index": idx, "telegram_message_id": message_id}
                    append_jsonl(log_path, part_event)
                    part_events.append(part_event)

            event = {**base, "status": "SENT", "telegram_message_ids": message_ids, "parts": part_events}
            lifecycle_ack_failed = False
            lifecycle_ids = tuple(getattr(payload, "lifecycle_event_ids", ()) or ())
            if lifecycle_ids:
                try:
                    _mark_lifecycle_events_notified(ctx, lifecycle_ids)
                except Exception as exc:
                    lifecycle_ack_failed = True
                    append_jsonl(log_path, {**base, "status": "LIFECYCLE_ACK_FAILED", "error": str(exc)})
            if not lifecycle_ack_failed:
                index[key] = event
                write_json(index_path, index)
            append_jsonl(log_path, event)
            results.append(event)
        except Exception as exc:
            folder = failed_root / ctx.trade_date.isoformat()
            folder.mkdir(parents=True, exist_ok=True)
            suffix = attachment.suffix if attachment is not None else ".txt"
            payload_path = folder / f"{ctx.run_id}_{payload.report_type}{suffix}"
            if attachment is not None and attachment.exists():
                payload_path.write_bytes(attachment.read_bytes())
            else:
                payload_path.write_text(normalized_text, encoding="utf-8")
            event = {
                **base,
                "status": "FAILED",
                "error": str(exc),
                "failed_payload": str(payload_path),
                "failed_payload_sha256": file_sha256(payload_path),
                "telegram_message_ids": message_ids,
                "sent_parts_before_failure": len(message_ids),
            }
            append_jsonl(log_path, event)
            results.append(event)
    return results
''',
)

# ---------------------------------------------------------------------------
# 7) Dependency + regression tests.
# ---------------------------------------------------------------------------
requirements = ROOT / "requirements.txt"
req_text = requirements.read_text(encoding="utf-8")
if "matplotlib" not in req_text.lower():
    requirements.write_text(req_text.rstrip() + "\nmatplotlib>=3.8\n", encoding="utf-8")


test_path = ROOT / "tests/test_final_watchlist_presentation.py"
test_path.write_text(r'''from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from modules.job_runner.delivery import _idempotency_key
from modules.job_runner.reports import ReportPayload
from modules.telegram.daily_report_ui import format_watchlist_detail
from modules.telegram.final_watchlist_chart import generate_final_watchlist_chart


def full_row():
    return {
        "symbol": "ANTM",
        "setup": "BREAKOUT_RETEST",
        "trade_date": "2026-08-08",
        "last_price": 3390,
        "entry_low": 3350,
        "entry_high": 3400,
        "active_stop_loss": 3220,
        "target_1": 3600,
        "target_2": 3850,
        "risk_reward": 2.4,
        "technical_status": "VALID_SETUP",
        "confidence": 84,
        "broker_status": "BROKER_CONFIRM",
        "broker_score": 78,
        "broker_net_flow": 42_600_000_000,
        "buy_days": 4,
        "sell_days": 1,
        "buyer_concentration": 72,
        "seller_concentration": 51,
        "top_buyers": [
            {"broker": "XL", "avg_price": 3365},
            {"broker": "CC", "avg_price": 3352},
            {"broker": "YP", "avg_price": 3380},
        ],
        "top_sellers": [
            {"broker": "AK", "avg_price": 3425},
            {"broker": "LG", "avg_price": 3410},
            {"broker": "PD", "avg_price": 3398},
        ],
        "broker_pattern": "CONFIRMED_ACCUMULATION",
        "bandar_buy_cost": 3365,
        "distance_to_buy_cost": 0.74,
        "multi_day_flow": "ACCUMULATION",
        "flow_persistence": "STABLE_DOMINANCE",
        "trend": "UPTREND",
        "phase": "WAIT_TRIGGER",
        "support": 3300,
        "resistance": 3600,
        "fib_status": "ENGINE_NOT_AVAILABLE_V1_7",
        "engine_final_reason": "Struktur trend valid dan broker mengonfirmasi akumulasi.",
    }


def test_final_watchlist_format_is_exact_and_bold():
    text = format_watchlist_detail(full_row())
    assert text.startswith("<b>📈 SDE SWING — FINAL WATCHLIST</b>\n━━━━━━━━━━━━━━━━━━━━")
    assert "<b>🎯 TRADE SETUP</b>" in text
    assert "<b>🏦 BROKER SUMMARY</b>" in text
    assert "<b>🟢 Top Buy</b>" in text
    assert "XL @ 3.365" in text and "CC @ 3.352" in text and "YP @ 3.380" in text
    assert "<b>🔴 Top Sell</b>" in text
    assert "AK @ 3.425" in text and "LG @ 3.410" in text and "PD @ 3.398" in text
    assert "📅 Buy/Sell 4/1" in text
    assert "🎯 Concentration B 72.00% | S 51.00%" in text
    assert "<b>📌 SETUP CONTEXT</b>" in text
    assert "<b>Reason:</b>" in text


def test_chart_uses_same_historical_candle_directory(tmp_path: Path):
    historical = tmp_path / "historical"
    historical.mkdir()
    dates = pd.date_range("2026-04-01", periods=90, freq="B")
    base = pd.Series(range(90), dtype=float) + 3200
    frame = pd.DataFrame({
        "Date": dates,
        "Open": base + 1,
        "High": base + 20,
        "Low": base - 20,
        "Close": base + 5,
        "Volume": 1_000_000 + base * 10,
    })
    frame.to_csv(historical / "ANTM.csv", index=False)
    out = generate_final_watchlist_chart(full_row(), historical_dir=historical, output_dir=tmp_path / "charts", candle_limit=80)
    assert out.name == "ANTM_setup.png"
    assert out.exists() and out.stat().st_size > 1000


class DummyContext:
    trade_date = date(2026, 8, 8)
    run_id = "run-a"


def test_final_watchlist_idempotency_tracks_material_setup_not_run_id(tmp_path: Path):
    image = tmp_path / "ANTM_setup.png"
    image.write_bytes(b"png")
    first = ReportPayload("final_watchlist_detail", "a.txt", "text one", symbol="ANTM", material_signature="abc123")
    second = ReportPayload("final_watchlist_detail", "b.txt", "text two", symbol="ANTM", material_signature="abc123")
    changed = ReportPayload("final_watchlist_detail", "c.txt", "text two", symbol="ANTM", material_signature="def456")
    for payload in (first, second, changed):
        setattr(payload, "attachment_path", image)
    assert _idempotency_key(DummyContext(), first) == _idempotency_key(DummyContext(), second)
    assert _idempotency_key(DummyContext(), first) != _idempotency_key(DummyContext(), changed)
''', encoding="utf-8")

# Remove staging machinery from the final implementation commit.
for relative in (
    ".github/workflows/final-watchlist-upgrade.yml",
    "tools/_apply_final_watchlist_upgrade.py",
):
    target = ROOT / relative
    if target.exists():
        target.unlink()
