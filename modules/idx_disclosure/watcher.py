"""Orchestration for the isolated IDX disclosure watcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from .ai_reader import AIReaderPermanentError, AISummary, format_idx_ai_summary
from .ai_state import SQLiteDisclosureAIQueue
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


class DisclosureAIProcessor(Protocol):
    def summarize(self, disclosure: IDXDisclosure) -> AISummary:
        """Return a factual document summary or raise on failure."""
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
    ai_queued: int = 0
    ai_generated: int = 0
    ai_delivered: int = 0
    ai_failed: int = 0
    error: str = ""


class IDXDisclosureWatcher:
    """Coordinates source, normalization, dedup, delivery and optional AI reading.

    The official IDX message is always delivered before AI work is eligible.
    AI state has its own queue and never writes to SDE scoring/decision engines.
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
        ai_processor: DisclosureAIProcessor | None = None,
        ai_queue: SQLiteDisclosureAIQueue | None = None,
        ai_enabled: bool = False,
        ai_max_attempts: int = 3,
        ai_retry_backoff_seconds: tuple[int, ...] = (60, 300, 900),
        ai_max_documents_per_poll: int = 1,
    ) -> None:
        self.source = source
        self.repository = repository
        self.delivery = delivery
        self.delivery_enabled = bool(delivery_enabled and delivery is not None)
        self.page_size = max(1, int(page_size))
        self.now = now or (lambda: datetime.now(JAKARTA))
        self.ai_processor = ai_processor
        self.ai_queue = ai_queue
        self.ai_enabled = bool(
            ai_enabled
            and self.delivery_enabled
            and ai_processor is not None
            and ai_queue is not None
        )
        self.ai_max_attempts = max(1, int(ai_max_attempts))
        self.ai_retry_backoff_seconds = tuple(
            max(1, int(value)) for value in ai_retry_backoff_seconds
        ) or (60, 300, 900)
        self.ai_max_documents_per_poll = max(1, int(ai_max_documents_per_poll))

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

    def _ai_backoff(self, attempts_before: int) -> int:
        index = min(max(0, int(attempts_before)), len(self.ai_retry_backoff_seconds) - 1)
        return self.ai_retry_backoff_seconds[index]

    def _deliver_ai_ready(self) -> tuple[int, int]:
        if not self.ai_enabled or self.ai_queue is None or self.delivery is None:
            return 0, 0
        sent = failed = 0
        for work in self.ai_queue.ready_delivery(limit=10):
            try:
                self.delivery.send(
                    work.disclosure,
                    format_idx_ai_summary(work.disclosure, work.summary),
                )
            except Exception as exc:
                self.ai_queue.mark_delivery_error(work.disclosure.id2, error=str(exc))
                failed += 1
                continue
            self.ai_queue.mark_delivered(work.disclosure.id2, delivered_at=self.now())
            sent += 1
        return sent, failed

    def _process_ai(self) -> tuple[int, int, int]:
        if not self.ai_enabled or self.ai_queue is None or self.ai_processor is None:
            return 0, 0, 0

        delivered_before, delivery_failed_before = self._deliver_ai_ready()
        generated = failed = 0
        now = self.now()
        for work in self.ai_queue.pending_generation(
            now=now,
            max_attempts=self.ai_max_attempts,
            limit=self.ai_max_documents_per_poll,
        ):
            try:
                summary = self.ai_processor.summarize(work.disclosure)
            except AIReaderPermanentError as exc:
                self.ai_queue.mark_failure(
                    work.disclosure.id2,
                    error=f"{type(exc).__name__}:{exc}",
                    failed_at=self.now(),
                    next_attempt_at=None,
                    permanent=True,
                )
                failed += 1
                continue
            except Exception as exc:
                failed_at = self.now()
                exhausted = work.attempts + 1 >= self.ai_max_attempts
                self.ai_queue.mark_failure(
                    work.disclosure.id2,
                    error=f"{type(exc).__name__}:{exc}",
                    failed_at=failed_at,
                    next_attempt_at=(
                        None
                        if exhausted
                        else failed_at + timedelta(seconds=self._ai_backoff(work.attempts))
                    ),
                    permanent=exhausted,
                )
                failed += 1
                continue

            self.ai_queue.mark_ready(
                work.disclosure.id2,
                summary,
                processed_at=self.now(),
            )
            generated += 1

        delivered_after, delivery_failed_after = self._deliver_ai_ready()
        return (
            generated,
            delivered_before + delivered_after,
            failed + delivery_failed_before + delivery_failed_after,
        )

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

        discovered = skipped = ai_queued = 0
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
            if self.ai_enabled and self.ai_queue is not None:
                ai_queued += int(self.ai_queue.enqueue(item.id2, queued_at=now))

        # Official message first. AI generation is only eligible after the
        # disclosure has a successful telegram_sent_at in the official queue.
        delivered, delivery_failed = self._deliver_pending()
        ai_generated, ai_delivered, ai_failed = self._process_ai()
        return PollResult(
            fetched=fetched,
            discovered=discovered,
            delivered=delivered,
            skipped_seen=skipped,
            malformed=malformed,
            delivery_failed=delivery_failed,
            pages_fetched=pages,
            ai_queued=ai_queued,
            ai_generated=ai_generated,
            ai_delivered=ai_delivered,
            ai_failed=ai_failed,
        )
