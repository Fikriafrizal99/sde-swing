#!/usr/bin/env python3
"""Post-market Yahoo downloader guardrail.

This wrapper preserves the historical downloader implementation and only
adjusts its run-level quality classification. A small number of provider
failures must not invalidate an otherwise current IDX universe, while low
coverage still fails closed.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.historical_downloader import historical_downloader as base  # noqa: E402


DEFAULT_MIN_VALID_COVERAGE_RATIO = 0.98
_ORIGINAL_SUMMARIZE_MANIFEST = base.summarize_manifest


def minimum_valid_coverage_ratio() -> float:
    raw = str(os.getenv("SDE_YAHOO_MIN_VALID_COVERAGE", DEFAULT_MIN_VALID_COVERAGE_RATIO)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MIN_VALID_COVERAGE_RATIO
    return min(max(value, 0.0), 1.0)


def valid_closed_symbol_count(results: list[Any], expected_closed: date) -> int:
    expected_text = expected_closed.isoformat()
    valid_statuses = {"UPDATED_VALID", "UNCHANGED_ALREADY_CURRENT"}
    return sum(
        1
        for result in results
        if str(getattr(result, "status", "") or "") in valid_statuses
        and str(getattr(result, "latest_valid_close_date", "") or "") >= expected_text
    )


def summarize_manifest(
    args,
    input_path,
    output,
    results,
    expected_closed,
    fallback_used,
    accepted_stale,
    warning,
    started_at,
    finished_at,
    duration_seconds,
    network_request_batch_count,
):
    manifest = _ORIGINAL_SUMMARIZE_MANIFEST(
        args,
        input_path,
        output,
        results,
        expected_closed,
        fallback_used,
        accepted_stale,
        warning,
        started_at,
        finished_at,
        duration_seconds,
        network_request_batch_count,
    )

    total = len(results)
    valid_count = valid_closed_symbol_count(results, expected_closed)
    coverage = (valid_count / total) if total else 0.0
    threshold = minimum_valid_coverage_ratio()
    failed_symbols = [str(x) for x in manifest.get("Failed_Symbols", []) if str(x)]

    manifest["Valid_Closed_Symbol_Count"] = valid_count
    manifest["Valid_Symbol_Coverage_Ratio"] = round(coverage, 6)
    manifest["Minimum_Valid_Symbol_Coverage_Ratio"] = threshold
    manifest["Failure_Tolerance_Applied"] = False

    # The base downloader intentionally fails closed when any symbol fails.
    # For a large IDX universe this is too coarse: a transient failure in one
    # or two names should not invalidate hundreds of current closed candles.
    # Only override PROVIDER_FAILED when the *current closed-candle* coverage
    # still clears the strict threshold. Below the threshold, base behavior is
    # untouched and the job exits non-zero.
    if (
        not fallback_used
        and total > 0
        and coverage >= threshold
        and coverage < 1.0
        and str(manifest.get("Data_Quality_Status", "")).upper() == "PROVIDER_FAILED"
    ):
        detail = f"PARTIAL_COVERAGE: {valid_count}/{total} ({coverage:.2%}) current closed candles"
        if failed_symbols:
            shown = ",".join(failed_symbols[:20])
            suffix = "..." if len(failed_symbols) > 20 else ""
            detail += f"; failed={shown}{suffix}"
        existing_warning = str(manifest.get("Warning") or "").strip()
        manifest.update({
            "Refresh_Status": "SUCCESS_WITH_WARNING",
            "Candle_Status": "VALID_CLOSED_CANDLE_PARTIAL_COVERAGE",
            "Refresh_Detail_Status": "VALID_CLOSED_CANDLE_PARTIAL_COVERAGE",
            "Data_Quality_Status": "PARTIAL_COVERAGE",
            "Warning": "; ".join(x for x in (existing_warning, detail) if x),
            "Failure_Tolerance_Applied": True,
        })

    return manifest


def main() -> int:
    # Monkey-patch only the run-level manifest classifier. Download planning,
    # Yahoo requests, candle merging, same-session revalidation, file writing,
    # and per-symbol status logic remain exactly in the baseline downloader.
    base.summarize_manifest = summarize_manifest
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
