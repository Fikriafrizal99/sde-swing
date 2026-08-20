from __future__ import annotations

import base64

from modules.idx_disclosure.browser_client import PlaywrightAnnouncementClient
from modules.idx_disclosure.browser_http_session import PlaywrightBackedGroqSession


class _FakePage:
    def __init__(self, data: bytes):
        self.data = data
        self.calls = []

    def evaluate(self, script, args):
        self.calls.append((script, args))
        return {
            "status": 200,
            "size": len(self.data),
            "base64": base64.b64encode(self.data).decode("ascii"),
        }


class _Source:
    def __init__(self):
        self.calls = []

    def fetch_document_bytes(self, url, *, max_bytes):
        self.calls.append((url, max_bytes))
        return b"%PDF-browser-session"


class _HTTP:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return object()

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return "post-response"


def test_playwright_client_fetches_document_inside_browser_page():
    client = PlaywrightAnnouncementClient(
        endpoint="https://www.idx.co.id/primary/ListedCompany/GetAnnouncement",
        max_retries=0,
    )
    page = _FakePage(b"%PDF-test")
    client._page = page

    data = client.fetch_document_bytes(
        "https://www.idx.co.id/StaticData/test.pdf",
        max_bytes=1_000_000,
    )

    assert data == b"%PDF-test"
    assert len(page.calls) == 1
    assert page.calls[0][1]["url"].endswith("test.pdf")


def test_ai_session_routes_idx_get_to_browser_and_groq_post_to_http():
    source = _Source()
    http = _HTTP()
    session = PlaywrightBackedGroqSession(
        source,
        max_pdf_bytes=2_000_000,
        http_session=http,
    )

    response = session.get("https://www.idx.co.id/StaticData/document.pdf")
    post_result = session.post("https://api.groq.com/openai/v1/chat/completions", json={})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert source.calls == [
        ("https://www.idx.co.id/StaticData/document.pdf", 2_000_000)
    ]
    assert http.get_calls == []
    assert len(http.post_calls) == 1
    assert post_result == "post-response"


def test_non_idx_get_stays_on_normal_http_session():
    source = _Source()
    http = _HTTP()
    session = PlaywrightBackedGroqSession(source, http_session=http)

    session.get("https://example.com/document.pdf", timeout=5)

    assert source.calls == []
    assert len(http.get_calls) == 1
