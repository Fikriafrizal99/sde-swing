#!/usr/bin/env python3
"""Run the frozen Technical Feature Engine behind the canonical data boundary.

The legacy Yahoo/historical downloader remains the acquisition provider.  This
wrapper selects only symbols proven current by the Yahoo refresh manifest, then
routes their historical rows through ``DataSourceManager`` and the legacy
DailyBar adapter.  The frozen Technical Feature Engine receives only the
run-scoped canonical CSV materialization, never the raw provider folder.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.data_sources.legacy_daily_bar_adapter import (  # noqa: E402
    CANONICAL_DAILY_HISTORY_CONTRACT,
    materialize_legacy_daily_history,
)
from modules.runtime.data_source_manager import DataSourceManager  # noqa: E402
from modules.technical_feature_engine import technical_feature_engine as base  # noqa: E402


ACCEPTED_YAHOO_QUALITY = {"VALID", "PARTIAL_COVERAGE"}
CURRENT_PLAN_STATUSES = {"UPDATED", "ALREADY_CURRENT", "UPDATED_VALID", "UNCHANGED_ALREADY_CURRENT"}
CANONICAL_BOUNDARY = "DataSourceManager.route"


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


def _atomic_audit(path: Path, payload: dict[str, Any]) -> None:
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        handle.flush()
    tmp.replace(path)


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
    if not expected:
        raise RuntimeError("YAHOO_EXPECTED_CLOSED_DATE_MISSING")
    if not selected:
        raise RuntimeError("YAHOO_VALIDATED_UNIVERSE_EMPTY")

    manager = DataSourceManager(
        PROJECT_ROOT / "config/data_sources.json",
        root=PROJECT_ROOT,
        mode="LIVE",
        run_id=run_id,
        force_mock=False,
        file_roots={"historical": input_dir},
    )
    canonical_dir, canonical = materialize_legacy_daily_history(
        manager,
        input_dir=input_dir,
        symbols=selected,
        expected_market_date=expected,
        run_id=run_id,
        manifest_dir=manifest_dir,
    )

    input_files = sorted(input_dir.glob("*.csv")) if input_dir.exists() else []
    all_existing = {_clean_symbol(path.stem) for path in input_files}
    universe = {
        _clean_symbol(plan.get("symbol"))
        for plan in manifest.get("Symbol_Plans", [])
        if isinstance(plan, dict) and _clean_symbol(plan.get("symbol"))
    }
    ignored_not_in_universe = sorted(all_existing - universe)
    omitted_not_current = sorted(universe - set(selected))
    accepted = list(canonical.get("Accepted_Symbols", []))
    rejected = dict(canonical.get("Rejected_Symbols", {}))

    audit = {
        "Run_ID": run_id,
        "Canonical_Boundary": CANONICAL_BOUNDARY,
        "Canonical_Contract": CANONICAL_DAILY_HISTORY_CONTRACT,
        "Legacy_Adapter": canonical.get("Legacy_Adapter", "LegacyHistoricalProviderAdapter"),
        "Expected_Closed_Date": expected,
        "Yahoo_Data_Quality_Status": manifest.get("Data_Quality_Status"),
        "Yahoo_Valid_Symbol_Coverage_Ratio": manifest.get("Valid_Symbol_Coverage_Ratio", 1.0),
        "Source_Input_Dir": str(input_dir.resolve()),
        "Validated_Input_Dir": str(canonical_dir.resolve()),
        "Canonical_Input_Dir": str(canonical_dir.resolve()),
        "Canonical_Manifest": canonical.get("Manifest_Path", ""),
        "Universe_Symbol_Count": len(universe),
        "Selected_Current_Symbol_Count": len(selected),
        # Compatibility name retained for older operational readers.  These
        # are canonicalized files now; no hardlinks/copies are created.
        "Linked_Current_Symbol_Count": len(accepted),
        "Canonicalized_Current_Symbol_Count": len(accepted),
        "Missing_Current_File_Count": sum(
            1 for reason in rejected.values() if reason == "SOURCE_FILE_MISSING"
        ),
        "Rejected_Canonical_Symbol_Count": len(rejected),
        "Canonical_Coverage_Ratio": canonical.get("Canonical_Coverage_Ratio", 0.0),
        "Omitted_Not_Current_Count": len(omitted_not_current),
        "Ignored_Not_In_Current_Universe_Count": len(ignored_not_in_universe),
        "Rejected_Canonical_Symbols": rejected,
        "Omitted_Not_Current": omitted_not_current,
        "Ignored_Not_In_Current_Universe": ignored_not_in_universe,
        "Link_Mode_Counts": {"hardlink": 0, "copy": 0, "canonical_materialized": len(accepted)},
        "Engine_Input_Is_Raw_Provider_Directory": False,
    }
    audit_path = manifest_dir / f"TECHNICAL_INPUT_FILTER_{run_id}.json"
    _atomic_audit(audit_path, audit)
    return canonical_dir, audit


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


def _annotate_technical_manifest(
    manifest_dir: Path,
    run_id: str,
    raw_input: Path,
    canonical_input: Path,
    audit: dict[str, Any],
) -> None:
    path = manifest_dir / f"TECHNICAL_MANIFEST_{run_id}.json"
    payload = _load_manifest(path)
    if not payload:
        raise RuntimeError(f"TECHNICAL_MANIFEST_NOT_FOUND_AFTER_ENGINE:{path}")
    payload.update({
        "Canonical_Data_Boundary": CANONICAL_BOUNDARY,
        "Canonical_Data_Contract": CANONICAL_DAILY_HISTORY_CONTRACT,
        "Canonical_Input_Manifest": audit.get("Canonical_Manifest", ""),
        "Canonical_Input_Dir": str(canonical_input.resolve()),
        "Raw_Provider_Input_Dir": str(raw_input.resolve()),
        "Engine_Input_Is_Raw_Provider_Directory": False,
    })
    _atomic_audit(path, payload)


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
        canonical_dir, audit = build_validated_input(
            Path(wrapper.input),
            manifest,
            wrapper.run_id,
            manifest_dir,
        )
    except Exception as exc:
        print(f"ERROR: gagal membangun canonical technical input: {exc}", file=sys.stderr)
        return 2

    print(
        "[CANONICAL INPUT] "
        f"current={audit['Canonicalized_Current_Symbol_Count']}/"
        f"{audit['Selected_Current_Symbol_Count']} | "
        f"coverage={float(audit['Canonical_Coverage_Ratio']):.0%} | "
        f"omitted_not_current={audit['Omitted_Not_Current_Count']} | "
        f"ignored_old_files={audit['Ignored_Not_In_Current_Universe_Count']}",
        flush=True,
    )

    original_argv = sys.argv
    try:
        sys.argv = [original_argv[0], *_replace_input_arg(original_argv[1:], canonical_dir)]
        result = base.main()
    finally:
        sys.argv = original_argv

    if result == 0:
        try:
            _annotate_technical_manifest(
                manifest_dir,
                wrapper.run_id,
                Path(wrapper.input),
                canonical_dir,
                audit,
            )
        except Exception as exc:
            print(f"ERROR: canonical technical lineage tidak dapat dipersist: {exc}", file=sys.stderr)
            return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
