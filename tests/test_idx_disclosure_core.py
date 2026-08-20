from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.idx_disclosure.client import IDXAnnouncementClient
from modules.idx_disclosure.formatter import format_idx_disclosure
from modules.idx_disclosure.normalizer import normalize_reply
from modules.idx_disclosure.repository import SQLiteDisclosureRepository
from modules.idx_disclosure.watcher import IDXDisclosureWatcher


JAKARTA = ZoneInfo("Asia/Jakarta")


def raw_reply(id2: str = "20260820104149-BEEF_id-id", ticker: str = "BEEF"):
    return {
        "pengumuman": {
            "Id2": id2,
            "NoPengumuman": "B.027-Corpsec-ETT-BEEF-VIII-2026",
            "TglPengumuman": "2026-08-20T10:41:49",
            "JudulPengumuman": "Rencana Penyelenggaraan Public Expose - Insidentil",
            "Kode_Emiten": ticker + " " * 12,
            "CreatedDate": "2026-08-20T10:45:00",
            "PerihalPengumuman": "Rencana Penyelenggaraan Public",
        },
        "attachments": [
            {"OriginalFilename": "main.pdf", "FullSavePath": "https://www.idx.co.id/main.pdf", "IsAttachment": False},
            {"OriginalFilename": "lamp1.pdf", "FullSavePath": "https://www.idx.co.id/lamp1.pdf", "IsAttachment": True},
        ],
    }


class FakeResponse:
    status_code = 200
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): return None
    def json(self): return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.headers = {}
        self.calls = []
    def get(self, endpoint, *, params, timeout):
        self.calls.append((endpoint, params, timeout))
        return FakeResponse(self.payload)


def test_client_uses_current_date_and_global_issuer_filter():
    session = FakeSession({"ResultCount": 1, "Replies": [raw_reply()]})
    client = IDXAnnouncementClient(session=session, max_retries=0)
    page = client.fetch_page(trade_date=date(2026, 8, 20), page_size=50)
    assert page.result_count == 1
    _, params, _ = session.calls[0]
    assert params["kodeEmiten"] == ""
    assert params["dateFrom"] == "20260820"
    assert params["dateTo"] == "20260820"
    assert params["pageSize"] == 50


def test_normalizer_strips_ticker_and_keeps_idx_links():
    item = normalize_reply(raw_reply())
    assert item.ticker == "BEEF"
    assert item.published_at.tzinfo is not None
    assert item.attachments[0].is_attachment is False
    assert item.attachments[1].url.endswith("lamp1.pdf")


def test_formatter_is_factual_and_links_documents():
    text = format_idx_disclosure(normalize_reply(raw_reply()))
    assert "IDX KETERBUKAAN INFORMASI" in text
    assert "Dokumen Utama" in text
    assert "Lampiran 1" in text
    assert "BUY" not in text
    assert "SELL" not in text
    assert "score" not in text.lower()


def test_repository_dedup_survives_reopen(tmp_path: Path):
    path = tmp_path / "idx.db"
    item = normalize_reply(raw_reply())
    now = datetime(2026, 8, 20, 11, 0, tzinfo=JAKARTA)
    repo = SQLiteDisclosureRepository(path)
    repo.save(item, first_seen_at=now, suppress_delivery=False)
    assert repo.contains(item.id2)
    reopened = SQLiteDisclosureRepository(path)
    assert reopened.contains(item.id2)
    assert reopened.pending_delivery()[0].id2 == item.id2


class SequenceSource:
    def __init__(self, batches):
        self.batches = list(batches)
        self.position = 0
    def fetch_page(self, *, trade_date, index_from=0, page_size=50):
        from modules.idx_disclosure.client import AnnouncementPage
        batch = self.batches[min(self.position, len(self.batches) - 1)]
        self.position += 1
        return AnnouncementPage(len(batch), tuple(batch), index_from, page_size)


class RecordingDelivery:
    def __init__(self): self.items = []
    def send(self, disclosure, text): self.items.append((disclosure.id2, text))


def test_first_run_seeds_without_delivery_then_new_item_delivers(tmp_path: Path):
    source = SequenceSource([[raw_reply("old-id", "BEEF")], [raw_reply("new-id", "NAYZ"), raw_reply("old-id", "BEEF")]])
    repo = SQLiteDisclosureRepository(tmp_path / "idx.db")
    delivery = RecordingDelivery()
    fixed_now = lambda: datetime(2026, 8, 20, 11, 0, tzinfo=JAKARTA)
    watcher = IDXDisclosureWatcher(source, repo, delivery=delivery, delivery_enabled=True, now=fixed_now)
    first = watcher.poll_once(jakarta_date=date(2026, 8, 20))
    assert first.baseline_seeded == 1 and first.delivered == 0 and delivery.items == []
    second = watcher.poll_once(jakarta_date=date(2026, 8, 20))
    assert second.discovered == 1 and second.delivered == 1
    assert delivery.items[0][0] == "new-id"


def test_dry_run_items_never_become_future_delivery_backlog(tmp_path: Path):
    source = SequenceSource([[raw_reply("old-id", "BEEF")], [raw_reply("dry-id", "NAYZ"), raw_reply("old-id", "BEEF")]])
    repo = SQLiteDisclosureRepository(tmp_path / "idx.db")
    fixed_now = lambda: datetime(2026, 8, 20, 11, 0, tzinfo=JAKARTA)
    watcher = IDXDisclosureWatcher(source, repo, delivery_enabled=False, now=fixed_now)
    watcher.poll_once(jakarta_date=date(2026, 8, 20))
    watcher.poll_once(jakarta_date=date(2026, 8, 20))
    assert repo.pending_delivery() == ()
