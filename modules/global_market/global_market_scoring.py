from __future__ import annotations

from typing import Any


VALID_STATUSES = {"VALID", "DELAYED_ACCEPTED"}


def compute_global_sentiment(instruments: list[dict[str, Any]], registry: dict[str, Any]) -> dict[str, Any]:
    enabled_count = max(len(instruments), 0)
    valid_rows = [row for row in instruments if row.get("freshness_status") in VALID_STATUSES and row.get("change_pct") is not None]
    missing = [row["instrument"] for row in instruments if row not in valid_rows]
    coverage = len(valid_rows) / enabled_count if enabled_count else 0.0
    minimum = float(registry.get("minimum_sentiment_coverage_ratio", 0.5))
    if enabled_count == 0 or coverage < minimum:
        return {
            "positive_instruments": [],
            "negative_instruments": [],
            "neutral_instruments": [],
            "missing_instruments": missing,
            "coverage_ratio": coverage,
            "sentiment_score": 0.0,
            "sentiment_state": "INSUFFICIENT_DATA",
            "reason": f"coverage {coverage:.0%} di bawah minimum {minimum:.0%}",
        }
    score = 0.0
    total_weight = 0.0
    positive: list[str] = []
    negative: list[str] = []
    neutral: list[str] = []
    for row in valid_rows:
        change = float(row.get("change_pct") or 0.0)
        weight = abs(float(row.get("weight", 1.0) or 1.0))
        inverse = bool(row.get("inverse_sentiment", False))
        direction = -change if inverse else change
        total_weight += weight
        if direction > 0.15:
            positive.append(row["instrument"])
            score += weight
        elif direction < -0.15:
            negative.append(row["instrument"])
            score -= weight
        else:
            neutral.append(row["instrument"])
    normalized = score / total_weight if total_weight else 0.0
    if normalized >= 0.25:
        state = "RISK_ON"
    elif normalized <= -0.25:
        state = "RISK_OFF"
    else:
        state = "NEUTRAL"
    return {
        "positive_instruments": positive,
        "negative_instruments": negative,
        "neutral_instruments": neutral,
        "missing_instruments": missing,
        "coverage_ratio": coverage,
        "sentiment_score": normalized,
        "sentiment_state": state,
        "reason": f"{len(positive)} positif, {len(negative)} negatif, {len(neutral)} netral, {len(missing)} tidak tersedia",
    }

