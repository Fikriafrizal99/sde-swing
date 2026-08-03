from __future__ import annotations

"""Adapters from legacy decision frames to the canonical candidate contract."""

from typing import Any, Iterable, Mapping

from .candidate import CanonicalCandidate, validate_candidate


def canonicalize_candidates(
    rows: Iterable[Mapping[str, Any]],
    *,
    trade_date: str,
    source_provenance: dict[str, Any],
    snapshot_ids: dict[str, str],
) -> tuple[list[CanonicalCandidate], list[dict[str, Any]]]:
    candidates: list[CanonicalCandidate] = []
    errors: list[dict[str, Any]] = []
    for row in rows:
        candidate = CanonicalCandidate.from_mapping(
            row,
            source_provenance=source_provenance,
            snapshot_ids=snapshot_ids,
        )
        if not candidate.trade_date:
            candidate.trade_date = trade_date
        validation = validate_candidate(candidate)
        if validation:
            errors.append({"symbol": candidate.symbol, "errors": validation})
        candidates.append(candidate)
    return candidates, errors

