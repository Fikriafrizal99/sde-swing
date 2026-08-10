#!/usr/bin/env python3
from __future__ import annotations

"""Final Watchlist orchestration with explicit Broker Summary horizon.

The existing Broker Fusion / Broker Confidence / Decision / Entry / Exit
engines remain untouched. This wrapper owns only input selection, immutable
snapshotting, lineage, transactional canonical activation, and stage order.
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

from swing_utils import make_run_id
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
            # ``os.kill(pid, 0)`` is not a non-destructive liveness probe on
            # every supported Windows Python/runtime combination. Querying a
            # limited process handle is read-only and avoids signalling the
            # current process.
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
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
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
    """Restore canonical files left by a killed/interrupted prior process."""
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
    parser = argparse.ArgumentParser(description="Final Watchlist with 1D/3D/5D/CUSTOM Broker Summary horizon")
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
    print("[5] REUSE  - snapshot valid tersimpan untuk tanggal ini")
    choice = input("Pilih periode: ").strip()
    period = {"1": "1D", "2": "3D", "3": "5D", "4": "CUSTOM", "5": "REUSE"}.get(choice, "")
    if not period:
        raise RuntimeError("BROKER_PERIOD_SELECTION_INVALID")
    if period == "CUSTOM":
        custom_start = input("Tanggal awal CUSTOM [YYYY-MM-DD]: ").strip()
    return period, custom_start


def select_reuse(trade_date: str) -> dict[str, Any]:
    snapshots = list_reusable_snapshots(trade_date)
    if not snapshots:
        raise RuntimeError(f"BROKER_REUSE_SNAPSHOT_NOT_FOUND:{trade_date}")
    print("\nSNAPSHOT VALID TERSEDIA")
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
        sys.executable, "-u", str(PROJECT_ROOT / "run_sde_job.py"),
        "--job", job,
        "--config", str(config_path),
        "--scheduler-config", scheduler_config,
        "--trade-date", trade_date,
        "--run-id", run_id,
    ]
    if no_telegram:
        command.append("--no-telegram")
    if debug:
        command.append("--debug")
    print(f"\n[STAGE] {job}", flush=True)
    return int(subprocess.run(command, cwd=PROJECT_ROOT).returncode)


def active_sidecar_payload(manifest: dict[str, Any], run_id: str) -> dict[str, Any]:
    return {
        "Run_ID": run_id,
        "snapshot_id": manifest.get("snapshot_id"),
        "broker_period_type": manifest.get("broker_period_type"),
        "broker_period_start": manifest.get("broker_period_start"),
        "broker_period_end": manifest.get("broker_period_end"),
        "broker_trading_days": manifest.get("broker_trading_days"),
        "broker_session_dates": manifest.get("broker_session_dates", []),
        "freshness_status": manifest.get("freshness_status") or "CURRENT",
        "broker_date": manifest.get("broker_period_end"),
        "from_date": manifest.get("broker_period_start"),
        "to_date": manifest.get("broker_period_end"),
        "coverage": manifest.get("coverage_ratio", 0.0),
        "matched": manifest.get("matched_symbols", 0),
        "expected": manifest.get("expected_symbols", 0),
        "source_hash": manifest.get("summary_snapshot_hash"),
        "BROKER_PERIOD_PRIMARY": True,
        "SCORING_ADJUSTMENT_APPLIED": False,
        "FRESHNESS_ADJUSTMENT_APPLIED": False,
        "PERSISTENCE_ADJUSTMENT_APPLIED": False,
    }


def archive_daily_raw(raw_path: Path, archive_dir: Path, trade_date: str) -> Path | None:
    if not raw_path.exists() or raw_path.stat().st_size <= 0:
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / f"BROKER_RAW_{trade_date}_{datetime.now(WIB):%H%M%S%f}.csv"
    shutil.copy2(raw_path, destination)
    return destination


def capture_performance(run_id: str, manifest_path: Path, config: dict[str, Any]) -> None:
    db = resolve_project(config.get("paths", {}).get("swing_database", "data/database/sde_swing_history.db"))
    command = [
        sys.executable, "-u", str(PROJECT_ROOT / "tools/broker_period_performance.py"),
        "capture", "--db", str(db), "--run-id", run_id,
        "--snapshot-manifest", str(manifest_path),
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
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

    if period_choice == "REUSE":
        selected_manifest = select_reuse(trade_date)
        spec = spec_from_manifest(selected_manifest)
        summary_snapshot = Path(str(selected_manifest.get("summary_snapshot_path", "")))
        raw_text = str(selected_manifest.get("raw_snapshot_path", "")).strip()
        raw_snapshot = Path(raw_text) if raw_text else None
    else:
        spec = custom_period_spec(custom_start, trade_date) if period_choice == "CUSTOM" else fixed_period_spec(period_choice, trade_date)
        broker_cfg = config.get("broker", {})
        downloads = resolve_project(broker_cfg.get("downloads_dir", "%USERPROFILE%/Downloads"))
        if not downloads.exists():
            raise RuntimeError(f"BROKER_DOWNLOADS_NOT_FOUND:{downloads}")
        timeout = args.timeout if args.timeout >= 0 else int(broker_cfg.get("timeout_seconds", 1200))
        poll = float(broker_cfg.get("poll_seconds", 2.0))
        min_coverage = float(broker_cfg.get("min_coverage", 0.8))
        print("\n" + "=" * 68)
        print("BROKER BRIDGE - FINAL WATCHLIST")
        print("=" * 68)
        print(f"Technical date : {snapshot.get('trade_date')}")
        print(f"Period         : {spec.period_type}")
        print(f"Range          : {spec.period_start} s/d {spec.period_end}")
        print(f"IDX sessions   : {spec.trading_sessions}")
        print(f"Session dates  : {', '.join(spec.session_dates)}")
        print(f"Symbols        : {len(expected_symbols)}")
        print(f"Navigator      : {navigator}")
        print("Export Broker Summary Stockbit dengan range PERSIS di atas.")
        print("SDE hanya menerima export dengan range + coverage yang valid.")
        print("=" * 68, flush=True)
        export_path, info = wait_for_matching_export(
            downloads, expected_symbols, min_coverage, spec,
            timeout_seconds=timeout, poll_seconds=poll,
        )
        selected_manifest = persist_snapshot(export_path, raw_companion(export_path), spec, info)
        summary_snapshot = Path(str(selected_manifest["summary_snapshot_path"]))
        raw_text = str(selected_manifest.get("raw_snapshot_path", "")).strip()
        raw_snapshot = Path(raw_text) if raw_text else None

    if spec.period_end != trade_date:
        raise RuntimeError(f"BROKER_PRIMARY_CONTEXT_NOT_CURRENT:{spec.period_end}!={trade_date}")
    if not summary_snapshot.exists():
        raise RuntimeError(f"BROKER_SNAPSHOT_SUMMARY_MISSING:{summary_snapshot}")

    paths = config.get("paths", {})
    canonical_summary = resolve_project(paths.get("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv"))
    canonical_sidecar = canonical_summary.with_suffix(".manifest.json")
    canonical_raw = resolve_project(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))
    raw_archive = resolve_project(paths.get("broker_raw_archive_dir", "data/input/broker/archive"))
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
    daily_archive_path: Path | None = None

    try:
        transaction.activate("summary", summary_snapshot)
        atomic_write_json(canonical_sidecar, active_sidecar_payload(selected_manifest, run_id))

        raw_is_available = bool(raw_snapshot and raw_snapshot.exists() and raw_snapshot.stat().st_size > 0)
        if raw_is_available:
            transaction.activate("raw", raw_snapshot)  # type: ignore[arg-type]
        else:
            # Stale raw data must never leak into the selected range's Fusion.
            transaction.mask("raw")

        rc = run_stage(
            "broker_summary", run_id, config_path=config_path,
            scheduler_config=args.scheduler_config, trade_date=trade_date,
            no_telegram=True, debug=args.debug,
        )
        if rc != 0:
            return rc

        if spec.period_type == "1D" and raw_is_available:
            # Valid 1D raw is the only selected-period raw allowed to become a
            # daily Multi-Day observation. Keep it canonical until the stage
            # finishes, but do not commit the transaction yet.
            daily_archive_path = archive_daily_raw(raw_snapshot, raw_archive, trade_date)  # type: ignore[arg-type]
        else:
            # 3D/5D/CUSTOM aggregate raw must not masquerade as today's daily
            # observation. A missing 1D raw also means today's history is missing.
            transaction.restore_one("raw")

        rc = run_stage(
            "broker_multi_day", run_id, config_path=config_path,
            scheduler_config=args.scheduler_config, trade_date=trade_date,
            no_telegram=True, debug=args.debug,
        )
        if rc != 0:
            return rc

        rc = run_stage(
            "final_watchlist", run_id, config_path=config_path,
            scheduler_config=args.scheduler_config, trade_date=trade_date,
            no_telegram=args.no_telegram, debug=args.debug,
        )
        if rc != 0:
            return rc

        # Only after Final Watchlist succeeds may the new canonical state become
        # durable. Any earlier failure rolls every canonical input back.
        transaction.commit()
        committed = True

        try:
            selected_manifest["snapshot_state"] = "COMMITTED"
            selected_manifest["final_watchlist_run_id"] = run_id
            selected_manifest["activated_at"] = datetime.now(WIB).isoformat(timespec="seconds")
            selected_manifest["committed_at"] = selected_manifest["activated_at"]
            selected_manifest["primary_context"] = True
            selected_manifest["daily_archive_path"] = str(daily_archive_path.resolve()) if daily_archive_path else ""
            manifest_path = Path(str(selected_manifest.get("manifest_path", "")))
            if manifest_path.exists():
                atomic_write_json(manifest_path, selected_manifest)
            latest_selected = PROJECT_ROOT / "data/output/broker_snapshots/latest_selected.json"
            atomic_write_json(latest_selected, selected_manifest)
            if manifest_path.exists():
                capture_performance(run_id, manifest_path, config)
            else:
                print("[PERFORMANCE WARNING] immutable snapshot manifest tidak ditemukan.", flush=True)
        except Exception as exc:
            # Analytics/audit enrichment is non-destructive and must not turn a
            # successfully delivered Final Watchlist into a failed trading run.
            print(f"[AUDIT WARNING] {type(exc).__name__}: {exc}", flush=True)

        print("\n" + "=" * 68)
        print("FINAL WATCHLIST - BROKER PERIOD COMPLETE")
        print("=" * 68)
        print(f"Run ID          : {run_id}")
        print(f"Broker period   : {spec.period_type}")
        print(f"Range           : {spec.period_start} s/d {spec.period_end}")
        print(f"Trading sessions: {spec.trading_sessions}")
        print(f"Snapshot ID     : {selected_manifest.get('snapshot_id')}")
        print("Broker scoring  : EXISTING FORMULA (tidak diubah)")
        print("Persistence     : context only; tidak ada bonus/penalty baru")
        print("Telegram        : Final Watchlist + Lifecycle saja")
        print("=" * 68)
        return 0
    finally:
        try:
            if not committed:
                transaction.rollback()
                if daily_archive_path and daily_archive_path.exists():
                    daily_archive_path.unlink()
        finally:
            run_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
