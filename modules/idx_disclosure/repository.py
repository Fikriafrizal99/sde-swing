"""Persistence contract for IDX disclosure deduplication."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import IDXDisclosure


class DisclosureRepository(Protocol):
    def is_initialized(self) -> bool: ...

    def contains(self, disclosure_id: str) -> bool: ...

    def save(self, disclosure: IDXDisclosure, *, first_seen_at: datetime) -> None: ...

    def mark_delivered(self, disclosure_id: str, *, delivered_at: datetime) -> None: ...

    def pending_delivery(self) -> tuple[IDXDisclosure, ...]: ...
