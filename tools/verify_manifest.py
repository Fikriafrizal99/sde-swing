#!/usr/bin/env python3
"""Verify package SHA-256 manifest and detect untracked package files."""
from __future__ import annotations

import hashlib
import json
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
    return (
        path.is_file()
        and not any(part in EXCLUDED_PARTS for part in rel.parts)
        and rel_posix not in EXCLUDED_REL_PATHS
        and not any(rel_posix.startswith(prefix) for prefix in EXCLUDED_REL_PREFIXES)
        and path.name not in EXCLUDED_NAMES
        and path.suffix != ".pyc"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    manifest_path = ROOT / "MANIFEST_SHA256.txt"
    release_path = ROOT / "RELEASE_MANIFEST.json"
    if not manifest_path.exists() or not release_path.exists():
        print("FAIL: manifest files are missing")
        return 2

    expected: dict[str, str] = {}
    errors: list[str] = []
    for number, raw in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            digest, rel = raw.split("  ", 1)
        except ValueError:
            errors.append(f"line {number}: invalid format")
            continue
        expected[rel] = digest

    actual_paths = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if included(path)
    }
    expected_paths = set(expected)
    for rel in sorted(expected_paths - actual_paths):
        errors.append(f"missing: {rel}")
    for rel in sorted(actual_paths - expected_paths):
        errors.append(f"untracked: {rel}")
    for rel in sorted(actual_paths & expected_paths):
        actual = sha256(ROOT / rel)
        if actual != expected[rel]:
            errors.append(f"hash mismatch: {rel}")

    release = json.loads(release_path.read_text(encoding="utf-8"))
    if release.get("Manifest_SHA256") != sha256(manifest_path):
        errors.append("RELEASE_MANIFEST Manifest_SHA256 mismatch")
    if int(release.get("File_Count", -1)) != len(expected):
        errors.append("RELEASE_MANIFEST File_Count mismatch")

    if errors:
        print(f"FAIL: {len(errors)} issue(s)")
        for error in errors:
            print("-", error)
        return 1
    print(f"PASS: {len(expected)} files verified")
    print("Package version:", release.get("Package_Version", "UNKNOWN"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
