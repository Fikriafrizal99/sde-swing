#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import ensure_dir, file_sha256, make_run_id, normalize_symbol, write_json
from modules.broker_bridge.broker_raw import validate_broker_raw

REQUIRED = {
    "FROM_DATE", "TO_DATE", "EMITEN", "TOTAL_BUY", "TOTAL_SELL", "NET_FLOW",
    "TOP_BUYER_1", "TOP_SELLER_1", "BUYER_CONCENTRATION", "SELLER_CONCENTRATION",
}


def default_downloads() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"


def read_symbols(path: Path) -> list[str]:
    df = pd.read_csv(path, low_memory=False)
    col = next((c for c in df.columns if str(c).strip().upper() in {"SYMBOL", "EMITEN", "TICKER", "CODE"}), None)
    if col is None:
        raise RuntimeError(f"Kolom Symbol/Emiten tidak ditemukan: {path}")
    symbols = [normalize_symbol(x) for x in df[col]]
    return list(dict.fromkeys(x for x in symbols if x))


def normalize_broker_symbols(df: pd.DataFrame) -> pd.Series:
    return df["EMITEN"].map(normalize_symbol)


def inspect(path: Path, expected: list[str], min_coverage: float) -> tuple[bool, str, dict]:
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as exc:
        return False, f"belum selesai/CSV tidak bisa dibaca: {exc}", {}
    missing_cols = sorted(REQUIRED - set(df.columns))
    if missing_cols:
        return False, f"kolom wajib tidak lengkap: {missing_cols}", {}
    if df.empty:
        return False, "CSV kosong", {}

    clean_symbols = normalize_broker_symbols(df)
    symbols = set(x for x in clean_symbols if x)
    expected_set = set(expected)
    matched = expected_set & symbols
    coverage = len(matched) / max(1, len(expected_set))
    duplicates = sorted(clean_symbols[clean_symbols.duplicated()].dropna().unique().tolist())
    unexpected = sorted(symbols - expected_set)
    if coverage < min_coverage:
        return False, f"coverage {len(matched)}/{len(expected_set)} ({coverage:.0%}) < {min_coverage:.0%}", {
            "source": str(path), "rows": int(len(df)), "expected": len(expected_set),
            "matched": len(matched), "coverage": coverage,
            "missing_symbols": sorted(expected_set - symbols),
            "unexpected_symbols": unexpected,
            "duplicate_symbols": duplicates,
        }

    dates = pd.to_datetime(df["TO_DATE"], errors="coerce")
    from_dates = pd.to_datetime(df["FROM_DATE"], errors="coerce")
    valid_from_dates = from_dates.dropna()
    broker_date = dates.max()
    if pd.isna(broker_date):
        return False, "TO_DATE tidak valid", {}
    info = {
        "source": str(path),
        "source_hash": file_sha256(path),
        "rows": int(len(df)),
        "expected": len(expected_set),
        "matched": len(matched),
        "symbol_count": int(len(symbols)),
        "coverage": coverage,
        "broker_date": broker_date.strftime("%Y-%m-%d"),
        "from_date": valid_from_dates.min().strftime("%Y-%m-%d") if not valid_from_dates.empty else "",
        "to_date": broker_date.strftime("%Y-%m-%d"),
        "missing_symbols": sorted(expected_set - symbols),
        "unexpected_symbols": unexpected,
        "duplicate_symbols": duplicates,
        "required_columns": sorted(REQUIRED),
    }
    return True, "OK", info


def resolve_manual_file(value: str, downloads: Path) -> Path:
    raw = Path(os.path.expandvars(value.strip().strip('"'))).expanduser()
    candidates = [raw]
    if not raw.is_absolute():
        candidates.extend([PROJECT_ROOT / raw, downloads / raw.name])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(f"Manual broker file tidak ditemukan: {value}")


def files_for_scan(downloads: Path, started: float, include_existing: bool) -> list[Path]:
    files = sorted(downloads.glob("BROKER_SUMMARY_COMBINED_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if include_existing:
        return files
    return [p for p in files if p.stat().st_mtime >= started - 60]


def print_mismatch(info: dict, expected_symbols: list[str], expected_date: str, technical_date: str, policy: str) -> None:
    print("\n[BROKER DATE MISMATCH]")
    print(f"Technical Data Date   : {technical_date or '-'}")
    print(f"Expected Broker Date  : {expected_date or '-'}")
    print(f"Detected Broker Date  : {info.get('broker_date', '-')}")
    print(f"Broker File           : {info.get('source', '-')}")
    print(f"Broker Symbol Count   : {info.get('symbol_count', '-')}")
    print(f"Expected Symbol Count : {len(expected_symbols)}")
    print(f"Coverage              : {info.get('matched', 0)}/{info.get('expected', 0)} - {info.get('coverage', 0):.0%}")
    print(f"Missing Symbols       : {', '.join(info.get('missing_symbols', [])) or '-'}")
    print(f"Unexpected Symbols    : {', '.join(info.get('unexpected_symbols', [])) or '-'}")
    print(f"Duplicate Symbols     : {', '.join(info.get('duplicate_symbols', [])) or '-'}")
    print(f"Broker Date Policy    : {policy}")


def ask_date_mismatch(info: dict, expected_symbols: list[str], expected_date: str, technical_date: str, policy: str) -> str:
    print_mismatch(info, expected_symbols, expected_date, technical_date, policy)
    print("\n[1] Gunakan Broker Summary yang ditemukan")
    print("[2] Pilih Broker Summary secara manual")
    print("[3] Scan ulang folder broker")
    print("[4] Tunggu export Tampermonkey baru")
    print("[5] Batalkan pipeline")
    return input("Pilihan: ").strip()


def choose_valid_file(
    files: list[Path],
    expected: list[str],
    min_coverage: float,
    expected_date: str = "",
    exact_date: bool = False,
) -> tuple[Path | None, dict | None, str]:
    last_message = ""
    for candidate in files:
        ok, message, info = inspect(candidate, expected, min_coverage)
        if not ok:
            last_message = f"{candidate.name}: {message}"
            continue
        if exact_date and expected_date and info.get("broker_date") != expected_date:
            last_message = (
                f"{candidate.name}: tanggal broker {info.get('broker_date', 'UNKNOWN')} "
                f"bukan expected {expected_date}"
            )
            continue
        return candidate, info, "OK"
    return None, None, last_message or "Broker Summary valid belum ditemukan"


def select_manual(path_text: str, downloads: Path, expected: list[str], min_coverage: float) -> tuple[Path, dict]:
    manual_path = resolve_manual_file(path_text, downloads)
    ok, message, info = inspect(manual_path, expected, min_coverage)
    if not ok:
        raise RuntimeError(f"Manual broker file tidak valid: {message}")
    return manual_path, info


def date_matches(info: dict, expected_date: str) -> bool:
    return bool(expected_date) and info.get("broker_date") == expected_date


def main() -> int:
    p = argparse.ArgumentParser(description="Wait for Tampermonkey BROKER_SUMMARY_COMBINED export")
    p.add_argument("--symbols", required=True)
    p.add_argument("--downloads", default=str(default_downloads()))
    p.add_argument("--output", required=True)
    p.add_argument("--raw-output", default="")
    p.add_argument("--archive-dir", default="")
    p.add_argument("--timeout", type=int, default=1200)
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--min-coverage", type=float, default=0.80)
    p.add_argument("--allow-old-date", action="store_true")
    p.add_argument("--expected-broker-date", default="")
    p.add_argument("--technical-date", default="")
    p.add_argument("--broker-date-policy", choices=["exact", "latest", "manual", "ask"], default="exact")
    p.add_argument("--manual-broker-file", default="")
    p.add_argument("--include-existing", action="store_true")
    p.add_argument("--run-id", default=None)
    p.add_argument("--manifest-dir", default="")
    args = p.parse_args()
    args.run_id = args.run_id or make_run_id()
    if args.allow_old_date and args.broker_date_policy == "exact":
        args.broker_date_policy = "latest"

    symbols_path = Path(args.symbols)
    downloads = Path(os.path.expandvars(args.downloads)).expanduser()
    output = Path(args.output)
    expected = read_symbols(symbols_path)
    if not expected:
        raise RuntimeError("Daftar simbol kandidat kosong")
    if not downloads.exists():
        raise FileNotFoundError(f"Folder Downloads tidak ditemukan: {downloads}")

    expected_date = args.expected_broker_date or args.technical_date or date.today().isoformat()
    started = time.time()
    print(f"[BROKER] Menunggu export Tampermonkey di: {downloads}", flush=True)
    print(f"[BROKER] Import ke Tampermonkey: {symbols_path}", flush=True)
    print(f"[BROKER] Expected symbols={len(expected)}, expected_date={expected_date}, policy={args.broker_date_policy}", flush=True)

    selected: Path | None = None
    selected_info: dict | None = None
    override = False
    warning = ""
    policy = args.broker_date_policy

    if policy == "manual":
        if not args.manual_broker_file:
            raise RuntimeError("--manual-broker-file wajib diisi untuk broker-date-policy manual")
        selected, selected_info = select_manual(args.manual_broker_file, downloads, expected, args.min_coverage)
        override = not date_matches(selected_info, expected_date)
        warning = "DATE_MISMATCH_ACCEPTED_BY_USER" if override else ""
    else:
        last_message = ""
        while time.time() - started <= args.timeout:
            include_existing = args.include_existing or policy in {"latest", "ask"}
            files = files_for_scan(downloads, started, include_existing)
            candidate, info, message = choose_valid_file(
                files,
                expected,
                args.min_coverage,
                expected_date=expected_date,
                exact_date=(policy == "exact"),
            )
            if candidate is None or info is None:
                if message != last_message:
                    print(f"[BROKER] {message}", flush=True)
                    last_message = message
                time.sleep(max(0.5, args.poll))
                continue

            if policy == "latest":
                selected, selected_info = candidate, info
                override = not date_matches(info, expected_date)
                warning = "DATE_MISMATCH_ACCEPTED_BY_POLICY_LATEST" if override else ""
                break

            if policy == "exact" and date_matches(info, expected_date):
                selected, selected_info = candidate, info
                break

            if policy == "exact":
                message = f"{candidate.name}: tanggal broker {info['broker_date']} bukan expected {expected_date}"
                if message != last_message:
                    print(f"[BROKER] {message}", flush=True)
                    last_message = message
                time.sleep(max(0.5, args.poll))
                continue

            if policy == "ask":
                choice = ask_date_mismatch(info, expected, expected_date, args.technical_date, policy)
                if choice == "1":
                    selected, selected_info = candidate, info
                    override = not date_matches(info, expected_date)
                    warning = "DATE_MISMATCH_ACCEPTED_BY_USER" if override else ""
                    break
                if choice == "2":
                    manual = input("Path Broker Summary manual: ").strip()
                    selected, selected_info = select_manual(manual, downloads, expected, args.min_coverage)
                    override = not date_matches(selected_info, expected_date)
                    warning = "DATE_MISMATCH_ACCEPTED_BY_USER" if override else ""
                    break
                if choice == "3":
                    continue
                if choice == "4":
                    started = time.time()
                    continue
                raise RuntimeError("Pipeline dibatalkan oleh user karena broker date mismatch")

            time.sleep(max(0.5, args.poll))

    if selected is None or selected_info is None:
        raise TimeoutError("Broker export terbaru tidak ditemukan/valid. Telegram tidak dikirim agar data lama tidak terpakai.")

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(selected, output)
    archive_dir = Path(args.archive_dir) if args.archive_dir else output.parent / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"BROKER_SUMMARY_{selected_info['broker_date']}_{datetime.now():%H%M%S}.csv"
    shutil.copy2(selected, archive)

    raw_source = selected.with_name(selected.name.replace("BROKER_SUMMARY_COMBINED_", "BROKER_RAW_COMBINED_"))
    raw_output = Path(args.raw_output) if args.raw_output else output.with_name("BROKER_RAW_LATEST.csv")
    raw_archive = ""
    raw_warning = ""
    if raw_source.exists():
        valid_raw, raw_df, raw_warning = validate_broker_raw(raw_source, selected_info["broker_date"])
        if valid_raw:
            raw_output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_source, raw_output)
            raw_archive_path = archive_dir / f"BROKER_RAW_{selected_info['broker_date']}_{datetime.now():%H%M%S}.csv"
            shutil.copy2(raw_source, raw_archive_path)
            raw_archive = str(raw_archive_path)
            avg_available = int(pd.to_numeric(raw_df.get("AVG_PRICE"), errors="coerce").fillna(0).gt(0).sum())
            if avg_available < len(raw_df):
                raw_warning = f"BROKER_RAW_PARTIAL_AVG_COVERAGE: {avg_available}/{len(raw_df)}"
    else:
        raw_warning = "BROKER_RAW_COMPANION_NOT_FOUND"

    data_quality = "VALID"
    if override:
        data_quality = "BROKER_DATE_OVERRIDE"
    elif selected_info.get("coverage", 0) < 1:
        data_quality = "PARTIAL_COVERAGE"
    if policy == "manual":
        data_quality = "MANUAL_FILE" if not override else "BROKER_DATE_OVERRIDE"

    manifest = selected_info | {
        "Run_ID": args.run_id,
        "copied_to": str(output),
        "archive": str(archive),
        "broker_raw_source": str(raw_source) if raw_source.exists() else "",
        "broker_raw_copied_to": str(raw_output) if raw_output.exists() else "",
        "broker_raw_archive": raw_archive,
        "broker_raw_warning": raw_warning,
        "symbols_source": str(symbols_path.resolve()),
        "symbols_source_hash": file_sha256(symbols_path),
        "expected_broker_date": expected_date,
        "technical_date": args.technical_date,
        "BROKER_DATE_OVERRIDE": bool(override),
        "BROKER_DATE_SELECTED": selected_info.get("broker_date"),
        "BROKER_FILE_SELECTED": str(selected),
        "BROKER_DATE_POLICY": policy,
        "BROKER_WARNING": warning,
        "DATA_QUALITY_STATUS": data_quality,
    }
    manifest_path = output.with_suffix(".manifest.json")
    write_json(manifest_path, manifest)
    if args.manifest_dir:
        manifest_dir = Path(args.manifest_dir)
        ensure_dir(manifest_dir)
        write_json(manifest_dir / f"BROKER_MANIFEST_{args.run_id}.json", manifest)

    print(f"[BROKER] OK rows={selected_info['rows']} matched={selected_info['matched']}/{selected_info['expected']}", flush=True)
    print(f"[BROKER] broker_date={selected_info['broker_date']}", flush=True)
    print(f"[BROKER] coverage={selected_info['coverage']:.0%}", flush=True)
    if override:
        print("[BROKER] WARNING: DATE_MISMATCH_ACCEPTED", flush=True)
    if selected_info["missing_symbols"]:
        print(f"[BROKER] missing={','.join(selected_info['missing_symbols'])}", flush=True)
    if selected_info["unexpected_symbols"]:
        print(f"[BROKER] unexpected={','.join(selected_info['unexpected_symbols'])}", flush=True)
    if selected_info["duplicate_symbols"]:
        print(f"[BROKER] duplicates={','.join(selected_info['duplicate_symbols'])}", flush=True)
    print(f"[BROKER] output={output}", flush=True)
    if raw_output.exists():
        print(f"[BROKER] raw_detail={raw_output}", flush=True)
    elif raw_warning:
        print(f"[BROKER] raw_warning={raw_warning}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
