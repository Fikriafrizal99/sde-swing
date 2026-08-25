from __future__ import annotations

"""Exact preview/replay support for already-delivered Telegram reports.

The resend boundary is intentionally presentation-only.  It selects a source
run from the append-only delivery log, exposes that run's immutable preview
files, and replays the original Telegram messages with ``copyMessage``.  No
report formatter, engine output, or ``LATEST`` artifact is read here.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import requests
except ImportError:  # pragma: no cover - handled by the live copy boundary
    requests = None

from swing_utils import file_sha256

from .delivery import _credentials, _response_json
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
            "replay_mode": "TELEGRAM_COPY_EXACT",
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
        if manifest.get("schema") == "SDE_DELIVERY_PREVIEW_BUNDLE_V1":
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
    """Copy the exact original Telegram messages in their original order."""
    token, chat_id = _credentials(ctx)
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
                copied = int(response.get("result", {}).get("message_id") or 0)
                if copied <= 0:
                    raise RuntimeError(f"Telegram copyMessage tidak mengembalikan message_id: {response}")
                copied_ids.append(copied)
            event = {**base, "status": "SENT", "telegram_message_ids": copied_ids}
            append_jsonl(_delivery_log_path(ctx), event)
            results.append(event)
        except Exception as exc:
            failed = True
            event = {
                **base,
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "telegram_message_ids": copied_ids,
                "sent_parts_before_failure": len(copied_ids),
            }
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
