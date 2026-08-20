"""Durable AI queue state for the isolated IDX disclosure watcher."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ai_reader import AISummary
from .models import DisclosureAttachment, IDXDisclosure


JAKARTA = ZoneInfo("Asia/Jakarta")


@dataclass(frozen=True, slots=True)
class AIGenerationWork:
    disclosure: IDXDisclosure
    attempts: int


@dataclass(frozen=True, slots=True)
class AIReadyWork:
    disclosure: IDXDisclosure
    summary: AISummary


class SQLiteDisclosureAIQueue:
    """AI-only queue stored beside disclosure state, never in the SDE trading DB."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS idx_disclosure_ai (
                    disclosure_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    queued_at TEXT NOT NULL,
                    next_attempt_at TEXT,
                    summary_json TEXT,
                    model TEXT,
                    source_hash TEXT,
                    input_chars INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    processed_at TEXT,
                    telegram_sent_at TEXT,
                    FOREIGN KEY(disclosure_id) REFERENCES idx_disclosures(id2) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_disclosure_ai_pending
                    ON idx_disclosure_ai(status, next_attempt_at, queued_at);
                """
            )

    @staticmethod
    def _dt(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JAKARTA)
        return parsed.astimezone(JAKARTA)

    def enqueue(self, disclosure_id: str, *, queued_at: datetime) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO idx_disclosure_ai(
                    disclosure_id, status, attempts, queued_at
                ) VALUES (?, 'PENDING', 0, ?)
                """,
                (disclosure_id, queued_at.isoformat()),
            )
            return cursor.rowcount > 0

    def enqueue_latest_delivered(self, *, limit: int, queued_at: datetime) -> int:
        if limit <= 0:
            return 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.id2
                FROM idx_disclosures d
                LEFT JOIN idx_disclosure_ai a ON a.disclosure_id=d.id2
                WHERE d.delivery_suppressed=0
                  AND d.telegram_sent_at IS NOT NULL
                  AND a.disclosure_id IS NULL
                ORDER BY d.published_at DESC, d.first_seen_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            count = 0
            for row in rows:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO idx_disclosure_ai(
                        disclosure_id, status, attempts, queued_at
                    ) VALUES (?, 'PENDING', 0, ?)
                    """,
                    (str(row["id2"]), queued_at.isoformat()),
                )
                count += int(cursor.rowcount > 0)
        return count

    def _attachments(self, conn: sqlite3.Connection, disclosure_id: str) -> tuple[DisclosureAttachment, ...]:
        rows = conn.execute(
            """
            SELECT filename, url, is_attachment
            FROM idx_disclosure_attachments
            WHERE disclosure_id=?
            ORDER BY is_attachment ASC, id ASC
            """,
            (disclosure_id,),
        ).fetchall()
        return tuple(
            DisclosureAttachment(
                filename=str(row["filename"]),
                url=str(row["url"]),
                is_attachment=bool(row["is_attachment"]),
            )
            for row in rows
        )

    def _disclosure(self, conn: sqlite3.Connection, row: sqlite3.Row) -> IDXDisclosure:
        published_at = self._dt(str(row["published_at"]))
        assert published_at is not None
        return IDXDisclosure(
            id2=str(row["id2"]),
            ticker=str(row["ticker"]),
            announcement_no=str(row["announcement_no"]),
            published_at=published_at,
            title=str(row["title"]),
            subject=str(row["subject"]),
            idx_created_at=self._dt(row["idx_created_at"]),
            attachments=self._attachments(conn, str(row["id2"])),
            raw_source=row["raw_source"],
        )

    def pending_generation(
        self,
        *,
        now: datetime,
        max_attempts: int,
        limit: int,
    ) -> tuple[AIGenerationWork, ...]:
        if limit <= 0:
            return ()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.*, a.attempts
                FROM idx_disclosure_ai a
                JOIN idx_disclosures d ON d.id2=a.disclosure_id
                WHERE a.status IN ('PENDING', 'RETRY')
                  AND a.attempts < ?
                  AND (a.next_attempt_at IS NULL OR a.next_attempt_at <= ?)
                  AND d.delivery_suppressed=0
                  AND d.telegram_sent_at IS NOT NULL
                ORDER BY a.queued_at ASC, d.published_at ASC
                LIMIT ?
                """,
                (int(max_attempts), now.isoformat(), int(limit)),
            ).fetchall()
            return tuple(
                AIGenerationWork(
                    disclosure=self._disclosure(conn, row),
                    attempts=int(row["attempts"] or 0),
                )
                for row in rows
            )

    def ready_delivery(self, *, limit: int = 10) -> tuple[AIReadyWork, ...]:
        if limit <= 0:
            return ()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.*, a.summary_json
                FROM idx_disclosure_ai a
                JOIN idx_disclosures d ON d.id2=a.disclosure_id
                WHERE a.status='READY'
                  AND a.summary_json IS NOT NULL
                  AND a.telegram_sent_at IS NULL
                  AND d.delivery_suppressed=0
                  AND d.telegram_sent_at IS NOT NULL
                ORDER BY a.processed_at ASC, a.queued_at ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            result: list[AIReadyWork] = []
            for row in rows:
                try:
                    summary = AISummary.from_json(str(row["summary_json"]))
                except Exception:
                    continue
                result.append(AIReadyWork(self._disclosure(conn, row), summary))
            return tuple(result)

    def mark_ready(self, disclosure_id: str, summary: AISummary, *, processed_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE idx_disclosure_ai
                SET status='READY', attempts=attempts+1, next_attempt_at=NULL,
                    summary_json=?, model=?, source_hash=?, input_chars=?,
                    last_error=NULL, processed_at=?
                WHERE disclosure_id=?
                """,
                (
                    summary.to_json(),
                    summary.model,
                    summary.source_hash,
                    int(summary.input_chars),
                    processed_at.isoformat(),
                    disclosure_id,
                ),
            )

    def mark_failure(
        self,
        disclosure_id: str,
        *,
        error: str,
        failed_at: datetime,
        next_attempt_at: datetime | None,
        permanent: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE idx_disclosure_ai
                SET status=?, attempts=attempts+1, last_error=?, processed_at=?, next_attempt_at=?
                WHERE disclosure_id=?
                """,
                (
                    "PERMANENT_FAILED" if permanent else "RETRY",
                    str(error)[:1000],
                    failed_at.isoformat(),
                    next_attempt_at.isoformat() if next_attempt_at else None,
                    disclosure_id,
                ),
            )

    def mark_delivery_error(self, disclosure_id: str, *, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE idx_disclosure_ai SET last_error=? WHERE disclosure_id=? AND status='READY'",
                (f"TELEGRAM:{str(error)[:900]}", disclosure_id),
            )

    def mark_delivered(self, disclosure_id: str, *, delivered_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE idx_disclosure_ai
                SET status='SENT', telegram_sent_at=?, last_error=NULL
                WHERE disclosure_id=? AND status='READY'
                """,
                (delivered_at.isoformat(), disclosure_id),
            )
