from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

from modules.analytics.outcome_tracker import connect, pending_lifecycle_events, record_lifecycle_event
from modules.job_runner.delivery import _lifecycle_ack_ids, deliver
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import RunnerContext


def _context(tmp_path: Path, db: Path) -> RunnerContext:
    return RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 12),
        run_id="LIVE-DIGEST-TEST",
        config={"paths": {"swing_database": str(db)}},
        scheduler_config={
            "paths": {
                "state_root": str(tmp_path / "state"),
                "job_status_root": str(tmp_path / "status"),
                "preview_root": str(tmp_path / "previews"),
            },
            "delivery": {
                "idempotency_index": str(tmp_path / "state" / "idempotency.json"),
                "delivery_log": str(tmp_path / "state" / "delivery.jsonl"),
                "failed_root": str(tmp_path / "failed"),
            },
        },
        calendar_config={},
    )


def _digest_text(labels: list[str]) -> str:
    return "\n".join([
        "🔔 <b>SDE SWING — LIFECYCLE DIGEST</b>",
        *[f"◆ <b>{label}</b>" for label in labels],
    ])


def test_status_changes_ack_is_bounded_by_rendered_events() -> None:
    payload = ReportPayload(
        "status_changes",
        "status_changes.txt",
        _digest_text([f"EVENT {index}" for index in range(20)]),
        "status",
    )
    setattr(payload, "lifecycle_event_ids", tuple(f"EVENT-{index:02d}" for index in range(25)))
    bounded = _lifecycle_ack_ids(payload, payload.text)
    assert bounded == tuple(f"EVENT-{index:02d}" for index in range(20))


def test_live_digest_does_not_ack_hidden_tp_or_stop_events(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    ids: list[str] = []
    event_types: list[str] = []
    for index in range(25):
        event_type = "ENTRY_TRIGGERED"
        reason = "CLOSE_ABOVE"
        if index == 20:
            event_type = "TP1_HIT"
            reason = "TP1_HIT"
        elif index == 21:
            event_type = "STOP_LOSS_HIT"
            reason = "STOP_LOSS_HIT"
        event_types.append(event_type)
        ids.append(record_lifecycle_event(
            conn,
            signal_id=f"SIG-{index:02d}",
            symbol=f"T{index:03d}",
            event_type=event_type,
            previous_status=(
                "WAITING_TRIGGER" if event_type == "ENTRY_TRIGGERED" else "OPEN"
            ),
            new_status="CLOSED" if event_type == "STOP_LOSS_HIT" else "OPEN",
            event_date="2026-08-12",
            event_price=1000 + index,
            event_reason=reason,
        ))
    conn.commit()
    conn.close()

    ctx = _context(tmp_path, db)
    first = ReportPayload(
        "status_changes",
        "status_changes.txt",
        _digest_text(event_types[:20]),
        "status",
    )
    # Reproduce the historical live-bridge bug: the payload carries every
    # pending ID even though the bounded text renders only the first 20.
    setattr(first, "lifecycle_event_ids", tuple(ids))

    with patch("modules.job_runner.delivery._send_telegram", return_value={"ok": True, "result": {"message_id": 1}}):
        result = deliver(ctx, [first])
    assert result[0]["status"] == "SENT"

    check = connect(db)
    remaining = pending_lifecycle_events(check)
    remaining_ids = [row["event_id"] for row in remaining]
    remaining_types = [row["event_type"] for row in remaining]
    check.close()

    assert remaining_ids == ids[20:]
    assert "TP1_HIT" in remaining_types
    assert "STOP_LOSS_HIT" in remaining_types

    second = ReportPayload(
        "status_changes",
        "status_changes_second.txt",
        _digest_text(event_types[20:]),
        "status",
    )
    setattr(second, "lifecycle_event_ids", tuple(ids[20:]))
    with patch("modules.job_runner.delivery._send_telegram", return_value={"ok": True, "result": {"message_id": 2}}):
        result = deliver(ctx, [second])
    assert result[0]["status"] == "SENT"

    check = connect(db)
    assert pending_lifecycle_events(check) == []
    check.close()
