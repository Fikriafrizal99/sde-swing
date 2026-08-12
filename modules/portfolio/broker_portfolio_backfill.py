#!/usr/bin/env python3
"""Prepare and import daily Broker Summary backfill for actual portfolio positions.

This module is intentionally isolated from Candidate Selector, Broker Fusion,
Decision Engine, and Final Watchlist.  It only prepares dedicated Tampermonkey
backfill tasks and archives validated DAILY Broker Summary rows into the shared
``sde_swing_history.db`` broker history.

A backfill observation must be one trading day only: FROM_DATE == TO_DATE.
Cumulative multi-day exports are rejected so Position Management never treats
one cumulative row as several daily observations.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.database.swing_history_db import archive_broker, connect, init_schema
from modules.market_calendar.idx_calendar import is_idx_trading_day
from modules.portfolio.broker_history_context import _snapshot_is_real_daily
from swing_utils import ensure_dir, file_sha256, normalize_symbol, write_json

DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
DEFAULT_CALENDAR = PROJECT_ROOT / "config/trading_calendar.json"
DEFAULT_TASKS = PROJECT_ROOT / "data/input/broker/BROKER_PORTFOLIO_BACKFILL_TASKS.csv"
DEFAULT_ARCHIVE = PROJECT_ROOT / "data/archive/broker_portfolio_backfill"
BACKFILL_PREFIX = "BROKER_PORTFOLIO_BACKFILL_SUMMARY_"

REQUIRED_SUMMARY_COLUMNS = {
    "FROM_DATE",
    "TO_DATE",
    "EMITEN",
    "TOTAL_BUY",
    "TOTAL_SELL",
    "NET_FLOW",
    "TOP_BUYER_1",
    "TOP_SELLER_1",
    "BUYER_CONCENTRATION",
    "SELLER_CONCENTRATION",
}


def default_downloads() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"


def load_calendar(path: Path) -> tuple[list[str], list[Any]]:
    if not path.exists():
        return [], []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    holidays_raw = payload.get("holidays", {})
    holidays = list(holidays_raw.keys()) if isinstance(holidays_raw, dict) else list(holidays_raw or [])
    special = list(payload.get("special_trading_days", []) or [])
    return holidays, special


def parse_date(value: str, field: str) -> date:
    raw = str(value or "").strip()
    try:
        return date.fromisoformat(raw[:10])
    except Exception as exc:
        raise ValueError(f"{field} harus format YYYY-MM-DD: {value}") from exc


def latest_trading_on_or_before(
    value: date,
    *,
    holidays: list[str],
    special_trading_days: list[Any],
) -> date:
    probe = value
    while not is_idx_trading_day(
        probe,
        holidays=holidays,
        special_trading_days=special_trading_days,
    ):
        probe -= timedelta(days=1)
    return probe


def trading_dates(
    start: date,
    end: date,
    *,
    holidays: list[str],
    special_trading_days: list[Any],
) -> list[str]:
    if start > end:
        raise ValueError(f"from-date {start} lebih besar dari to-date {end}")
    output: list[str] = []
    probe = start
    while probe <= end:
        if is_idx_trading_day(
            probe,
            holidays=holidays,
            special_trading_days=special_trading_days,
        ):
            output.append(probe.isoformat())
        probe += timedelta(days=1)
    return output


def open_portfolio_rows(conn: sqlite3.Connection, symbol: str = "") -> list[dict[str, str]]:
    init_schema(conn)
    params: list[Any] = []
    where = "WHERE UPPER(current_status)='OPEN'"
    normalized = normalize_symbol(symbol)
    if normalized:
        where += " AND UPPER(symbol)=?"
        params.append(normalized)
    rows = conn.execute(
        f"""
        SELECT position_id, symbol, buy_date
        FROM portfolio_positions
        {where}
        ORDER BY buy_date, symbol, position_id
        """,
        params,
    ).fetchall()

    # Broker history is symbol/date-level, not lot-level.  If more than one OPEN
    # lot exists for a symbol, start at the earliest OPEN buy date once.
    by_symbol: dict[str, dict[str, str]] = {}
    for position_id, raw_symbol, buy_date in rows:
        sym = normalize_symbol(raw_symbol)
        day = str(buy_date or "")[:10]
        if not sym or not day:
            continue
        current = by_symbol.get(sym)
        if current is None or day < current["buy_date"]:
            by_symbol[sym] = {
                "position_id": str(position_id or ""),
                "symbol": sym,
                "buy_date": day,
            }
    return [by_symbol[key] for key in sorted(by_symbol)]


def existing_broker_dates(
    conn: sqlite3.Connection,
    symbol: str,
    start_date: str,
    end_date: str,
) -> set[str]:
    """Return only broker dates usable as real 1D Portfolio Management history.

    ``broker_snapshots`` intentionally contains both real daily snapshots and
    aggregate 3D/5D/CUSTOM snapshots.  Portfolio backfill must therefore use
    the exact same provenance contract as Position Management; otherwise an
    aggregate ending on a date can falsely suppress the missing DAILY task.
    """
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT s.broker_date, s.from_date, s.to_date, s.manifest_json
        FROM broker_summary b
        JOIN broker_snapshots s ON s.broker_snapshot_id=b.broker_snapshot_id
        WHERE UPPER(b.symbol)=UPPER(?)
          AND COALESCE(s.broker_date, '') BETWEEN ? AND ?
        ORDER BY s.broker_date, s.created_at, s.broker_snapshot_id
        """,
        (normalize_symbol(symbol), start_date, end_date),
    ).fetchall()

    existing: set[str] = set()
    for row in rows:
        if not _snapshot_is_real_daily(row):
            continue
        day = str(row[0] or "").strip()[:10]
        if day:
            existing.add(day)
    return existing


def build_tasks(
    conn: sqlite3.Connection,
    *,
    symbol: str = "",
    from_date: str = "",
    to_date: str = "",
    calendar_path: Path = DEFAULT_CALENDAR,
    force: bool = False,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    holidays, special = load_calendar(calendar_path)
    end = parse_date(to_date, "to-date") if to_date else latest_trading_on_or_before(
        date.today(), holidays=holidays, special_trading_days=special
    )

    normalized = normalize_symbol(symbol)
    positions = open_portfolio_rows(conn, normalized if normalized else "")
    requests: list[dict[str, str]] = []

    if normalized:
        if from_date:
            start = parse_date(from_date, "from-date")
            position_id = positions[0]["position_id"] if positions else ""
            source = "OPEN_PORTFOLIO" if positions else "MANUAL"
        elif positions:
            start = parse_date(positions[0]["buy_date"], "buy_date")
            position_id = positions[0]["position_id"]
            source = "OPEN_PORTFOLIO"
        else:
            raise ValueError(
                f"{normalized} tidak ditemukan sebagai posisi OPEN. Isi --from-date untuk backfill manual."
            )
        requests.append(
            {
                "symbol": normalized,
                "position_id": position_id,
                "start": start.isoformat(),
                "source": source,
            }
        )
    else:
        if from_date:
            raise ValueError("--from-date hanya boleh dipakai bersama --symbol")
        if not positions:
            return [], {
                "status": "NO_OPEN_POSITION",
                "requested_symbols": 0,
                "task_count": 0,
                "skipped_existing": 0,
            }
        for row in positions:
            requests.append(
                {
                    "symbol": row["symbol"],
                    "position_id": row["position_id"],
                    "start": row["buy_date"],
                    "source": "OPEN_PORTFOLIO",
                }
            )

    tasks: list[dict[str, str]] = []
    skipped_existing = 0
    requested_dates = 0
    for request in requests:
        start = parse_date(request["start"], "start")
        days = trading_dates(
            start,
            end,
            holidays=holidays,
            special_trading_days=special,
        )
        requested_dates += len(days)
        existing = set() if force else existing_broker_dates(
            conn, request["symbol"], start.isoformat(), end.isoformat()
        )
        for day in days:
            if day in existing:
                skipped_existing += 1
                continue
            tasks.append(
                {
                    "Symbol": request["symbol"],
                    "FROM_DATE": day,
                    "TO_DATE": day,
                    "TASK_KEY": f"{request['symbol']}|{day}",
                    "POSITION_ID": request["position_id"],
                    "SOURCE": request["source"],
                }
            )

    tasks.sort(key=lambda item: (item["TO_DATE"], item["Symbol"]))
    meta = {
        "status": "READY" if tasks else "NO_MISSING_DATES",
        "requested_symbols": len(requests),
        "requested_trading_days": requested_dates,
        "task_count": len(tasks),
        "skipped_existing": skipped_existing,
        "to_date": end.isoformat(),
        "force": bool(force),
    }
    return tasks, meta


def write_tasks(path: Path, tasks: list[dict[str, str]], meta: dict[str, Any]) -> Path:
    ensure_dir(path.parent)
    columns = ["Symbol", "FROM_DATE", "TO_DATE", "TASK_KEY", "POSITION_ID", "SOURCE"]
    pd.DataFrame(tasks, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")
    manifest = {
        **meta,
        "task_file": str(path.resolve()),
        "required_tampermonkey_mode": "PORTFOLIO_BACKFILL",
        "daily_only": True,
        "final_watchlist_input": False,
    }
    write_json(path.with_suffix(".manifest.json"), manifest)
    return path


def validate_backfill_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_SUMMARY_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"Kolom Broker Summary backfill tidak lengkap: {missing}")
    if df.empty:
        raise ValueError("File backfill kosong")

    clean = df.copy()
    clean["EMITEN"] = clean["EMITEN"].map(normalize_symbol)
    from_parsed = pd.to_datetime(clean["FROM_DATE"], errors="coerce")
    to_parsed = pd.to_datetime(clean["TO_DATE"], errors="coerce")
    invalid_date = from_parsed.isna() | to_parsed.isna()
    if invalid_date.any():
        rows = [int(i) + 2 for i in clean.index[invalid_date].tolist()[:10]]
        raise ValueError(f"Tanggal backfill tidak valid pada baris CSV: {rows}")

    clean["FROM_DATE"] = from_parsed.dt.date.astype(str)
    clean["TO_DATE"] = to_parsed.dt.date.astype(str)
    non_daily = clean["FROM_DATE"] != clean["TO_DATE"]
    if non_daily.any():
        sample = clean.loc[non_daily, ["EMITEN", "FROM_DATE", "TO_DATE"]].head(10).to_dict("records")
        raise ValueError(
            "BACKFILL_REJECTED_NOT_DAILY: FROM_DATE wajib sama dengan TO_DATE. "
            f"Jangan import cumulative range. Sample={sample}"
        )
    if (clean["EMITEN"] == "").any():
        raise ValueError("Ada EMITEN kosong/tidak valid pada file backfill")
    return clean


def find_latest_backfill_export(downloads: Path) -> Path:
    files = sorted(
        downloads.glob(f"{BACKFILL_PREFIX}*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(
            f"Tidak menemukan {BACKFILL_PREFIX}*.csv di {downloads}"
        )
    return files[0]


def archive_backfill(
    conn: sqlite3.Connection,
    df: pd.DataFrame,
    *,
    source_path: Path,
    archive_root: Path,
) -> list[dict[str, Any]]:
    clean = validate_backfill_dataframe(df)
    init_schema(conn)
    ensure_dir(archive_root)
    results: list[dict[str, Any]] = []

    for broker_date, group in clean.groupby("TO_DATE", sort=True):
        daily = group.drop_duplicates(subset=["EMITEN"], keep="last").sort_values("EMITEN")
        day_dir = archive_root / str(broker_date)
        ensure_dir(day_dir)
        daily_path = day_dir / f"BROKER_SUMMARY_BACKFILL_{broker_date}.csv"
        daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
        manifest_path = daily_path.with_suffix(".manifest.json")
        manifest = {
            "broker_date": str(broker_date),
            "from_date": str(broker_date),
            "to_date": str(broker_date),
            "source": "PORTFOLIO_BACKFILL",
            "source_file": str(source_path.resolve()),
            "coverage": 1.0,
            "DATA_QUALITY_STATUS": "VALID_BACKFILL_DAILY",
            "daily_only": True,
            "symbol_count": int(len(daily)),
        }
        write_json(manifest_path, manifest)
        snapshot_id = archive_broker(conn, daily_path, manifest_path)
        conn.commit()
        results.append(
            {
                "broker_date": str(broker_date),
                "symbol_count": int(len(daily)),
                "symbols": sorted(daily["EMITEN"].tolist()),
                "snapshot_id": snapshot_id,
                "archive_file": str(daily_path.resolve()),
                "archive_hash": file_sha256(daily_path),
            }
        )
    return results


def status_rows(
    conn: sqlite3.Connection,
    *,
    calendar_path: Path,
    to_date: str = "",
) -> list[dict[str, Any]]:
    holidays, special = load_calendar(calendar_path)
    end = parse_date(to_date, "to-date") if to_date else latest_trading_on_or_before(
        date.today(), holidays=holidays, special_trading_days=special
    )
    rows: list[dict[str, Any]] = []
    for position in open_portfolio_rows(conn):
        start = parse_date(position["buy_date"], "buy_date")
        expected = trading_dates(
            start,
            end,
            holidays=holidays,
            special_trading_days=special,
        )
        existing = existing_broker_dates(conn, position["symbol"], start.isoformat(), end.isoformat())
        missing = [day for day in expected if day not in existing]
        rows.append(
            {
                "symbol": position["symbol"],
                "buy_date": position["buy_date"],
                "expected": len(expected),
                "available": len(set(expected) & existing),
                "missing": len(missing),
                "missing_dates": missing,
            }
        )
    return rows


def cmd_prepare(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    conn = connect(db_path)
    try:
        tasks, meta = build_tasks(
            conn,
            symbol=args.symbol,
            from_date=args.from_date,
            to_date=args.to_date,
            calendar_path=Path(args.calendar),
            force=args.force,
        )
    finally:
        conn.close()

    output = Path(args.output)
    write_tasks(output, tasks, meta)
    print("PORTFOLIO BROKER BACKFILL - PREPARE")
    print(f"Status          : {meta['status']}")
    print(f"Symbols         : {meta['requested_symbols']}")
    print(f"Trading dates   : {meta.get('requested_trading_days', 0)}")
    print(f"Already in DB   : {meta.get('skipped_existing', 0)}")
    print(f"Tasks to fetch  : {meta['task_count']}")
    print(f"Task CSV        : {output.resolve()}")
    if tasks:
        print("Import CSV ini ke Tampermonkey 'SDE Broker Portfolio Backfill'.")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    source = Path(args.file).expanduser() if args.file else find_latest_backfill_export(Path(args.downloads).expanduser())
    if not source.exists():
        raise FileNotFoundError(f"File backfill tidak ditemukan: {source}")
    df = pd.read_csv(source, low_memory=False)
    conn = connect(Path(args.db))
    try:
        results = archive_backfill(
            conn,
            df,
            source_path=source,
            archive_root=Path(args.archive_dir),
        )
    finally:
        conn.close()

    total_symbols = sum(int(row["symbol_count"]) for row in results)
    print("PORTFOLIO BROKER BACKFILL - IMPORT")
    print(f"Source          : {source.resolve()}")
    print(f"Daily snapshots : {len(results)}")
    print(f"Symbol rows     : {total_symbols}")
    for row in results:
        print(f"  {row['broker_date']}: {row['symbol_count']} symbol -> {row['snapshot_id']}")
    print("Final Watchlist tidak dijalankan dan tidak diubah oleh proses import ini.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    try:
        rows = status_rows(
            conn,
            calendar_path=Path(args.calendar),
            to_date=args.to_date,
        )
    finally:
        conn.close()
    print("PORTFOLIO BROKER BACKFILL - STATUS")
    if not rows:
        print("Tidak ada posisi OPEN.")
        return 0
    for row in rows:
        coverage = row["available"] / max(1, row["expected"])
        missing_preview = ", ".join(row["missing_dates"][:8]) or "-"
        if len(row["missing_dates"]) > 8:
            missing_preview += ", ..."
        print(
            f"{row['symbol']}: {row['available']}/{row['expected']} ({coverage:.0%}) | "
            f"missing={row['missing']} | {missing_preview}"
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily broker backfill for actual portfolio positions")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--calendar", default=str(DEFAULT_CALENDAR))
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Generate dedicated Tampermonkey backfill task CSV")
    prepare.add_argument("--symbol", default="")
    prepare.add_argument("--from-date", default="")
    prepare.add_argument("--to-date", default="")
    prepare.add_argument("--output", default=str(DEFAULT_TASKS))
    prepare.add_argument("--force", action="store_true", help="Include dates already available in DB")
    prepare.set_defaults(func=cmd_prepare)

    imp = sub.add_parser("import", help="Import dedicated Tampermonkey backfill summary into shared DB")
    imp.add_argument("--file", default="", help="Backfill summary CSV; blank = newest file in Downloads")
    imp.add_argument("--downloads", default=str(default_downloads()))
    imp.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE))
    imp.set_defaults(func=cmd_import)

    status = sub.add_parser("status", help="Show broker-history coverage for all OPEN positions")
    status.add_argument("--to-date", default="")
    status.set_defaults(func=cmd_status)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"[FAILED] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
