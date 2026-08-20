"""Orchestration for the isolated IDX disclosure watcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from .client import AnnouncementSource
from .formatter import format_idx_disclosure
from .models import IDXDisclosure
from .normalizer import IDXPayloadError, normalize_reply
from .repository import DisclosureRepository


JAKARTA = ZoneInfo("Asia/Jakarta")


class DisclosureDelivery(Protocol):
    def send(self, disclosure: IDXDisclosure, text: str) -> None:
        """Send one disclosure or raise on failure."""
        ...


@dataclass(frozen=True, slots=True)
class PollResult:
    fetched: int = 0
    discovered: int = 0
    delivered: int = 0
    skipped_seen: int = 0
    baseline_seeded: int = 0
    malformed: int = 0
    delivery_failed: int = 0
    pages_fetched: int = 0
    error: str = ""


class IDXDisclosureWatcher:
    """Coordinates source, normalization, dedup and optional delivery.

    This package never writes to SDE decision engines. With delivery disabled,
    newly discovered records are stored as delivery-suppressed so enabling
    Telegram later cannot flood old dry-run items.
    """

    def __init__(
        self,
        source: AnnouncementSource,
        repository: DisclosureRepository,
        *,
        delivery: DisclosureDelivery | None = None,
        delivery_enabled: bool = False,
        page_size: int = 50,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.source = source
        self.repository = repository
        self.delivery = delivery
        self.delivery_enabled = bool(delivery_enabled and delivery is not None)
        self.page_size = max(1, int(page_size))
        self.now = now or (lambda: datetime.now(JAKARTA))

    def _fetch_pages(self, jakarta_date: date, *, baseline: bool) -> tuple[list[IDXDisclosure], int, int, int]:
        disclosures: list[IDXDisclosure] = []
        fetched = malformed = pages = 0
        index_from = 0

        while True:
            page = self.source.fetch_page(
                trade_date=jakarta_date,
                index_from=index_from,
                page_size=self.page_size,
            )
            pages += 1
            fetched += len(page.replies)
            page_seen = 0
            page_valid = 0

            for raw in page.replies:
                try:
                    item = normalize_reply(raw)
                except IDXPayloadError:
                    malformed += 1
                    continue
                page_valid += 1
                if self.repository.contains(item.id2):
                    page_seen += 1
                disclosures.append(item)

            if len(page.replies) < self.page_size:
                break
            if not baseline and page_valid > 0 and page_seen == page_valid:
                break
            index_from += self.page_size
            if index_from >= page.result_count:
                break

        unique = {item.id2: item for item in disclosures}
        ordered = sorted(unique.values(), key=lambda item: (item.published_at, item.id2))
        return ordered, fetched, malformed, pages

    def _deliver_pending(self) -> tuple[int, int]:
        if not self.delivery_enabled or self.delivery is None:
            return 0, 0
        delivered = failed = 0
        for item in self.repository.pending_delivery():
            try:
                self.delivery.send(item, format_idx_disclosure(item))
            except Exception:
                failed += 1
                continue
            self.repository.mark_delivered(item.id2, delivered_at=self.now())
            delivered += 1
        return delivered, failed

    def poll_once(self, *, jakarta_date: date) -> PollResult:
        baseline = not self.repository.is_initialized()
        now = self.now()
        try:
            disclosures, fetched, malformed, pages = self._fetch_pages(
                jakarta_date, baseline=baseline
            )
        except Exception as exc:
            return PollResult(error=f"{type(exc).__name__}: {exc}")

        if baseline:
            seeded = 0
            for item in disclosures:
                if self.repository.contains(item.id2):
                    continue
                self.repository.save(
                    item,
                    first_seen_at=now,
                    suppress_delivery=True,
                )
                seeded += 1
            self.repository.mark_initialized(initialized_at=now)
            return PollResult(
                fetched=fetched,
                baseline_seeded=seeded,
                malformed=malformed,
                pages_fetched=pages,
            )

        discovered = skipped = 0
        for item in disclosures:
            if self.repository.contains(item.id2):
                skipped += 1
                continue
            self.repository.save(
                item,
                first_seen_at=now,
                suppress_delivery=not self.delivery_enabled,
            )
            discovered += 1

        delivered, delivery_failed = self._deliver_pending()
        return PollResult(
            fetched=fetched,
            discovered=discovered,
            delivered=delivered,
            skipped_seen=skipped,
            malformed=malformed,
            delivery_failed=delivery_failed,
            pages_fetched=pages,
        )
