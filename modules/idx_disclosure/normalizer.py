"""Normalization boundary for raw IDX announcement payloads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .models import DisclosureAttachment, IDXDisclosure


JAKARTA = ZoneInfo("Asia/Jakarta")


class IDXPayloadError(ValueError):
    """Raised when IDX payload no longer satisfies the expected contract."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = _text(mapping.get(key))
    if not value:
        raise IDXPayloadError(f"IDX field {key} is missing")
    return value


def _parse_datetime(value: Any, *, required: bool) -> datetime | None:
    raw = _text(value)
    if not raw:
        if required:
            raise IDXPayloadError("IDX datetime is missing")
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise IDXPayloadError(f"Invalid IDX datetime: {raw}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JAKARTA)
    return parsed.astimezone(JAKARTA)


def _fallback_id(ticker: str, announcement_no: str, published_at: datetime) -> str:
    material = f"{ticker}|{announcement_no}|{published_at.isoformat()}".encode("utf-8")
    return "fallback-" + hashlib.sha256(material).hexdigest()


def _attachment_filename(item: Mapping[str, Any], url: str) -> str:
    original = _text(item.get("OriginalFilename"))
    if original:
        return original
    pdf_name = _text(item.get("PDFFilename"))
    if pdf_name:
        return pdf_name
    return PurePosixPath(urlparse(url).path).name or "IDX document"


def normalize_reply(reply: Mapping[str, Any]) -> IDXDisclosure:
    """Normalize one `Replies[]` item into a stable internal contract."""
    announcement = reply.get("pengumuman")
    if not isinstance(announcement, Mapping):
        raise IDXPayloadError("Replies[] item missing pengumuman object")

    ticker = _required_text(announcement, "Kode_Emiten")
    announcement_no = _text(announcement.get("NoPengumuman"))
    published_at = _parse_datetime(announcement.get("TglPengumuman"), required=True)
    assert published_at is not None
    title = _required_text(announcement, "JudulPengumuman")
    subject = _text(announcement.get("PerihalPengumuman"))
    idx_created_at = _parse_datetime(announcement.get("CreatedDate"), required=False)

    id2 = _text(announcement.get("Id2"))
    if not id2:
        id2 = _fallback_id(ticker, announcement_no, published_at)

    raw_attachments = reply.get("attachments")
    attachments: list[DisclosureAttachment] = []
    if isinstance(raw_attachments, list):
        for item in raw_attachments:
            if not isinstance(item, Mapping):
                continue
            url = _text(item.get("FullSavePath"))
            if not url:
                continue
            attachments.append(
                DisclosureAttachment(
                    filename=_attachment_filename(item, url),
                    url=url,
                    is_attachment=bool(item.get("IsAttachment", False)),
                )
            )

    attachments.sort(key=lambda item: item.is_attachment)

    try:
        raw_source = json.dumps(reply, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        raw_source = None

    return IDXDisclosure(
        id2=id2,
        ticker=ticker.strip(),
        announcement_no=announcement_no,
        published_at=published_at,
        title=title,
        subject=subject,
        idx_created_at=idx_created_at,
        attachments=tuple(attachments),
        raw_source=raw_source,
    )
