"""IDX announcement client with browser-compatible transport.

IDX currently sits behind Cloudflare. Plain ``requests`` can receive HTTP 403 even
when the endpoint is valid in a real browser, so the default transport uses
``curl_cffi`` to impersonate Chrome's TLS/HTTP fingerprint. A caller-supplied
session is still supported for tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Protocol, Sequence

import requests as std_requests

try:
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - depends on runtime installation
    curl_requests = None


DEFAULT_ENDPOINT = "https://www.idx.co.id/primary/ListedCompany/GetAnnouncement"
DEFAULT_REFERER = "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"
DEFAULT_BOOTSTRAP_URL = DEFAULT_REFERER


class IDXClientError(RuntimeError):
    """Raised when IDX cannot be queried or its response contract is invalid."""


class IDXBlockedError(IDXClientError):
    """Raised when IDX/Cloudflare blocks the selected transport."""


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
    """Persistent client for the public IDX GetAnnouncement endpoint.

    ``transport="auto"`` prefers curl_cffi browser impersonation. Plain requests
    is retained only as a compatibility fallback and may be rejected by
    Cloudflare.
    """

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
        transport: str = "auto",
        browser_impersonate: str = "chrome",
        bootstrap_url: str = DEFAULT_BOOTSTRAP_URL,
        bootstrap_before_api: bool = True,
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
        self.browser_impersonate = str(browser_impersonate or "chrome")
        self.bootstrap_url = str(bootstrap_url or DEFAULT_BOOTSTRAP_URL)
        self.bootstrap_before_api = bool(bootstrap_before_api)
        self.sleep = sleep
        self._bootstrap_attempted = False
        self._custom_session = session is not None

        requested_transport = str(transport or "auto").strip().lower()
        if session is not None:
            self.session = session
            self.transport_name = "custom"
            self._curl_impersonation = False
        elif requested_transport in {"auto", "curl_cffi", "browser_impersonation"}:
            if curl_requests is None:
                if requested_transport != "auto":
                    raise IDXClientError(
                        "curl_cffi is required for IDX browser impersonation. "
                        "Install requirements.txt first."
                    )
                self.session = std_requests.Session()
                self.transport_name = "requests"
                self._curl_impersonation = False
            else:
                self.session = curl_requests.Session()
                self.transport_name = "curl_cffi"
                self._curl_impersonation = True
        elif requested_transport == "requests":
            self.session = std_requests.Session()
            self.transport_name = "requests"
            self._curl_impersonation = False
        else:
            raise ValueError(f"Unsupported IDX transport: {transport}")

        headers = getattr(self.session, "headers", None)
        if headers is not None:
            headers.update(
                {
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Referer": DEFAULT_REFERER,
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/142.0.0.0 Safari/537.36"
                    ),
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                    "X-Requested-With": "XMLHttpRequest",
                }
            )

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

    def _get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        html_bootstrap: bool = False,
    ) -> Any:
        kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if params is not None:
            kwargs["params"] = params
        if self._curl_impersonation:
            kwargs["impersonate"] = self.browser_impersonate
        if html_bootstrap:
            kwargs["headers"] = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Referer": "https://www.idx.co.id/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Upgrade-Insecure-Requests": "1",
            }
        return self.session.get(url, **kwargs)

    def _ensure_bootstrap(self) -> None:
        if (
            self._bootstrap_attempted
            or not self.bootstrap_before_api
            or self._custom_session
            or not self.bootstrap_url
        ):
            return
        self._bootstrap_attempted = True
        try:
            response = self._get(self.bootstrap_url, html_bootstrap=True)
            status = int(getattr(response, "status_code", 0) or 0)
            if 200 <= status < 400:
                return
        except Exception:
            return

    def _blocked_message(self, status: int) -> str:
        if self.transport_name == "requests" and curl_requests is None:
            return (
                f"IDX HTTP {status}. Plain requests is blocked by IDX/Cloudflare "
                "and curl_cffi is not installed. Run: pip install -r requirements.txt"
            )
        return f"IDX HTTP {status} via {self.transport_name}"

    def fetch_page(
        self,
        *,
        trade_date: date,
        index_from: int = 0,
        page_size: int = 50,
    ) -> AnnouncementPage:
        params = self._params(trade_date, index_from, page_size)
        last_error: Exception | None = None
        self._ensure_bootstrap()

        for attempt in range(self.max_retries + 1):
            try:
                response = self._get(self.endpoint, params=params)
                status = int(getattr(response, "status_code", 0) or 0)
                if status == 403:
                    raise IDXBlockedError(self._blocked_message(status))
                if status == 429 or status >= 500:
                    raise IDXClientError(f"IDX HTTP {status} via {self.transport_name}")
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
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = self._backoff_for(attempt)
                if delay > 0:
                    self.sleep(delay)

        raise IDXClientError(
            f"IDX request failed after retries [{self.transport_name}]: {last_error}"
        ) from last_error
