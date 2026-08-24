from __future__ import annotations

"""Canonical Telegram presentation for SDE Swing lifecycle analytics.

This module owns presentation only. Lifecycle state transitions, AGE,
recommendation history, trigger/expiry rules, and price-path evaluation remain
owned by ``modules.analytics.outcome_tracker`` / ``outcome_tracker_baseline``.

All automatic, manual, preview, and resend paths must reuse the builders here
instead of implementing their own Telegram formatters.
"""

import html
import math
from typing import Any, Mapping

import pandas as pd

from modules.analytics import outcome_tracker_baseline as tracker
from modules.telegram.idx_price import (
    fmt_idx_price,
    fmt_idx_zone,
    snap_idx_price,
    snap_idx_zone,
)


EVENT_GROUP_ORDER = (
    "ENTRY_TRIGGERED",
    "TP1_HIT",
    "STOP_LOSS_HIT",
    "TP2_HIT",
    "MAX_HOLD_EXIT",
    "EXPIRED",
    "INVALIDATED_BEFORE_ENTRY",
)


def _int_or_zero(value: Any) -> int:
    try:
        number = float(value)
        if math.isnan(number):
            return 0
        return int(number)
    except Exception:
        return 0


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() not in {"", "nan", "none", "null"}


def _session_age(row: pd.Series) -> int:
    value = row.get("market_session_age")
    if not _has_value(value):
        value = row.get("age_sessions")
    return _int_or_zero(value)


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    try:
        return float(value) != 0.0
    except Exception:
        return str(value).strip().upper() in {"TRUE", "YES", "Y"}


def actionable_snapshot(active: pd.DataFrame) -> pd.DataFrame:
    """Return the single valid OPEN/WAITING presentation snapshot.

    Duplicate actionable ownership is rejected rather than hidden. This mirrors
    the lifecycle storage invariant without changing any lifecycle state.
    """
    if active.empty:
        return active.copy()
    required = {"symbol", "current_status"}
    missing = sorted(required.difference(active.columns))
    if missing:
        raise ValueError(f"ACTIVE_RECOMMENDATIONS_MISSING_COLUMNS:{','.join(missing)}")

    work = active.copy()
    work["current_status"] = work["current_status"].astype(str).str.strip().str.upper()
    work["symbol"] = (
        work["symbol"].astype(str).str.strip().str.upper().str.replace(".JK", "", regex=False)
    )
    work = work[
        work["current_status"].isin(tracker.ACTIVE_STATUSES)
        & work["symbol"].ne("")
    ].copy()
    duplicate_symbols = sorted(
        work.loc[work["symbol"].duplicated(keep=False), "symbol"].unique().tolist()
    )
    if duplicate_symbols:
        raise RuntimeError(
            "DUPLICATE_ACTIONABLE_LIFECYCLE:" + ",".join(duplicate_symbols)
        )
    return work


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


def _compact_price(value: Any, *, anchor_price: Any = None) -> str:
    return fmt_idx_price(value, anchor_price=anchor_price).replace(".", "")


def _compact_zone(low: Any, high: Any, *, anchor_price: Any) -> str:
    return (
        fmt_idx_zone(low, high, anchor_price=anchor_price)
        .replace(".", "")
        .replace("–", "-")
    )


def _gap_text(current: Any, low: Any, high: Any) -> str:
    current_value = snap_idx_price(current, anchor_price=current, mode="nearest")
    low_value, high_value = snap_idx_zone(low, high, anchor_price=current)
    if current_value is None or low_value is None or high_value is None:
        return "-"
    if low_value <= current_value <= high_value:
        return "RANGE"
    if current_value < low_value and low_value:
        pct = (current_value / low_value - 1.0) * 100.0
    elif high_value:
        pct = (current_value / high_value - 1.0) * 100.0
    else:
        return "-"
    return f"{pct:+.2f}%".replace(".", ",")


def _render_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    left_columns: set[int] | None = None,
) -> str:
    left_columns = left_columns or {0}
    normalized = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in normalized:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(row: list[str]) -> str:
        cells: list[str] = []
        for index, cell in enumerate(row):
            if index in left_columns:
                cells.append(cell.ljust(widths[index]))
            else:
                cells.append(cell.rjust(widths[index]))
        return " ".join(cells).rstrip()

    output = [format_row(headers)]
    output.extend(format_row(row) for row in normalized)
    return "\n".join(output)


def build_active_message(active: pd.DataFrame) -> str:
    """Render the canonical Active Recommendations card.

    AGE is lifecycle age in IDX market sessions. ``scan_staleness_sessions`` is
    intentionally retained in analytics data but is not rendered in Telegram;
    it is an operational freshness field, not lifecycle age.
    """
    active = actionable_snapshot(active)
    statuses = (
        active["current_status"].astype(str).str.upper()
        if "current_status" in active.columns
        else pd.Series(dtype=str)
    )
    open_count = int((statuses == "OPEN").sum())
    waiting_count = int((statuses == "WAITING_TRIGGER").sum())
    lines = [
        "📊 <b>SDE SWING — ACTIVE RECOMMENDATIONS</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"Total actionable: {len(active)} saham",
        "AGE = sesi pasar IDX",
    ]
    if active.empty:
        return "\n".join(lines + ["", "Belum ada rekomendasi aktif."])

    open_rows: list[list[str]] = []
    tp1_trailing: list[str] = []
    for _, row in active[statuses == "OPEN"].iterrows():
        symbol = str(row.get("symbol") or "").strip().upper()
        current_raw = _first_price(row.get("current_price"))
        anchor = _first_price(current_raw, row.get("reference_price"), row.get("entry_price"))
        entry_raw = _first_price(row.get("entry_price"), row.get("reference_price"))
        open_rows.append([
            symbol,
            _compact_price(entry_raw, anchor_price=anchor),
            _compact_price(current_raw, anchor_price=anchor),
            _fmt_pct(row.get("simulated_return_pct")),
            _compact_price(row.get("stop_loss"), anchor_price=anchor),
            _compact_price(row.get("take_profit_1"), anchor_price=anchor),
            _compact_price(row.get("take_profit_2"), anchor_price=anchor),
            f"{max(_int_or_zero(row.get('recommendation_count')), 1)}x",
            str(_session_age(row)),
        ])
        if _is_true(row.get("tp1_hit")):
            tp1_trailing.append(symbol)

    if open_rows:
        lines.extend([
            "",
            f"📈 <b>ACTIVE — {open_count}</b>",
            "<pre>" + html.escape(_render_table(
                ["EMT", "ENTRY", "NOW", "P/L", "SL", "TP1", "TP2", "REC", "AGE"],
                open_rows,
            )) + "</pre>",
        ])
        lines.extend(
            f"🎯 <b>{html.escape(symbol)}</b> · TP1 HIT · 🟢 TRAILING ACTIVE"
            for symbol in tp1_trailing
        )

    waiting_rows: list[list[str]] = []
    for _, row in active[statuses == "WAITING_TRIGGER"].iterrows():
        symbol = str(row.get("symbol") or "").strip().upper()
        current_raw = _first_price(row.get("current_price"))
        anchor = _first_price(current_raw, row.get("reference_price"), row.get("entry_price"))
        low = _first_price(row.get("entry_zone_low"))
        high = _first_price(row.get("entry_zone_high"))
        waiting_rows.append([
            symbol,
            _compact_zone(low, high, anchor_price=anchor),
            _compact_price(current_raw, anchor_price=anchor),
            _gap_text(current_raw, low, high),
            _compact_price(row.get("stop_loss"), anchor_price=anchor),
            _compact_price(row.get("take_profit_1"), anchor_price=anchor),
            _compact_price(row.get("take_profit_2"), anchor_price=anchor),
            f"{max(_int_or_zero(row.get('recommendation_count')), 1)}x",
            str(_session_age(row)),
        ])

    if waiting_rows:
        lines.extend([
            "",
            f"⏳ <b>WAITING ENTRY — {waiting_count}</b>",
            "<pre>" + html.escape(_render_table(
                ["EMT", "ENTRY", "NOW", "GAP", "SL", "TP1", "TP2", "REC", "AGE"],
                waiting_rows,
                left_columns={0},
            )) + "</pre>",
        ])

    return "\n".join(lines)


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


def build_lifecycle_message(
    events: list[Mapping[str, Any]],
    *,
    max_events: int = 20,
) -> str:
    """Render the canonical material Lifecycle Digest card."""
    material = tracker.material_lifecycle_events(events)
    if not material:
        return ""

    limit = max(int(max_events or 1), 1)
    rendered = material[:limit]
    lines = [
        "🔔 <b>SDE SWING — LIFECYCLE DIGEST</b>",
        f"📊 <b>{len(material)} perubahan material</b>",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    body: list[str] = []

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for event in rendered:
        event_type = str(_value(event, "event_type") or "").strip().upper()
        grouped.setdefault(event_type, []).append(event)

    ordered_types = [event_type for event_type in EVENT_GROUP_ORDER if grouped.get(event_type)]
    ordered_types.extend(event_type for event_type in grouped if event_type not in EVENT_GROUP_ORDER)
    for event_type in ordered_types:
        group = grouped[event_type]
        emoji, label = _event_label(event_type)
        if body:
            body.append("")
        body.append(f"{emoji} {label} — {len(group)}")
        for event in group:
            symbol = str(_value(event, "symbol") or "").strip().upper()
            reason = str(_value(event, "event_reason") or event_type or "").replace("_", " ").strip()
            price = fmt_idx_price(
                _value(event, "event_price"),
                anchor_price=_value(event, "event_price"),
            )
            date_text = _event_date(_value(event, "event_date"))
            if event_type == "ENTRY_TRIGGERED":
                body.append(f"◆ {symbol} @ {price} · {date_text} · {reason.title() or '-'}")
            elif event_type == "TP1_HIT":
                body.extend([
                    f"◆ {symbol} @ {price} · {date_text}",
                    "  → Trailing active",
                ])
            elif event_type in {"TP2_HIT", "STOP_LOSS_HIT", "MAX_HOLD_EXIT"}:
                body.append(f"◆ {symbol} @ {price} · {date_text}")
            elif event_type == "EXPIRED":
                expiry = tracker.waiting_expiry_sessions(
                    _value(event, "trigger_expiry_days", 7)
                )
                rec = tracker.as_int(_value(event, "recommendation_count"), 0)
                original = _event_date(
                    _value(event, "original_signal_date", _value(event, "event_date"))
                )
                body.extend([
                    f"◆ {symbol} · {date_text}",
                    f"  Waiting {expiry} sesi perdagangan tanpa entry trigger",
                    f"  REC selama lifecycle: {rec}x",
                    f"  Original signal: {original}",
                ])
            elif event_type == "INVALIDATED_BEFORE_ENTRY":
                body.extend([
                    f"◆ {symbol} · {date_text}",
                    f"  Reason: {reason.title() or '-'}",
                    f"  Price: {price}",
                ])
            else:
                previous = str(_value(event, "previous_status") or "-").replace("_", " ")
                new = str(_value(event, "new_status") or "-").replace("_", " ")
                body.append(f"◆ {symbol} · {previous} -> {new} · {date_text}")

    lines.extend(["", "<pre>" + html.escape("\n".join(body)) + "</pre>"])
    if len(material) > limit:
        lines.extend([
            "",
            f"… {len(material) - limit} perubahan lain tersimpan di ledger.",
        ])
    return "\n".join(lines)


__all__ = [
    "actionable_snapshot",
    "build_active_message",
    "build_lifecycle_message",
]
