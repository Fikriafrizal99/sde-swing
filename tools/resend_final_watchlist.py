#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from swing_utils import file_sha256
from modules.job_runner.existing_delivery import (
    ExactDeliveryError,
    copy_existing_delivery,
    find_existing_delivery,
    load_preview_selection,
    save_preview_selection,
)
from modules.job_runner.runtime import (
    EXIT_DELIVERY_FAILED,
    EXIT_FAILED,
    EXIT_SUCCESS,
    FileLock,
    JobAlreadyRunning,
    ResourceLocked,
    load_context,
    read_json,
    resolve,
    write_status,
)


# Final Watchlist has had two presentation generations. A source run can contain
# compatibility delivery rows from both generations. Resend must choose exactly
# one family or it can replay the same logical report twice in different formats.
_MODERN_FINAL_WATCHLIST_TYPES = {
    "final_watchlist_summary",
    "final_watchlist_detail",
    "final_watchlist_csv",
}
_LEGACY_FINAL_WATCHLIST_TYPES = {
    "final_watchlist",
    "signal_detail",
}


def _source_message_ids(entry: dict) -> list[int]:
    raw = entry.get("telegram_message_ids")
    if not isinstance(raw, list):
        raw = [entry.get("telegram_message_id")] if entry.get("telegram_message_id") not in (None, "") else []
    result: list[int] = []
    for value in raw:
        try:
            message_id = int(value)
        except (TypeError, ValueError):
            continue
        if message_id > 0 and message_id not in result:
            result.append(message_id)
    return result


def _canonicalize_final_watchlist_source(source):
    """Select one presentation family and suppress duplicate source rows.

    Modern Final Watchlist is authoritative whenever its summary exists in the
    selected run. Legacy rows remain supported only for genuinely legacy runs.
    Telegram message IDs are also unique across the replay plan so one original
    message can never be copied twice because of duplicate audit rows.
    """
    ordered = sorted(
        source.entries,
        key=lambda item: (
            int(item.get("delivery_sequence") or 0),
            str(item.get("time") or ""),
        ),
    )
    present = {str(item.get("report_type") or "").strip().lower() for item in ordered}
    if "final_watchlist_summary" in present:
        family = "MODERN"
        allowed = _MODERN_FINAL_WATCHLIST_TYPES
        required = "final_watchlist_summary"
    elif "final_watchlist" in present:
        family = "LEGACY"
        allowed = _LEGACY_FINAL_WATCHLIST_TYPES
        required = "final_watchlist"
    else:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_CANONICAL_SOURCE_NOT_FOUND:{source.source_run_id}"
        )

    candidates = [
        dict(item)
        for item in ordered
        if str(item.get("report_type") or "").strip().lower() in allowed
    ]
    if not any(str(item.get("report_type") or "").strip().lower() == required for item in candidates):
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_CANONICAL_SUMMARY_MISSING:{source.source_run_id}:{family}"
        )

    canonical: list[dict] = []
    seen_message_ids: set[int] = set()
    seen_payloads: set[tuple[str, str, str]] = set()
    dropped = 0

    for entry in candidates:
        report_type = str(entry.get("report_type") or "").strip().lower()
        message_ids = _source_message_ids(entry)
        if not message_ids:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_SOURCE_MESSAGE_ID_MISSING:{source.source_run_id}:{report_type}"
            )

        overlap = seen_message_ids.intersection(message_ids)
        if overlap:
            # A completely repeated message-id set is an append-only audit
            # duplicate and is safe to suppress. Partial overlap is ambiguous:
            # fail closed rather than risk a partial/double replay.
            if set(message_ids).issubset(seen_message_ids):
                dropped += 1
                continue
            overlap_text = ",".join(str(value) for value in sorted(overlap))
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_SOURCE_MESSAGE_ID_OVERLAP:{source.source_run_id}:{overlap_text}"
            )

        identity = (
            report_type,
            str(entry.get("idempotency_key") or "").strip(),
            str(entry.get("signature") or "").strip(),
        )
        if (identity[1] or identity[2]) and identity in seen_payloads:
            dropped += 1
            continue

        entry["telegram_message_ids"] = message_ids
        entry.pop("telegram_message_id", None)
        canonical.append(entry)
        seen_message_ids.update(message_ids)
        if identity[1] or identity[2]:
            seen_payloads.add(identity)

    if not canonical:
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_CANONICAL_SOURCE_EMPTY:{source.source_run_id}:{family}"
        )

    return replace(source, entries=tuple(canonical)), family, dropped


def _preflight_exact_bundle(source) -> dict[str, int]:
    """Require a complete immutable media fallback before sending anything.

    Telegram has no read-only probe for copyMessage. Without this preflight an
    old text summary can be sent successfully and a later photo can fail, leaving
    a partial Final Watchlist. New source runs archive every attachment and its
    SHA-256 before delivery, so they can be replayed atomically enough for this
    workflow: every media payload has a verified fallback before message #1 is
    attempted. Legacy media runs without that archive are blocked up front.
    """
    media_entries = [
        entry for entry in source.entries
        if str(entry.get("attachment_path") or "").strip()
    ]
    if not media_entries:
        return {"media_entries": 0, "verified_media_entries": 0}

    manifest_path = source.preview_manifest
    if manifest_path is None or not manifest_path.exists():
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED_LEGACY_MEDIA:{source.source_run_id}:"
            "IMMUTABLE_ATTACHMENT_ARCHIVE_MISSING"
        )

    manifest = read_json(manifest_path)
    payloads = manifest.get("payloads") if isinstance(manifest, dict) else None
    if not isinstance(payloads, list):
        raise ExactDeliveryError(
            f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED_LEGACY_MEDIA:{source.source_run_id}:"
            "PAYLOAD_ARCHIVE_MANIFEST_MISSING"
        )

    records_by_sequence: dict[int, dict] = {}
    for record in payloads:
        if not isinstance(record, dict):
            continue
        try:
            sequence = int(record.get("sequence") or 0)
        except (TypeError, ValueError):
            continue
        if sequence > 0:
            records_by_sequence[sequence] = record

    approved_paths = {
        str(path.resolve())
        for path in source.preview_paths
        if path.exists() and path.is_file()
    }
    verified = 0
    for replay_sequence, entry in enumerate(source.entries, start=1):
        if not str(entry.get("attachment_path") or "").strip():
            continue
        try:
            source_sequence = int(entry.get("delivery_sequence") or replay_sequence)
        except (TypeError, ValueError):
            source_sequence = replay_sequence
        record = records_by_sequence.get(source_sequence)
        report_type = str(entry.get("report_type") or "").strip().lower()
        if not isinstance(record, dict):
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED:{source.source_run_id}:"
                f"{report_type}:{source_sequence}:ARCHIVE_RECORD_MISSING"
            )

        raw_archive = str(record.get("attachment_archive") or "").strip()
        expected_hash = str(record.get("attachment_sha256") or "").strip()
        if not raw_archive or not expected_hash:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED_LEGACY_MEDIA:{source.source_run_id}:"
                f"{report_type}:{source_sequence}:IMMUTABLE_ATTACHMENT_ARCHIVE_MISSING"
            )

        archive_path = resolve(raw_archive)
        if not archive_path.exists() or not archive_path.is_file():
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED:{source.source_run_id}:"
                f"{report_type}:{source_sequence}:ARCHIVE_FILE_MISSING"
            )
        if str(archive_path.resolve()) not in approved_paths:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED:{source.source_run_id}:"
                f"{report_type}:{source_sequence}:ARCHIVE_NOT_APPROVED"
            )
        if file_sha256(archive_path) != expected_hash:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED:{source.source_run_id}:"
                f"{report_type}:{source_sequence}:ARCHIVE_HASH_MISMATCH"
            )

        parts = record.get("telegram_parts")
        kinds = {
            str(part.get("kind") or "").strip().lower()
            for part in parts
            if isinstance(part, dict)
        } if isinstance(parts, list) else set()
        required_kind = "photo" if archive_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else "document"
        if required_kind not in kinds:
            raise ExactDeliveryError(
                f"FINAL_WATCHLIST_ATOMIC_RESEND_BLOCKED:{source.source_run_id}:"
                f"{report_type}:{source_sequence}:TELEGRAM_PARTS_MISSING_{required_kind.upper()}"
            )
        verified += 1

    return {"media_entries": len(media_entries), "verified_media_entries": verified}


def _bundle_readiness(source) -> tuple[str, str, dict[str, int]]:
    try:
        details = _preflight_exact_bundle(source)
        return "READY", "", details
    except ExactDeliveryError as exc:
        return "BLOCKED", str(exc), {"media_entries": 0, "verified_media_entries": 0}


def _delivery_status(delivery: list[dict]) -> tuple[str, str, int]:
    if not delivery or any(str(item.get("status") or "").upper() != "SENT" for item in delivery):
        return "FAILED", "FAILED", EXIT_DELIVERY_FAILED
    return "SUCCESS", "SENT", EXIT_SUCCESS


def _message_ids(delivery: list[dict]) -> list[int]:
    result: list[int] = []
    for item in delivery:
        for value in item.get("telegram_message_ids", []) or []:
            try:
                message_id = int(value)
            except (TypeError, ValueError):
                continue
            if message_id not in result:
                result.append(message_id)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview/kirim ulang exact FINAL WATCHLIST yang sudah terkirim; "
            "tidak menjalankan formatter, engine, atau membaca artifact LATEST"
        )
    )
    parser.add_argument("--trade-date", required=True, help="Tanggal analisis YYYY-MM-DD")
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Pilih dan tampilkan preview exact, lalu kunci run sumber untuk Kirim Ulang",
    )
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ctx = load_context(
        job="final_watchlist",
        config_path=args.config,
        scheduler_config_path=args.scheduler_config,
        trade_date=args.trade_date,
        dry_run=False,
        preview_existing=bool(args.preview_only),
        no_telegram=bool(args.preview_only),
        force=not bool(args.preview_only),
        debug=False,
        interactive_broker=False,
    )
    setattr(ctx, "delivery_only", True)
    mode = "PREVIEW_EXACT_DELIVERY" if args.preview_only else "RESEND_EXACT_PREVIEW"

    write_status(ctx, "RUNNING", mode, EXIT_SUCCESS, {
        "engine_status": "NOT_RUN",
        "report_status": "REUSING_EXACT_DELIVERY",
        "delivery_status": "NOT_RUN",
        "warnings": ["FORMATTER_DISABLED; artifact LATEST tidak dibaca."],
    })

    try:
        with FileLock(ctx):
            if args.preview_only:
                raw_source = find_existing_delivery(ctx, "final_watchlist")
                source, family, dropped = _canonicalize_final_watchlist_source(raw_source)
                readiness, readiness_reason, readiness_details = _bundle_readiness(source)
                selection_path = save_preview_selection(ctx, raw_source)
                report_types = [str(item.get("report_type") or "") for item in source.entries]
                warnings = [
                    "EXACT_PREVIEW_READ_ONLY; Kirim Ulang dikunci ke source_run_id ini.",
                    "FINAL_WATCHLIST_CANONICALIZED; format modern diprioritaskan dan message ID duplikat disuppress.",
                ]
                if readiness == "BLOCKED":
                    warnings.append(
                        "ATOMIC_RESEND_BLOCKED; source legacy media tidak punya immutable archive lengkap, sehingga resend tidak akan mengirim pesan parsial."
                    )
                write_status(ctx, "SUCCESS", "FINAL_WATCHLIST_PREVIEW_EXACT", EXIT_SUCCESS, {
                    "engine_status": "NOT_RUN",
                    "report_status": "REUSED_EXACT",
                    "delivery_status": "SKIPPED_PREVIEW_ONLY",
                    "telegram_status": "SKIPPED",
                    "preview_paths": [str(path) for path in raw_source.preview_paths],
                    "preview_selection": str(selection_path),
                    "canonical_format_family": family,
                    "canonical_report_types": report_types,
                    "canonical_message_count": source.message_count,
                    "duplicate_source_entries_suppressed": dropped,
                    "exact_resend_readiness": readiness,
                    "exact_resend_block_reason": readiness_reason,
                    **readiness_details,
                    **source.source_details(),
                    "warnings": warnings,
                })
                print(f"SOURCE RUN: {source.source_run_id}")
                print(f"FORMAT FAMILY: {family}")
                print(f"MESSAGE COUNT: {source.message_count}")
                print("REPORT TYPES: " + ", ".join(report_types))
                print(f"EXACT RESEND READY: {readiness}")
                if readiness_reason:
                    print(f"BLOCK REASON: {readiness_reason}")
                if dropped:
                    print(f"DUPLICATE SOURCE ROWS SUPPRESSED: {dropped}")
                for path in raw_source.preview_paths:
                    print(path)
                return EXIT_SUCCESS

            raw_source = load_preview_selection(ctx, "final_watchlist")
            source, family, dropped = _canonicalize_final_watchlist_source(raw_source)
            # Atomic preflight happens before copyMessage #1. This prevents a
            # legacy summary from being sent when a later media card has no
            # immutable fallback and Telegram copyMessage turns out unavailable.
            preflight = _preflight_exact_bundle(source)
            delivery = copy_existing_delivery(ctx, source)
            overall, telegram_status, code = _delivery_status(delivery)
            ids = _message_ids(delivery)
            write_status(ctx, overall, "FINAL_WATCHLIST_RESEND_EXACT", code, {
                "engine_status": "NOT_RUN",
                "report_status": "REUSED_EXACT",
                "delivery_status": telegram_status,
                "telegram_status": telegram_status,
                "telegram_message_ids": ids,
                "telegram_part_count": len(ids),
                "preview_paths": [str(path) for path in raw_source.preview_paths],
                "delivery": delivery,
                "canonical_format_family": family,
                "canonical_report_types": [str(item.get("report_type") or "") for item in source.entries],
                "canonical_message_count": source.message_count,
                "duplicate_source_entries_suppressed": dropped,
                "exact_resend_readiness": "READY",
                **preflight,
                **source.source_details(),
                "warnings": [
                    "FINAL_WATCHLIST_CANONICAL_REPLAY; modern/legacy tidak dicampur dan message ID duplikat disuppress.",
                    "ATOMIC_MEDIA_PREFLIGHT_PASSED; semua media punya immutable hash-locked fallback sebelum pesan pertama dikirim.",
                ],
            })
            return code
    except ExactDeliveryError as exc:
        write_status(ctx, "FAILED", f"{mode}_SOURCE", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "exact_resend_readiness": "BLOCKED",
            "errors": [str(exc)],
            "warnings": [
                "RESEND_ABORTED_BEFORE_SEND; tidak ada pesan Telegram yang dikirim oleh attempt ini.",
                "Jalankan Preview Existing untuk melihat readiness source run sebelum Kirim Ulang."
            ],
        })
        print(str(exc), file=sys.stderr)
        return EXIT_FAILED
    except (JobAlreadyRunning, ResourceLocked) as exc:
        write_status(ctx, "SKIPPED", f"{mode}_LOCK", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "errors": [str(exc)],
        })
        return EXIT_FAILED
    except Exception as exc:
        write_status(ctx, "FAILED", f"{mode}_EXCEPTION", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "REUSED_EXACT",
            "delivery_status": "FAILED",
            "errors": [f"{type(exc).__name__}: {exc}"],
        })
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
