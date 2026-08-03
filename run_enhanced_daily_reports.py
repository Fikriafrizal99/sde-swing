#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from modules.ai_interpretation import GeminiInterpreter
from modules.job_runner.enhanced_daily_delivery import EnhancedDailyDelivery
from modules.job_runner.enhanced_daily_reports import EnhancedDailyReportBuilder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and deliver SDE Swing daily Telegram UI with optional Gemini interpretation."
    )
    parser.add_argument(
        "--bundle",
        required=True,
        help="Path to deterministic JSON bundle containing market_outlook, post_market, broker_summary, broker_multiday, and final_watchlist.",
    )
    parser.add_argument("--telegram-config", default="config/telegram.json")
    parser.add_argument("--output-root", default="data/output")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--disable-ai", action="store_true")
    parser.add_argument("--max-watchlist", type=int, default=5)
    return parser.parse_args()


def read_json(path: str | Path) -> dict:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {target}")
    with target.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Bundle wajib berupa JSON object")
    return value


def main() -> int:
    args = parse_args()
    bundle = read_json(args.bundle)
    telegram_config = read_json(args.telegram_config)
    interpreter = GeminiInterpreter(enabled=not args.disable_ai)
    builder = EnhancedDailyReportBuilder(
        output_root=args.output_root,
        interpreter=interpreter,
        max_watchlist_messages=args.max_watchlist,
    )
    artifacts = builder.build_all(bundle)

    preview_root = Path(args.output_root) / "enhanced_daily_preview"
    preview_root.mkdir(parents=True, exist_ok=True)
    for index, artifact in enumerate(artifacts, 1):
        if artifact.text:
            symbol = f"_{artifact.symbol}" if artifact.symbol else ""
            path = preview_root / f"{index:02d}_{artifact.report_type}{symbol}.txt"
            path.write_text(artifact.text + "\n", encoding="utf-8")

    delivery = EnhancedDailyDelivery(telegram_config).deliver(artifacts, dry_run=args.dry_run)
    manifest = {
        "ai_configured": interpreter.configured,
        "artifact_count": len(artifacts),
        "delivery": delivery,
    }
    manifest_path = preview_root / "delivery_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [item for item in delivery if item.get("status") == "FAILED"]
    print(f"Artifacts: {len(artifacts)}")
    print(f"Gemini configured: {interpreter.configured}")
    print(f"Manifest: {manifest_path}")
    if failed:
        for item in failed:
            print(f"FAILED {item.get('report_type')}: {item.get('error')}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
