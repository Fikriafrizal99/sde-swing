#!/usr/bin/env python3
"""Deterministic management layer for actual OPEN portfolio positions.

This module is intentionally isolated from Discovery, Decision, Exit, and
Outcome Tracker.  It never edits recommendation history and never closes an
actual portfolio position.  Actual BUY/SELL remains user-recorded in
``portfolio_positions``.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import find_col, normalize_symbol
from modules.job_runner.delivery import deliver
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import load_context, read_json, resolve
from modules.technical_feature_engine.technical_feature_engine import compute_features, normalize_columns

DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
DEFAULT_HISTORICAL = PROJECT_ROOT / "data/output/historical/by_symbol"
DEFAULT_BROKER = PROJECT_ROOT / "data/input/broker/BROKER_SUMMARY_LATEST.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/output/portfolio_management"
DEFAULT_SECTOR_METADATA = PROJECT_ROOT / "data/input/sector_metadata.csv"
DEFAULT_SECTOR_ROTATION = PROJECT_ROOT / "data/output/market/SECTOR_ROTATION.json"

MILESTONE_RANK = {"PRE_TARGET": 0, "TP1_HIT": 1, "TP2_HIT": 2}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS position_initial_plan (
    position_id TEXT PRIMARY KEY,
    linked_signal_id TEXT,
    symbol TEXT NOT NULL,
    buy_date TEXT NOT NULL,
    buy_price REAL NOT NULL,
    machine_entry_price REAL,
    initial_stop_loss REAL,
    initial_tp1 REAL,
    initial_tp2 REAL,
    initial_setup TEXT,
    initial_decision TEXT,
    initial_score REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS position_management_state (
    position_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    last_analysis_date TEXT,
    data_date TEXT,
    current_price REAL,
    pnl_pct REAL,
    milestone TEXT,
    technical_state TEXT,
    broker_state TEXT,
    sector_state TEXT,
    market_state TEXT,
    management_action TEXT,
    active_stop_loss REAL,
    extended_target REAL,
    reason TEXT,
    data_quality_status TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS position_management_history (
    history_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    analysis_date TEXT NOT NULL,
    data_date TEXT,
    current_price REAL,
    pnl_pct REAL,
    milestone TEXT,
    technical_state TEXT,
    broker_state TEXT,
    sector_state TEXT,
    market_state TEXT,
    management_action TEXT,
    active_stop_loss REAL,
    extended_target REAL,
    reason TEXT,
    data_quality_status TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_management_history_position_date
    ON position_management_history(position_id, analysis_date);

CREATE TABLE IF NOT EXISTS position_management_events (
    event_id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    previous_value TEXT,
    new_value TEXT,
    event_price REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_management_events_position
    ON position_management_events(position_id, event_date);
"""


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def norm_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return default if text.lower() in {"", "nan", "none", "null"} else text


def price_tick(price: float) -> float:
    if price < 200:
        return 1.0
    if price < 500:
        return 2.0
    if price < 2000:
        return 5.0
    if price < 5000:
        return 10.0
    return 25.0


def round_price(value: float | None, *, direction: str = "nearest") -> float | None:
    if value is None or value <= 0:
        return None
    tick = price_tick(value)
    units = value / tick
    if direction == "down":
        rounded = math.floor(units) * tick
    elif direction == "up":
        rounded = math.ceil(units) * tick
    else:
        rounded = round(units) * tick
    return float(max(rounded, tick))


def fmt_price(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "-"
    return f"{number:,.0f}".replace(",", ".")


def fmt_pct(value: Any) -> str:
    number = as_float(value)
    return "-" if number is None else f"{number:+.2f}%".replace(".", ",")


def load_config(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def open_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not table_exists(conn, "portfolio_positions"):
        return []
    return conn.execute(
        "SELECT * FROM portfolio_positions WHERE UPPER(current_status)='OPEN' ORDER BY buy_date, symbol, position_id"
    ).fetchall()


def signal_for_position(conn: sqlite3.Connection, position: sqlite3.Row) -> sqlite3.Row | None:
    if not table_exists(conn, "signal_outcome_ledger"):
        return None
    signal_id = norm_text(position["signal_id"] if "signal_id" in position.keys() else "")
    if signal_id:
        row = conn.execute(
            "SELECT * FROM signal_outcome_ledger WHERE signal_id=?", (signal_id,)
        ).fetchone()
        if row:
            return row
    return conn.execute(
        "SELECT * FROM signal_outcome_ledger WHERE symbol=? ORDER BY signal_date DESC, updated_at DESC LIMIT 1",
        (normalize_symbol(position["symbol"]),),
    ).fetchone()


def get_or_create_initial_plan(conn: sqlite3.Connection, position: sqlite3.Row) -> dict[str, Any]:
    existing = conn.execute(
        "SELECT * FROM position_initial_plan WHERE position_id=?", (position["position_id"],)
    ).fetchone()
    if existing:
        return dict(existing)

    signal = signal_for_position(conn, position)
    signal_map = dict(signal) if signal else {}
    payload = {
        "position_id": position["position_id"],
        "linked_signal_id": norm_text(signal_map.get("signal_id")),
        "symbol": normalize_symbol(position["symbol"]),
        "buy_date": norm_text(position["buy_date"]),
        "buy_price": as_float(position["buy_price"]) or 0.0,
        "machine_entry_price": as_float(signal_map.get("entry_price")) or as_float(signal_map.get("reference_price")),
        "initial_stop_loss": as_float(signal_map.get("stop_loss")),
        "initial_tp1": as_float(signal_map.get("take_profit_1")),
        "initial_tp2": as_float(signal_map.get("take_profit_2")),
        "initial_setup": norm_text(signal_map.get("setup_type")),
        "initial_decision": norm_text(signal_map.get("raw_decision")),
        "initial_score": as_float(signal_map.get("score")),
        "created_at": now_text(),
    }
    conn.execute(
        """
        INSERT INTO position_initial_plan (
            position_id, linked_signal_id, symbol, buy_date, buy_price,
            machine_entry_price, initial_stop_loss, initial_tp1, initial_tp2,
            initial_setup, initial_decision, initial_score, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(payload[key] for key in (
            "position_id", "linked_signal_id", "symbol", "buy_date", "buy_price",
            "machine_entry_price", "initial_stop_loss", "initial_tp1", "initial_tp2",
            "initial_setup", "initial_decision", "initial_score", "created_at",
        )),
    )
    conn.commit()
    return payload


def previous_state(conn: sqlite3.Connection, position_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM position_management_state WHERE position_id=?", (position_id,)
    ).fetchone()
    return dict(row) if row else {}


def history_path(historical_dir: Path, symbol: str) -> Path | None:
    for candidate in (historical_dir / f"{symbol}.csv", historical_dir / f"{symbol}.JK.csv"):
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def technical_snapshot(historical_dir: Path, symbol: str, buy_date: str) -> dict[str, Any]:
    path = history_path(historical_dir, symbol)
    if path is None:
        return {"status": "MISSING", "reason": "HISTORICAL_FILE_NOT_FOUND", "symbol": symbol}
    try:
        raw = normalize_columns(pd.read_csv(path, low_memory=False))
        features = compute_features(raw, symbol)
    except Exception as exc:
        return {"status": "INVALID", "reason": f"TECHNICAL_COMPUTE_FAILED:{type(exc).__name__}:{exc}", "symbol": symbol}
    if features.empty:
        return {"status": "INVALID", "reason": "TECHNICAL_EMPTY", "symbol": symbol}

    features = features.sort_values("Date").reset_index(drop=True)
    latest = features.iloc[-1]
    feature_dates = pd.to_datetime(features["Date"], errors="coerce").dt.normalize()
    buy_ts = pd.to_datetime(buy_date, errors="coerce")
    buy_day = buy_ts.normalize() if pd.notna(buy_ts) else pd.NaT

    # Daily candles do not reveal whether the buy happened before or after the
    # intraday High/Low on the BUY date.  Never use that day's High/Low to
    # infer TP/SL events.  The BUY-date Close is safe as an end-of-session
    # observation, while all later sessions may use their full High/Low range.
    if pd.notna(buy_day):
        later = features.loc[feature_dates > buy_day].copy()
        buy_rows = features.loc[feature_dates == buy_day]
        buy_close = as_float(buy_rows.iloc[-1].get("Close")) if not buy_rows.empty else None
    else:
        later = features.copy()
        buy_close = None

    event_highs = pd.to_numeric(later.get("High", pd.Series(dtype=float)), errors="coerce").dropna().tolist()
    event_lows = pd.to_numeric(later.get("Low", pd.Series(dtype=float)), errors="coerce").dropna().tolist()
    if buy_close is not None:
        event_highs.append(buy_close)
        event_lows.append(buy_close)
    max_high_since_buy = max(event_highs) if event_highs else None
    min_low_since_buy = min(event_lows) if event_lows else None

    current = as_float(latest.get("Close"))
    sma20 = as_float(latest.get("SMA_20"))
    sma50 = as_float(latest.get("SMA_50"))
    atr = as_float(latest.get("ATR_14"))
    rsi = as_float(latest.get("RSI_14"))
    slope = as_float(latest.get("Slope_SMA20_10"))
    regime = norm_text(latest.get("Technical_Regime"), "neutral").upper()
    trend_votes = sum(
        int(as_float(latest.get(name)) or 0) > 0
        for name in ("Above_SMA20", "Above_SMA50", "SMA20_Above_SMA50", "MACD_Bullish")
    )
    overextended = (as_float(latest.get("Distance_SMA_20_Pct")) or 0.0) >= 8.0
    strong_bullish = (
        regime == "BULLISH"
        and trend_votes >= 3
        and (rsi is None or 45.0 <= rsi <= 78.0)
        and (slope is None or slope >= 0)
        and not overextended
    )
    bearish = regime == "BEARISH" or bool(
        current is not None
        and sma20 is not None
        and sma50 is not None
        and current < sma20
        and current < sma50
        and int(as_float(latest.get("MACD_Bullish")) or 0) == 0
    )
    return {
        "status": "VALID",
        "path": str(path),
        "data_date": pd.Timestamp(latest["Date"]).date().isoformat(),
        "current_price": current,
        "sma20": sma20,
        "sma50": sma50,
        "atr": atr,
        "rsi": rsi,
        "volume_ratio_20": as_float(latest.get("Volume_Ratio_20")),
        "technical_regime": regime,
        "strong_bullish": strong_bullish,
        "bearish": bearish,
        "overextended": overextended,
        "max_high_since_buy": max_high_since_buy,
        "min_low_since_buy": min_low_since_buy,
        "buy_day_range_policy": "CLOSE_ONLY_ON_BUY_DATE",
    }


def load_broker_row(path: Path, symbol: str) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {"state": "UNAVAILABLE", "score": None, "net_flow": None, "raw_signal": ""}
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return {"state": "UNAVAILABLE", "score": None, "net_flow": None, "raw_signal": ""}
    if frame.empty:
        return {"state": "UNAVAILABLE", "score": None, "net_flow": None, "raw_signal": ""}
    symbol_col = find_col(frame, "EMITEN", "Symbol", "Ticker", "Code")
    if not symbol_col:
        return {"state": "UNAVAILABLE", "score": None, "net_flow": None, "raw_signal": ""}
    normalized = frame[symbol_col].astype(str).str.upper().str.replace(".JK", "", regex=False).str.strip()
    subset = frame.loc[normalized == symbol]
    if subset.empty:
        return {"state": "UNAVAILABLE", "score": None, "net_flow": None, "raw_signal": ""}
    row = subset.iloc[-1]
    signal_col = find_col(frame, "BROKER_SIGNAL", "SIGNAL", "BROKER_PATTERN", "ACC_DIST", "BROKER_ACCDIST")
    score_col = find_col(frame, "BROKER_SCORE", "SCORE", "Broker Score")
    net_col = find_col(frame, "NET_FLOW", "NET FLOW", "NETFLOW", "NET_VALUE")
    raw_signal = norm_text(row.get(signal_col)) if signal_col else ""
    score = as_float(row.get(score_col)) if score_col else None
    net_flow = as_float(row.get(net_col)) if net_col else None
    text = raw_signal.upper()
    if any(marker in text for marker in ("DISTRIB", "SELL", "BEAR")) or (score is not None and score < 40 and (net_flow or 0) < 0):
        state = "DISTRIBUTION"
    elif any(marker in text for marker in ("ACCUM", "BUY", "BULL")) or (score is not None and score >= 60 and (net_flow is None or net_flow >= 0)):
        state = "ACCUMULATION"
    else:
        state = "NEUTRAL"
    return {"state": state, "score": score, "net_flow": net_flow, "raw_signal": raw_signal}


def sector_state(symbol: str, metadata_path: Path, rotation_path: Path) -> tuple[str, str]:
    if not metadata_path.exists() or not rotation_path.exists():
        return "UNAVAILABLE", ""
    try:
        meta = pd.read_csv(metadata_path, low_memory=False)
        rotation = json.loads(rotation_path.read_text(encoding="utf-8"))
    except Exception:
        return "UNAVAILABLE", ""
    symbol_col = find_col(meta, "Symbol", "Ticker", "EMITEN")
    sector_col = find_col(meta, "Sector", "Sektor", "Sector_Name", "SubSector", "Sub_Sector", "Industry")
    if not symbol_col or not sector_col:
        return "UNAVAILABLE", ""
    symbols = meta[symbol_col].astype(str).str.upper().str.replace(".JK", "", regex=False).str.strip()
    row = meta.loc[symbols == symbol]
    if row.empty:
        return "UNAVAILABLE", ""
    sector = norm_text(row.iloc[-1].get(sector_col))
    if not sector:
        return "UNAVAILABLE", ""
    for bucket in ("leading", "improving", "weakening", "lagging"):
        names = {norm_text(item).lower() for item in rotation.get(bucket, [])}
        if sector.lower() in names:
            return bucket.upper(), sector
    return "NEUTRAL", sector


def market_state(trade_date: str) -> str:
    path = PROJECT_ROOT / "data/output/market_regime" / trade_date / "market_outlook_regime.json"
    payload = read_json(path)
    value = payload.get("market_regime") or payload.get("regime") or payload.get("state") or "UNAVAILABLE"
    return norm_text(value, "UNAVAILABLE").upper()


def observed_milestone(plan: dict[str, Any], tech: dict[str, Any], previous: dict[str, Any]) -> str:
    high = as_float(tech.get("max_high_since_buy"))
    tp1 = as_float(plan.get("initial_tp1"))
    tp2 = as_float(plan.get("initial_tp2"))
    observed = "PRE_TARGET"
    if high is not None and tp2 is not None and high >= tp2:
        observed = "TP2_HIT"
    elif high is not None and tp1 is not None and high >= tp1:
        observed = "TP1_HIT"
    prior = norm_text(previous.get("milestone"), "PRE_TARGET").upper()
    return prior if MILESTONE_RANK.get(prior, 0) > MILESTONE_RANK.get(observed, 0) else observed


def safe_stop_candidate(current: float, candidates: list[float | None]) -> float | None:
    valid = [value for value in (as_float(item) for item in candidates) if value is not None and value > 0 and value < current]
    if not valid:
        return None
    return round_price(max(valid), direction="down")


def choose_management(
    *,
    plan: dict[str, Any],
    tech: dict[str, Any],
    broker: dict[str, Any],
    sector: str,
    market: str,
    previous: dict[str, Any],
) -> dict[str, Any]:
    if tech.get("status") != "VALID" or as_float(tech.get("current_price")) is None:
        return {
            "action": "REVIEW_DATA",
            "milestone": norm_text(previous.get("milestone"), "PRE_TARGET"),
            "active_stop_loss": as_float(previous.get("active_stop_loss")) or as_float(plan.get("initial_stop_loss")),
            "extended_target": as_float(previous.get("extended_target")),
            "reason": norm_text(tech.get("reason"), "Technical data tidak tersedia."),
            "data_quality_status": "INVALID_TECHNICAL_DATA",
        }

    current = float(tech["current_price"])
    buy = as_float(plan.get("buy_price")) or current
    initial_stop = as_float(plan.get("initial_stop_loss"))
    prior_stop = as_float(previous.get("active_stop_loss"))
    prior_target = as_float(previous.get("extended_target"))
    milestone = observed_milestone(plan, tech, previous)
    broker_state = norm_text(broker.get("state"), "UNAVAILABLE").upper()
    strong_bull = bool(tech.get("strong_bullish"))
    bearish = bool(tech.get("bearish"))
    market_bearish = "BEAR" in market
    sector_weak = sector in {"WEAKENING", "LAGGING"}
    initial_stop_breached = (
        initial_stop is not None
        and as_float(tech.get("min_low_since_buy")) is not None
        and float(tech["min_low_since_buy"]) <= initial_stop
    )

    base_stop = max([x for x in (initial_stop, prior_stop) if x is not None], default=None)
    action = "HOLD"
    reason_parts: list[str] = []

    if initial_stop_breached:
        action = "EXIT"
        reason_parts.append("Initial stop pernah terlewati; thesis awal sudah invalid.")
    elif base_stop is not None and current <= base_stop:
        action = "EXIT"
        reason_parts.append("Harga berada di/bawah active stop.")
    elif bearish and broker_state == "DISTRIBUTION":
        action = "EXIT"
        reason_parts.append("Teknikal bearish dan broker distribution selaras.")
    elif milestone == "TP2_HIT":
        if strong_bull and broker_state != "DISTRIBUTION" and not market_bearish and not sector_weak:
            action = "HOLD_AFTER_TP2"
            reason_parts.append("TP2 tercapai, tetapi struktur continuation masih kuat.")
        elif bearish:
            action = "PROTECT_PROFIT"
            reason_parts.append("TP2 tercapai dan momentum teknikal melemah.")
        elif broker_state == "DISTRIBUTION" or market_bearish or sector_weak:
            action = "PROTECT_PROFIT"
            reason_parts.append("TP2 tercapai; konteks broker/market/sector meminta proteksi profit.")
        else:
            action = "PROTECT_PROFIT"
            reason_parts.append("TP2 tercapai; lanjut dengan trailing protection.")
    elif milestone == "TP1_HIT":
        if strong_bull and broker_state != "DISTRIBUTION":
            action = "HOLD_AFTER_TP1"
            reason_parts.append("TP1 tercapai dan continuation masih sehat.")
        elif bearish or broker_state == "DISTRIBUTION":
            action = "PROTECT_PROFIT"
            reason_parts.append("TP1 tercapai, tetapi momentum/broker melemah.")
        else:
            action = "HOLD"
            reason_parts.append("TP1 tercapai; posisi tetap valid dengan proteksi risiko.")
    else:
        if strong_bull and broker_state == "ACCUMULATION" and not market_bearish:
            action = "HOLD_STRONG"
            reason_parts.append("Trend kuat dan broker accumulation mendukung posisi.")
        elif bearish:
            action = "PROTECT_PROFIT" if current > buy else "EXIT"
            reason_parts.append("Struktur teknikal melemah sebelum target tercapai.")
        elif broker_state == "DISTRIBUTION":
            action = "PROTECT_PROFIT"
            reason_parts.append("Broker distribution muncul; risiko dinaikkan.")
        else:
            action = "HOLD"
            reason_parts.append("Thesis belum invalid dan target awal belum selesai.")

    atr = as_float(tech.get("atr"))
    sma20 = as_float(tech.get("sma20"))
    stop_candidates: list[float | None] = [base_stop]
    if milestone in {"TP1_HIT", "TP2_HIT"}:
        stop_candidates.append(buy)
    if milestone == "TP2_HIT":
        stop_candidates.extend([sma20, current - (2.0 * atr) if atr else None])
    if action == "PROTECT_PROFIT":
        stop_candidates.extend([
            buy if current > buy else None,
            sma20,
            current - (1.5 * atr) if atr else None,
        ])
    active_stop = safe_stop_candidate(current, stop_candidates)
    if prior_stop is not None and (active_stop is None or prior_stop > active_stop) and prior_stop < current:
        active_stop = round_price(prior_stop, direction="down")

    extended_target = prior_target
    if milestone == "TP2_HIT" and action == "HOLD_AFTER_TP2":
        tp1 = as_float(plan.get("initial_tp1"))
        tp2 = as_float(plan.get("initial_tp2"))
        gap = (tp2 - tp1) if tp1 is not None and tp2 is not None and tp2 > tp1 else 0.0
        extension = max(
            2.0 * atr if atr else 0.0,
            0.5 * gap,
            current * 0.05,
        )
        proposed = round_price(current + extension, direction="up")
        if proposed is not None:
            extended_target = max(proposed, prior_target or 0.0)

    if broker_state == "UNAVAILABLE":
        reason_parts.append("Broker data belum tersedia untuk posisi ini.")
    if sector == "UNAVAILABLE":
        reason_parts.append("Sector context belum tersedia.")
    if bool(tech.get("overextended")):
        reason_parts.append("Harga relatif jauh dari SMA20; jangan tambah posisi.")

    return {
        "action": action,
        "milestone": milestone,
        "active_stop_loss": active_stop,
        "extended_target": extended_target,
        "reason": " ".join(reason_parts),
        "data_quality_status": "VALID" if broker_state != "UNAVAILABLE" else "VALID_WITH_BROKER_WARNING",
    }


def event_key(position_id: str, event_date: str, event_type: str, new_value: str) -> str:
    raw = "|".join([position_id, event_date, event_type, new_value])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def persist_result(conn: sqlite3.Connection, result: dict[str, Any], previous: dict[str, Any]) -> None:
    timestamp = now_text()
    conn.execute(
        """
        INSERT INTO position_management_state (
            position_id, symbol, last_analysis_date, data_date, current_price, pnl_pct,
            milestone, technical_state, broker_state, sector_state, market_state,
            management_action, active_stop_loss, extended_target, reason,
            data_quality_status, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(position_id) DO UPDATE SET
            symbol=excluded.symbol,
            last_analysis_date=excluded.last_analysis_date,
            data_date=excluded.data_date,
            current_price=excluded.current_price,
            pnl_pct=excluded.pnl_pct,
            milestone=excluded.milestone,
            technical_state=excluded.technical_state,
            broker_state=excluded.broker_state,
            sector_state=excluded.sector_state,
            market_state=excluded.market_state,
            management_action=excluded.management_action,
            active_stop_loss=excluded.active_stop_loss,
            extended_target=excluded.extended_target,
            reason=excluded.reason,
            data_quality_status=excluded.data_quality_status,
            updated_at=excluded.updated_at
        """,
        (
            result["position_id"], result["symbol"], result["analysis_date"], result["data_date"],
            result["current_price"], result["pnl_pct"], result["milestone"], result["technical_state"],
            result["broker_state"], result["sector_state"], result["market_state"], result["management_action"],
            result["active_stop_loss"], result["extended_target"], result["reason"],
            result["data_quality_status"], timestamp,
        ),
    )
    history_id = hashlib.sha256(
        f"{result['position_id']}|{result['analysis_date']}".encode("utf-8")
    ).hexdigest()[:32]
    conn.execute(
        """
        INSERT INTO position_management_history (
            history_id, position_id, symbol, analysis_date, data_date, current_price,
            pnl_pct, milestone, technical_state, broker_state, sector_state,
            market_state, management_action, active_stop_loss, extended_target,
            reason, data_quality_status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(history_id) DO UPDATE SET
            data_date=excluded.data_date,
            current_price=excluded.current_price,
            pnl_pct=excluded.pnl_pct,
            milestone=excluded.milestone,
            technical_state=excluded.technical_state,
            broker_state=excluded.broker_state,
            sector_state=excluded.sector_state,
            market_state=excluded.market_state,
            management_action=excluded.management_action,
            active_stop_loss=excluded.active_stop_loss,
            extended_target=excluded.extended_target,
            reason=excluded.reason,
            data_quality_status=excluded.data_quality_status,
            created_at=excluded.created_at
        """,
        (
            history_id, result["position_id"], result["symbol"], result["analysis_date"], result["data_date"],
            result["current_price"], result["pnl_pct"], result["milestone"], result["technical_state"],
            result["broker_state"], result["sector_state"], result["market_state"], result["management_action"],
            result["active_stop_loss"], result["extended_target"], result["reason"],
            result["data_quality_status"], timestamp,
        ),
    )

    changes = (
        ("MILESTONE_CHANGED", norm_text(previous.get("milestone")), result["milestone"]),
        ("ACTION_CHANGED", norm_text(previous.get("management_action")), result["management_action"]),
    )
    for event_type, old, new in changes:
        if old and old != new:
            identifier = event_key(result["position_id"], result["analysis_date"], event_type, new)
            conn.execute(
                """
                INSERT OR IGNORE INTO position_management_events (
                    event_id, position_id, symbol, event_date, event_type,
                    previous_value, new_value, event_price, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (identifier, result["position_id"], result["symbol"], result["analysis_date"], event_type,
                 old, new, result["current_price"], timestamp),
            )
    conn.commit()


def analyze_position(
    conn: sqlite3.Connection,
    position: sqlite3.Row,
    *,
    analysis_date: str,
    historical_dir: Path,
    broker_path: Path,
    sector_metadata_path: Path,
    sector_rotation_path: Path,
    market: str,
) -> dict[str, Any]:
    symbol = normalize_symbol(position["symbol"])
    plan = get_or_create_initial_plan(conn, position)
    previous = previous_state(conn, position["position_id"])
    tech = technical_snapshot(historical_dir, symbol, plan["buy_date"])
    broker = load_broker_row(broker_path, symbol)
    sector_bucket, sector_name = sector_state(symbol, sector_metadata_path, sector_rotation_path)
    decision = choose_management(
        plan=plan,
        tech=tech,
        broker=broker,
        sector=sector_bucket,
        market=market,
        previous=previous,
    )
    current = as_float(tech.get("current_price"))
    buy_price = as_float(plan.get("buy_price"))
    pnl_pct = ((current / buy_price - 1.0) * 100.0) if current is not None and buy_price else None
    data_date = norm_text(tech.get("data_date"))
    data_quality = decision["data_quality_status"]
    if data_date and analysis_date and data_date != analysis_date:
        data_quality = "STALE_PRICE_DATA"
        decision["action"] = "REVIEW_DATA"
        decision["reason"] = f"Data harga terakhir {data_date}, berbeda dari analysis date {analysis_date}. " + decision["reason"]

    result = {
        "position_id": position["position_id"],
        "symbol": symbol,
        "analysis_date": analysis_date,
        "data_date": data_date,
        "buy_date": plan["buy_date"],
        "buy_price": buy_price,
        "current_price": current,
        "pnl_pct": pnl_pct,
        "initial_stop_loss": as_float(plan.get("initial_stop_loss")),
        "initial_tp1": as_float(plan.get("initial_tp1")),
        "initial_tp2": as_float(plan.get("initial_tp2")),
        "initial_setup": norm_text(plan.get("initial_setup")),
        "linked_signal_id": norm_text(plan.get("linked_signal_id")),
        "milestone": decision["milestone"],
        "technical_state": norm_text(tech.get("technical_regime"), "UNAVAILABLE"),
        "broker_state": broker["state"],
        "broker_score": broker.get("score"),
        "sector_state": sector_bucket,
        "sector_name": sector_name,
        "market_state": market,
        "management_action": decision["action"],
        "active_stop_loss": decision["active_stop_loss"],
        "extended_target": decision["extended_target"],
        "reason": decision["reason"],
        "data_quality_status": data_quality,
    }
    persist_result(conn, result, previous)
    return result


def telegram_text(results: list[dict[str, Any]], analysis_date: str) -> str:
    lines = [
        "📊 <b>SDE SWING — ACTIVE PORTFOLIO</b>",
        f"🕒 {html.escape(analysis_date)}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    for index, row in enumerate(results):
        if index:
            lines.extend(["", "━━━━━━━━━━━━━━━━━━━"])
        tp1_mark = " ✅" if MILESTONE_RANK.get(row["milestone"], 0) >= 1 and row.get("initial_tp1") else ""
        tp2_mark = " ✅" if MILESTONE_RANK.get(row["milestone"], 0) >= 2 and row.get("initial_tp2") else ""
        lines.extend([
            f"📌 <b>{html.escape(row['symbol'])}</b>",
            f"Buy / Current : {fmt_price(row.get('buy_price'))} / {fmt_price(row.get('current_price'))}",
            f"P/L           : {fmt_pct(row.get('pnl_pct'))}",
            "",
            "🎯 <b>INITIAL PLAN</b>",
            f"TP1 : {fmt_price(row.get('initial_tp1'))}{tp1_mark}",
            f"TP2 : {fmt_price(row.get('initial_tp2'))}{tp2_mark}",
            f"SL  : {fmt_price(row.get('initial_stop_loss'))}",
            "",
            "📈 <b>CURRENT</b>",
            f"Technical : {html.escape(row.get('technical_state') or '-')}",
            f"Broker    : {html.escape(row.get('broker_state') or '-')}",
            f"Sector    : {html.escape(row.get('sector_state') or '-')}",
            f"Market    : {html.escape(row.get('market_state') or '-')}",
            "",
            "🛡️ <b>MANAGEMENT</b>",
            f"Action          : <b>{html.escape(row.get('management_action') or '-')}</b>",
            f"Active SL       : {fmt_price(row.get('active_stop_loss'))}",
            f"Extended Target : {fmt_price(row.get('extended_target'))}",
            f"Data            : {html.escape(row.get('data_quality_status') or '-')}",
            "",
            "🧠 " + html.escape(row.get("reason") or "-"),
        ])
    return "\n".join(lines)


def write_outputs(output_root: Path, analysis_date: str, results: list[dict[str, Any]], text: str) -> dict[str, str]:
    folder = output_root / analysis_date
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / "ACTIVE_PORTFOLIO_MANAGEMENT.csv"
    json_path = folder / "ACTIVE_PORTFOLIO_MANAGEMENT.json"
    txt_path = folder / "ACTIVE_PORTFOLIO_TELEGRAM.txt"
    pd.DataFrame(results).to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    latest = output_root / "latest"
    latest.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(latest / csv_path.name, index=False, encoding="utf-8-sig")
    (latest / json_path.name).write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
    (latest / txt_path.name).write_text(text, encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path), "telegram": str(txt_path)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze actual OPEN portfolio positions without changing the main SDE engines")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--db", default="")
    parser.add_argument("--historical-dir", default="")
    parser.add_argument("--broker-summary", default="")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve(args.config)
    config = load_config(config_path)
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    db_path = resolve(args.db or paths.get("swing_database", str(DEFAULT_DB)))
    historical_dir = resolve(args.historical_dir or paths.get("historical_dir", str(DEFAULT_HISTORICAL)))
    broker_path = resolve(args.broker_summary or paths.get("broker_summary_latest", str(DEFAULT_BROKER)))
    sector_metadata_path = resolve(paths.get("sector_rotation_metadata", str(DEFAULT_SECTOR_METADATA)))
    sector_rotation_path = resolve(paths.get("sector_rotation_output", str(DEFAULT_SECTOR_ROTATION)))
    output_root = resolve(args.output_dir)

    ctx = load_context(
        job="position_management",
        config_path=str(config_path),
        scheduler_config_path=args.scheduler_config,
        trade_date=args.trade_date or None,
        dry_run=args.dry_run,
        preview_existing=False,
        no_telegram=(args.no_telegram or not args.telegram),
        force=args.force,
        debug=False,
        interactive_broker=False,
    )
    analysis_date = ctx.trade_date.isoformat()
    market = market_state(analysis_date)

    conn = connect(db_path)
    try:
        positions = open_positions(conn)
        if not positions:
            output_root.mkdir(parents=True, exist_ok=True)
            message = "📊 <b>SDE SWING — ACTIVE PORTFOLIO</b>\n\nTidak ada posisi aktual OPEN."
            outputs = write_outputs(output_root, analysis_date, [], message)
            print("POSITION MANAGEMENT: SKIPPED_NO_OPEN_POSITION")
            print(f"Output: {outputs['json']}")
            return 0

        results = [
            analyze_position(
                conn,
                position,
                analysis_date=analysis_date,
                historical_dir=historical_dir,
                broker_path=broker_path,
                sector_metadata_path=sector_metadata_path,
                sector_rotation_path=sector_rotation_path,
                market=market,
            )
            for position in positions
        ]
    finally:
        conn.close()

    message = telegram_text(results, analysis_date)
    outputs = write_outputs(output_root, analysis_date, results, message)
    print(f"POSITION MANAGEMENT: {len(results)} OPEN position(s) analyzed")
    for row in results:
        print(
            f"  {row['symbol']}: {row['management_action']} | "
            f"P/L={fmt_pct(row['pnl_pct'])} | milestone={row['milestone']} | "
            f"active_sl={fmt_price(row['active_stop_loss'])}"
        )
    print(f"Output: {outputs['json']}")

    if args.telegram and not args.no_telegram:
        payload = ReportPayload(
            report_type="POSITION_MANAGEMENT",
            filename="active_portfolio_management.txt",
            text=message,
            topic="position_management",
            signal_status="ACTIVE_PORTFOLIO",
            signal_version=analysis_date,
            input_paths=(str(db_path), str(historical_dir), str(broker_path)),
            source_of_truth=("portfolio_positions", "shared_technical_features", "broker_summary"),
            row_count=len(results),
        )
        delivery = deliver(ctx, [payload])
        failed = [item for item in delivery if item.get("status") == "FAILED"]
        if failed:
            print(f"Telegram delivery failed: {failed}", file=sys.stderr)
            return 50
        print("Telegram: processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
