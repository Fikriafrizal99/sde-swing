"""Compact one-message Telegram composition for IDX disclosure + AI summary."""

from __future__ import annotations

import re
from html import escape
from typing import Sequence

from .ai_reader import AISummary
from .formatter import format_idx_disclosure
from .models import IDXDisclosure


TELEGRAM_SAFE_TEXT_LIMIT = 3900


def _clip(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _first_sentences(value: str, *, count: int = 2, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    chunks = re.split(r"(?<=[.!?])\s+", text)
    selected = " ".join(chunk for chunk in chunks[:count] if chunk).strip()
    return _clip(selected or text, limit)


def _rows(values: Sequence[str], *, max_items: int, item_limit: int) -> list[str]:
    result: list[str] = []
    for item in values:
        text = _clip(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= max_items:
            break
    return result


def format_idx_ai_compact_section(result: AISummary, *, max_bullets: int = 4) -> str:
    """Render only the concise AI add-on used when editing the official message."""

    summary = _first_sentences(result.summary, count=2, limit=420)

    # Key points are the primary scan surface. Important dates are used to fill
    # remaining slots so event/record/deadline dates are visible without a
    # second verbose section.
    bullets = _rows(result.key_points, max_items=max_bullets, item_limit=170)
    if len(bullets) < max_bullets:
        for date_item in _rows(
            result.important_dates,
            max_items=max_bullets,
            item_limit=150,
        ):
            if date_item not in bullets:
                bullets.append(date_item)
            if len(bullets) >= max_bullets:
                break

    parts = ["🤖 <b>Ringkasan AI</b>"]
    if summary:
        parts.append(escape(summary))
    if bullets:
        parts.extend(["", "<b>Poin utama</b>"])
        parts.extend(f"• {escape(item)}" for item in bullets)

    parts.extend(
        [
            "",
            "<i>AI summary dari dokumen resmi IDX • bukan rekomendasi trading</i>",
        ]
    )
    return "\n".join(parts).strip()


def format_idx_disclosure_with_ai(
    disclosure: IDXDisclosure,
    result: AISummary,
    *,
    max_chars: int = TELEGRAM_SAFE_TEXT_LIMIT,
) -> str:
    """Combine the already-sent official IDX message with a compact AI add-on."""

    official = format_idx_disclosure(disclosure).strip()

    # Reduce optional AI detail progressively if an unusually long set of IDX
    # attachment URLs brings the Telegram message close to its hard limit.
    for bullets in (4, 3, 2, 0):
        if bullets:
            ai_section = format_idx_ai_compact_section(result, max_bullets=bullets)
        else:
            short_summary = _first_sentences(result.summary, count=1, limit=260)
            ai_section = "\n".join(
                [
                    "🤖 <b>Ringkasan AI</b>",
                    escape(short_summary),
                    "",
                    "<i>AI summary dari dokumen resmi IDX • bukan rekomendasi trading</i>",
                ]
            ).strip()
        combined = f"{official}\n\n{ai_section}".strip()
        if len(combined) <= max_chars:
            return combined

    # Official content is always more important than AI. If the official text
    # itself is already unusually large, append the smallest possible marker.
    marker = "\n\n🤖 <i>Ringkasan AI tersedia, tetapi pesan terlalu panjang untuk digabungkan.</i>"
    if len(official) + len(marker) <= max_chars:
        return official + marker
    return official[:max_chars]
