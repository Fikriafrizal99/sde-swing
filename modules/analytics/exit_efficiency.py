#!/usr/bin/env python3
from __future__ import annotations

"""Read-only Exit Efficiency analytics for SDE Swing.

The module never mutates ``signal_outcome_ledger`` or price history. It audits
existing lifecycle outcomes and measures how much of the observed excursion was
captured by the current exit plan. Daily OHLC cannot establish intraday order,
so strict and inclusive peak metrics are reported separately.
"""

import argparse
import math
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from modules.analytics.execution_integrity import (
        outcome_consistency,
        validate_plan_geometry,
    )
except ModuleNotFoundError:  # direct execution from modules/analytics
    import sys

    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from modules.analytics.execution_integrity import (
        outcome_consistency,
        validate_plan_geometry,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/output/analytics/performance"
CLOSED_OUTCOMES = {"WIN", "LOSS", "AMBIGUOUS"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(price: Any, base: Any) -> float | None:
    price_value = _number(price)
    base_value = _number(base)
    if price_value is None or base_value is None or base_value == 0:
        return None
    return (price_value / base_value - 1.0) * 100.0


def _median(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.median())


def _mean(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return None if values.empty else float(values.mean())


def _safe_rate(count: int, total: int) -> float | None:
    return None if total <= 0 else 100.0 * count / total


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    path = db_path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"SDE_DB_NOT_FOUND:{path}")
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _require_table(conn: sqlite3.Connection, name: str) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    if not exists:
        raise RuntimeError(f"SDE_TABLE_NOT_FOUND:{name}")


def load_source(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = _open_read_only(db_path)
    try:
        _require_table(conn, "signal_outcome_ledger")
        _require_table(conn, "market_prices_daily")
        ledger = pd.read_sql_query(
            """
            SELECT
                signal_id, run_id, symbol, signal_date, setup_type,
                data_quality_status, current_status, trigger_type,
                entry_date, entry_price, reference_price,
                stop_loss, take_profit_1, take_profit_2, max_hold_days,
                exit_date, exit_price, exit_reason, final_outcome,
                realized_return_pct, mfe_pct, mae_pct, tp1_hit, tp2_hit, sl_hit
            FROM signal_outcome_ledger
            ORDER BY signal_date, symbol, signal_id
            """,
            conn,
        )
        prices = pd.read_sql_query(
            """
            SELECT symbol, price_date, open, high, low, close, source
            FROM market_prices_daily
            ORDER BY symbol, price_date
            """,
            conn,
        )
    finally:
        conn.close()

    if not prices.empty:
        prices["price_date"] = pd.to_datetime(prices["price_date"], errors="coerce")
        for column in ("open", "high", "low", "close"):
            prices[column] = pd.to_numeric(prices[column], errors="coerce")
        prices = (
            prices.dropna(subset=["symbol", "price_date", "high", "low", "close"])
            .drop_duplicates(["symbol", "price_date"], keep="last")
            .sort_values(["symbol", "price_date"])
            .reset_index(drop=True)
        )
    return ledger, prices


def _peak_return(frame: pd.DataFrame, entry: float) -> float | None:
    if frame.empty:
        return None
    high = pd.to_numeric(frame["high"], errors="coerce").dropna()
    return None if high.empty else _pct(float(high.max()), entry)


def _first_touch_and_upside(
    maxhold: pd.DataFrame,
    *,
    entry_date: pd.Timestamp,
    entry_price: float,
    target: float | None,
) -> tuple[str, float | None]:
    if target is None or target <= entry_price or maxhold.empty:
        return "", None

    # Exclude the entry session. Daily OHLC cannot tell whether its high was
    # printed before or after the actual trigger.
    eligible = maxhold[maxhold["price_date"] > entry_date]
    touched = eligible[pd.to_numeric(eligible["high"], errors="coerce") >= target]
    if touched.empty:
        return "", None

    touch_date = pd.Timestamp(touched.iloc[0]["price_date"]).normalize()
    after = maxhold[maxhold["price_date"] > touch_date]
    if after.empty:
        return touch_date.date().isoformat(), None
    high_after = pd.to_numeric(after["high"], errors="coerce").dropna()
    if high_after.empty:
        return touch_date.date().isoformat(), None
    return touch_date.date().isoformat(), _pct(float(high_after.max()), target)


def _post_exit_metrics(
    prices: pd.DataFrame,
    *,
    exit_date: pd.Timestamp,
    entry_price: float,
    exit_price: float,
    sessions: int,
) -> tuple[int, bool, float | None, float | None]:
    future = prices[prices["price_date"] > exit_date].head(sessions)
    observed = int(len(future))
    full = observed == sessions
    if not full:
        return observed, False, None, None
    high = pd.to_numeric(future["high"], errors="coerce").dropna()
    if high.empty:
        return observed, True, None, None
    peak = float(high.max())
    return observed, True, _pct(peak, entry_price), _pct(peak, exit_price)


def build_trade_frame(ledger: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame()

    by_symbol = {
        str(symbol).upper(): group.sort_values("price_date").reset_index(drop=True)
        for symbol, group in prices.groupby(prices["symbol"].astype(str).str.upper())
    } if not prices.empty else {}

    output: list[dict[str, Any]] = []
    for _, row in ledger.iterrows():
        entry = _number(row.get("entry_price"))
        if entry is None or entry <= 0:
            continue

        stop = _number(row.get("stop_loss"))
        tp1 = _number(row.get("take_profit_1"))
        tp2 = _number(row.get("take_profit_2"))
        geometry = validate_plan_geometry(entry, stop, tp1, tp2)
        reported = str(row.get("final_outcome") or "").strip().upper()
        realized = _number(row.get("realized_return_pct"))
        consistency = outcome_consistency(reported, realized)
        closed = reported in CLOSED_OUTCOMES

        entry_date_raw = pd.to_datetime(row.get("entry_date"), errors="coerce")
        exit_date_raw = pd.to_datetime(row.get("exit_date"), errors="coerce")
        symbol_prices = by_symbol.get(str(row.get("symbol") or "").upper(), pd.DataFrame())

        strict_peak = None
        through_exit_peak = None
        maxhold_peak = None
        tp1_touch_date = ""
        tp2_touch_date = ""
        upside_after_tp1 = None
        upside_after_tp2 = None
        post3_obs = 0
        post3_full = False
        post3_entry = None
        post3_cont = None
        post5_obs = 0
        post5_full = False
        post5_entry = None
        post5_cont = None

        if not pd.isna(entry_date_raw) and not symbol_prices.empty:
            entry_date = pd.Timestamp(entry_date_raw).normalize()
            max_hold = max(int(_number(row.get("max_hold_days")) or 20), 1)
            maxhold = symbol_prices[symbol_prices["price_date"] >= entry_date].head(max_hold)
            maxhold_peak = _peak_return(maxhold, entry)

            tp1_touch_date, upside_after_tp1 = _first_touch_and_upside(
                maxhold, entry_date=entry_date, entry_price=entry, target=tp1,
            )
            tp2_touch_date, upside_after_tp2 = _first_touch_and_upside(
                maxhold, entry_date=entry_date, entry_price=entry, target=tp2,
            )

            if closed and not pd.isna(exit_date_raw):
                exit_date = pd.Timestamp(exit_date_raw).normalize()
                strict = symbol_prices[
                    (symbol_prices["price_date"] > entry_date)
                    & (symbol_prices["price_date"] < exit_date)
                ]
                through = symbol_prices[
                    (symbol_prices["price_date"] >= entry_date)
                    & (symbol_prices["price_date"] <= exit_date)
                ]
                strict_peak = _peak_return(strict, entry)
                through_exit_peak = _peak_return(through, entry)
                exit_price = _number(row.get("exit_price"))
                if exit_price is not None and exit_price > 0:
                    post3_obs, post3_full, post3_entry, post3_cont = _post_exit_metrics(
                        symbol_prices,
                        exit_date=exit_date,
                        entry_price=entry,
                        exit_price=exit_price,
                        sessions=3,
                    )
                    post5_obs, post5_full, post5_entry, post5_cont = _post_exit_metrics(
                        symbol_prices,
                        exit_date=exit_date,
                        entry_price=entry,
                        exit_price=exit_price,
                        sessions=5,
                    )

        risk_distance = (entry - stop) / entry * 100.0 if stop is not None else None
        tp1_distance = _pct(tp1, entry)
        tp2_distance = _pct(tp2, entry)

        exit_efficiency = None
        profit_left = None
        clean_closed = closed and geometry.valid and consistency == "CONSISTENT"
        if clean_closed and through_exit_peak is not None and realized is not None:
            profit_left = through_exit_peak - realized
            if realized > 0 and through_exit_peak > 0:
                exit_efficiency = realized / through_exit_peak * 100.0

        output.append({
            "Signal_ID": row.get("signal_id"),
            "Symbol": row.get("symbol"),
            "Signal_Date": row.get("signal_date"),
            "Setup_Type": row.get("setup_type"),
            "Current_Status": row.get("current_status"),
            "Entry_Date": row.get("entry_date"),
            "Entry_Price": entry,
            "Stop_Loss": stop,
            "TP1": tp1,
            "TP2": tp2,
            "Exit_Date": row.get("exit_date"),
            "Exit_Price": _number(row.get("exit_price")),
            "Exit_Reason": row.get("exit_reason"),
            "Reported_Outcome": reported,
            "Realized_Return_Pct": realized,
            "Plan_Geometry_Status": geometry.status,
            "Plan_Geometry_Valid": bool(geometry.valid),
            "Outcome_Consistency": consistency,
            "Clean_Closed": bool(clean_closed),
            "Risk_Distance_Pct": risk_distance,
            "TP1_Distance_Pct": tp1_distance,
            "TP2_Distance_Pct": tp2_distance,
            "Strict_Peak_Before_Exit_Pct": strict_peak,
            "Peak_Through_Exit_Day_Pct": through_exit_peak,
            "Peak_During_MaxHold_Pct": maxhold_peak,
            "Exit_Efficiency_Pct": exit_efficiency,
            "Profit_Left_On_Table_PctPts": profit_left,
            "TP1_First_Touch_Date_Strict": tp1_touch_date,
            "Upside_After_TP1_Pct": upside_after_tp1,
            "TP2_First_Touch_Date_Strict": tp2_touch_date,
            "Upside_After_TP2_Pct": upside_after_tp2,
            "Post_Exit_3D_Sessions": post3_obs,
            "Post_Exit_3D_Full": bool(post3_full),
            "Post_Exit_Peak_3D_vs_Entry_Pct": post3_entry,
            "Post_Exit_Continuation_3D_Pct": post3_cont,
            "Post_Exit_5D_Sessions": post5_obs,
            "Post_Exit_5D_Full": bool(post5_full),
            "Post_Exit_Peak_5D_vs_Entry_Pct": post5_entry,
            "Post_Exit_Continuation_5D_Pct": post5_cont,
        })
    return pd.DataFrame(output)


def build_summary(trades: pd.DataFrame, prices: pd.DataFrame) -> dict[str, Any]:
    data_through = "-"
    if not prices.empty:
        maximum = pd.to_datetime(prices["price_date"], errors="coerce").max()
        if not pd.isna(maximum):
            data_through = pd.Timestamp(maximum).date().isoformat()

    if trades.empty:
        return {
            "Data_Through": data_through,
            "Triggered": 0,
            "Geometry_Valid": 0,
            "Geometry_Invalid": 0,
            "Closed": 0,
            "Clean_Closed": 0,
            "Outcome_Inconsistencies": 0,
            "Sample_Status": "INSUFFICIENT",
        }

    geometry_valid = trades["Plan_Geometry_Valid"].fillna(False).astype(bool)
    closed = trades["Reported_Outcome"].isin(CLOSED_OUTCOMES)
    clean_closed = trades["Clean_Closed"].fillna(False).astype(bool)
    clean_trades = trades[geometry_valid]
    closed_clean = trades[clean_closed]

    inconsistency = closed & trades["Outcome_Consistency"].ne("CONSISTENT")
    post3 = closed_clean[closed_clean["Post_Exit_3D_Full"].fillna(False).astype(bool)]
    post5 = closed_clean[closed_clean["Post_Exit_5D_Full"].fillna(False).astype(bool)]

    return {
        "Data_Through": data_through,
        "Triggered": int(len(trades)),
        "Geometry_Valid": int(geometry_valid.sum()),
        "Geometry_Invalid": int((~geometry_valid).sum()),
        "Closed": int(closed.sum()),
        "Clean_Closed": int(clean_closed.sum()),
        "Outcome_Inconsistencies": int(inconsistency.sum()),
        "Median_Risk_Distance_Pct": _median(clean_trades["Risk_Distance_Pct"]),
        "Median_TP1_Distance_Pct": _median(clean_trades["TP1_Distance_Pct"]),
        "Median_TP2_Distance_Pct": _median(clean_trades["TP2_Distance_Pct"]),
        "TP2_LE_5_Pct": _safe_rate(
            int(pd.to_numeric(clean_trades["TP2_Distance_Pct"], errors="coerce").le(5).sum()),
            int(pd.to_numeric(clean_trades["TP2_Distance_Pct"], errors="coerce").notna().sum()),
        ),
        "Median_Realized_Return_Pct": _median(closed_clean["Realized_Return_Pct"]),
        "Mean_Realized_Return_Pct": _mean(closed_clean["Realized_Return_Pct"]),
        "Median_Peak_Through_Exit_Day_Pct": _median(closed_clean["Peak_Through_Exit_Day_Pct"]),
        "Mean_Peak_Through_Exit_Day_Pct": _mean(closed_clean["Peak_Through_Exit_Day_Pct"]),
        "Median_Exit_Efficiency_Pct": _median(closed_clean["Exit_Efficiency_Pct"]),
        "Mean_Exit_Efficiency_Pct": _mean(closed_clean["Exit_Efficiency_Pct"]),
        "Median_Profit_Left_On_Table_PctPts": _median(closed_clean["Profit_Left_On_Table_PctPts"]),
        "Mean_Profit_Left_On_Table_PctPts": _mean(closed_clean["Profit_Left_On_Table_PctPts"]),
        "Post_Exit_3D_Full_Eligible": int(len(post3)),
        "Post_Exit_3D_Continuation_GT3_Pct": _safe_rate(
            int(pd.to_numeric(post3["Post_Exit_Continuation_3D_Pct"], errors="coerce").gt(3).sum()),
            int(pd.to_numeric(post3["Post_Exit_Continuation_3D_Pct"], errors="coerce").notna().sum()),
        ),
        "Post_Exit_3D_Continuation_GT5_Pct": _safe_rate(
            int(pd.to_numeric(post3["Post_Exit_Continuation_3D_Pct"], errors="coerce").gt(5).sum()),
            int(pd.to_numeric(post3["Post_Exit_Continuation_3D_Pct"], errors="coerce").notna().sum()),
        ),
        "Post_Exit_5D_Full_Eligible": int(len(post5)),
        "Post_Exit_5D_Continuation_GT3_Pct": _safe_rate(
            int(pd.to_numeric(post5["Post_Exit_Continuation_5D_Pct"], errors="coerce").gt(3).sum()),
            int(pd.to_numeric(post5["Post_Exit_Continuation_5D_Pct"], errors="coerce").notna().sum()),
        ),
        "Post_Exit_5D_Continuation_GT5_Pct": _safe_rate(
            int(pd.to_numeric(post5["Post_Exit_Continuation_5D_Pct"], errors="coerce").gt(5).sum()),
            int(pd.to_numeric(post5["Post_Exit_Continuation_5D_Pct"], errors="coerce").notna().sum()),
        ),
        "Sample_Status": (
            "INSUFFICIENT" if int(clean_closed.sum()) < 20
            else "EARLY" if int(clean_closed.sum()) < 50
            else "REPRESENTATIVE"
        ),
    }


def build_by_setup(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for setup, group in trades.groupby("Setup_Type", dropna=False):
        valid = group[group["Plan_Geometry_Valid"].fillna(False).astype(bool)]
        clean_closed = group[group["Clean_Closed"].fillna(False).astype(bool)]
        rows.append({
            "Setup_Type": str(setup),
            "Triggered": int(len(group)),
            "Geometry_Valid": int(len(valid)),
            "Geometry_Invalid": int(len(group) - len(valid)),
            "Clean_Closed": int(len(clean_closed)),
            "Median_Risk_Distance_Pct": _median(valid["Risk_Distance_Pct"]),
            "Median_TP1_Distance_Pct": _median(valid["TP1_Distance_Pct"]),
            "Median_TP2_Distance_Pct": _median(valid["TP2_Distance_Pct"]),
            "Mean_Realized_Return_Pct": _mean(clean_closed["Realized_Return_Pct"]),
            "Median_Profit_Left_On_Table_PctPts": _median(
                clean_closed["Profit_Left_On_Table_PctPts"]
            ),
        })
    return pd.DataFrame(rows).sort_values(["Triggered", "Setup_Type"], ascending=[False, True])


def run_analysis(
    db_path: Path,
    output_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    ledger, prices = load_source(db_path)
    trades = build_trade_frame(ledger, prices)
    summary = build_summary(trades, prices)
    by_setup = build_by_setup(trades)

    output_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output_dir / "EXIT_EFFICIENCY_TRADES.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([summary]).to_csv(
        output_dir / "EXIT_EFFICIENCY_SUMMARY.csv", index=False, encoding="utf-8-sig"
    )
    by_setup.to_csv(
        output_dir / "EXIT_EFFICIENCY_BY_SETUP.csv", index=False, encoding="utf-8-sig"
    )
    integrity = trades[
        (~trades["Plan_Geometry_Valid"].fillna(False).astype(bool))
        | (
            trades["Reported_Outcome"].isin(CLOSED_OUTCOMES)
            & trades["Outcome_Consistency"].ne("CONSISTENT")
        )
    ].copy() if not trades.empty else pd.DataFrame()
    integrity.to_csv(
        output_dir / "EXIT_EFFICIENCY_INTEGRITY.csv", index=False, encoding="utf-8-sig"
    )
    return trades, by_setup, summary


def _fmt(value: Any, suffix: str = "") -> str:
    number = _number(value)
    return "-" if number is None else f"{number:.2f}{suffix}"


def print_report(
    trades: pd.DataFrame,
    by_setup: pd.DataFrame,
    summary: dict[str, Any],
    output_dir: Path,
) -> None:
    print("\nEXIT EFFICIENCY")
    print("=" * 76)
    print(f"Data through        : {summary.get('Data_Through', '-')}")
    print(f"Triggered           : {summary.get('Triggered', 0)}")
    print(
        f"Geometry valid/bad  : {summary.get('Geometry_Valid', 0)} / "
        f"{summary.get('Geometry_Invalid', 0)}"
    )
    print(
        f"Closed / clean      : {summary.get('Closed', 0)} / "
        f"{summary.get('Clean_Closed', 0)}"
    )
    print(f"Outcome conflicts   : {summary.get('Outcome_Inconsistencies', 0)}")
    print("-" * 76)
    print(f"Median Risk         : {_fmt(summary.get('Median_Risk_Distance_Pct'), '%')}")
    print(f"Median TP1 distance : {_fmt(summary.get('Median_TP1_Distance_Pct'), '%')}")
    print(f"Median TP2 distance : {_fmt(summary.get('Median_TP2_Distance_Pct'), '%')}")
    print(f"TP2 <= 5%           : {_fmt(summary.get('TP2_LE_5_Pct'), '%')}")
    print(f"Median realized     : {_fmt(summary.get('Median_Realized_Return_Pct'), '%')}")
    print(
        f"Median peak exitday : "
        f"{_fmt(summary.get('Median_Peak_Through_Exit_Day_Pct'), '%')}"
    )
    print(f"Median capture      : {_fmt(summary.get('Median_Exit_Efficiency_Pct'), '%')}")
    print(
        f"Median profit left  : "
        f"{_fmt(summary.get('Median_Profit_Left_On_Table_PctPts'), ' pp')}"
    )
    print("-" * 76)
    print(
        f"Post-exit full 3D   : {summary.get('Post_Exit_3D_Full_Eligible', 0)} closed trades"
    )
    print(
        f"Post-exit full 5D   : {summary.get('Post_Exit_5D_Full_Eligible', 0)} closed trades"
    )
    print(f"Sample status       : {summary.get('Sample_Status', 'INSUFFICIENT')}")
    print("\nDaily OHLC note: Peak Through Exit Day has intraday-order ambiguity.")
    print("Strict_Peak_Before_Exit excludes entry and exit sessions.")
    print("No TP/SL formula is changed by this report.")
    print(f"\nOutput: {output_dir.resolve()}")

    if not trades.empty:
        issues = trades[
            (~trades["Plan_Geometry_Valid"].fillna(False).astype(bool))
            | (
                trades["Reported_Outcome"].isin(CLOSED_OUTCOMES)
                & trades["Outcome_Consistency"].ne("CONSISTENT")
            )
        ]
        if not issues.empty:
            print("\nDATA INTEGRITY FLAGS")
            columns = [
                "Signal_Date", "Symbol", "Entry_Price", "TP1", "TP2",
                "Reported_Outcome", "Realized_Return_Pct",
                "Plan_Geometry_Status", "Outcome_Consistency",
            ]
            print(issues[columns].to_string(index=False, na_rep="-"))


def main() -> int:
    parser = argparse.ArgumentParser(description="SDE Swing read-only Exit Efficiency analytics")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    trades, by_setup, summary = run_analysis(args.db, args.output_dir)
    print_report(trades, by_setup, summary, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
