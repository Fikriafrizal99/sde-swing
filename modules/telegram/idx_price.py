from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any


def _decimal(value: Any) -> Decimal | None:
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text or text.lower() in {"nan", "none", "null"}:
            return None
        number = Decimal(text)
        return number if number.is_finite() and number > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def idx_price_fraction(anchor_price: Any) -> int:
    """IDX regular/cash market price fraction based on the session price band."""
    price = _decimal(anchor_price)
    if price is None:
        return 1
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def snap_idx_price(value: Any, *, anchor_price: Any = None, mode: str = "nearest") -> float | None:
    price = _decimal(value)
    if price is None:
        return None
    anchor = _decimal(anchor_price) or price
    tick = Decimal(idx_price_fraction(anchor))
    units = price / tick
    rounding = {
        "nearest": ROUND_HALF_UP,
        "ceil": ROUND_CEILING,
        "floor": ROUND_FLOOR,
    }.get(str(mode).lower(), ROUND_HALF_UP)
    snapped = units.to_integral_value(rounding=rounding) * tick
    return float(snapped)


def fmt_idx_price(
    value: Any,
    *,
    anchor_price: Any = None,
    mode: str = "nearest",
    missing: str = "-",
) -> str:
    snapped = snap_idx_price(value, anchor_price=anchor_price, mode=mode)
    if snapped is None:
        return missing
    return f"{snapped:,.0f}".replace(",", ".")


def snap_idx_zone(low: Any, high: Any, *, anchor_price: Any) -> tuple[float | None, float | None]:
    low_value = snap_idx_price(low, anchor_price=anchor_price, mode="ceil")
    high_value = snap_idx_price(high, anchor_price=anchor_price, mode="floor")
    if low_value is None or high_value is None:
        return low_value, high_value
    if low_value <= high_value:
        return low_value, high_value

    low_raw = _decimal(low)
    high_raw = _decimal(high)
    midpoint_raw = (low_raw + high_raw) / 2 if low_raw is not None and high_raw is not None else None
    midpoint = snap_idx_price(midpoint_raw, anchor_price=anchor_price, mode="nearest")
    return midpoint, midpoint


def fmt_idx_zone(low: Any, high: Any, *, anchor_price: Any, missing: str = "-") -> str:
    low_value, high_value = snap_idx_zone(low, high, anchor_price=anchor_price)
    if low_value is None and high_value is None:
        return missing
    if low_value is None:
        return fmt_idx_price(high_value, anchor_price=anchor_price, missing=missing)
    if high_value is None:
        return fmt_idx_price(low_value, anchor_price=anchor_price, missing=missing)
    if low_value == high_value:
        return fmt_idx_price(low_value, anchor_price=anchor_price, missing=missing)
    return (
        f"{fmt_idx_price(low_value, anchor_price=anchor_price, missing=missing)}"
        f"–{fmt_idx_price(high_value, anchor_price=anchor_price, missing=missing)}"
    )
