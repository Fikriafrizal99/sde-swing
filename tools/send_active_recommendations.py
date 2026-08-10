#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.analytics import outcome_tracker as tracker
from modules.telegram.idx_price import (
    fmt_idx_price,
    fmt_idx_zone,
    snap_idx_price,
    snap_idx_zone,
)


def _int_or_zero(value: Any) -> int:
    try:
        number = float(value)
        if math.isnan(number):
            return 0
        return int(number)
    except Exception:
        return 0


def _first_price(*values: Any) -> float | None:
    for value in values:
        parsed = tracker.as_float(value)
        if parsed is not None:
            return parsed
    return None


def _fmt_pct(value: Any) -> str:
    try:
        number = float(value)
        if math.isnan(number):
            return "-"
    except Exception:
        return "-"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%".replace(".", ",")


def _source_plan(row: pd.Series) -> dict[str, Any]:
    raw = row.get("source_json")
    if raw is None:
        return {}
    try:
        payload = json.loads(str(raw))
    except Exception:
        return {}
    plan = payload.get("plan")
    return plan if isinstance(plan, dict) else {}


def _risk_reward(row: pd.Series) -> str:
    plan = _source_plan(row)
    candidates = (
        "Risk_Reward",
        "Risk_Reward_Ratio",
        "Risk_Reward_Final",
        "RiskReward",
        "RiskRewardRatio",
        "RR_Ratio",
        "RR",
        "R_R",
    )
    value: Any = None
    for key in candidates:
        if key in plan and str(plan.get(key) or "").strip():
            value = plan.get(key)
            break
    if value is None:
        return "-"
    text = str(value).strip()
    if ":" in text:
        _, right = text.split(":", 1)
        try:
            number = float(right.replace(",", "."))
            return f"1:{number:.2f}"
        except Exception:
            return text
    try:
        number = float(text.replace(",", "."))
        return f"1:{number:.2f}"
    except Exception:
        return text


def _gap_text(current: Any, low: Any, high: Any) -> str:
    current_value = snap_idx_price(current, anchor_price=current, mode="nearest")
    low_value, high_value = snap_idx_zone(low, high, anchor_price=current)
    if current_value is None or low_value is None or high_value is None:
        return "-"
    if low_value <= current_value <= high_value:
        return "IN RANGE"
    if current_value < low_value and low_value:
        pct = (current_value / low_value - 1.0) * 100.0
    elif high_value:
        pct = (current_value / high_value - 1.0) * 100.0
    else:
        return "-"
    return f"{pct:+.2f}%".replace(".", ",")


def build_active_message(active: pd.DataFrame) -> str:
    lines = [
        "📌 <b>REKOMENDASI AKTIF</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"Total aktif: {len(active)} saham",
    ]
    if active.empty:
        return "\n".join(lines + ["", "Belum ada rekomendasi aktif."])

    statuses = active["current_status"].astype(str).str.upper()
    for status, heading in (
        ("OPEN", "📈 <b>ACTIVE</b>"),
        ("WAITING_TRIGGER", "⏳ <b>WAITING ENTRY</b>"),
    ):
        subset = active[statuses == status]
        if subset.empty:
            continue
        lines.extend(["", heading])
        section_lines: list[str] = []

        for _, row in subset.iterrows():
            symbol = str(row.get("symbol") or "").strip().upper()
            current_raw = _first_price(row.get("current_price"))
            anchor = _first_price(current_raw, row.get("reference_price"), row.get("entry_price"))
            current = fmt_idx_price(current_raw, anchor_price=anchor)

            if status == "OPEN":
                entry_raw = _first_price(row.get("entry_price"), row.get("reference_price"))
                block = [
                    f"{symbol} | ACTIVE",
                    f"Entry   {fmt_idx_price(entry_raw, anchor_price=anchor)}",
                    f"Now     {current}",
                    f"P/L     {_fmt_pct(row.get('simulated_return_pct'))}",
                    f"TP1     {fmt_idx_price(row.get('take_profit_1'), anchor_price=anchor)}",
                    f"TP2     {fmt_idx_price(row.get('take_profit_2'), anchor_price=anchor)}",
                    f"SL      {fmt_idx_price(row.get('stop_loss'), anchor_price=anchor)}",
                    f"Age     {_int_or_zero(row.get('age_sessions'))}D",
                ]
            else:
                low = _first_price(row.get("entry_zone_low"))
                high = _first_price(row.get("entry_zone_high"))
                block = [
                    f"{symbol} | WAITING",
                    f"Entry   {fmt_idx_zone(low, high, anchor_price=anchor)}",
                    f"Now     {current}",
                    f"Gap     {_gap_text(current_raw, low, high)}",
                    f"SL      {fmt_idx_price(row.get('stop_loss'), anchor_price=anchor)}",
                    f"TP1     {fmt_idx_price(row.get('take_profit_1'), anchor_price=anchor)}",
                    f"TP2     {fmt_idx_price(row.get('take_profit_2'), anchor_price=anchor)}",
                    f"RR      {_risk_reward(row)}",
                    f"Age     {_int_or_zero(row.get('age_sessions'))}D",
                ]

            if section_lines:
                section_lines.append("")
            section_lines.extend(block)

        lines.append("<pre>" + html.escape("\n".join(section_lines)) + "</pre>")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send compact monospace Active Recommendations")
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
