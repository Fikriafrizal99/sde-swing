from __future__ import annotations

"""Source router: selects the canonical record for a given record type.

The router is the last component before the feature engine.  It applies the
resolver mode from config, calls the conflict resolver when multiple sources
return a record, and marks fallback usage.  The feature engine and everything
downstream never see source names — only canonical records with provenance.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from modules.data_sources import constants as C
from modules.data_sources.canonical import CanonicalRecord, now_wib
from modules.data_sources.config import DataSourceConfig
from modules.data_sources.conflict_resolver import ConflictResolver, ResolutionResult
from modules.data_sources.data_quality import DataQualityEngine, QualityResult


@dataclass
class RouterResult:
    record: CanonicalRecord | None
    record_type: str
    symbol: str
    market_date: str
    source_used: str
    fallback_used: bool
    quality: QualityResult | None
    resolution: ResolutionResult | None
    routed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "symbol": self.symbol,
            "market_date": self.market_date,
            "source_used": self.source_used,
            "fallback_used": self.fallback_used,
            "routed_at": self.routed_at,
            "quality": self.quality.to_dict() if self.quality else None,
            "resolution": self.resolution.to_dict() if self.resolution else None,
        }


class SourceRouter:
    def __init__(
        self,
        config: DataSourceConfig,
        quality_engine: DataQualityEngine,
        conflict_resolver: ConflictResolver,
    ) -> None:
        self.config = config
        self.quality = quality_engine
        self.resolver = conflict_resolver

    def route(
        self,
        record_type: str,
        symbol: str,
        market_date: str,
        candidates: dict[str, CanonicalRecord],
        *,
        expected_market_date: str | None = None,
        seen_keys: set[tuple[str, ...]] | None = None,
        at: datetime | None = None,
    ) -> RouterResult:
        """Select the best canonical record from source candidates.

        ``candidates`` maps source_name -> record.  The router validates each
        candidate, then resolves conflicts according to the configured mode.
        """
        now = at or now_wib()
        chain = self.config.resolution_chain(record_type)
        mode = self.config.resolver_mode
        ownership = self.config.ownership_for(record_type)

        # Validate each candidate in priority order.
        valid: list[CanonicalRecord] = []
        for source_name in chain:
            record = candidates.get(source_name)
            if record is None:
                continue
            src_cfg = self.config.source(source_name)
            result = self.quality.validate(
                record,
                source_config=src_cfg,
                expected_market_date=expected_market_date,
                seen_keys=seen_keys,
                at=now,
            )
            if result.accepted:
                valid.append(record)
            elif mode == C.MODE_PRIMARY_ONLY:
                # In PRIMARY_ONLY, a rejected primary means no data.
                break

        if not valid:
            return RouterResult(
                record=None,
                record_type=record_type,
                symbol=symbol,
                market_date=market_date,
                source_used="",
                fallback_used=False,
                quality=None,
                resolution=None,
                routed_at=now.isoformat(),
            )

        # Resolve conflicts across valid candidates.
        resolution = self.resolver.resolve(valid)
        winner = resolution.record

        if winner is None:
            return RouterResult(
                record=None,
                record_type=record_type,
                symbol=symbol,
                market_date=market_date,
                source_used="",
                fallback_used=False,
                quality=None,
                resolution=resolution,
                routed_at=now.isoformat(),
            )

        # Determine if a fallback was used.
        primary_source = chain[0] if chain else ""
        fallback_used = winner.source != primary_source
        winner.fallback_used = fallback_used

        # Fail-closed for trading status ambiguity.
        if resolution.fail_closed:
            winner.quality_status = C.QUALITY_REJECTED
            return RouterResult(
                record=winner,
                record_type=record_type,
                symbol=symbol,
                market_date=market_date,
                source_used=winner.source,
                fallback_used=fallback_used,
                quality=None,
                resolution=resolution,
                routed_at=now.isoformat(),
            )

        # Re-validate the winner after conflict resolution.
        src_cfg = self.config.source(winner.source)
        final_quality = self.quality.validate(
            winner,
            source_config=src_cfg,
            expected_market_date=expected_market_date,
            at=now,
        )

        return RouterResult(
            record=winner,
            record_type=record_type,
            symbol=symbol,
            market_date=market_date,
            source_used=winner.source,
            fallback_used=fallback_used,
            quality=final_quality,
            resolution=resolution,
            routed_at=now.isoformat(),
        )