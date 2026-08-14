#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOBS = (
    "pre_market",
    "market_outlook",
    "post_market",
    "broker_summary",
    "broker_multi_day",
    "final_watchlist",
    "full_manual",
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "UNREADABLE_STATUS", "errors": [str(exc)]}


def as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        items: list[str] = []
        for nested in value.values():
            items.extend(walk_strings(nested))
        return items
    if isinstance(value, list):
        items = []
        for nested in value:
            items.extend(walk_strings(nested))
        return items
    return []


def outside_current_root(text: str) -> bool:
    if not text:
        return False
    try:
        path = Path(text)
    except Exception:
        return False
    if not path.is_absolute():
        return False
    try:
        path.resolve().relative_to(ROOT.resolve())
        return False
    except Exception:
        return True


def print_job_status(job: str, args: argparse.Namespace) -> int:
    path = ROOT / "data/output/job_status" / f"{job}_latest.json"
    payload = load_json(path)
    if not payload:
        print(f"Status belum ada: {path}")
        print("Jalankan BAT *_CEK untuk membuat status baru di folder ini.")
        return 1

    if args.status_only:
        print(str(payload.get("status", "UNKNOWN")).upper())
        return 0

    details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}
    overall_status = str(payload.get("status", "") or "UNKNOWN").upper()
    engine_status = str(details.get("engine_status") or overall_status)
    report_status = str(details.get("report_status") or ("SUCCESS" if overall_status in {"SUCCESS", "SUCCESS_WITH_WARNING"} else "NOT_RUN"))
    delivery_status = str(
        details.get("delivery_status")
        or payload.get("telegram_status")
        or ("SKIPPED" if payload.get("no_telegram") else "NOT_RUN")
    )
    if args.log_fields:
        print(
            f"run_id={payload.get('run_id', '')} "
            f"engine_status={engine_status} report_status={report_status} "
            f"delivery_status={delivery_status} overall_status={overall_status}"
        )
        return 0
    print("============================================================")
    print(f"SDE Job Status: {job}")
    print("============================================================")
    if overall_status == "SUCCESS_WITH_WARNING":
        print("[OK WITH WARNING]")
    elif overall_status == "SUCCESS":
        print("[OK] SUCCESS")
    print(f"Run ID       : {payload.get('run_id', '')}")
    print(f"Trade date   : {payload.get('trade_date', '')}")
    print(f"Mode         : {payload.get('job_mode', '')}")
    print(f"Engine       : {engine_status}")
    print(f"Report       : {report_status}")
    print(f"Delivery     : {delivery_status}")
    print(f"Overall      : {overall_status}")
    print(f"Stage        : {payload.get('current_stage', '')}")
    print(f"Exit code    : {payload.get('exit_code', '')}")

    reason = details.get("reason") or payload.get("broker_readiness_status") or details.get("status") or ""
    if reason:
        print(f"Reason       : {reason}")
    broker_date = payload.get("broker_summary_date") or details.get("broker_date") or ""
    if broker_date:
        print(f"Broker date  : {broker_date}")
    snapshot = payload.get("snapshot_id") or details.get("snapshot_id") or ""
    if snapshot:
        print(f"Snapshot ID  : {snapshot}")
    print("")

    previews = as_list(payload.get("preview_paths") or details.get("preview_paths"))
    if previews:
        print("Preview files:")
        for item in previews:
            print(f"- {item}")
    else:
        print("Preview files: belum ada")

    delivery = as_list(details.get("delivery"))
    if delivery:
        print("")
        print("Delivery:")
        for item in delivery[:10]:
            if not isinstance(item, dict):
                print(f"- {item}")
                continue
            label = item.get("report_type", "report")
            status = item.get("status", "")
            parts = item.get("part_count", "")
            force = item.get("force_resend", "")
            suffix = f", parts={parts}" if parts != "" else ""
            suffix += ", force=true" if force is True else ""
            print(f"- {label}: {status}{suffix}")
            if item.get("error"):
                print(f"  error: {item.get('error')}")

    warnings = as_list(payload.get("warnings") or details.get("warnings"))
    errors = as_list(payload.get("errors") or details.get("errors"))
    if warnings:
        print("")
        print("Warnings:")
        for item in warnings[:10]:
            print(f"- {item}")
    if errors:
        print("")
        print("Errors:")
        for item in errors[:10]:
            print(f"- {item}")

    stale_paths = sorted({text for text in walk_strings(payload) if outside_current_root(text)})
    if stale_paths:
        print("")
        print("Catatan path:")
        print("- Status ini masih memuat path absolut dari folder lain.")
        print(r"- Kalau ini muncul setelah copy folder, jalankan maintenance\RESET_RUNTIME.bat lalu ulangi mode Preview dari BAT utama.")
        for item in stale_paths[:3]:
            print(f"  contoh: {item}")

    print("")
    print(f"Status JSON  : {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print latest SDE job status in a click-friendly format"
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job")
    selection.add_argument("--jobs", nargs="+")
    selection.add_argument("--all", action="store_true")
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--log-fields", action="store_true")
    args = parser.parse_args(argv)

    jobs = list(DEFAULT_JOBS) if args.all else list(args.jobs or [args.job])
    result = 0
    for index, job in enumerate(jobs):
        if index:
            print("")
        result = print_job_status(str(job), args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
