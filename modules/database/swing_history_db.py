#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import (
    clean_dataframe_for_json,
    ensure_dir,
    file_sha256,
    find_col,
    make_run_id,
    normalize_symbol,
    read_json,
    write_json,
)

ENTRY_WATCHLIST_DECISIONS = {"STRONG BUY", "BUY", "BUY CANDIDATE", "WATCH HIGH", "WATCH"}


def connect(db_path: Path) -> sqlite3.Connection:
    ensure_dir(db_path.parent)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id TEXT PRIMARY KEY,
            strategy_type TEXT,
            pipeline_version TEXT,
            started_at TEXT,
            finished_at TEXT,
            status TEXT,
            technical_date TEXT,
            broker_date TEXT,
            market_date TEXT,
            data_mode TEXT,
            fallback_used INTEGER,
            broker_date_override INTEGER,
            broker_coverage REAL,
            data_quality_status TEXT,
            warning TEXT,
            error_message TEXT,
            manifest_json TEXT
        );

        CREATE TABLE IF NOT EXISTS provider_runs (
            run_id TEXT,
            provider TEXT,
            requested_start TEXT,
            requested_end TEXT,
            latest_expected_date TEXT,
            latest_valid_date TEXT,
            symbol_total INTEGER,
            symbol_updated INTEGER,
            symbol_partial INTEGER,
            symbol_failed INTEGER,
            fallback_used INTEGER,
            status TEXT,
            manifest_json TEXT,
            PRIMARY KEY (run_id, provider)
        );

        CREATE TABLE IF NOT EXISTS market_prices_daily (
            symbol TEXT NOT NULL,
            price_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adjusted_close REAL,
            volume REAL,
            source TEXT NOT NULL,
            source_revision TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (symbol, price_date, source)
        );

        CREATE TABLE IF NOT EXISTS run_data_snapshots (
            run_id TEXT,
            dataset_type TEXT,
            source_path TEXT,
            dataset_hash TEXT,
            row_count INTEGER,
            latest_data_date TEXT,
            created_at TEXT,
            PRIMARY KEY (run_id, dataset_type, dataset_hash)
        );

        CREATE TABLE IF NOT EXISTS technical_features (
            symbol TEXT,
            feature_date TEXT,
            engine_version TEXT,
            run_id TEXT,
            technical_score REAL,
            technical_quality_check TEXT,
            data_quality_status TEXT,
            row_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (symbol, feature_date, engine_version)
        );

        CREATE TABLE IF NOT EXISTS candidates (
            run_id TEXT,
            symbol TEXT,
            rank INTEGER,
            technical_score REAL,
            technical_score_v2 REAL,
            technical_quality_check TEXT,
            technical_score_final REAL,
            candidate_status TEXT,
            candidate_hash TEXT,
            row_json TEXT,
            PRIMARY KEY (run_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS broker_snapshots (
            broker_snapshot_id TEXT PRIMARY KEY,
            broker_date TEXT,
            from_date TEXT,
            to_date TEXT,
            source_files TEXT,
            coverage REAL,
            snapshot_hash TEXT,
            data_quality_status TEXT,
            manifest_json TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS broker_raw (
            broker_snapshot_id TEXT,
            symbol TEXT,
            broker_code TEXT,
            side TEXT,
            row_json TEXT,
            PRIMARY KEY (broker_snapshot_id, symbol, broker_code, side)
        );

        CREATE TABLE IF NOT EXISTS broker_summary (
            broker_snapshot_id TEXT,
            symbol TEXT,
            row_json TEXT,
            PRIMARY KEY (broker_snapshot_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS broker_status (
            broker_snapshot_id TEXT,
            symbol TEXT,
            status TEXT,
            row_json TEXT,
            PRIMARY KEY (broker_snapshot_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS fusion_results (
            run_id TEXT,
            symbol TEXT,
            rank INTEGER,
            row_json TEXT,
            PRIMARY KEY (run_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS decision_results (
            run_id TEXT,
            symbol TEXT,
            rank INTEGER,
            decision TEXT,
            final_score REAL,
            row_json TEXT,
            PRIMARY KEY (run_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS entry_exit_results (
            run_id TEXT,
            symbol TEXT,
            entry REAL,
            stop_loss REAL,
            take_profit_1 REAL,
            take_profit_2 REAL,
            entry_status TEXT,
            exit_status TEXT,
            exit_reason TEXT,
            holding_period INTEGER,
            row_json TEXT,
            PRIMARY KEY (run_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS watchlist_history (
            run_id TEXT,
            symbol TEXT,
            signal_date TEXT,
            lifecycle TEXT,
            decision TEXT,
            final_score REAL,
            data_quality_status TEXT,
            row_json TEXT,
            PRIMARY KEY (run_id, symbol)
        );

        CREATE TABLE IF NOT EXISTS watchlist_outcomes (
            signal_id TEXT PRIMARY KEY,
            run_id TEXT,
            symbol TEXT,
            signal_date TEXT,
            decision TEXT,
            reference_price REAL,
            entry_price REAL,
            stop_loss REAL,
            take_profit_1 REAL,
            take_profit_2 REAL,
            close_d1 REAL,
            close_d3 REAL,
            close_d5 REAL,
            close_d7 REAL,
            return_d1 REAL,
            return_d3 REAL,
            return_d5 REAL,
            return_d7 REAL,
            max_price_d7 REAL,
            min_price_d7 REAL,
            mfe REAL,
            mae REAL,
            tp1_hit INTEGER,
            tp2_hit INTEGER,
            sl_hit INTEGER,
            final_outcome TEXT,
            data_quality_status TEXT
        );

        CREATE TABLE IF NOT EXISTS telegram_logs (
            run_id TEXT,
            message_type TEXT,
            sent_at TEXT,
            status TEXT,
            telegram_message_id TEXT,
            error TEXT,
            preview_path TEXT,
            PRIMARY KEY (run_id, message_type, sent_at)
        );

        CREATE TABLE IF NOT EXISTS archived_source_files (
            dataset_type TEXT,
            source_path TEXT,
            source_revision TEXT,
            row_count INTEGER,
            archived_at TEXT,
            PRIMARY KEY (dataset_type, source_path, source_revision)
        );
        """
    )
    conn.commit()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def to_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def to_int(value: Any) -> int | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(float(str(value).replace(",", "")))
    except Exception:
        return None


def upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], keys: list[str]) -> None:
    columns = list(row)
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c not in keys)
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    if updates:
        sql += f" ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}"
    else:
        sql += f" ON CONFLICT({','.join(keys)}) DO NOTHING"
    conn.execute(sql, [row[c] for c in columns])


def upsert_many(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]], keys: list[str]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c not in keys)
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    if updates:
        sql += f" ON CONFLICT({','.join(keys)}) DO UPDATE SET {updates}"
    else:
        sql += f" ON CONFLICT({','.join(keys)}) DO NOTHING"
    conn.executemany(sql, [[row.get(c) for c in columns] for row in rows])


def source_file_already_archived(conn: sqlite3.Connection, dataset_type: str, path: Path, revision: str) -> bool:
    found = conn.execute(
        """
        SELECT 1 FROM archived_source_files
        WHERE dataset_type=? AND source_path=? AND source_revision=?
        LIMIT 1
        """,
        (dataset_type, str(path.resolve()), revision),
    ).fetchone()
    return bool(found)


def mark_source_file_archived(conn: sqlite3.Connection, dataset_type: str, path: Path, revision: str, row_count: int) -> None:
    upsert(conn, "archived_source_files", {
        "dataset_type": dataset_type,
        "source_path": str(path.resolve()),
        "source_revision": revision,
        "row_count": int(row_count),
        "archived_at": datetime.now().isoformat(timespec="seconds"),
    }, ["dataset_type", "source_path", "source_revision"])


def load_csv(path: Path | str | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def latest_data_date(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    col = find_col(df, "Date", "Technical_Data_Date", "TO_DATE", "Broker_Data_Date", "Signal_Date")
    if not col:
        return ""
    parsed = pd.to_datetime(df[col], errors="coerce")
    valid = parsed.dropna()
    return valid.max().date().isoformat() if not valid.empty else ""


def add_snapshot(conn: sqlite3.Connection, run_id: str, dataset_type: str, path: Path | str | None) -> None:
    if not path:
        return
    p = Path(path)
    if not p.exists() or p.is_dir():
        return
    df = load_csv(p) if p.suffix.lower() == ".csv" else pd.DataFrame()
    row = {
        "run_id": run_id,
        "dataset_type": dataset_type,
        "source_path": str(p.resolve()),
        "dataset_hash": file_sha256(p),
        "row_count": int(len(df)) if not df.empty else 0,
        "latest_data_date": latest_data_date(df),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    upsert(conn, "run_data_snapshots", row, ["run_id", "dataset_type", "dataset_hash"])


def archive_prices(conn: sqlite3.Connection, historical_dir: Path, source: str = "YAHOO") -> int:
    if not historical_dir.exists():
        return 0
    files = sorted(historical_dir.glob("*.csv")) if historical_dir.is_dir() else [historical_dir]
    total = 0
    skipped = 0
    now = datetime.now().isoformat(timespec="seconds")
    file_total = len(files)
    print(f"[DB] Market prices: {file_total} file historical", flush=True)
    for index, file in enumerate(files, start=1):
        revision = file_sha256(file, short=True)
        if source_file_already_archived(conn, "market_prices_daily", file, revision):
            skipped += 1
            if index % 50 == 0 or index == file_total:
                print(f"[DB] Market prices progress: {index}/{file_total} file, rows={total}, skipped={skipped}", flush=True)
            continue
        df = load_csv(file)
        if df.empty:
            continue
        date_col = find_col(df, "Date")
        open_col = find_col(df, "Open")
        high_col = find_col(df, "High")
        low_col = find_col(df, "Low")
        close_col = find_col(df, "Close")
        volume_col = find_col(df, "Volume")
        if not all([date_col, open_col, high_col, low_col, close_col, volume_col]):
            continue
        symbol_col = find_col(df, "Symbol", "Ticker", "EMITEN")
        adj_col = find_col(df, "Adj Close", "Adjusted_Close", "Adj_Close")
        parsed_date = pd.to_datetime(df[date_col], errors="coerce")
        close_series = pd.to_numeric(df[close_col], errors="coerce")
        valid = parsed_date.notna() & close_series.notna()
        if not valid.any():
            continue

        if symbol_col:
            symbols = df.loc[valid, symbol_col].map(normalize_symbol)
        else:
            symbols = pd.Series([normalize_symbol(file.stem)] * int(valid.sum()), index=df.index[valid])
        adjusted_close = pd.to_numeric(df[adj_col], errors="coerce") if adj_col else close_series
        adjusted_close = adjusted_close.fillna(close_series)
        price_df = pd.DataFrame({
            "symbol": symbols,
            "price_date": parsed_date.loc[valid].dt.date.astype(str),
            "open": pd.to_numeric(df.loc[valid, open_col], errors="coerce"),
            "high": pd.to_numeric(df.loc[valid, high_col], errors="coerce"),
            "low": pd.to_numeric(df.loc[valid, low_col], errors="coerce"),
            "close": close_series.loc[valid],
            "adjusted_close": adjusted_close.loc[valid],
            "volume": pd.to_numeric(df.loc[valid, volume_col], errors="coerce"),
            "source": source,
            "source_revision": revision,
            "created_at": now,
            "updated_at": now,
        })
        rows = price_df.where(pd.notna(price_df), None).to_dict("records")
        upsert_many(conn, "market_prices_daily", rows, ["symbol", "price_date", "source"])
        mark_source_file_archived(conn, "market_prices_daily", file, revision, len(rows))
        total += len(rows)
        if index % 50 == 0 or index == file_total:
            conn.commit()
            print(f"[DB] Market prices progress: {index}/{file_total} file, rows={total}, skipped={skipped}", flush=True)
    conn.commit()
    return total


def archive_technical(conn: sqlite3.Connection, run_id: str, path: Path, quality: str) -> int:
    df = load_csv(path)
    if df.empty:
        return 0
    symbol_col = find_col(df, "Symbol", "Ticker", "EMITEN")
    date_col = find_col(df, "Date", "Latest_Valid_Candle_Date")
    score_col = find_col(df, "Technical_Score_Final", "Technical_Score", "Score")
    quality_col = find_col(df, "Technical_Quality_Check")
    if not symbol_col or not date_col:
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    for _, row in df.iterrows():
        feature_date = pd.to_datetime(row.get(date_col), errors="coerce")
        if pd.isna(feature_date):
            continue
        record = {
            "symbol": normalize_symbol(row.get(symbol_col)),
            "feature_date": feature_date.date().isoformat(),
            "engine_version": "technical_feature_engine_v1_1",
            "run_id": run_id,
            "technical_score": to_float(row.get(score_col)) if score_col else None,
            "technical_quality_check": str(row.get(quality_col, "")) if quality_col else "",
            "data_quality_status": quality,
            "row_json": json_text(row.where(pd.notna(row), None).to_dict()),
            "created_at": now,
            "updated_at": now,
        }
        upsert(conn, "technical_features", record, ["symbol", "feature_date", "engine_version"])
    return len(df)


def archive_candidates(conn: sqlite3.Connection, run_id: str, path: Path) -> int:
    df = load_csv(path)
    if df.empty:
        return 0
    symbol_col = find_col(df, "Symbol", "Ticker", "EMITEN")
    rank_col = find_col(df, "Rank", "Rank_V3")
    if not symbol_col:
        return 0
    candidate_hash = file_sha256(path)
    for idx, (_, row) in enumerate(df.iterrows(), 1):
        symbol = normalize_symbol(row.get(symbol_col))
        record = {
            "run_id": run_id,
            "symbol": symbol,
            "rank": to_int(row.get(rank_col)) if rank_col else idx,
            "technical_score": to_float(row.get(find_col(df, "Technical_Score"))) if find_col(df, "Technical_Score") else None,
            "technical_score_v2": to_float(row.get(find_col(df, "Technical_Score_V2"))) if find_col(df, "Technical_Score_V2") else None,
            "technical_quality_check": str(row.get(find_col(df, "Technical_Quality_Check"), "")) if find_col(df, "Technical_Quality_Check") else "",
            "technical_score_final": to_float(row.get(find_col(df, "Technical_Score_Final"))) if find_col(df, "Technical_Score_Final") else None,
            "candidate_status": str(row.get(find_col(df, "Candidate_Status"), "")) if find_col(df, "Candidate_Status") else "",
            "candidate_hash": candidate_hash,
            "row_json": json_text(row.where(pd.notna(row), None).to_dict()),
        }
        upsert(conn, "candidates", record, ["run_id", "symbol"])
    return len(df)


def archive_broker(conn: sqlite3.Connection, path: Path, manifest_path: Path | None = None) -> str:
    df = load_csv(path)
    if df.empty:
        return ""
    manifest = read_json(manifest_path) if manifest_path and manifest_path.exists() else {}
    snapshot_hash = file_sha256(path)
    broker_date = manifest.get("broker_date") or latest_data_date(df)
    snapshot_id = hashlib.sha256(f"{broker_date}|{snapshot_hash}".encode("utf-8")).hexdigest()[:16]
    row = {
        "broker_snapshot_id": snapshot_id,
        "broker_date": broker_date,
        "from_date": manifest.get("from_date", ""),
        "to_date": manifest.get("to_date", broker_date),
        "source_files": manifest.get("source") or str(path.resolve()),
        "coverage": manifest.get("coverage"),
        "snapshot_hash": snapshot_hash,
        "data_quality_status": manifest.get("DATA_QUALITY_STATUS", "VALID"),
        "manifest_json": json_text(manifest),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    upsert(conn, "broker_snapshots", row, ["broker_snapshot_id"])

    symbol_col = find_col(df, "EMITEN", "Symbol", "Ticker")
    status_col = find_col(df, "STATUS", "Scrape_Status", "Broker_Status")
    for _, item in df.iterrows():
        symbol = normalize_symbol(item.get(symbol_col)) if symbol_col else ""
        if not symbol:
            continue
        payload = json_text(item.where(pd.notna(item), None).to_dict())
        upsert(conn, "broker_summary", {"broker_snapshot_id": snapshot_id, "symbol": symbol, "row_json": payload}, ["broker_snapshot_id", "symbol"])
        if status_col:
            upsert(conn, "broker_status", {
                "broker_snapshot_id": snapshot_id,
                "symbol": symbol,
                "status": str(item.get(status_col, "")),
                "row_json": payload,
            }, ["broker_snapshot_id", "symbol"])
    return snapshot_id


def archive_payload_table(conn: sqlite3.Connection, table: str, run_id: str, path: Path) -> int:
    df = load_csv(path)
    if df.empty:
        return 0
    symbol_col = find_col(df, "Symbol", "EMITEN", "Ticker")
    rank_col = find_col(df, "Rank_V3", "Rank")
    decision_col = find_col(df, "Decision_V3", "Decision")
    score_col = find_col(df, "Final_Score_V3", "Final_Score")
    if not symbol_col:
        return 0
    for idx, (_, row) in enumerate(df.iterrows(), 1):
        symbol = normalize_symbol(row.get(symbol_col))
        payload = json_text(row.where(pd.notna(row), None).to_dict())
        if table == "fusion_results":
            record = {"run_id": run_id, "symbol": symbol, "rank": to_int(row.get(rank_col)) if rank_col else idx, "row_json": payload}
        else:
            record = {
                "run_id": run_id,
                "symbol": symbol,
                "rank": to_int(row.get(rank_col)) if rank_col else idx,
                "decision": str(row.get(decision_col, "")) if decision_col else "",
                "final_score": to_float(row.get(score_col)) if score_col else None,
                "row_json": payload,
            }
        upsert(conn, table, record, ["run_id", "symbol"])
    return len(df)


def archive_entry_exit(conn: sqlite3.Connection, run_id: str, exit_dir: Path) -> int:
    plans = load_csv(exit_dir / "ENTRY_PLANS.csv")
    alerts = load_csv(exit_dir / "EXIT_ALERTS.csv")
    rows: dict[str, dict[str, Any]] = {}
    if not plans.empty:
        symbol_col = find_col(plans, "Symbol")
        for _, row in plans.iterrows():
            symbol = normalize_symbol(row.get(symbol_col)) if symbol_col else ""
            if not symbol:
                continue
            rows[symbol] = {
                "run_id": run_id,
                "symbol": symbol,
                "entry": to_float(row.get(find_col(plans, "Reference_Close", "Entry_Price", "Entry_Zone_Low"))) if find_col(plans, "Reference_Close", "Entry_Price", "Entry_Zone_Low") else None,
                "stop_loss": to_float(row.get(find_col(plans, "Initial_Stop", "Stop_Loss"))) if find_col(plans, "Initial_Stop", "Stop_Loss") else None,
                "take_profit_1": to_float(row.get(find_col(plans, "Target_1", "Take_Profit_1"))) if find_col(plans, "Target_1", "Take_Profit_1") else None,
                "take_profit_2": to_float(row.get(find_col(plans, "Target_2", "Take_Profit_2"))) if find_col(plans, "Target_2", "Take_Profit_2") else None,
                "entry_status": str(row.get(find_col(plans, "Plan_Status", "Entry_Status"), "")) if find_col(plans, "Plan_Status", "Entry_Status") else "",
                "exit_status": "",
                "exit_reason": "",
                "holding_period": None,
                "row_json": json_text(row.where(pd.notna(row), None).to_dict()),
            }
    if not alerts.empty:
        symbol_col = find_col(alerts, "Symbol")
        for _, row in alerts.iterrows():
            symbol = normalize_symbol(row.get(symbol_col)) if symbol_col else ""
            if not symbol:
                continue
            base = rows.get(symbol, {"run_id": run_id, "symbol": symbol, "entry": None, "stop_loss": None, "take_profit_1": None, "take_profit_2": None, "entry_status": "", "row_json": "{}"})
            base.update({
                "exit_status": str(row.get(find_col(alerts, "Alert", "Exit_Status"), "EXIT")) if find_col(alerts, "Alert", "Exit_Status") else "EXIT",
                "exit_reason": str(row.get(find_col(alerts, "Reason", "Exit_Reason"), "")) if find_col(alerts, "Reason", "Exit_Reason") else "",
                "holding_period": to_int(row.get(find_col(alerts, "Holding_Days", "Holding_Period"))) if find_col(alerts, "Holding_Days", "Holding_Period") else None,
                "row_json": json_text(row.where(pd.notna(row), None).to_dict()),
            })
            rows[symbol] = base
    for row in rows.values():
        upsert(conn, "entry_exit_results", row, ["run_id", "symbol"])
    return len(rows)


def previous_valid_watchlist(conn: sqlite3.Connection, current_run_id: str) -> set[str]:
    found = conn.execute(
        """
        SELECT run_id FROM pipeline_runs
        WHERE strategy_type='SWING' AND run_id<>? AND data_quality_status='VALID'
        ORDER BY COALESCE(finished_at, started_at) DESC
        LIMIT 1
        """,
        (current_run_id,),
    ).fetchone()
    if not found:
        return set()
    rows = conn.execute("SELECT symbol FROM watchlist_history WHERE run_id=? AND lifecycle<>'REMOVED'", (found[0],)).fetchall()
    return {r[0] for r in rows}


def archive_watchlist(conn: sqlite3.Connection, run_id: str, decision_path: Path, quality: str) -> pd.DataFrame:
    df = load_csv(decision_path).copy()
    if df.empty:
        return pd.DataFrame()
    symbol_col = find_col(df, "Symbol", "EMITEN", "Ticker")
    decision_col = find_col(df, "Decision_V3", "Decision")
    date_col = find_col(df, "Technical_Data_Date", "Date", "Latest_Valid_Candle_Date")
    score_col = find_col(df, "Final_Score_V3", "Final_Score")
    if not symbol_col or not decision_col:
        return pd.DataFrame()
    df["_Symbol"] = df[symbol_col].map(normalize_symbol)
    df["_Decision"] = df[decision_col].astype(str).str.upper().str.strip()
    current = df[df["_Decision"].isin(ENTRY_WATCHLIST_DECISIONS)].copy()
    previous = previous_valid_watchlist(conn, run_id)
    current_symbols = set(current["_Symbol"])
    now_rows: list[dict[str, Any]] = []
    for _, row in current.iterrows():
        symbol = row["_Symbol"]
        signal_date = pd.to_datetime(row.get(date_col), errors="coerce") if date_col else pd.NaT
        lifecycle = "CONTINUING" if symbol in previous else "NEW"
        record = {
            "run_id": run_id,
            "symbol": symbol,
            "signal_date": signal_date.date().isoformat() if pd.notna(signal_date) else "",
            "lifecycle": lifecycle,
            "decision": row["_Decision"],
            "final_score": to_float(row.get(score_col)) if score_col else None,
            "data_quality_status": quality,
            "row_json": json_text(row.where(pd.notna(row), None).to_dict()),
        }
        upsert(conn, "watchlist_history", record, ["run_id", "symbol"])
        now_rows.append(record)
    for symbol in sorted(previous - current_symbols):
        record = {
            "run_id": run_id,
            "symbol": symbol,
            "signal_date": "",
            "lifecycle": "REMOVED",
            "decision": "",
            "final_score": None,
            "data_quality_status": quality,
            "row_json": "{}",
        }
        upsert(conn, "watchlist_history", record, ["run_id", "symbol"])
        now_rows.append(record)
    return pd.DataFrame(now_rows)


def load_price_map(historical_dir: Path) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if not historical_dir.exists():
        return out
    for file in sorted(historical_dir.glob("*.csv")):
        df = load_csv(file)
        if df.empty:
            continue
        date_col = find_col(df, "Date")
        close_col = find_col(df, "Close")
        high_col = find_col(df, "High")
        low_col = find_col(df, "Low")
        if not all([date_col, close_col, high_col, low_col]):
            continue
        symbol_col = find_col(df, "Symbol", "Ticker", "EMITEN")
        symbol = normalize_symbol(df[symbol_col].iloc[0]) if symbol_col and len(df) else normalize_symbol(file.stem)
        work = pd.DataFrame({
            "Date": pd.to_datetime(df[date_col], errors="coerce"),
            "Close": pd.to_numeric(df[close_col], errors="coerce"),
            "High": pd.to_numeric(df[high_col], errors="coerce"),
            "Low": pd.to_numeric(df[low_col], errors="coerce"),
        }).dropna(subset=["Date", "Close", "High", "Low"]).sort_values("Date").reset_index(drop=True)
        out[symbol] = work
    return out


def map_entry_plans(path: Path) -> dict[str, dict[str, Any]]:
    plans = load_csv(path)
    if plans.empty:
        return {}
    symbol_col = find_col(plans, "Symbol")
    out = {}
    for _, row in plans.iterrows():
        symbol = normalize_symbol(row.get(symbol_col)) if symbol_col else ""
        if symbol:
            out[symbol] = row.to_dict()
    return out


def archive_watchlist_outcomes(
    conn: sqlite3.Connection,
    run_id: str,
    watchlist: pd.DataFrame,
    historical_dir: Path,
    entry_plans_path: Path,
    quality: str,
) -> int:
    if watchlist.empty:
        return 0
    prices = load_price_map(historical_dir)
    plans = map_entry_plans(entry_plans_path)
    count = 0
    for _, row in watchlist[watchlist["lifecycle"].isin(["NEW", "CONTINUING"])].iterrows():
        symbol = row["symbol"]
        signal_date = pd.to_datetime(row.get("signal_date"), errors="coerce")
        if pd.isna(signal_date):
            continue
        px = prices.get(symbol, pd.DataFrame())
        future = px[px["Date"] > signal_date].copy() if not px.empty else pd.DataFrame()
        plan = plans.get(symbol, {})
        try:
            row_payload = json.loads(row.get("row_json", "{}"))
        except Exception:
            row_payload = {}
        reference = to_float(plan.get("Reference_Close")) or to_float(row_payload.get("Close"))
        entry_price = reference or to_float(plan.get("Entry_Price"))
        if entry_price is None and not future.empty:
            entry_price = to_float(future.iloc[0]["Close"])
        stop = to_float(plan.get("Initial_Stop")) or to_float(plan.get("Stop_Loss"))
        tp1 = to_float(plan.get("Target_1")) or to_float(plan.get("Take_Profit_1"))
        tp2 = to_float(plan.get("Target_2")) or to_float(plan.get("Take_Profit_2"))

        def close_at(days: int) -> float | None:
            if len(future) >= days:
                return to_float(future.iloc[days - 1]["Close"])
            return None

        close_d1, close_d3, close_d5, close_d7 = close_at(1), close_at(3), close_at(5), close_at(7)

        def ret(close: float | None) -> float | None:
            return (close / entry_price - 1) * 100 if close is not None and entry_price else None

        window7 = future.head(7)
        max_price = to_float(window7["High"].max()) if not window7.empty else None
        min_price = to_float(window7["Low"].min()) if not window7.empty else None
        mfe = (max_price / entry_price - 1) * 100 if max_price is not None and entry_price else None
        mae = (min_price / entry_price - 1) * 100 if min_price is not None and entry_price else None
        tp1_hit = bool(tp1 is not None and not window7.empty and window7["High"].ge(tp1).any())
        tp2_hit = bool(tp2 is not None and not window7.empty and window7["High"].ge(tp2).any())
        sl_hit = bool(stop is not None and not window7.empty and window7["Low"].le(stop).any())
        if close_d7 is None:
            outcome = "OPEN"
        elif sl_hit and not (tp1_hit or tp2_hit):
            outcome = "LOSS"
        elif tp1_hit or tp2_hit or (ret(close_d7) or 0) > 0:
            outcome = "WIN"
        elif (ret(close_d7) or 0) < 0:
            outcome = "LOSS"
        else:
            outcome = "AMBIGUOUS"
        signal_id = hashlib.sha256(f"{symbol}|{signal_date.date()}|{row.get('decision')}".encode("utf-8")).hexdigest()[:20]
        record = {
            "signal_id": signal_id,
            "run_id": run_id,
            "symbol": symbol,
            "signal_date": signal_date.date().isoformat(),
            "decision": row.get("decision"),
            "reference_price": reference,
            "entry_price": entry_price,
            "stop_loss": stop,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "close_d1": close_d1,
            "close_d3": close_d3,
            "close_d5": close_d5,
            "close_d7": close_d7,
            "return_d1": ret(close_d1),
            "return_d3": ret(close_d3),
            "return_d5": ret(close_d5),
            "return_d7": ret(close_d7),
            "max_price_d7": max_price,
            "min_price_d7": min_price,
            "mfe": mfe,
            "mae": mae,
            "tp1_hit": int(tp1_hit),
            "tp2_hit": int(tp2_hit),
            "sl_hit": int(sl_hit),
            "final_outcome": outcome,
            "data_quality_status": quality,
        }
        upsert(conn, "watchlist_outcomes", record, ["signal_id"])
        count += 1
    return count


def archive_provider(conn: sqlite3.Connection, run_id: str, yahoo_manifest: dict[str, Any]) -> None:
    if not yahoo_manifest:
        return
    row = {
        "run_id": run_id,
        "provider": yahoo_manifest.get("Provider", "Yahoo Finance"),
        "requested_start": yahoo_manifest.get("Requested_Start_Date", ""),
        "requested_end": yahoo_manifest.get("Requested_End_Date", ""),
        "latest_expected_date": yahoo_manifest.get("Latest_Expected_Trading_Date", ""),
        "latest_valid_date": yahoo_manifest.get("Latest_Valid_Close_Date", ""),
        "symbol_total": yahoo_manifest.get("Symbol_Total", 0),
        "symbol_updated": yahoo_manifest.get("Symbol_Updated_Valid", 0),
        "symbol_partial": yahoo_manifest.get("Symbol_Partial", 0),
        "symbol_failed": yahoo_manifest.get("Symbol_Failed", 0),
        "fallback_used": int(bool(yahoo_manifest.get("Fallback_Used", False))),
        "status": yahoo_manifest.get("Refresh_Status", ""),
        "manifest_json": json_text(yahoo_manifest),
    }
    upsert(conn, "provider_runs", row, ["run_id", "provider"])


def archive_pipeline_run(conn: sqlite3.Connection, args: argparse.Namespace, manifest: dict[str, Any], broker_manifest: dict[str, Any], quality: str) -> None:
    row = {
        "run_id": args.run_id,
        "strategy_type": "SWING",
        "pipeline_version": manifest.get("Pipeline_Version", "SDE_SWING_V1_2_SAFE_BASELINE"),
        "started_at": manifest.get("Started_At", ""),
        "finished_at": manifest.get("Finished_At", datetime.now().isoformat(timespec="seconds")),
        "status": manifest.get("Pipeline_Status", "ARCHIVED"),
        "technical_date": manifest.get("Technical_Date", ""),
        "broker_date": manifest.get("Broker_Date", broker_manifest.get("broker_date", "")),
        "market_date": manifest.get("Historical_Latest_Valid_Date", ""),
        "data_mode": manifest.get("DATA_MODE", manifest.get("data_mode", "")),
        "fallback_used": int(bool(manifest.get("Fallback_Used", False))),
        "broker_date_override": int(bool(manifest.get("Broker_Date_Override", broker_manifest.get("BROKER_DATE_OVERRIDE", False)))),
        "broker_coverage": manifest.get("Broker_Coverage", broker_manifest.get("coverage")),
        "data_quality_status": quality,
        "warning": json_text(manifest.get("Warnings", [])),
        "error_message": json_text(manifest.get("Errors", [])),
        "manifest_json": json_text(manifest),
    }
    upsert(conn, "pipeline_runs", row, ["run_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive SDE Swing run into SQLite")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--db", default="data/database/sde_swing_history.db")
    parser.add_argument("--run-manifest", default="")
    parser.add_argument("--yahoo-manifest", default="")
    parser.add_argument("--broker-manifest", default="")
    parser.add_argument("--historical-dir", default="data/output/historical/by_symbol")
    parser.add_argument("--technical", default="data/output/technical/latest_technical_features.csv")
    parser.add_argument("--candidates", default="data/output/candidates/technical_candidates_top30.csv")
    parser.add_argument("--broker-summary", default="data/input/broker/BROKER_SUMMARY_LATEST.csv")
    parser.add_argument("--fusion", default="data/input/FINAL_DECISION_V2.csv")
    parser.add_argument("--decision", default="data/output/decision/FINAL_DECISION_V3.csv")
    parser.add_argument("--exit-dir", default="data/output/exit")
    parser.add_argument("--data-quality-status", default="VALID")
    parser.add_argument("--summary-output", default="")
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()

    db_path = Path(args.db)
    conn = connect(db_path)
    init_schema(conn)
    manifest = read_json(Path(args.run_manifest)) if args.run_manifest else {}
    yahoo_manifest = read_json(Path(args.yahoo_manifest)) if args.yahoo_manifest else {}
    broker_manifest = read_json(Path(args.broker_manifest)) if args.broker_manifest else {}
    quality = args.data_quality_status or manifest.get("Data_Quality_Status", "VALID")

    print("[DB] Archive started", flush=True)
    price_rows = archive_prices(conn, Path(args.historical_dir))
    print(f"[DB] Market prices archived: {price_rows} rows", flush=True)
    tech_rows = archive_technical(conn, args.run_id, Path(args.technical), quality)
    print(f"[DB] Technical features archived: {tech_rows} rows", flush=True)
    candidate_rows = archive_candidates(conn, args.run_id, Path(args.candidates))
    print(f"[DB] Candidates archived: {candidate_rows} rows", flush=True)
    broker_snapshot_id = archive_broker(conn, Path(args.broker_summary), Path(args.broker_manifest) if args.broker_manifest else None)
    print("[DB] Broker snapshot archived", flush=True)
    fusion_rows = archive_payload_table(conn, "fusion_results", args.run_id, Path(args.fusion))
    print(f"[DB] Fusion rows archived: {fusion_rows}", flush=True)
    decision_rows = archive_payload_table(conn, "decision_results", args.run_id, Path(args.decision))
    print(f"[DB] Decision rows archived: {decision_rows}", flush=True)
    entry_exit_rows = archive_entry_exit(conn, args.run_id, Path(args.exit_dir))
    print(f"[DB] Entry/exit rows archived: {entry_exit_rows}", flush=True)
    watchlist = archive_watchlist(conn, args.run_id, Path(args.decision), quality)
    print(f"[DB] Watchlist rows archived: {len(watchlist)}", flush=True)
    outcome_rows = archive_watchlist_outcomes(conn, args.run_id, watchlist, Path(args.historical_dir), Path(args.exit_dir) / "ENTRY_PLANS.csv", quality)
    print(f"[DB] Watchlist outcomes archived: {outcome_rows}", flush=True)
    archive_provider(conn, args.run_id, yahoo_manifest)
    archive_pipeline_run(conn, args, manifest, broker_manifest, quality)

    for dataset_type, path in {
        "historical_combined": Path(args.historical_dir).parent / "historical_ohlcv_combined.csv",
        "technical_features": args.technical,
        "candidates": args.candidates,
        "broker_summary": args.broker_summary,
        "fusion_results": args.fusion,
        "decision_results": args.decision,
        "entry_plans": Path(args.exit_dir) / "ENTRY_PLANS.csv",
        "exit_alerts": Path(args.exit_dir) / "EXIT_ALERTS.csv",
    }.items():
        add_snapshot(conn, args.run_id, dataset_type, Path(path))

    conn.commit()
    summary = {
        "Run_ID": args.run_id,
        "database": str(db_path.resolve()),
        "price_rows_processed": price_rows,
        "technical_rows": tech_rows,
        "candidate_rows": candidate_rows,
        "broker_snapshot_id": broker_snapshot_id,
        "fusion_rows": fusion_rows,
        "decision_rows": decision_rows,
        "entry_exit_rows": entry_exit_rows,
        "watchlist_rows": int(len(watchlist)),
        "watchlist_outcome_rows": outcome_rows,
        "data_quality_status": quality,
    }
    if args.summary_output:
        write_json(Path(args.summary_output), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
