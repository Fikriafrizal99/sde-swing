from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - handled at runtime for live send
    requests = None

from swing_utils import PACKAGE_VERSION, file_sha256
from modules.telegram.router import TelegramRouter

from .reports import ReportPayload
from .runtime import RunnerContext, append_jsonl, now_wib, read_json, resolve
from .delivery_idempotency import (
    DeliveryIdempotencyStore,
    ReservationOwnershipLost,
)


_PHOTO_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _attachment_path(payload: ReportPayload) -> Path | None:
    raw = getattr(payload, "attachment_path", None)
    if raw in (None, ""):
        return None
    return Path(raw)


def _attachment_caption(payload: ReportPayload) -> str:
    # An explicitly supplied empty caption is intentional for standalone
    # visual cards such as the Post Market heatmap.
    if hasattr(payload, "caption"):
        return str(getattr(payload, "caption", "") or "").strip()
    return str(payload.text or "").strip()


def _is_photo_attachment(path: Path | None) -> bool:
    return path is not None and path.suffix.lower() in _PHOTO_SUFFIXES


def _idempotency_key(ctx: RunnerContext, payload: ReportPayload) -> str:
    report = payload.report_type.upper()
    attachment = _attachment_path(payload)
    if _is_photo_attachment(attachment) and report == "FINAL_WATCHLIST_DETAIL":
        symbol = (payload.symbol or "UNKNOWN").upper()
        material = payload.material_signature or payload.signal_version or payload.signature[:24]
        return f"{ctx.trade_date.isoformat()}:{report}:{symbol}:{material}"
    if attachment is None and report == "FINAL_WATCHLIST_DETAIL" and (payload.material_signature or payload.signal_version):
        symbol = (payload.symbol or "UNKNOWN").upper()
        material = payload.material_signature or payload.signal_version
        return f"{ctx.trade_date.isoformat()}:{report}:{symbol}:{material}"
    if attachment is not None:
        return f"{ctx.trade_date.isoformat()}:{report}:{attachment.name.upper()}"
    if report == "DATA_WARNING":
        return f"{ctx.trade_date.isoformat()}:DATA_WARNING:{ctx.job.upper()}"
    if report in {"SIGNAL_DETAIL", "FINAL_WATCHLIST_DETAIL"}:
        symbol = (payload.symbol or payload.filename.replace("signal_detail_", "").replace(".txt", "")).upper()
        status = (payload.signal_status or "UNKNOWN").upper()
        version = payload.signal_version or ctx.run_id
        return f"{ctx.trade_date.isoformat()}:{report}:{symbol}:{status}:{version}"
    if report == "STATUS_CHANGES":
        return f"{ctx.trade_date.isoformat()}:{report}:{payload.signal_version or payload.signature[:24]}"
    if report == "ACTIVE_RECOMMENDATIONS":
        return f"{ctx.trade_date.isoformat()}:{report}"
    return f"{ctx.trade_date.isoformat()}:{report}"


def _state_paths(ctx: RunnerContext) -> tuple[Path, Path]:
    delivery_cfg = ctx.scheduler_config.get("delivery", {})
    index = resolve(delivery_cfg.get("idempotency_index", "data/state/scheduler/telegram_idempotency.json"))
    log = resolve(delivery_cfg.get("delivery_log", "data/state/scheduler/delivery_log.jsonl"))
    return index, log


def _expand_post_market_heatmap_payloads(
    ctx: RunnerContext,
    payloads: list[ReportPayload],
) -> list[ReportPayload]:
    """Insert one non-blocking heatmap photo immediately before Post Market.

    Rendering is deliberately performed in the delivery/output layer. A stale
    or failed heatmap can never block the Post Market text or any SDE engine.
    """
    if not payloads:
        return payloads
    # ``write_payloads`` prepares the exact outbound list before archiving the
    # preview.  Delivery may receive that already-prepared list, so expansion
    # must be idempotent.
    if any(
        str(payload.report_type or "").strip().lower() == "post_market_heatmap"
        for payload in payloads
    ):
        return payloads

    expanded: list[ReportPayload] = []
    inserted = False
    for payload in payloads:
        if not inserted and str(payload.report_type or "").strip().lower() == "post_market":
            try:
                from modules.telegram.market_heatmap import heatmap_enabled, render_market_heatmap

                if heatmap_enabled(ctx):
                    path = render_market_heatmap(ctx)
                    heatmap = ReportPayload(
                        report_type="post_market_heatmap",
                        filename="post_market_heatmap.txt",
                        text=(
                            "📊 SDE SWING — MARKET HEATMAP\n"
                            "⚠️ Gambar heatmap tidak dapat dikirim; Post Market tetap dilanjutkan."
                        ),
                        topic="post_market",
                    )
                    setattr(heatmap, "attachment_path", path)
                    setattr(heatmap, "caption", "")
                    expanded.append(heatmap)
                    inserted = True
            except Exception as exc:
                _, log_path = _state_paths(ctx)
                append_jsonl(log_path, {
                    "time": now_wib().isoformat(timespec="seconds"),
                    "run_id": ctx.run_id,
                    "job": ctx.job,
                    "trade_date": ctx.trade_date.isoformat(),
                    "report_type": "post_market_heatmap",
                    "status": "HEATMAP_RENDER_SKIPPED",
                    "error": f"{type(exc).__name__}:{exc}",
                    "non_blocking": True,
                })
                inserted = True
        expanded.append(payload)
    return expanded


def prepare_delivery_payloads(
    ctx: RunnerContext,
    payloads: list[ReportPayload],
) -> list[ReportPayload]:
    """Return the exact ordered payload list that the delivery loop will use."""
    return _expand_post_market_heatmap_payloads(ctx, payloads)


def _idempotency_store(
    ctx: RunnerContext,
    index_path: Path | None = None,
) -> DeliveryIdempotencyStore:
    delivery_cfg = ctx.scheduler_config.get("delivery", {})
    legacy_index = index_path or _state_paths(ctx)[0]
    configured_database = str(delivery_cfg.get("idempotency_database", "") or "").strip()
    database_path = (
        resolve(configured_database)
        if configured_database
        else legacy_index.with_suffix(".sqlite3")
    )
    lease_seconds = int(delivery_cfg.get("idempotency_reservation_ttl_seconds", 900))
    return DeliveryIdempotencyStore(database_path, legacy_index, lease_seconds)


def _mark_lifecycle_events_notified(ctx: RunnerContext, event_ids: tuple[str, ...] | list[str]) -> int:
    identifiers = [str(item).strip() for item in event_ids if str(item).strip()]
    if not identifiers:
        return 0
    db_path = resolve(ctx.config.get("paths", {}).get("swing_database", "data/database/sde_swing_history.db"))
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        placeholders = ",".join("?" for _ in identifiers)
        cursor = conn.execute(
            f"UPDATE lifecycle_events SET telegram_notified_at=? WHERE event_id IN ({placeholders}) AND (telegram_notified_at IS NULL OR telegram_notified_at='')",
            [now_wib().isoformat(timespec="seconds"), *identifiers],
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def _lifecycle_ack_ids(payload: ReportPayload, normalized_text: str) -> tuple[str, ...]:
    """Return only lifecycle IDs that are visibly represented in the message.

    The lifecycle bridge can carry a larger pending ID set than the bounded
    digest text (normally 20 events). Acknowledging the full pending set would
    silently lose later TP/SL events. STATUS_CHANGES uses one `◆` line per
    rendered event, so the visible event count is the authoritative ACK bound.
    A legacy single-event payload has no digest marker; it remains safe to ACK
    because its one ID cannot hide additional pending lifecycle events.
    """
    identifiers = tuple(
        str(item).strip()
        for item in (getattr(payload, "lifecycle_event_ids", ()) or ())
        if str(item).strip()
    )
    if not identifiers:
        return ()
    if str(payload.report_type or "").upper() != "STATUS_CHANGES":
        return identifiers
    rendered_count = len(re.findall(r"(?m)^◆\s+", normalized_text))
    if rendered_count > 0:
        return identifiers[:rendered_count]
    if len(identifiers) == 1:
        return identifiers
    return ()


def should_send(ctx: RunnerContext, payload: ReportPayload) -> tuple[bool, str]:
    if ctx.dry_run:
        return False, "DRY_RUN"
    if ctx.no_telegram:
        return False, "NO_TELEGRAM"
    key = _idempotency_key(ctx, payload)
    index_path, _ = _state_paths(ctx)
    return _idempotency_store(ctx, index_path).can_send(key, force=ctx.force)


def _telegram_config(ctx: RunnerContext) -> dict[str, Any]:
    return read_json(ctx.path("telegram_config", "config/telegram.json"))


def telegram_route(ctx: RunnerContext, payload: ReportPayload) -> dict[str, Any]:
    """Resolve one of SIGNAL/REPORT/SYSTEM topics with env-first semantics."""
    cfg = _telegram_config(ctx)
    route = TelegramRouter(cfg, os.environ).resolve(payload.report_type, payload.topic)
    if route.fallback_to_main_chat:
        scheduler_routing = ctx.scheduler_config.get("delivery", {}).get("topic_routing", {})
        configured = scheduler_routing.get(payload.report_type) or scheduler_routing.get(payload.topic) or ""
        if configured:
            route = type(route)(route.category, route.target_thread, str(configured).strip(), False)
    return {**route.to_dict(), "env_var": f"TELEGRAM_THREAD_{route.category}_ID"}


def _topic_id(ctx: RunnerContext, payload: ReportPayload) -> str:
    return str(telegram_route(ctx, payload)["message_thread_id"] or "").strip()


def split_telegram_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    blocks = text.split("\n\n")
    parts: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            parts.append(current)
            current = ""
        if len(block) <= max_len:
            current = block
            continue
        for line in block.splitlines():
            candidate = line if not current else current + "\n" + line
            if len(candidate) <= max_len:
                current = candidate
                continue
            if current:
                parts.append(current)
                current = ""
            while len(line) > max_len:
                parts.append(line[:max_len])
                line = line[max_len:]
            current = line
    if current:
        parts.append(current)
    if len(parts) <= 1:
        return parts
    worst_marker = f"Bagian {len(parts)}/{len(parts)}\n\n"
    content_limit = max_len - len(worst_marker)
    normalized: list[str] = []
    for part in parts:
        while len(part) > content_limit:
            normalized.append(part[:content_limit])
            part = part[content_limit:]
        normalized.append(part)
    total = len(normalized)
    return [f"Bagian {index}/{total}\n\n{part}" for index, part in enumerate(normalized, start=1)]


def normalize_telegram_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _credentials(ctx: RunnerContext | None = None) -> tuple[str, str]:
    runtime_cfg: dict[str, Any] = {}
    if ctx is not None:
        try:
            telegram_cfg = _telegram_config(ctx)
            runtime_cfg = telegram_cfg.get("telegram", {}) if isinstance(telegram_cfg.get("telegram", {}), dict) else {}
        except Exception:
            runtime_cfg = {}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or str(runtime_cfg.get("bot_token", "")).strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or str(runtime_cfg.get("chat_id", "")).strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram token/chat_id belum dikonfigurasi.")
    return token, chat_id


def telegram_configured(ctx: RunnerContext) -> bool:
    try:
        _credentials(ctx)
    except RuntimeError:
        return False
    return True


def _response_json(response: Any) -> dict[str, Any]:
    try:
        body = response.json()
    except Exception as exc:
        raise RuntimeError(f"Respons Telegram bukan JSON: HTTP {response.status_code}") from exc
    if not response.ok or not body.get("ok"):
        raise RuntimeError(f"Telegram API gagal: {body}")
    return body


def _send_telegram(
    ctx: RunnerContext,
    payload: ReportPayload,
    text: str | None = None,
    part_index: int = 1,
    part_count: int = 1,
) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang. Jalankan maintenance\\INSTALL_REQUIREMENTS.bat.")
    token, chat_id = _credentials(ctx)
    message_text = text if text is not None else payload.text
    cfg = _telegram_config(ctx)
    ui_cfg = cfg.get("telegram_ui", {})
    parse_mode = str(ui_cfg.get("parse_mode", "HTML")).strip() or "HTML"
    data: dict[str, Any] = {
        "chat_id": chat_id,
        "text": message_text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": "true",
    }
    topic_id = _topic_id(ctx, payload)
    if topic_id:
        data["message_thread_id"] = topic_id
    response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
    return _response_json(response)


def _send_document(ctx: RunnerContext, payload: ReportPayload) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang. Jalankan maintenance\\INSTALL_REQUIREMENTS.bat.")
    path = _attachment_path(payload)
    if path is None:
        raise RuntimeError("Attachment path tidak tersedia.")
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"Attachment tidak ditemukan: {path}")
    token, chat_id = _credentials(ctx)
    data: dict[str, Any] = {"chat_id": chat_id}
    caption = _attachment_caption(payload)
    if caption:
        data["caption"] = caption[:1024]
        data["parse_mode"] = "HTML"
    topic_id = _topic_id(ctx, payload)
    if topic_id:
        data["message_thread_id"] = topic_id
    with path.open("rb") as handle:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=data,
            files={"document": (path.name, handle, "text/csv")},
            timeout=60,
        )
    return _response_json(response)


def _send_photo(ctx: RunnerContext, payload: ReportPayload, caption: str) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang. Jalankan maintenance\\INSTALL_REQUIREMENTS.bat.")
    path = _attachment_path(payload)
    if path is None or not path.exists() or not path.is_file():
        raise RuntimeError(f"Photo attachment tidak ditemukan: {path}")
    token, chat_id = _credentials(ctx)
    data: dict[str, Any] = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption[:1024]
        data["parse_mode"] = "HTML"
    topic_id = _topic_id(ctx, payload)
    if topic_id:
        data["message_thread_id"] = topic_id
    with path.open("rb") as handle:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=data,
            files={"photo": (path.name, handle, "image/png")},
            timeout=60,
        )
    return _response_json(response)


def _photo_parts(payload: ReportPayload, max_len: int) -> tuple[str, list[str]]:
    full = normalize_telegram_text(payload.text)
    # An explicit caption is the formatter-approved complete photo card.
    # ``payload.text`` remains the audit/text-fallback representation and must
    # not silently become a second Telegram card.
    if hasattr(payload, "caption"):
        return normalize_telegram_text(_attachment_caption(payload))[:1024], []
    if len(full) <= 1024:
        return _attachment_caption(payload), []
    caption = normalize_telegram_text(_attachment_caption(payload))
    if not caption:
        caption = full[:900]
    caption = caption[:1024]
    remainder = full[len(caption):].strip() if full.startswith(caption) else full
    return caption, split_telegram_text(remainder, max_len=max_len) if remainder else []


def delivery_preview_parts(payload: ReportPayload, max_len: int = 4000) -> list[dict[str, str]]:
    """Describe the exact outbound Telegram parts without sending anything."""
    attachment = _attachment_path(payload)
    if _is_photo_attachment(attachment):
        caption, followups = _photo_parts(payload, max_len)
        return [
            {"kind": "photo", "text": caption},
            *({"kind": "text", "text": part} for part in followups),
        ]
    if attachment is not None:
        return [{"kind": "document", "text": _attachment_caption(payload)[:1024]}]
    normalized = normalize_telegram_text(payload.text)
    return [
        {"kind": "text", "text": part}
        for part in split_telegram_text(normalized, max_len=max_len)
    ]


def _record_successful_lifecycle_ack(
    ctx: RunnerContext,
    payload: ReportPayload,
    normalized_text: str,
    base: dict[str, Any],
    log_path: Path,
) -> bool:
    lifecycle_ids = _lifecycle_ack_ids(payload, normalized_text)
    if not lifecycle_ids:
        return True
    try:
        _mark_lifecycle_events_notified(ctx, lifecycle_ids)
        return True
    except Exception as exc:
        append_jsonl(log_path, {**base, "status": "LIFECYCLE_ACK_FAILED", "error": str(exc)})
        return False


def deliver(ctx: RunnerContext, payloads: list[ReportPayload]) -> list[dict[str, Any]]:
    payloads = prepare_delivery_payloads(ctx, payloads)
    index_path, log_path = _state_paths(ctx)
    idempotency_store: DeliveryIdempotencyStore | None = None
    results: list[dict[str, Any]] = []
    failed_root = resolve(ctx.scheduler_config.get("delivery", {}).get("failed_root", "data/output/failed_delivery"))
    delivery_total = len(payloads)
    credentials_ready = telegram_configured(ctx)
    provenance = getattr(ctx, "config_provenance", {}) or {}
    official_runtime = str(provenance.get("config_version", "")) == PACKAGE_VERSION

    for delivery_sequence, payload in enumerate(payloads, start=1):
        key = _idempotency_key(ctx, payload)
        if ctx.dry_run:
            allowed, reason = False, "DRY_RUN"
        elif ctx.no_telegram:
            allowed, reason = False, "NO_TELEGRAM"
        else:
            if idempotency_store is None:
                idempotency_store = _idempotency_store(ctx, index_path)
            allowed, reason = idempotency_store.can_send(key, force=ctx.force)
        attachment = _attachment_path(payload)
        is_photo = _is_photo_attachment(attachment)
        max_len = int(ctx.scheduler_config.get("telegram", {}).get("maximum_message_length", 4000))
        normalized_text = normalize_telegram_text(payload.text)
        photo_caption, photo_followups = _photo_parts(payload, max_len) if is_photo else ("", [])
        parts = [] if attachment is not None else split_telegram_text(normalized_text, max_len=max_len)
        expected_parts = (1 + len(photo_followups)) if is_photo else (1 if attachment is not None else len(parts))
        base = {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "report_type": payload.report_type,
            "signature": payload.signature,
            "idempotency_key": key,
            "part_count": expected_parts,
            "delivery_sequence": delivery_sequence,
            "delivery_total": delivery_total,
            "force_resend": bool(ctx.force),
            "delivery_guarantee": "AT_LEAST_ONCE_WITH_CRASH_AMBIGUITY",
            "attachment_path": str(attachment) if attachment else "",
            **telegram_route(ctx, payload),
            "telegram_message_id": "",
        }
        if not allowed:
            event = {**base, "status": reason}
            if reason == "DUPLICATE_SUPPRESSED":
                _record_successful_lifecycle_ack(
                    ctx, payload, normalized_text, base, log_path
                )
            append_jsonl(log_path, event)
            results.append(event)
            continue
        if not credentials_ready and official_runtime:
            event = {**base, "status": "SKIPPED_NOT_CONFIGURED", "reason": "TELEGRAM_CREDENTIALS_EMPTY"}
            append_jsonl(log_path, event)
            results.append(event)
            continue

        if idempotency_store is None:  # Defensive; non-dry sends initialize it above.
            idempotency_store = _idempotency_store(ctx, index_path)
        reservation = idempotency_store.reserve(key, ctx.run_id, force=ctx.force)
        if not reservation.acquired:
            event = {
                **base,
                "status": reservation.reason,
                "idempotency_attempt_id": reservation.attempt_id,
                "reservation_expires_at": reservation.lease_expires_at,
            }
            if reservation.reason == "DUPLICATE_SUPPRESSED":
                _record_successful_lifecycle_ack(
                    ctx, payload, normalized_text, base, log_path
                )
            append_jsonl(log_path, event)
            results.append(event)
            continue
        base.update({
            "idempotency_attempt_id": reservation.attempt_id,
            "reservation_expires_at": reservation.lease_expires_at,
        })

        def renew_reservation() -> None:
            base["reservation_expires_at"] = idempotency_store.renew(reservation)

        message_ids: list[Any] = []
        part_events: list[dict[str, Any]] = []
        try:
            if is_photo:
                try:
                    renew_reservation()
                    response = _send_photo(ctx, payload, photo_caption)
                except Exception as photo_exc:
                    fallback_text = photo_caption if hasattr(payload, "caption") else normalized_text
                    fallback_parts = split_telegram_text(fallback_text, max_len=max_len)
                    if not fallback_parts:
                        raise
                    for idx, part in enumerate(fallback_parts, start=1):
                        renew_reservation()
                        response = _send_telegram(ctx, payload, text=part, part_index=idx, part_count=len(fallback_parts))
                        message_id = response.get("result", {}).get("message_id", "")
                        message_ids.append(message_id)
                        part_event = {**base, "status": "SENT_FALLBACK_PART", "part_index": idx, "telegram_message_id": message_id}
                        append_jsonl(log_path, part_event)
                        part_events.append(part_event)
                    event = {
                        **base,
                        "status": "SENT_WITH_TEXT_FALLBACK",
                        "photo_error": str(photo_exc),
                        "telegram_message_ids": message_ids,
                        "parts": part_events,
                    }
                    projection_error = idempotency_store.complete(reservation, event)
                    if projection_error:
                        event["idempotency_projection_warning"] = projection_error
                    _record_successful_lifecycle_ack(
                        ctx, payload, normalized_text, base, log_path
                    )
                    append_jsonl(log_path, event)
                    results.append(event)
                    continue

                message_id = response.get("result", {}).get("message_id", "")
                message_ids.append(message_id)
                photo_event = {**base, "status": "SENT_PHOTO", "part_index": 1, "telegram_message_id": message_id}
                append_jsonl(log_path, photo_event)
                part_events.append(photo_event)
                for idx, part in enumerate(photo_followups, start=2):
                    renew_reservation()
                    response = _send_telegram(ctx, payload, text=part, part_index=idx, part_count=expected_parts)
                    message_id = response.get("result", {}).get("message_id", "")
                    message_ids.append(message_id)
                    part_event = {**base, "status": "SENT_PART", "part_index": idx, "telegram_message_id": message_id}
                    append_jsonl(log_path, part_event)
                    part_events.append(part_event)
            elif attachment is not None:
                renew_reservation()
                response = _send_document(ctx, payload)
                message_id = response.get("result", {}).get("message_id", "")
                message_ids.append(message_id)
                part_event = {**base, "status": "SENT_PART", "part_index": 1, "telegram_message_id": message_id}
                append_jsonl(log_path, part_event)
                part_events.append(part_event)
            else:
                for idx, part in enumerate(parts, start=1):
                    renew_reservation()
                    response = _send_telegram(ctx, payload, text=part, part_index=idx, part_count=len(parts))
                    message_id = response.get("result", {}).get("message_id", "")
                    message_ids.append(message_id)
                    part_event = {**base, "status": "SENT_PART", "part_index": idx, "telegram_message_id": message_id}
                    append_jsonl(log_path, part_event)
                    part_events.append(part_event)

            event = {**base, "status": "SENT", "telegram_message_ids": message_ids, "parts": part_events}
            projection_error = idempotency_store.complete(reservation, event)
            if projection_error:
                event["idempotency_projection_warning"] = projection_error
            _record_successful_lifecycle_ack(
                ctx, payload, normalized_text, base, log_path
            )
            append_jsonl(log_path, event)
            results.append(event)
        except ReservationOwnershipLost as exc:
            event = {
                **base,
                "status": "DELIVERY_STATE_UNCERTAIN",
                "error": str(exc),
                "telegram_message_ids": message_ids,
                "sent_parts_before_failure": len(message_ids),
            }
            append_jsonl(log_path, event)
            results.append(event)
        except Exception as exc:
            folder = failed_root / ctx.trade_date.isoformat()
            folder.mkdir(parents=True, exist_ok=True)
            suffix = attachment.suffix if attachment is not None else ".txt"
            payload_path = folder / f"{ctx.run_id}_{payload.report_type}{suffix}"
            if attachment is not None and attachment.exists():
                payload_path.write_bytes(attachment.read_bytes())
            else:
                payload_path.write_text(normalized_text, encoding="utf-8")
            event = {
                **base,
                "status": "FAILED",
                "error": str(exc),
                "failed_payload": str(payload_path),
                "failed_payload_sha256": file_sha256(payload_path),
                "telegram_message_ids": message_ids,
                "sent_parts_before_failure": len(message_ids),
            }
            try:
                state_recorded = idempotency_store.fail(
                    reservation, event, str(exc)
                )
            except Exception as state_exc:
                state_recorded = False
                event["idempotency_state_error"] = str(state_exc)
            event["idempotency_failure_recorded"] = state_recorded
            append_jsonl(log_path, event)
            results.append(event)
    try:
        from .reports import mark_preview_manifest_delivery

        mark_preview_manifest_delivery(ctx, results)
    except Exception as exc:
        # Telegram delivery has already happened.  Preserve that outcome and
        # make the archival problem visible without pretending the send failed.
        append_jsonl(log_path, {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "report_type": "delivery_bundle",
            "status": "DELIVERY_BUNDLE_FINALIZE_FAILED",
            "error": f"{type(exc).__name__}:{exc}",
        })
    return results
