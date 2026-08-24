#!/usr/bin/env python3
from __future__ import annotations

"""Final Watchlist orchestration for the canonical broker-period contract.

Production broker analysis has exactly two sources:

* PRIMARY: the exact Stockbit period selected by the operator (1D/3D/5D/CUSTOM).
* TODAY: an exact real-1D capture for the Final Watchlist date, only when
  PRIMARY is longer than 1D.

No rolling broker-history database is used to reconstruct a second 3D/5D
opinion.  Broker Fusion keeps its existing scoring semantics and the Final
Decision Engine remains the sole owner of trading decisions.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.broker_bridge.broker_period_context import (
    BrokerPeriodSpec,
    atomic_copy,
    custom_period_spec,
    expected_symbols_from_csv,
    fixed_period_spec,
    list_reusable_snapshots,
    persist_snapshot,
    raw_companion,
    wait_for_matching_export,
)
from swing_utils import file_sha256, make_run_id

WIB = ZoneInfo("Asia/Jakarta")


def resolve_project(value: str | Path) -> Path:
    path = Path(os.path.expandvars(str(value))).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def atomic_mask(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".masked_tmp")
    temp.write_bytes(b"")
    os.replace(temp, path)


class BridgeRunLock:
    """Prevent a second wrapper from recovering a transaction still in flight."""

    def __init__(self, recovery_root: Path) -> None:
        self.root = recovery_root
        self.path = recovery_root / ".bridge_run.lock"
        self.acquired = False

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if os.name == "nt":
            import ctypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    pid = int(self.path.read_text(encoding="utf-8").strip())
                except (OSError, ValueError) as exc:
                    raise RuntimeError("BROKER_BRIDGE_LOCK_UNREADABLE") from exc
                if pid > 0 and self._pid_is_alive(pid):
                    raise RuntimeError(f"BROKER_BRIDGE_ALREADY_RUNNING:{pid}")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
            self.acquired = True
            return
        raise RuntimeError("BROKER_BRIDGE_LOCK_ACQUIRE_FAILED")

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            owner = self.path.read_text(encoding="utf-8").strip()
            if owner == str(os.getpid()):
                self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self.acquired = False


class CanonicalTransaction:
    """Crash-recoverable transaction around mutable canonical broker inputs."""

    def __init__(self, recovery_root: Path, run_id: str, paths: dict[str, Path]) -> None:
        self.run_id = run_id
        self.root = recovery_root / run_id
        self.manifest_path = self.root / "transaction.json"
        self.paths = paths
        self.entries: dict[str, dict[str, Any]] = {}
        self.state = "NEW"

    def begin(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for label, canonical in self.paths.items():
            existed = canonical.exists()
            backup = self.root / f"{label}.backup"
            if existed:
                shutil.copy2(canonical, backup)
            self.entries[label] = {
                "canonical": str(canonical.resolve()),
                "original_existed": existed,
                "backup": str(backup.resolve()) if existed else "",
            }
        self.state = "ACTIVE"
        self._write()

    def _write(self) -> None:
        atomic_write_json(
            self.manifest_path,
            {
                "run_id": self.run_id,
                "state": self.state,
                "updated_at": datetime.now(WIB).isoformat(timespec="seconds"),
                "entries": self.entries,
            },
        )

    def activate(self, label: str, source: Path) -> None:
        atomic_copy(source, Path(self.entries[label]["canonical"]))

    def mask(self, label: str) -> None:
        atomic_mask(Path(self.entries[label]["canonical"]))

    def restore_one(self, label: str) -> None:
        entry = self.entries[label]
        canonical = Path(entry["canonical"])
        backup_text = str(entry.get("backup", ""))
        if entry.get("original_existed") and backup_text and Path(backup_text).exists():
            atomic_copy(Path(backup_text), canonical)
        elif canonical.exists():
            canonical.unlink()

    def rollback(self) -> None:
        if self.state != "ACTIVE":
            return
        for label in self.entries:
            self.restore_one(label)
        self.state = "ROLLED_BACK"
        self._write()

    def commit(self) -> None:
        self.state = "COMMITTED"
        self._write()


def recover_unfinished_transactions(recovery_root: Path) -> int:
    if not recovery_root.exists():
        return 0
    recovered = 0
    for manifest_path in sorted(recovery_root.glob("*/transaction.json")):
        payload = read_json(manifest_path)
        if str(payload.get("state", "")).upper() != "ACTIVE":
            continue
        entries = payload.get("entries", {}) if isinstance(payload.get("entries"), dict) else {}
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            canonical_text = str(entry.get("canonical", "")).strip()
            if not canonical_text:
                continue
            canonical = Path(canonical_text)
            backup_text = str(entry.get("backup", "")).strip()
            if entry.get("original_existed") and backup_text and Path(backup_text).exists():
                atomic_copy(Path(backup_text), canonical)
            elif canonical.exists():
                canonical.unlink()
        payload["state"] = "AUTO_RECOVERED"
        payload["recovered_at"] = datetime.now(WIB).isoformat(timespec="seconds")
        atomic_write_json(manifest_path, payload)
        recovered += 1
    return recovered


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Final Watchlist with exact PRIMARY broker period + optional TODAY 1D pulse")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--period", choices=["1D", "3D", "5D", "CUSTOM", "REUSE"], default="")
    parser.add_argument("--custom-start", default="")
    parser.add_argument("--timeout", type=int, default=-1)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def choose_period(requested: str, custom_start: str) -> tuple[str, str]:
    if requested:
        if requested == "CUSTOM" and not custom_start:
            custom_start = input("Tanggal awal CUSTOM [YYYY-MM-DD]: ").strip()
        return requested, custom_start
    print("\nBROKER ANALYSIS PERIOD")
    print("[1] 1D     - 1 sesi perdagangan IDX")
    print("[2] 3D     - 3 sesi perdagangan IDX")
    print("[3] 5D     - 5 sesi perdagangan IDX")
    print("[4] CUSTOM - tanggal awal sampai tanggal Final Watchlist")
    print("[5] REUSE  - snapshot exact yang valid untuk tanggal ini")
    choice = input("Pilih periode: ").strip()
    period = {"1": "1D", "2": "3D", "3": "5D", "4": "CUSTOM", "5": "REUSE"}.get(choice, "")
    if not period:
        raise RuntimeError("BROKER_PERIOD_SELECTION_INVALID")
    if period == "CUSTOM":
        custom_start = input("Tanggal awal CUSTOM [YYYY-MM-DD]: ").strip()
    return period, custom_start


def _is_exact_snapshot(item: dict[str, Any]) -> bool:
    source = str(item.get("broker_period_source") or "").strip().upper()
    return source in {"STOCKBIT_1D", "STOCKBIT_AGGREGATE_EXPORT", ""}


def select_reuse(trade_date: str) -> dict[str, Any]:
    snapshots = [item for item in list_reusable_snapshots(trade_date) if _is_exact_snapshot(item)]
    if not snapshots:
        raise RuntimeError(f"BROKER_REUSE_SNAPSHOT_NOT_FOUND:{trade_date}")
    print("\nSNAPSHOT EXACT VALID TERSEDIA")
    for idx, item in enumerate(snapshots, 1):
        print(
            f"[{idx}] {item.get('broker_period_type')} "
            f"{item.get('broker_period_start')}..{item.get('broker_period_end')} | "
            f"coverage {float(item.get('coverage_ratio', 0.0) or 0.0):.0%} | "
            f"{item.get('snapshot_id')}"
        )
    try:
        return snapshots[int(input("Pilih snapshot: ").strip()) - 1]
    except Exception as exc:
        raise RuntimeError("BROKER_REUSE_SELECTION_INVALID") from exc


def spec_from_manifest(manifest: dict[str, Any]) -> BrokerPeriodSpec:
    return BrokerPeriodSpec(
        period_type=str(manifest.get("broker_period_type", "")).upper(),
        period_start=str(manifest.get("broker_period_start", "")),
        period_end=str(manifest.get("broker_period_end", "")),
        trading_sessions=int(manifest.get("broker_trading_days", 0) or 0),
        session_dates=tuple(str(item) for item in manifest.get("broker_session_dates", []) or []),
    )


def resolve_snapshot_and_symbols(config: dict[str, Any], trade_date: str) -> tuple[dict[str, Any], Path, list[str]]:
    snapshot_path = PROJECT_ROOT / "data/output/snapshots" / trade_date / "latest_snapshot.json"
    snapshot = read_json(snapshot_path)
    if not snapshot:
        raise RuntimeError(f"TECHNICAL_SNAPSHOT_NOT_FOUND:{snapshot_path}")
    if str(snapshot.get("trade_date", "")) != trade_date:
        raise RuntimeError(f"TECHNICAL_SNAPSHOT_DATE_MISMATCH:{snapshot.get('trade_date')}!={trade_date}")

    navigator_text = str(snapshot.get("broker_navigator_path") or "").strip()
    navigator = Path(navigator_text) if navigator_text else resolve_project(
        config.get("paths", {}).get("broker_navigator_symbols", "data/output/candidates/BROKER_NAVIGATOR_SYMBOLS.csv")
    )
    if not navigator.is_absolute():
        navigator = PROJECT_ROOT / navigator
    if not navigator.exists():
        fallback_text = str(snapshot.get("output_paths", {}).get("broker_symbols", "")).strip()
        fallback = Path(fallback_text) if fallback_text else Path()
        if fallback_text and fallback.exists():
            navigator = fallback
    if not navigator.exists():
        raise RuntimeError(f"BROKER_NAVIGATOR_SYMBOLS_NOT_FOUND:{navigator}")
    expected = expected_symbols_from_csv(navigator)
    if not expected:
        raise RuntimeError("BROKER_EXPECTED_SYMBOLS_EMPTY")
    return snapshot, navigator, expected


def run_stage(job: str, run_id: str, *, config_path: Path, scheduler_config: str, trade_date: str, no_telegram: bool, debug: bool) -> int:
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "run_sde_job.py"),
        "--job",
        job,
        "--config",
        str(config_path),
        "--scheduler-config",
        scheduler_config,
        "--trade-date",
        trade_date,
        "--run-id",
        run_id,
    ]
    if no_telegram:
        command.append("--no-telegram")
    if debug:
        command.append("--debug")
    print(f"\n[STAGE] {job}", flush=True)
    return int(subprocess.run(command, cwd=PROJECT_ROOT).returncode)


def active_sidecar_payload(
    manifest: dict[str, Any],
    run_id: str,
    *,
    daily_manifest: dict[str, Any] | None = None,
    daily_archive_path: Path | None = None,
) -> dict[str, Any]:
    """Build the one canonical PRIMARY/TODAY lineage sidecar."""
    daily_manifest = dict(daily_manifest or {})
    period_type = str(manifest.get("broker_period_type") or "").upper()
    separate_today = period_type not in {"", "1D", "1DAY", "DAY"}
    daily_summary = str(daily_manifest.get("summary_snapshot_path") or "").strip()
    daily_raw = str(daily_manifest.get("raw_snapshot_path") or "").strip()
    daily_available = bool(
        separate_today
        and daily_summary
        and Path(daily_summary).exists()
        and daily_raw
        and Path(daily_raw).exists()
    )
    coverage = manifest.get("broker_coverage", manifest.get("coverage_ratio", 0.0))
    missing_sessions = list(manifest.get("broker_missing_sessions") or manifest.get("broker_missing_session_dates") or [])
    return {
        "Run_ID": run_id,
        "snapshot_id": manifest.get("snapshot_id"),
        "broker_snapshot_id": manifest.get("broker_snapshot_id") or manifest.get("snapshot_id"),
        "broker_period_type": period_type,
        "broker_period_start": manifest.get("broker_period_start"),
        "broker_period_end": manifest.get("broker_period_end"),
        "broker_trading_days": manifest.get("broker_trading_days"),
        "broker_session_dates": manifest.get("broker_session_dates", []),
        "freshness_status": manifest.get("freshness_status") or "CURRENT",
        "broker_freshness_status": manifest.get("freshness_status") or "CURRENT",
        "broker_date": manifest.get("broker_period_end"),
        "from_date": manifest.get("broker_period_start"),
        "to_date": manifest.get("broker_period_end"),
        "coverage": manifest.get("coverage_ratio", 0.0),
        "broker_coverage": coverage,
        "matched": manifest.get("matched_symbols", 0),
        "expected": manifest.get("expected_symbols", 0),
        "source_hash": manifest.get("summary_snapshot_hash"),
        "summary_source_hash": manifest.get("summary_source_hash") or manifest.get("summary_snapshot_hash"),
        "summary_hash": manifest.get("summary_hash") or manifest.get("summary_snapshot_hash"),
        "raw_source_hash": manifest.get("raw_source_hash", ""),
        "raw_hash": manifest.get("raw_hash") or manifest.get("raw_source_hash", ""),
        "broker_period_source": str(manifest.get("broker_period_source") or "").upper(),
        "broker_missing_sessions": missing_sessions,
        "broker_period_complete": bool(manifest.get("broker_period_complete", not missing_sessions)),
        "broker_period_coverage": manifest.get("broker_period_coverage", manifest.get("broker_session_coverage", 1.0 if not missing_sessions else 0.0)),
        "aggregate_snapshot": bool(manifest.get("aggregate_snapshot", period_type not in {"1D", "1DAY", "DAY"})),
        "primary_summary_snapshot_path": manifest.get("summary_snapshot_path", ""),
        "primary_raw_snapshot_path": manifest.get("raw_snapshot_path", ""),
        "daily_capture_snapshot_id": daily_manifest.get("snapshot_id", "") if separate_today else "",
        "daily_capture_manifest_path": daily_manifest.get("manifest_path", "") if separate_today else "",
        "daily_capture_summary_snapshot_path": daily_summary if separate_today else "",
        "daily_capture_raw_snapshot_path": daily_raw if separate_today else "",
        "daily_archive_path": str(daily_archive_path.resolve()) if daily_archive_path and separate_today else "",
        "today_pulse_available": daily_available,
        "today_pulse_date": str(manifest.get("broker_period_end", ""))[:10] if daily_available else "",
        "today_pulse_snapshot_id": daily_manifest.get("snapshot_id", "") if daily_available else "",
        "today_pulse_source": "STOCKBIT_1D" if daily_available else "",
        "today_pulse_status": "AVAILABLE" if daily_available else ("NOT_APPLICABLE" if not separate_today else "NOT_AVAILABLE"),
        "BROKER_PERIOD_PRIMARY": True,
        "BROKER_PERIOD_CONTEXT_MODEL": "EXACT_PRIMARY_PLUS_TODAY_V1",
        "SCORING_ADJUSTMENT_APPLIED": False,
        "FRESHNESS_ADJUSTMENT_APPLIED": False,
        "PERSISTENCE_ADJUSTMENT_APPLIED": False,
    }


def archive_daily_raw(raw_path: Path, archive_dir: Path, trade_date: str) -> Path | None:
    """Keep the real 1D raw capture for audit only; it is not an analysis DB."""
    if not raw_path.exists() or raw_path.stat().st_size <= 0:
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    digest = file_sha256(raw_path)
    destination = archive_dir / f"BROKER_RAW_{trade_date}_{digest[:16]}.csv"
    if destination.exists() and file_sha256(destination) == digest:
        return destination
    if destination.exists():
        destination = archive_dir / f"BROKER_RAW_{trade_date}_{digest}.csv"
    shutil.copy2(raw_path, destination)
    return destination


def _manifest_from_path(path_text: str) -> dict[str, Any]:
    path = Path(str(path_text or "").strip()) if str(path_text or "").strip() else None
    return read_json(path) if path else {}


def capture_real_daily_snapshot(
    *,
    downloads: Path,
    expected_symbols: list[str],
    min_coverage: float,
    trade_date: str,
    timeout_seconds: int,
    poll_seconds: float,
    archive_dir: Path,
    snapshot_root: Path,
) -> tuple[dict[str, Any], Path | None]:
    """Capture one exact real-1D Stockbit snapshot for the current session."""
    daily_spec = fixed_period_spec("1D", trade_date)
    print(
        f"\n[TODAY 1D] Export Broker Summary PERSIS {daily_spec.period_start}..{daily_spec.period_end}.",
        flush=True,
    )
    export_path, info = wait_for_matching_export(
        downloads,
        expected_symbols,
        min_coverage,
        daily_spec,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    raw_path = raw_companion(export_path)
    manifest = persist_snapshot(
        export_path,
        raw_path,
        daily_spec,
        info,
        snapshot_root=snapshot_root,
        selected_by="TODAY_1D_CAPTURE",
    )
    archived = (
        archive_daily_raw(Path(str(manifest["raw_snapshot_path"])), archive_dir, trade_date)
        if manifest.get("raw_snapshot_path")
        else None
    )
    manifest.update({
        "daily_history_eligible": True,
        "aggregate_snapshot": False,
        "broker_period_source": "STOCKBIT_1D",
        "broker_period_complete": True,
        "broker_missing_sessions": [],
        "broker_session_coverage": 1.0,
        "broker_coverage_text": "1/1",
        "analysis_role": "TODAY" if daily_spec.period_type == "1D" else "",
    })
    manifest_path_text = str(manifest.get("manifest_path", "")).strip()
    if manifest_path_text:
        atomic_write_json(Path(manifest_path_text), manifest)
    return manifest, archived


def capture_exact_aggregate_primary(
    *,
    downloads: Path,
    expected_symbols: list[str],
    min_coverage: float,
    spec: BrokerPeriodSpec,
    timeout_seconds: int,
    poll_seconds: float,
    snapshot_root: Path,
) -> dict[str, Any]:
    """Capture the exact operator-selected aggregate used by Broker Fusion."""
    print(
        f"\n[PRIMARY {spec.period_type}] Export range PERSIS {spec.period_start}..{spec.period_end}.",
        flush=True,
    )
    export_path, info = wait_for_matching_export(
        downloads,
        expected_symbols,
        min_coverage,
        spec,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    manifest = persist_snapshot(
        export_path,
        raw_companion(export_path),
        spec,
        info,
        snapshot_root=snapshot_root,
        selected_by="FINAL_WATCHLIST_EXACT_PRIMARY",
    )
    manifest.update({
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT",
        "aggregate_snapshot": True,
        "daily_history_eligible": False,
        "analysis_role": "PRIMARY",
    })
    manifest_path_text = str(manifest.get("manifest_path", "")).strip()
    if manifest_path_text:
        atomic_write_json(Path(manifest_path_text), manifest)
    return manifest


def _find_reusable_today(trade_date: str) -> dict[str, Any]:
    for item in list_reusable_snapshots(trade_date):
        if str(item.get("broker_period_type") or "").upper() != "1D":
            continue
        if str(item.get("broker_period_end") or "")[:10] != trade_date:
            continue
        if not _is_exact_snapshot(item):
            continue
        return item
    return {}


def mark_manifest_committed(manifest: dict[str, Any], run_id: str) -> Path | None:
    manifest_path_text = str(manifest.get("manifest_path", "")).strip()
    if not manifest_path_text:
        return None
    manifest_path = Path(manifest_path_text)
    if not manifest_path.exists():
        return None
    now = datetime.now(WIB).isoformat(timespec="seconds")
    manifest.update({
        "snapshot_state": "COMMITTED",
        "final_watchlist_run_id": run_id,
        "activated_at": now,
        "committed_at": now,
        "primary_context": str(manifest.get("analysis_role") or "").upper() == "PRIMARY",
    })
    atomic_write_json(manifest_path, manifest)
    return manifest_path


def capture_performance(run_id: str, manifest_path: Path, config: dict[str, Any]) -> None:
    db = resolve_project(config.get("paths", {}).get("swing_database", "data/database/sde_swing_history.db"))
    completed = subprocess.run(
        [
            sys.executable,
            "-u",
            str(PROJECT_ROOT / "tools/broker_period_performance.py"),
            "capture",
            "--db",
            str(db),
            "--run-id",
            run_id,
            "--snapshot-manifest",
            str(manifest_path),
        ],
        cwd=PROJECT_ROOT,
    )
    if completed.returncode != 0:
        print("[PERFORMANCE WARNING] metadata Broker Period belum tercatat; Final Watchlist tetap valid.", flush=True)


def main() -> int:
    args = parse_args()
    config_path = resolve_project(args.config)
    config = read_json(config_path)
    if not config:
        raise RuntimeError(f"PIPELINE_CONFIG_INVALID:{config_path}")

    trade_date = args.trade_date or datetime.now(WIB).date().isoformat()
    snapshot, navigator, expected_symbols = resolve_snapshot_and_symbols(config, trade_date)
    period_choice, custom_start = choose_period(args.period, args.custom_start)

    paths = config.get("paths", {})
    broker_cfg = config.get("broker", {})
    downloads = resolve_project(broker_cfg.get("downloads_dir", "%USERPROFILE%/Downloads"))
    raw_archive = resolve_project(paths.get("broker_raw_archive_dir", "data/input/broker/archive"))
    snapshot_root = resolve_project("data/output/broker_snapshots")
    timeout = args.timeout if args.timeout >= 0 else int(broker_cfg.get("timeout_seconds", 1200))
    poll = float(broker_cfg.get("poll_seconds", 2.0))
    min_coverage = float(broker_cfg.get("min_coverage", 0.8))

    if not downloads.exists() and period_choice != "REUSE":
        raise RuntimeError(f"BROKER_DOWNLOADS_NOT_FOUND:{downloads}")

    daily_manifest: dict[str, Any] = {}
    daily_archive_path: Path | None = None

    if period_choice == "REUSE":
        selected_manifest = select_reuse(trade_date)
        spec = spec_from_manifest(selected_manifest)
        if spec.period_type not in {"1D", "1DAY", "DAY"}:
            daily_manifest = _manifest_from_path(
                str(selected_manifest.get("daily_capture_manifest_path") or "")
            )
            if not daily_manifest:
                daily_manifest = _find_reusable_today(trade_date)
            if not daily_manifest:
                raise RuntimeError(f"BROKER_TODAY_PULSE_REQUIRED_FOR_REUSE:{trade_date}")
    else:
        requested_spec = (
            custom_period_spec(custom_start, trade_date)
            if period_choice == "CUSTOM"
            else fixed_period_spec(period_choice, trade_date)
        )
        print("\n" + "=" * 68)
        print("BROKER BRIDGE — EXACT PRIMARY + TODAY")
        print("=" * 68)
        print(f"Technical date    : {snapshot.get('trade_date')}")
        print(f"Requested PRIMARY : {requested_spec.period_type}")
        print(f"Range             : {requested_spec.period_start} s/d {requested_spec.period_end}")
        print(f"IDX sessions      : {requested_spec.trading_sessions}")
        print(f"Symbols           : {len(expected_symbols)}")
        print(f"Navigator         : {navigator}")
        print("Rolling broker multi-day DB: DISABLED FOR PRODUCTION ANALYSIS")
        print("=" * 68, flush=True)

        if period_choice == "1D":
            selected_manifest, daily_archive_path = capture_real_daily_snapshot(
                downloads=downloads,
                expected_symbols=expected_symbols,
                min_coverage=min_coverage,
                trade_date=trade_date,
                timeout_seconds=timeout,
                poll_seconds=poll,
                archive_dir=raw_archive,
                snapshot_root=snapshot_root,
            )
            selected_manifest["analysis_role"] = "PRIMARY"
            spec = requested_spec
        else:
            daily_manifest, daily_archive_path = capture_real_daily_snapshot(
                downloads=downloads,
                expected_symbols=expected_symbols,
                min_coverage=min_coverage,
                trade_date=trade_date,
                timeout_seconds=timeout,
                poll_seconds=poll,
                archive_dir=raw_archive,
                snapshot_root=snapshot_root,
            )
            selected_manifest = capture_exact_aggregate_primary(
                downloads=downloads,
                expected_symbols=expected_symbols,
                min_coverage=min_coverage,
                spec=requested_spec,
                timeout_seconds=timeout,
                poll_seconds=poll,
                snapshot_root=snapshot_root,
            )
            spec = requested_spec

    summary_snapshot = Path(str(selected_manifest.get("summary_snapshot_path") or ""))
    raw_text = str(selected_manifest.get("raw_snapshot_path") or "").strip()
    raw_snapshot = Path(raw_text) if raw_text else None
    if spec.period_end != trade_date:
        raise RuntimeError(f"BROKER_PRIMARY_CONTEXT_NOT_CURRENT:{spec.period_end}!={trade_date}")
    if not summary_snapshot.exists():
        raise RuntimeError(f"BROKER_SNAPSHOT_SUMMARY_MISSING:{summary_snapshot}")
    if raw_snapshot is None or not raw_snapshot.exists() or raw_snapshot.stat().st_size <= 0:
        raise RuntimeError("BROKER_PRIMARY_RAW_MISSING")

    if spec.period_type not in {"1D", "1DAY", "DAY"}:
        daily_summary = Path(str(daily_manifest.get("summary_snapshot_path") or ""))
        daily_raw = Path(str(daily_manifest.get("raw_snapshot_path") or ""))
        if not daily_summary.exists() or not daily_raw.exists():
            raise RuntimeError(f"BROKER_TODAY_PULSE_REQUIRED:{trade_date}")

    canonical_summary = resolve_project(paths.get("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv"))
    canonical_sidecar = canonical_summary.with_suffix(".manifest.json")
    canonical_raw = resolve_project(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))
    recovery_root = PROJECT_ROOT / "data/output/broker_snapshots/recovery"

    run_lock = BridgeRunLock(recovery_root)
    run_lock.acquire()
    try:
        recovered = recover_unfinished_transactions(recovery_root)
        if recovered:
            print(f"[RECOVERY] {recovered} transaksi Broker Bridge lama dipulihkan sebelum run baru.", flush=True)
        run_id = make_run_id()
        transaction = CanonicalTransaction(
            recovery_root,
            run_id,
            {"summary": canonical_summary, "sidecar": canonical_sidecar, "raw": canonical_raw},
        )
        transaction.begin()
    except Exception:
        run_lock.release()
        raise

    committed = False
    try:
        transaction.activate("summary", summary_snapshot)
        transaction.activate("raw", raw_snapshot)
        atomic_write_json(
            canonical_sidecar,
            active_sidecar_payload(
                selected_manifest,
                run_id,
                daily_manifest=daily_manifest,
                daily_archive_path=daily_archive_path,
            ),
        )

        rc = run_stage(
            "broker_summary",
            run_id,
            config_path=config_path,
            scheduler_config=args.scheduler_config,
            trade_date=trade_date,
            no_telegram=True,
            debug=args.debug,
        )
        if rc != 0:
            return rc

        # No legacy database-derived broker stage. Final Watchlist consumes Broker Fusion's
        # exact PRIMARY result; TODAY remains presentation context only.
        rc = run_stage(
            "final_watchlist",
            run_id,
            config_path=config_path,
            scheduler_config=args.scheduler_config,
            trade_date=trade_date,
            no_telegram=args.no_telegram,
            debug=args.debug,
        )
        if rc != 0:
            return rc

        transaction.commit()
        committed = True

        try:
            if daily_manifest and spec.period_type not in {"1D", "1DAY", "DAY"}:
                selected_manifest["daily_capture_manifest_path"] = daily_manifest.get("manifest_path", "")
                selected_manifest["daily_capture_snapshot_id"] = daily_manifest.get("snapshot_id", "")
                selected_manifest["daily_capture_summary_snapshot_path"] = daily_manifest.get("summary_snapshot_path", "")
                selected_manifest["daily_capture_raw_snapshot_path"] = daily_manifest.get("raw_snapshot_path", "")
                selected_manifest["daily_archive_path"] = str(daily_archive_path.resolve()) if daily_archive_path else ""
            manifest_path = mark_manifest_committed(selected_manifest, run_id)
            if daily_manifest and daily_manifest.get("snapshot_id") != selected_manifest.get("snapshot_id"):
                daily_manifest["daily_archive_path"] = str(daily_archive_path.resolve()) if daily_archive_path else ""
                mark_manifest_committed(daily_manifest, run_id)
            latest_selected = PROJECT_ROOT / "data/output/broker_snapshots/latest_selected.json"
            atomic_write_json(latest_selected, selected_manifest)
            if manifest_path and manifest_path.exists():
                capture_performance(run_id, manifest_path, config)
            else:
                print("[PERFORMANCE WARNING] immutable snapshot manifest tidak ditemukan.", flush=True)
        except Exception as exc:
            print(f"[AUDIT WARNING] {type(exc).__name__}: {exc}", flush=True)

        print("\n" + "=" * 68)
        print("FINAL WATCHLIST — BROKER PERIOD COMPLETE")
        print("=" * 68)
        print(f"Run ID           : {run_id}")
        print(f"PRIMARY          : {spec.period_type} {spec.period_start} s/d {spec.period_end}")
        print(f"TODAY pulse      : {'1D exact ' + trade_date if spec.period_type != '1D' else 'NOT APPLICABLE (PRIMARY sudah 1D)'}")
        print(f"Snapshot ID      : {selected_manifest.get('snapshot_id')}")
        print("Broker scoring   : EXISTING BROKER FUSION FORMULA (tidak diubah)")
        print("Rolling multi-day: REMOVED FROM PRODUCTION ANALYSIS")
        print("Telegram         : Final Watchlist + Lifecycle")
        print("=" * 68)
        return 0
    finally:
        try:
            if not committed:
                transaction.rollback()
        finally:
            run_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
