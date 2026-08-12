#!/usr/bin/env python3
"""User-facing daily broker maintenance for actual OPEN portfolio positions.

The shared SQLite database is the source of truth.  Task CSVs are derived from
OPEN positions plus broker dates that are still missing from that database.
After a successful import the task CSV is rebuilt immediately, so stale BUY
history or CLOSED symbols are not left behind for the next Tampermonkey run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.database.swing_history_db import connect
from modules.portfolio.broker_portfolio_backfill import (
    DEFAULT_ARCHIVE,
    DEFAULT_CALENDAR,
    DEFAULT_DB,
    DEFAULT_TASKS,
    archive_backfill,
    build_tasks,
    default_downloads,
    find_latest_backfill_export,
    status_rows,
    write_tasks,
)


def _print_coverage(rows: list[dict]) -> None:
    if not rows:
        print("Portfolio OPEN   : tidak ada")
        return
    print("\nSTATUS HISTORI BROKER")
    print("-" * 72)
    for row in rows:
        expected = int(row.get("expected", 0))
        available = int(row.get("available", 0))
        missing = int(row.get("missing", 0))
        coverage = available / max(1, expected)
        dates = list(row.get("missing_dates", []))
        preview = ", ".join(dates[:5]) if dates else "-"
        if len(dates) > 5:
            preview += ", ..."
        print(
            f"{row['symbol']:<8} | BUY {row['buy_date']} | DB {available}/{expected} "
            f"({coverage:.0%}) | missing {missing} | {preview}"
        )


def sync_tasks(
    *,
    db_path: Path,
    calendar_path: Path,
    output_path: Path,
    symbol: str = "",
    from_date: str = "",
    to_date: str = "",
    force: bool = False,
) -> tuple[list[dict[str, str]], dict, list[dict]]:
    conn = connect(db_path)
    try:
        tasks, meta = build_tasks(
            conn,
            symbol=symbol,
            from_date=from_date,
            to_date=to_date,
            calendar_path=calendar_path,
            force=force,
        )
        coverage = status_rows(conn, calendar_path=calendar_path, to_date=to_date)
    finally:
        conn.close()
    write_tasks(output_path, tasks, meta)
    return tasks, meta, coverage


def _print_task_result(tasks: list[dict[str, str]], meta: dict, coverage: list[dict], output_path: Path) -> None:
    print("\nSDE - UPDATE BROKER PORTFOLIO")
    print("=" * 72)
    print(f"Target trading day : {meta.get('to_date', '-')}")
    print(f"Portfolio/symbol   : {meta.get('requested_symbols', 0)}")
    print(f"Sudah ada di DB    : {meta.get('skipped_existing', 0)} tanggal")
    print(f"Perlu diambil      : {meta.get('task_count', 0)} task harian")
    _print_coverage(coverage)
    print("\n" + "-" * 72)
    if tasks:
        dates = sorted({row["TO_DATE"] for row in tasks})
        symbols = sorted({row["Symbol"] for row in tasks})
        date_text = dates[0] if len(dates) == 1 else f"{dates[0]} s/d {dates[-1]}"
        print(f"[ACTION] Tampermonkey perlu dijalankan: {len(tasks)} task")
        print(f"         Symbol : {', '.join(symbols)}")
        print(f"         Tanggal: {date_text}")
        print(f"         CSV    : {output_path.resolve()}")
        print("         Setelah export selesai, pilih menu IMPORT HASIL.")
    else:
        print(f"[SELESAI] Histori broker portfolio sudah lengkap s/d {meta.get('to_date', '-')}.")
        print("          Tidak perlu menjalankan Tampermonkey sekarang.")
        print(f"          Task CSV sudah disinkronkan: {output_path.resolve()}")


def cmd_daily(args: argparse.Namespace) -> int:
    tasks, meta, coverage = sync_tasks(
        db_path=Path(args.db),
        calendar_path=Path(args.calendar),
        output_path=Path(args.output),
        to_date=args.to_date,
    )
    _print_task_result(tasks, meta, coverage, Path(args.output))
    return 0


def cmd_one(args: argparse.Namespace) -> int:
    tasks, meta, coverage = sync_tasks(
        db_path=Path(args.db),
        calendar_path=Path(args.calendar),
        output_path=Path(args.output),
        symbol=args.symbol,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    _print_task_result(tasks, meta, coverage, Path(args.output))
    return 0


def cmd_refresh_all(args: argparse.Namespace) -> int:
    tasks, meta, coverage = sync_tasks(
        db_path=Path(args.db),
        calendar_path=Path(args.calendar),
        output_path=Path(args.output),
        to_date=args.to_date,
        force=True,
    )
    print("[ADVANCED] FULL REFRESH aktif: tanggal yang sudah ada di DB ikut diminta ulang.")
    _print_task_result(tasks, meta, coverage, Path(args.output))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    source = Path(args.file).expanduser() if args.file else find_latest_backfill_export(Path(args.downloads).expanduser())
    if not source.exists():
        raise FileNotFoundError(f"File hasil Tampermonkey tidak ditemukan: {source}")

    df = pd.read_csv(source, low_memory=False)
    db_path = Path(args.db)
    calendar_path = Path(args.calendar)
    output_path = Path(args.output)
    conn = connect(db_path)
    try:
        results = archive_backfill(
            conn,
            df,
            source_path=source,
            archive_root=Path(args.archive_dir),
        )
        # Rebuild immediately from the DB. This is the key guard against a stale
        # task CSV containing old BUY history or symbols that are no longer OPEN.
        tasks, meta = build_tasks(
            conn,
            calendar_path=calendar_path,
            force=False,
        )
        coverage = status_rows(conn, calendar_path=calendar_path)
    finally:
        conn.close()

    write_tasks(output_path, tasks, meta)
    total_symbols = sum(int(row["symbol_count"]) for row in results)
    print("\nSDE - IMPORT BROKER PORTFOLIO")
    print("=" * 72)
    print(f"File             : {source.resolve()}")
    print(f"Snapshot harian  : {len(results)}")
    print(f"Symbol tersimpan : {total_symbols}")
    print("[OK] Database sudah di-update dan task CSV langsung disinkronkan ulang.")
    _print_task_result(tasks, meta, coverage, output_path)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = connect(Path(args.db))
    try:
        rows = status_rows(conn, calendar_path=Path(args.calendar), to_date=args.to_date)
    finally:
        conn.close()
    print("\nSDE - STATUS HISTORI BROKER PORTFOLIO")
    print("=" * 72)
    _print_coverage(rows)
    if rows and all(int(row.get("missing", 0)) == 0 for row in rows):
        print("\n[OK] Semua posisi OPEN sudah lengkap sampai trading day target.")
    elif rows:
        print("\n[NEXT] Jalankan menu UPDATE HARIAN untuk membuat CSV hanya dari tanggal missing.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple daily broker workflow for actual portfolio")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--calendar", default=str(DEFAULT_CALENDAR))
    parser.add_argument("--output", default=str(DEFAULT_TASKS))
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="Recommended: create tasks only for broker dates missing from DB")
    daily.add_argument("--to-date", default="")
    daily.set_defaults(func=cmd_daily)

    one = sub.add_parser("one", help="Repair/backfill one symbol")
    one.add_argument("--symbol", required=True)
    one.add_argument("--from-date", default="")
    one.add_argument("--to-date", default="")
    one.set_defaults(func=cmd_one)

    imp = sub.add_parser("import", help="Import Tampermonkey result and immediately resync task CSV")
    imp.add_argument("--file", default="")
    imp.add_argument("--downloads", default=str(default_downloads()))
    imp.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE))
    imp.set_defaults(func=cmd_import)

    status = sub.add_parser("status", help="Show broker-history coverage for OPEN positions")
    status.add_argument("--to-date", default="")
    status.set_defaults(func=cmd_status)

    refresh = sub.add_parser("refresh-all", help="Advanced: intentionally re-fetch dates already in DB")
    refresh.add_argument("--to-date", default="")
    refresh.set_defaults(func=cmd_refresh_all)
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
