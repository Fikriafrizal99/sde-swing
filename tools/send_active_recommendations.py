#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.analytics import outcome_tracker as tracker
from modules.analytics.lifecycle_presentation import (
    actionable_snapshot,
    build_active_message,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send canonical Active Recommendations")
    parser.add_argument("--output-dir", default=str(tracker.DEFAULT_OUTPUT))
    parser.add_argument("--telegram-config", default="config/telegram.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    active_csv = output_dir / "ACTIVE_RECOMMENDATIONS.csv"
    message_path = output_dir / "ACTIVE_RECOMMENDATIONS_TELEGRAM.txt"

    if not active_csv.exists():
        raise FileNotFoundError(
            "ACTIVE_RECOMMENDATIONS.csv belum tersedia. Jalankan Update Outcome terlebih dahulu."
        )

    active = actionable_snapshot(pd.read_csv(active_csv, low_memory=False))
    if active.empty:
        print("Tidak ada rekomendasi aktif. Telegram tidak dikirim.")
        return 0

    message = build_active_message(active)
    if message.count("<b>") != message.count("</b>") or message.count("<pre>") != message.count("</pre>"):
        raise RuntimeError("Active Recommendations menghasilkan HTML Telegram yang tidak seimbang.")

    output_dir.mkdir(parents=True, exist_ok=True)
    message_path.write_text(message, encoding="utf-8")
    tracker.send_telegram(
        message_path,
        Path(args.telegram_config),
        Path(args.scheduler_config),
        args.dry_run,
    )
    print("Active recommendations berhasil diproses.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
