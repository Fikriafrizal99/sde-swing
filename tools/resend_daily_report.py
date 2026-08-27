#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.daily_report_recovery import (
    clear_recovery_selection,
    find_recoverable_daily_report,
    has_current_recovery_selection,
    load_recovery_selection,
    replay_recoverable_daily_report,
    save_recovery_selection,
    source_ack_ambiguous,
    source_recovery_mode,
)
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
    write_status,
)

SUPPORTED_JOBS = {"market_outlook", "post_market"}


def _delivery_result(delivery: list[dict]) -> tuple[str, str, int]:
    successful = {"SENT", "RECOVERY_ALREADY_SENT"}
    if not delivery or any(str(item.get("status") or "").upper() not in successful for item in delivery):
        return "FAILED", "FAILED", EXIT_DELIVERY_FAILED
    return "SUCCESS", "SENT", EXIT_SUCCESS


def _message_ids(delivery: list[dict]) -> list[int]:
    result: list[int] = []
    for item in delivery:
        for value in item.get("telegram_message_ids", []) or []:
            try:
                result.append(int(value))
            except (TypeError, ValueError):
                continue
    return result


def _source_details(source, source_mode: str) -> dict:
    details = source.source_details()
    details["source_delivery_mode"] = source_mode
    if source_mode.startswith("RECOVERABLE_"):
        details.update({
            "source_message_count": 0,
            "replay_mode": "ARCHIVED_DAILY_REPORT_EXACT_RECOVERY",
            "recovery_outbound_entry_count": len(source.entries),
            "source_ack_ambiguous": source_ack_ambiguous(source),
        })
    return details


def _preview_source(ctx, job: str):
    try:
        source = find_existing_delivery(ctx, job)
    except ExactDeliveryError as exc:
        if not str(exc).startswith(f"EXACT_DELIVERY_NOT_FOUND:{job}:"):
            raise
        source = find_recoverable_daily_report(ctx, job)
        source_mode = source_recovery_mode(source)
        selection_path = save_recovery_selection(ctx, source)
        return source, source_mode, selection_path

    selection_path = save_preview_selection(ctx, source)
    clear_recovery_selection(ctx, job)
    return source, "DELIVERED", selection_path


def _load_selected_source(ctx, job: str):
    if has_current_recovery_selection(ctx, job):
        source = load_recovery_selection(ctx, job)
        return source, source_recovery_mode(source)
    return load_preview_selection(ctx, job), "DELIVERED"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Preview/kirim ulang exact Market Outlook atau Post Market; source SENT diprioritaskan, "
            "dan source unsent yang aman (FAILED tanpa ACK atau NO_TELEGRAM recovery) dapat dipulihkan "
            "dari immutable archive. Formatter, engine, dan artifact LATEST tidak digunakan."
        )
    )
    parser.add_argument("--job", required=True, choices=sorted(SUPPORTED_JOBS))
    parser.add_argument("--trade-date", required=True, help="Tanggal trading YYYY-MM-DD")
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
        job=args.job,
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
                source, source_mode, selection_path = _preview_source(ctx, args.job)
                warnings = [
                    "EXACT_PREVIEW_READ_ONLY; Kirim Ulang dikunci ke source_run_id ini."
                ]
                if source_mode.startswith("RECOVERABLE_"):
                    if source_mode == "RECOVERABLE_FAILED":
                        warnings.append(
                            "FAILED_DELIVERY_RECOVERY_SOURCE; tidak ada source SENT, sehingga Preview memakai immutable archive dari delivery gagal tanpa Telegram message_id."
                        )
                    elif source_mode == "RECOVERABLE_NO_TELEGRAM":
                        warnings.append(
                            "NO_TELEGRAM_RECOVERY_SOURCE; tidak ada source SENT, sehingga Preview memakai immutable archive hasil recovery [9] yang sengaja tidak dikirim ke Telegram."
                        )
                    warnings.append(
                        "RECOVERY_HASH_LOCKED_ARCHIVE_ONLY; formatter, engine, dan artifact LATEST tidak digunakan saat Kirim Ulang."
                    )
                    if source_ack_ambiguous(source):
                        warnings.append(
                            "TELEGRAM_ACK_AMBIGUOUS_READ_TIMEOUT; request asli timeout saat menunggu respons. Telegram mungkin sempat menerima pesan; Kirim Ulang [3] adalah keputusan operator dan dapat menghasilkan duplikat."
                        )
                write_status(ctx, "SUCCESS", f"{args.job.upper()}_PREVIEW_EXACT", EXIT_SUCCESS, {
                    "engine_status": "NOT_RUN",
                    "report_status": "REUSED_EXACT",
                    "delivery_status": "SKIPPED_PREVIEW_ONLY",
                    "telegram_status": "SKIPPED",
                    "preview_paths": [str(path) for path in source.preview_paths],
                    "preview_selection": str(selection_path),
                    **_source_details(source, source_mode),
                    "warnings": warnings,
                })
                print(f"SOURCE RUN: {source.source_run_id}")
                print(f"SOURCE MODE: {source_mode}")
                if source_mode == "RECOVERABLE_FAILED" and source_ack_ambiguous(source):
                    print("SOURCE ACK: AMBIGUOUS_READ_TIMEOUT")
                    print("WARNING: Telegram mungkin menerima request asli; [3] dapat menduplikasi pesan.")
                for path in source.preview_paths:
                    print(path)
                return EXIT_SUCCESS

            source, source_mode = _load_selected_source(ctx, args.job)
            if source_mode.startswith("RECOVERABLE_"):
                delivery = replay_recoverable_daily_report(ctx, source)
            else:
                delivery = copy_existing_delivery(ctx, source)
            overall, telegram_status, code = _delivery_result(delivery)
            ids = _message_ids(delivery)
            warnings = []
            if source_mode.startswith("RECOVERABLE_"):
                warnings.append(
                    "TELEGRAM_UNSENT_ARCHIVE_RECOVERY; immutable preview/archive yang dikunci oleh [2] dikirim tanpa menjalankan formatter atau engine."
                )
                if source_ack_ambiguous(source):
                    warnings.append(
                        "SOURCE_ACK_WAS_AMBIGUOUS; duplicate tetap mungkin karena request asli mengalami ReadTimeout tanpa Telegram message_id."
                    )
            else:
                warnings.append(
                    "TELEGRAM_EXACT_REPLAY; copyMessage diprioritaskan, dengan fallback preview hash-locked tanpa formatter."
                )
            write_status(ctx, overall, f"{args.job.upper()}_RESEND_EXACT", code, {
                "engine_status": "NOT_RUN",
                "report_status": "REUSED_EXACT",
                "delivery_status": telegram_status,
                "telegram_status": telegram_status,
                "telegram_message_ids": ids,
                "telegram_part_count": len(ids),
                "preview_paths": [str(path) for path in source.preview_paths],
                "delivery": delivery,
                **_source_details(source, source_mode),
                "warnings": warnings,
            })
            return code
    except ExactDeliveryError as exc:
        write_status(ctx, "FAILED", f"{mode}_SOURCE", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "errors": [str(exc)],
            "warnings": [
                "Jalankan Preview Existing terlebih dahulu; resend tidak boleh memilih atau membangun format sendiri.",
                "Recovery hanya diterima jika bundle immutable lengkap, nol Telegram ACK, dan source berstatus FAILED atau NO_TELEGRAM.",
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
