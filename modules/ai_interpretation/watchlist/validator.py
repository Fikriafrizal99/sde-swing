from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping


_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:Rp\s*)?[-+]?\d+(?:[.,]\d+)*(?:%|x)?",
    re.IGNORECASE,
)
_UNIT_SCALES = {
    "k": 1_000.0,
    "ribu": 1_000.0,
    "thousand": 1_000.0,
    "m": 1_000_000.0,
    "juta": 1_000_000.0,
    "million": 1_000_000.0,
    "b": 1_000_000_000.0,
    "miliar": 1_000_000_000.0,
    "billion": 1_000_000_000.0,
    "t": 1_000_000_000_000.0,
    "triliun": 1_000_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
}


def _variants(token: str) -> set[str]:
    value = str(token or "").strip().lower()
    value = value.replace("rp", "").replace("%", "").replace("x", "")
    value = value.lstrip("+-").strip()
    if not value:
        return set()
    variants = {value}
    compact = value.replace(".", "").replace(",", "")
    if compact:
        variants.add(compact)
    variants.add(value.replace(",", "."))
    variants.add(value.replace(".", ","))
    return {item.strip(".,") for item in variants if item.strip(".,")}


def _parse_number(token: str) -> float | None:
    raw = str(token or "").strip().lower()
    raw = raw.replace("rp", "").replace("%", "").replace("x", "")
    raw = raw.strip(" .,\t\r\n")
    if not raw:
        return None

    sign = -1.0 if raw.startswith("-") else 1.0
    raw = raw.lstrip("+-")
    if not raw:
        return None

    # Indonesian/financial presentation can use either 3.120 for a grouped
    # integer or 42,6 for a decimal. Preserve decimal intent while accepting
    # common thousand separators.
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        left, right = raw.rsplit(",", 1)
        if len(right) == 3 and left.replace(",", "").isdigit():
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[0].isdigit()):
            raw = raw.replace(".", "")

    try:
        return sign * float(raw)
    except ValueError:
        return None


def _context_numeric_values(context: Mapping[str, Any]) -> list[float]:
    rendered = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    values: list[float] = []
    for token in _NUMBER_RE.findall(rendered):
        parsed = _parse_number(token)
        if parsed is not None and math.isfinite(parsed):
            values.append(parsed)
    return values


def _close(left: float, right: float) -> bool:
    tolerance = max(1e-9, abs(right) * 1e-6)
    return abs(left - right) <= tolerance


def _following_unit(text: str, end: int) -> tuple[str, float] | None:
    tail = text[end : end + 24].lower()
    match = re.match(r"\s*(?:rupiah\s*)?([a-z]+)", tail)
    if not match:
        return None
    unit = match.group(1)
    scale = _UNIT_SCALES.get(unit)
    return (unit, scale) if scale is not None else None


def validate_numbers(text: str, context: Mapping[str, Any]) -> None:
    """Reject invented numbers while allowing faithful presentation transforms.

    Exact SDE numbers may be formatted with Indonesian grouping/decimal marks.
    Two common semantic-equivalent transforms are also accepted:

    - ratio/fraction -> percent, but only when the AI token carries `%`;
    - full nominal -> thousand/million/billion/trillion form, but only when a
      matching magnitude word/suffix immediately follows the number.

    The transform is deliberately context-sensitive so a broker net flow of
    42.6B cannot accidentally authorize an unrelated plain level `42.6`.
    """
    rendered = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    exact_allowed: set[str] = set()
    for token in _NUMBER_RE.findall(rendered):
        exact_allowed.update(_variants(token))
    numeric_values = _context_numeric_values(context)

    for match in _NUMBER_RE.finditer(str(text or "")):
        token = match.group(0)
        variants = _variants(token)
        if variants and not variants.isdisjoint(exact_allowed):
            continue

        value = _parse_number(token)
        if value is None:
            continue

        # RR prose may render the conventional leading `1:` even when the
        # artifact stores only the reward-side value.
        if _close(abs(value), 1.0):
            after = str(text or "")[match.end() : match.end() + 2]
            if after.startswith(":"):
                continue

        # `/100` is a presentation denominator for SDE scores, not a new fact.
        if _close(abs(value), 100.0):
            before = str(text or "")[max(0, match.start() - 2) : match.start()]
            if "/" in before:
                continue

        if token.rstrip().endswith("%"):
            pct = value
            if any(_close(pct, raw) or _close(pct, raw * 100.0) for raw in numeric_values):
                continue

        unit = _following_unit(str(text or ""), match.end())
        if unit is not None:
            _, scale = unit
            scaled = value * scale
            if any(_close(scaled, raw) for raw in numeric_values):
                continue

        raise ValueError(f"AI introduced unsupported number: {token}")


__all__ = ["validate_numbers"]
