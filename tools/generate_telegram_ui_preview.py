#!/usr/bin/env python3
"""Generate previews for the current SDE Swing Telegram presentations only."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.enhanced_runtime_bridge import (
    final_watchlist_payloads,
    market_outlook_payloads,
    post_market_payloads,
)
from modules.job_runner.runtime import load_context
from modules.telegram.professional_ui import UiConfig, format_data_warning


def latest_file(pattern: str) -> Path | None:
    files = [path for path in ROOT.glob(pattern) if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def read_json(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def manifest_trade_date(payload: dict) -> str:
    return str(
        payload.get("Technical_Date")
        or payload.get("trade_date")
        or payload.get("Trade_Date")
        or payload.get("snapshot_trade_date")
        or ""
    )[:10]


def latest_manifest_for_date(trade_date: str) -> tuple[Path | None, dict]:
    candidates = [
        path
        for pattern in (
            "data/output/manifests/SWING_RUN_MANIFEST_SDE-FINAL-WATCHLIST-*.json",
            "data/output/manifests/SWING_RUN_MANIFEST_*.json",
        )
        for path in ROOT.glob(pattern)
        if path.is_file()
    ]
    matches: list[tuple[Path, dict]] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        payload = read_json(path)
        if payload and manifest_trade_date(payload) == trade_date:
            matches.append((path, payload))
    if not matches:
        return None, {}
    return max(matches, key=lambda item: item[0].stat().st_mtime)


def make_context(job: str, trade_date: str):
    return load_context(
        job=job,
        config_path="config/pipeline.json",
        scheduler_config_path="config/scheduler.json",
        trade_date=trade_date,
        dry_run=True,
        preview_existing=True,
        no_telegram=True,
        force=True,
        debug=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate previews for current Telegram report presentations"
    )
    parser.add_argument(
        "--trade-date",
        default=datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat(),
    )
    parser.add_argument(
        "--output-dir",
        default="data/output/telegram_ui_preview/scheduled",
    )
    parser.add_argument("--include-warning-example", action="store_true", default=True)
    args = parser.parse_args()

    output = (ROOT / args.output_dir).resolve()
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)

    manifest_path, final_manifest = latest_manifest_for_date(args.trade_date)

    global_path = (
        ROOT
        / "data/output/global_market"
        / args.trade_date
        / "global_market_snapshot.json"
    )
    regime_path = (
        ROOT
        / "data/output/market_regime"
        / args.trade_date
        / "market_outlook_regime.json"
    )
    global_snapshot = read_json(global_path)
    market_status = read_json(regime_path)

    payloads = []
    build_errors: list[str] = []

    def collect(label: str, builder: Callable[[], list]) -> None:
        try:
            payloads.extend(builder())
        except Exception as exc:
            build_errors.append(f"{label}:{type(exc).__name__}:{exc}")

    if global_snapshot and market_status:
        collect(
            "market_outlook",
            lambda: market_outlook_payloads(
                make_context("market_outlook", args.trade_date),
                global_snapshot,
                market_status,
            ),
        )
    else:
        build_errors.append("market_outlook:source_artifact_not_found")

    if final_manifest:
        collect(
            "post_market",
            lambda: post_market_payloads(
                make_context("post_market", args.trade_date),
                final_manifest,
            ),
        )
        collect(
            "final_watchlist",
            lambda: final_watchlist_payloads(
                make_context("final_watchlist", args.trade_date),
                final_manifest,
            ),
        )
    else:
        build_errors.append("post_market/final_watchlist:run_manifest_not_found")

    entries: list[dict] = []
    sequence = 1
    supported = {
        "market_outlook",
        "post_market",
        "final_watchlist_summary",
        "final_watchlist_detail",
    }
    for payload in payloads:
        if payload.report_type not in supported or not str(payload.text or "").strip():
            continue
        symbol_suffix = f"_{payload.symbol}" if payload.symbol else ""
        filename = f"{sequence:02d}_{payload.report_type}{symbol_suffix}.txt"
        path = output / filename
        path.write_text(payload.text.strip() + "\n", encoding="utf-8")
        entries.append({
            "file": filename,
            "report_type": payload.report_type,
            "symbol": payload.symbol or "",
            "chars": len(payload.text),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "attachment": str(getattr(payload, "attachment_path", "") or ""),
        })
        sequence += 1

    telegram_cfg = read_json(ROOT / "config/telegram.json")
    cfg = UiConfig.from_dict(telegram_cfg.get("telegram_ui", {}))
    if args.include_warning_example:
        warning_text = format_data_warning(
            run_id="SWING-PREVIEW-DATA-WARNING",
            expected_date=args.trade_date,
            latest_valid_date=final_manifest.get("Technical_Date", "DATA_NOT_AVAILABLE"),
            broker_date=final_manifest.get("Broker_Date", "DATA_NOT_AVAILABLE"),
            fallback_used=True,
            broker_override=False,
            data_status="STALE_ACCEPTED",
            warnings=["STALE_DATA_ACCEPTED_BY_USER"],
            data_source="LIVE_YAHOO",
            config=cfg,
        )
        path = output / f"{sequence:02d}_data_warning_example.txt"
        path.write_text(warning_text.strip() + "\n", encoding="utf-8")
        entries.append({
            "file": path.name,
            "report_type": "data_warning",
            "symbol": "",
            "chars": len(warning_text),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "attachment": "",
        })

    source_manifest_ref = manifest_path.relative_to(ROOT).as_posix() if manifest_path else ""
    manifest = {
        "trade_date": args.trade_date,
        "source_manifest": source_manifest_ref,
        "global_snapshot": global_path.relative_to(ROOT).as_posix(),
        "market_regime": regime_path.relative_to(ROOT).as_posix(),
        "presentation_contract": "CURRENT_ENHANCED_RUNTIME_ONLY",
        "superseded_reports_excluded": [
            "daily_signal_recap",
            "closing_bell",
        ],
        "parse_mode": cfg.parse_mode,
        "telegram_send": "SKIPPED_DRY_RUN",
        "report_count": len(entries),
        "reports": entries,
        "build_errors": build_errors,
    }
    (output / "PREVIEW_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(entries)} current Telegram UI previews in {output}")
    if build_errors:
        print("Preview warnings:")
        for item in build_errors:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
