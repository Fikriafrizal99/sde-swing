from __future__ import annotations

"""Fail-closed recovery for a Final Watchlist prepared but never accepted by Telegram.

Normal exact resend intentionally selects only fully delivered source runs.  This
module covers the different recovery case where the report bundle was prepared
and archived successfully but every Telegram delivery attempt failed before any
message_id was returned.  Recovery replays only immutable, hash-locked run-scoped
preview/archive files; it never invokes a formatter, engine, or mutable LATEST
artifact.
"""

import json
import re
from pathlib import Path
from typing import Any

from swing_utils import file_sha256

from . import existing_delivery as exact
from .existing_delivery import ExactDeliveryError, ExistingDelivery
from .runtime import RunnerContext, append_jsonl, now_wib, read_json, write_json


RECOVERY_SELECTION_SCHEMA = "SDE_FINAL_WATCHLIST_RECOVERY_SELECTION_V1"
_MODERN_TYPES = {
    "final_watchlist_summary",
    "final_watchlist_detail",
    "final_watchlist_csv",
}
_LEGACY_TYPES = {"final_watchlist", "signal_detail"}


def _selection_path(ctx: RunnerContext) -> Path:
    return ctx.state_root / "final_watchlist_recovery_selection.json"


def _safe_error(exc: Exception) -> str:
    # Requests exceptions can embed the Telegram bot token in the request URL.
    text = f"{type(exc).__name__}: {exc}"
    return re.sub(r"/bot[^/\s]+/", "/bot***REDACTED***/", text)


def _candidate_groups(ctx: RunnerContext) -> list[tuple[str, list[dict[str, Any]]]]:
    trade_date = ctx.trade_date.isoformat()
    accepted = set(exact._REPORT_TYPES["final_watchlist"])
    events = exact._read_delivery_events(exact._delivery_log_path(ctx))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if str(event.get("trade_date") or "") != trade_date:
            continue
        if str(event.get("job") or "").lower() not in {"final_watchlist", "full_manual"}:
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
    return list(grouped.items())


def _canonical_failed_entries(
    run_id: str,
    entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str, int]:
    ordered = sorted(
        entries,
        key=lambda item: (
            int(item.get("delivery_sequence") or 0),
            str(item.get("time") or ""),
        ),
    )
    present = {str(item.get("report_type") or "").strip().lower() for item in ordered}
    if "final_watchlist_summary" in present:
        family = "MODERN"
        allowed = _MODERN_TYPES
        required = "final_watchlist_summary"
    elif "final_watchlist" in present:
        family = "LEGACY"
        allowed = _LEGACY_TYPES
        required = "final_watchlist"
    else:
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_SUMMARY_MISSING:{run_id}")

    candidates = [
        item for item in ordered
        if str(item.get("report_type") or "").strip().lower() in allowed
    ]
    if not any(str(item.get("report_type") or "").strip().lower() == required for item in candidates):
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_SUMMARY_MISSING:{run_id}:{family}")

    # Recovery is allowed only when Telegram gave us zero acknowledgement for
    # the whole logical bundle.  Any message_id means a resend could duplicate
    # content, so fail closed and require manual investigation instead.
    acknowledged = [
        message_id
        for entry in candidates
        for message_id in exact._message_ids(entry)
    ]
    if acknowledged:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_BLOCKED_PARTIAL_TELEGRAM_ACK:{run_id}:"
            + ",".join(str(value) for value in acknowledged)
        )

    unsafe_statuses = sorted({
        str(item.get("status") or "").upper()
        for item in candidates
        if str(item.get("status") or "").upper() != "FAILED"
    })
    if unsafe_statuses:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_BLOCKED_NONFAILED_STATE:{run_id}:"
            + ",".join(unsafe_statuses)
        )

    canonical: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    dropped = 0
    for entry in candidates:
        report_type = str(entry.get("report_type") or "").strip().lower()
        sequence = int(entry.get("delivery_sequence") or 0)
        signature = str(entry.get("signature") or "").strip()
        identity = (sequence, report_type, signature)
        if identity in seen:
            dropped += 1
            continue
        if sequence <= 0:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_RECOVERY_SEQUENCE_INVALID:{run_id}:{report_type}"
            )
        seen.add(identity)
        canonical.append(dict(entry))

    if not canonical:
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_SOURCE_EMPTY:{run_id}:{family}")
    return canonical, family, dropped


def _manifest_source(
    ctx: RunnerContext,
    run_id: str,
    entries: list[dict[str, Any]],
) -> tuple[tuple[Path, ...], Path]:
    folder = ctx.previews_root / ctx.trade_date.isoformat()
    manifest_path = folder / f"{run_id}_preview_manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") != "SDE_DELIVERY_PREVIEW_BUNDLE_V1":
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_MANIFEST_NOT_FOUND:{run_id}")
    if str(manifest.get("run_id") or "") != run_id:
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_MANIFEST_RUN_MISMATCH:{run_id}")
    if str(manifest.get("job") or "").lower() not in {"final_watchlist", "full_manual"}:
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_MANIFEST_JOB_MISMATCH:{run_id}")
    if str(manifest.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_MANIFEST_DATE_MISMATCH:{run_id}")
    if str(manifest.get("state") or "").upper() not in {"PREPARED", "DELIVERY_INCOMPLETE"}:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_MANIFEST_STATE_UNSAFE:{run_id}:{manifest.get('state')}"
        )
    if manifest.get("delivery_complete") is True:
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_ALREADY_DELIVERED:{run_id}")

    records = manifest.get("payloads")
    if not isinstance(records, list):
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_PAYLOAD_MANIFEST_MISSING:{run_id}")
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

    approved: list[Path] = []
    seen_paths: set[str] = set()
    for entry in entries:
        sequence = int(entry.get("delivery_sequence") or 0)
        report_type = str(entry.get("report_type") or "").strip().lower()
        record = by_sequence.get(sequence)
        if not isinstance(record, dict):
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_RECOVERY_ARCHIVE_RECORD_MISSING:{run_id}:{sequence}:{report_type}"
            )
        if str(record.get("report_type") or "").strip().lower() != report_type:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_RECOVERY_ARCHIVE_TYPE_MISMATCH:{run_id}:{sequence}:{report_type}"
            )
        entry_signature = str(entry.get("signature") or "").strip()
        record_signature = str(record.get("signature") or "").strip()
        if entry_signature and record_signature and entry_signature != record_signature:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_RECOVERY_ARCHIVE_SIGNATURE_MISMATCH:{run_id}:{sequence}:{report_type}"
            )
        parts = record.get("telegram_parts")
        if not isinstance(parts, list) or not parts:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_RECOVERY_TELEGRAM_PARTS_MISSING:{run_id}:{sequence}:{report_type}"
            )

        raw_preview = str(record.get("run_scoped_preview") or "").strip()
        expected_preview_hash = str(record.get("preview_sha256") or "").strip()
        if not raw_preview or not expected_preview_hash:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_RECOVERY_PREVIEW_ARCHIVE_MISSING:{run_id}:{sequence}:{report_type}"
            )
        preview = exact.resolve(raw_preview)
        if not preview.exists() or not preview.is_file() or file_sha256(preview) != expected_preview_hash:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_RECOVERY_PREVIEW_INTEGRITY_FAILED:{run_id}:{sequence}:{report_type}"
            )
        preview_key = str(preview.resolve())
        if preview_key not in seen_paths:
            approved.append(preview)
            seen_paths.add(preview_key)

        source_had_attachment = bool(str(entry.get("attachment_path") or "").strip())
        raw_attachment = str(record.get("attachment_archive") or "").strip()
        expected_attachment_hash = str(record.get("attachment_sha256") or "").strip()
        if source_had_attachment:
            if not raw_attachment or not expected_attachment_hash:
                raise ExactDeliveryError(
                    f"FINAL_WATCHLIST_RECOVERY_ATTACHMENT_ARCHIVE_MISSING:{run_id}:{sequence}:{report_type}"
                )
            attachment = exact.resolve(raw_attachment)
            if (
                not attachment.exists()
                or not attachment.is_file()
                or file_sha256(attachment) != expected_attachment_hash
            ):
                raise ExactDeliveryError(
                    f"FINAL_WATCHLIST_RECOVERY_ATTACHMENT_INTEGRITY_FAILED:{run_id}:{sequence}:{report_type}"
                )
            attachment_key = str(attachment.resolve())
            if attachment_key not in seen_paths:
                approved.append(attachment)
                seen_paths.add(attachment_key)

    if not approved:
        raise ExactDeliveryError(f"FINAL_WATCHLIST_RECOVERY_ARCHIVE_EMPTY:{run_id}")
    return tuple(approved), manifest_path


def find_recoverable_final_watchlist(
    ctx: RunnerContext,
    *,
    source_run_id: str | None = None,
) -> tuple[ExistingDelivery, str, int]:
    groups = _candidate_groups(ctx)
    if source_run_id:
        groups = [item for item in groups if item[0] == source_run_id]
    if not groups:
        suffix = f":{source_run_id}" if source_run_id else ""
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_SOURCE_NOT_FOUND:{ctx.trade_date.isoformat()}{suffix}"
        )

    # Inspect the latest source run first. Never silently fall back to an older
    # same-date run if the latest one is unsafe/partially acknowledged.
    run_id, raw_entries = max(
        groups,
        key=lambda item: max(str(entry.get("time") or "") for entry in item[1]),
    )
    entries, family, dropped = _canonical_failed_entries(run_id, raw_entries)
    preview_paths, manifest_path = _manifest_source(ctx, run_id, entries)
    source_time = max(str(entry.get("time") or "") for entry in entries)
    source_job = str(entries[0].get("job") or "final_watchlist")
    source = ExistingDelivery(
        requested_job="final_watchlist",
        trade_date=ctx.trade_date.isoformat(),
        source_run_id=run_id,
        source_job=source_job,
        source_time=source_time,
        entries=tuple(entries),
        preview_paths=preview_paths,
        preview_manifest=manifest_path,
        signature=exact._delivery_signature(entries),
    )
    return source, family, dropped


def save_recovery_selection(
    ctx: RunnerContext,
    source: ExistingDelivery,
    family: str,
) -> Path:
    path = _selection_path(ctx)
    write_json(path, {
        "schema": RECOVERY_SELECTION_SCHEMA,
        "job": "final_watchlist",
        "trade_date": source.trade_date,
        "source_run_id": source.source_run_id,
        "source_delivery_signature": source.signature,
        "source_delivery_time": source.source_time,
        "canonical_format_family": family,
        "preview_paths": [str(item) for item in source.preview_paths],
        "preview_sha256": [file_sha256(item) for item in source.preview_paths],
        "selected_at": now_wib().isoformat(timespec="seconds"),
        "source_delivery_mode": "RECOVERABLE_UNSENT",
    })
    return path


def clear_recovery_selection(ctx: RunnerContext) -> None:
    path = _selection_path(ctx)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # A stale recovery selector is never used unless its date matches, so a
        # cleanup failure cannot make an exact delivered resend unsafe.
        pass


def has_current_recovery_selection(ctx: RunnerContext) -> bool:
    selection = read_json(_selection_path(ctx))
    return bool(
        isinstance(selection, dict)
        and selection.get("schema") == RECOVERY_SELECTION_SCHEMA
        and str(selection.get("trade_date") or "") == ctx.trade_date.isoformat()
    )


def load_recovery_selection(ctx: RunnerContext) -> tuple[ExistingDelivery, str, int]:
    path = _selection_path(ctx)
    selection = read_json(path)
    if not isinstance(selection, dict) or selection.get("schema") != RECOVERY_SELECTION_SCHEMA:
        raise ExactDeliveryError("FINAL_WATCHLIST_RECOVERY_SELECTION_NOT_FOUND")
    if str(selection.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_SELECTION_DATE_MISMATCH:"
            f"{selection.get('trade_date')}:{ctx.trade_date.isoformat()}"
        )
    run_id = str(selection.get("source_run_id") or "").strip()
    source, family, dropped = find_recoverable_final_watchlist(ctx, source_run_id=run_id)
    if str(selection.get("source_delivery_signature") or "") != source.signature:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_SELECTION_INTEGRITY_FAILED:{run_id}"
        )
    selected_paths = [str(value) for value in selection.get("preview_paths", [])]
    current_paths = [str(value) for value in source.preview_paths]
    selected_hashes = [str(value) for value in selection.get("preview_sha256", [])]
    current_hashes = [file_sha256(value) for value in source.preview_paths]
    if selected_paths != current_paths or selected_hashes != current_hashes:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_SELECTION_FILES_CHANGED:{run_id}"
        )
    if str(selection.get("canonical_format_family") or "") != family:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_RECOVERY_SELECTION_FAMILY_CHANGED:{run_id}"
        )
    return source, family, dropped


def replay_recoverable_final_watchlist(
    ctx: RunnerContext,
    source: ExistingDelivery,
) -> list[dict[str, Any]]:
    """Send the selected immutable archive directly; no copyMessage is possible."""
    specs = exact._archive_replay_specs(source)
    results: list[dict[str, Any]] = []
    failed = False
    total = len(source.entries)
    log_path = exact._delivery_log_path(ctx)

    for replay_sequence, entry in enumerate(source.entries, start=1):
        report_type = str(entry.get("report_type") or "").strip().lower()
        base = {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": "final_watchlist",
            "trade_date": source.trade_date,
            "report_type": report_type,
            "source_run_id": source.source_run_id,
            "source_telegram_message_ids": [],
            "message_thread_id": str(entry.get("message_thread_id") or ""),
            "delivery_sequence": replay_sequence,
            "delivery_total": total,
            "copy_mode": "ARCHIVED_PREVIEW_EXACT_RECOVERY",
            "force_resend": True,
            "source_delivery_signature": source.signature,
            "source_delivery_mode": "RECOVERABLE_UNSENT",
        }
        if failed:
            event = {**base, "status": "SKIPPED_AFTER_RECOVERY_FAILURE", "telegram_message_ids": []}
            append_jsonl(log_path, event)
            results.append(event)
            continue

        sent_ids: list[int] = []
        try:
            exact._send_archived_entry(
                ctx,
                entry,
                specs[replay_sequence],
                message_ids=sent_ids,
            )
            event = {
                **base,
                "status": "SENT",
                "part_count": len(sent_ids),
                "telegram_message_ids": sent_ids,
            }
            append_jsonl(log_path, event)
            results.append(event)
        except Exception as exc:
            failed = True
            event = {
                **base,
                "status": "FAILED",
                "part_count": len(sent_ids),
                "telegram_message_ids": sent_ids,
                "error": _safe_error(exc),
            }
            append_jsonl(log_path, event)
            results.append(event)
    return results
