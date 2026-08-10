#!/usr/bin/env python3
"""Run the unchanged Technical Feature Engine on the current validated universe.

The baseline technical engine intentionally scans every CSV in its input
folder. Historical files can outlive the current universe, and a transient
Yahoo failure can leave a stale symbol file behind. This wrapper builds a
run-scoped hardlink/copy view containing only symbols proven current by the
Yahoo refresh manifest, then delegates to the baseline engine unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.technical_feature_engine import technical_feature_engine as base  # noqa: E402


ACCEPTED_YAHOO_QUALITY = {"VALID", "PARTIAL_COVERAGE"}
CURRENT_PLAN_STATUSES = {"UPDATED", "ALREADY_CURRENT", "UPDATED_VALID", "UNCHANGED_ALREADY_CURRENT"}


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def select_current_symbols(manifest: dict[str, Any]) -> list[str]:
    expected = str(
        manifest.get("Latest_Expected_Trading_Date")
        or manifest.get("Latest_Closed_Candle_Date")
        or ""
    )
    plans = manifest.get("Symbol_Plans", [])
    if not expected or not isinstance(plans, list):
        return []

    selected: list[str] = []
    seen: set[str] = set()
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        symbol = _clean_symbol(plan.get("symbol"))
        status = str(plan.get("status") or "").strip().upper()
        latest = str(plan.get("local_last_date_after") or "").strip()
        if symbol and symbol not in seen and status in CURRENT_PLAN_STATUSES and latest >= expected:
            seen.add(symbol)
            selected.append(symbol)
    return selected


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _link_or_copy(source: Path, destination: Path) -> str:
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def build_validated_input(
    input_dir: Path,
    manifest: dict[str, Any],
    run_id: str,
    manifest_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    selected = select_current_symbols(manifest)
    expected = str(
        manifest.get("Latest_Expected_Trading_Date")
        or manifest.get("Latest_Closed_Candle_Date")
        or ""
    )
    if not selected:
        raise RuntimeError("YAHOO_VALIDATED_UNIVERSE_EMPTY")

    root = input_dir.parent / "validated_runs" / run_id
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    linked: list[str] = []
    missing_files: list[str] = []
    link_modes: dict[str, int] = {"hardlink": 0, "copy": 0}
    for symbol in selected:
        source = input_dir / f"{symbol}.csv"
        if not source.exists() or source.stat().st_size == 0:
            missing_files.append(symbol)
            continue
        mode = _link_or_copy(source, root / source.name)
        link_modes[mode] = link_modes.get(mode, 0) + 1
        linked.append(symbol)

    if not linked:
        raise RuntimeError("YAHOO_VALIDATED_INPUT_FILES_EMPTY")

    input_files = sorted(input_dir.glob("*.csv")) if input_dir.exists() else []
    all_existing = {_clean_symbol(path.stem) for path in input_files}
    universe = {
        _clean_symbol(plan.get("symbol"))
        for plan in manifest.get("Symbol_Plans", [])
        if isinstance(plan, dict) and _clean_symbol(plan.get("symbol"))
    }
    ignored_not_in_universe = sorted(all_existing - universe)
    omitted_not_current = sorted(universe - set(selected))

    audit = {
        "Run_ID": run_id,
        "Expected_Closed_Date": expected,
        "Yahoo_Data_Quality_Status": manifest.get("Data_Quality_Status"),
        "Yahoo_Valid_Symbol_Coverage_Ratio": manifest.get("Valid_Symbol_Coverage_Ratio", 1.0),
        "Source_Input_Dir": str(input_dir.resolve()),
        "Validated_Input_Dir": str(root.resolve()),
        "Universe_Symbol_Count": len(universe),
        "Selected_Current_Symbol_Count": len(selected),
        "Linked_Current_Symbol_Count": len(linked),
        "Missing_Current_File_Count": len(missing_files),
        "Omitted_Not_Current_Count": len(omitted_not_current),
        "Ignored_Not_In_Current_Universe_Count": len(ignored_not_in_universe),
        "Missing_Current_Files": missing_files,
        "Omitted_Not_Current": omitted_not_current,
        "Ignored_Not_In_Current_Universe": ignored_not_in_universe,
        "Link_Mode_Counts": link_modes,
    }
    audit_path = manifest_dir / f"TECHNICAL_INPUT_FILTER_{run_id}.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return root, audit


def _wrapper_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--input", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest-dir", required=True)
    parsed, _ = parser.parse_known_args(argv)
    return parsed


def _replace_input_arg(argv: list[str], new_input: Path) -> list[str]:
    result = list(argv)
    try:
        index = result.index("--input")
    except ValueError:
        result.extend(["--input", str(new_input)])
        return result
    if index + 1 >= len(result):
        result.append(str(new_input))
    else:
        result[index + 1] = str(new_input)
    return result


def main() -> int:
    wrapper = _wrapper_args(sys.argv[1:])
    manifest_dir = Path(wrapper.manifest_dir)
    yahoo_manifest_path = manifest_dir / f"YAHOO_REFRESH_MANIFEST_{wrapper.run_id}.json"
    manifest = _load_manifest(yahoo_manifest_path)
    if not manifest:
        print(f"ERROR: Yahoo manifest tidak ditemukan/invalid: {yahoo_manifest_path}", file=sys.stderr)
        return 2

    quality = str(manifest.get("Data_Quality_Status") or "").upper()
    if quality not in ACCEPTED_YAHOO_QUALITY:
        print(f"ERROR: Yahoo data quality tidak boleh masuk Technical Engine: {quality or 'UNKNOWN'}", file=sys.stderr)
        return 2

    try:
        validated_dir, audit = build_validated_input(
            Path(wrapper.input),
            manifest,
            wrapper.run_id,
            manifest_dir,
        )
    except Exception as exc:
        print(f"ERROR: gagal membangun validated technical input: {exc}", file=sys.stderr)
        return 2

    print(
        "[VALIDATED INPUT] "
        f"current={audit['Linked_Current_Symbol_Count']}/"
        f"{audit['Universe_Symbol_Count']} | "
        f"omitted_not_current={audit['Omitted_Not_Current_Count']} | "
        f"ignored_old_files={audit['Ignored_Not_In_Current_Universe_Count']}",
        flush=True,
    )

    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0], *_replace_input_arg(original_argv[1:], validated_dir)]
        return base.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    raise SystemExit(main())
