#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _delivery_status(delivery: list[dict]) -> tuple[str, str, int]:
    if not delivery or any(str(item.get("status") or "").upper() != "SENT" for item in delivery):
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
                source = find_existing_delivery(ctx, "final_watchlist")
                selection_path = save_preview_selection(ctx, source)
                write_status(ctx, "SUCCESS", "FINAL_WATCHLIST_PREVIEW_EXACT", EXIT_SUCCESS, {
                    "engine_status": "NOT_RUN",
                    "report_status": "REUSED_EXACT",
                    "delivery_status": "SKIPPED_PREVIEW_ONLY",
                    "telegram_status": "SKIPPED",
                    "preview_paths": [str(path) for path in source.preview_paths],
                    "preview_selection": str(selection_path),
                    **source.source_details(),
                    "warnings": [
                        "EXACT_PREVIEW_READ_ONLY; Kirim Ulang dikunci ke source_run_id ini."
                    ],
                })
                print(f"SOURCE RUN: {source.source_run_id}")
                for path in source.preview_paths:
                    print(path)
                return EXIT_SUCCESS

            source = load_preview_selection(ctx, "final_watchlist")
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
                "preview_paths": [str(path) for path in source.preview_paths],
                "delivery": delivery,
                **source.source_details(),
                "warnings": [
                    "TELEGRAM_EXACT_REPLAY; copyMessage diprioritaskan, dengan fallback preview hash-locked tanpa formatter."
                ],
            })
            return code
    except ExactDeliveryError as exc:
        write_status(ctx, "FAILED", f"{mode}_SOURCE", EXIT_FAILED, {
            "engine_status": "NOT_RUN",
            "report_status": "NOT_RUN",
            "delivery_status": "NOT_RUN",
            "errors": [str(exc)],
            "warnings": [
                "Jalankan Preview Existing terlebih dahulu; resend tidak boleh memilih atau membangun format sendiri."
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
