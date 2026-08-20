from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from modules.idx_disclosure.ai_reader import (
    AIReaderPermanentError,
    GroqDisclosureAIReader,
    format_idx_ai_summary,
)
from modules.idx_disclosure.ai_state import SQLiteDisclosureAIQueue
from modules.idx_disclosure.models import DisclosureAttachment, IDXDisclosure


JAKARTA = ZoneInfo("Asia/Jakarta")


def _disclosure() -> IDXDisclosure:
    return IDXDisclosure(
        id2="dg-groq-test",
        ticker="DGWG",
        announcement_no="0006/DGWG-CORSEC/VIII/2026",
        published_at=datetime(2026, 8, 20, 12, 25, tzinfo=JAKARTA),
        title="Pemberitahuan Rencana Rapat Umum Pemegang Saham Luar Biasa",
        subject="",
        idx_created_at=None,
        attachments=(
            DisclosureAttachment(
                filename="main.pdf",
                url="https://www.idx.co.id/main.pdf",
                is_attachment=False,
            ),
        ),
    )


class _Response:
    def __init__(self, status_code=200, *, content=b"pdf", payload=None):
        self.status_code = status_code
        self.content = content
        self.headers = {}
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _Response(content=b"fake-pdf")

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response(
            payload={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Perseroan merencanakan RUPSLB.",
                                    "key_points": ["Agenda akan disampaikan dalam RUPSLB."],
                                    "important_dates": ["20 Agustus 2026"],
                                    "important_values": [],
                                    "related_parties": ["Perseroan"],
                                    "document_type": "Pemberitahuan RUPSLB",
                                }
                            )
                        }
                    }
                ]
            }
        )


def test_reader_extracts_locally_then_calls_groq_once():
    session = _Session()
    reader = GroqDisclosureAIReader(
        api_key="test-key",
        session=session,
        min_main_text_chars=10,
        pdf_text_extractor=lambda data, pages: "Dokumen resmi RUPSLB dan agenda perseroan. " * 4,
    )

    result = reader.summarize(_disclosure())

    assert result.summary.startswith("Perseroan")
    assert result.documents_read == ("main.pdf",)
    assert len(session.get_calls) == 1
    assert len(session.post_calls) == 1
    text = format_idx_ai_summary(_disclosure(), result)
    assert "RINGKASAN DOKUMEN IDX" in text
    assert "BUY" not in text
    assert "SELL" not in text
    assert "score" not in text.lower()


def test_unreadable_pdf_never_spends_groq_call():
    session = _Session()
    reader = GroqDisclosureAIReader(
        api_key="test-key",
        session=session,
        pdf_text_extractor=lambda data, pages: "",
    )

    with pytest.raises(AIReaderPermanentError, match="NO_EXTRACTABLE_PDF_TEXT"):
        reader.summarize(_disclosure())

    assert session.post_calls == []


def _initialize_official_db(path: Path, *, delivered: bool) -> None:
    item = _disclosure()
    sent_at = item.published_at.isoformat() if delivered else None
    with sqlite3.connect(path) as conn:
        conn.executescript(
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
            );
            CREATE TABLE idx_disclosure_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                disclosure_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                url TEXT NOT NULL,
                is_attachment INTEGER NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO idx_disclosures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id2,
                item.ticker,
                item.announcement_no,
                item.published_at.isoformat(),
                item.title,
                item.subject,
                None,
                item.published_at.isoformat(),
                sent_at,
                0,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO idx_disclosure_attachments(disclosure_id, filename, url, is_attachment)
            VALUES (?, ?, ?, ?)
            """,
            (item.id2, "main.pdf", "https://www.idx.co.id/main.pdf", 0),
        )


def test_ai_queue_waits_for_official_delivery_and_persists_ready(tmp_path: Path):
    path = tmp_path / "idx.db"
    _initialize_official_db(path, delivered=False)
    queue = SQLiteDisclosureAIQueue(path)
    now = datetime(2026, 8, 20, 13, 0, tzinfo=JAKARTA)
    assert queue.enqueue(_disclosure().id2, queued_at=now)

    # AI must never run before the factual official Telegram message succeeds.
    assert queue.pending_generation(now=now, max_attempts=3, limit=1) == ()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE idx_disclosures SET telegram_sent_at=? WHERE id2=?",
            (now.isoformat(), _disclosure().id2),
        )

    pending = queue.pending_generation(now=now, max_attempts=3, limit=1)
    assert len(pending) == 1
    assert pending[0].attempts == 0

    session = _Session()
    summary = GroqDisclosureAIReader(
        api_key="test-key",
        session=session,
        min_main_text_chars=10,
        pdf_text_extractor=lambda data, pages: "Dokumen resmi RUPSLB. " * 10,
    ).summarize(_disclosure())
    queue.mark_ready(_disclosure().id2, summary, processed_at=now)

    assert queue.pending_generation(now=now, max_attempts=3, limit=1) == ()
    reopened = SQLiteDisclosureAIQueue(path)
    ready = reopened.ready_delivery()
    assert len(ready) == 1
    assert ready[0].summary.summary == summary.summary

    reopened.mark_delivered(_disclosure().id2, delivered_at=now)
    assert reopened.ready_delivery() == ()


def test_ai_config_keeps_decision_engine_isolated():
    config = json.loads(Path("config/idx_disclosure.json").read_text(encoding="utf-8"))
    assert config["ai_reader"]["enabled"] is True
    assert config["ai_reader"]["safety"]["decision_engine_write_access"] is False
    assert config["ai_reader"]["safety"]["allow_scoring"] is False
    assert config["ai_reader"]["safety"]["allow_sentiment"] is False
    assert config["ai_reader"]["safety"]["allow_trade_recommendation"] is False
    assert config["safety"]["decision_engine_write_access"] is False
