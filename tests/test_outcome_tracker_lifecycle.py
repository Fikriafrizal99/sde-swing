from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from modules.analytics.outcome_tracker import (
    connect,
    export_reports,
    is_material_lifecycle_event,
    lifecycle_telegram,
    mark_lifecycle_events_notified,
    material_lifecycle_events,
    pending_lifecycle_events,
    record_lifecycle_event,
    record_portfolio_buy,
    record_portfolio_sell,
    register_decision_file,
    update_outcomes,
)
from modules.job_runner.delivery import deliver
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import RunnerContext


def _write_signal_files(root: Path, *, decision: str = "BUY", plan_status: str = "ACCEPT") -> tuple[Path, Path]:
    decisions = root / "FINAL_DECISION_V3.csv"
    plans = root / "ENTRY_PLANS.csv"
    pd.DataFrame([{
        "Symbol": "BBCA",
        "Decision_V3": decision,
        "Technical_Data_Date": "2026-08-01",
        "Final_Score_V3": 82,
        "Data_Quality_Status": "VALID",
        "Setup_Type": "BREAKOUT",
    }]).to_csv(decisions, index=False)
    pd.DataFrame([{
        "Symbol": "BBCA",
        "Plan_Status": plan_status,
        "Setup_Type": "BREAKOUT",
        "Reference_Close": 103,
        "Entry_Zone_Low": 100,
        "Entry_Zone_High": 105,
        "Initial_Stop": 95,
        "Target_1": 110,
        "Target_2": 115,
        "Max_Hold_Days": 3,
    }]).to_csv(plans, index=False)
    return decisions, plans


def _write_prices(root: Path, highs: list[float]) -> Path:
    path = root / "BBCA.csv"
    dates = pd.date_range("2026-08-04", periods=len(highs), freq="D")
    pd.DataFrame({
        "Date": dates,
        "Open": [103] * len(highs),
        "High": highs,
        "Low": [101] * len(highs),
        "Close": [104] * len(highs),
        "Volume": [1000] * len(highs),
    }).to_csv(path, index=False)
    return path


def test_buy_signal_is_deduplicated_and_downgrade_only_updates_scan_state(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    decisions, plans = _write_signal_files(tmp_path)
    conn = connect(db)
    first = register_decision_file(conn, decisions, plans, "RUN-1", "2026-08-01")
    second = register_decision_file(conn, decisions, plans, "RUN-2", "2026-08-01")
    assert first.inserted == 1
    assert second.inserted == 0
    assert conn.execute("SELECT COUNT(*) FROM signal_outcome_ledger").fetchone()[0] == 1

    downgraded, _ = _write_signal_files(tmp_path, decision="WATCH")
    register_decision_file(conn, downgraded, plans, "RUN-3", "2026-08-02")
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "WAITING_TRIGGER"
    assert row["latest_scan_status"] == "WATCH"

    empty = tmp_path / "empty.csv"
    pd.DataFrame().to_csv(empty, index=False)
    register_decision_file(conn, empty, plans, "RUN-4", "2026-08-03")
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "WAITING_TRIGGER"
    assert row["latest_scan_status"] == "NOT_IN_LATEST_SCAN"
    conn.close()


def test_waiting_trigger_opens_then_closes_at_tp1_with_events(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    historical = tmp_path / "prices"
    historical.mkdir()
    decisions, plans = _write_signal_files(tmp_path)
    _write_prices(historical, [106, 108])
    conn = connect(db)
    register_decision_file(conn, decisions, plans, "RUN-1", "2026-08-01")
    result = update_outcomes(conn, historical)
    assert result["updated"] == 1
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "OPEN"
    assert row["entry_date"] == "2026-08-04"
    assert {item["event_type"] for item in pending_lifecycle_events(conn)} >= {"SIGNAL_CREATED", "ENTRY_TRIGGERED"}

    _write_prices(historical, [106, 111, 108])
    update_outcomes(conn, historical)
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "CLOSED"
    assert row["final_outcome"] == "WIN"
    assert row["exit_reason"] == "TP1_HIT"
    event_types = {item["event_type"] for item in pending_lifecycle_events(conn)}
    assert {"TP1_HIT", "CLOSED"} <= event_types
    conn.close()


def test_invalid_before_entry_is_not_a_loss_and_state_survives_restart(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    decisions, plans = _write_signal_files(tmp_path, plan_status="REJECT")
    conn = connect(db)
    register_decision_file(conn, decisions, plans, "RUN-INVALID", "2026-08-01")
    row = conn.execute("SELECT * FROM signal_outcome_ledger").fetchone()
    assert row["current_status"] == "INVALIDATED_BEFORE_ENTRY"
    assert row["final_outcome"] == "INVALIDATED"
    assert row["final_outcome"] != "LOSS"
    conn.close()

    reopened = connect(db)
    persisted = reopened.execute("SELECT current_status,final_outcome FROM signal_outcome_ledger").fetchone()
    assert tuple(persisted) == ("INVALIDATED_BEFORE_ENTRY", "INVALIDATED")
    event_ids = [item["event_id"] for item in pending_lifecycle_events(reopened)]
    assert mark_lifecycle_events_notified(db, event_ids) == len(event_ids)
    assert pending_lifecycle_events(reopened) == []
    reopened.close()


def test_actual_portfolio_buy_is_idempotent_and_sellable(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    first = record_portfolio_buy(
        conn, symbol="BBCA", quantity=2, buy_price=8950, buy_date="2026-08-04", notes="watchlist",
    )
    second = record_portfolio_buy(
        conn, symbol="BBCA", quantity=2, buy_price=8950, buy_date="2026-08-04", notes="watchlist",
    )
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM portfolio_positions").fetchone()[0] == 1
    record_portfolio_sell(conn, position_id=first, sell_price=9125, sell_date="2026-08-05")
    row = conn.execute("SELECT current_status,realized_return_pct FROM portfolio_positions").fetchone()
    assert row["current_status"] == "CLOSED"
    assert row["realized_return_pct"] > 0
    conn.close()


def test_export_reports_writes_lifecycle_and_portfolio_artifacts(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    output = tmp_path / "performance"
    conn = connect(db)
    export_reports(conn, output, tmp_path / "prices")
    conn.close()
    for name in (
        "SIGNAL_OUTCOME_LEDGER.csv",
        "ACTIVE_RECOMMENDATIONS.csv",
        "LIFECYCLE_EVENTS.csv",
        "PORTFOLIO_POSITIONS.csv",
        "ACTIVE_RECOMMENDATIONS_TELEGRAM.txt",
        "STATUS_CHANGES_TELEGRAM.txt",
    ):
        assert (output / name).exists()


def test_export_reports_formats_active_recommendation_prices(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    output = tmp_path / "performance"
    historical = tmp_path / "prices"
    historical.mkdir()
    decisions, plans = _write_signal_files(tmp_path)
    _write_prices(historical, [106, 108])
    conn = connect(db)
    register_decision_file(conn, decisions, plans, "RUN-1", "2026-08-01")
    update_outcomes(conn, historical)

    export_reports(conn, output, historical)
    telegram = (output / "ACTIVE_RECOMMENDATIONS_TELEGRAM.txt").read_text(encoding="utf-8")
    assert "BBCA" in telegram
    assert "Entry mesin  : 103" in telegram
    assert "Harga kini   : 104" in telegram
    assert "TP1          : 110" in telegram
    conn.close()


def _delivery_context(tmp_path: Path, db: Path) -> RunnerContext:
    return RunnerContext(
        job="final_watchlist",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=date(2026, 8, 4),
        run_id="DELIVERY-TEST",
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


def test_lifecycle_event_is_marked_only_after_successful_telegram_delivery(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    event = record_lifecycle_event(
        conn,
        signal_id="SIG-1",
        symbol="BBCA",
        event_type="ENTRY_TRIGGERED",
        previous_status="WAITING_TRIGGER",
        new_status="OPEN",
        event_date="2026-08-04",
        event_price=8950,
        event_reason="ENTRY_ZONE_TOUCH",
    )
    conn.commit()
    conn.close()
    ctx = _delivery_context(tmp_path, db)
    payload = ReportPayload("status_changes", "status_changes.txt", "status", signal_version=event)
    setattr(payload, "lifecycle_event_ids", (event,))
    with patch("modules.job_runner.delivery._send_telegram", side_effect=RuntimeError("network")):
        failed = deliver(ctx, [payload])
    assert failed[0]["status"] == "FAILED"
    check = connect(db)
    assert len(pending_lifecycle_events(check)) == 1
    check.close()

    with patch("modules.job_runner.delivery._send_telegram", return_value={"ok": True, "result": {"message_id": 1}}):
        sent = deliver(ctx, [payload])
    assert sent[0]["status"] == "SENT"
    check = connect(db)
    assert pending_lifecycle_events(check) == []
    check.close()


def test_material_lifecycle_filter_excludes_reconfirmation_and_companion_close() -> None:
    events = [
        {"event_type": "SIGNAL_RECONFIRMED", "event_reason": "SCAN:BUY CANDIDATE"},
        {"event_type": "CLOSED", "event_reason": "TP1_HIT"},
        {"event_type": "TP1_HIT", "event_reason": "TP1_HIT"},
        {"event_type": "ENTRY_TRIGGERED", "event_reason": "CLOSE_ABOVE"},
    ]
    assert [event["event_type"] for event in material_lifecycle_events(events)] == [
        "TP1_HIT", "ENTRY_TRIGGERED"
    ]
    assert is_material_lifecycle_event(events[-1]) is True
    assert is_material_lifecycle_event(events[0]) is False
    assert is_material_lifecycle_event({"event_type": "CLOSED", "event_reason": "OTHER"}) is False


def test_lifecycle_digest_marks_only_material_events_after_success(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    material_id = record_lifecycle_event(
        conn,
        signal_id="SIG-MATERIAL",
        symbol="BBCA",
        event_type="ENTRY_TRIGGERED",
        previous_status="WAITING_TRIGGER",
        new_status="OPEN",
        event_date="2026-08-05",
        event_price=8950,
        event_reason="CLOSE_ABOVE",
    )
    noisy_id = record_lifecycle_event(
        conn,
        signal_id="SIG-NOISY",
        symbol="BBCA",
        event_type="SIGNAL_RECONFIRMED",
        previous_status="OPEN",
        new_status="OPEN",
        event_date="2026-08-05",
        event_price=8950,
        event_reason="SCAN:BUY CANDIDATE",
    )
    conn.commit()
    conn.close()
    args = SimpleNamespace(
        db=str(db), output_dir=str(tmp_path / "performance"),
        telegram_config=str(tmp_path / "telegram.json"),
        scheduler_config=str(tmp_path / "scheduler.json"),
        max_events=20, dry_run=False,
    )
    with patch("modules.analytics.outcome_tracker.send_telegram") as send:
        assert lifecycle_telegram(args) == 0
    send.assert_called_once()
    check = connect(db)
    pending = pending_lifecycle_events(check)
    assert [row["event_id"] for row in pending] == [noisy_id]
    assert material_id not in {row["event_id"] for row in pending}
    check.close()
    text = (tmp_path / "performance" / "LIFECYCLE_DIGEST_TELEGRAM.txt").read_text(encoding="utf-8")
    assert "CLOSE_ABOVE" in text
    assert "SIGNAL_RECONFIRMED" not in text


def test_lifecycle_digest_keeps_events_pending_when_send_fails(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    event_id = record_lifecycle_event(
        conn,
        signal_id="SIG-FAIL",
        symbol="BBCA",
        event_type="TP1_HIT",
        previous_status="OPEN",
        new_status="CLOSED",
        event_date="2026-08-05",
        event_price=9100,
        event_reason="TP1_HIT",
    )
    conn.commit()
    conn.close()
    args = SimpleNamespace(
        db=str(db), output_dir=str(tmp_path / "performance"),
        telegram_config=str(tmp_path / "telegram.json"),
        scheduler_config=str(tmp_path / "scheduler.json"),
        max_events=20, dry_run=False,
    )
    with patch("modules.analytics.outcome_tracker.send_telegram", side_effect=RuntimeError("network")):
        try:
            lifecycle_telegram(args)
        except RuntimeError:
            pass
    check = connect(db)
    assert event_id in {row["event_id"] for row in pending_lifecycle_events(check)}
    check.close()


def test_lifecycle_digest_does_not_send_when_only_noisy_events_are_pending(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    noisy_id = record_lifecycle_event(
        conn,
        signal_id="SIG-NOISY",
        symbol="BBCA",
        event_type="SIGNAL_RECONFIRMED",
        previous_status="OPEN",
        new_status="OPEN",
        event_date="2026-08-05",
        event_price=8950,
        event_reason="SCAN:BUY CANDIDATE",
    )
    conn.commit()
    conn.close()
    args = SimpleNamespace(
        db=str(db), output_dir=str(tmp_path / "performance"),
        telegram_config=str(tmp_path / "telegram.json"),
        scheduler_config=str(tmp_path / "scheduler.json"),
        max_events=20, dry_run=False,
    )
    with patch("modules.analytics.outcome_tracker.send_telegram") as send:
        assert lifecycle_telegram(args) == 0
    send.assert_not_called()
    check = connect(db)
    assert [row["event_id"] for row in pending_lifecycle_events(check)] == [noisy_id]
    check.close()
    assert (tmp_path / "performance" / "LIFECYCLE_DIGEST_TELEGRAM.txt").read_text(encoding="utf-8") == ""


def test_lifecycle_digest_acknowledges_only_events_in_bounded_message(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = connect(db)
    first_id = record_lifecycle_event(
        conn,
        signal_id="SIG-FIRST",
        symbol="BBCA",
        event_type="ENTRY_TRIGGERED",
        previous_status="WAITING_TRIGGER",
        new_status="OPEN",
        event_date="2026-08-04",
        event_price=8950,
        event_reason="CLOSE_ABOVE",
    )
    second_id = record_lifecycle_event(
        conn,
        signal_id="SIG-SECOND",
        symbol="TLKM",
        event_type="TP1_HIT",
        previous_status="OPEN",
        new_status="CLOSED",
        event_date="2026-08-05",
        event_price=3000,
        event_reason="TP1_HIT",
    )
    conn.commit()
    conn.close()
    args = SimpleNamespace(
        db=str(db), output_dir=str(tmp_path / "performance"),
        telegram_config=str(tmp_path / "telegram.json"),
        scheduler_config=str(tmp_path / "scheduler.json"),
        max_events=1, dry_run=False,
    )
    with patch("modules.analytics.outcome_tracker.send_telegram") as send:
        assert lifecycle_telegram(args) == 0
    send.assert_called_once()
    check = connect(db)
    assert [row["event_id"] for row in pending_lifecycle_events(check)] == [second_id]
    assert first_id not in {row["event_id"] for row in pending_lifecycle_events(check)}
    check.close()
