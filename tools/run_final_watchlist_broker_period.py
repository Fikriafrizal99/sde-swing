#!/usr/bin/env python3
from __future__ import annotations

"""Final Watchlist orchestration with explicit Broker Summary horizon.

The existing Broker Fusion / Broker Confidence / Decision / Entry / Exit
engines remain untouched.  This wrapper only owns input selection, immutable
snapshotting, lineage, and stage order.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import file_sha256, make_run_id
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


class CanonicalGuard:
    """Transactional protection for a mutable canonical input file."""

    def __init__(self, canonical: Path, backup_root: Path, label: str) -> None:
        self.canonical = canonical
        self.backup_root = backup_root
        self.label = label
        self.backup: Path | None = None
        self.original_existed = False
        self.committed = False

    def activate(self, source: Path) -> None:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.original_existed = self.canonical.exists()
        if self.original_existed:
            stamp = datetime.now(WIB).strftime("%Y%m%d_%H%M%S_%f")
            self.backup = self.backup_root / f"{self.label}_PRE_{stamp}{self.canonical.suffix}"
            shutil.copy2(self.canonical, self.backup)
        atomic_copy(source, self.canonical)

    def mask(self) -> None:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        self.original_existed = self.canonical.exists()
        if self.original_existed:
            stamp = datetime.now(WIB).strftime("%Y%m%d_%H%M%S_%f")
            self.backup = self.backup_root / f"{self.label}_PRE_{stamp}{self.canonical.suffix}"
            shutil.copy2(self.canonical, self.backup)
        self.canonical.parent.mkdir(parents=True, exist_ok=True)
        self.canonical.write_bytes(b"")

    def restore(self) -> None:
        if self.committed:
            return
        if self.backup is not None and self.backup.exists():
            atomic_copy(self.backup, self.canonical)
        elif not self.original_existed and self.canonical.exists():
            self.canonical.unlink()

    def commit(self) -> None:
        self.committed = True


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Final Watchlist with 1D/3D/5D/CUSTOM Broker Summary horizon")
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--period", choices=["1D", "3D", "5D", "CUSTOM", "REUSE"], default="")
    parser.add_argument("--custom-start", default="")
    parser.add_argument("--timeout", type=int, default=-1)
    parser.add_argument("--no-telegram", action="store_true", help="Preview final output without Telegram delivery")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def choose_period(trade_date: str, requested: str, custom_start: str) -> tuple[str, str]:
    if requested:
        if requested == "CUSTOM" and not custom_start:
            custom_start = input("Tanggal awal CUSTOM [YYYY-MM-DD]: ").strip()
        return requested, custom_start
    print("\nBROKER ANALYSIS PERIOD")
    print("[1] 1D     - 1 sesi perdagangan IDX")
    print("[2] 3D     - 3 sesi perdagangan IDX")
    print("[3] 5D     - 5 sesi perdagangan IDX")
    print("[4] CUSTOM - tanggal awal sampai tanggal Final Watchlist")
    print("[5] REUSE  - snapshot valid yang sudah tersimpan untuk tanggal ini")
    choice = input("Pilih periode: ").strip()
    mapping = {"1": "1D", "2": "3D", "3": "5D", "4": "CUSTOM", "5": "REUSE"}
    period = mapping.get(choice, "")
    if not period:
        raise RuntimeError("BROKER_PERIOD_SELECTION_INVALID")
    if period == "CUSTOM":
        custom_start = input("Tanggal awal CUSTOM [YYYY-MM-DD]: ").strip()
    return period, custom_start


def select_reuse(trade_date: str) -> dict:
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
    raw = input("Pilih snapshot: ").strip()
    try:
        selected = snapshots[int(raw) - 1]
    except Exception as exc:
        raise RuntimeError("BROKER_REUSE_SELECTION_INVALID") from exc
    return selected


def spec_from_manifest(manifest: dict) -> BrokerPeriodSpec:
    return BrokerPeriodSpec(
        period_type=str(manifest.get("broker_period_type", "")).upper(),
        period_start=str(manifest.get("broker_period_start", "")),
        period_end=str(manifest.get("broker_period_end", "")),
        trading_sessions=int(manifest.get("broker_trading_days", 0) or 0),
        session_dates=tuple(str(item) for item in manifest.get("broker_session_dates", []) or []),
    )


def resolve_snapshot_and_symbols(config: dict, trade_date: str) -> tuple[dict, Path, list[str]]:
    snapshot_path = PROJECT_ROOT / "data/output/snapshots" / trade_date / "latest_snapshot.json"
    snapshot = read_json(snapshot_path)
    if not snapshot:
        raise RuntimeError(f"TECHNICAL_SNAPSHOT_NOT_FOUND:{snapshot_path}")
    if str(snapshot.get("trade_date", "")) != trade_date:
        raise RuntimeError(
            f"TECHNICAL_SNAPSHOT_DATE_MISMATCH:{snapshot.get('trade_date')}!={trade_date}"
        )
    navigator_text = str(snapshot.get("broker_navigator_path") or "").strip()
    if navigator_text:
        navigator = Path(navigator_text)
    else:
        navigator = PROJECT_ROOT / str(config.get("paths", {}).get("broker_navigator_symbols", "data/output/candidates/BROKER_NAVIGATOR_SYMBOLS.csv"))
    if not navigator.is_absolute():
        navigator = PROJECT_ROOT / navigator
    if not navigator.exists():
        fallback = Path(str(snapshot.get("output_paths", {}).get("broker_symbols", "")))
        navigator = fallback if fallback.exists() else navigator
    if not navigator.exists():
        raise RuntimeError(f"BROKER_NAVIGATOR_SYMBOLS_NOT_FOUND:{navigator}")
    expected = expected_symbols_from_csv(navigator)
    if not expected:
        raise RuntimeError("BROKER_EXPECTED_SYMBOLS_EMPTY")
    return snapshot, navigator, expected


def run_stage(
    job: str,
    run_id: str,
    *,
    config_path: str,
    scheduler_config: str,
    trade_date: str,
    no_telegram: bool,
    debug: bool,
) -> int:
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "run_sde_job.py"),
        "--job",
        job,
        "--config",
        config_path,
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
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    return int(completed.returncode)


def write_active_manifest(canonical_summary: Path, manifest: dict, run_id: str) -> None:
    sidecar = canonical_summary.with_suffix(".manifest.json")
    payload = {
        "Run_ID": run_id,
        "snapshot_id": manifest.get("snapshot_id"),
        "broker_period_type": manifest.get("broker_period_type"),
        "broker_period_start": manifest.get("broker_period_start"),
        "broker_period_end": manifest.get("broker_period_end"),
        "broker_trading_days": manifest.get("broker_trading_days"),
        "broker_session_dates": manifest.get("broker_session_dates", []),
        "freshness_status": "CURRENT",
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
    sidecar.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def archive_daily_raw(raw_path: Path, archive_dir: Path, trade_date: str) -> None:
    if not raw_path.exists() or raw_path.stat().st_size <= 0:
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / f"BROKER_RAW_{trade_date}_{datetime.now(WIB):%H%M%S}.csv"
    shutil.copy2(raw_path, destination)


def capture_performance(run_id: str, manifest_path: str, config: dict) -> None:
    db = PROJECT_ROOT / str(config.get("paths", {}).get("swing_database", "data/database/sde_swing_history.db"))
    if not db.is_absolute():
        db = PROJECT_ROOT / db
    command = [
        sys.executable,
        "-u",
        str(PROJECT_ROOT / "tools/broker_period_performance.py"),
        "capture",
        "--db",
        str(db),
        "--run-id",
        run_id,
        "--snapshot-manifest",
        manifest_path,
    ]
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    if completed.returncode != 0:
        print("[PERFORMANCE WARNING] Broker period metadata belum tercatat; Final Watchlist tetap valid.", flush=True)


def main() -> int:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = read_json(config_path)
    if not config:
        raise RuntimeError(f"PIPELINE_CONFIG_INVALID:{config_path}")

    trade_date = args.trade_date or datetime.now(WIB).date().isoformat()
    snapshot, navigator, expected_symbols = resolve_snapshot_and_symbols(config, trade_date)
    period_choice, custom_start = choose_period(trade_date, args.period, args.custom_start)

    if period_choice == "REUSE":
        selected_manifest = select_reuse(trade_date)
        spec = spec_from_manifest(selected_manifest)
        summary_snapshot = Path(str(selected_manifest.get("summary_snapshot_path", "")))
        raw_snapshot_text = str(selected_manifest.get("raw_snapshot_path", "")).strip()
        raw_snapshot = Path(raw_snapshot_text) if raw_snapshot_text else None
    else:
        if period_choice == "CUSTOM":
            spec = custom_period_spec(custom_start, trade_date)
        else:
            spec = fixed_period_spec(period_choice, trade_date)
        broker_cfg = config.get("broker", {})
        downloads = Path(os.path.expandvars(str(broker_cfg.get("downloads_dir", "%USERPROFILE%/Downloads")))).expanduser()
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
            downloads,
            expected_symbols,
            min_coverage,
            spec,
            timeout_seconds=timeout,
            poll_seconds=poll,
        )
        selected_manifest = persist_snapshot(
            export_path,
            raw_companion(export_path),
            spec,
            info,
        )
        summary_snapshot = Path(str(selected_manifest["summary_snapshot_path"]))
        raw_snapshot_text = str(selected_manifest.get("raw_snapshot_path", "")).strip()
        raw_snapshot = Path(raw_snapshot_text) if raw_snapshot_text else None

    if spec.period_end != trade_date:
        raise RuntimeError(f"BROKER_PRIMARY_CONTEXT_NOT_CURRENT:{spec.period_end}!={trade_date}")
    if not summary_snapshot.exists():
        raise RuntimeError(f"BROKER_SNAPSHOT_SUMMARY_MISSING:{summary_snapshot}")

    paths = config.get("paths", {})
    canonical_summary = PROJECT_ROOT / str(paths.get("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv"))
    canonical_raw = PROJECT_ROOT / str(paths.get("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv"))
    raw_archive = PROJECT_ROOT / str(paths.get("broker_raw_archive_dir", "data/input/broker/archive"))
    if canonical_summary.is_absolute() is False:
        canonical_summary = PROJECT_ROOT / canonical_summary
    if canonical_raw.is_absolute() is False:
        canonical_raw = PROJECT_ROOT / canonical_raw
    if raw_archive.is_absolute() is False:
        raw_archive = PROJECT_ROOT / raw_archive

    recovery_root = PROJECT_ROOT / "data/output/broker_snapshots/recovery"
    summary_guard = CanonicalGuard(canonical_summary, recovery_root, "BROKER_SUMMARY")
    sidecar_guard = CanonicalGuard(canonical_summary.with_suffix(".manifest.json"), recovery_root, "BROKER_SUMMARY_MANIFEST")
    raw_guard = CanonicalGuard(canonical_raw, recovery_root, "BROKER_RAW")
    run_id = make_run_id()
    summary_committed = False
    raw_daily_committed = False

    try:
        summary_guard.activate(summary_snapshot)
        # Protect the previous sidecar transactionally too.  A small temporary
        # JSON is used as source, then replaced with the full active manifest.
        temp_sidecar = recovery_root / f"ACTIVE_MANIFEST_{run_id}.json"
        temp_sidecar.parent.mkdir(parents=True, exist_ok=True)
        temp_sidecar.write_text("{}", encoding="utf-8")
        sidecar_guard.activate(temp_sidecar)
        write_active_manifest(canonical_summary, selected_manifest, run_id)

        if raw_snapshot is not None and raw_snapshot.exists() and raw_snapshot.stat().st_size > 0:
            raw_guard.activate(raw_snapshot)
        else:
            # Never let a stale RAW file from another period leak into Fusion.
            raw_guard.mask()

        rc = run_stage(
            "broker_summary",
            run_id,
            config_path=str(config_path),
            scheduler_config=args.scheduler_config,
            trade_date=trade_date,
            no_telegram=True,
            debug=args.debug,
        )
        if rc != 0:
            return rc

        # Only a true 1D export may become a daily-history observation.
        if spec.period_type == "1D" and raw_snapshot is not None and raw_snapshot.exists():
            archive_daily_raw(raw_snapshot, raw_archive, trade_date)
            raw_guard.commit()
            raw_daily_committed = True
        else:
            raw_guard.restore()

        rc = run_stage(
            "broker_multi_day",
            run_id,
            config_path=str(config_path),
            scheduler_config=args.scheduler_config,
            trade_date=trade_date,
            no_telegram=True,
            debug=args.debug,
        )
        if rc != 0:
            return rc

        rc = run_stage(
            "final_watchlist",
            run_id,
            config_path=str(config_path),
            scheduler_config=args.scheduler_config,
            trade_date=trade_date,
            no_telegram=args.no_telegram,
            debug=args.debug,
        )
        if rc != 0:
            return rc

        summary_guard.commit()
        sidecar_guard.commit()
        summary_committed = True
        selected_manifest["final_watchlist_run_id"] = run_id
        selected_manifest["activated_at"] = datetime.now(WIB).isoformat(timespec="seconds")
        selected_manifest["primary_context"] = True
        manifest_path = Path(str(selected_manifest.get("manifest_path", "")))
        if manifest_path.exists():
            manifest_path.write_text(json.dumps(selected_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        latest_selected = PROJECT_ROOT / "data/output/broker_snapshots/latest_selected.json"
        latest_selected.parent.mkdir(parents=True, exist_ok=True)
        latest_selected.write_text(json.dumps(selected_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        capture_performance(run_id, str(manifest_path), config)

        print("\n" + "=" * 68)
        print("FINAL WATCHLIST - BROKER PERIOD COMPLETE")
        print("=" * 68)
        print(f"Run ID          : {run_id}")
        print(f"Broker period   : {spec.period_type}")
        print(f"Range           : {spec.period_start} s/d {spec.period_end}")
        print(f"Trading sessions: {spec.trading_sessions}")
        print(f"Snapshot ID     : {selected_manifest.get('snapshot_id')}")
        print("Broker scoring  : EXISTING FORMULA (tidak diubah)")
        print("Persistence     : metadata/context only; tidak ada bonus/penalty baru")
        print("Telegram        : Final Watchlist + Lifecycle saja")
        print("=" * 68)
        return 0
    finally:
        if not summary_committed:
            sidecar_guard.restore()
            summary_guard.restore()
        if not raw_daily_committed:
            raw_guard.restore()


if __name__ == "__main__":
    raise SystemExit(main())
