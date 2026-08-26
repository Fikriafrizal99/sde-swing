#!/usr/bin/env python3
from __future__ import annotations

"""Recovery-aware Final Watchlist preview/resend entrypoint.

Delivered reports keep the established copyMessage-first exact resend path.
When no delivered source exists for the trade date, a strictly unsent failed
bundle may be selected and replayed from its immutable hash-locked archive.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.resend_final_watchlist as baseline
from modules.job_runner.existing_delivery import (
    ExactDeliveryError,
    copy_existing_delivery,
    find_existing_delivery,
    load_preview_selection,
    save_preview_selection,
)
from modules.job_runner.final_watchlist_recovery import (
    clear_recovery_selection,
    find_recoverable_final_watchlist,
    has_current_recovery_selection,
    load_recovery_selection,
    replay_recoverable_final_watchlist,
    save_recovery_selection,
)
from modules.job_runner.runtime import (
    EXIT_FAILED,
    EXIT_SUCCESS,
    FileLock,
    JobAlreadyRunning,
    ResourceLocked,
    load_context,
    write_status,
)


def _source_details(source, source_mode: str) -> dict:
    details = source.source_details()
    if source_mode == "RECOVERABLE_UNSENT":
        details.update({
            "source_delivery_mode": source_mode,
            "source_message_count": 0,
            "replay_mode": "ARCHIVED_PREVIEW_EXACT_RECOVERY",
            "recovery_outbound_entry_count": len(source.entries),
        })
    else:
        details["source_delivery_mode"] = "DELIVERED"
    return details


def _preview_source(ctx):
    try:
        raw_source = find_existing_delivery(ctx, "final_watchlist")
    except ExactDeliveryError as exc:
        if not str(exc).startswith("EXACT_DELIVERY_NOT_FOUND:final_watchlist:"):
            raise
        source, family, dropped = find_recoverable_final_watchlist(ctx)
        selection_path = save_recovery_selection(ctx, source, family)
        return source, source, family, dropped, "RECOVERABLE_UNSENT", selection_path

    source, family, dropped = baseline._canonicalize_final_watchlist_source(raw_source)
    selection_path = save_preview_selection(ctx, raw_source)
    clear_recovery_selection(ctx)
    return raw_source, source, family, dropped, "DELIVERED", selection_path


def _load_selected_source(ctx):
    if has_current_recovery_selection(ctx):
        source, family, dropped = load_recovery_selection(ctx)
        return source, source, family, dropped, "RECOVERABLE_UNSENT"
    raw_source = load_preview_selection(ctx, "final_watchlist")
    source, family, dropped = baseline._canonicalize_final_watchlist_source(raw_source)
    return raw_source, source, family, dropped, "DELIVERED"


def main() -> int:
    args = baseline.parse_args()
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
                raw_source, source, family, dropped, source_mode, selection_path = _preview_source(ctx)
                readiness, readiness_reason, readiness_details = baseline._bundle_readiness(source)
                report_types = [str(item.get("report_type") or "") for item in source.entries]
                warnings = [
                    "EXACT_PREVIEW_READ_ONLY; Kirim Ulang dikunci ke source_run_id ini.",
                    "FINAL_WATCHLIST_CANONICALIZED; format modern diprioritaskan dan source duplikat disuppress.",
                ]
                if source_mode == "RECOVERABLE_UNSENT":
                    warnings.extend([
                        "RECOVERY_SOURCE_UNSENT; source dipilih karena seluruh delivery gagal tanpa satu pun Telegram message_id.",
                        "RECOVERY_HASH_LOCKED_ARCHIVE_ONLY; formatter, engine, dan artifact LATEST tidak digunakan saat resend.",
                    ])
                if readiness == "BLOCKED":
                    warnings.append(
                        "ATOMIC_RESEND_BLOCKED; immutable archive tidak lengkap sehingga resend tidak akan dimulai."
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
                    **_source_details(source, source_mode),
                    "warnings": warnings,
                })
                print(f"SOURCE RUN: {source.source_run_id}")
                print(f"SOURCE MODE: {source_mode}")
                print(f"FORMAT FAMILY: {family}")
                if source_mode == "DELIVERED":
                    print(f"SOURCE MESSAGE COUNT: {source.message_count}")
                else:
                    print(f"SOURCE MESSAGE COUNT: 0 (UNSENT RECOVERY)")
                    print(f"RECOVERY OUTBOUND ENTRIES: {len(source.entries)}")
                print("REPORT TYPES: " + ", ".join(report_types))
                print(f"EXACT RESEND READY: {readiness}")
                if readiness_reason:
                    print(f"BLOCK REASON: {readiness_reason}")
                if dropped:
                    print(f"DUPLICATE SOURCE ROWS SUPPRESSED: {dropped}")
                for path in raw_source.preview_paths:
                    print(path)
                return EXIT_SUCCESS

            raw_source, source, family, dropped, source_mode = _load_selected_source(ctx)
            # Preflight runs before the first outbound request for both normal
            # exact resend and failed-delivery recovery.
            preflight = baseline._preflight_exact_bundle(source)
            if source_mode == "RECOVERABLE_UNSENT":
                delivery = replay_recoverable_final_watchlist(ctx, source)
            else:
                delivery = copy_existing_delivery(ctx, source)
            overall, telegram_status, code = baseline._delivery_status(delivery)
            ids = baseline._message_ids(delivery)
            warnings = [
                "ATOMIC_MEDIA_PREFLIGHT_PASSED; semua media punya immutable hash-locked fallback sebelum pesan pertama dikirim."
            ]
            if source_mode == "RECOVERABLE_UNSENT":
                warnings.insert(
                    0,
                    "FINAL_WATCHLIST_RECOVERY_REPLAY; source gagal-delivery tanpa Telegram ACK dikirim langsung dari immutable archive.",
                )
            else:
                warnings.insert(
                    0,
                    "FINAL_WATCHLIST_CANONICAL_REPLAY; modern/legacy tidak dicampur dan message ID duplikat disuppress.",
                )
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
                **_source_details(source, source_mode),
                "warnings": warnings,
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
                "Jalankan Preview Existing untuk melihat readiness source run sebelum Kirim Ulang.",
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
