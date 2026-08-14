#!/usr/bin/env python3
"""Serialized, atomic publisher for the canonical FINAL_DECISION_V2 artifact.

The quant/fusion calculation remains owned by modules.broker_fusion.broker_fusion.
This module adds only artifact-integrity controls around that engine:

- one exclusive write lock for the canonical V2 artifact;
- a run-scoped immutable evidence copy;
- atomic publication to the shared canonical path;
- SHA-256 equivalence checks;
- atomic canonical/run manifest publication.

It deliberately does not modify broker scoring, thresholds, or decision semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import file_sha256, make_run_id


DEFAULT_ARTIFACT_ROOT = Path("data/output/fusion")
DEFAULT_LOCK_ROOT = Path("data/state/artifacts")


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _atomic_write_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.parent / (
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()


def _atomic_write_json(destination: Path, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    _atomic_write_bytes(destination, body)


def _atomic_publish_file(source: Path, destination: Path) -> str:
    if not source.exists() or source.stat().st_size <= 0:
        raise RuntimeError(f"Run-scoped artifact missing or empty: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.parent / (
        f".{destination.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with source.open("rb") as src, temp.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()

    source_hash = file_sha256(source)
    destination_hash = file_sha256(destination)
    if not source_hash or source_hash != destination_hash:
        raise RuntimeError(
            "FINAL_DECISION_V2 publication hash mismatch: "
            f"run_scoped={source_hash or 'MISSING'} canonical={destination_hash or 'MISSING'}"
        )
    return destination_hash


def read_published_v2_snapshot(
    canonical_output: Path,
    *,
    attempts: int = 40,
    retry_delay_seconds: float = 0.005,
) -> tuple[bytes, dict[str, Any]]:
    """Read one complete canonical V2 publication generation.

    The publisher replaces the CSV before replacing its manifest.  A reader
    can therefore briefly observe a new CSV with the preceding manifest.  A
    context consumer must retry that transition instead of treating either
    file independently or taking the writer lock itself.

    Returning immutable bytes lets downstream code create run-scoped derived
    artifacts without ever writing back to the canonical publication.
    """

    canonical = _resolve(canonical_output)
    manifest_path = canonical.with_suffix(".manifest.json")
    last_error = "publication files unavailable"
    for attempt in range(max(1, int(attempts))):
        try:
            manifest_before = manifest_path.read_bytes()
            artifact = canonical.read_bytes()
            manifest_after = manifest_path.read_bytes()
            if manifest_before != manifest_after:
                last_error = "manifest changed while reading"
            else:
                manifest = json.loads(manifest_after.decode("utf-8"))
                expected_hash = str(
                    manifest.get("canonical_output_hash")
                    or manifest.get("output_hash")
                    or ""
                ).strip()
                actual_hash = hashlib.sha256(artifact).hexdigest()
                publication_status = str(manifest.get("publication_status", ""))
                integrity_version = str(manifest.get("artifact_integrity_version", ""))
                if publication_status != "PUBLISHED_ATOMIC":
                    last_error = f"unexpected publication status {publication_status or 'MISSING'}"
                elif integrity_version != "V2_SERIALIZED_ATOMIC_V1":
                    last_error = f"unexpected integrity version {integrity_version or 'MISSING'}"
                elif not expected_hash or expected_hash != actual_hash:
                    last_error = (
                        "manifest/artifact hash mismatch "
                        f"{expected_hash or 'MISSING'} != {actual_hash}"
                    )
                else:
                    return artifact, manifest
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < max(1, int(attempts)) - 1:
            time.sleep(max(0.0, float(retry_delay_seconds)))
    raise RuntimeError(f"FINAL_DECISION_V2_STABLE_SNAPSHOT_UNAVAILABLE: {last_error}")


def _broker_period_lineage(broker_path: Path) -> dict[str, Any]:
    """Return optional broker-period lineage owned by this publication."""

    sidecar_path = broker_path.with_suffix(".manifest.json")
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    snapshot_id = str(
        sidecar.get("snapshot_id") or sidecar.get("broker_snapshot_id") or ""
    ).strip()
    if not snapshot_id:
        return {}
    return {
        "broker_period_snapshot_id": snapshot_id,
        "broker_period_type": sidecar.get("broker_period_type", ""),
        "broker_period_start": sidecar.get("broker_period_start", ""),
        "broker_period_end": sidecar.get("broker_period_end", ""),
        "broker_period_source": sidecar.get("broker_period_source", ""),
        "broker_period_summary_hash": sidecar.get("summary_snapshot_hash")
        or sidecar.get("summary_source_hash", ""),
        "broker_period_raw_snapshot_hash": sidecar.get("raw_snapshot_hash")
        or sidecar.get("raw_source_hash", ""),
    }


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return ctypes.get_last_error() == 5
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


class ArtifactWriteLock:
    """Exclusive file lock for one canonical artifact writer."""

    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        stale_after_seconds: int = 3 * 60 * 60,
    ):
        self.path = path
        self.run_id = run_id
        self.stale_after_seconds = max(60, int(stale_after_seconds))
        self._fd: int | None = None

    def _clear_stale(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        created_raw = str(payload.get("created_at", ""))
        pid = int(payload.get("pid") or 0)
        same_host = str(payload.get("host", "")) == socket.gethostname()
        try:
            created = datetime.fromisoformat(created_raw)
            age = (datetime.now().astimezone() - created.astimezone()).total_seconds()
        except Exception:
            age = self.stale_after_seconds + 1

        owner_alive = same_host and _process_alive(pid)
        if age > self.stale_after_seconds and not owner_alive:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def __enter__(self) -> "ArtifactWriteLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clear_stale()
        payload = {
            "run_id": self.run_id,
            "artifact": "FINAL_DECISION_V2",
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        try:
            self._fd = os.open(
                str(self.path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(
                self._fd,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            )
            os.fsync(self._fd)
            return self
        except FileExistsError as exc:
            raise RuntimeError(
                f"FINAL_DECISION_V2 writer lock is active: {self.path}"
            ) from exc

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def publish_run_scoped_artifact(
    *,
    run_scoped_output: Path,
    canonical_output: Path,
    base_manifest: dict[str, Any],
    run_id: str,
    manifest_dir: Path | None,
    lock_path: Path,
) -> dict[str, Any]:
    """Atomically publish one completed run-scoped V2 artifact."""

    run_hash = file_sha256(run_scoped_output)
    if not run_hash:
        raise RuntimeError(f"Run-scoped V2 hash unavailable: {run_scoped_output}")

    canonical_hash = _atomic_publish_file(run_scoped_output, canonical_output)
    if canonical_hash != run_hash:
        raise RuntimeError(
            f"V2 hash mismatch after publication: {run_hash} != {canonical_hash}"
        )

    published_at = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = dict(base_manifest)
    manifest.update(
        {
            "Run_ID": run_id,
            "output": str(canonical_output.resolve()),
            "output_hash": canonical_hash,
            "run_scoped_output": str(run_scoped_output.resolve()),
            "run_scoped_output_hash": run_hash,
            "canonical_output": str(canonical_output.resolve()),
            "canonical_output_hash": canonical_hash,
            "artifact_integrity_version": "V2_SERIALIZED_ATOMIC_V1",
            "publication_status": "PUBLISHED_ATOMIC",
            "published_at": published_at,
            "writer_lock": str(lock_path.resolve()),
            "lineage_complete": bool(
                base_manifest.get("technical_source_hash")
                and base_manifest.get("broker_source_hash")
                and run_id
            ),
        }
    )

    canonical_manifest = canonical_output.with_suffix(".manifest.json")
    _atomic_write_json(canonical_manifest, manifest)
    if manifest_dir is not None:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(
            manifest_dir / f"BROKER_FUSION_MANIFEST_{run_id}.json",
            manifest,
        )
    _atomic_write_json(
        run_scoped_output.with_suffix(".manifest.json"),
        manifest,
    )
    return manifest


def _run_fusion(args: argparse.Namespace) -> tuple[Any, Path, dict[str, Any]]:
    from modules.broker_fusion.broker_fusion import (
        fuse,
        newest_broker_file,
        newest_candidate_file,
    )

    technical_path = (
        newest_candidate_file(args.technical)
        if args.technical.is_dir()
        else args.technical
    )
    broker_path = newest_broker_file(args.broker)

    artifact_root = _resolve(args.artifact_root)
    run_root = artifact_root / args.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    run_scoped_output = run_root / args.output.name

    result = fuse(
        technical_path,
        broker_path,
        run_scoped_output,
        args.run_id,
        args.data_quality_status,
        None,
        min_coverage=args.min_coverage,
        expected_broker_date=args.expected_broker_date,
        allow_partial_broker=args.allow_partial_broker,
        allow_date_mismatch=args.allow_date_mismatch,
        broker_raw_path=args.broker_raw,
    )
    staged_manifest_path = run_scoped_output.with_suffix(".manifest.json")
    if not staged_manifest_path.exists():
        raise RuntimeError(
            f"Broker Fusion did not create its run-scoped manifest: {staged_manifest_path}"
        )
    try:
        base_manifest = json.loads(
            staged_manifest_path.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise RuntimeError(
            f"Invalid run-scoped Broker Fusion manifest: {staged_manifest_path}"
        ) from exc
    base_manifest.update(_broker_period_lineage(broker_path))
    return result, run_scoped_output, base_manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serialized/atomic publisher for Broker Fusion FINAL_DECISION_V2"
    )
    parser.add_argument(
        "technical",
        type=Path,
        help="Candidate CSV or candidate output folder",
    )
    parser.add_argument(
        "broker",
        type=Path,
        help="Broker Summary CSV or broker input folder",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("data/input/FINAL_DECISION_V2.csv"),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--data-quality-status", default="VALID")
    parser.add_argument("--min-coverage", type=float, default=0.80)
    parser.add_argument("--expected-broker-date", default="")
    parser.add_argument("--allow-partial-broker", action="store_true")
    parser.add_argument("--allow-date-mismatch", action="store_true")
    parser.add_argument(
        "--broker-raw",
        type=Path,
        default=None,
        help="Stockbit BROKER_RAW CSV for foreign/domestic flow",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
        help="Run-scoped immutable Broker Fusion evidence root",
    )
    parser.add_argument(
        "--lock-root",
        type=Path,
        default=DEFAULT_LOCK_ROOT,
        help="Artifact writer lock root",
    )
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()

    args.output = _resolve(args.output)
    args.manifest_dir = (
        _resolve(args.manifest_dir) if args.manifest_dir is not None else None
    )
    args.broker_raw = (
        _resolve(args.broker_raw) if args.broker_raw is not None else None
    )
    if not args.technical.is_absolute():
        args.technical = _resolve(args.technical)
    if not args.broker.is_absolute():
        args.broker = _resolve(args.broker)

    lock_path = _resolve(args.lock_root) / "FINAL_DECISION_V2.writer.lock"
    with ArtifactWriteLock(lock_path, run_id=args.run_id):
        result, run_scoped_output, base_manifest = _run_fusion(args)
        manifest = publish_run_scoped_artifact(
            run_scoped_output=run_scoped_output,
            canonical_output=args.output,
            base_manifest=base_manifest,
            run_id=args.run_id,
            manifest_dir=args.manifest_dir,
            lock_path=lock_path,
        )

    print(f"TECHNICAL : {base_manifest.get('technical_source', args.technical)}")
    print(f"BROKER    : {base_manifest.get('broker_source', args.broker)}")
    print(f"MATCHED   : {int(result['Broker_Data_Available'].sum())}/{len(result)}")
    print(f"RUN COPY  : {manifest['run_scoped_output']}")
    print(f"OUTPUT    : {args.output}")
    print(f"SHA256    : {manifest['output_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
