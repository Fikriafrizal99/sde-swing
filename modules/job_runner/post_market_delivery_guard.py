from __future__ import annotations

"""Post Market-only guard against duplicate Telegram text after remote ACK.

The shared delivery layer intentionally provides at-least-once semantics.  A
narrow crash window exists after Telegram accepts a message but before local
idempotency state is finalized.  If that window is hit, the scheduler can
retry and send the same Post Market text twice.

This module hardens only the market-first Post Market entrypoint.  It does not
change the shared delivery engine, scheduler retry policy, report contents, or
trading logic.
"""

import json
import sqlite3
from typing import Any

from .delivery import (
    _idempotency_store,
    _state_paths,
    deliver as _baseline_deliver,
)
from .runtime import RunnerContext, append_jsonl, now_wib


_HARDENED_REPORT_TYPES = {"POST_MARKET"}
_REMOTE_ACK_TERMINAL_STATUS = "DELIVERY_STATE_UNCERTAIN"


def _message_ids(item: dict[str, Any]) -> list[Any]:
    values = item.get("telegram_message_ids")
    if isinstance(values, list):
        return [value for value in values if value not in (None, "")]
    single = item.get("telegram_message_id")
    return [single] if single not in (None, "") else []


def _remote_payload_fully_accepted(item: dict[str, Any]) -> bool:
    """True only when Telegram ACKed every expected outbound part."""
    ids = _message_ids(item)
    try:
        expected = max(1, int(item.get("part_count") or 1))
    except (TypeError, ValueError):
        expected = 1
    try:
        sent_before_failure = int(item.get("sent_parts_before_failure") or len(ids))
    except (TypeError, ValueError):
        sent_before_failure = len(ids)
    return bool(ids) and len(ids) >= expected and sent_before_failure >= expected


def _is_hardened_remote_ack_failure(item: dict[str, Any]) -> bool:
    report = str(item.get("report_type") or "").strip().upper()
    status = str(item.get("status") or "").strip().upper()
    return (
        report in _HARDENED_REPORT_TYPES
        and status in {"FAILED", "DELIVERY_STATE_UNCERTAIN"}
        and _remote_payload_fully_accepted(item)
        and bool(str(item.get("idempotency_key") or "").strip())
    )


def _seal_remote_acceptance(ctx: RunnerContext, item: dict[str, Any]) -> None:
    """Persist Telegram-accepted Post Market as terminal duplicate-suppressed.

    The baseline sender may already have recorded FAILED after a post-send
    local finalization exception.  Telegram message IDs are the evidence that
    every outbound part was accepted remotely, so the state is sealed as SENT
    while the attempt remains explicitly marked SENT_UNCERTAIN for audit.
    """
    store = _idempotency_store(ctx)
    key = str(item.get("idempotency_key") or "").strip()
    attempt_id = str(item.get("idempotency_attempt_id") or "").strip()
    now = now_wib().isoformat(timespec="seconds")
    sealed_event = {
        **item,
        "status": _REMOTE_ACK_TERMINAL_STATUS,
        "remote_acceptance_confirmed": True,
        "automatic_retry_suppressed": True,
        "sealed_at": now,
    }
    rendered = json.dumps(sealed_event, ensure_ascii=False, default=str)

    conn = sqlite3.connect(store.database_path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            """
            UPDATE telegram_delivery_state
            SET status='SENT', updated_at=?, lease_expires_at=?,
                event_json=?, last_error=?
            WHERE idempotency_key=?
            """,
            (
                now,
                now,
                rendered,
                "REMOTE_ACCEPTED_LOCAL_FINALIZATION_UNCERTAIN",
                key,
            ),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise RuntimeError(f"POST_MARKET_IDEMPOTENCY_STATE_NOT_FOUND:{key}")
        if attempt_id:
            conn.execute(
                """
                UPDATE telegram_delivery_attempts
                SET status='SENT_UNCERTAIN', completed_at=?, event_json=?,
                    error='REMOTE_ACCEPTED_LOCAL_FINALIZATION_UNCERTAIN'
                WHERE attempt_id=?
                """,
                (now, rendered, attempt_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # SQLite is authoritative.  Projection is best-effort only.
    try:
        store.refresh_legacy_projection()
    except Exception:
        pass


def deliver_post_market_hardened(
    ctx: RunnerContext,
    payloads: list[Any],
) -> list[dict[str, Any]]:
    """Delegate normally, then suppress retry only after complete remote ACK.

    Pre-send/network failures stay FAILED and remain retryable.  Partial
    multi-part deliveries also stay FAILED.  Only a fully accepted Post Market
    payload whose local finalization failed is converted to terminal uncertain.
    """
    results = _baseline_deliver(ctx, payloads)
    if not results:
        return results

    _, log_path = _state_paths(ctx)
    guarded: list[dict[str, Any]] = []
    for original in results:
        if not _is_hardened_remote_ack_failure(original):
            guarded.append(original)
            continue

        item = dict(original)
        item.update(
            {
                "status": _REMOTE_ACK_TERMINAL_STATUS,
                "remote_acceptance_confirmed": True,
                "automatic_retry_suppressed": True,
                "anti_duplicate_guard": "POST_MARKET_REMOTE_ACK_V1",
            }
        )
        try:
            _seal_remote_acceptance(ctx, item)
            item["idempotency_sealed_after_remote_acceptance"] = True
        except Exception as exc:
            # Even if local sealing also fails, do not ask the scheduler to
            # immediately resend content Telegram already acknowledged.
            item["idempotency_sealed_after_remote_acceptance"] = False
            item["idempotency_seal_error"] = f"{type(exc).__name__}:{exc}"

        append_jsonl(
            log_path,
            {
                **item,
                "time": now_wib().isoformat(timespec="seconds"),
                "status": "POST_MARKET_REMOTE_ACK_RETRY_SUPPRESSED",
            },
        )
        guarded.append(item)
    return guarded
