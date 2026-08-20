"""Real-browser IDX source and resilient fallback adapter.

This module is deliberately isolated from the watcher.  It uses the user's
installed Chrome/Edge via Playwright only when direct HTTP is blocked by IDX.
The API call itself is executed with window.fetch() inside idx.co.id so it uses
the browser network stack, cookies, and origin context.
"""

from __future__ import annotations

import atexit
import json
from datetime import date
from typing import Any, Mapping, Sequence
from urllib.parse import urlencode

from .client import AnnouncementPage, AnnouncementSource, IDXClientError


DEFAULT_BOOTSTRAP_URL = (
    "https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi/"
)


class PlaywrightAnnouncementClient:
    """Fetch IDX announcements through a real installed Chromium browser."""

    def __init__(
        self,
        *,
        endpoint: str,
        bootstrap_url: str = DEFAULT_BOOTSTRAP_URL,
        timeout_seconds: float = 20,
        emiten_type: str = "*",
        language: str = "id",
        keyword: str = "",
        browser_channels: Sequence[str] = ("chrome", "msedge"),
        headless: bool = False,
        bootstrap_wait_ms: int = 2500,
        max_retries: int = 1,
    ) -> None:
        self.endpoint = endpoint
        self.bootstrap_url = bootstrap_url
        self.timeout_seconds = float(timeout_seconds)
        self.emiten_type = emiten_type
        self.language = language
        self.keyword = keyword
        self.browser_channels = tuple(str(x) for x in browser_channels if str(x))
        self.headless = bool(headless)
        self.bootstrap_wait_ms = max(0, int(bootstrap_wait_ms))
        self.max_retries = max(0, int(max_retries))
        self._pw: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._channel = ""
        atexit.register(self.close)

    @property
    def transport_name(self) -> str:
        channel = self._channel or "/".join(self.browser_channels) or "chromium"
        mode = "headless" if self.headless else "headed"
        return f"playwright:{channel}:{mode}"

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

    def _start(self) -> None:
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise IDXClientError(
                "Playwright belum terpasang. Jalankan: pip install -r requirements.txt"
            ) from exc

        self._pw = sync_playwright().start()
        launch_errors: list[str] = []
        channels = self.browser_channels or ("chrome", "msedge")
        for channel in channels:
            try:
                self._browser = self._pw.chromium.launch(
                    channel=channel,
                    headless=self.headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                self._channel = channel
                break
            except Exception as exc:
                launch_errors.append(f"{channel}: {type(exc).__name__}: {exc}")

        if self._browser is None:
            self.close()
            raise IDXClientError(
                "Tidak bisa membuka Chrome/Edge via Playwright. "
                + " | ".join(launch_errors)
            )

        self._context = self._browser.new_context(
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            viewport={"width": 1280, "height": 900},
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(int(self.timeout_seconds * 1000))
        try:
            response = self._page.goto(
                self.bootstrap_url,
                wait_until="domcontentloaded",
                timeout=int(self.timeout_seconds * 1000),
            )
            if response is not None and int(response.status) >= 500:
                raise IDXClientError(f"IDX bootstrap HTTP {response.status}")
            if self.bootstrap_wait_ms:
                self._page.wait_for_timeout(self.bootstrap_wait_ms)
        except Exception as exc:
            self.close()
            if isinstance(exc, IDXClientError):
                raise
            raise IDXClientError(f"IDX browser bootstrap gagal: {exc}") from exc

    def close(self) -> None:
        for obj_name in ("_context", "_browser"):
            obj = getattr(self, obj_name, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, obj_name, None)
        self._page = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def _fetch_json(self, url: str) -> Mapping[str, Any]:
        self._start()
        assert self._page is not None
        result = self._page.evaluate(
            """async ({url}) => {
                const response = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers: {
                        "Accept": "application/json, text/plain, */*"
                    }
                });
                const text = await response.text();
                return {status: response.status, ok: response.ok, text};
            }""",
            {"url": url},
        )
        status = int(result.get("status", 0) or 0)
        if status != 200:
            body = str(result.get("text", ""))[:180].replace("\n", " ")
            raise IDXClientError(
                f"IDX HTTP {status} via {self.transport_name}; body={body!r}"
            )
        try:
            payload = json.loads(str(result.get("text", "")))
        except json.JSONDecodeError as exc:
            raise IDXClientError(
                f"IDX browser response bukan JSON via {self.transport_name}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise IDXClientError("IDX browser response is not a JSON object")
        return payload

    def fetch_page(
        self,
        *,
        trade_date: date,
        index_from: int = 0,
        page_size: int = 50,
    ) -> AnnouncementPage:
        params = self._params(trade_date, index_from, page_size)
        url = f"{self.endpoint}?{urlencode(params, safe='*')}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                payload = self._fetch_json(url)
                replies = payload.get("Replies")
                if not isinstance(replies, list):
                    raise IDXClientError("IDX browser response missing Replies[]")
                try:
                    result_count = int(payload.get("ResultCount", len(replies)))
                except (TypeError, ValueError) as exc:
                    raise IDXClientError("IDX browser ResultCount is invalid") from exc
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
                try:
                    self.close()
                except Exception:
                    pass
        if isinstance(last_error, IDXClientError):
            raise last_error
        raise IDXClientError(
            f"IDX browser request failed via {self.transport_name}: {last_error}"
        ) from last_error


class ResilientAnnouncementSource:
    """Use lightweight HTTP first, then permanently switch to browser fallback."""

    def __init__(
        self,
        primary: AnnouncementSource,
        fallback: AnnouncementSource,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self._fallback_active = False
        self._primary_error = ""

    @property
    def transport_name(self) -> str:
        active = self.fallback if self._fallback_active else self.primary
        return str(getattr(active, "transport_name", type(active).__name__))

    @property
    def primary_error(self) -> str:
        return self._primary_error

    def fetch_page(
        self,
        *,
        trade_date: date,
        index_from: int = 0,
        page_size: int = 50,
    ) -> AnnouncementPage:
        if not self._fallback_active:
            try:
                return self.primary.fetch_page(
                    trade_date=trade_date,
                    index_from=index_from,
                    page_size=page_size,
                )
            except IDXClientError as exc:
                self._primary_error = str(exc)
                self._fallback_active = True

        return self.fallback.fetch_page(
            trade_date=trade_date,
            index_from=index_from,
            page_size=page_size,
        )

    def close(self) -> None:
        close = getattr(self.fallback, "close", None)
        if callable(close):
            close()
