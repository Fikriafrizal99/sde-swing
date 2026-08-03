#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import find_col, normalize_symbol

try:
    import requests
except ImportError:  # live Telegram only
    requests = None

ACTIVE_STATUSES = {"WAITING_TRIGGER", "OPEN"}
FINAL_OUTCOMES = {"WIN", "LOSS", "AMBIGUOUS"}
TRACKED_DECISIONS = {"STRONG BUY", "BUY", "BUY CANDIDATE", "BUY READY", "BUY ON TRIGGER", "BUY CONFIRMED"}
CURRENT_RECOMMENDATION_DECISIONS = set(TRACKED_DECISIONS)
VALID_SIGNAL_QUALITY = {"VALID", "PARTIAL_COVERAGE", "SUCCESS_WITH_WARNING", "VALID_WITH_REFRESH_FALLBACK"}
DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
DEFAULT_HISTORICAL = PROJECT_ROOT / "data/output/historical/by_symbol"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/output/analytics/performance"
DEFAULT_DECISIONS = PROJECT_ROOT / "data/output/decision/FINAL_DECISION_V3.csv"
DEFAULT_PLANS = PROJECT_ROOT / "data/output/exit/ENTRY_PLANS.csv"


LEDGER_COLUMNS = [
    "signal_id", "run_id", "symbol", "signal_date", "last_seen_date",
    "signal_type", "raw_decision", "setup_type", "score",
    "technical_quality", "entry_readiness", "broker_confidence",
    "broker_confidence_bucket", "broker_direction", "market_regime",
    "data_quality_status", "plan_status", "trigger_type", "trigger_price",
    "trigger_expiry_days", "entry_zone_low", "entry_zone_high",
    "reference_price", "stop_loss", "take_profit_1", "take_profit_2",
    "max_hold_days", "current_status", "trigger_date", "entry_date",
    "entry_price", "exit_date", "exit_price", "exit_reason", "holding_days",
    "close_d1", "close_d3", "close_d5", "close_d7", "return_d1",
    "return_d3", "return_d5", "return_d7", "max_price", "min_price",
    "mfe_pct", "mae_pct", "tp1_hit", "tp2_hit", "sl_hit",
    "final_outcome", "realized_return_pct", "created_at", "updated_at",
    "source_json",
]


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS signal_outcome_ledger (
    signal_id TEXT PRIMARY KEY,
    run_id TEXT,
    symbol TEXT NOT NULL,
    signal_date TEXT NOT NULL,
    last_seen_date TEXT,
    signal_type TEXT,
    raw_decision TEXT,
    setup_type TEXT,
    score REAL,
    technical_quality REAL,
    entry_readiness REAL,
    broker_confidence REAL,
    broker_confidence_bucket TEXT,
    broker_direction TEXT,
    market_regime TEXT,
    data_quality_status TEXT,
    plan_status TEXT,
    trigger_type TEXT,
    trigger_price REAL,
    trigger_expiry_days INTEGER,
    entry_zone_low REAL,
    entry_zone_high REAL,
    reference_price REAL,
    stop_loss REAL,
    take_profit_1 REAL,
    take_profit_2 REAL,
    max_hold_days INTEGER,
    current_status TEXT,
    trigger_date TEXT,
    entry_date TEXT,
    entry_price REAL,
    exit_date TEXT,
    exit_price REAL,
    exit_reason TEXT,
    holding_days INTEGER,
    close_d1 REAL,
    close_d3 REAL,
    close_d5 REAL,
    close_d7 REAL,
    return_d1 REAL,
    return_d3 REAL,
    return_d5 REAL,
    return_d7 REAL,
    max_price REAL,
    min_price REAL,
    mfe_pct REAL,
    mae_pct REAL,
    tp1_hit INTEGER DEFAULT 0,
    tp2_hit INTEGER DEFAULT 0,
    sl_hit INTEGER DEFAULT 0,
    final_outcome TEXT,
    realized_return_pct REAL,
    created_at TEXT,
    updated_at TEXT,
    source_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_signal_ledger_symbol_status
    ON signal_outcome_ledger(symbol, current_status);
CREATE INDEX IF NOT EXISTS idx_signal_ledger_signal_date
    ON signal_outcome_ledger(signal_date);
"""


@dataclass
class RegisterResult:
    inserted: int = 0
    updated_active: int = 0
    skipped: int = 0


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm_text(value: Any, default: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return default
    text = str(value).strip()
    return default if text.lower() in {"", "nan", "none", "null"} else text


def as_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip().lower() in {"", "nan", "none", "null"}:
            return None
        number = float(str(value).replace(",", ""))
        return number if math.isfinite(number) else None
    except Exception:
        return None


def as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def parse_date(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.date().isoformat()


def row_value(row: pd.Series | dict[str, Any], *aliases: str, default: Any = None) -> Any:
    if isinstance(row, pd.Series):
        mapping = {str(c).strip().lower().replace(" ", "_"): c for c in row.index}
        for alias in aliases:
            key = alias.strip().lower().replace(" ", "_")
            col = mapping.get(key)
            if col is not None and pd.notna(row[col]):
                return row[col]
        return default
    lowered = {str(k).strip().lower().replace(" ", "_"): v for k, v in row.items()}
    for alias in aliases:
        value = lowered.get(alias.strip().lower().replace(" ", "_"))
        if value is not None:
            return value
    return default


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def normalize_setup(value: Any) -> str:
    setup = norm_text(value, "DEVELOPING").upper().replace("_", " ")
    known = {
        "BREAKOUT", "PULLBACK", "TREND CONTINUATION", "DEVELOPING",
        "BASE BREAKOUT", "REVERSAL", "REVERSAL EARLY", "RANGE BREAKOUT",
    }
    return setup if setup in known else "LEGACY UNKNOWN"


def confidence_bucket(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value < 60:
        return "<60%"
    if value < 75:
        return "60-74%"
    return ">=75%"


def plan_map(plans: pd.DataFrame) -> dict[str, pd.Series]:
    if plans.empty:
        return {}
    symbol_col = find_col(plans, "Symbol", "Ticker", "EMITEN")
    if not symbol_col:
        return {}
    result: dict[str, pd.Series] = {}
    for _, row in plans.iterrows():
        symbol = normalize_symbol(row.get(symbol_col))
        if symbol:
            result[symbol] = row
    return result


def classify_signal(raw_decision: str, plan_status: str) -> str:
    if raw_decision in {"STRONG BUY", "BUY"} and plan_status == "ACCEPT":
        return "BUY CONFIRMED"
    return "BUY CANDIDATE"


def trigger_spec(plan: pd.Series, setup_type: str) -> tuple[str, float | None]:
    status = norm_text(row_value(plan, "Plan_Status"), "").upper()
    reason = norm_text(row_value(plan, "Rejection_Reason"), "").upper()
    if status == "CONDITIONAL" and reason == "MINOR_RESISTANCE_NEAR":
        trigger = as_float(row_value(plan, "Minor_Resistance", "Nearest_Resistance"))
        return ("CLOSE_ABOVE", trigger) if trigger is not None else ("INVALID", None)
    if status in {"ACCEPT", "CONDITIONAL"}:
        low = as_float(row_value(plan, "Entry_Zone_Low"))
        high = as_float(row_value(plan, "Entry_Zone_High"))
        if low is not None and high is not None and low > 0 and high >= low:
            return "ENTRY_ZONE_TOUCH", None
        if setup_type in {"BREAKOUT", "TREND CONTINUATION"}:
            trigger = as_float(row_value(plan, "Minor_Resistance", "Nearest_Resistance"))
            return ("CLOSE_ABOVE", trigger) if trigger is not None else ("INVALID", None)
    # REJECT/MISSING plans are kept in the ledger for audit, but are never
    # treated as executed recommendations until a later run upgrades them.
    return "INVALID", None


def build_signal_record(
    decision_row: pd.Series | dict[str, Any],
    plan: pd.Series | dict[str, Any],
    run_id: str,
    default_signal_date: str = "",
    trigger_expiry_days: int = 7,
) -> dict[str, Any] | None:
    raw_decision = norm_text(row_value(decision_row, "Decision_V3", "Decision"), "").upper()
    if raw_decision not in TRACKED_DECISIONS:
        return None
    symbol = normalize_symbol(row_value(decision_row, "Symbol", "Ticker", "EMITEN"))
    if not symbol:
        return None
    signal_date = parse_date(row_value(
        decision_row,
        "Technical_Data_Date", "Latest_Valid_Candle_Date", "Date", "Signal_Date",
        default=default_signal_date,
    )) or parse_date(default_signal_date)
    if not signal_date:
        return None
    plan_status = norm_text(row_value(plan, "Plan_Status", "Entry_Status"), "MISSING").upper()
    setup_type = normalize_setup(
        row_value(plan, "Setup_Type", default=row_value(decision_row, "Setup_Type", "Setup_Label", default="DEVELOPING"))
    )
    trigger_type, trigger_price = trigger_spec(pd.Series(plan), setup_type)
    signal_type = classify_signal(raw_decision, plan_status)
    broker_conf = as_float(row_value(decision_row, "Broker_Confidence_Final", "Broker_Confidence"))
    reference_price = as_float(row_value(plan, "Reference_Close", "Entry_Reference_Price"))
    if reference_price is None:
        reference_price = as_float(row_value(decision_row, "Close", "Current_Price"))
    source = {
        "decision": dict(decision_row) if not isinstance(decision_row, pd.Series) else decision_row.where(pd.notna(decision_row), None).to_dict(),
        "plan": dict(plan) if not isinstance(plan, pd.Series) else plan.where(pd.notna(plan), None).to_dict(),
    }
    entry_zone_low = as_float(row_value(plan, "Entry_Zone_Low"))
    entry_zone_high = as_float(row_value(plan, "Entry_Zone_High"))
    key = "|".join([
        symbol,
        signal_date,
        signal_type,
        setup_type,
        trigger_type,
        str(trigger_price or ""),
        str(entry_zone_low or ""),
        str(entry_zone_high or ""),
    ])
    signal_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    current_status = "WAITING_TRIGGER" if trigger_type != "INVALID" else "INVALID_DATA"
    return {
        "signal_id": signal_id,
        "run_id": run_id,
        "symbol": symbol,
        "signal_date": signal_date,
        "last_seen_date": signal_date,
        "signal_type": signal_type,
        "raw_decision": raw_decision,
        "setup_type": setup_type,
        "score": as_float(row_value(decision_row, "Final_Score_V3", "Final_Score", "Score")),
        "technical_quality": as_float(row_value(decision_row, "Technical_Quality_Score_Final", "Technical_Quality_Score", "Technical_Score_Final", "Technical_Score")),
        "entry_readiness": as_float(row_value(plan, "Entry_Readiness_Final", "Entry_Readiness_PreScore", default=row_value(decision_row, "Entry_Readiness_PreScore_Final", "Entry_Readiness_PreScore"))),
        "broker_confidence": broker_conf,
        "broker_confidence_bucket": confidence_bucket(broker_conf),
        "broker_direction": norm_text(row_value(decision_row, "Broker_Direction_Final", "Broker_Direction", "Broker_Confirmation"), "UNKNOWN").upper(),
        "market_regime": norm_text(row_value(decision_row, "Market_Regime"), "UNKNOWN").upper(),
        "data_quality_status": norm_text(row_value(decision_row, "Data_Quality_Status"), "UNKNOWN").upper(),
        "plan_status": plan_status,
        "trigger_type": trigger_type,
        "trigger_price": trigger_price,
        "trigger_expiry_days": trigger_expiry_days,
        "entry_zone_low": entry_zone_low,
        "entry_zone_high": entry_zone_high,
        "reference_price": reference_price,
        "stop_loss": as_float(row_value(plan, "Initial_Stop", "Stop_Loss")),
        "take_profit_1": as_float(row_value(plan, "Target_1", "Take_Profit_1")),
        "take_profit_2": as_float(row_value(plan, "Target_2", "Take_Profit_2")),
        "max_hold_days": as_int(row_value(plan, "Max_Hold_Days"), 20),
        "current_status": current_status,
        "trigger_date": None,
        "entry_date": None,
        "entry_price": None,
        "exit_date": None,
        "exit_price": None,
        "exit_reason": "MISSING_TRIGGER_DATA" if trigger_type == "INVALID" else None,
        "holding_days": None,
        "close_d1": None,
        "close_d3": None,
        "close_d5": None,
        "close_d7": None,
        "return_d1": None,
        "return_d3": None,
        "return_d5": None,
        "return_d7": None,
        "max_price": None,
        "min_price": None,
        "mfe_pct": None,
        "mae_pct": None,
        "tp1_hit": 0,
        "tp2_hit": 0,
        "sl_hit": 0,
        "final_outcome": None,
        "realized_return_pct": None,
        "created_at": now_text(),
        "updated_at": now_text(),
        "source_json": json.dumps(source, ensure_ascii=False, default=str),
    }


def upsert_signal(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    exact = conn.execute(
        "SELECT signal_id FROM signal_outcome_ledger WHERE signal_id=?",
        (record["signal_id"],),
    ).fetchone()
    if exact:
        return "SKIPPED"
    active = conn.execute(
        """
        SELECT * FROM signal_outcome_ledger
        WHERE symbol=? AND current_status IN ('WAITING_TRIGGER','OPEN')
        ORDER BY signal_date ASC LIMIT 1
        """,
        (record["symbol"],),
    ).fetchone()
    if active:
        updates: dict[str, Any] = {
            "last_seen_date": max(norm_text(active["last_seen_date"]), record["signal_date"]),
            "updated_at": now_text(),
            "score": record["score"],
            "technical_quality": record["technical_quality"],
            "entry_readiness": record["entry_readiness"],
            "broker_confidence": record["broker_confidence"],
            "broker_confidence_bucket": record["broker_confidence_bucket"],
            "broker_direction": record["broker_direction"],
            "market_regime": record["market_regime"],
        }
        # A live candidate may be upgraded to confirmed before it triggers. Keep
        # the original signal date, but adopt the newly validated plan.
        if active["current_status"] == "WAITING_TRIGGER" and record["signal_type"] == "BUY CONFIRMED":
            for name in [
                "signal_type", "raw_decision", "plan_status", "trigger_type", "trigger_price",
                "entry_zone_low", "entry_zone_high", "reference_price", "stop_loss",
                "take_profit_1", "take_profit_2", "max_hold_days", "source_json",
            ]:
                updates[name] = record[name]
        assignments = ",".join(f"{key}=?" for key in updates)
        conn.execute(
            f"UPDATE signal_outcome_ledger SET {assignments} WHERE signal_id=?",
            [*updates.values(), active["signal_id"]],
        )
        return "UPDATED_ACTIVE"
    columns = LEDGER_COLUMNS
    placeholders = ",".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO signal_outcome_ledger ({','.join(columns)}) VALUES ({placeholders})",
        [record.get(col) for col in columns],
    )
    return "INSERTED" if cursor.rowcount > 0 else "SKIPPED"


def register_decision_file(
    conn: sqlite3.Connection,
    decision_path: Path,
    plans_path: Path,
    run_id: str,
    default_signal_date: str = "",
    trigger_expiry_days: int = 7,
) -> RegisterResult:
    decisions = read_csv(decision_path)
    plans = read_csv(plans_path)
    result = RegisterResult()
    if decisions.empty:
        return result
    plan_lookup = plan_map(plans)
    symbol_col = find_col(decisions, "Symbol", "Ticker", "EMITEN")
    decision_col = find_col(decisions, "Decision_V3", "Decision")
    if not symbol_col or not decision_col:
        return result

    current_state: dict[str, tuple[str, dict[str, Any] | None]] = {}
    records: list[dict[str, Any]] = []
    for _, row in decisions.iterrows():
        symbol = normalize_symbol(row.get(symbol_col))
        raw_decision = norm_text(row.get(decision_col), "").upper()
        record = build_signal_record(
            row,
            plan_lookup.get(symbol, pd.Series(dtype=object)),
            run_id,
            default_signal_date,
            trigger_expiry_days,
        )
        current_state[symbol] = (raw_decision, record)
        if record is not None:
            records.append(record)

    # A recommendation that is explicitly downgraded before its trigger is not
    # a LOSS. It is cancelled and excluded from win-rate calculations.
    active_waiting = conn.execute(
        "SELECT signal_id,symbol FROM signal_outcome_ledger WHERE current_status='WAITING_TRIGGER'"
    ).fetchall()
    for active in active_waiting:
        state = current_state.get(active["symbol"])
        if state is None:
            continue
        raw_decision, current_record = state
        invalidated = raw_decision not in TRACKED_DECISIONS or (
            current_record is not None and current_record.get("current_status") == "INVALID_DATA"
        )
        if invalidated:
            conn.execute(
                """
                UPDATE signal_outcome_ledger
                SET current_status='CANCELLED', final_outcome='CANCELLED',
                    exit_reason='SIGNAL_DOWNGRADED_BEFORE_TRIGGER', updated_at=?
                WHERE signal_id=?
                """,
                (now_text(), active["signal_id"]),
            )

    for record in records:
        action = upsert_signal(conn, record)
        if action == "INSERTED":
            result.inserted += 1
        elif action == "UPDATED_ACTIVE":
            result.updated_active += 1
        else:
            result.skipped += 1
    conn.commit()
    return result


def bootstrap_from_legacy_db(conn: sqlite3.Connection, trigger_expiry_days: int = 7) -> RegisterResult:
    result = RegisterResult()
    try:
        rows = conn.execute(
            """
            SELECT w.run_id,w.symbol,w.signal_date,w.decision,w.final_score,w.data_quality_status,w.row_json,
                   e.entry,e.stop_loss,e.take_profit_1,e.take_profit_2,e.entry_status,e.row_json AS plan_json
            FROM watchlist_history w
            LEFT JOIN entry_exit_results e ON e.run_id=w.run_id AND e.symbol=w.symbol
            WHERE w.lifecycle='NEW'
              AND UPPER(w.decision) IN ('STRONG BUY','BUY','BUY CANDIDATE')
            ORDER BY w.signal_date,w.run_id,w.symbol
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return result
    for raw in rows:
        try:
            decision = json.loads(raw["row_json"] or "{}")
        except Exception:
            decision = {}
        decision.update({
            "Symbol": raw["symbol"],
            "Signal_Date": raw["signal_date"],
            "Decision_V3": raw["decision"],
            "Final_Score_V3": raw["final_score"],
            "Data_Quality_Status": raw["data_quality_status"],
        })
        try:
            plan = json.loads(raw["plan_json"] or "{}")
        except Exception:
            plan = {}
        if not plan:
            plan = {
                "Plan_Status": raw["entry_status"],
                "Reference_Close": raw["entry"],
                "Entry_Zone_Low": raw["entry"],
                "Entry_Zone_High": raw["entry"],
                "Initial_Stop": raw["stop_loss"],
                "Target_1": raw["take_profit_1"],
                "Target_2": raw["take_profit_2"],
                "Max_Hold_Days": 20,
            }
        record = build_signal_record(
            decision,
            plan,
            raw["run_id"],
            raw["signal_date"],
            trigger_expiry_days,
        )
        if record is None:
            result.skipped += 1
            continue
        action = upsert_signal(conn, record)
        if action == "INSERTED":
            result.inserted += 1
        elif action == "UPDATED_ACTIVE":
            result.updated_active += 1
        else:
            result.skipped += 1
    conn.commit()
    return result


def load_prices(path: Path) -> pd.DataFrame:
    frame = read_csv(path)
    if frame.empty:
        return frame
    date_col = find_col(frame, "Date", "Datetime", "Timestamp")
    open_col = find_col(frame, "Open")
    high_col = find_col(frame, "High")
    low_col = find_col(frame, "Low")
    close_col = find_col(frame, "Close")
    if not all([date_col, open_col, high_col, low_col, close_col]):
        return pd.DataFrame()
    out = pd.DataFrame({
        "Date": pd.to_datetime(frame[date_col], errors="coerce"),
        "Open": pd.to_numeric(frame[open_col], errors="coerce"),
        "High": pd.to_numeric(frame[high_col], errors="coerce"),
        "Low": pd.to_numeric(frame[low_col], errors="coerce"),
        "Close": pd.to_numeric(frame[close_col], errors="coerce"),
    })
    return out.dropna().drop_duplicates("Date").sort_values("Date").reset_index(drop=True)


def entry_from_zone(bar: pd.Series, low: float, high: float) -> float:
    opened = float(bar["Open"])
    if low <= opened <= high:
        return opened
    if opened > high:
        return high
    if opened < low:
        return low
    return (low + high) / 2.0


def first_trigger(record: sqlite3.Row, future: pd.DataFrame) -> tuple[int, float] | None:
    expiry = max(as_int(record["trigger_expiry_days"], 7), 1)
    window = future.head(expiry)
    if window.empty:
        return None
    trigger_type = norm_text(record["trigger_type"]).upper()
    if trigger_type == "CLOSE_ABOVE":
        trigger = as_float(record["trigger_price"])
        if trigger is None:
            return None
        matches = window.index[window["Close"] > trigger].tolist()
        if not matches:
            return None
        idx = int(matches[0])
        return idx, float(future.loc[idx, "Close"])
    if trigger_type == "ENTRY_ZONE_TOUCH":
        low = as_float(record["entry_zone_low"])
        high = as_float(record["entry_zone_high"])
        if low is None or high is None:
            return None
        matches = window.index[(window["Low"] <= high) & (window["High"] >= low)].tolist()
        if not matches:
            return None
        idx = int(matches[0])
        return idx, entry_from_zone(future.loc[idx], low, high)
    return None


def close_after(px: pd.DataFrame, entry_idx: int, days: int) -> float | None:
    idx = entry_idx + days
    return float(px.loc[idx, "Close"]) if idx < len(px) else None


def ret_pct(price: float | None, entry: float | None) -> float | None:
    if price is None or entry is None or entry == 0:
        return None
    return (price / entry - 1.0) * 100.0


def evaluate_record(record: sqlite3.Row, price: pd.DataFrame) -> dict[str, Any]:
    signal_date = pd.Timestamp(record["signal_date"])
    future = price[price["Date"] > signal_date].copy().reset_index(drop=True)
    update: dict[str, Any] = {"updated_at": now_text()}
    if future.empty:
        return update
    trigger = first_trigger(record, future)
    expiry = max(as_int(record["trigger_expiry_days"], 7), 1)
    if trigger is None:
        if len(future) >= expiry:
            update.update({
                "current_status": "EXPIRED",
                "final_outcome": "EXPIRED",
                "exit_reason": "TRIGGER_NOT_REACHED_WITHIN_WINDOW",
            })
        return update
    entry_idx, entry_price = trigger
    entry_date = future.loc[entry_idx, "Date"].date().isoformat()
    update.update({
        "current_status": "OPEN",
        "trigger_date": entry_date,
        "entry_date": entry_date,
        "entry_price": entry_price,
    })
    for days in [1, 3, 5, 7]:
        close = close_after(future, entry_idx, days)
        update[f"close_d{days}"] = close
        update[f"return_d{days}"] = ret_pct(close, entry_price)

    trigger_type = norm_text(record["trigger_type"]).upper()
    evaluation_start = entry_idx + 1 if trigger_type == "CLOSE_ABOVE" else entry_idx
    max_hold = max(as_int(record["max_hold_days"], 20), 1)
    evaluation_end = min(evaluation_start + max_hold - 1, len(future) - 1)
    bars = future.loc[evaluation_start:evaluation_end]
    if bars.empty:
        return update
    stop = as_float(record["stop_loss"])
    tp1 = as_float(record["take_profit_1"])
    tp2 = as_float(record["take_profit_2"])
    update["max_price"] = float(bars["High"].max())
    update["min_price"] = float(bars["Low"].min())
    update["mfe_pct"] = ret_pct(update["max_price"], entry_price)
    update["mae_pct"] = ret_pct(update["min_price"], entry_price)

    for idx, bar in bars.iterrows():
        holding_days = idx - entry_idx + 1
        hit_stop = stop is not None and float(bar["Low"]) <= stop
        hit_tp2 = tp2 is not None and float(bar["High"]) >= tp2
        hit_tp1 = tp1 is not None and float(bar["High"]) >= tp1
        # Conservative daily-candle rule: if stop and target are both touched in
        # the same candle, stop is assumed first.
        if hit_stop:
            update.update({
                "current_status": "CLOSED",
                "final_outcome": "LOSS",
                "sl_hit": 1,
                "exit_date": bar["Date"].date().isoformat(),
                "exit_price": stop,
                "exit_reason": "STOP_LOSS_HIT" if not (hit_tp1 or hit_tp2) else "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE",
                "holding_days": holding_days,
                "realized_return_pct": ret_pct(stop, entry_price),
            })
            return update
        if hit_tp2:
            update.update({
                "current_status": "CLOSED",
                "final_outcome": "WIN",
                "tp1_hit": 1,
                "tp2_hit": 1,
                "exit_date": bar["Date"].date().isoformat(),
                "exit_price": tp2,
                "exit_reason": "TP2_HIT",
                "holding_days": holding_days,
                "realized_return_pct": ret_pct(tp2, entry_price),
            })
            return update
        if hit_tp1:
            update.update({
                "current_status": "CLOSED",
                "final_outcome": "WIN",
                "tp1_hit": 1,
                "exit_date": bar["Date"].date().isoformat(),
                "exit_price": tp1,
                "exit_reason": "TP1_HIT",
                "holding_days": holding_days,
                "realized_return_pct": ret_pct(tp1, entry_price),
            })
            return update

    sessions_available = len(future) - entry_idx
    if sessions_available >= max_hold:
        exit_idx = entry_idx + max_hold - 1
        exit_bar = future.loc[exit_idx]
        realized = ret_pct(float(exit_bar["Close"]), entry_price)
        outcome = "WIN" if realized is not None and realized > 0 else "LOSS" if realized is not None and realized < 0 else "AMBIGUOUS"
        update.update({
            "current_status": "CLOSED",
            "final_outcome": outcome,
            "exit_date": exit_bar["Date"].date().isoformat(),
            "exit_price": float(exit_bar["Close"]),
            "exit_reason": "MAX_HOLD_EXIT",
            "holding_days": max_hold,
            "realized_return_pct": realized,
        })
    return update


def update_outcomes(conn: sqlite3.Connection, historical_dir: Path) -> dict[str, int]:
    counters = {"evaluated": 0, "updated": 0, "missing_price": 0}
    records = conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE current_status IN ('WAITING_TRIGGER','OPEN') ORDER BY signal_date,symbol"
    ).fetchall()
    for record in records:
        path = historical_dir / f"{record['symbol']}.csv"
        prices = load_prices(path)
        if prices.empty:
            counters["missing_price"] += 1
            conn.execute(
                "UPDATE signal_outcome_ledger SET updated_at=?, exit_reason=COALESCE(exit_reason,'PRICE_DATA_MISSING') WHERE signal_id=?",
                (now_text(), record["signal_id"]),
            )
            continue
        counters["evaluated"] += 1
        changes = evaluate_record(record, prices)
        if len(changes) > 1:
            assignments = ",".join(f"{key}=?" for key in changes)
            conn.execute(
                f"UPDATE signal_outcome_ledger SET {assignments} WHERE signal_id=?",
                [*changes.values(), record["signal_id"]],
            )
            counters["updated"] += 1
    conn.commit()
    return counters


def ledger_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM signal_outcome_ledger ORDER BY signal_date DESC, symbol",
        conn,
    )


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator * 100.0 if denominator else None


def performance_row(group: pd.DataFrame, label: str) -> dict[str, Any]:
    quality = group["data_quality_status"].astype(str).str.upper() if "data_quality_status" in group.columns else pd.Series("UNKNOWN", index=group.index)
    raw_decisions = group["raw_decision"].astype(str).str.upper() if "raw_decision" in group.columns else pd.Series("", index=group.index)
    current_recommendations = group[raw_decisions.isin(CURRENT_RECOMMENDATION_DECISIONS)].copy()
    valid = group[(group["current_status"] != "INVALID_DATA") & quality.isin(VALID_SIGNAL_QUALITY)].copy()
    excluded = len(group) - len(valid)
    triggered = valid[valid["entry_date"].notna() & (valid["entry_date"].astype(str) != "")]
    closed = valid[valid["final_outcome"].isin(FINAL_OUTCOMES)]
    wins = int((closed["final_outcome"] == "WIN").sum())
    losses = int((closed["final_outcome"] == "LOSS").sum())
    ambiguous = int((closed["final_outcome"] == "AMBIGUOUS").sum())
    realized = pd.to_numeric(closed["realized_return_pct"], errors="coerce").dropna()
    positive = realized[realized > 0].sum()
    negative = abs(realized[realized < 0].sum())
    return {
        "Group": label,
        "Signals": len(valid),
        "Current_Recommendations": len(current_recommendations),
        "Historical_Evaluated_Signals": len(valid),
        "Excluded_Invalid_Data": excluded,
        "Triggered": len(triggered),
        "Triggered_Lifecycle": len(triggered),
        "Trigger_Rate_Pct": safe_ratio(len(triggered), len(valid)),
        "Waiting_Trigger": int((valid["current_status"] == "WAITING_TRIGGER").sum()),
        "Open": int((valid["current_status"] == "OPEN").sum()),
        "Expired": int((valid["current_status"] == "EXPIRED").sum()),
        "Cancelled": int((valid["current_status"] == "CANCELLED").sum()),
        "Closed": len(closed),
        "Closed_Outcomes": len(closed),
        "Win": wins,
        "Loss": losses,
        "Ambiguous": ambiguous,
        "Win_Rate_Pct": safe_ratio(wins, wins + losses),
        "Conservative_Win_Rate_Pct": safe_ratio(wins, wins + losses + ambiguous),
        "TP1_Hit_Rate_Pct": safe_ratio(int(pd.to_numeric(closed["tp1_hit"], errors="coerce").fillna(0).gt(0).sum()), len(closed)),
        "TP2_Hit_Rate_Pct": safe_ratio(int(pd.to_numeric(closed["tp2_hit"], errors="coerce").fillna(0).gt(0).sum()), len(closed)),
        "SL_Hit_Rate_Pct": safe_ratio(int(pd.to_numeric(closed["sl_hit"], errors="coerce").fillna(0).gt(0).sum()), len(closed)),
        "Average_Return_Pct": realized.mean() if len(realized) else None,
        "Median_Return_Pct": realized.median() if len(realized) else None,
        "Profit_Factor": positive / negative if negative > 0 else None,
        "Expectancy_Pct": realized.mean() if len(realized) else None,
        "Average_MFE_Pct": pd.to_numeric(triggered["mfe_pct"], errors="coerce").mean() if len(triggered) else None,
        "Average_MAE_Pct": pd.to_numeric(triggered["mae_pct"], errors="coerce").mean() if len(triggered) else None,
        "Average_Holding_Days": pd.to_numeric(closed["holding_days"], errors="coerce").mean() if len(closed) else None,
    }


def grouped_performance(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty or column not in df.columns:
        return pd.DataFrame()
    rows = [performance_row(group, str(label)) for label, group in df.groupby(column, dropna=False)]
    return pd.DataFrame(rows).sort_values(["Closed", "Signals"], ascending=[False, False]) if rows else pd.DataFrame()


def fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "belum tersedia"
    return f"{float(value):.{digits}f}{suffix}".replace(".", ",")


def sample_note(closed: int) -> str:
    if closed < 20:
        return "Sampel masih terlalu kecil; belum layak menyimpulkan kualitas sistem."
    if closed < 50:
        return "Sampel cukup untuk evaluasi awal, tetapi belum kuat."
    if closed < 100:
        return "Sampel mulai representatif untuk evaluasi operasional."
    return "Sampel sudah lebih kuat, tetap evaluasi per regime dan setup."


def telegram_report(overall: dict[str, Any], by_setup: pd.DataFrame, by_signal: pd.DataFrame) -> str:
    start = overall.get("Start_Date", "-")
    end = overall.get("End_Date", "-")
    lines = [
        "📊 <b>SDE SWING — PERFORMANCE</b>",
        f"📅 {html.escape(str(start))} s.d. {html.escape(str(end))}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"Recommendations : {overall['Current_Recommendations']}",
        f"Evaluated       : {overall['Historical_Evaluated_Signals']}",
        f"Excluded Data   : {overall['Excluded_Invalid_Data']}",
        f"Triggered Life  : {overall['Triggered_Lifecycle']}",
        f"Trigger Rate    : {fmt(overall['Trigger_Rate_Pct'], 1, '%')}",
        f"Closed Outcomes : {overall['Closed_Outcomes']}",
        f"Open            : {overall['Open']}",
        f"Waiting Trigger : {overall['Waiting_Trigger']}",
        f"Expired         : {overall['Expired']}",
        f"Cancelled       : {overall['Cancelled']}",
        "",
        f"✅ Win           : {overall['Win']}",
        f"❌ Loss          : {overall['Loss']}",
        f"⚪ Ambiguous     : {overall['Ambiguous']}",
        f"🎯 Win Rate      : {fmt(overall['Win_Rate_Pct'], 1, '%')}",
        f"Avg Return      : {fmt(overall['Average_Return_Pct'], 2, '%')}",
        f"Profit Factor   : {fmt(overall['Profit_Factor'], 2)}",
        f"Avg MFE / MAE   : {fmt(overall['Average_MFE_Pct'], 2, '%')} / {fmt(overall['Average_MAE_Pct'], 2, '%')}",
    ]
    eligible_setup = by_setup[pd.to_numeric(by_setup.get("Closed", 0), errors="coerce").fillna(0) > 0] if not by_setup.empty else pd.DataFrame()
    if not eligible_setup.empty:
        ranked = eligible_setup.sort_values(["Win_Rate_Pct", "Closed"], ascending=[False, False])
        best = ranked.iloc[0]
        worst = ranked.iloc[-1]
        lines += [
            "",
            "🏆 <b>SETUP TERBAIK</b>",
            f"{html.escape(str(best['Group']))} — win rate {fmt(best['Win_Rate_Pct'], 1, '%')} ({int(best['Closed'])} closed)",
        ]
        if len(ranked) > 1:
            lines += [
                "",
                "🔎 <b>PERLU EVALUASI</b>",
                f"{html.escape(str(worst['Group']))} — win rate {fmt(worst['Win_Rate_Pct'], 1, '%')} ({int(worst['Closed'])} closed)",
            ]
    lines += ["", f"⚠️ {html.escape(sample_note(int(overall['Closed'])))}"]
    return "\n".join(lines)


def export_reports(conn: sqlite3.Connection, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = ledger_df(conn)
    df.to_csv(output_dir / "SIGNAL_OUTCOME_LEDGER.csv", index=False, encoding="utf-8-sig")
    overall = performance_row(df, "ALL")
    overall["Start_Date"] = df["signal_date"].min() if not df.empty else "-"
    overall["End_Date"] = df["signal_date"].max() if not df.empty else "-"
    overall_df = pd.DataFrame([overall])
    overall_df.to_csv(output_dir / "PERFORMANCE_SUMMARY.csv", index=False, encoding="utf-8-sig")
    by_setup = grouped_performance(df, "setup_type")
    by_signal = grouped_performance(df, "signal_type")
    by_broker = grouped_performance(df, "broker_confidence_bucket")
    by_regime = grouped_performance(df, "market_regime")
    by_setup.to_csv(output_dir / "PERFORMANCE_BY_SETUP.csv", index=False, encoding="utf-8-sig")
    by_signal.to_csv(output_dir / "PERFORMANCE_BY_SIGNAL_TYPE.csv", index=False, encoding="utf-8-sig")
    by_broker.to_csv(output_dir / "PERFORMANCE_BY_BROKER_CONFIDENCE.csv", index=False, encoding="utf-8-sig")
    by_regime.to_csv(output_dir / "PERFORMANCE_BY_MARKET_REGIME.csv", index=False, encoding="utf-8-sig")
    message = telegram_report(overall, by_setup, by_signal)
    (output_dir / "PERFORMANCE_TELEGRAM.txt").write_text(message, encoding="utf-8")
    payload = {
        "generated_at": now_text(),
        "overall": overall,
        "files": {
            "ledger": str((output_dir / "SIGNAL_OUTCOME_LEDGER.csv").resolve()),
            "summary": str((output_dir / "PERFORMANCE_SUMMARY.csv").resolve()),
            "by_setup": str((output_dir / "PERFORMANCE_BY_SETUP.csv").resolve()),
            "by_signal": str((output_dir / "PERFORMANCE_BY_SIGNAL_TYPE.csv").resolve()),
            "by_broker_confidence": str((output_dir / "PERFORMANCE_BY_BROKER_CONFIDENCE.csv").resolve()),
            "by_market_regime": str((output_dir / "PERFORMANCE_BY_MARKET_REGIME.csv").resolve()),
            "telegram_preview": str((output_dir / "PERFORMANCE_TELEGRAM.txt").resolve()),
        },
    }
    (output_dir / "PERFORMANCE_SUMMARY.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return payload


def print_table(path: Path, title: str, section: str = "") -> None:
    df = read_csv(path)
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)
    if df.empty:
        print("Belum ada data. Jalankan Update semua outcome terlebih dahulu.")
        return
    if section == "overall":
        row = df.iloc[0].to_dict()
        print(f"Periode          : {row.get('Start_Date', '-')} s.d. {row.get('End_Date', '-')}")
        print(f"Recommendations  : {int(row.get('Current_Recommendations', 0))}")
        print(f"Evaluated Signals: {int(row.get('Historical_Evaluated_Signals', row.get('Signals', 0)))}")
        print(f"Excluded Data    : {int(row.get('Excluded_Invalid_Data', 0))}")
        print(f"Triggered Life   : {int(row.get('Triggered_Lifecycle', row.get('Triggered', 0)))} ({fmt(row.get('Trigger_Rate_Pct'), 1, '%')})")
        print(f"Waiting Trigger  : {int(row.get('Waiting_Trigger', 0))}")
        print(f"Open             : {int(row.get('Open', 0))}")
        print(f"Expired          : {int(row.get('Expired', 0))}")
        print(f"Cancelled        : {int(row.get('Cancelled', 0))}")
        print(f"Closed Outcomes  : {int(row.get('Closed_Outcomes', row.get('Closed', 0)))}")
        print(f"Win / Loss       : {int(row.get('Win', 0))} / {int(row.get('Loss', 0))}")
        print(f"Ambiguous        : {int(row.get('Ambiguous', 0))}")
        print(f"Win Rate         : {fmt(row.get('Win_Rate_Pct'), 1, '%')}")
        print(f"Avg Return       : {fmt(row.get('Average_Return_Pct'), 2, '%')}")
        print(f"Profit Factor    : {fmt(row.get('Profit_Factor'), 2)}")
        print(f"Avg MFE / MAE    : {fmt(row.get('Average_MFE_Pct'), 2, '%')} / {fmt(row.get('Average_MAE_Pct'), 2, '%')}")
        print(f"Avg Holding Days : {fmt(row.get('Average_Holding_Days'), 1)}")
        print()
        print(sample_note(int(row.get('Closed', 0))))
        print(f"\nFile: {path.resolve()}")
        return
    if section == "ledger":
        columns = [
            "signal_date", "symbol", "signal_type", "setup_type", "current_status",
            "entry_date", "final_outcome", "realized_return_pct", "exit_reason",
        ]
        columns = [c for c in columns if c in df.columns]
        print(df[columns].head(50).to_string(index=False, na_rep="-"))
        if len(df) > 50:
            print(f"\nMenampilkan 50 dari {len(df)} baris.")
        print(f"\nFile lengkap: {path.resolve()}")
        return
    columns = [
        "Group", "Signals", "Triggered", "Trigger_Rate_Pct", "Closed", "Win", "Loss",
        "Win_Rate_Pct", "Average_Return_Pct", "Profit_Factor", "Average_MFE_Pct", "Average_MAE_Pct",
    ]
    columns = [c for c in columns if c in df.columns]
    print(df[columns].to_string(index=False, na_rep="-"))
    print(f"\nFile: {path.resolve()}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def split_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = line
    if current:
        parts.append(current)
    return parts


def send_telegram(message_path: Path, telegram_config: Path, scheduler_config: Path, dry_run: bool = False) -> None:
    text = message_path.read_text(encoding="utf-8")
    if dry_run:
        print(text)
        return
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang.")
    tg = load_json(telegram_config).get("telegram", {})
    scheduler = load_json(scheduler_config)
    token = os.getenv("TELEGRAM_BOT_TOKEN") or norm_text(tg.get("bot_token"))
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or norm_text(tg.get("chat_id"))
    if not token or not chat_id:
        raise RuntimeError("Telegram token/chat_id belum dikonfigurasi.")
    topic = norm_text(scheduler.get("delivery", {}).get("topic_routing", {}).get("evaluation"))
    for part in split_text(text):
        data = {
            "chat_id": chat_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        if topic:
            data["message_thread_id"] = topic
        response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
        body = response.json()
        if not response.ok or not body.get("ok"):
            raise RuntimeError(f"Telegram API gagal: {body}")


def sync(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    bootstrap = RegisterResult()
    registered = RegisterResult()
    if args.bootstrap_db:
        bootstrap = bootstrap_from_legacy_db(conn, args.trigger_expiry_days)
    if args.decisions:
        registered = register_decision_file(
            conn,
            Path(args.decisions),
            Path(args.entry_plans),
            args.run_id or f"OUTCOME-SYNC-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            args.signal_date,
            args.trigger_expiry_days,
        )
    updates = update_outcomes(conn, Path(args.historical_dir))
    payload = export_reports(conn, Path(args.output_dir))
    conn.close()
    overall = payload["overall"]
    print("\nOutcome Tracker selesai")
    print(f"Bootstrap legacy : inserted={bootstrap.inserted}, active_updated={bootstrap.updated_active}")
    print(f"Register current : inserted={registered.inserted}, active_updated={registered.updated_active}")
    print(f"Evaluated        : {updates['evaluated']}")
    print(f"Missing price    : {updates['missing_price']}")
    print(f"Signals          : {overall['Signals']}")
    print(f"Triggered        : {overall['Triggered']}")
    print(f"Closed           : {overall['Closed']}")
    print(f"Win/Loss         : {overall['Win']}/{overall['Loss']}")
    print(f"Win rate         : {fmt(overall['Win_Rate_Pct'], 1, '%')}")
    print(f"Output           : {Path(args.output_dir).resolve()}")
    return 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SDE Swing persistent outcome tracker and performance evaluator")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync", help="Register signals, update outcomes, and rebuild reports")
    sync_parser.add_argument("--db", default=str(DEFAULT_DB))
    sync_parser.add_argument("--historical-dir", default=str(DEFAULT_HISTORICAL))
    sync_parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    sync_parser.add_argument("--decisions", default="")
    sync_parser.add_argument("--entry-plans", default=str(DEFAULT_PLANS))
    sync_parser.add_argument("--run-id", default="")
    sync_parser.add_argument("--signal-date", default="")
    sync_parser.add_argument("--trigger-expiry-days", type=int, default=7)
    sync_parser.add_argument("--bootstrap-db", action="store_true")

    show = sub.add_parser("show", help="Show a generated performance section")
    show.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    show.add_argument("--section", choices=["overall", "setup", "signal", "broker", "regime", "ledger"], default="overall")

    telegram = sub.add_parser("telegram", help="Send the latest performance report to Telegram")
    telegram.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    telegram.add_argument("--telegram-config", default=str(PROJECT_ROOT / "config/telegram.json"))
    telegram.add_argument("--scheduler-config", default=str(PROJECT_ROOT / "config/scheduler.json"))
    telegram.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    if args.command == "sync":
        return sync(args)
    output = Path(args.output_dir)
    if args.command == "show":
        mapping = {
            "overall": ("PERFORMANCE_SUMMARY.csv", "PERFORMA KESELURUHAN"),
            "setup": ("PERFORMANCE_BY_SETUP.csv", "PERFORMA BERDASARKAN SETUP"),
            "signal": ("PERFORMANCE_BY_SIGNAL_TYPE.csv", "PERFORMA BERDASARKAN JENIS SINYAL"),
            "broker": ("PERFORMANCE_BY_BROKER_CONFIDENCE.csv", "PERFORMA BERDASARKAN BROKER CONFIDENCE"),
            "regime": ("PERFORMANCE_BY_MARKET_REGIME.csv", "PERFORMA BERDASARKAN MARKET REGIME"),
            "ledger": ("SIGNAL_OUTCOME_LEDGER.csv", "SIGNAL OUTCOME LEDGER"),
        }
        filename, title = mapping[args.section]
        print_table(output / filename, title, args.section)
        return 0
    if args.command == "telegram":
        message_path = output / "PERFORMANCE_TELEGRAM.txt"
        if not message_path.exists():
            raise FileNotFoundError("Laporan belum dibuat. Jalankan Update Outcome terlebih dahulu.")
        send_telegram(message_path, Path(args.telegram_config), Path(args.scheduler_config), args.dry_run)
        print("Laporan performance berhasil diproses.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
