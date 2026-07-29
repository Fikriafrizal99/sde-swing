#!/usr/bin/env python3
"""Export Candidate Selector symbols into a Tampermonkey-compatible CSV bridge."""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import file_sha256, make_run_id, write_json

SYMBOL_ALIASES = ("Symbol", "EMITEN", "Ticker", "Code")
EXCLUDED = {"BRENT", "OIL", "XAU", "IHSG"}
PRIORITY_FILENAMES = (
    "BUY_CANDIDATES.csv",
    "TECHNICAL_CANDIDATES.csv",
    "CANDIDATES.csv",
    "WATCHLIST.csv",
    "FINAL_DECISION_RANKING.csv",
)


def normalize_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def normalize_symbol(value: object) -> str:
    first_line = str(value or "").strip().splitlines()[0] if str(value or "").strip() else ""
    first_line = first_line.upper().replace(".JK", "")
    match = re.search(r"[A-Z0-9]{2,12}", first_line)
    return match.group(0) if match else ""


def read_symbols(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []
        mapping = {normalize_header(name): name for name in reader.fieldnames}
        symbol_col = next(
            (mapping[normalize_header(alias)] for alias in SYMBOL_ALIASES if normalize_header(alias) in mapping),
            None,
        )
        if symbol_col is None:
            return []

        symbols: list[str] = []
        seen: set[str] = set()
        for row in reader:
            symbol = normalize_symbol(row.get(symbol_col, ""))
            if not symbol or symbol in EXCLUDED or symbol in seen:
                continue
            seen.add(symbol)
            symbols.append(symbol)
        return symbols


def candidate_files(folder: Path, output: Path) -> list[Path]:
    files = [p for p in folder.glob("*.csv") if p.resolve() != output.resolve() and p.stat().st_size > 0]
    priority = {name.lower(): index for index, name in enumerate(PRIORITY_FILENAMES)}
    return sorted(
        files,
        key=lambda p: (priority.get(p.name.lower(), len(priority)), -p.stat().st_mtime),
    )


def choose_source(source: Path, output: Path) -> tuple[Path, list[str]]:
    if source.is_file():
        symbols = read_symbols(source)
        if not symbols:
            raise ValueError(f"Tidak ada simbol valid di file kandidat: {source}")
        return source, symbols

    if not source.is_dir():
        raise FileNotFoundError(f"Sumber kandidat tidak ditemukan: {source}")

    inspected: list[str] = []
    for path in candidate_files(source, output):
        inspected.append(path.name)
        symbols = read_symbols(path)
        if symbols:
            return path, symbols

    detail = ", ".join(inspected) if inspected else "tidak ada CSV"
    raise ValueError(f"Tidak menemukan CSV kandidat berisi simbol di {source} ({detail}).")




def write_symbol_csv(path: Path, symbols: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Symbol"])
        writer.writerows([[symbol] for symbol in symbols])


def export_symbols(
    output: Path,
    symbols: list[str],
    run_id: str,
    writer=write_symbol_csv,
) -> tuple[Path, str]:
    """Write symbols and fall back to a run-specific file on Windows file lock."""
    actual_output = output
    warning = ""
    try:
        writer(output, symbols)
    except PermissionError:
        actual_output = output.with_name(f"{output.stem}_{run_id}{output.suffix}")
        warning = (
            f"Output default terkunci ({output}). "
            f"Menulis file alternatif: {actual_output.name}"
        )
        writer(actual_output, symbols)
    return actual_output, warning


def main() -> None:
    parser = argparse.ArgumentParser(description="Create BROKER_NAVIGATOR_SYMBOLS.csv for Tampermonkey")
    parser.add_argument("source", type=Path, help="File kandidat atau folder output Candidate Selector")
    parser.add_argument("--output", "-o", type=Path, default=Path("BROKER_NAVIGATOR_SYMBOLS.csv"))
    parser.add_argument("--sort", action="store_true", help="Urutkan simbol secara alfabetis")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest-dir", default=None)
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    source_file, symbols = choose_source(args.source, args.output)
    if args.sort:
        symbols = sorted(symbols)

    actual_output, write_warning = export_symbols(args.output, symbols, args.run_id)

    print(f"OK: {len(symbols)} simbol -> {actual_output}")
    if write_warning:
        print(f"WARNING: {write_warning}")
    print(f"SOURCE: {source_file}")
    manifest = {
        "Run_ID": args.run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source_file.resolve()),
        "source_hash": file_sha256(source_file),
        "requested_output": str(args.output.resolve()),
        "output": str(actual_output.resolve()),
        "output_hash": file_sha256(actual_output),
        "symbol_count": len(symbols),
        "unique_symbol_count": len(set(symbols)),
        "symbols": symbols,
        "required_columns": ["Symbol"],
        "status": "OK_WITH_ALTERNATE_OUTPUT" if write_warning else "OK",
        "warning": write_warning,
    }
    manifest_path = actual_output.with_suffix(".manifest.json")
    write_json(manifest_path, manifest)
    if args.manifest_dir:
        manifest_dir = Path(args.manifest_dir)
        write_json(manifest_dir / f"BROKER_NAVIGATOR_MANIFEST_{args.run_id}.json", manifest)


if __name__ == "__main__":
    main()
