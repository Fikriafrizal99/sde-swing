#!/usr/bin/env python3
"""Repair provenance metadata for legacy validated portfolio broker backfills.

Older portfolio-backfill snapshots were already stored as one row per trading
session, but some generations carried incomplete snapshot metadata. Coverage
could therefore see a broker_date while Portfolio Management rejected the same
snapshot as not proven 1D.

This migration changes metadata only. Raw Broker Summary row_json values,
hashes, and snapshot IDs are never changed. A snapshot is upgraded only when
it is a validated portfolio backfill and either its snapshot dates are already
exactly daily or every stored Broker Summary row proves
``FROM_DATE == TO_DATE == broker_date``.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.database.swing_history_db import connect, init_schema

DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"


def _flag(value: Any) -> bool:
    return value is True or str(value or "").strip().upper() in {"1", "TRUE", "YES", "Y"}


def _manifest(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _norm_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _payload_date(payload: dict[str, Any], *aliases: str) -> str:
    wanted = {_norm_key(alias) for alias in aliases}
    for key, value in payload.items():
        if _norm_key(key) in wanted:
            return str(value or "").strip()[:10]
    return ""


def _rows_prove_daily(conn: sqlite3.Connection, snapshot_id: str, day: str) -> bool:
    """Require every stored summary row to explicitly prove the same 1D date."""
    rows = conn.execute(
        "SELECT row_json FROM broker_summary WHERE broker_snapshot_id=?",
        (snapshot_id,),
    ).fetchall()
    if not rows:
        return False

    for (raw,) in rows:
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        start = _payload_date(payload, "FROM_DATE", "Broker_From_Date")
        end = _payload_date(payload, "TO_DATE", "Broker_To_Date")
        if not start or not end or start != day or end != day:
            return False
    return True


def _eligible_legacy_backfill(
    *,
    snapshot_id: str,
    broker_date: str,
    from_date: str,
    to_date: str,
    data_quality_status: str,
    manifest: dict[str, Any],
    conn: sqlite3.Connection,
) -> bool:
    day = str(broker_date or "").strip()[:10]
    start = str(from_date or "").strip()[:10]
    end = str(to_date or "").strip()[:10]
    if not day:
        return False

    source = str(manifest.get("source") or "").strip().upper()
    quality = str(data_quality_status or "").strip().upper()
    daily_only = _flag(manifest.get("daily_only"))
    validated_backfill = (
        source == "PORTFOLIO_BACKFILL"
        or quality == "VALID_BACKFILL_DAILY"
        or daily_only
    )
    if not validated_backfill:
        return False

    # Fail closed if provenance explicitly says aggregate/multi-day.
    period_type = str(manifest.get("broker_period_type") or "").strip().upper()
    if period_type and period_type not in {"1D", "DAILY"}:
        return False
    if _flag(manifest.get("aggregate_snapshot")):
        return False
    if "daily_only" in manifest and not daily_only:
        return False

    metadata_proves_daily = start == day and end == day
    row_data_proves_daily = _rows_prove_daily(conn, snapshot_id, day)
    return metadata_proves_daily or row_data_proves_daily


def repair_legacy_backfill_provenance(conn: sqlite3.Connection) -> int:
    init_schema(conn)
    rows = conn.execute(
        """
        SELECT broker_snapshot_id, broker_date, from_date, to_date,
               data_quality_status, manifest_json
        FROM broker_snapshots
        ORDER BY broker_date, broker_snapshot_id
        """
    ).fetchall()

    repaired = 0
    for snapshot_id, broker_date, from_date, to_date, quality, manifest_raw in rows:
        manifest = _manifest(manifest_raw)
        if not _eligible_legacy_backfill(
            snapshot_id=str(snapshot_id or ""),
            broker_date=str(broker_date or ""),
            from_date=str(from_date or ""),
            to_date=str(to_date or ""),
            data_quality_status=str(quality or ""),
            manifest=manifest,
            conn=conn,
        ):
            continue

        day = str(broker_date)[:10]
        already_normalized = (
            str(manifest.get("broker_period_type") or "").upper() == "1D"
            and str(manifest.get("broker_period_source") or "").upper() in {"STOCKBIT_1D", "STOCKBIT"}
            and _flag(manifest.get("daily_history_eligible"))
            and not _flag(manifest.get("aggregate_snapshot"))
            and str(from_date or "")[:10] == day
            and str(to_date or "")[:10] == day
        )
        if already_normalized:
            continue

        repaired_manifest = dict(manifest)
        repaired_manifest.update(
            {
                "broker_period_type": "1D",
                "broker_period_start": day,
                "broker_period_end": day,
                "broker_trading_days": 1,
                "broker_period_source": "STOCKBIT_1D",
                "daily_history_eligible": True,
                "aggregate_snapshot": False,
                "broker_period_complete": True,
                "broker_session_dates": [day],
                "broker_date": day,
                "from_date": day,
                "to_date": day,
                "daily_only": True,
                "provenance_repair": "LEGACY_PORTFOLIO_BACKFILL_1D_V2",
            }
        )
        conn.execute(
            """
            UPDATE broker_snapshots
            SET from_date=?, to_date=?, manifest_json=?
            WHERE broker_snapshot_id=?
            """,
            (
                day,
                day,
                json.dumps(repaired_manifest, ensure_ascii=False, separators=(",", ":")),
                snapshot_id,
            ),
        )
        repaired += 1

    if repaired:
        conn.commit()
    return repaired


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair legacy daily portfolio broker provenance")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn = connect(Path(args.db))
    try:
        repaired = repair_legacy_backfill_provenance(conn)
    finally:
        conn.close()
    if repaired:
        print(f"BROKER HISTORY MIGRATION: repaired {repaired} legacy daily snapshot(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
