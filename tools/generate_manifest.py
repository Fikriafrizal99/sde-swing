#!/usr/bin/env python3
"""Generate deterministic package manifests after tests and cleanup."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".git", ".venv"}
EXCLUDED_NAMES = {"MANIFEST_SHA256.txt", "RELEASE_MANIFEST.json"}
EXCLUDED_REL_PREFIXES = {
    "data/cache/",
    "data/output/analytics/",
    "data/output/dry_run/",
    "data/output/failed_delivery/",
    "data/output/global_market/",
    "data/output/job_status/",
    "data/output/manifests/",
    "data/output/previews/",
    "data/output/reports/",
    "data/output/snapshots/",
    "data/output/telegram_preview/",
    "data/state/scheduler/locks/",
    "scheduler/windows/generated/",
}
EXCLUDED_REL_PATHS = {
    "data/output/telegram_ui_preview_dry_run.log",
    "data/state/scheduler/delivery_log.jsonl",
    "data/state/scheduler/telegram_idempotency.json",
    "logs/master_pipeline.log",
    "logs/sde_job_runner.log",
}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    rel_posix = rel.as_posix()
    if any(part in EXCLUDED_PARTS for part in rel.parts):
        return False
    if rel_posix in EXCLUDED_REL_PATHS:
        return False
    if any(rel_posix.startswith(prefix) for prefix in EXCLUDED_REL_PREFIXES):
        return False
    if path.name in EXCLUDED_NAMES or path.suffix == ".pyc":
        return False
    return path.is_file()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="SDE_SWING_V1_6_0_SIGNAL_QUALITY_ENTRY_READINESS")
    args = parser.parse_args()
    files = sorted((p for p in ROOT.rglob("*") if included(p)), key=lambda p: p.relative_to(ROOT).as_posix())
    entries = [(sha256(path), path.relative_to(ROOT).as_posix(), path.stat().st_size) for path in files]
    (ROOT / "MANIFEST_SHA256.txt").write_text(
        "".join(f"{digest}  {rel}\n" for digest, rel, _ in entries), encoding="utf-8"
    )
    categories: dict[str, int] = {}
    for _, rel, _ in entries:
        category = rel.split("/", 1)[0] if "/" in rel else "root"
        categories[category] = categories.get(category, 0) + 1
    release = {
        "Package_Version": args.version,
        "Generated_At_UTC": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "File_Count": len(entries),
        "Total_Bytes": sum(size for _, _, size in entries),
        "Categories": categories,
        "Manifest_File": "MANIFEST_SHA256.txt",
        "Manifest_SHA256": sha256(ROOT / "MANIFEST_SHA256.txt"),
        "Excluded": sorted(
            EXCLUDED_PARTS
            | EXCLUDED_NAMES
            | {"*.pyc"}
            | EXCLUDED_REL_PREFIXES
            | EXCLUDED_REL_PATHS
        ),
    }
    (ROOT / "RELEASE_MANIFEST.json").write_text(json.dumps(release, indent=2), encoding="utf-8")
    print(f"Generated {len(entries)} hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
