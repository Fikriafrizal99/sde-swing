from __future__ import annotations

"""Recovery support for Market Outlook/Post Market unsent Telegram delivery.

The normal exact-delivery path remains authoritative and is always preferred.
This module is used only when no fully SENT source exists for the selected trade
date. It can select either a failed daily-report bundle with zero Telegram ACK
or a deliberate NO_TELEGRAM recovery bundle, provided every outbound payload is
represented and all replay files are immutable/hash-locked. Engine, formatter,
and mutable LATEST artifacts are never used during preview/resend.

A source ReadTimeout is surfaced as ACK ambiguity during preview. Re-send still
requires the operator to explicitly choose the existing [3] action after [2]
has pinned the exact source. A ReadTimeout during the recovery attempt itself
is checkpointed as DELIVERY_STATE_UNCERTAIN and is never retried automatically.
"""

import re
from pathlib import Path
from typing import Any

from swing_utils import file_sha256

from . import existing_delivery as exact
from .existing_delivery import ExactDeliveryError, ExistingDelivery
from .runtime import RunnerContext, append_jsonl, now_wib, read_json, write_json


SUPPORTED_JOBS = {"market_outlook", "post_market"}
RECOVERY_SELECTION_SCHEMA = "SDE_DAILY_REPORT_RECOVERY_SELECTION_V1"
_RECOVERY_COPY_MODE = "ARCHIVED_DAILY_REPORT_EXACT_RECOVERY"
_RECOVERABLE_SOURCE_STATUSES = {"FAILED", "NO_TELEGRAM"}


def _selection_path(ctx: RunnerContext, job: str) -> Path:
    return ctx.state_root / f"{job}_daily_report_recovery_selection.json"


def _safe_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return re.sub(r"/bot[^/\s]+/", "/bot***REDACTED***/", text)


def _looks_like_read_timeout(value: Any) -> bool:
    text = str(value or "").lower()
    return "readtimeout" in text or "read timed out" in text


def source_ack_ambiguous(source: ExistingDelivery) -> bool:
    return any(_looks_like_read_timeout(item.get("error")) for item in source.entries)


def source_recovery_mode(source: ExistingDelivery) -> str:
    statuses = {
        str(item.get("status") or "").upper()
        for item in source.entries
        if str(item.get("status") or "").strip()
    }
    if statuses == {"FAILED"}:
        return "RECOVERABLE_FAILED"
    if statuses == {"NO_TELEGRAM"}:
        return "RECOVERABLE_NO_TELEGRAM"
    raise ExactDeliveryError(
        f"DAILY_REPORT_RECOVERY_SOURCE_STATE_UNSAFE:{source.requested_job}:"
        f"{source.source_run_id}:{','.join(sorted(statuses))}"
    )


def _candidate_groups(
    ctx: RunnerContext,
    job: str,
) -> list[tuple[str, list[dict[str, Any]]]]:
    accepted = set(exact._REPORT_TYPES[job])
    events = exact._read_delivery_events(exact._delivery_log_path(ctx))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if str(event.get("trade_date") or "") != ctx.trade_date.isoformat():
            continue
        if str(event.get("job") or "").lower() not in {job, "full_manual"}:
            continue
        if str(event.get("report_type") or "").lower() not in accepted:
            continue
        if str(event.get("status") or "").upper() not in exact._AGGREGATE_STATUSES:
            continue
        if bool(event.get("force_resend")):
            continue
        run_id = str(event.get("run_id") or "").strip()
        if run_id:
            grouped.setdefault(run_id, []).append(dict(event))

    # Include any run that contains a potentially recoverable unsent state.
    # The canonical validator below then rejects mixed/partial states explicitly
    # instead of silently falling back to an older same-date run.
    return [
        (run_id, entries)
        for run_id, entries in grouped.items()
        if any(
            str(item.get("status") or "").upper() in _RECOVERABLE_SOURCE_STATUSES
            for item in entries
        )
    ]


def _canonical_unsent_entries(
    job: str,
    run_id: str,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ordered = sorted(
        entries,
        key=lambda item: (
            int(item.get("delivery_sequence") or 0),
            str(item.get("time") or ""),
        ),
    )
    accepted = set(exact._REPORT_TYPES[job])
    candidates = [
        dict(item)
        for item in ordered
        if str(item.get("report_type") or "").strip().lower() in accepted
    ]
    present = {str(item.get("report_type") or "").strip().lower() for item in candidates}
    required = exact._REQUIRED_REPORT_TYPES[job]
    if not all(report_type in present for report_type in required):
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_REQUIRED_TYPE_MISSING:{job}:{run_id}")

    acknowledged = [
        message_id
        for entry in candidates
        for message_id in exact._message_ids(entry)
    ]
    if acknowledged:
        raise ExactDeliveryError(
            f"DAILY_REPORT_RECOVERY_BLOCKED_PARTIAL_TELEGRAM_ACK:{job}:{run_id}:"
            + ",".join(str(value) for value in acknowledged)
        )

    statuses = {
        str(item.get("status") or "").upper()
        for item in candidates
        if str(item.get("status") or "").strip()
    }
    if statuses not in ({"FAILED"}, {"NO_TELEGRAM"}):
        raise ExactDeliveryError(
            f"DAILY_REPORT_RECOVERY_BLOCKED_SOURCE_STATE:{job}:{run_id}:"
            + ",".join(sorted(statuses))
        )

    for entry in candidates:
        try:
            sent_parts = int(entry.get("sent_parts_before_failure") or 0)
        except (TypeError, ValueError):
            sent_parts = -1
        if sent_parts != 0:
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_BLOCKED_PARTIAL_PART_ACK:{job}:{run_id}:"
                f"{entry.get('report_type')}:{sent_parts}"
            )

    canonical: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for entry in candidates:
        report_type = str(entry.get("report_type") or "").strip().lower()
        sequence = int(entry.get("delivery_sequence") or 0)
        signature = str(entry.get("signature") or "").strip()
        if sequence <= 0:
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_SEQUENCE_INVALID:{job}:{run_id}:{report_type}"
            )
        identity = (sequence, report_type, signature)
        if identity in seen:
            continue
        seen.add(identity)
        canonical.append(entry)

    if not canonical:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_SOURCE_EMPTY:{job}:{run_id}")
    return canonical


def _manifest_source(
    ctx: RunnerContext,
    job: str,
    run_id: str,
    entries: list[dict[str, Any]],
) -> tuple[tuple[Path, ...], Path]:
    folder = ctx.previews_root / ctx.trade_date.isoformat()
    manifest_path = folder / f"{run_id}_preview_manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") != "SDE_DELIVERY_PREVIEW_BUNDLE_V1":
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_MANIFEST_NOT_FOUND:{job}:{run_id}")
    if str(manifest.get("run_id") or "") != run_id:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_MANIFEST_RUN_MISMATCH:{job}:{run_id}")
    if str(manifest.get("job") or "").lower() not in {job, "full_manual"}:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_MANIFEST_JOB_MISMATCH:{job}:{run_id}")
    if str(manifest.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_MANIFEST_DATE_MISMATCH:{job}:{run_id}")
    if str(manifest.get("state") or "").upper() not in {"PREPARED", "DELIVERY_INCOMPLETE"}:
        raise ExactDeliveryError(
            f"DAILY_REPORT_RECOVERY_MANIFEST_STATE_UNSAFE:{job}:{run_id}:{manifest.get('state')}"
        )
    if manifest.get("delivery_complete") is True:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_ALREADY_DELIVERED:{job}:{run_id}")

    records = manifest.get("payloads")
    if not isinstance(records, list):
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_PAYLOAD_MANIFEST_MISSING:{job}:{run_id}")
    by_sequence: dict[int, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            sequence = int(record.get("sequence") or 0)
        except (TypeError, ValueError):
            continue
        if sequence > 0:
            by_sequence[sequence] = record

    expected_count = int(manifest.get("payload_count") or 0)
    entry_sequences = {int(item.get("delivery_sequence") or 0) for item in entries}
    if expected_count <= 0 or entry_sequences != set(range(1, expected_count + 1)):
        raise ExactDeliveryError(
            f"DAILY_REPORT_RECOVERY_BUNDLE_INCOMPLETE:{job}:{run_id}:"
            f"events={sorted(entry_sequences)}:expected={expected_count}"
        )

    approved: list[Path] = []
    seen_paths: set[str] = set()
    for entry in entries:
        sequence = int(entry.get("delivery_sequence") or 0)
        report_type = str(entry.get("report_type") or "").strip().lower()
        record = by_sequence.get(sequence)
        if not isinstance(record, dict):
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_ARCHIVE_RECORD_MISSING:{job}:{run_id}:{sequence}"
            )
        if str(record.get("report_type") or "").strip().lower() != report_type:
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_ARCHIVE_TYPE_MISMATCH:{job}:{run_id}:{sequence}"
            )
        record_signature = str(record.get("signature") or "").strip()
        event_signature = str(entry.get("signature") or "").strip()
        if event_signature and record_signature and event_signature != record_signature:
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_ARCHIVE_SIGNATURE_MISMATCH:{job}:{run_id}:{sequence}"
            )
        parts = record.get("telegram_parts")
        if not isinstance(parts, list) or not parts:
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_TELEGRAM_PARTS_MISSING:{job}:{run_id}:{sequence}"
            )

        raw_preview = str(record.get("run_scoped_preview") or "").strip()
        preview_hash = str(record.get("preview_sha256") or "").strip()
        if not raw_preview or not preview_hash:
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_PREVIEW_ARCHIVE_MISSING:{job}:{run_id}:{sequence}"
            )
        preview = exact.resolve(raw_preview)
        if not preview.exists() or not preview.is_file() or file_sha256(preview) != preview_hash:
            raise ExactDeliveryError(
                f"DAILY_REPORT_RECOVERY_PREVIEW_INTEGRITY_FAILED:{job}:{run_id}:{sequence}"
            )
        key = str(preview.resolve())
        if key not in seen_paths:
            approved.append(preview)
            seen_paths.add(key)

        source_had_attachment = bool(str(entry.get("attachment_path") or "").strip())
        attachment_raw = str(record.get("attachment_archive") or "").strip()
        attachment_hash = str(record.get("attachment_sha256") or "").strip()
        if source_had_attachment:
            if not attachment_raw or not attachment_hash:
                raise ExactDeliveryError(
                    f"DAILY_REPORT_RECOVERY_ATTACHMENT_ARCHIVE_MISSING:{job}:{run_id}:{sequence}"
                )
            attachment = exact.resolve(attachment_raw)
            if (
                not attachment.exists()
                or not attachment.is_file()
                or file_sha256(attachment) != attachment_hash
            ):
                raise ExactDeliveryError(
                    f"DAILY_REPORT_RECOVERY_ATTACHMENT_INTEGRITY_FAILED:{job}:{run_id}:{sequence}"
                )
            key = str(attachment.resolve())
            if key not in seen_paths:
                approved.append(attachment)
                seen_paths.add(key)

        failed_raw = str(entry.get("failed_payload") or "").strip()
        failed_hash = str(entry.get("failed_payload_sha256") or "").strip()
        if failed_raw:
            failed_path = exact.resolve(failed_raw)
            if not failed_path.exists() or not failed_path.is_file():
                raise ExactDeliveryError(
                    f"DAILY_REPORT_RECOVERY_FAILED_PAYLOAD_MISSING:{job}:{run_id}:{sequence}"
                )
            if failed_hash and file_sha256(failed_path) != failed_hash:
                raise ExactDeliveryError(
                    f"DAILY_REPORT_RECOVERY_FAILED_PAYLOAD_INTEGRITY_FAILED:{job}:{run_id}:{sequence}"
                )

    if not approved:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_ARCHIVE_EMPTY:{job}:{run_id}")
    return tuple(approved), manifest_path


def find_recoverable_daily_report(
    ctx: RunnerContext,
    job: str,
    *,
    source_run_id: str | None = None,
) -> ExistingDelivery:
    job = str(job or "").lower()
    if job not in SUPPORTED_JOBS:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_JOB_UNSUPPORTED:{job}")
    groups = _candidate_groups(ctx, job)
    if source_run_id:
        groups = [item for item in groups if item[0] == source_run_id]
    if not groups:
        suffix = f":{source_run_id}" if source_run_id else ""
        raise ExactDeliveryError(
            f"DAILY_REPORT_RECOVERY_SOURCE_NOT_FOUND:{job}:{ctx.trade_date.isoformat()}{suffix}"
        )

    run_id, raw_entries = max(
        groups,
        key=lambda item: max(str(entry.get("time") or "") for entry in item[1]),
    )
    entries = _canonical_unsent_entries(job, run_id, raw_entries)
    preview_paths, manifest_path = _manifest_source(ctx, job, run_id, entries)
    source_time = max(str(entry.get("time") or "") for entry in entries)
    source_job = str(entries[0].get("job") or job)
    return ExistingDelivery(
        requested_job=job,
        trade_date=ctx.trade_date.isoformat(),
        source_run_id=run_id,
        source_job=source_job,
        source_time=source_time,
        entries=tuple(entries),
        preview_paths=preview_paths,
        preview_manifest=manifest_path,
        signature=exact._delivery_signature(entries),
    )


def save_recovery_selection(ctx: RunnerContext, source: ExistingDelivery) -> Path:
    mode = source_recovery_mode(source)
    path = _selection_path(ctx, source.requested_job)
    write_json(path, {
        "schema": RECOVERY_SELECTION_SCHEMA,
        "job": source.requested_job,
        "trade_date": source.trade_date,
        "source_run_id": source.source_run_id,
        "source_delivery_signature": source.signature,
        "source_delivery_time": source.source_time,
        "preview_paths": [str(item) for item in source.preview_paths],
        "preview_sha256": [file_sha256(item) for item in source.preview_paths],
        "selected_at": now_wib().isoformat(timespec="seconds"),
        "source_delivery_mode": mode,
        "source_ack_ambiguous": source_ack_ambiguous(source),
    })
    return path


def clear_recovery_selection(ctx: RunnerContext, job: str) -> None:
    try:
        _selection_path(ctx, job).unlink(missing_ok=True)
    except OSError:
        pass


def has_current_recovery_selection(ctx: RunnerContext, job: str) -> bool:
    selection = read_json(_selection_path(ctx, job))
    return bool(
        isinstance(selection, dict)
        and selection.get("schema") == RECOVERY_SELECTION_SCHEMA
        and str(selection.get("job") or "").lower() == job
        and str(selection.get("trade_date") or "") == ctx.trade_date.isoformat()
    )


def load_recovery_selection(ctx: RunnerContext, job: str) -> ExistingDelivery:
    path = _selection_path(ctx, job)
    selection = read_json(path)
    if not isinstance(selection, dict) or selection.get("schema") != RECOVERY_SELECTION_SCHEMA:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_SELECTION_NOT_FOUND:{job}")
    if str(selection.get("job") or "").lower() != job:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_SELECTION_JOB_MISMATCH:{job}")
    if str(selection.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError(
            f"DAILY_REPORT_RECOVERY_SELECTION_DATE_MISMATCH:{job}:"
            f"{selection.get('trade_date')}:{ctx.trade_date.isoformat()}"
        )
    run_id = str(selection.get("source_run_id") or "").strip()
    source = find_recoverable_daily_report(ctx, job, source_run_id=run_id)
    if str(selection.get("source_delivery_signature") or "") != source.signature:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_SELECTION_INTEGRITY_FAILED:{job}:{run_id}")
    selected_mode = str(selection.get("source_delivery_mode") or "").strip()
    if selected_mode and selected_mode != source_recovery_mode(source):
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_SELECTION_MODE_CHANGED:{job}:{run_id}")
    selected_paths = [str(value) for value in selection.get("preview_paths", [])]
    current_paths = [str(value) for value in source.preview_paths]
    selected_hashes = [str(value) for value in selection.get("preview_sha256", [])]
    current_hashes = [file_sha256(value) for value in source.preview_paths]
    if selected_paths != current_paths or selected_hashes != current_hashes:
        raise ExactDeliveryError(f"DAILY_REPORT_RECOVERY_SELECTION_FILES_CHANGED:{job}:{run_id}")
    return source


def _recovery_checkpoint(
    ctx: RunnerContext,
    source: ExistingDelivery,
) -> tuple[dict[int, list[int]], set[int]]:
    try:
        events = exact._read_delivery_events(exact._delivery_log_path(ctx))
    except ExactDeliveryError:
        return {}, set()
    confirmed: dict[int, list[int]] = {}
    uncertain: set[int] = set()
    for event in events:
        if not bool(event.get("force_resend")):
            continue
        if str(event.get("copy_mode") or "") != _RECOVERY_COPY_MODE:
            continue
        if str(event.get("source_run_id") or "") != source.source_run_id:
            continue
        if str(event.get("source_delivery_signature") or "") not in {"", source.signature}:
            continue
        sequence = int(event.get("delivery_sequence") or 0)
        if sequence <= 0:
            continue
        status = str(event.get("status") or "").upper()
        ids = exact._message_ids(event)
        if status in {"SENT", "RECOVERY_ALREADY_SENT"} and ids:
            confirmed[sequence] = ids
            uncertain.discard(sequence)
        elif sequence not in confirmed and (
            status == "DELIVERY_STATE_UNCERTAIN"
            or (status == "FAILED" and _looks_like_read_timeout(event.get("error")))
        ):
            uncertain.add(sequence)
    return confirmed, uncertain


def replay_recoverable_daily_report(
    ctx: RunnerContext,
    source: ExistingDelivery,
) -> list[dict[str, Any]]:
    specs = exact._archive_replay_specs(source)
    confirmed, uncertain = _recovery_checkpoint(ctx, source)
    results: list[dict[str, Any]] = []
    log_path = exact._delivery_log_path(ctx)
    total = len(source.entries)
    source_mode = source_recovery_mode(source)

    for replay_sequence, entry in enumerate(source.entries, start=1):
        spec = specs[replay_sequence]
        report_type = str(entry.get("report_type") or "").strip().lower()
        base = {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": source.requested_job,
            "trade_date": source.trade_date,
            "report_type": report_type,
            "source_run_id": source.source_run_id,
            "source_telegram_message_ids": [],
            "message_thread_id": str(entry.get("message_thread_id") or ""),
            "delivery_sequence": replay_sequence,
            "delivery_total": total,
            "copy_mode": _RECOVERY_COPY_MODE,
            "force_resend": True,
            "source_delivery_signature": source.signature,
            "source_delivery_mode": source_mode,
            "source_ack_ambiguous": source_ack_ambiguous(source),
            "recovery_preview_path": str(spec.get("preview_path") or ""),
        }

        if replay_sequence in confirmed:
            event = {
                **base,
                "status": "RECOVERY_ALREADY_SENT",
                "part_count": len(confirmed[replay_sequence]),
                "telegram_message_ids": confirmed[replay_sequence],
                "checkpoint_reused": True,
            }
            append_jsonl(log_path, event)
            results.append(event)
            continue

        if replay_sequence in uncertain:
            event = {
                **base,
                "status": "DELIVERY_STATE_UNCERTAIN",
                "part_count": 0,
                "telegram_message_ids": [],
                "error": "Prior recovery request has ambiguous Telegram ACK; automatic retry blocked.",
                "checkpoint_reused": True,
            }
            append_jsonl(log_path, event)
            results.append(event)
            continue

        sent_ids: list[int] = []
        try:
            returned = exact._send_archived_entry(ctx, entry, spec, message_ids=sent_ids)
            if returned and not sent_ids:
                sent_ids.extend(int(value) for value in returned)
            event = {
                **base,
                "status": "SENT",
                "part_count": len(sent_ids),
                "telegram_message_ids": sent_ids,
            }
        except Exception as exc:
            safe_error = _safe_error(exc)
            ambiguous = bool(sent_ids) or _looks_like_read_timeout(safe_error)
            event = {
                **base,
                "status": "DELIVERY_STATE_UNCERTAIN" if ambiguous else "FAILED",
                "part_count": len(sent_ids),
                "telegram_message_ids": sent_ids,
                "error": safe_error,
            }
        append_jsonl(log_path, event)
        results.append(event)

    return results
