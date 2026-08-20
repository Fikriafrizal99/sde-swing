"""Direct HTTP adapter for IDX listed-company announcements."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Protocol, Sequence

import requests


DEFAULT_ENDPOINT = "https://www.idx.co.id/primary/ListedCompany/GetAnnouncement"


class IDXClientError(RuntimeError):
    """Raised when IDX cannot be queried or its response contract is invalid."""


@dataclass(frozen=True, slots=True)
class AnnouncementPage:
    result_count: int
    replies: Sequence[Mapping[str, Any]]
    index_from: int
    page_size: int


class AnnouncementSource(Protocol):
    def fetch_page(
        self,
        *,
        trade_date: date,
        index_from: int = 0,
        page_size: int = 50,
    ) -> AnnouncementPage:
        """Fetch one page of IDX announcements for all issuers."""
        ...


class IDXAnnouncementClient:
    """Small, persistent-session client for the public GetAnnouncement endpoint."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 10,
        max_retries: int = 3,
        backoff_seconds: Sequence[float] = (2, 4, 8),
        emiten_type: str = "*",
        language: str = "id",
        keyword: str = "",
        session: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.backoff_seconds = tuple(float(value) for value in backoff_seconds)
        self.emiten_type = emiten_type
        self.language = language
        self.keyword = keyword
        self.session = session or requests.Session()
        self.sleep = sleep
        headers = getattr(self.session, "headers", None)
        if headers is not None:
            headers.update({
                "Accept": "application/json, text/plain, */*",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/142.0 Safari/537.36"
                ),
                "Referer": "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/",
            })

    def _params(self, trade_date: date, index_from: int, page_size: int) -> dict[str, Any]:
        if index_from < 0:
            raise ValueError("index_from must be >= 0")
        if page_size <= 0:
            raise ValueError("page_size must be > 0")
        date_value = trade_date.strftime("%Y%m%d")
        return {
            "kodeEmiten": "",
            "emitenType": self.emiten_type,
            "indexFrom": index_from,
            "pageSize": page_size,
            "dateFrom": date_value,
            "dateTo": date_value,
            "lang": self.language,
            "keyword": self.keyword,
        }

    def _backoff_for(self, retry_index: int) -> float:
        if not self.backoff_seconds:
            return 0.0
        return self.backoff_seconds[min(retry_index, len(self.backoff_seconds) - 1)]

    def fetch_page(
        self,
        *,
        trade_date: date,
        index_from: int = 0,
        page_size: int = 50,
    ) -> AnnouncementPage:
        params = self._params(trade_date, index_from, page_size)
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(
                    self.endpoint,
                    params=params,
                    timeout=self.timeout_seconds,
                )
                status = int(getattr(response, "status_code", 0) or 0)
                if status in {403, 429} or status >= 500:
                    raise IDXClientError(f"IDX HTTP {status}")
                if hasattr(response, "raise_for_status"):
                    response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, Mapping):
                    raise IDXClientError("IDX response is not a JSON object")
                replies = payload.get("Replies")
                if not isinstance(replies, list):
                    raise IDXClientError("IDX response missing Replies[]")
                try:
                    result_count = int(payload.get("ResultCount", len(replies)))
                except (TypeError, ValueError) as exc:
                    raise IDXClientError("IDX ResultCount is invalid") from exc
                return AnnouncementPage(
                    result_count=result_count,
                    replies=tuple(item for item in replies if isinstance(item, Mapping)),
                    index_from=index_from,
                    page_size=page_size,
                )
            except (requests.RequestException, ValueError, IDXClientError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = self._backoff_for(attempt)
                if delay > 0:
                    self.sleep(delay)

        raise IDXClientError(f"IDX request failed after retries: {last_error}") from last_error
