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
from typing import Any, Iterable, Mapping

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import find_col, normalize_symbol
from modules.job_runner.runtime import load_environment_file

try:
    import requests
except ImportError:  # live Telegram only
    requests = None

ACTIVE_STATUSES = {"WAITING_TRIGGER", "OPEN"}
FINAL_OUTCOMES = {"WIN", "LOSS", "AMBIGUOUS"}
TRACKED_DECISIONS = {"STRONG BUY", "BUY", "BUY CANDIDATE", "BUY READY", "BUY ON TRIGGER", "BUY CONFIRMED"}
CURRENT_RECOMMENDATION_DECISIONS = set(TRACKED_DECISIONS)
TERMINAL_STATUSES = {"CLOSED", "EXPIRED", "INVALIDATED_BEFORE_ENTRY"}
VALID_SIGNAL_QUALITY = {
    "VALID",
    "PARTIAL_COVERAGE",
    "SUCCESS_WITH_WARNING",
    "VALID_WITH_REFRESH_FALLBACK",
    "VALID_WITH_ZAPI_WARNING",
}
MATERIAL_LIFECYCLE_EVENT_TYPES = {
    "ENTRY_TRIGGERED",
    "TP1_HIT",
    "TP2_HIT",
    "STOP_LOSS_HIT",
    "MAX_HOLD_EXIT",
    "EXPIRED",
    "INVALIDATED_BEFORE_ENTRY",
}
DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
DEFAULT_HISTORICAL = PROJECT_ROOT / "data/output/historical/by_symbol"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/output/analytics/performance"
DEFAULT_DECISIONS = PROJECT_ROOT / "data/output/decision/FINAL_DECISION_V3.csv"
DEFAULT_PLANS = PROJECT_ROOT / "data/output/exit/ENTRY_PLANS.csv"


LEDGER_COLUMNS = [
    "signal_id", "run_id", "symbol", "signal_date", "last_seen_date",
    "latest_scan_status", "latest_scan_date", "latest_scan_run_id",
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
    latest_scan_status TEXT,
    latest_scan_date TEXT,
    latest_scan_run_id TEXT,
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

CREATE TABLE IF NOT EXISTS lifecycle_events (
    event_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT,
    event_date TEXT NOT NULL,
    event_price REAL,
    event_reason TEXT,
    telegram_notified_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_events_pending
    ON lifecycle_events(telegram_notified_at, event_date);
CREATE INDEX IF NOT EXISTS idx_lifecycle_events_signal
    ON lifecycle_events(signal_id, created_at);

CREATE TABLE IF NOT EXISTS portfolio_positions (
    position_id TEXT PRIMARY KEY,
    signal_id TEXT,
    symbol TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    quantity REAL NOT NULL,
    buy_price REAL NOT NULL,
    current_status TEXT NOT NULL DEFAULT 'OPEN',
    sell_date TEXT,
    sell_price REAL,
    realized_return_pct REAL,
    notes TEXT,
    source_run_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portfolio_positions_symbol_status
    ON portfolio_positions(symbol, current_status);
CREATE INDEX IF NOT EXISTS idx_portfolio_positions_buy_date
    ON portfolio_positions(buy_date);
"""


@dataclass
class RegisterResult:
    inserted: int = 0
    updated_active: int = 0
    skipped: int = 0


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def event_id(
    signal_id: str,
    event_type: str,
    event_date: str,
    event_price: Any = None,
    event_reason: str = "",
) -> str:
    """Return a deterministic lifecycle event key for idempotent re-runs."""
    raw = "|".join([
        norm_text(signal_id),
        norm_text(event_type).upper(),
        norm_text(event_date),
        norm_text(event_price),
        norm_text(event_reason),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def record_lifecycle_event(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    symbol: str,
    event_type: str,
    previous_status: str,
    new_status: str,
    event_date: str,
    event_price: Any = None,
    event_reason: str = "",
) -> str:
    """Persist one lifecycle transition/milestone without duplicating it."""
    event_type = norm_text(event_type).upper()
    event_date = parse_date(event_date) or norm_text(event_date)
    identifier = event_id(signal_id, event_type, event_date, event_price, event_reason)
    conn.execute(
        """
        INSERT OR IGNORE INTO lifecycle_events (
            event_id, signal_id, symbol, event_type, previous_status, new_status,
            event_date, event_price, event_reason, telegram_notified_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            identifier,
            signal_id,
            normalize_symbol(symbol),
            event_type,
            norm_text(previous_status),
            norm_text(new_status),
            event_date,
            as_float(event_price),
            norm_text(event_reason),
            now_text(),
        ),
    )
    return identifier


def pending_lifecycle_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM lifecycle_events
        WHERE telegram_notified_at IS NULL OR telegram_notified_at=''
        ORDER BY event_date, created_at, symbol
        """
    ).fetchall()


def _event_value(event: Mapping[str, Any] | sqlite3.Row, key: str, default: Any = "") -> Any:
    try:
        return event[key]
    except (KeyError, IndexError, TypeError):
        return default


def is_material_lifecycle_event(event: Mapping[str, Any] | sqlite3.Row) -> bool:
    """Return whether an event is worth sending as a lifecycle notification.

    Reconfirmation scans and the synthetic ``CLOSED`` companion event are
    retained in SQLite for auditability, but they do not create a Telegram
    notification.  The milestone/terminal event carries the actionable fact.
    """
    event_type = norm_text(_event_value(event, "event_type")).upper()
    if event_type in MATERIAL_LIFECYCLE_EVENT_TYPES:
        return True
    if event_type == "CLOSED":
        # ``CLOSED`` is emitted as a synthetic companion to a concrete
        # milestone (including conservative stop/target exits).  The
        # milestone itself is the notification-worthy event, so never send
        # the companion and risk duplicate Telegram noise.
        return False
    return False


def material_lifecycle_events(
    events: Iterable[Mapping[str, Any] | sqlite3.Row],
) -> list[Mapping[str, Any] | sqlite3.Row]:
    """Filter pending/audit events to actionable lifecycle milestones."""
    return [event for event in events if is_material_lifecycle_event(event)]


def mark_lifecycle_events_notified(db_path: Path, event_ids: Iterable[str]) -> int:
    identifiers = [norm_text(item) for item in event_ids if norm_text(item)]
    if not identifiers:
        return 0
    conn = connect(db_path)
    placeholders = ",".join("?" for _ in identifiers)
    cursor = conn.execute(
        f"UPDATE lifecycle_events SET telegram_notified_at=? WHERE event_id IN ({placeholders}) AND (telegram_notified_at IS NULL OR telegram_notified_at='')",
        [now_text(), *identifiers],
    )
    conn.commit()
    count = cursor.rowcount
    conn.close()
    return count


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
    existing = {row[1] for row in conn.execute("PRAGMA table_info(signal_outcome_ledger)")}
    for name in ("latest_scan_status", "latest_scan_date", "latest_scan_run_id"):
        if name not in existing:
            conn.execute(f"ALTER TABLE signal_outcome_ledger ADD COLUMN {name} TEXT")
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
    # A BUY without a valid executable plan is retained as an explicit terminal
    # invalidation, never silently converted into a loss or deleted.
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
    current_status = "WAITING_TRIGGER" if trigger_type != "INVALID" else "INVALIDATED_BEFORE_ENTRY"
    return {
        "signal_id": signal_id,
        "run_id": run_id,
        "symbol": symbol,
        "signal_date": signal_date,
        "last_seen_date": signal_date,
        "latest_scan_status": raw_decision,
        "latest_scan_date": signal_date,
        "latest_scan_run_id": run_id,
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
        "exit_reason": "INVALID_PLAN_BEFORE_ENTRY" if trigger_type == "INVALID" else None,
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
        "final_outcome": "INVALIDATED" if trigger_type == "INVALID" else None,
        "realized_return_pct": None,
        "created_at": now_text(),
        "updated_at": now_text(),
        "source_json": json.dumps(source, ensure_ascii=False, default=str),
    }


def _update_scan_metadata(
    conn: sqlite3.Connection,
    *,
    signal_id: str,
    status: str,
    scan_date: str,
    run_id: str,
) -> None:
    conn.execute(
        """
        UPDATE signal_outcome_ledger
        SET latest_scan_status=?, latest_scan_date=?, latest_scan_run_id=?, updated_at=?
        WHERE signal_id=?
        """,
        (norm_text(status, "NOT_IN_LATEST_SCAN").upper(), parse_date(scan_date) or scan_date, run_id, now_text(), signal_id),
    )


def upsert_signal(conn: sqlite3.Connection, record: dict[str, Any]) -> str:
    """Insert a signal once, or refresh the same active lifecycle in place."""
    exact = conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE signal_id=?",
        (record["signal_id"],),
    ).fetchone()
    active = None if exact else conn.execute(
        """
        SELECT * FROM signal_outcome_ledger
        WHERE symbol=? AND current_status IN ('WAITING_TRIGGER','OPEN')
        ORDER BY signal_date ASC LIMIT 1
        """,
        (record["symbol"],),
    ).fetchone()
    target = exact or active
    if target:
        signal_id = target["signal_id"]
        previous_status = norm_text(target["current_status"])
        updates: dict[str, Any] = {
            "latest_scan_status": record.get("latest_scan_status") or record.get("raw_decision"),
            "latest_scan_date": record.get("latest_scan_date") or record.get("signal_date"),
            "latest_scan_run_id": record.get("latest_scan_run_id") or record.get("run_id"),
            "run_id": record.get("run_id"),
            "last_seen_date": max(norm_text(target["last_seen_date"]), record["signal_date"]),
            "updated_at": now_text(),
        }
        if previous_status in ACTIVE_STATUSES:
            updates.update({
                "raw_decision": record["raw_decision"],
                "data_quality_status": record["data_quality_status"],
                "score": record["score"],
                "technical_quality": record["technical_quality"],
                "entry_readiness": record["entry_readiness"],
                "broker_confidence": record["broker_confidence"],
                "broker_confidence_bucket": record["broker_confidence_bucket"],
                "broker_direction": record["broker_direction"],
                "market_regime": record["market_regime"],
            })
            # A waiting candidate can be upgraded to a confirmed plan.  Once
            # OPEN, the original engine-owned plan is immutable.
            if previous_status == "WAITING_TRIGGER" and record["current_status"] == "INVALIDATED_BEFORE_ENTRY":
                updates.update({
                    "current_status": "INVALIDATED_BEFORE_ENTRY",
                    "final_outcome": "INVALIDATED",
                    "exit_reason": "INVALID_PLAN_BEFORE_ENTRY",
                })
            elif previous_status == "WAITING_TRIGGER" and record["signal_type"] == "BUY CONFIRMED":
                for name in [
                    "signal_type", "raw_decision", "plan_status", "trigger_type", "trigger_price",
                    "entry_zone_low", "entry_zone_high", "reference_price", "stop_loss",
                    "take_profit_1", "take_profit_2", "max_hold_days", "source_json",
                ]:
                    updates[name] = record[name]
        assignments = ",".join(f"{key}=?" for key in updates)
        conn.execute(
            f"UPDATE signal_outcome_ledger SET {assignments} WHERE signal_id=?",
            [*updates.values(), signal_id],
        )
        new_status = norm_text(updates.get("current_status", previous_status))
        if previous_status in ACTIVE_STATUSES and record.get("current_status") != "INVALIDATED_BEFORE_ENTRY":
            record_lifecycle_event(
                conn,
                signal_id=signal_id,
                symbol=record["symbol"],
                event_type="SIGNAL_RECONFIRMED",
                previous_status=previous_status,
                new_status=new_status,
                event_date=record["signal_date"],
                event_price=record.get("reference_price"),
                event_reason=f"SCAN:{record.get('raw_decision', '')}",
            )
        elif previous_status == "WAITING_TRIGGER" and new_status == "INVALIDATED_BEFORE_ENTRY":
            record_lifecycle_event(
                conn,
                signal_id=signal_id,
                symbol=record["symbol"],
                event_type="INVALIDATED_BEFORE_ENTRY",
                previous_status=previous_status,
                new_status=new_status,
                event_date=record["signal_date"],
                event_price=record.get("reference_price"),
                event_reason="INVALID_PLAN_BEFORE_ENTRY",
            )
        return "UPDATED_ACTIVE" if active or exact else "SKIPPED"
    columns = LEDGER_COLUMNS
    placeholders = ",".join("?" for _ in columns)
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO signal_outcome_ledger ({','.join(columns)}) VALUES ({placeholders})",
        [record.get(col) for col in columns],
    )
    if cursor.rowcount > 0:
        record_lifecycle_event(
            conn,
            signal_id=record["signal_id"],
            symbol=record["symbol"],
            event_type="INVALIDATED_BEFORE_ENTRY" if record["current_status"] == "INVALIDATED_BEFORE_ENTRY" else "SIGNAL_CREATED",
            previous_status="",
            new_status=record["current_status"],
            event_date=record["signal_date"],
            event_price=record.get("reference_price"),
            event_reason=record.get("exit_reason") or "BUY_SIGNAL_REGISTERED",
        )
        return "INSERTED"
    return "SKIPPED"


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
        for active in conn.execute(
            "SELECT signal_id FROM signal_outcome_ledger WHERE current_status IN ('WAITING_TRIGGER','OPEN')"
        ).fetchall():
            _update_scan_metadata(
                conn,
                signal_id=active["signal_id"],
                status="NOT_IN_LATEST_SCAN",
                scan_date=default_signal_date,
                run_id=run_id,
            )
        conn.commit()
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

    # Scanner visibility is metadata only.  A downgrade or disappearance from
    # today's scan never cancels an active recommendation.
    scan_date = norm_text(default_signal_date)
    if not scan_date:
        date_candidates = []
        for alias in ("Technical_Data_Date", "Latest_Valid_Candle_Date", "Date", "Signal_Date"):
            column = find_col(decisions, alias)
            if column:
                date_candidates.extend(pd.to_datetime(decisions[column], errors="coerce").dropna().tolist())
        if date_candidates:
            scan_date = max(date_candidates).date().isoformat()
    for active in conn.execute(
        "SELECT signal_id,symbol FROM signal_outcome_ledger WHERE current_status IN ('WAITING_TRIGGER','OPEN')"
    ).fetchall():
        raw_decision, _ = current_state.get(active["symbol"], ("NOT_IN_LATEST_SCAN", None))
        _update_scan_metadata(conn, signal_id=active["signal_id"], status=raw_decision, scan_date=scan_date, run_id=run_id)

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
            WHERE UPPER(w.decision) IN (
                'STRONG BUY','BUY','BUY CANDIDATE','BUY READY','BUY ON TRIGGER','BUY CONFIRMED'
            )
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
    update: dict[str, Any] = {"updated_at": now_text()}
    status = norm_text(record["current_status"]).upper()
    if status not in ACTIVE_STATUSES or price.empty:
        return update

    trigger_type = norm_text(record["trigger_type"]).upper()
    newly_triggered = status == "WAITING_TRIGGER"
    if newly_triggered:
        signal_date = pd.Timestamp(record["signal_date"])
        future = price[price["Date"] > signal_date].copy().reset_index(drop=True)
        if future.empty:
            return update
        trigger = first_trigger(record, future)
        expiry = max(as_int(record["trigger_expiry_days"], 7), 1)
        if trigger is None:
            if len(future) >= expiry:
                bar = future.iloc[expiry - 1]
                update.update({
                    "current_status": "EXPIRED",
                    "final_outcome": "EXPIRED",
                    "exit_date": bar["Date"].date().isoformat(),
                    "exit_price": float(bar["Close"]),
                    "exit_reason": "TRIGGER_NOT_REACHED_WITHIN_WINDOW",
                    "_event_type": "EXPIRED",
                    "_event_date": bar["Date"].date().isoformat(),
                    "_event_price": float(bar["Close"]),
                    "_event_reason": "TRIGGER_NOT_REACHED_WITHIN_WINDOW",
                })
            return update
        entry_idx, entry_price = trigger
        entry_date = future.loc[entry_idx, "Date"].date().isoformat()
        update.update({
            "current_status": "OPEN",
            "trigger_date": entry_date,
            "entry_date": entry_date,
            "entry_price": entry_price,
            "_event_type": "ENTRY_TRIGGERED",
            "_event_date": entry_date,
            "_event_price": entry_price,
            "_event_reason": trigger_type,
        })
    else:
        entry_date = norm_text(record["entry_date"])
        entry_price = as_float(record["entry_price"])
        if not entry_date or entry_price is None:
            return update
        entry_timestamp = pd.Timestamp(entry_date)
        future = price[price["Date"] >= entry_timestamp].copy().reset_index(drop=True)
        if future.empty:
            return update
        entry_idx = 0

    for days in [1, 3, 5, 7]:
        close = close_after(future, entry_idx, days)
        update[f"close_d{days}"] = close
        update[f"return_d{days}"] = ret_pct(close, entry_price)

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
            reason = "STOP_LOSS_HIT" if not (hit_tp1 or hit_tp2) else "STOP_AND_TARGET_SAME_CANDLE_CONSERVATIVE"
            update.update({
                "current_status": "CLOSED",
                "final_outcome": "LOSS",
                "sl_hit": 1,
                "exit_date": bar["Date"].date().isoformat(),
                "exit_price": stop,
                "exit_reason": reason,
                "holding_days": holding_days,
                "realized_return_pct": ret_pct(stop, entry_price),
                "_event_type": "STOP_LOSS_HIT",
                "_event_date": bar["Date"].date().isoformat(),
                "_event_price": stop,
                "_event_reason": reason,
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
                "_event_type": "TP2_HIT",
                "_event_date": bar["Date"].date().isoformat(),
                "_event_price": tp2,
                "_event_reason": "TP2_HIT",
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
                "_event_type": "TP1_HIT",
                "_event_date": bar["Date"].date().isoformat(),
                "_event_price": tp1,
                "_event_reason": "TP1_HIT",
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
            "_event_type": "MAX_HOLD_EXIT",
            "_event_date": exit_bar["Date"].date().isoformat(),
            "_event_price": float(exit_bar["Close"]),
            "_event_reason": "MAX_HOLD_EXIT",
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
        event_type = norm_text(changes.pop("_event_type", "")).upper()
        event_date = norm_text(changes.pop("_event_date", ""))
        event_price = changes.pop("_event_price", None)
        event_reason = norm_text(changes.pop("_event_reason", ""))
        if len(changes) > 1:
            assignments = ",".join(f"{key}=?" for key in changes)
            conn.execute(
                f"UPDATE signal_outcome_ledger SET {assignments} WHERE signal_id=?",
                [*changes.values(), record["signal_id"]],
            )
            counters["updated"] += 1
        if event_type:
            previous_status = norm_text(record["current_status"])
            new_status = norm_text(changes.get("current_status", previous_status))
            if previous_status == "WAITING_TRIGGER" and changes.get("entry_date"):
                record_lifecycle_event(
                    conn,
                    signal_id=record["signal_id"],
                    symbol=record["symbol"],
                    event_type="ENTRY_TRIGGERED",
                    previous_status="WAITING_TRIGGER",
                    new_status="OPEN" if new_status != "INVALIDATED_BEFORE_ENTRY" else new_status,
                    event_date=str(changes["entry_date"]),
                    event_price=changes.get("entry_price"),
                    event_reason=norm_text(record["trigger_type"]),
                )
            record_lifecycle_event(
                conn,
                signal_id=record["signal_id"],
                symbol=record["symbol"],
                event_type=event_type,
                previous_status=previous_status,
                new_status=new_status,
                event_date=event_date or record["signal_date"],
                event_price=event_price,
                event_reason=event_reason,
            )
            if new_status == "CLOSED" and event_type in {"TP1_HIT", "TP2_HIT", "STOP_LOSS_HIT", "MAX_HOLD_EXIT"}:
                record_lifecycle_event(
                    conn,
                    signal_id=record["signal_id"],
                    symbol=record["symbol"],
                    event_type="CLOSED",
                    previous_status=previous_status,
                    new_status="CLOSED",
                    event_date=event_date or record["signal_date"],
                    event_price=event_price,
                    event_reason=event_reason,
                )
    conn.commit()
    return counters


def ledger_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM signal_outcome_ledger ORDER BY signal_date DESC, symbol",
        conn,
    )


def _latest_price(historical_dir: Path, symbol: str) -> float | None:
    prices = load_prices(historical_dir / f"{symbol}.csv")
    if prices.empty:
        return None
    return as_float(prices.iloc[-1]["Close"])


def active_recommendations_df(conn: sqlite3.Connection, historical_dir: Path) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT * FROM signal_outcome_ledger
        WHERE current_status IN ('WAITING_TRIGGER','OPEN')
        ORDER BY CASE current_status WHEN 'OPEN' THEN 0 ELSE 1 END, signal_date, symbol
        """
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=[*LEDGER_COLUMNS, "current_price", "simulated_return_pct", "age_sessions"])
    output: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        current = _latest_price(historical_dir, item["symbol"])
        base_price = as_float(item.get("entry_price")) or as_float(item.get("reference_price"))
        item["current_price"] = current
        item["simulated_return_pct"] = ret_pct(current, base_price)
        prices = load_prices(historical_dir / f"{item['symbol']}.csv")
        signal_date = pd.Timestamp(item["signal_date"])
        item["age_sessions"] = int(len(prices[prices["Date"] > signal_date])) if not prices.empty else 0
        output.append(item)
    return pd.DataFrame(output)


def portfolio_df(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM portfolio_positions ORDER BY buy_date DESC, symbol, position_id",
        conn,
    )


def _resolve_signal_id(conn: sqlite3.Connection, symbol: str, signal_id: str = "") -> str:
    if norm_text(signal_id):
        found = conn.execute(
            "SELECT signal_id FROM signal_outcome_ledger WHERE signal_id=?",
            (norm_text(signal_id),),
        ).fetchone()
        if not found:
            raise ValueError(f"SIGNAL_ID_NOT_FOUND:{signal_id}")
        return str(found[0])
    found = conn.execute(
        """
        SELECT signal_id FROM signal_outcome_ledger
        WHERE symbol=? AND current_status IN ('WAITING_TRIGGER','OPEN')
        ORDER BY signal_date DESC LIMIT 1
        """,
        (normalize_symbol(symbol),),
    ).fetchone()
    return str(found[0]) if found else ""


def record_portfolio_buy(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    quantity: float,
    buy_price: float,
    buy_date: str = "",
    signal_id: str = "",
    notes: str = "",
    source_run_id: str = "",
) -> str:
    symbol = normalize_symbol(symbol)
    quantity = float(quantity)
    buy_price = float(buy_price)
    if not symbol:
        raise ValueError("SYMBOL_REQUIRED")
    if quantity <= 0 or buy_price <= 0:
        raise ValueError("QUANTITY_AND_PRICE_MUST_BE_POSITIVE")
    buy_date = parse_date(buy_date) or datetime.now().astimezone().date().isoformat()
    linked_signal = _resolve_signal_id(conn, symbol, signal_id)
    position_id = hashlib.sha256(
        "|".join([linked_signal, symbol, buy_date, f"{quantity:.8f}", f"{buy_price:.8f}"]).encode("utf-8")
    ).hexdigest()[:24]
    timestamp = now_text()
    conn.execute(
        """
        INSERT OR IGNORE INTO portfolio_positions (
            position_id, signal_id, symbol, buy_date, quantity, buy_price,
            current_status, sell_date, sell_price, realized_return_pct, notes,
            source_run_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'OPEN', NULL, NULL, NULL, ?, ?, ?, ?)
        """,
        (position_id, linked_signal, symbol, buy_date, quantity, buy_price, norm_text(notes), norm_text(source_run_id), timestamp, timestamp),
    )
    conn.commit()
    return position_id


def record_portfolio_sell(
    conn: sqlite3.Connection,
    *,
    position_id: str = "",
    symbol: str = "",
    sell_price: float,
    sell_date: str = "",
) -> str:
    sell_price = float(sell_price)
    if sell_price <= 0:
        raise ValueError("SELL_PRICE_MUST_BE_POSITIVE")
    sell_date = parse_date(sell_date) or datetime.now().astimezone().date().isoformat()
    if norm_text(position_id):
        row = conn.execute(
            "SELECT * FROM portfolio_positions WHERE position_id=? AND current_status='OPEN'",
            (norm_text(position_id),),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM portfolio_positions
            WHERE symbol=? AND current_status='OPEN'
            ORDER BY buy_date DESC LIMIT 1
            """,
            (normalize_symbol(symbol),),
        ).fetchone()
    if not row:
        raise ValueError("OPEN_PORTFOLIO_POSITION_NOT_FOUND")
    realized = ret_pct(sell_price, as_float(row["buy_price"]))
    conn.execute(
        """
        UPDATE portfolio_positions
        SET current_status='CLOSED', sell_date=?, sell_price=?, realized_return_pct=?, updated_at=?
        WHERE position_id=?
        """,
        (sell_date, sell_price, realized, now_text(), row["position_id"]),
    )
    conn.commit()
    return str(row["position_id"])


def safe_ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator * 100.0 if denominator else None


def performance_row(group: pd.DataFrame, label: str) -> dict[str, Any]:
    quality = group["data_quality_status"].astype(str).str.upper() if "data_quality_status" in group.columns else pd.Series("UNKNOWN", index=group.index)
    raw_decisions = group["raw_decision"].astype(str).str.upper() if "raw_decision" in group.columns else pd.Series("", index=group.index)
    current_recommendations = group[raw_decisions.isin(CURRENT_RECOMMENDATION_DECISIONS)].copy()
    valid = group[
        ~group["current_status"].isin({"INVALID_DATA", "INVALIDATED_BEFORE_ENTRY"})
        & quality.isin(VALID_SIGNAL_QUALITY)
    ].copy()
    excluded = len(group) - len(valid)
    invalidated = int((group["current_status"] == "INVALIDATED_BEFORE_ENTRY").sum())
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
        "Invalidated_Before_Entry": invalidated,
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


def fmt_price(value: Any, missing: str = "belum tersedia") -> str:
    """Format a price for lifecycle artifacts without relying on report helpers."""
    if value is None:
        return missing
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return missing
    if math.isnan(numeric):
        return missing
    return f"{numeric:,.0f}".replace(",", ".")


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


def _active_recommendations_telegram(active: pd.DataFrame) -> str:
    lines = [
        "📌 </b>REKOMENDASI AKTIF</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"Total aktif: {len(active)} saham",
    ]
    if active.empty:
        return "\n".join(lines + ["", "Belum ada rekomendasi aktif."])
    for status, heading in (("OPEN", "📈 </b>ACTIVE</b>"), ("WAITING_TRIGGER", "⏳ </b>WAITING ENTRY</b>")):
        subset = active[active["current_status"].astype(str).str.upper() == status]
        if subset.empty:
            continue
        lines.extend(["", heading])
        for _, row in subset.iterrows():
            symbol = html.escape(str(row.get("symbol") or ""))
            signal_date = html.escape(str(row.get("signal_date") or ""))
            current = fmt_price(row.get("current_price"))
            if status == "OPEN":
                entry = fmt_price(row.get("entry_price") or row.get("reference_price"))
                pnl = fmt(row.get("simulated_return_pct"), 2, "%")
                lines.extend([
                    "", f"</b>{symbol}</b>", f"Sinyal       : {signal_date}",
                    f"Entry mesin  : {entry}", f"Harga kini   : {current}",
                    f"P/L simulasi : {pnl}",
                    f"TP1          : {fmt_price(row.get('take_profit_1'))}",
                    f"TP2          : {fmt_price(row.get('take_profit_2'))}",
                    f"SL           : {fmt_price(row.get('stop_loss'))}",
                    f"Umur posisi  : {int(row.get('age_sessions') or 0)} sesi",
                ])
            else:
                low = fmt_price(row.get("entry_zone_low"), missing="")
                high = fmt_price(row.get("entry_zone_high"), missing="")
                entry = f"{low}–{high}" if low and high else (low or high or "belum tersedia")
                lines.extend([
                    "", f"</b>{symbol}</b>", f"Sinyal      : {signal_date}", f"Entry       : {entry}",
                    f"Harga kini  : {current}",
                    f"Status scan : {html.escape(str(row.get('latest_scan_status') or 'NOT_IN_LATEST_SCAN'))}",
                    f"Umur sinyal : {int(row.get('age_sessions') or 0)} sesi",
                ])
    return "\n".join(lines)


def _status_changes_telegram(events: list[sqlite3.Row], *, max_events: int = 20) -> str:
    material = material_lifecycle_events(events)
    if not material:
        return ""
    limit = max(int(max_events or 1), 1)
    lines = [
        "🔔 <b>LIFECYCLE DIGEST</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 <b>{len(material)} perubahan material</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    for event in material[:limit]:
        symbol = html.escape(str(event["symbol"] or ""))
        event_type = str(event["event_type"] or "").upper()
        previous = html.escape(
            str(event["previous_status"] or "-").replace("_", " ")
        )
        new = html.escape(
            str(event["new_status"] or "-").replace("_", " ")
        )
        reason_raw = str(event["event_reason"] or event_type or "")
        reason = html.escape(reason_raw.replace("_", " "))
        price = fmt_price(event["event_price"])
        raw_date = str(event["event_date"] or "")
        try:
            event_date = pd.to_datetime(raw_date).strftime("%d %b %Y")
        except Exception:
            event_date = html.escape(raw_date)
        lines.append("")
        # SYMBOL
        lines.append(f"◆ <b>{symbol}</b>")

        # EVENT TYPE
        if event_type == "ENTRY_TRIGGERED":
            lines.append("📈 <b>ENTRY TRIGGERED</b>")
            lines.append(f"🎯 Trigger : {reason.title()}")
            lines.append(f"💰 Entry   : {price}")
        elif event_type == "TP1_HIT":
            lines.append("🎯 <b>TP1 HIT</b>")
            lines.append(f"💰 Exit    : {price}")
        elif event_type == "TP2_HIT":
            lines.append("🚀 <b>TP2 HIT</b>")
            lines.append(f"💰 Exit    : {price}")
        elif event_type == "STOP_LOSS_HIT":
            lines.append("🛑 <b>STOP LOSS HIT</b>")
            lines.append(f"💰 Exit    : {price}")
        elif event_type == "MAX_HOLD_EXIT":
            lines.append("⏱ <b>MAX HOLD EXIT</b>")
            lines.append(f"💰 Exit    : {price}")
        elif event_type == "EXPIRED":
            lines.append("⌛ <b>SIGNAL EXPIRED</b>")
            lines.append(f"⚠️ Reason  : {reason}")
        elif event_type == "INVALIDATED_BEFORE_ENTRY":
            lines.append("🚫 <b>SIGNAL INVALIDATED</b>")
            lines.append(f"⚠️ Reason  : {reason}")
            lines.append(f"💰 Price   : {price}")
        else:
            lines.append(f"🔄 <b>{previous} → {new}</b>")
            if reason:
                lines.append(f"📌 Reason  : {reason}")
            if price != "belum tersedia":
                lines.append(f"💰 Price   : {price}")
        lines.append(f"📅 Date    : {event_date}")
    if len(material) > limit:
        lines.extend([
            "",
            f"… {len(material) - limit} perubahan lain tersimpan di ledger.",
        ])
    return "\n".join(lines)

def export_reports(
    conn: sqlite3.Connection,
    output_dir: Path,
    historical_dir: Path = DEFAULT_HISTORICAL,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = ledger_df(conn)
    df.to_csv(output_dir / "SIGNAL_OUTCOME_LEDGER.csv", index=False, encoding="utf-8-sig")
    active = active_recommendations_df(conn, historical_dir)
    active.to_csv(output_dir / "ACTIVE_RECOMMENDATIONS.csv", index=False, encoding="utf-8-sig")
    events = pd.read_sql_query(
        "SELECT * FROM lifecycle_events ORDER BY event_date, created_at, symbol",
        conn,
    )
    events.to_csv(output_dir / "LIFECYCLE_EVENTS.csv", index=False, encoding="utf-8-sig")
    portfolio = portfolio_df(conn)
    portfolio.to_csv(output_dir / "PORTFOLIO_POSITIONS.csv", index=False, encoding="utf-8-sig")
    pending = material_lifecycle_events(pending_lifecycle_events(conn))
    (output_dir / "ACTIVE_RECOMMENDATIONS_TELEGRAM.txt").write_text(
        _active_recommendations_telegram(active), encoding="utf-8"
    )
    (output_dir / "STATUS_CHANGES_TELEGRAM.txt").write_text(
        _status_changes_telegram(pending), encoding="utf-8"
    )
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
            "active_recommendations": str((output_dir / "ACTIVE_RECOMMENDATIONS.csv").resolve()),
            "lifecycle_events": str((output_dir / "LIFECYCLE_EVENTS.csv").resolve()),
            "portfolio_positions": str((output_dir / "PORTFOLIO_POSITIONS.csv").resolve()),
            "active_recommendations_telegram": str((output_dir / "ACTIVE_RECOMMENDATIONS_TELEGRAM.txt").resolve()),
            "status_changes_telegram": str((output_dir / "STATUS_CHANGES_TELEGRAM.txt").resolve()),
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
        print(f"Invalidated pre  : {int(row.get('Invalidated_Before_Entry', 0))}")
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


def lifecycle_telegram(args: argparse.Namespace) -> int:
    """Send only actionable lifecycle events and acknowledge them on success."""
    db_path = Path(args.db)
    output_dir = Path(args.output_dir)
    conn = connect(db_path)
    try:
        events = material_lifecycle_events(pending_lifecycle_events(conn))
    finally:
        conn.close()

    message_path = output_dir / "LIFECYCLE_DIGEST_TELEGRAM.txt"
    output_dir.mkdir(parents=True, exist_ok=True)
    limit = max(int(args.max_events or 1), 1)
    message = _status_changes_telegram(events, max_events=limit)
    message_path.write_text(message, encoding="utf-8")
    if not events:
        print("Tidak ada perubahan lifecycle material. Telegram tidak dikirim.")
        return 0
    if args.dry_run:
        print(message)
        return 0

    send_telegram(message_path, Path(args.telegram_config), Path(args.scheduler_config), False)
    # A bounded digest must not acknowledge rows that were omitted from the
    # message; they remain pending for the next maintenance send.
    event_ids = [norm_text(event["event_id"]) for event in events[:limit]]
    marked = mark_lifecycle_events_notified(db_path, event_ids)
    print(f"Lifecycle digest terkirim: {len(events)} event, acknowledged={marked}")
    return 0


def active_telegram(args: argparse.Namespace) -> int:
    """Send the latest active-recommendation snapshot to Telegram."""
    output_dir = Path(args.output_dir)
    message_path = output_dir / "ACTIVE_RECOMMENDATIONS_TELEGRAM.txt"
    active_csv = output_dir / "ACTIVE_RECOMMENDATIONS.csv"
    if not message_path.exists():
        raise FileNotFoundError("Rekomendasi aktif belum dibuat. Jalankan Update Outcome terlebih dahulu.")

    active_count: int | None = None
    if active_csv.exists():
        try:
            active_count = len(pd.read_csv(active_csv, low_memory=False))
        except Exception:
            active_count = None
    if active_count == 0:
        print("Tidak ada rekomendasi aktif. Telegram tidak dikirim.")
        return 0
    if not message_path.read_text(encoding="utf-8").strip():
        print("File rekomendasi aktif kosong. Telegram tidak dikirim.")
        return 0

    send_telegram(message_path, Path(args.telegram_config), Path(args.scheduler_config), args.dry_run)
    print("Active recommendations berhasil diproses.")
    return 0


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
    payload = export_reports(conn, Path(args.output_dir), Path(args.historical_dir))
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


def portfolio_command(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    try:
        if args.portfolio_action == "record-buy":
            position_id = record_portfolio_buy(
                conn,
                symbol=args.symbol,
                quantity=args.quantity,
                buy_price=args.price,
                buy_date=args.buy_date,
                signal_id=args.signal_id,
                notes=args.notes,
                source_run_id=args.source_run_id,
            )
            print(f"Portfolio BUY tercatat: {normalize_symbol(args.symbol)} position_id={position_id}")
            return 0
        if args.portfolio_action == "record-sell":
            position_id = record_portfolio_sell(
                conn,
                position_id=args.position_id,
                symbol=args.symbol,
                sell_price=args.price,
                sell_date=args.sell_date,
            )
            print(f"Portfolio SELL tercatat: position_id={position_id}")
            return 0
        frame = portfolio_df(conn)
        if frame.empty:
            print("Belum ada posisi portfolio.")
        else:
            print(frame.to_string(index=False, na_rep="-"))
        return 0
    finally:
        conn.close()


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

    portfolio = sub.add_parser("portfolio", help="Maintain portfolio actual pengguna")
    portfolio.add_argument("--db", default=str(DEFAULT_DB))
    portfolio_sub = portfolio.add_subparsers(dest="portfolio_action", required=True)
    buy = portfolio_sub.add_parser("record-buy", help="Catat pembelian aktual dari watchlist")
    buy.add_argument("--symbol", required=True)
    buy.add_argument("--quantity", required=True, type=float)
    buy.add_argument("--price", required=True, type=float)
    buy.add_argument("--buy-date", default="")
    buy.add_argument("--signal-id", default="")
    buy.add_argument("--notes", default="")
    buy.add_argument("--source-run-id", default="")
    sell = portfolio_sub.add_parser("record-sell", help="Catat penjualan posisi aktual")
    sell.add_argument("--position-id", default="")
    sell.add_argument("--symbol", default="")
    sell.add_argument("--price", required=True, type=float)
    sell.add_argument("--sell-date", default="")
    portfolio_sub.add_parser("list", help="Tampilkan portfolio aktual")

    show = sub.add_parser("show", help="Show a generated performance section")
    show.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    show.add_argument("--section", choices=["overall", "setup", "signal", "broker", "regime", "ledger"], default="overall")

    telegram = sub.add_parser("telegram", help="Send the latest performance report to Telegram")
    telegram.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    telegram.add_argument("--telegram-config", default=str(PROJECT_ROOT / "config/telegram.json"))
    telegram.add_argument("--scheduler-config", default=str(PROJECT_ROOT / "config/scheduler.json"))
    telegram.add_argument("--dry-run", action="store_true")

    lifecycle = sub.add_parser(
        "lifecycle-telegram",
        help="Send only material lifecycle changes to Telegram",
    )
    lifecycle.add_argument("--db", default=str(DEFAULT_DB))
    lifecycle.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    lifecycle.add_argument("--telegram-config", default=str(PROJECT_ROOT / "config/telegram.json"))
    lifecycle.add_argument("--scheduler-config", default=str(PROJECT_ROOT / "config/scheduler.json"))
    lifecycle.add_argument("--max-events", type=int, default=20)
    lifecycle.add_argument("--dry-run", action="store_true")

    active = sub.add_parser(
        "active-telegram",
        help="Send the latest active recommendations to Telegram",
    )
    active.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    active.add_argument("--telegram-config", default=str(PROJECT_ROOT / "config/telegram.json"))
    active.add_argument("--scheduler-config", default=str(PROJECT_ROOT / "config/scheduler.json"))
    active.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    load_environment_file()
    if args.command == "sync":
        return sync(args)
    if args.command == "portfolio":
        return portfolio_command(args)
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
    if args.command == "lifecycle-telegram":
        return lifecycle_telegram(args)
    if args.command == "active-telegram":
        return active_telegram(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
