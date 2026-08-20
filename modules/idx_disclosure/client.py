"""IDX GetAnnouncement source adapter contract.

Implementation is intentionally deferred until the direct HTTP behavior is
validated from the SDE runtime environment. No browser automation is enabled by
this scaffolding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Protocol, Sequence


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
