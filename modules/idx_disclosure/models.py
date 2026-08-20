"""Normalized contracts for IDX disclosure data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Tuple


@dataclass(frozen=True, slots=True)
class DisclosureAttachment:
    filename: str
    url: str
    is_attachment: bool


@dataclass(frozen=True, slots=True)
class IDXDisclosure:
    id2: str
    ticker: str
    announcement_no: str
    published_at: datetime
    title: str
    subject: str
    idx_created_at: datetime | None
    attachments: Tuple[DisclosureAttachment, ...] = ()
    raw_source: str | None = None
