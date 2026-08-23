from __future__ import annotations

import re
from typing import Any, Iterable

from .reports import ReportPayload


DEFAULT_DETAIL_STATUSES = ("BUY ON TRIGGER", "BUY CANDIDATE")
_UNLIMITED_GENERATION_CAP = 10_000


def _normalize_decision(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").upper().replace("_", " ")).strip()


def _configured_detail_statuses(ctx: Any) -> set[str]:
    final_cfg = (getattr(ctx, "scheduler_config", {}) or {}).get("final_watchlist", {}) or {}
    configured = final_cfg.get("detail_statuses", DEFAULT_DETAIL_STATUSES)
    if not isinstance(configured, (list, tuple, set)):
        configured = DEFAULT_DETAIL_STATUSES
    normalized = {_normalize_decision(item) for item in configured if _normalize_decision(item)}
    return normalized or set(DEFAULT_DETAIL_STATUSES)


def _configured_detail_limit(ctx: Any) -> int:
    final_cfg = (getattr(ctx, "scheduler_config", {}) or {}).get("final_watchlist", {}) or {}
    try:
        return int(final_cfg.get("max_detail_symbols", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _detail_decision(payload: ReportPayload) -> str:
    if str(payload.report_type or "").strip().lower() != "final_watchlist_detail":
        return ""
    text = str(payload.text or "").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    # Official compact detail contract: "📈 SYMBOL | DECISION | CONFIDENCE".
    parts = [part.strip() for part in first_line.split("|")]
    if len(parts) >= 3:
        return _normalize_decision(parts[1])
    return ""


def prepare_final_watchlist_detail_generation(ctx: Any) -> None:
    """Raise only the presentation-generation cap when Telegram detail is unlimited.

    The existing builder historically defaults to five detail cards. The policy
    keeps that builder contract untouched and widens only the runtime presentation
    cap so every already-selected Final Watchlist row can reach the downstream
    delivery filter. No engine decision, ranking, score, CSV row, or lifecycle
    state is changed.
    """
    scheduler = getattr(ctx, "scheduler_config", None)
    if not isinstance(scheduler, dict):
        return
    enhanced = scheduler.setdefault("enhanced_reporting", {})
    if not isinstance(enhanced, dict):
        return
    limit = _configured_detail_limit(ctx)
    required = _UNLIMITED_GENERATION_CAP if limit <= 0 else max(limit, 1)
    try:
        current = int(enhanced.get("max_watchlist_messages", 5) or 5)
    except (TypeError, ValueError):
        current = 5
    if current < required:
        enhanced["max_watchlist_messages"] = required


def apply_final_watchlist_delivery_policy(
    ctx: Any,
    payloads: Iterable[ReportPayload],
) -> list[ReportPayload]:
    """Keep all non-detail payloads and only actionable configured detail cards.

    Summary/CSV/audit payloads stay complete. Only Telegram/preview detail cards
    are filtered. A non-positive max_detail_symbols means unlimited after status
    filtering.
    """
    allowed = _configured_detail_statuses(ctx)
    limit = _configured_detail_limit(ctx)
    kept: list[ReportPayload] = []
    detail_count = 0

    for payload in payloads:
        if str(payload.report_type or "").strip().lower() != "final_watchlist_detail":
            kept.append(payload)
            continue
        decision = _detail_decision(payload)
        if decision not in allowed:
            continue
        if limit > 0 and detail_count >= limit:
            continue
        kept.append(payload)
        detail_count += 1

    return kept


__all__ = [
    "DEFAULT_DETAIL_STATUSES",
    "apply_final_watchlist_delivery_policy",
    "prepare_final_watchlist_detail_generation",
]
