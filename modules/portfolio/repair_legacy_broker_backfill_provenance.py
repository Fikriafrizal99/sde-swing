#!/usr/bin/env python3
"""Repair provenance metadata for legacy validated portfolio broker backfills.

Older portfolio-backfill snapshots were already stored as one row per trading
session, but their manifest only carried ``source=PORTFOLIO_BACKFILL`` and
``daily_only=true``. Portfolio Management's stricter daily-history reader now
expects an explicit 1D provenance envelope, so those otherwise-valid legacy
rows can be invisible even though coverage reports them as present.

This migration changes metadata only. Raw Broker Summary rows, values, hashes,
and snapshot IDs are never changed. A row is upgraded only when the canonical
DB proves it is exactly one day (broker_date == from_date == to_date) and the
snapshot is explicitly marked as validated portfolio backfill data.
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


def _eligible_legacy_backfill(
    *,
    broker_date: str,
    from_date: str,
    to_date: str,
    data_quality_status: str,
    manifest: dict[str, Any],
) -> bool:
    day = str(broker_date or "").strip()[:10]
    start = str(from_date or "").strip()[:10]
    end = str(to_date or "").strip()[:10]
    if not day or start != day or end != day:
        return False

    source = str(manifest.get("source") or "").strip().upper()
    quality = str(data_quality_status or "").strip().upper()
    validated_backfill = source == "PORTFOLIO_BACKFILL" or quality == "VALID_BACKFILL_DAILY"
    if not validated_backfill:
        return False

    # Fail closed if a manifest explicitly says this is aggregate/multi-day.
    period_type = str(manifest.get("broker_period_type") or "").strip().upper()
    if period_type and period_type not in {"1D", "DAILY"}:
        return False
    if _flag(manifest.get("aggregate_snapshot")):
        return False
    if "daily_only" in manifest and not _flag(manifest.get("daily_only")):
        return False
    return True


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
            broker_date=str(broker_date or ""),
            from_date=str(from_date or ""),
            to_date=str(to_date or ""),
            data_quality_status=str(quality or ""),
            manifest=manifest,
        ):
            continue

        day = str(broker_date)[:10]
        already_normalized = (
            str(manifest.get("broker_period_type") or "").upper() == "1D"
            and str(manifest.get("broker_period_source") or "").upper() in {"STOCKBIT_1D", "STOCKBIT"}
            and _flag(manifest.get("daily_history_eligible"))
            and not _flag(manifest.get("aggregate_snapshot"))
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
                "provenance_repair": "LEGACY_PORTFOLIO_BACKFILL_1D_V1",
            }
        )
        conn.execute(
            "UPDATE broker_snapshots SET manifest_json=? WHERE broker_snapshot_id=?",
            (json.dumps(repaired_manifest, ensure_ascii=False, separators=(",", ":")), snapshot_id),
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
