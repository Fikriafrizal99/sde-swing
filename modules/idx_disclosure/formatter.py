"""Factual Telegram presentation for IDX disclosures."""

from __future__ import annotations

from html import escape

from .models import IDXDisclosure


_MONTHS_ID = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "Mei",
    "Jun",
    "Jul",
    "Agu",
    "Sep",
    "Okt",
    "Nov",
    "Des",
)


def _attachment_lines(disclosure: IDXDisclosure) -> list[str]:
    lines: list[str] = []
    attachment_number = 0
    for item in disclosure.attachments:
        if item.is_attachment:
            attachment_number += 1
            label = f"📎 Lampiran {attachment_number}"
        else:
            label = "📄 Dokumen Utama"
        url = escape(item.url, quote=True)
        lines.append(f'<a href="{url}">{label}</a>')
    return lines


def format_idx_disclosure(disclosure: IDXDisclosure) -> str:
    """Render a factual IDX notification without scoring or trade language."""
    dt = disclosure.published_at
    date_text = f"{dt.day:02d} {_MONTHS_ID[dt.month]} {dt.year} • {dt:%H:%M} WIB"
    parts = [
        "📢 <b>IDX KETERBUKAAN INFORMASI</b>",
        "",
        f"<b>{escape(disclosure.ticker)}</b>",
        date_text,
        "",
        escape(disclosure.title),
    ]
    if disclosure.announcement_no:
        parts.extend(["", "No. Pengumuman:", escape(disclosure.announcement_no)])
    attachment_lines = _attachment_lines(disclosure)
    if attachment_lines:
        parts.extend(["", *attachment_lines])
    parts.extend(["", "Sumber: Bursa Efek Indonesia"])
    return "\n".join(parts).strip()
