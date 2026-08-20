"""SQLite persistence for IDX disclosure deduplication and retry state."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .models import DisclosureAttachment, IDXDisclosure


JAKARTA = ZoneInfo("Asia/Jakarta")


class DisclosureRepository(Protocol):
    def is_initialized(self) -> bool: ...
    def mark_initialized(self, *, initialized_at: datetime) -> None: ...
    def contains(self, disclosure_id: str) -> bool: ...
    def save(
        self,
        disclosure: IDXDisclosure,
        *,
        first_seen_at: datetime,
        suppress_delivery: bool = False,
    ) -> None: ...
    def mark_delivered(
        self,
        disclosure_id: str,
        *,
        delivered_at: datetime,
        telegram_message_id: int | None = None,
    ) -> None: ...
    def telegram_message_id(self, disclosure_id: str) -> int | None: ...
    def pending_delivery(self) -> tuple[IDXDisclosure, ...]: ...


class SQLiteDisclosureRepository:
    """Small standalone state store; no writes to the SDE trading database."""

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
                CREATE TABLE IF NOT EXISTS idx_disclosures (
                    id2 TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    announcement_no TEXT NOT NULL DEFAULT '',
                    published_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    idx_created_at TEXT,
                    first_seen_at TEXT NOT NULL,
                    telegram_sent_at TEXT,
                    telegram_message_id INTEGER,
                    delivery_suppressed INTEGER NOT NULL DEFAULT 0,
                    raw_source TEXT
                );

                CREATE TABLE IF NOT EXISTS idx_disclosure_attachments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    disclosure_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    url TEXT NOT NULL,
                    is_attachment INTEGER NOT NULL,
                    FOREIGN KEY(disclosure_id) REFERENCES idx_disclosures(id2) ON DELETE CASCADE,
                    UNIQUE(disclosure_id, url)
                );

                CREATE TABLE IF NOT EXISTS idx_disclosure_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_disclosures_pending
                    ON idx_disclosures(delivery_suppressed, telegram_sent_at, published_at);
                """
            )

            # Existing production databases predate one-message AI editing.
            # Migrate in place without rebuilding or touching disclosure rows.
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(idx_disclosures)").fetchall()
            }
            if "telegram_message_id" not in columns:
                conn.execute(
                    "ALTER TABLE idx_disclosures ADD COLUMN telegram_message_id INTEGER"
                )

    def is_initialized(self) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM idx_disclosure_meta WHERE key='baseline_initialized_at'"
            ).fetchone()
        return row is not None and bool(str(row["value"]).strip())

    def mark_initialized(self, *, initialized_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO idx_disclosure_meta(key, value)
                VALUES('baseline_initialized_at', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (initialized_at.isoformat(),),
            )

    def contains(self, disclosure_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM idx_disclosures WHERE id2=? LIMIT 1",
                (disclosure_id,),
            ).fetchone()
        return row is not None

    def save(
        self,
        disclosure: IDXDisclosure,
        *,
        first_seen_at: datetime,
        suppress_delivery: bool = False,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO idx_disclosures(
                    id2, ticker, announcement_no, published_at, title, subject,
                    idx_created_at, first_seen_at, telegram_sent_at,
                    telegram_message_id, delivery_suppressed, raw_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                """,
                (
                    disclosure.id2,
                    disclosure.ticker,
                    disclosure.announcement_no,
                    disclosure.published_at.isoformat(),
                    disclosure.title,
                    disclosure.subject,
                    disclosure.idx_created_at.isoformat() if disclosure.idx_created_at else None,
                    first_seen_at.isoformat(),
                    1 if suppress_delivery else 0,
                    disclosure.raw_source,
                ),
            )
            for attachment in disclosure.attachments:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO idx_disclosure_attachments(
                        disclosure_id, filename, url, is_attachment
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        disclosure.id2,
                        attachment.filename,
                        attachment.url,
                        1 if attachment.is_attachment else 0,
                    ),
                )

    def mark_delivered(
        self,
        disclosure_id: str,
        *,
        delivered_at: datetime,
        telegram_message_id: int | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE idx_disclosures
                SET telegram_sent_at=?, telegram_message_id=COALESCE(?, telegram_message_id)
                WHERE id2=? AND delivery_suppressed=0
                """,
                (
                    delivered_at.isoformat(),
                    int(telegram_message_id) if telegram_message_id else None,
                    disclosure_id,
                ),
            )

    def telegram_message_id(self, disclosure_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT telegram_message_id FROM idx_disclosures WHERE id2=?",
                (disclosure_id,),
            ).fetchone()
        if row is None or row["telegram_message_id"] is None:
            return None
        try:
            value = int(row["telegram_message_id"])
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

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

    @staticmethod
    def _dt(value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JAKARTA)
        return parsed.astimezone(JAKARTA)

    def pending_delivery(self) -> tuple[IDXDisclosure, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM idx_disclosures
                WHERE delivery_suppressed=0 AND telegram_sent_at IS NULL
                ORDER BY published_at ASC, first_seen_at ASC
                """
            ).fetchall()
            disclosures = []
            for row in rows:
                published_at = self._dt(str(row["published_at"]))
                assert published_at is not None
                disclosures.append(
                    IDXDisclosure(
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
                )
        return tuple(disclosures)
