from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - handled at runtime for live send
    requests = None

from swing_utils import file_sha256
from modules.telegram.router import TelegramRouter

from .reports import ReportPayload
from .runtime import RunnerContext, append_jsonl, now_wib, read_json, resolve, write_json


def _idempotency_key(ctx: RunnerContext, payload: ReportPayload) -> str:
    report = payload.report_type.upper()
    if report == "DATA_WARNING":
        return f"{ctx.trade_date.isoformat()}:DATA_WARNING:{ctx.job.upper()}"
    if report == "SIGNAL_DETAIL":
        symbol = (payload.symbol or payload.filename.replace("signal_detail_", "").replace(".txt", "")).upper()
        status = (payload.signal_status or "UNKNOWN").upper()
        version = payload.signal_version or ctx.run_id
        return f"{ctx.trade_date.isoformat()}:SIGNAL_DETAIL:{symbol}:{status}:{version}"
    return f"{ctx.trade_date.isoformat()}:{report}"


def _state_paths(ctx: RunnerContext) -> tuple[Path, Path]:
    delivery_cfg = ctx.scheduler_config.get("delivery", {})
    index = resolve(delivery_cfg.get("idempotency_index", "data/state/scheduler/telegram_idempotency.json"))
    log = resolve(delivery_cfg.get("delivery_log", "data/state/scheduler/delivery_log.jsonl"))
    return index, log


def should_send(ctx: RunnerContext, payload: ReportPayload) -> tuple[bool, str]:
    if ctx.dry_run:
        return False, "DRY_RUN"
    if ctx.no_telegram:
        return False, "NO_TELEGRAM"
    index_path, _ = _state_paths(ctx)
    index = read_json(index_path)
    key = _idempotency_key(ctx, payload)
    if key in index and not ctx.force:
        return False, "DUPLICATE_SUPPRESSED"
    return True, key


def _telegram_config(ctx: RunnerContext) -> dict[str, Any]:
    return read_json(ctx.path("telegram_config", "config/telegram.json"))


def _topic_id(ctx: RunnerContext, payload: ReportPayload) -> str:
    return str(telegram_route(ctx, payload)["message_thread_id"] or "").strip()


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
        lines = block.splitlines()
        for line in lines:
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
    marked: list[str] = []
    for index, part in enumerate(normalized, start=1):
        marker = f"Bagian {index}/{total}\n\n"
        marked.append(marker + part)
    return marked


def normalize_telegram_text(text: str) -> str:
    """Keep message spacing stable across Windows, preview files, and Telegram."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _send_telegram(ctx: RunnerContext, payload: ReportPayload, text: str | None = None, part_index: int = 1, part_count: int = 1) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang. Jalankan maintenance\\INSTALL_REQUIREMENTS.bat.")
    cfg = _telegram_config(ctx)
    telegram = cfg.get("telegram", {})
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Telegram token/chat_id belum dikonfigurasi.")
    message_text = text if text is not None else payload.text
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
    try:
        body = response.json()
    except Exception as exc:
        raise RuntimeError(f"Respons Telegram bukan JSON: HTTP {response.status_code}") from exc
    if not response.ok or not body.get("ok"):
        raise RuntimeError(f"Telegram API gagal: {body}")
    return body


def deliver(ctx: RunnerContext, payloads: list[ReportPayload]) -> list[dict[str, Any]]:
    index_path, log_path = _state_paths(ctx)
    index = read_json(index_path)
    results: list[dict[str, Any]] = []
    failed_root = resolve(ctx.scheduler_config.get("delivery", {}).get("failed_root", "data/output/failed_delivery"))
    delivery_total = len(payloads)
    for delivery_sequence, payload in enumerate(payloads, start=1):
        allowed, reason = should_send(ctx, payload)
        key = _idempotency_key(ctx, payload)
        max_len = int(ctx.scheduler_config.get("telegram", {}).get("maximum_message_length", 4000))
        normalized_text = normalize_telegram_text(payload.text)
        parts = split_telegram_text(normalized_text, max_len=max_len)
        base = {
            "time": now_wib().isoformat(timespec="seconds"),
            "run_id": ctx.run_id,
            "job": ctx.job,
            "trade_date": ctx.trade_date.isoformat(),
            "report_type": payload.report_type,
            "signature": payload.signature,
            "idempotency_key": key,
            "part_count": len(parts),
            "delivery_sequence": delivery_sequence,
            "delivery_total": delivery_total,
            "force_resend": bool(ctx.force),
            **telegram_route(ctx, payload),
            "telegram_message_id": "",
        }
        if not allowed:
            event = {**base, "status": reason}
            append_jsonl(log_path, event)
            results.append(event)
            continue
        message_ids: list[Any] = []
        part_events: list[dict[str, Any]] = []
        try:
            for idx, part in enumerate(parts, start=1):
                response = _send_telegram(ctx, payload, text=part, part_index=idx, part_count=len(parts))
                message_id = response.get("result", {}).get("message_id", "")
                message_ids.append(message_id)
                part_event = {**base, "status": "SENT_PART", "part_index": idx, "telegram_message_id": message_id}
                append_jsonl(log_path, part_event)
                part_events.append(part_event)
            event = {**base, "status": "SENT", "telegram_message_ids": message_ids, "parts": part_events}
            index[key] = event
            write_json(index_path, index)
            append_jsonl(log_path, event)
            results.append(event)
        except Exception as exc:
            folder = failed_root / ctx.trade_date.isoformat()
            folder.mkdir(parents=True, exist_ok=True)
            payload_path = folder / f"{ctx.run_id}_{payload.report_type}.txt"
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
            append_jsonl(log_path, event)
            results.append(event)
    return results
