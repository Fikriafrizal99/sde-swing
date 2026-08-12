#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.analytics import outcome_tracker as tracker
from modules.telegram.idx_price import fmt_idx_price


def _value(event: Mapping[str, Any], key: str, default: Any = "") -> Any:
    try:
        return event[key]
    except Exception:
        return default


def _event_date(value: Any) -> str:
    raw = str(value or "")
    try:
        return pd.to_datetime(raw).strftime("%d %b %Y")
    except Exception:
        return raw or "-"


def _event_label(event_type: str) -> tuple[str, str]:
    return {
        "ENTRY_TRIGGERED": ("📈", "ENTRY TRIGGERED"),
        "TP1_HIT": ("🎯", "TP1 HIT"),
        "TP2_HIT": ("🚀", "TP2 HIT"),
        "STOP_LOSS_HIT": ("🛑", "STOP LOSS HIT"),
        "MAX_HOLD_EXIT": ("⏱", "MAX HOLD EXIT"),
        "EXPIRED": ("⌛", "SIGNAL EXPIRED"),
        "INVALIDATED_BEFORE_ENTRY": ("🚫", "SIGNAL INVALIDATED"),
    }.get(event_type, ("🔄", event_type.replace("_", " ") or "STATUS CHANGE"))


def build_lifecycle_message(events: list[Mapping[str, Any]], *, max_events: int = 20) -> str:
    material = tracker.material_lifecycle_events(events)
    if not material:
        return ""

    limit = max(int(max_events or 1), 1)
    lines = [
        "🔔 <b>LIFECYCLE DIGEST</b>",
        f"📊 <b>{len(material)} perubahan material</b>",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    body: list[str] = []

    for event in material[:limit]:
        symbol = str(_value(event, "symbol") or "").strip().upper()
        event_type = str(_value(event, "event_type") or "").strip().upper()
        reason = str(_value(event, "event_reason") or event_type or "").replace("_", " ").strip()
        price = fmt_idx_price(_value(event, "event_price"), anchor_price=_value(event, "event_price"))
        date_text = _event_date(_value(event, "event_date"))
        emoji, label = _event_label(event_type)

        block = [f"{emoji} {symbol} | {label}"]
        if event_type == "ENTRY_TRIGGERED":
            block.extend([
                f"Trigger   {reason.title() or '-'}",
                f"Price     {price}",
            ])
        elif event_type in {"TP1_HIT", "TP2_HIT", "STOP_LOSS_HIT", "MAX_HOLD_EXIT"}:
            block.append(f"Exit      {price}")
        elif event_type in {"EXPIRED", "INVALIDATED_BEFORE_ENTRY"}:
            if event_type == "EXPIRED":
                expiry = tracker.waiting_expiry_sessions(_value(event, "trigger_expiry_days", 7))
                rec = tracker.as_int(_value(event, "recommendation_count"), 0)
                original = _event_date(_value(event, "original_signal_date", _value(event, "event_date")))
                block.extend([
                    f"Waiting   {expiry} sesi perdagangan tanpa entry trigger",
                    f"REC       {rec}x",
                    f"Original  {original}",
                ])
            else:
                block.extend([
                    f"Reason    {reason.title() or '-'}",
                    f"Price     {price}",
                ])
        else:
            previous = str(_value(event, "previous_status") or "-").replace("_", " ")
            new = str(_value(event, "new_status") or "-").replace("_", " ")
            block.append(f"Status    {previous} -> {new}")
            if reason:
                block.append(f"Reason    {reason.title()}")
            if price != "-":
                block.append(f"Price     {price}")
        block.append(f"Date      {date_text}")

        if body:
            body.append("")
        body.extend(block)

    lines.extend(["", "<pre>" + html.escape("\n".join(body)) + "</pre>"])
    if len(material) > limit:
        lines.extend([
            "",
            f"… {len(material) - limit} perubahan lain tersimpan di ledger.",
        ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send compact monospace lifecycle digest")
    parser.add_argument("--db", default=str(tracker.DEFAULT_DB))
    parser.add_argument("--output-dir", default=str(tracker.DEFAULT_OUTPUT))
    parser.add_argument("--telegram-config", default="config/telegram.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--max-events", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    message_path = output_dir / "LIFECYCLE_DIGEST_TELEGRAM.txt"

    conn = tracker.connect(db_path)
    try:
        events = tracker.material_lifecycle_events(tracker.pending_lifecycle_events(conn))
    finally:
        conn.close()

    limit = max(int(args.max_events or 1), 1)
    message = build_lifecycle_message(events, max_events=limit)
    message_path.write_text(message, encoding="utf-8")

    if not events:
        print("Tidak ada perubahan lifecycle material. Telegram tidak dikirim.")
        return 0
    if message.count("<b>") != message.count("</b>") or message.count("<pre>") != message.count("</pre>"):
        raise RuntimeError("Lifecycle Digest menghasilkan HTML Telegram yang tidak seimbang.")
    if args.dry_run:
        print(message)
        return 0

    tracker.send_telegram(
        message_path,
        Path(args.telegram_config),
        Path(args.scheduler_config),
        False,
    )
    event_ids = [tracker.norm_text(event["event_id"]) for event in events[:limit]]
    marked = tracker.mark_lifecycle_events_notified(db_path, event_ids)
    print(f"Lifecycle digest terkirim: {len(events)} event, acknowledged={marked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
