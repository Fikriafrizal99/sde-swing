"""Orchestration contract for the isolated IDX disclosure watcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .client import AnnouncementSource
from .repository import DisclosureRepository


@dataclass(frozen=True, slots=True)
class PollResult:
    fetched: int = 0
    discovered: int = 0
    delivered: int = 0
    skipped_seen: int = 0
    baseline_seeded: int = 0


class IDXDisclosureWatcher:
    """Coordinates source, normalization, dedup and delivery.

    The implementation is deliberately disabled in the architecture commit.
    It must remain non-blocking and must not write to SDE decision engines.
    """

    def __init__(self, source: AnnouncementSource, repository: DisclosureRepository) -> None:
        self.source = source
        self.repository = repository

    def poll_once(self, *, jakarta_date: date) -> PollResult:
        raise NotImplementedError("Watcher implementation pending Phase 2")
