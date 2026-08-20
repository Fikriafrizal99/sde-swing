"""Telegram presentation contract for IDX disclosures."""

from __future__ import annotations

from .models import IDXDisclosure


def format_idx_disclosure(disclosure: IDXDisclosure) -> str:
    """Render a factual IDX notification without scoring or trade language."""
    raise NotImplementedError("Telegram formatter implementation pending Phase 2")
