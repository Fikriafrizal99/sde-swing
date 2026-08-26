from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from modules.job_runner.delivery import _idempotency_key, _idempotency_store
from modules.job_runner.post_market_delivery_guard import deliver_post_market_hardened
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import RunnerContext


ROOT = Path(__file__).resolve().parents[1]


def make_ctx(tmp: Path, run_id: str) -> RunnerContext:
    scheduler_config = {
        "paths": {
            "preview_root": str(tmp / "previews"),
            "job_status_root": str(tmp / "status"),
            "state_root": str(tmp / "state"),
            "job_log": str(tmp / "job.log"),
        },
        "delivery": {
            "idempotency_index": str(tmp / "state" / "idempotency.json"),
            "delivery_log": str(tmp / "state" / "delivery.jsonl"),
            "failed_root": str(tmp / "failed"),
            "idempotency_reservation_ttl_seconds": 60,
            "topic_routing": {},
        },
    }
    config = {"paths": {"telegram_config": str(tmp / "telegram.json")}}
    return RunnerContext(
        job="post_market",
        config_path=ROOT / "config" / "pipeline.json",
        scheduler_config_path=ROOT / "config" / "scheduler.json",
        trade_date=date(2026, 8, 26),
        run_id=run_id,
        config=config,
        scheduler_config=scheduler_config,
        calendar_config={"holidays": [], "special_trading_days": []},
    )


def failed_after_remote_ack(ctx: RunnerContext, payload: ReportPayload, *, part_count: int, sent_count: int) -> dict:
    store = _idempotency_store(ctx)
    key = _idempotency_key(ctx, payload)
    reservation = store.reserve(key, ctx.run_id)
    assert reservation.acquired
    ids = list(range(700, 700 + sent_count))
    event = {
        "run_id": ctx.run_id,
        "job": ctx.job,
        "trade_date": ctx.trade_date.isoformat(),
        "report_type": payload.report_type,
        "status": "FAILED",
        "idempotency_key": key,
        "idempotency_attempt_id": reservation.attempt_id,
        "part_count": part_count,
        "telegram_message_ids": ids,
        "sent_parts_before_failure": sent_count,
        "error": "LOCAL_FINALIZATION_FAILED",
    }
    assert store.fail(reservation, event, event["error"])
    return event


def test_complete_post_market_remote_ack_is_terminal_and_duplicate_suppressed() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = make_ctx(tmp, "RUN-POST-ACK")
        payload = ReportPayload("post_market", "post_market.txt", "POST MARKET")
        failed = failed_after_remote_ack(ctx, payload, part_count=1, sent_count=1)

        with patch(
            "modules.job_runner.post_market_delivery_guard._baseline_deliver",
            return_value=[failed],
        ):
            result = deliver_post_market_hardened(ctx, [payload])

        assert result[0]["status"] == "DELIVERY_STATE_UNCERTAIN"
        assert result[0]["remote_acceptance_confirmed"] is True
        assert result[0]["automatic_retry_suppressed"] is True
        assert result[0]["idempotency_sealed_after_remote_acceptance"] is True

        store = _idempotency_store(ctx)
        key = _idempotency_key(ctx, payload)
        assert store.can_send(key) == (False, "DUPLICATE_SUPPRESSED")
        with closing(sqlite3.connect(store.database_path)) as conn:
            state = conn.execute(
                "SELECT status FROM telegram_delivery_state WHERE idempotency_key=?",
                (key,),
            ).fetchone()[0]
            attempt = conn.execute(
                "SELECT status FROM telegram_delivery_attempts WHERE attempt_id=?",
                (failed["idempotency_attempt_id"],),
            ).fetchone()[0]
        assert state == "SENT"
        assert attempt == "SENT_UNCERTAIN"


def test_post_market_failure_without_remote_ack_remains_retryable() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = make_ctx(tmp, "RUN-POST-NO-ACK")
        payload = ReportPayload("post_market", "post_market.txt", "POST MARKET")
        store = _idempotency_store(ctx)
        key = _idempotency_key(ctx, payload)
        reservation = store.reserve(key, ctx.run_id)
        assert reservation.acquired
        failed = {
            "run_id": ctx.run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "report_type": "post_market",
            "status": "FAILED",
            "idempotency_key": key,
            "idempotency_attempt_id": reservation.attempt_id,
            "part_count": 1,
            "telegram_message_ids": [],
            "sent_parts_before_failure": 0,
            "error": "NETWORK_FAILED_BEFORE_ACK",
        }
        assert store.fail(reservation, failed, failed["error"])

        with patch(
            "modules.job_runner.post_market_delivery_guard._baseline_deliver",
            return_value=[failed],
        ):
            result = deliver_post_market_hardened(ctx, [payload])

        assert result[0]["status"] == "FAILED"
        assert store.can_send(key)[0] is True


def test_partial_multi_part_post_market_is_not_sealed() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ctx = make_ctx(tmp, "RUN-POST-PARTIAL")
        payload = ReportPayload("post_market", "post_market.txt", "POST MARKET")
        failed = failed_after_remote_ack(ctx, payload, part_count=2, sent_count=1)

        with patch(
            "modules.job_runner.post_market_delivery_guard._baseline_deliver",
            return_value=[failed],
        ):
            result = deliver_post_market_hardened(ctx, [payload])

        assert result[0]["status"] == "FAILED"
        store = _idempotency_store(ctx)
        assert store.can_send(_idempotency_key(ctx, payload))[0] is True


def test_market_first_shim_activates_only_hardened_delivery_wrapper() -> None:
    source = (ROOT / "run_sde_job_integrated_market_first.py").read_text(encoding="utf-8")
    assert "integrated.deliver = deliver_post_market_hardened" in source
    assert "integrated.post_market_payloads = post_market_live_payloads" in source
