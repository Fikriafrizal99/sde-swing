#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_DIRS = [
    "data/output/analytics",
    "data/cache/global_market",
    "data/output/dry_run",
    "data/output/failed_delivery",
    "data/output/global_market",
    "data/output/job_status",
    "data/output/manifests",
    "data/output/previews",
    "data/output/reports",
    "data/output/snapshots",
    "data/output/telegram_preview",
    "data/state/scheduler/locks",
]

RUNTIME_FILES = [
    "data/output/telegram_ui_preview_dry_run.log",
    "data/state/scheduler/delivery_log.jsonl",
    "data/state/scheduler/telegram_idempotency.json",
    "logs/master_pipeline.log",
    "logs/sde_job_runner.log",
]


def safe_path(rel: str) -> Path:
    path = (ROOT / rel).resolve()
    path.relative_to(ROOT.resolve())
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean SDE runtime status, logs, previews, cache, and locks")
    parser.add_argument("--apply", action="store_true", help="actually delete files; without this only prints the plan")
    args = parser.parse_args()

    targets = [safe_path(rel) for rel in RUNTIME_DIRS + RUNTIME_FILES]
    action = "DELETE" if args.apply else "DRY RUN"
    print(f"{action}: runtime artifacts under {ROOT}")

    for path in targets:
        if not path.exists():
            print(f"- skip missing: {path.relative_to(ROOT)}")
            continue
        if not args.apply:
            print(f"- would remove: {path.relative_to(ROOT)}")
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"- removed: {path.relative_to(ROOT)}")

    if args.apply:
        (ROOT / "logs").mkdir(exist_ok=True)
        (ROOT / "logs/.gitkeep").touch(exist_ok=True)
        (ROOT / "data/state/scheduler").mkdir(parents=True, exist_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
