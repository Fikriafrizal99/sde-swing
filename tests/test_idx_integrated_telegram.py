from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.idx_disclosure.ai_reader import AISummary
from modules.idx_disclosure.ai_state import SQLiteDisclosureAIQueue
from modules.idx_disclosure.client import AnnouncementPage
from modules.idx_disclosure.repository import SQLiteDisclosureRepository
from modules.idx_disclosure.watcher import IDXDisclosureWatcher


JAKARTA = ZoneInfo("Asia/Jakarta")


def _raw_reply():
    return {
        "pengumuman": {
            "Id2": "integrated-message-id",
            "NoPengumuman": "0006/TEST/VIII/2026",
            "TglPengumuman": "2026-08-20T13:40:00",
            "JudulPengumuman": "Pemberitahuan Rencana Rapat Umum Pemegang Saham Luar Biasa",
            "Kode_Emiten": "TEST",
            "CreatedDate": "2026-08-20T13:40:00",
            "PerihalPengumuman": "RUPSLB",
        },
        "attachments": [
            {
                "OriginalFilename": "main.pdf",
                "FullSavePath": "https://www.idx.co.id/main.pdf",
                "IsAttachment": False,
            }
        ],
    }


class Source:
    def fetch_page(self, *, trade_date, index_from=0, page_size=50):
        return AnnouncementPage(1, (_raw_reply(),), index_from, page_size)


class AIProcessor:
    def __init__(self):
        self.calls = 0

    def summarize(self, disclosure):
        self.calls += 1
        return AISummary(
            summary=(
                "Perseroan akan melaksanakan RUPSLB pada 28 September 2026. "
                "Pemegang saham yang berhak hadir ditentukan pada 3 September 2026."
            ),
            key_points=(
                "RUPSLB: 28 September 2026 pukul 09.00 WIB",
                "DPS/record date: 3 September 2026 pukul 16.00 WIB",
                "Pemanggilan: paling lambat 4 September 2026",
                "Usulan agenda: minimal 1/20 saham",
            ),
            important_dates=("28 September 2026 – Pelaksanaan RUPSLB",),
            model="openai/gpt-oss-120b",
            input_chars=8000,
        )


class Delivery:
    def __init__(self, *, fail_first_edit: bool = False):
        self.sent = []
        self.edited = []
        self.fail_first_edit = fail_first_edit

    def send(self, disclosure, text):
        self.sent.append((disclosure.id2, text))
        return 777

    def edit(self, disclosure, text, *, message_id):
        if self.fail_first_edit:
            self.fail_first_edit = False
            raise RuntimeError("temporary edit failure")
        self.edited.append((disclosure.id2, message_id, text))


def _watcher(tmp_path: Path, delivery: Delivery, processor: AIProcessor):
    db = tmp_path / "idx.db"
    repo = SQLiteDisclosureRepository(db)
    repo.mark_initialized(
        initialized_at=datetime(2026, 8, 20, 13, 39, tzinfo=JAKARTA)
    )
    queue = SQLiteDisclosureAIQueue(db)
    now = lambda: datetime(2026, 8, 20, 13, 42, tzinfo=JAKARTA)
    watcher = IDXDisclosureWatcher(
        Source(),
        repo,
        delivery=delivery,
        delivery_enabled=True,
        now=now,
        ai_processor=processor,
        ai_queue=queue,
        ai_enabled=True,
        ai_max_documents_per_poll=1,
    )
    return watcher, repo, queue


def test_ai_edits_the_original_official_message(tmp_path: Path):
    delivery = Delivery()
    processor = AIProcessor()
    watcher, repo, queue = _watcher(tmp_path, delivery, processor)

    result = watcher.poll_once(jakarta_date=date(2026, 8, 20))

    assert result.delivered == 1
    assert result.ai_generated == 1
    assert result.ai_delivered == 1
    assert result.ai_failed == 0
    assert processor.calls == 1
    assert len(delivery.sent) == 1
    assert len(delivery.edited) == 1
    assert repo.telegram_message_id("integrated-message-id") == 777

    _, message_id, final_text = delivery.edited[0]
    assert message_id == 777
    assert "IDX KETERBUKAAN INFORMASI" in final_text
    assert "Ringkasan AI" in final_text
    assert "Poin utama" in final_text
    assert "Pihak terkait" not in final_text
    assert len(final_text) <= 3900
    assert queue.ready_delivery() == ()


def test_failed_edit_retries_without_recalling_ai(tmp_path: Path):
    delivery = Delivery(fail_first_edit=True)
    processor = AIProcessor()
    watcher, _, queue = _watcher(tmp_path, delivery, processor)

    first = watcher.poll_once(jakarta_date=date(2026, 8, 20))
    assert first.ai_generated == 1
    assert first.ai_delivered == 0
    assert first.ai_failed == 1
    assert processor.calls == 1
    assert len(queue.ready_delivery()) == 1

    second = watcher.poll_once(jakarta_date=date(2026, 8, 20))
    assert second.ai_generated == 0
    assert second.ai_delivered == 1
    assert second.ai_failed == 0
    assert processor.calls == 1
    assert len(delivery.edited) == 1
    assert queue.ready_delivery() == ()


def test_repository_migrates_legacy_db_with_message_id_column(tmp_path: Path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE idx_disclosures (
                id2 TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                announcement_no TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL,
                title TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                idx_created_at TEXT,
                first_seen_at TEXT NOT NULL,
                telegram_sent_at TEXT,
                delivery_suppressed INTEGER NOT NULL DEFAULT 0,
                raw_source TEXT
            )
            """
        )

    SQLiteDisclosureRepository(db)
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(idx_disclosures)")}
    assert "telegram_message_id" in columns
