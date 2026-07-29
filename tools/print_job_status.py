#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Print latest SDE job status in a click-friendly format")
    parser.add_argument("--job", required=True)
    args = parser.parse_args()

    path = ROOT / "data/output/job_status" / f"{args.job}_latest.json"
    payload = load_json(path)
    if not payload:
        print(f"Status belum ada: {path}")
        print("Jalankan BAT *_CEK untuk membuat status baru di folder ini.")
        return 1

    details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}
    print("============================================================")
    print(f"SDE Job Status: {args.job}")
    print("============================================================")
    print(f"Run ID       : {payload.get('run_id', '')}")
    print(f"Trade date   : {payload.get('trade_date', '')}")
    print(f"Mode         : {payload.get('job_mode', '')}")
    print(f"Status       : {payload.get('status', '')}")
    print(f"Stage        : {payload.get('current_stage', '')}")
    print(f"Exit code    : {payload.get('exit_code', '')}")
    print(f"Telegram     : {payload.get('telegram_status', '') or 'DATA_NOT_AVAILABLE'}")

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


if __name__ == "__main__":
    raise SystemExit(main())
