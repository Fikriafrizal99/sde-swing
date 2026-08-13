from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from modules.job_runner.delivery import (
    _idempotency_key,
    _idempotency_store,
    deliver,
    should_send,
)
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import RunnerContext


ROOT = Path(__file__).resolve().parents[1]


def make_ctx(tmp: Path, run_id: str, *, force: bool = False) -> RunnerContext:
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
    config = {
        "paths": {
            "telegram_config": str(tmp / "telegram.json"),
        }
    }
    return RunnerContext(
        job="market_outlook",
        config_path=ROOT / "config" / "pipeline.json",
        scheduler_config_path=ROOT / "config" / "scheduler.json",
        trade_date=date(2026, 8, 13),
        run_id=run_id,
        force=force,
        config=config,
        scheduler_config=scheduler_config,
        calendar_config={"holidays": [], "special_trading_days": []},
    )


class DeliveryIdempotencyPhase2Tests(unittest.TestCase):
    def test_concurrent_callers_share_one_active_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            first_ctx = make_ctx(tmp, "RUN-FIRST")
            second_ctx = make_ctx(tmp, "RUN-SECOND")
            payload = ReportPayload("market_outlook", "market_outlook.txt", "same")
            entered = threading.Event()
            release = threading.Event()
            calls: list[str] = []
            first_result: list[dict] = []

            def sender(ctx, _payload, **_kwargs):
                calls.append(ctx.run_id)
                entered.set()
                self.assertTrue(release.wait(timeout=5))
                return {"ok": True, "result": {"message_id": 101}}

            def run_first() -> None:
                first_result.extend(deliver(first_ctx, [payload]))

            with patch("modules.job_runner.delivery._send_telegram", side_effect=sender):
                worker = threading.Thread(target=run_first, daemon=True)
                worker.start()
                self.assertTrue(entered.wait(timeout=5))
                second_result = deliver(second_ctx, [payload])
                release.set()
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(calls, ["RUN-FIRST"])
            self.assertEqual(first_result[0]["status"], "SENT")
            self.assertEqual(second_result[0]["status"], "DELIVERY_IN_PROGRESS")
            self.assertEqual(deliver(second_ctx, [payload])[0]["status"], "DUPLICATE_SUPPRESSED")

            store = _idempotency_store(first_ctx)
            self.assertEqual(store.integrity_check(), "ok")
            projection = json.loads(
                (tmp / "state" / "idempotency.json").read_text(encoding="utf-8")
            )
            self.assertIn(_idempotency_key(first_ctx, payload), projection)

    def test_failed_attempt_is_retryable_and_attempt_history_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            payload = ReportPayload("market_outlook", "market_outlook.txt", "retry")
            first_ctx = make_ctx(tmp, "RUN-FAIL")
            with patch(
                "modules.job_runner.delivery._send_telegram",
                side_effect=RuntimeError("temporary network failure"),
            ):
                failed = deliver(first_ctx, [payload])
            self.assertEqual(failed[0]["status"], "FAILED")
            self.assertTrue(failed[0]["idempotency_failure_recorded"])

            retry_ctx = make_ctx(tmp, "RUN-RETRY")
            with patch(
                "modules.job_runner.delivery._send_telegram",
                return_value={"ok": True, "result": {"message_id": 202}},
            ):
                retried = deliver(retry_ctx, [payload])
            self.assertEqual(retried[0]["status"], "SENT")

            db = tmp / "state" / "idempotency.sqlite3"
            with closing(sqlite3.connect(db)) as conn:
                states = [
                    row[0]
                    for row in conn.execute(
                        "SELECT status FROM telegram_delivery_attempts ORDER BY reserved_at"
                    )
                ]
                self.assertEqual(states, ["FAILED", "SENT"])
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_stale_reservation_is_reclaimed_but_live_force_is_not_stolen(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            payload = ReportPayload("market_outlook", "market_outlook.txt", "stale")
            abandoned_ctx = make_ctx(tmp, "RUN-ABANDONED")
            key = _idempotency_key(abandoned_ctx, payload)
            store = _idempotency_store(abandoned_ctx)
            abandoned = store.reserve(key, abandoned_ctx.run_id)
            self.assertTrue(abandoned.acquired)

            with closing(sqlite3.connect(store.database_path)) as conn:
                conn.execute(
                    "UPDATE telegram_delivery_state SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE idempotency_key=?",
                    (key,),
                )
                conn.commit()

            retry_ctx = make_ctx(tmp, "RUN-STALE-RETRY")
            with patch(
                "modules.job_runner.delivery._send_telegram",
                return_value={"ok": True, "result": {"message_id": 303}},
            ):
                result = deliver(retry_ctx, [payload])
            self.assertEqual(result[0]["status"], "SENT")
            self.assertFalse(
                store.fail(
                    abandoned,
                    {"status": "FAILED", "run_id": abandoned_ctx.run_id},
                    "late stale owner",
                )
            )

            with closing(sqlite3.connect(store.database_path)) as conn:
                rows = conn.execute(
                    "SELECT status FROM telegram_delivery_attempts ORDER BY reserved_at"
                ).fetchall()
            self.assertEqual([row[0] for row in rows], ["STALE_RECLAIMED", "SENT"])

            forced_ctx = make_ctx(tmp, "RUN-FORCE-OWNER", force=True)
            forced = store.reserve(key, forced_ctx.run_id, force=True)
            self.assertTrue(forced.acquired)
            competing_force = store.reserve(key, "RUN-FORCE-COMPETITOR", force=True)
            self.assertFalse(competing_force.acquired)
            self.assertEqual(competing_force.reason, "DELIVERY_IN_PROGRESS")

    def test_force_resend_is_explicit_and_normal_send_remains_suppressed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            payload = ReportPayload("market_outlook", "market_outlook.txt", "force")
            calls: list[int] = []

            def sender(*_args, **_kwargs):
                calls.append(len(calls) + 1)
                return {"ok": True, "result": {"message_id": calls[-1]}}

            with patch("modules.job_runner.delivery._send_telegram", side_effect=sender):
                first = deliver(make_ctx(tmp, "RUN-NORMAL"), [payload])
                forced = deliver(make_ctx(tmp, "RUN-FORCED", force=True), [payload])
                duplicate = deliver(make_ctx(tmp, "RUN-DUPLICATE"), [payload])

            self.assertEqual(first[0]["status"], "SENT")
            self.assertEqual(forced[0]["status"], "SENT")
            self.assertTrue(forced[0]["force_resend"])
            self.assertEqual(duplicate[0]["status"], "DUPLICATE_SUPPRESSED")
            self.assertEqual(len(calls), 2)

    def test_legacy_json_index_migrates_as_delivered_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = make_ctx(tmp, "RUN-LEGACY")
            payload = ReportPayload("market_outlook", "market_outlook.txt", "legacy")
            key = _idempotency_key(ctx, payload)
            index = tmp / "state" / "idempotency.json"
            index.parent.mkdir(parents=True)
            index.write_text(
                json.dumps({key: {"status": "SENT", "idempotency_key": key}}),
                encoding="utf-8",
            )

            self.assertEqual(should_send(ctx, payload), (False, "DUPLICATE_SUPPRESSED"))
            self.assertEqual(_idempotency_store(ctx).integrity_check(), "ok")


if __name__ == "__main__":
    unittest.main()
