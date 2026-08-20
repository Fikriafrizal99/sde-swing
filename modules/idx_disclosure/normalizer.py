"""Normalization boundary for raw IDX announcement payloads."""

from __future__ import annotations

from typing import Any, Mapping

from .models import IDXDisclosure


class IDXPayloadError(ValueError):
    """Raised when IDX payload no longer satisfies the expected contract."""


def normalize_reply(reply: Mapping[str, Any]) -> IDXDisclosure:
    """Normalize one `Replies[]` item.

    Full parsing is implemented in Phase 2 after fixture tests lock the observed
    IDX schema. Keeping this explicit prevents accidental coupling to raw fields.
    """
    raise NotImplementedError("IDX normalizer implementation pending Phase 2")
