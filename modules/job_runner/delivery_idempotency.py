from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from swing_utils import read_json, write_json


DELIVERY_STATE_SCHEMA_VERSION = "SDE_TELEGRAM_IDEMPOTENCY_V1"


class ReservationOwnershipLost(RuntimeError):
    """Raised when a sender no longer owns the durable delivery reservation."""


@dataclass(frozen=True)
class ReservationDecision:
    acquired: bool
    reason: str
    key: str
    attempt_id: str = ""
    owner_token: str = ""
    lease_expires_at: str = ""


class DeliveryIdempotencyStore:
    """SQLite-backed check-and-reserve state for Telegram delivery.

    The database is authoritative. The historical JSON index is maintained as
    a backward-compatible projection of successfully delivered keys.

    This prevents concurrent in-process and cross-process check/send races. It
    deliberately does not claim exactly-once delivery: a process can still die
    after Telegram accepts a message but before the local SENT transaction is
    committed. A stale lease then permits a retry, which is the least-bad
    recoverable behavior in the absence of a Telegram idempotency API.
    """

    def __init__(self, database_path: Path, legacy_index_path: Path, lease_seconds: int = 900):
        self.database_path = Path(database_path)
        self.legacy_index_path = Path(legacy_index_path)
        self.lease_seconds = max(60, int(lease_seconds))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _parse_iso(value: str) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value or ""))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_delivery_state (
                    idempotency_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    owner_token TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    force_count INTEGER NOT NULL DEFAULT 0,
                    event_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS telegram_delivery_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    owner_token TEXT NOT NULL,
                    force_resend INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    event_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_telegram_delivery_attempts_key
                    ON telegram_delivery_attempts(idempotency_key, reserved_at);

                CREATE TABLE IF NOT EXISTS telegram_delivery_meta (
                    meta_key TEXT PRIMARY KEY,
                    meta_value TEXT NOT NULL
                );
                """
            )
            self._migrate_legacy_index(conn)
        finally:
            conn.close()

    def _migrate_legacy_index(self, conn: sqlite3.Connection) -> None:
        legacy = read_json(self.legacy_index_path)
        now = self._iso(self._utc_now())
        conn.execute("BEGIN IMMEDIATE")
        try:
            migrated = conn.execute(
                "SELECT meta_value FROM telegram_delivery_meta WHERE meta_key='legacy_json_migrated'"
            ).fetchone()
            if migrated is not None:
                conn.commit()
                return
            if isinstance(legacy, dict):
                for key, event in legacy.items():
                    normalized_key = str(key).strip()
                    if not normalized_key:
                        continue
                    attempt_id = "legacy-" + hashlib.sha256(
                        normalized_key.encode("utf-8")
                    ).hexdigest()
                    rendered = json.dumps(event, ensure_ascii=False, default=str)
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO telegram_delivery_state (
                            idempotency_key, status, owner_token, attempt_id,
                            run_id, reserved_at, lease_expires_at, updated_at,
                            attempt_count, force_count, event_json, last_error
                        ) VALUES (?, 'SENT', 'LEGACY_IMPORT', ?, 'LEGACY_IMPORT',
                                  ?, ?, ?, 1, 0, ?, '')
                        """,
                        (normalized_key, attempt_id, now, now, now, rendered),
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO telegram_delivery_attempts (
                            attempt_id, idempotency_key, run_id, owner_token,
                            force_resend, status, reserved_at, completed_at,
                            event_json, error
                        ) VALUES (?, ?, 'LEGACY_IMPORT', 'LEGACY_IMPORT', 0,
                                  'SENT', ?, ?, ?, '')
                        """,
                        (attempt_id, normalized_key, now, now, rendered),
                    )
            conn.execute(
                "INSERT INTO telegram_delivery_meta(meta_key, meta_value) VALUES (?, ?)",
                ("legacy_json_migrated", now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO telegram_delivery_meta(meta_key, meta_value) VALUES (?, ?)",
                ("schema_version", DELIVERY_STATE_SCHEMA_VERSION),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def can_send(self, key: str, force: bool = False) -> tuple[bool, str]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status, lease_expires_at FROM telegram_delivery_state WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return True, key
        if row["status"] == "SENT" and not force:
            return False, "DUPLICATE_SUPPRESSED"
        if row["status"] == "RESERVED":
            lease = self._parse_iso(row["lease_expires_at"])
            if lease is not None and lease > self._utc_now():
                return False, "DELIVERY_IN_PROGRESS"
        return True, key

    def reserve(self, key: str, run_id: str, force: bool = False) -> ReservationDecision:
        now_dt = self._utc_now()
        now = self._iso(now_dt)
        lease_expires = self._iso(now_dt + timedelta(seconds=self.lease_seconds))
        owner_token = uuid.uuid4().hex
        attempt_id = uuid.uuid4().hex
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM telegram_delivery_state WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if row is not None and row["status"] == "SENT" and not force:
                conn.commit()
                return ReservationDecision(False, "DUPLICATE_SUPPRESSED", key)

            if row is not None and row["status"] == "RESERVED":
                existing_lease = self._parse_iso(row["lease_expires_at"])
                if existing_lease is not None and existing_lease > now_dt:
                    conn.commit()
                    return ReservationDecision(
                        False,
                        "DELIVERY_IN_PROGRESS",
                        key,
                        str(row["attempt_id"]),
                        "",
                        str(row["lease_expires_at"]),
                    )
                conn.execute(
                    """
                    UPDATE telegram_delivery_attempts
                    SET status='STALE_RECLAIMED', completed_at=?,
                        error='RESERVATION_LEASE_EXPIRED'
                    WHERE attempt_id=? AND status='RESERVED'
                    """,
                    (now, row["attempt_id"]),
                )

            attempt_count = int(row["attempt_count"] if row is not None else 0) + 1
            force_count = int(row["force_count"] if row is not None else 0) + int(bool(force))
            conn.execute(
                """
                INSERT INTO telegram_delivery_state (
                    idempotency_key, status, owner_token, attempt_id, run_id,
                    reserved_at, lease_expires_at, updated_at, attempt_count,
                    force_count, event_json, last_error
                ) VALUES (?, 'RESERVED', ?, ?, ?, ?, ?, ?, ?, ?, '{}', '')
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    status='RESERVED', owner_token=excluded.owner_token,
                    attempt_id=excluded.attempt_id, run_id=excluded.run_id,
                    reserved_at=excluded.reserved_at,
                    lease_expires_at=excluded.lease_expires_at,
                    updated_at=excluded.updated_at,
                    attempt_count=excluded.attempt_count,
                    force_count=excluded.force_count, event_json='{}',
                    last_error=''
                """,
                (
                    key,
                    owner_token,
                    attempt_id,
                    str(run_id),
                    now,
                    lease_expires,
                    now,
                    attempt_count,
                    force_count,
                ),
            )
            conn.execute(
                """
                INSERT INTO telegram_delivery_attempts (
                    attempt_id, idempotency_key, run_id, owner_token,
                    force_resend, status, reserved_at
                ) VALUES (?, ?, ?, ?, ?, 'RESERVED', ?)
                """,
                (attempt_id, key, str(run_id), owner_token, int(bool(force)), now),
            )
            conn.commit()
            return ReservationDecision(
                True,
                "RESERVED",
                key,
                attempt_id,
                owner_token,
                lease_expires,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def renew(self, reservation: ReservationDecision) -> str:
        now_dt = self._utc_now()
        now = self._iso(now_dt)
        lease_expires = self._iso(now_dt + timedelta(seconds=self.lease_seconds))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE telegram_delivery_state
                SET lease_expires_at=?, updated_at=?
                WHERE idempotency_key=? AND status='RESERVED'
                  AND owner_token=? AND attempt_id=?
                """,
                (
                    lease_expires,
                    now,
                    reservation.key,
                    reservation.owner_token,
                    reservation.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise ReservationOwnershipLost(
                    f"Delivery reservation ownership lost for {reservation.key}"
                )
            conn.commit()
            return lease_expires
        finally:
            conn.close()

    def complete(
        self,
        reservation: ReservationDecision,
        event: dict[str, Any],
    ) -> str:
        now = self._iso(self._utc_now())
        rendered = json.dumps(event, ensure_ascii=False, default=str)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE telegram_delivery_state
                SET status='SENT', updated_at=?, lease_expires_at=?,
                    event_json=?, last_error=''
                WHERE idempotency_key=? AND status='RESERVED'
                  AND owner_token=? AND attempt_id=?
                """,
                (
                    now,
                    now,
                    rendered,
                    reservation.key,
                    reservation.owner_token,
                    reservation.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise ReservationOwnershipLost(
                    f"Cannot commit SENT state without ownership for {reservation.key}"
                )
            conn.execute(
                """
                UPDATE telegram_delivery_attempts
                SET status='SENT', completed_at=?, event_json=?, error=''
                WHERE attempt_id=? AND owner_token=?
                """,
                (now, rendered, reservation.attempt_id, reservation.owner_token),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        try:
            self.refresh_legacy_projection()
        except Exception as exc:
            # SQLite is authoritative. A projection failure must not downgrade
            # an acknowledged Telegram send to FAILED and trigger a duplicate.
            return str(exc)
        return ""

    def fail(
        self,
        reservation: ReservationDecision,
        event: dict[str, Any],
        error: str,
    ) -> bool:
        now = self._iso(self._utc_now())
        rendered = json.dumps(event, ensure_ascii=False, default=str)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE telegram_delivery_state
                SET status='FAILED', updated_at=?, lease_expires_at=?,
                    event_json=?, last_error=?
                WHERE idempotency_key=? AND status='RESERVED'
                  AND owner_token=? AND attempt_id=?
                """,
                (
                    now,
                    now,
                    rendered,
                    str(error),
                    reservation.key,
                    reservation.owner_token,
                    reservation.attempt_id,
                ),
            )
            conn.execute(
                """
                UPDATE telegram_delivery_attempts
                SET status='FAILED', completed_at=?, event_json=?, error=?
                WHERE attempt_id=? AND owner_token=? AND status='RESERVED'
                """,
                (
                    now,
                    rendered,
                    str(error),
                    reservation.attempt_id,
                    reservation.owner_token,
                ),
            )
            conn.commit()
            return cursor.rowcount == 1
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def refresh_legacy_projection(self) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT idempotency_key, event_json
                FROM telegram_delivery_state
                WHERE status='SENT'
                ORDER BY idempotency_key
                """
            ).fetchall()
            projection: dict[str, Any] = {}
            for row in rows:
                try:
                    projection[str(row["idempotency_key"])] = json.loads(row["event_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    projection[str(row["idempotency_key"])] = {
                        "status": "SENT",
                        "idempotency_key": str(row["idempotency_key"]),
                    }
            write_json(self.legacy_index_path, projection)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def integrity_check(self) -> str:
        conn = self._connect()
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return str(row[0] if row else "")
        finally:
            conn.close()
