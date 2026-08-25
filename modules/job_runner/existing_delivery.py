from __future__ import annotations

"""Exact preview/replay support for already-delivered Telegram reports.

The resend boundary is intentionally presentation-only.  It selects a source
run from the append-only delivery log, exposes that run's immutable preview
files, and replays the original Telegram messages with ``copyMessage``. When a
source message is no longer copyable, it can send only immutable, hash-locked
preview content directly; mutable legacy attachments fail closed. No report
formatter, engine output, or ``LATEST`` artifact is read here.
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
except ImportError:  # pragma: no cover - handled by the live copy boundary
    requests = None

from swing_utils import file_sha256

from .delivery import (
    _credentials,
    _is_photo_attachment,
    _response_json,
    _send_document,
    _send_photo,
    _send_telegram,
    normalize_telegram_text,
    split_telegram_text,
)
from .reports import ReportPayload
from .runtime import RunnerContext, append_jsonl, now_wib, read_json, resolve, write_json


EXACT_PREVIEW_SELECTION_SCHEMA = "SDE_EXACT_PREVIEW_SELECTION_V1"

_REPORT_TYPES: dict[str, tuple[str, ...]] = {
    "market_outlook": ("market_outlook",),
    "post_market": ("post_market_heatmap", "post_market"),
    "final_watchlist": (
        "final_watchlist_summary",
        "final_watchlist_detail",
        "final_watchlist_csv",
        # Compatibility with older compact report names.
        "final_watchlist",
        "signal_detail",
    ),
}

_REQUIRED_REPORT_TYPES: dict[str, tuple[str, ...]] = {
    "market_outlook": ("market_outlook",),
    "post_market": ("post_market",),
    "final_watchlist": ("final_watchlist_summary", "final_watchlist"),
}

_AGGREGATE_STATUSES = {
    "SENT",
    "FAILED",
    "SENT_WITH_TEXT_FALLBACK",
    "DELIVERY_STATE_UNCERTAIN",
    "DUPLICATE_SUPPRESSED",
    "SKIPPED_NOT_CONFIGURED",
    "NO_TELEGRAM",
    "DRY_RUN",
}


class ExactDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExistingDelivery:
    requested_job: str
    trade_date: str
    source_run_id: str
    source_job: str
    source_time: str
    entries: tuple[dict[str, Any], ...]
    preview_paths: tuple[Path, ...]
    preview_manifest: Path | None
    signature: str

    @property
    def message_count(self) -> int:
        return sum(len(_message_ids(entry)) for entry in self.entries)

    def source_details(self) -> dict[str, Any]:
        return {
            "source_run_id": self.source_run_id,
            "source_job": self.source_job,
            "source_delivery_time": self.source_time,
            "source_delivery_signature": self.signature,
            "source_preview_manifest": str(self.preview_manifest or ""),
            "source_message_count": self.message_count,
            "replay_mode": "TELEGRAM_COPY_WITH_HASH_LOCKED_ARCHIVE_FALLBACK",
        }


def _delivery_log_path(ctx: RunnerContext) -> Path:
    configured = ctx.scheduler_config.get("delivery", {}).get(
        "delivery_log", "data/state/scheduler/delivery_log.jsonl"
    )
    return resolve(configured)


def _read_delivery_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        raise ExactDeliveryError(f"EXACT_DELIVERY_LOG_NOT_FOUND:{path}")
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _message_ids(entry: dict[str, Any]) -> list[int]:
    raw: Iterable[Any]
    if isinstance(entry.get("telegram_message_ids"), list):
        raw = entry.get("telegram_message_ids", [])
    elif entry.get("telegram_message_id") not in (None, ""):
        raw = [entry.get("telegram_message_id")]
    else:
        raw = []
    result: list[int] = []
    for value in raw:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id > 0:
            result.append(message_id)
    return result


def _delivery_signature(entries: Iterable[dict[str, Any]]) -> str:
    normalized = [
        {
            "sequence": int(entry.get("delivery_sequence") or 0),
            "report_type": str(entry.get("report_type") or "").lower(),
            "message_thread_id": str(entry.get("message_thread_id") or ""),
            "message_ids": _message_ids(entry),
        }
        for entry in entries
    ]
    rendered = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _qualifying_groups(
    events: Iterable[dict[str, Any]],
    requested_job: str,
    trade_date: str,
) -> list[tuple[str, list[dict[str, Any]]]]:
    accepted = set(_REPORT_TYPES[requested_job])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if str(event.get("trade_date") or "") != trade_date:
            continue
        source_job = str(event.get("job") or "").lower()
        if source_job not in {requested_job, "full_manual"}:
            continue
        if str(event.get("report_type") or "").lower() not in accepted:
            continue
        if str(event.get("status") or "").upper() not in _AGGREGATE_STATUSES:
            continue
        run_id = str(event.get("run_id") or "").strip()
        if run_id:
            grouped.setdefault(run_id, []).append(event)

    qualified: list[tuple[str, list[dict[str, Any]]]] = []
    for run_id, entries in grouped.items():
        # A resend/delivery-only run can never become the canonical source for
        # another resend, even if it happened later than the official run.
        if any(bool(entry.get("force_resend")) for entry in entries):
            continue
        ordered = sorted(entries, key=lambda item: int(item.get("delivery_sequence") or 0))
        if not ordered or any(str(item.get("status") or "").upper() != "SENT" for item in ordered):
            continue
        present = {str(item.get("report_type") or "").lower() for item in ordered}
        required = _REQUIRED_REPORT_TYPES[requested_job]
        if requested_job == "final_watchlist":
            if not any(report_type in present for report_type in required):
                continue
        elif not all(report_type in present for report_type in required):
            continue
        if any(not _message_ids(item) for item in ordered):
            continue
        qualified.append((run_id, ordered))
    return qualified


def _source_preview_paths(
    ctx: RunnerContext,
    requested_job: str,
    source_run_id: str,
) -> tuple[tuple[Path, ...], Path | None]:
    folder = ctx.previews_root / ctx.trade_date.isoformat()
    manifest_path = folder / f"{source_run_id}_preview_manifest.json"
    manifest = read_json(manifest_path)
    accepted = set(_REPORT_TYPES[requested_job])
    paths: list[Path] = []
    expected_hashes: dict[str, str] = {}

    is_bundle = bool(
        isinstance(manifest, dict)
        and manifest.get("schema") == "SDE_DELIVERY_PREVIEW_BUNDLE_V1"
    )
    if isinstance(manifest, dict) and manifest:
        manifest_run_id = str(manifest.get("run_id") or "").strip()
        manifest_job = str(manifest.get("job") or "").strip().lower()
        manifest_date = str(manifest.get("trade_date") or "").strip()
        if manifest_run_id and manifest_run_id != source_run_id:
            raise ExactDeliveryError(f"EXACT_PREVIEW_RUN_MISMATCH:{manifest_run_id}:{source_run_id}")
        if manifest_job and manifest_job not in {requested_job, "full_manual"}:
            raise ExactDeliveryError(f"EXACT_PREVIEW_JOB_MISMATCH:{manifest_job}:{requested_job}")
        if manifest_date and manifest_date != ctx.trade_date.isoformat():
            raise ExactDeliveryError(
                f"EXACT_PREVIEW_DATE_MISMATCH:{manifest_date}:{ctx.trade_date.isoformat()}"
            )
        if is_bundle:
            if manifest.get("state") != "DELIVERED" or manifest.get("delivery_complete") is not True:
                raise ExactDeliveryError(f"EXACT_PREVIEW_BUNDLE_NOT_DELIVERED:{source_run_id}")

    # New manifests carry immutable, explicit run-scoped paths.
    payload_records = manifest.get("payloads") if isinstance(manifest, dict) else None
    if isinstance(payload_records, list):
        for record in payload_records:
            if not isinstance(record, dict):
                continue
            if str(record.get("report_type") or "").lower() not in accepted:
                continue
            raw = str(record.get("run_scoped_preview") or "").strip()
            if raw:
                preview_path = resolve(raw)
                paths.append(preview_path)
                expected_hashes[str(preview_path)] = str(record.get("preview_sha256") or "").strip()
            attachment_raw = str(record.get("attachment_archive") or "").strip()
            if attachment_raw:
                attachment_path = resolve(attachment_raw)
                paths.append(attachment_path)
                expected_hashes[str(attachment_path)] = str(
                    record.get("attachment_sha256") or ""
                ).strip()

    # Legacy manifests point at mutable canonical preview files.  Derive the
    # immutable run-scoped copies written alongside them instead.
    if not paths and isinstance(manifest, dict):
        files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        report_types = (
            manifest.get("report_types")
            if isinstance(manifest.get("report_types"), list)
            else []
        )
        for raw, report_type in zip(files, report_types):
            if str(report_type or "").lower() not in accepted:
                continue
            canonical = resolve(str(raw))
            paths.append(canonical.parent / f"{source_run_id}_{canonical.name}")

    existing: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.exists() or not path.is_file():
            raise ExactDeliveryError(f"EXACT_PREVIEW_FILE_NOT_FOUND:{path}")
        expected_hash = expected_hashes.get(str(path), "")
        if expected_hash and file_sha256(path) != expected_hash:
            raise ExactDeliveryError(f"EXACT_PREVIEW_FILE_INTEGRITY_FAILED:{path}")
        existing.append(path)
    if not existing:
        raise ExactDeliveryError(
            f"EXACT_PREVIEW_NOT_ARCHIVED:{requested_job}:{ctx.trade_date.isoformat()}:{source_run_id}"
        )
    return tuple(existing), manifest_path if manifest_path.exists() else None


def find_existing_delivery(
    ctx: RunnerContext,
    requested_job: str | None = None,
    *,
    source_run_id: str | None = None,
) -> ExistingDelivery:
    job = str(requested_job or ctx.job).lower()
    if job not in _REPORT_TYPES:
        raise ExactDeliveryError(f"EXACT_DELIVERY_JOB_UNSUPPORTED:{job}")
    trade_date = ctx.trade_date.isoformat()
    groups = _qualifying_groups(_read_delivery_events(_delivery_log_path(ctx)), job, trade_date)
    if source_run_id:
        groups = [item for item in groups if item[0] == source_run_id]
    if not groups:
        suffix = f":{source_run_id}" if source_run_id else ""
        raise ExactDeliveryError(f"EXACT_DELIVERY_NOT_FOUND:{job}:{trade_date}{suffix}")

    run_id, entries = max(
        groups,
        key=lambda item: max(str(entry.get("time") or "") for entry in item[1]),
    )
    preview_paths, preview_manifest = _source_preview_paths(ctx, job, run_id)
    source_time = max(str(entry.get("time") or "") for entry in entries)
    source_job = str(entries[0].get("job") or job)
    return ExistingDelivery(
        requested_job=job,
        trade_date=trade_date,
        source_run_id=run_id,
        source_job=source_job,
        source_time=source_time,
        entries=tuple(entries),
        preview_paths=preview_paths,
        preview_manifest=preview_manifest,
        signature=_delivery_signature(entries),
    )


def _selection_path(ctx: RunnerContext, requested_job: str) -> Path:
    return ctx.state_root / f"{requested_job}_exact_preview_selection.json"


def save_preview_selection(ctx: RunnerContext, delivery: ExistingDelivery) -> Path:
    path = _selection_path(ctx, delivery.requested_job)
    write_json(path, {
        "schema": EXACT_PREVIEW_SELECTION_SCHEMA,
        "preview_run_id": ctx.run_id,
        "job": delivery.requested_job,
        "trade_date": delivery.trade_date,
        "source_run_id": delivery.source_run_id,
        "source_delivery_signature": delivery.signature,
        "source_delivery_time": delivery.source_time,
        "preview_paths": [str(item) for item in delivery.preview_paths],
        "preview_sha256": [file_sha256(item) for item in delivery.preview_paths],
        "selected_at": now_wib().isoformat(timespec="seconds"),
    })
    return path


def load_preview_selection(ctx: RunnerContext, requested_job: str | None = None) -> ExistingDelivery:
    job = str(requested_job or ctx.job).lower()
    path = _selection_path(ctx, job)
    selection = read_json(path)
    if not isinstance(selection, dict) or selection.get("schema") != EXACT_PREVIEW_SELECTION_SCHEMA:
        raise ExactDeliveryError(f"EXACT_PREVIEW_SELECTION_NOT_FOUND:{job}")
    if str(selection.get("job") or "").lower() != job:
        raise ExactDeliveryError(f"EXACT_PREVIEW_SELECTION_JOB_MISMATCH:{job}")
    if str(selection.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError(
            f"EXACT_PREVIEW_SELECTION_DATE_MISMATCH:{selection.get('trade_date')}:{ctx.trade_date.isoformat()}"
        )
    source_run_id = str(selection.get("source_run_id") or "").strip()
    delivery = find_existing_delivery(ctx, job, source_run_id=source_run_id)
    expected = str(selection.get("source_delivery_signature") or "")
    if not expected or expected != delivery.signature:
        raise ExactDeliveryError(f"EXACT_PREVIEW_SELECTION_INTEGRITY_FAILED:{job}:{source_run_id}")
    selected_paths = [str(value) for value in selection.get("preview_paths", [])]
    current_paths = [str(value) for value in delivery.preview_paths]
    selected_hashes = [str(value) for value in selection.get("preview_sha256", [])]
    current_hashes = [file_sha256(value) for value in delivery.preview_paths]
    if selected_paths != current_paths or selected_hashes != current_hashes:
        raise ExactDeliveryError(f"EXACT_PREVIEW_SELECTION_FILES_CHANGED:{job}:{source_run_id}")
    return delivery


def _read_preview_card(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if (
        len(lines) >= 3
        and lines[0].startswith("Run ID:")
        and lines[1].startswith("Trade Date:")
        and lines[2].startswith("Report Type:")
    ):
        report_type = lines[2].split(":", 1)[1].strip().lower()
        return report_type, "\n".join(lines[3:]).strip()
    return "", text.strip()


def _archive_replay_specs(delivery: ExistingDelivery) -> dict[int, dict[str, Any]]:
    """Resolve only files covered by the approved Preview receipt."""
    approved = {str(path.resolve()) for path in delivery.preview_paths}
    manifest = read_json(delivery.preview_manifest) if delivery.preview_manifest else {}
    records = manifest.get("payloads") if isinstance(manifest, dict) else None
    records_by_sequence: dict[int, dict[str, Any]] = {}
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            try:
                sequence = int(record.get("sequence") or 0)
            except (TypeError, ValueError):
                continue
            if sequence > 0:
                records_by_sequence[sequence] = record

    preview_queues: dict[str, list[tuple[Path, str]]] = {}
    for path in delivery.preview_paths:
        if path.suffix.lower() != ".txt":
            continue
        report_type, body = _read_preview_card(path)
        if report_type:
            preview_queues.setdefault(report_type, []).append((path, body))

    specs: dict[int, dict[str, Any]] = {}
    for replay_sequence, entry in enumerate(delivery.entries, start=1):
        report_type = str(entry.get("report_type") or "").strip().lower()
        try:
            source_sequence = int(entry.get("delivery_sequence") or replay_sequence)
        except (TypeError, ValueError):
            source_sequence = replay_sequence
        record = records_by_sequence.get(source_sequence, {})

        preview_path: Path | None = None
        body = ""
        raw_preview = str(record.get("run_scoped_preview") or "").strip()
        if raw_preview:
            preview_path = resolve(raw_preview)
            _, body = _read_preview_card(preview_path)
            queue = preview_queues.get(report_type, [])
            if queue and queue[0][0].resolve() == preview_path.resolve():
                queue.pop(0)
        else:
            queue = preview_queues.get(report_type, [])
            if queue:
                preview_path, body = queue.pop(0)

        raw_attachment = str(record.get("attachment_archive") or "").strip()
        attachment = resolve(raw_attachment) if raw_attachment else None
        for selected in (preview_path, attachment):
            if selected is not None and str(selected.resolve()) not in approved:
                raise ExactDeliveryError(
                    f"ARCHIVED_REPLAY_FILE_NOT_APPROVED:{report_type}:{selected}"
                )
        telegram_parts = record.get("telegram_parts")
        specs[replay_sequence] = {
            "report_type": report_type,
            "preview_path": preview_path,
            "body": body,
            "attachment": attachment,
            "source_had_attachment": bool(str(entry.get("attachment_path") or "").strip()),
            "telegram_parts": (
                [dict(item) for item in telegram_parts if isinstance(item, dict)]
                if isinstance(telegram_parts, list)
                else []
            ),
        }
    return specs


def _telegram_message_id(response: dict[str, Any]) -> int:
    message_id = int(response.get("result", {}).get("message_id") or 0)
    if message_id <= 0:
        raise RuntimeError(f"Telegram tidak mengembalikan message_id: {response}")
    return message_id


def _send_with_rate_limit_retry(sender: Any, *, max_retries: int = 3) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        try:
            return sender()
        except RuntimeError as exc:
            match = re.search(r"retry_after['\"]?\s*:\s*(\d+)", str(exc))
            if match is None or attempt >= max_retries:
                raise
            time.sleep(max(1, min(int(match.group(1)) + 1, 60)))
    raise RuntimeError("Telegram archive replay retry exhausted")  # pragma: no cover


def _send_archived_entry(
    ctx: RunnerContext,
    entry: dict[str, Any],
    spec: dict[str, Any],
    message_ids: list[int] | None = None,
) -> list[int]:
    """Send the approved bytes/text directly, without invoking a formatter."""
    report_type = str(spec.get("report_type") or entry.get("report_type") or "")
    if spec.get("source_had_attachment") and not isinstance(spec.get("attachment"), Path):
        raise ExactDeliveryError(
            f"ARCHIVED_REPLAY_ATTACHMENT_NOT_IMMUTABLE:{report_type}:"
            f"{entry.get('delivery_sequence') or ''}"
        )
    preview_path = spec.get("preview_path")
    payload = ReportPayload(
        report_type=report_type,
        filename=(preview_path.name if isinstance(preview_path, Path) else f"{report_type}.txt"),
        text=str(spec.get("body") or ""),
        topic="default",
    )
    setattr(payload, "_message_thread_id_override", str(entry.get("message_thread_id") or ""))
    attachment = spec.get("attachment")
    if isinstance(attachment, Path):
        setattr(payload, "attachment_path", attachment)

    configured_parts = spec.get("telegram_parts")
    parts = configured_parts if isinstance(configured_parts, list) else []
    if not parts:
        body = normalize_telegram_text(payload.text)
        if isinstance(attachment, Path):
            kind = "photo" if _is_photo_attachment(attachment) else "document"
            parts = [{"kind": kind, "text": body}]
        else:
            if not body:
                raise ExactDeliveryError(f"ARCHIVED_REPLAY_TEXT_EMPTY:{report_type}")
            max_len = int(
                ctx.scheduler_config.get("telegram", {}).get("maximum_message_length", 4000)
            )
            parts = [
                {"kind": "text", "text": text}
                for text in split_telegram_text(body, max_len=max_len)
            ]

    sent_ids = message_ids if message_ids is not None else []
    for part in parts:
        kind = str(part.get("kind") or "text").strip().lower()
        text = str(part.get("text") or "")
        if kind == "photo":
            if not isinstance(attachment, Path) or not _is_photo_attachment(attachment):
                raise ExactDeliveryError(f"ARCHIVED_REPLAY_PHOTO_MISSING:{report_type}")
            if len(text) > 1024:
                raise ExactDeliveryError(f"ARCHIVED_REPLAY_PHOTO_CAPTION_TOO_LONG:{report_type}")
            response = _send_with_rate_limit_retry(
                lambda: _send_photo(ctx, payload, text)
            )
        elif kind == "document":
            if not isinstance(attachment, Path) or _is_photo_attachment(attachment):
                raise ExactDeliveryError(f"ARCHIVED_REPLAY_DOCUMENT_MISSING:{report_type}")
            setattr(payload, "caption", text)
            response = _send_with_rate_limit_retry(lambda: _send_document(ctx, payload))
        elif kind == "text":
            if not text:
                raise ExactDeliveryError(f"ARCHIVED_REPLAY_TEXT_EMPTY:{report_type}")
            response = _send_with_rate_limit_retry(
                lambda: _send_telegram(ctx, payload, text=text)
            )
        else:
            raise ExactDeliveryError(f"ARCHIVED_REPLAY_PART_UNSUPPORTED:{report_type}:{kind}")
        sent_ids.append(_telegram_message_id(response))
    return sent_ids


def _source_message_unavailable(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "message to copy not found",
            "message can't be copied",
            "message cannot be copied",
            "protected content",
        )
    )


def _copy_request(
    token: str,
    data: dict[str, Any],
    *,
    max_retries: int = 3,
) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang. Jalankan maintenance\\INSTALL_REQUIREMENTS.bat.")
    for attempt in range(max_retries + 1):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/copyMessage",
            data=data,
            timeout=30,
        )
        try:
            body = response.json()
        except Exception:
            body = {}
        if response.status_code == 429 and attempt < max_retries:
            retry_after = int((body.get("parameters") or {}).get("retry_after") or 1)
            time.sleep(max(1, min(retry_after + 1, 60)))
            continue
        return _response_json(response)
    raise RuntimeError("Telegram copyMessage retry exhausted")  # pragma: no cover


def copy_existing_delivery(ctx: RunnerContext, delivery: ExistingDelivery) -> list[dict[str, Any]]:
    """Replay the approved messages in order, with a hash-locked archive fallback."""
    token, chat_id = _credentials(ctx)
    archive_specs = _archive_replay_specs(delivery)
    results: list[dict[str, Any]] = []
    failed = False
    total = len(delivery.entries)
    for replay_sequence, entry in enumerate(delivery.entries, start=1):
        source_ids = _message_ids(entry)
        base = {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": delivery.requested_job,
            "trade_date": delivery.trade_date,
            "report_type": str(entry.get("report_type") or ""),
            "source_run_id": delivery.source_run_id,
            "source_telegram_message_ids": source_ids,
            "message_thread_id": str(entry.get("message_thread_id") or ""),
            "delivery_sequence": replay_sequence,
            "delivery_total": total,
            "copy_mode": "TELEGRAM_COPY_EXACT",
            "part_count": len(source_ids),
            "force_resend": True,
            "source_delivery_signature": delivery.signature,
        }
        if failed:
            event = {**base, "status": "SKIPPED_AFTER_COPY_FAILURE", "telegram_message_ids": []}
            append_jsonl(_delivery_log_path(ctx), event)
            results.append(event)
            continue

        copied_ids: list[int] = []
        copy_error = ""
        replay_mode = "TELEGRAM_COPY_EXACT"
        try:
            try:
                for source_message_id in source_ids:
                    data: dict[str, Any] = {
                        "chat_id": chat_id,
                        "from_chat_id": chat_id,
                        "message_id": source_message_id,
                    }
                    thread_id = str(entry.get("message_thread_id") or "").strip()
                    if thread_id:
                        data["message_thread_id"] = thread_id
                    response = _copy_request(token, data)
                    copied_ids.append(_telegram_message_id(response))
            except Exception as exc:
                if copied_ids or not _source_message_unavailable(exc):
                    raise
                copy_error = f"{type(exc).__name__}: {exc}"
                replay_mode = "ARCHIVED_PREVIEW_EXACT"
                copied_ids = _send_archived_entry(
                    ctx,
                    entry,
                    archive_specs[replay_sequence],
                    message_ids=copied_ids,
                )

            event = {
                **base,
                "status": "SENT",
                "copy_mode": replay_mode,
                "part_count": len(copied_ids),
                "telegram_message_ids": copied_ids,
            }
            if copy_error:
                event["copy_fallback_reason"] = copy_error
            append_jsonl(_delivery_log_path(ctx), event)
            results.append(event)
        except Exception as exc:
            failed = True
            event = {
                **base,
                "status": "FAILED",
                "copy_mode": replay_mode,
                "error": f"{type(exc).__name__}: {exc}",
                "telegram_message_ids": copied_ids,
                "sent_parts_before_failure": len(copied_ids),
            }
            if copy_error:
                event["copy_fallback_reason"] = copy_error
            append_jsonl(_delivery_log_path(ctx), event)
            results.append(event)
    return results


__all__ = [
    "EXACT_PREVIEW_SELECTION_SCHEMA",
    "ExactDeliveryError",
    "ExistingDelivery",
    "copy_existing_delivery",
    "find_existing_delivery",
    "load_preview_selection",
    "save_preview_selection",
]
