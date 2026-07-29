#!/usr/bin/env python3
"""Generate the seven SDE Swing Telegram UI previews without network/send."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.job_runner.reports import final_watchlist_payloads, market_outlook_payload, post_market_payloads
from modules.job_runner.runtime import load_context
from modules.telegram.professional_ui import UiConfig, format_data_warning


def latest_file(pattern: str) -> Path | None:
    files = [path for path in ROOT.glob(pattern) if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def read_json(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate professional Telegram UI previews")
    parser.add_argument("--trade-date", default=datetime.now(ZoneInfo("Asia/Jakarta")).date().isoformat())
    parser.add_argument("--output-dir", default="data/output/telegram_ui_preview/scheduled")
    parser.add_argument("--include-warning-example", action="store_true", default=True)
    args = parser.parse_args()

    output = (ROOT / args.output_dir).resolve()
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)

    ctx = load_context(
        job="post_market",
        config_path="config/pipeline.json",
        scheduler_config_path="config/scheduler.json",
        trade_date=args.trade_date,
        dry_run=True,
        preview_existing=True,
        no_telegram=False,
        force=True,
        debug=False,
    )

    final_manifest_path = latest_file("data/output/manifests/SWING_RUN_MANIFEST_SDE-FINAL-WATCHLIST-*.json")
    if final_manifest_path is None:
        final_manifest_path = latest_file("data/output/manifests/SWING_RUN_MANIFEST_*.json")
    final_manifest = read_json(final_manifest_path)

    global_path = ROOT / "data/output/global_market" / args.trade_date / "global_market_snapshot.json"
    if not global_path.exists():
        global_path = latest_file("data/output/global_market/*/global_market_snapshot.json") or global_path
    global_snapshot = read_json(global_path)

    payloads = []
    payloads += market_outlook_payload(ctx, global_snapshot)
    payloads += [
        payload for payload in post_market_payloads(ctx, final_manifest)
        if payload.report_type in {"closing_bell", "daily_signal_recap"}
    ]
    payloads += final_watchlist_payloads(ctx, final_manifest)

    telegram_cfg = read_json(ROOT / "config/telegram.json")
    cfg = UiConfig.from_dict(telegram_cfg.get("telegram_ui", {}))
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

    order = [
        ("01_market_outlook.txt", "market_outlook"),
        ("02_daily_signal_recap.txt", "daily_signal_recap"),
        ("03_closing_bell.txt", "closing_bell"),
        ("04_final_watchlist.txt", "final_watchlist"),
        ("05_signal_detail_example.txt", "signal_detail"),
        ("07_position_evaluation.txt", "position_evaluation"),
    ]
    entries: list[dict] = []
    for filename, report_type in order:
        match = next((payload for payload in payloads if payload.report_type == report_type), None)
        if not match:
            continue
        path = output / filename
        path.write_text(match.text.strip() + "\n", encoding="utf-8")
        entries.append({
            "file": filename,
            "report_type": report_type,
            "chars": len(match.text),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    if args.include_warning_example:
        path = output / "06_data_warning_stale_example.txt"
        path.write_text(warning_text.strip() + "\n", encoding="utf-8")
        entries.append({
            "file": path.name,
            "report_type": "data_warning",
            "chars": len(warning_text),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    entries.sort(key=lambda item: item["file"])
    source_manifest_ref = final_manifest_path.relative_to(ROOT).as_posix() if final_manifest_path else ""
    try:
        global_snapshot_ref = global_path.relative_to(ROOT).as_posix()
    except ValueError:
        global_snapshot_ref = str(global_path)
    manifest = {
        "trade_date": args.trade_date,
        "source_manifest": source_manifest_ref,
        "global_snapshot": global_snapshot_ref,
        "parse_mode": cfg.parse_mode,
        "telegram_send": "SKIPPED_DRY_RUN",
        "report_count": len(entries),
        "reports": entries,
    }
    (output / "PREVIEW_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {len(entries)} Telegram UI previews in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
