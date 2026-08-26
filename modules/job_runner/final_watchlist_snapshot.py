from __future__ import annotations

"""Preview-locked Final Watchlist presentation snapshot.

This module deliberately sits after the trading engine. Preview builds a fresh
presentation from already-produced Final Watchlist source artifacts, freezes the
exact Telegram text/chart/CSV bytes, and stores a hash-locked selection receipt.
Resend then replays only that approved snapshot. The engine, scoring, decisions,
trade plan, broker calculations, and source artifacts are never rerun or mutated.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from swing_utils import file_sha256

from . import existing_delivery as exact
from .delivery import telegram_route
from .enhanced_runtime_bridge import final_watchlist_payloads
from .existing_delivery import ExactDeliveryError, ExistingDelivery
from .reports import ReportPayload, write_payloads
from .runtime import RunnerContext, append_jsonl, now_wib, read_json, write_json


SNAPSHOT_SELECTION_SCHEMA = "SDE_FINAL_WATCHLIST_PRESENTATION_SELECTION_V1"
SNAPSHOT_COPY_MODE = "FINAL_WATCHLIST_PRESENTATION_SNAPSHOT_V1"
MAX_DETAIL_CARDS = 10
_ALLOWED_TYPES = {
    "final_watchlist_summary",
    "final_watchlist_detail",
    "final_watchlist_csv",
}


def _selection_path(ctx: RunnerContext) -> Path:
    return ctx.state_root / "final_watchlist_presentation_selection.json"


def _safe_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return re.sub(r"/bot[^/\s]+/", "/bot***REDACTED***/", text)


def _is_read_timeout(value: Any) -> bool:
    text = str(value or "").lower()
    return "readtimeout" in text or "read timed out" in text


def _snapshot_signature(manifest_hash: str, routes: dict[str, str]) -> str:
    rendered = json.dumps(
        {"manifest_sha256": manifest_hash, "routes": routes},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _manifest_paths(manifest: dict[str, Any]) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    hashes: list[str] = []
    for record in manifest.get("payloads", []) if isinstance(manifest.get("payloads"), list) else []:
        if not isinstance(record, dict):
            continue
        raw_preview = str(record.get("run_scoped_preview") or "").strip()
        preview_hash = str(record.get("preview_sha256") or "").strip()
        if not raw_preview or not preview_hash:
            raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_PREVIEW_ARCHIVE_MISSING")
        preview = exact.resolve(raw_preview)
        if not preview.exists() or not preview.is_file() or file_sha256(preview) != preview_hash:
            raise ExactDeliveryError(f"FINAL_WATCHLIST_SNAPSHOT_PREVIEW_INTEGRITY_FAILED:{preview}")
        paths.append(preview)
        hashes.append(preview_hash)

        raw_attachment = str(record.get("attachment_archive") or "").strip()
        attachment_hash = str(record.get("attachment_sha256") or "").strip()
        if raw_attachment:
            attachment = exact.resolve(raw_attachment)
            if not attachment_hash:
                raise ExactDeliveryError(
                    f"FINAL_WATCHLIST_SNAPSHOT_ATTACHMENT_HASH_MISSING:{record.get('report_type')}"
                )
            if (
                not attachment.exists()
                or not attachment.is_file()
                or file_sha256(attachment) != attachment_hash
            ):
                raise ExactDeliveryError(
                    f"FINAL_WATCHLIST_SNAPSHOT_ATTACHMENT_INTEGRITY_FAILED:{attachment}"
                )
            paths.append(attachment)
            hashes.append(attachment_hash)
    return paths, hashes


def _validate_payload_contract(payloads: list[ReportPayload]) -> None:
    types = [str(payload.report_type or "").strip().lower() for payload in payloads]
    unknown = sorted(set(types) - _ALLOWED_TYPES)
    if unknown:
        raise ExactDeliveryError(
            "FINAL_WATCHLIST_SNAPSHOT_UNEXPECTED_REPORT_TYPE:" + ",".join(unknown)
        )
    if types.count("final_watchlist_summary") != 1:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_SUMMARY_COUNT_INVALID")
    if types.count("final_watchlist_csv") != 1:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_CSV_COUNT_INVALID")
    detail = [payload for payload in payloads if str(payload.report_type).lower() == "final_watchlist_detail"]
    if len(detail) > MAX_DETAIL_CARDS:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_SNAPSHOT_DETAIL_LIMIT_EXCEEDED:{len(detail)}:{MAX_DETAIL_CARDS}"
        )
    for payload in detail:
        attachment = getattr(payload, "attachment_path", None)
        path = Path(attachment) if attachment not in (None, "") else None
        if path is None or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            symbol = str(getattr(payload, "symbol", "") or "UNKNOWN").upper()
            raise ExactDeliveryError(f"FINAL_WATCHLIST_SNAPSHOT_CHART_REQUIRED:{symbol}")


def create_snapshot(ctx: RunnerContext) -> dict[str, Any]:
    """Render current presentation from existing engine artifacts and freeze it."""
    payloads = final_watchlist_payloads(ctx)
    _validate_payload_contract(payloads)

    # Capture route while the real payload/topic is still available. Replay uses
    # the same thread ID even though it no longer invokes the formatter/router.
    routes = {
        str(index): str(telegram_route(ctx, payload).get("message_thread_id") or "")
        for index, payload in enumerate(payloads, start=1)
    }
    preview_paths = write_payloads(ctx, payloads)
    manifest_path = ctx.previews_root / ctx.trade_date.isoformat() / f"{ctx.run_id}_preview_manifest.json"
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") != "SDE_DELIVERY_PREVIEW_BUNDLE_V1":
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_INVALID")
    if str(manifest.get("run_id") or "") != ctx.run_id:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_RUN_MISMATCH")
    if str(manifest.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_DATE_MISMATCH")

    archived_paths, archived_hashes = _manifest_paths(manifest)
    manifest_hash = file_sha256(manifest_path)
    signature = _snapshot_signature(manifest_hash, routes)
    report_types = [str(value) for value in manifest.get("report_types", [])]
    detail_count = sum(1 for value in report_types if value == "final_watchlist_detail")

    selection = {
        "schema": SNAPSHOT_SELECTION_SCHEMA,
        "job": "final_watchlist",
        "trade_date": ctx.trade_date.isoformat(),
        "snapshot_run_id": ctx.run_id,
        "snapshot_manifest": str(manifest_path),
        "snapshot_manifest_sha256": manifest_hash,
        "snapshot_signature": signature,
        "message_thread_ids": routes,
        "approved_paths": [str(path) for path in archived_paths],
        "approved_sha256": archived_hashes,
        "report_types": report_types,
        "payload_count": len(report_types),
        "detail_count": detail_count,
        "max_detail_cards": MAX_DETAIL_CARDS,
        "created_at": now_wib().isoformat(timespec="seconds"),
    }
    path = _selection_path(ctx)
    write_json(path, selection)
    return {
        **selection,
        "selection_path": str(path),
        "preview_paths": [str(path) for path in preview_paths],
    }


def load_snapshot(ctx: RunnerContext) -> ExistingDelivery:
    selection = read_json(_selection_path(ctx))
    if not isinstance(selection, dict) or selection.get("schema") != SNAPSHOT_SELECTION_SCHEMA:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_SELECTION_NOT_FOUND")
    if str(selection.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_SNAPSHOT_SELECTION_DATE_MISMATCH:"
            f"{selection.get('trade_date')}:{ctx.trade_date.isoformat()}"
        )

    manifest_path = exact.resolve(str(selection.get("snapshot_manifest") or ""))
    if not manifest_path.exists() or not manifest_path.is_file():
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_NOT_FOUND")
    expected_manifest_hash = str(selection.get("snapshot_manifest_sha256") or "")
    if not expected_manifest_hash or file_sha256(manifest_path) != expected_manifest_hash:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_CHANGED")
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") != "SDE_DELIVERY_PREVIEW_BUNDLE_V1":
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_INVALID")
    run_id = str(selection.get("snapshot_run_id") or "")
    if str(manifest.get("run_id") or "") != run_id:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_RUN_MISMATCH")
    if str(manifest.get("trade_date") or "") != ctx.trade_date.isoformat():
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_MANIFEST_DATE_MISMATCH")

    paths, hashes = _manifest_paths(manifest)
    if [str(path) for path in paths] != [str(value) for value in selection.get("approved_paths", [])]:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_PATHS_CHANGED")
    if hashes != [str(value) for value in selection.get("approved_sha256", [])]:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_HASHES_CHANGED")

    routes = {
        str(key): str(value or "")
        for key, value in dict(selection.get("message_thread_ids") or {}).items()
    }
    signature = _snapshot_signature(expected_manifest_hash, routes)
    if signature != str(selection.get("snapshot_signature") or ""):
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_SELECTION_INTEGRITY_FAILED")

    records = manifest.get("payloads") if isinstance(manifest.get("payloads"), list) else []
    entries: list[dict[str, Any]] = []
    detail_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        sequence = int(record.get("sequence") or 0)
        report_type = str(record.get("report_type") or "").strip().lower()
        if report_type not in _ALLOWED_TYPES or sequence <= 0:
            raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_RECORD_INVALID")
        if report_type == "final_watchlist_detail":
            detail_count += 1
        entries.append({
            "delivery_sequence": sequence,
            "report_type": report_type,
            "message_thread_id": routes.get(str(sequence), ""),
            "attachment_path": str(record.get("attachment_archive") or ""),
            "signature": str(record.get("signature") or ""),
            "telegram_message_ids": [],
        })
    if detail_count > MAX_DETAIL_CARDS:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_DETAIL_LIMIT_EXCEEDED")
    if sum(1 for item in entries if item["report_type"] == "final_watchlist_summary") != 1:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_SUMMARY_COUNT_INVALID")
    if sum(1 for item in entries if item["report_type"] == "final_watchlist_csv") != 1:
        raise ExactDeliveryError("FINAL_WATCHLIST_SNAPSHOT_CSV_COUNT_INVALID")

    return ExistingDelivery(
        requested_job="final_watchlist",
        trade_date=ctx.trade_date.isoformat(),
        source_run_id=run_id,
        source_job="final_watchlist_snapshot",
        source_time=str(selection.get("created_at") or ""),
        entries=tuple(entries),
        preview_paths=tuple(paths),
        preview_manifest=manifest_path,
        signature=signature,
    )


def _checkpoint(ctx: RunnerContext, source: ExistingDelivery) -> tuple[dict[int, list[int]], set[int]]:
    try:
        events = exact._read_delivery_events(exact._delivery_log_path(ctx))
    except ExactDeliveryError:
        return {}, set()
    confirmed: dict[int, list[int]] = {}
    uncertain: set[int] = set()
    for event in events:
        if str(event.get("copy_mode") or "") != SNAPSHOT_COPY_MODE:
            continue
        if str(event.get("source_run_id") or "") != source.source_run_id:
            continue
        if str(event.get("source_delivery_signature") or "") != source.signature:
            continue
        try:
            sequence = int(event.get("delivery_sequence") or 0)
        except (TypeError, ValueError):
            continue
        if sequence <= 0:
            continue
        status = str(event.get("status") or "").upper()
        ids = exact._message_ids(event)
        if status in {"SENT", "SNAPSHOT_ALREADY_SENT"} and ids:
            confirmed[sequence] = ids
            uncertain.discard(sequence)
        elif sequence not in confirmed and status == "DELIVERY_STATE_UNCERTAIN":
            uncertain.add(sequence)
    return confirmed, uncertain


def replay_snapshot(ctx: RunnerContext, source: ExistingDelivery) -> list[dict[str, Any]]:
    """Send the approved preview exactly once per sequence, resumably."""
    specs = exact._archive_replay_specs(source)
    confirmed, uncertain = _checkpoint(ctx, source)
    log_path = exact._delivery_log_path(ctx)
    results: list[dict[str, Any]] = []
    hard_failed = False
    total = len(source.entries)

    for replay_sequence, entry in enumerate(source.entries, start=1):
        report_type = str(entry.get("report_type") or "")
        base = {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": "final_watchlist",
            "trade_date": source.trade_date,
            "report_type": report_type,
            "source_run_id": source.source_run_id,
            "message_thread_id": str(entry.get("message_thread_id") or ""),
            "delivery_sequence": replay_sequence,
            "delivery_total": total,
            "copy_mode": SNAPSHOT_COPY_MODE,
            "force_resend": True,
            "source_delivery_signature": source.signature,
        }

        if replay_sequence in confirmed:
            event = {
                **base,
                "status": "SNAPSHOT_ALREADY_SENT",
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
                "automatic_retry_blocked": True,
                "manual_verification_required": True,
                "reason": "PRIOR_READ_TIMEOUT_REMOTE_OUTCOME_UNKNOWN",
            }
            append_jsonl(log_path, event)
            results.append(event)
            continue
        if hard_failed:
            event = {
                **base,
                "status": "SKIPPED_AFTER_SNAPSHOT_FAILURE",
                "telegram_message_ids": [],
            }
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
        except Exception as exc:
            error = _safe_error(exc)
            ambiguous = bool(sent_ids) or _is_read_timeout(error)
            if ambiguous:
                event = {
                    **base,
                    "status": "DELIVERY_STATE_UNCERTAIN",
                    "part_count": len(sent_ids),
                    "telegram_message_ids": sent_ids,
                    "error": error,
                    "automatic_retry_blocked": True,
                    "manual_verification_required": True,
                    "reason": "PARTIAL_ACK_THEN_FAILURE" if sent_ids else "READ_TIMEOUT_REMOTE_OUTCOME_UNKNOWN",
                }
            else:
                hard_failed = True
                event = {
                    **base,
                    "status": "FAILED",
                    "part_count": 0,
                    "telegram_message_ids": [],
                    "error": error,
                }
        append_jsonl(log_path, event)
        results.append(event)
    return results


__all__ = [
    "MAX_DETAIL_CARDS",
    "SNAPSHOT_COPY_MODE",
    "create_snapshot",
    "load_snapshot",
    "replay_snapshot",
]
