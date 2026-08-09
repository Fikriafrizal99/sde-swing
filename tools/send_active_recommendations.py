#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.analytics import outcome_tracker as tracker


def _int_or_zero(value: Any) -> int:
    try:
        number = float(value)
        if math.isnan(number):
            return 0
        return int(number)
    except Exception:
        return 0


def build_active_message(active: pd.DataFrame) -> str:
    lines = [
        "📌 <b>REKOMENDASI AKTIF</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"Total aktif: {len(active)} saham",
    ]
    if active.empty:
        return "\n".join(lines + ["", "Belum ada rekomendasi aktif."])

    groups = (
        ("OPEN", "📈 <b>ACTIVE</b>"),
        ("WAITING_TRIGGER", "⏳ <b>WAITING ENTRY</b>"),
    )
    statuses = active["current_status"].astype(str).str.upper()
    for status, heading in groups:
        subset = active[statuses == status]
        if subset.empty:
            continue
        lines.extend(["", heading])
        for _, row in subset.iterrows():
            symbol = html.escape(str(row.get("symbol") or ""))
            signal_date = html.escape(str(row.get("signal_date") or ""))
            current = tracker.fmt_price(row.get("current_price"))
            lines.extend(["", f"<b>{symbol}</b>"])
            if status == "OPEN":
                entry = tracker.fmt_price(row.get("entry_price") or row.get("reference_price"))
                pnl = tracker.fmt(row.get("simulated_return_pct"), 2, "%")
                lines.extend([
                    f"Sinyal       : {signal_date}",
                    f"Entry mesin  : {entry}",
                    f"Harga kini   : {current}",
                    f"P/L simulasi : {pnl}",
                    f"TP1          : {tracker.fmt_price(row.get('take_profit_1'))}",
                    f"TP2          : {tracker.fmt_price(row.get('take_profit_2'))}",
                    f"SL           : {tracker.fmt_price(row.get('stop_loss'))}",
                    f"Umur posisi  : {_int_or_zero(row.get('age_sessions'))} sesi",
                ])
            else:
                low = tracker.fmt_price(row.get("entry_zone_low"), missing="")
                high = tracker.fmt_price(row.get("entry_zone_high"), missing="")
                entry = f"{low}–{high}" if low and high else (low or high or "belum tersedia")
                scan_status = html.escape(str(row.get("latest_scan_status") or "NOT_IN_LATEST_SCAN"))
                lines.extend([
                    f"Sinyal      : {signal_date}",
                    f"Entry       : {entry}",
                    f"Harga kini  : {current}",
                    f"Status scan : {scan_status}",
                    f"Umur sinyal : {_int_or_zero(row.get('age_sessions'))} sesi",
                ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send Active Recommendations with valid Telegram HTML")
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
        raise FileNotFoundError("ACTIVE_RECOMMENDATIONS.csv belum tersedia. Jalankan Update Outcome terlebih dahulu.")

    active = pd.read_csv(active_csv, low_memory=False)
    if active.empty:
        print("Tidak ada rekomendasi aktif. Telegram tidak dikirim.")
        return 0

    message = build_active_message(active)
    # Guard against the historical malformed pattern that caused Telegram 400:
    # closing </b> tags appearing where an opening <b> tag was intended.
    if message.count("<b>") != message.count("</b>") or any(
        line.lstrip().startswith("</b>") for line in message.splitlines()
    ):
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
