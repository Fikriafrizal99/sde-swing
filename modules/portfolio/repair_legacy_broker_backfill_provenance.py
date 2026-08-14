#!/usr/bin/env python3
"""Repair provenance metadata for legacy proven-daily Broker Summary snapshots.

Some old portfolio broker snapshots have a valid ``broker_date`` and valid
Broker Summary rows, but incomplete/legacy manifest metadata. Coverage can see
those dates while Portfolio Management rejects them before scoring.

This migration changes snapshot metadata only. Raw Broker Summary ``row_json``
values, hashes, and snapshot IDs are never changed. The strongest compatibility
proof is the stored row itself: every row in a snapshot must explicitly prove
``FROM_DATE == TO_DATE == broker_date``. Explicit aggregate/multi-day provenance
always wins and is rejected.
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
    """Require every stored Broker Summary row to prove one exact market day."""
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


def _explicitly_aggregate(manifest: dict[str, Any]) -> bool:
    period_type = str(manifest.get("broker_period_type") or "").strip().upper()
    source = str(
        manifest.get("broker_period_source")
        or manifest.get("source")
        or ""
    ).strip().upper()
    if period_type and period_type not in {"1D", "DAILY"}:
        return True
    if _flag(manifest.get("aggregate_snapshot")):
        return True
    if source in {
        "INTERNAL_DAILY_ROLLUP",
        "STOCKBIT_AGGREGATE_EXPORT",
        "CUSTOM_AGGREGATE",
    }:
        return True
    if "daily_only" in manifest and not _flag(manifest.get("daily_only")):
        return True
    return False


def _eligible_snapshot(
    *,
    snapshot_id: str,
    broker_date: str,
    manifest: dict[str, Any],
    conn: sqlite3.Connection,
) -> bool:
    day = str(broker_date or "").strip()[:10]
    if not day or _explicitly_aggregate(manifest):
        return False

    # Do not trust legacy labels. The stored Broker Summary rows themselves
    # are the compatibility proof. This handles snapshots that pre-date the
    # PORTFOLIO_BACKFILL/VALID_BACKFILL_DAILY provenance envelope.
    return _rows_prove_daily(conn, snapshot_id, day)


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
    proven_daily = 0
    for snapshot_id, broker_date, from_date, to_date, _quality, manifest_raw in rows:
        manifest = _manifest(manifest_raw)
        if not _eligible_snapshot(
            snapshot_id=str(snapshot_id or ""),
            broker_date=str(broker_date or ""),
            manifest=manifest,
            conn=conn,
        ):
            continue
        proven_daily += 1

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
                "provenance_repair": "LEGACY_PROVEN_DAILY_V3",
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
    print(
        f"BROKER HISTORY MIGRATION: proven_daily={proven_daily} | repaired={repaired}"
    )
    return repaired


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair proven-daily broker snapshot provenance")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    conn = connect(Path(args.db))
    try:
        repair_legacy_backfill_provenance(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
