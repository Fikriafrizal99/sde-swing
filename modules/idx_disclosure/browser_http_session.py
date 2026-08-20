"""HTTP session adapter for the isolated IDX AI reader.

IDX PDF links can reject plain requests even when the announcement API works
through Playwright. This adapter keeps Groq POST traffic on requests while
routing official idx.co.id document GETs through the already-running browser
source when it exposes fetch_document_bytes().
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests


class BrowserDocumentResponse:
    """Minimal requests-like response consumed by GroqDisclosureAIReader."""

    def __init__(self, content: bytes) -> None:
        self.status_code = 200
        self.content = bytes(content)
        self.headers = {"Content-Length": str(len(self.content))}


class PlaywrightBackedGroqSession:
    """Use the watcher browser session for IDX PDFs and requests for Groq."""

    def __init__(
        self,
        source: Any,
        *,
        max_pdf_bytes: int = 25_000_000,
        http_session: Any | None = None,
    ) -> None:
        self.source = source
        self.max_pdf_bytes = max(250_000, int(max_pdf_bytes))
        self.http_session = http_session or requests.Session()

    @staticmethod
    def _is_idx_document_url(url: str) -> bool:
        try:
            parsed = urlparse(str(url))
        except Exception:
            return False
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and host in {"idx.co.id", "www.idx.co.id"}

    def get(self, url: str, **kwargs: Any):
        fetch_document = getattr(self.source, "fetch_document_bytes", None)
        if self._is_idx_document_url(url) and callable(fetch_document):
            data = fetch_document(str(url), max_bytes=self.max_pdf_bytes)
            return BrowserDocumentResponse(data)
        return self.http_session.get(url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.http_session.post(url, **kwargs)

    def close(self) -> None:
        close = getattr(self.http_session, "close", None)
        if callable(close):
            close()
