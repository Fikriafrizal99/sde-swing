#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


DECISION_ORDER = ["BUY READY", "BUY ON TRIGGER", "WATCH", "AVOID", "STRONG BUY", "BUY", "BUY CANDIDATE", "WATCH HIGH", "SPECULATIVE"]
ENTRY_DECISIONS = {"BUY READY", "STRONG BUY", "BUY"}


def norm_col(value: str) -> str:
    value = str(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


def find_col(df: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {norm_col(c): c for c in df.columns}
    for alias in aliases:
        key = norm_col(alias)
        if key in mapping:
            return mapping[key]
    return None


def require_col(df: pd.DataFrame, *aliases: str) -> str:
    col = find_col(df, *aliases)
    if col is None:
        raise ValueError(f"Kolom wajib tidak ditemukan. Salah satu dari: {aliases}")
    return col


def load_signals(path: Path) -> pd.DataFrame:
    if path.is_dir():
        files = sorted(path.glob("*.csv"))
        if not files:
            raise ValueError(f"Tidak ada CSV snapshot di folder {path}")
        frames = []
        for file in files:
            frame = pd.read_csv(file, low_memory=False)
            date_col = find_col(frame, "Signal_Date", "Snapshot_Date", "Date")
            if date_col is None:
                match = re.search(r"(20\d{2}-\d{2}-\d{2})", file.name)
                if not match:
                    raise ValueError(f"{file.name}: Signal_Date tidak ada.")
                frame["Signal_Date"] = match.group(1)
            else:
                frame = frame.rename(columns={date_col: "Signal_Date"})
            frame = frame.copy()
            frame["Snapshot_Source"] = file.name
            frames.append(frame)
        signals = pd.concat(frames, ignore_index=True)
    else:
        signals = pd.read_csv(path, low_memory=False)
        date_col = require_col(signals, "Signal_Date", "Snapshot_Date", "Date")
        signals = signals.rename(columns={date_col: "Signal_Date"})
        signals = signals.copy()
        signals["Snapshot_Source"] = path.name

    symbol_col = require_col(signals, "Symbol", "EMITEN", "Ticker")
    decision_col = require_col(signals, "Decision_Status_Final", "Decision_Status", "Decision_V3", "Decision_V2_1", "Decision")
    score_col = find_col(signals, "Final_Score_V3", "Final_Score", "Score")

    # FINAL_DECISION_V3 can contain both Decision_V3 and an older Decision column.
    # Build canonical columns explicitly so duplicate names never turn Series access
    # into a DataFrame.
    symbol_values = signals[symbol_col].copy()
    decision_values = signals[decision_col].copy()
    score_values = signals[score_col].copy() if score_col else pd.Series(np.nan, index=signals.index)
    drop_cols = {symbol_col, decision_col, "Symbol", "Decision", "Signal_Score"}
    if score_col:
        drop_cols.add(score_col)
    signals = signals.drop(columns=[c for c in drop_cols if c in signals.columns]).copy()
    signals.insert(0, "Signal_Score", score_values)
    signals.insert(0, "Decision", decision_values)
    signals.insert(0, "Symbol", symbol_values)

    signals["Signal_Date"] = pd.to_datetime(signals["Signal_Date"], errors="coerce")
    signals["Symbol"] = signals["Symbol"].astype(str).str.upper().str.strip().str.replace(".JK", "", regex=False)
    signals["Decision"] = signals["Decision"].astype(str).str.upper().str.strip()
    signals["Signal_Score"] = pd.to_numeric(signals["Signal_Score"], errors="coerce")
    return signals.dropna(subset=["Signal_Date", "Symbol"]).sort_values(["Signal_Date", "Symbol"])


def load_price(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    rename = {
        require_col(df, "date", "datetime", "timestamp"): "Date",
        require_col(df, "open"): "Open",
        require_col(df, "high"): "High",
        require_col(df, "low"): "Low",
        require_col(df, "close"): "Close",
    }
    volume_col = find_col(df, "volume")
    if volume_col:
        rename[volume_col] = "Volume"
    df = df.rename(columns=rename)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["Date", "Open", "High", "Low", "Close"]).sort_values("Date").drop_duplicates("Date").reset_index(drop=True)


def index_price_files(price_dir: Path) -> dict[str, Path]:
    out = {}
    for file in price_dir.rglob("*.csv"):
        try:
            sample = pd.read_csv(file, nrows=3)
        except Exception:
            continue
        if not all(find_col(sample, x) for x in ["date", "open", "high", "low", "close"]):
            continue
        symbol_col = find_col(sample, "symbol", "ticker", "emiten")
        symbol = str(sample[symbol_col].iloc[0]).upper().strip() if symbol_col and len(sample) else file.stem.upper()
        out[symbol.replace(".JK", "")] = file
    return out


def load_market_regime(ihsg_path: Path) -> pd.DataFrame:
    px = load_price(ihsg_path)
    px["MA20"] = px["Close"].rolling(20).mean()
    px["MA50"] = px["Close"].rolling(50).mean()
    px["MA20_Slope"] = px["MA20"].diff(5)

    def classify(row):
        if pd.isna(row["MA20"]) or pd.isna(row["MA50"]):
            return "UNKNOWN"
        if row["Close"] > row["MA20"] > row["MA50"] and row["MA20_Slope"] > 0:
            return "BULLISH"
        if row["Close"] < row["MA20"] < row["MA50"] and row["MA20_Slope"] < 0:
            return "BEARISH"
        return "SIDEWAYS"

    px["Market_Regime"] = px.apply(classify, axis=1)
    return px[["Date", "Close", "MA20", "MA50", "Market_Regime"]]


def regime_asof(regime: pd.DataFrame, date: pd.Timestamp) -> str:
    found = regime[regime["Date"] <= date]
    return "UNKNOWN" if found.empty else str(found.iloc[-1]["Market_Regime"])


def apply_market_gate(decision: str, regime: str) -> str:
    """Market regime is context, not a universal rejection gate in Stage 2."""
    return decision


def suppress_repeated_entries(signals: pd.DataFrame, max_hold_days: int) -> pd.DataFrame:
    """One symbol = one active trade. Controls remain observable but repeated BUY entries are suppressed."""
    signals = signals.copy()
    signals["Trade_Eligible"] = True
    signals["Repeat_Status"] = "FIRST_SIGNAL"
    active_until: dict[str, pd.Timestamp] = {}

    for idx, row in signals.sort_values(["Signal_Date", "Symbol"]).iterrows():
        symbol = row["Symbol"]
        decision = row["Gated_Decision"]
        date = row["Signal_Date"]

        if symbol in active_until and date <= active_until[symbol]:
            if decision in ENTRY_DECISIONS:
                signals.at[idx, "Trade_Eligible"] = False
                signals.at[idx, "Repeat_Status"] = "SUPPRESSED_ACTIVE_TRADE"
            else:
                signals.at[idx, "Repeat_Status"] = "CONTROL_DURING_ACTIVE_TRADE"
        elif decision in ENTRY_DECISIONS:
            active_until[symbol] = date + pd.offsets.BDay(max_hold_days)
            signals.at[idx, "Repeat_Status"] = "NEW_ACTIVE_TRADE"
        else:
            signals.at[idx, "Repeat_Status"] = "CONTROL_SIGNAL"
    return signals


def evaluate_signal(signal, px, horizons, entry_mode, max_hold_days):
    signal_date = signal["Signal_Date"]
    candidates = px.index[px["Date"] > signal_date] if entry_mode == "next_open" else px.index[px["Date"] >= signal_date]
    base = signal.to_dict()
    if len(candidates) == 0:
        return {**base, "Status": "NO_FUTURE_PRICE"}

    entry_idx = int(candidates[0])
    raw_entry_price = float(px.loc[entry_idx, "Open"] if entry_mode == "next_open" else px.loc[entry_idx, "Close"])
    slippage_pct = pd.to_numeric(pd.Series([signal.get("Estimated_Slippage_Pct", signal.get("Liquidity_Estimated_Slippage_Pct", 0.0))]), errors="coerce").fillna(0.0).iloc[0]
    entry_price = raw_entry_price * (1.0 + max(float(slippage_pct), 0.0) / 100.0)
    result = {**base, "Status": "OK", "Entry_Date": px.loc[entry_idx, "Date"], "Raw_Entry_Price": raw_entry_price, "Entry_Price": entry_price, "Slippage_Pct": slippage_pct}

    for h in horizons:
        exit_idx = entry_idx + h
        if exit_idx < len(px):
            window = px.loc[entry_idx:exit_idx]
            gross = (float(px.loc[exit_idx, "Close"]) / entry_price - 1) * 100
            result[f"Return_{h}D_Pct"] = gross
            result[f"MFE_{h}D_Pct"] = (window["High"].max() / entry_price - 1) * 100
            result[f"MAE_{h}D_Pct"] = (window["Low"].min() / entry_price - 1) * 100
            result[f"Exit_Date_{h}D"] = px.loc[exit_idx, "Date"]
        else:
            result[f"Return_{h}D_Pct"] = np.nan
            result[f"MFE_{h}D_Pct"] = np.nan
            result[f"MAE_{h}D_Pct"] = np.nan

    for h in [1, 3, 5, 7]:
        result[f"Close_D{h}"] = np.nan
        result[f"Return_D{h}_Pct"] = np.nan
        exit_idx = entry_idx + h
        if exit_idx < len(px):
            result[f"Close_D{h}"] = float(px.loc[exit_idx, "Close"])
            result[f"Return_D{h}_Pct"] = (float(px.loc[exit_idx, "Close"]) / entry_price - 1) * 100

    window7 = px.loc[entry_idx:min(entry_idx + 7, len(px) - 1)]
    result["Max_Price_D7"] = float(window7["High"].max()) if not window7.empty else np.nan
    result["Min_Price_D7"] = float(window7["Low"].min()) if not window7.empty else np.nan
    result["MFE_D7_Pct"] = (result["Max_Price_D7"] / entry_price - 1) * 100 if entry_price else np.nan
    result["MAE_D7_Pct"] = (result["Min_Price_D7"] / entry_price - 1) * 100 if entry_price else np.nan
    stop = signal.get("Initial_Stop", signal.get("Stop_Loss", np.nan))
    tp1 = signal.get("Target_1", signal.get("Take_Profit_1", np.nan))
    tp2 = signal.get("Target_2", signal.get("Take_Profit_2", np.nan))
    stop = pd.to_numeric(pd.Series([stop]), errors="coerce").iloc[0]
    tp1 = pd.to_numeric(pd.Series([tp1]), errors="coerce").iloc[0]
    tp2 = pd.to_numeric(pd.Series([tp2]), errors="coerce").iloc[0]
    result["Stop_Loss"] = stop
    result["Take_Profit_1"] = tp1
    result["Take_Profit_2"] = tp2
    result["SL_Hit_D7"] = bool(pd.notna(stop) and not window7.empty and window7["Low"].le(stop).any())
    result["TP1_Hit_D7"] = bool(pd.notna(tp1) and not window7.empty and window7["High"].ge(tp1).any())
    result["TP2_Hit_D7"] = bool(pd.notna(tp2) and not window7.empty and window7["High"].ge(tp2).any())
    if pd.isna(result["Return_D7_Pct"]):
        result["Final_Outcome_D7"] = "OPEN"
    elif result["SL_Hit_D7"] and not (result["TP1_Hit_D7"] or result["TP2_Hit_D7"]):
        result["Final_Outcome_D7"] = "LOSS"
    elif result["TP1_Hit_D7"] or result["TP2_Hit_D7"] or result["Return_D7_Pct"] > 0:
        result["Final_Outcome_D7"] = "WIN"
    elif result["Return_D7_Pct"] < 0:
        result["Final_Outcome_D7"] = "LOSS"
    else:
        result["Final_Outcome_D7"] = "AMBIGUOUS"

    risk_pct = ((entry_price - stop) / entry_price * 100.0) if pd.notna(stop) and entry_price > stop else np.nan
    result["Initial_Risk_Pct"] = risk_pct
    result["Return_R_D7"] = result["Return_D7_Pct"] / risk_pct if pd.notna(risk_pct) and risk_pct > 0 and pd.notna(result["Return_D7_Pct"]) else np.nan
    result["MFE_R_D7"] = result["MFE_D7_Pct"] / risk_pct if pd.notna(risk_pct) and risk_pct > 0 else np.nan
    result["MAE_R_D7"] = result["MAE_D7_Pct"] / risk_pct if pd.notna(risk_pct) and risk_pct > 0 else np.nan
    result["Holding_Period_Days"] = min(7, max(0, len(window7) - 1))
    result["False_Positive"] = bool(signal.get("Gated_Decision") in ENTRY_DECISIONS and result["Final_Outcome_D7"] == "LOSS")
    result["False_Negative"] = bool(signal.get("Gated_Decision") not in ENTRY_DECISIONS and pd.notna(result["Return_D7_Pct"]) and result["Return_D7_Pct"] >= 3.0)

    last_idx = min(entry_idx + max_hold_days, len(px) - 1)
    result["MaxHold_Exit_Date"] = px.loc[last_idx, "Date"]
    result["MaxHold_Return_Pct"] = (float(px.loc[last_idx, "Close"]) / entry_price - 1) * 100
    return result


def attach_entry_plans(signals: pd.DataFrame, entry_plans_path: Path | None) -> pd.DataFrame:
    if not entry_plans_path or not entry_plans_path.exists() or entry_plans_path.stat().st_size == 0:
        return signals
    plans = pd.read_csv(entry_plans_path, low_memory=False)
    if plans.empty:
        return signals
    signal_symbol = require_col(signals, "Symbol")
    plan_symbol = require_col(plans, "Symbol")
    keep_aliases = [
        "Symbol", "Reference_Close", "Entry_Price", "Initial_Stop", "Stop_Loss",
        "Target_1", "Take_Profit_1", "Target_2", "Take_Profit_2", "Plan_Status",
    ]
    keep = [find_col(plans, name) for name in keep_aliases]
    keep = [c for c in keep if c]
    plans = plans[keep].copy().drop_duplicates(plan_symbol, keep="last")
    plans[plan_symbol] = plans[plan_symbol].astype(str).str.upper().str.strip().str.replace(".JK", "", regex=False)
    return signals.merge(plans, how="left", left_on=signal_symbol, right_on=plan_symbol, suffixes=("", "_Plan"))


def aggregate(detail: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    rows = []
    for decision, group in [("ALL", detail)] + list(detail.groupby("Gated_Decision")):
        record = {
            "Decision": decision,
            "Signals": len(group),
            "Trade_Eligible": int(group["Trade_Eligible"].fillna(False).sum()),
            "Symbols": group["Symbol"].nunique()
        }
        for h in horizons:
            ret_col = f"Return_{h}D_Pct"
            mfe_col = f"MFE_{h}D_Pct"
            mae_col = f"MAE_{h}D_Pct"
            if ret_col not in group.columns:
                record[f"Trades_{h}D"] = 0
                record[f"Avg_Return_{h}D_Pct"] = np.nan
                record[f"Median_Return_{h}D_Pct"] = np.nan
                record[f"Win_Rate_{h}D_Pct"] = np.nan
                record[f"Profit_Factor_{h}D"] = np.nan
                record[f"Avg_MFE_{h}D_Pct"] = np.nan
                record[f"Avg_MAE_{h}D_Pct"] = np.nan
                continue
            valid = pd.to_numeric(group[ret_col], errors="coerce").dropna()
            record[f"Trades_{h}D"] = len(valid)
            record[f"Avg_Return_{h}D_Pct"] = valid.mean()
            record[f"Median_Return_{h}D_Pct"] = valid.median()
            record[f"Win_Rate_{h}D_Pct"] = (valid > 0).mean() * 100 if len(valid) else np.nan
            record[f"Profit_Factor_{h}D"] = valid[valid > 0].sum() / abs(valid[valid < 0].sum()) if (valid < 0).any() else np.nan
            record[f"Avg_MFE_{h}D_Pct"] = pd.to_numeric(group[mfe_col], errors="coerce").mean() if mfe_col in group.columns else np.nan
            record[f"Avg_MAE_{h}D_Pct"] = pd.to_numeric(group[mae_col], errors="coerce").mean() if mae_col in group.columns else np.nan
        actionable = group[group["Gated_Decision"].isin(ENTRY_DECISIONS)]
        r_values = pd.to_numeric(actionable.get("Return_R_D7", pd.Series(dtype=float)), errors="coerce").dropna()
        record["Trigger_Rate"] = float((group["Gated_Decision"] == "BUY READY").sum() / max((group["Gated_Decision"].isin(["BUY READY", "BUY ON TRIGGER"])).sum(), 1))
        record["Expectancy_R"] = float(r_values.mean()) if len(r_values) else np.nan
        record["Average_MFE_R"] = pd.to_numeric(actionable.get("MFE_R_D7", pd.Series(dtype=float)), errors="coerce").mean()
        record["Average_MAE_R"] = pd.to_numeric(actionable.get("MAE_R_D7", pd.Series(dtype=float)), errors="coerce").mean()
        record["Stop_Rate"] = float(actionable.get("SL_Hit_D7", pd.Series(dtype=bool)).fillna(False).mean()) if len(actionable) else np.nan
        record["Target_1_Rate"] = float(actionable.get("TP1_Hit_D7", pd.Series(dtype=bool)).fillna(False).mean()) if len(actionable) else np.nan
        record["Target_2_Rate"] = float(actionable.get("TP2_Hit_D7", pd.Series(dtype=bool)).fillna(False).mean()) if len(actionable) else np.nan
        record["False_Positive"] = int(actionable.get("False_Positive", pd.Series(dtype=bool)).fillna(False).sum())
        record["False_Negative"] = int(group.get("False_Negative", pd.Series(dtype=bool)).fillna(False).sum())
        record["Average_Slippage_Pct"] = pd.to_numeric(actionable.get("Slippage_Pct", pd.Series(dtype=float)), errors="coerce").mean()
        record["Average_Holding_Period"] = pd.to_numeric(actionable.get("Holding_Period_Days", pd.Series(dtype=float)), errors="coerce").mean()
        rows.append(record)
    out = pd.DataFrame(rows)
    order = {"ALL": -1, **{d: i for i, d in enumerate(DECISION_ORDER)}}
    out["_o"] = out["Decision"].map(order).fillna(99)
    return out.sort_values("_o").drop(columns="_o")


def main():
    p = argparse.ArgumentParser(description="Stockbit SDE Backtesting Engine V1.2")
    p.add_argument("signals")
    p.add_argument("price_dir")
    p.add_argument("ihsg_csv")
    p.add_argument("--output-dir", default="backtest_output_v1_1")
    p.add_argument("--horizons", default="1,3,5,7,10,20")
    p.add_argument("--entry-mode", choices=["next_open", "same_close"], default="next_open")
    p.add_argument("--max-hold-days", type=int, default=20)
    p.add_argument("--include-suppressed", action="store_true")
    p.add_argument("--entry-plans", default="")
    args = p.parse_args()

    horizons = sorted({int(x) for x in args.horizons.split(",") if x.strip()})
    signals = load_signals(Path(args.signals))
    signals = attach_entry_plans(signals, Path(args.entry_plans) if args.entry_plans else None)
    regime = load_market_regime(Path(args.ihsg_csv))
    signals["Market_Regime"] = signals["Signal_Date"].map(lambda d: regime_asof(regime, d))
    signals["Gated_Decision"] = signals.apply(lambda r: apply_market_gate(r["Decision"], r["Market_Regime"]), axis=1)
    signals = suppress_repeated_entries(signals, args.max_hold_days)

    price_index = index_price_files(Path(args.price_dir))
    details = []
    missing = []

    for symbol, group in signals.groupby("Symbol"):
        path = price_index.get(symbol)
        if path is None:
            missing.append(symbol)
            for _, row in group.iterrows():
                details.append({**row.to_dict(), "Status": "PRICE_FILE_MISSING"})
            continue
        px = load_price(path)
        for _, row in group.iterrows():
            if not args.include_suppressed and not bool(row["Trade_Eligible"]) and row["Gated_Decision"] in ENTRY_DECISIONS:
                details.append({**row.to_dict(), "Status": "SUPPRESSED_ACTIVE_TRADE"})
                continue
            details.append(evaluate_signal(row, px, horizons, args.entry_mode, args.max_hold_days))

    detail = pd.DataFrame(details)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    detail.to_csv(output / "BACKTEST_DETAIL.csv", index=False)

    evaluated = detail[detail["Status"] == "OK"].copy()
    summary = aggregate(evaluated, horizons)
    summary.to_csv(output / "BACKTEST_SUMMARY.csv", index=False)
    outcome_cols = [
        "Symbol", "Signal_Date", "Decision", "Gated_Decision", "Signal_Score",
        "Entry_Date", "Entry_Price", "Stop_Loss", "Take_Profit_1", "Take_Profit_2",
        "Close_D1", "Close_D3", "Close_D5", "Close_D7",
        "Return_D1_Pct", "Return_D3_Pct", "Return_D5_Pct", "Return_D7_Pct",
        "Max_Price_D7", "Min_Price_D7", "MFE_D7_Pct", "MAE_D7_Pct",
        "TP1_Hit_D7", "TP2_Hit_D7", "SL_Hit_D7", "Final_Outcome_D7",
        "Initial_Risk_Pct", "Return_R_D7", "MFE_R_D7", "MAE_R_D7", "Slippage_Pct",
        "Holding_Period_Days", "False_Positive", "False_Negative", "Setup_Type",
        "Market_Regime", "Repeat_Status", "Status",
    ]
    existing_outcome_cols = [c for c in outcome_cols if c in detail.columns]
    detail[existing_outcome_cols].to_csv(output / "WATCHLIST_OUTCOMES.csv", index=False)

    agg_map = {"Signals": ("Symbol", "size"), "Symbols": ("Symbol", "nunique")}
    for h in [5, 10, 20]:
        col = f"Return_{h}D_Pct"
        if col in evaluated.columns:
            agg_map[f"Avg_Return_{h}D_Pct"] = (col, "mean")
    regime_summary = evaluated.groupby(["Market_Regime", "Gated_Decision"]).agg(**agg_map).reset_index()
    regime_summary.to_csv(output / "MARKET_REGIME_SUMMARY.csv", index=False)
    if "Setup_Type" in evaluated.columns:
        setup_summary = evaluated.groupby(["Setup_Type", "Gated_Decision"]).agg(**agg_map).reset_index()
        setup_summary.to_csv(output / "SETUP_SUMMARY.csv", index=False)

    repeats = detail[detail["Repeat_Status"].astype(str).str.contains("SUPPRESSED", na=False)]
    repeats.to_csv(output / "SUPPRESSED_REPEAT_SIGNALS.csv", index=False)
    pd.DataFrame({"Symbol": sorted(set(missing))}).to_csv(output / "MISSING_PRICE_SYMBOLS.csv", index=False)

    manifest = {
        "version": "1.6.2-stage2",
        "transaction_cost": "SLIPPAGE_FROM_SIGNAL_OR_ZERO",
        "horizons": horizons,
        "max_hold_days": args.max_hold_days,
        "decisions_tested": DECISION_ORDER,
        "repeat_policy": "One symbol = one active trade; repeated BUY READY/legacy entry suppressed until max hold.",
        "market_gate": {
            "BULLISH": "context only",
            "SIDEWAYS": "trigger confirmation handled before backtest",
            "BEARISH": "conditional sizing/trigger; no universal downgrade"
        },
        "entry_mode": args.entry_mode,
        "rows_input": len(signals),
        "rows_evaluated": len(evaluated),
        "suppressed_repeats": len(repeats),
        "missing_symbols": len(set(missing))
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Backtest Stage 2 moderate calibration selesai")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
