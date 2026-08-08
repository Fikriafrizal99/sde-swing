#!/usr/bin/env python3
"""Build the current IDX equity universe and exclude FCA/PPK stocks.

The master list is read from KSEI registered shares. The resulting CSV keeps a
minimal `Symbol` column because SDE Swing's historical downloader only requires
that field.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KSEI_URLS = (
    "https://web.ksei.co.id/services/registered-securities/shares?setLocale=id-ID",
    "https://web.ksei.co.id/services/registered-securities/shares",
)
DEFAULT_EXCLUSION = PROJECT_ROOT / "config" / "idx_fca_exclusions.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "input" / "IDX_ALL_NON_FCA.csv"
DEFAULT_EXCLUDED_OUTPUT = PROJECT_ROOT / "data" / "input" / "IDX_FCA_EXCLUDED.csv"
DEFAULT_METADATA = PROJECT_ROOT / "data" / "input" / "IDX_UNIVERSE_METADATA.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build all IDX shares excluding FCA/PPK.")
    p.add_argument("--source-url", default="", help="Optional KSEI URL override")
    p.add_argument("--exclusions", default=str(DEFAULT_EXCLUSION))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--excluded-output", default=str(DEFAULT_EXCLUDED_OUTPUT))
    p.add_argument("--metadata", default=str(DEFAULT_METADATA))
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument(
        "--activate",
        action="store_true",
        help="Set config/pipeline.json normalized_watchlist to the generated universe after success.",
    )
    return p.parse_args()


def fetch_html(source_url: str, timeout: int, retries: int) -> tuple[str, str]:
    urls = [source_url] if source_url else list(DEFAULT_KSEI_URLS)
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    })

    failures: list[str] = []
    attempts = max(1, retries)
    for url in urls:
        for attempt in range(1, attempts + 1):
            try:
                response = session.get(url, timeout=timeout, allow_redirects=True)
                if response.status_code == 200 and response.text.strip():
                    return response.text, str(response.url)
                failures.append(f"{url} -> HTTP {response.status_code}")
            except requests.RequestException as exc:
                failures.append(f"{url} -> {type(exc).__name__}: {exc}")
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))

    raise RuntimeError("KSEI request failed: " + " | ".join(failures[-6:]))


def extract_equity_symbols(html: str) -> list[str]:
    raw = re.findall(r"/registered-securities/shares/lc/([A-Za-z0-9]+)", html)
    if not raw:
        raw = re.findall(r"/shares/lc/([A-Za-z0-9]+)", html)

    seen: set[str] = set()
    symbols: list[str] = []
    for value in raw:
        code = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{4}", code):
            continue
        if code in seen:
            continue
        seen.add(code)
        symbols.append(code)
    return symbols


def load_exclusions(path: Path) -> tuple[set[str], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbols = {
        str(s).strip().upper()
        for s in payload.get("symbols", [])
        if re.fullmatch(r"[A-Z]{4}", str(s).strip().upper())
    }
    return symbols, payload


def write_symbol_csv(path: Path, symbols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Symbol"])
        writer.writerows([[symbol] for symbol in symbols])


def activate_pipeline(output_path: Path) -> None:
    pipeline_path = PROJECT_ROOT / "config" / "pipeline.json"
    payload = json.loads(pipeline_path.read_text(encoding="utf-8"))
    paths = payload.setdefault("paths", {})
    try:
        target = output_path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        target = output_path.as_posix()
    paths["normalized_watchlist"] = target
    pipeline_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"      Pipeline activated: normalized_watchlist = {target}", flush=True)


def main() -> int:
    args = parse_args()
    exclusion_path = Path(args.exclusions)
    output_path = Path(args.output)
    excluded_output_path = Path(args.excluded_output)
    metadata_path = Path(args.metadata)

    if not exclusion_path.exists():
        print(f"ERROR: exclusion file not found: {exclusion_path}", file=sys.stderr)
        return 2

    print("[1/4] Fetching current IDX registered shares from KSEI...", flush=True)
    try:
        html, resolved_source = fetch_html(args.source_url, args.timeout, args.retries)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        print(
            "Tip: coba buka halaman KSEI di browser. Jika browser juga gagal, ulangi beberapa menit lagi.",
            file=sys.stderr,
            flush=True,
        )
        return 3

    master = extract_equity_symbols(html)
    if len(master) < 900:
        print(
            f"ERROR: only {len(master)} equity-like symbols extracted; KSEI page structure/source may have changed.",
            file=sys.stderr,
        )
        return 4

    print(f"[2/4] Master equity-like symbols: {len(master)}", flush=True)
    exclusions, exclusion_meta = load_exclusions(exclusion_path)
    master_set = set(master)
    excluded_found = sorted(master_set & exclusions)
    excluded_missing = sorted(exclusions - master_set)
    universe = [symbol for symbol in master if symbol not in exclusions]

    if len(universe) < 700:
        print(f"ERROR: resulting universe unexpectedly small: {len(universe)}", file=sys.stderr)
        return 5

    print(f"[3/4] FCA/PPK excluded: {len(excluded_found)}", flush=True)
    if excluded_missing:
        print(
            "WARNING: exclusion symbols not present in current KSEI master: "
            + ", ".join(excluded_missing),
            flush=True,
        )

    write_symbol_csv(output_path, universe)
    write_symbol_csv(excluded_output_path, excluded_found)

    metadata = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": resolved_source,
        "master_equity_like_count": len(master),
        "fca_snapshot_as_of": exclusion_meta.get("as_of"),
        "fca_config_count": len(exclusions),
        "fca_excluded_found_count": len(excluded_found),
        "fca_not_in_master": excluded_missing,
        "output_count": len(universe),
        "output_path": str(output_path),
        "excluded_output_path": str(excluded_output_path),
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[4/4] Universe written: {output_path}", flush=True)
    print(f"      Active non-FCA symbols: {len(universe)}", flush=True)
    print(f"      FCA audit CSV: {excluded_output_path}", flush=True)
    print(f"      Metadata: {metadata_path}", flush=True)
    if args.activate:
        activate_pipeline(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
