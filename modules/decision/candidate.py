from __future__ import annotations

"""Single candidate contract shared by Final Watchlist and Final Decision."""

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

FINAL_ACTIONS = {"BUY", "WATCH", "WAIT", "AVOID", "NO_DATA"}


@dataclass
class CanonicalCandidate:
    symbol: str
    trade_date: str
    market_regime: str = "UNKNOWN"
    technical_score: float | None = None
    broker_score: float | None = None
    foreign_score: float | None = None
    liquidity_score: float | None = None
    setup_score: float | None = None
    fusion_score: float | None = None
    hard_blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entry: float | None = None
    stop_loss: float | None = None
    target_1: float | None = None
    target_2: float | None = None
    risk_reward: float | None = None
    final_action: str = "NO_DATA"
    confidence: float = 0.0
    source_provenance: dict[str, Any] = field(default_factory=dict)
    snapshot_ids: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any], *, source_provenance: dict[str, Any] | None = None, snapshot_ids: dict[str, str] | None = None) -> "CanonicalCandidate":
        values = dict(row)
        aliases = {
            "trade_date": "trade_date",
            "technical_score": "Technical_Score",
            "broker_score": "Broker_Score",
            "foreign_score": "Foreign_Score",
            "liquidity_score": "Liquidity_Score",
            "setup_score": "Setup_Score",
            "fusion_score": "Fusion_Score",
            "market_regime": "Market_Regime",
            "final_action": "Final_Action",
            "confidence": "Confidence",
            "hard_blockers": "Hard_Blockers",
            "warnings": "Warnings",
        }
        for target, alias in aliases.items():
            if target not in values and alias in values:
                values[target] = values[alias]
        if "final_action" not in values and "Decision_Status_Final" in values:
            values["final_action"] = values["Decision_Status_Final"]
        def number(key: str) -> float | None:
            value = values.get(key)
            if value in (None, "", "nan"):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        def list_value(key: str) -> list[str]:
            value = values.get(key, [])
            if isinstance(value, str):
                return [item.strip() for item in value.split("|") if item.strip()]
            return [str(item) for item in (value or [])]
        raw_action = str(values.get("final_action", "NO_DATA")).strip().upper() or "NO_DATA"
        action_aliases = {"BUY READY": "BUY", "BUY ON TRIGGER": "BUY", "BUY CONFIRMED": "BUY", "BUY CANDIDATE": "WATCH"}
        raw_action = action_aliases.get(raw_action, raw_action)
        return cls(
            symbol=str(values.get("symbol", values.get("Symbol", ""))).strip().upper(),
            trade_date=str(values.get("trade_date", "")),
            market_regime=str(values.get("market_regime", "UNKNOWN")),
            technical_score=number("technical_score"),
            broker_score=number("broker_score"),
            foreign_score=number("foreign_score"),
            liquidity_score=number("liquidity_score"),
            setup_score=number("setup_score"),
            fusion_score=number("fusion_score"),
            hard_blockers=list_value("hard_blockers"),
            warnings=list_value("warnings"),
            entry=number("entry"),
            stop_loss=number("stop_loss"),
            target_1=number("target_1"),
            target_2=number("target_2"),
            risk_reward=number("risk_reward"),
            final_action=raw_action,
            confidence=number("confidence") or 0.0,
            source_provenance=dict(source_provenance or values.get("source_provenance", {}) or {}),
            snapshot_ids=dict(snapshot_ids or values.get("snapshot_ids", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_candidate(candidate: CanonicalCandidate) -> list[str]:
    errors: list[str] = []
    if not candidate.symbol:
        errors.append("SYMBOL_MISSING")
    if not candidate.trade_date:
        errors.append("TRADE_DATE_MISSING")
    if candidate.final_action not in FINAL_ACTIONS:
        errors.append(f"FINAL_ACTION_INVALID:{candidate.final_action}")
    if not candidate.source_provenance:
        errors.append("SOURCE_PROVENANCE_MISSING")
    if not candidate.snapshot_ids:
        errors.append("SNAPSHOT_IDS_MISSING")
    if not 0.0 <= float(candidate.confidence) <= 1.0:
        errors.append("CONFIDENCE_OUT_OF_RANGE")
    return errors
