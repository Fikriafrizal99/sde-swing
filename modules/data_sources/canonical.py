from __future__ import annotations

"""Canonical typed schema for the multi-source data layer.

Every external record is mapped into one of these dataclasses.  The Final
Decision Engine consumes only these canonical records and never sees the
original source payload, so the ``source`` field is provenance metadata, not a
routing switch downstream.

All timestamps are timezone-aware (Asia/Jakarta / WIB).  Daily candles carry an
explicit ``is_closed`` flag so a partial intraday candle can never be mistaken
for the last closed daily candle.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from typing import Any

from modules.data_sources.constants import (
    CONFLICT_NONE,
    QUALITY_UNVALIDATED,
    SCHEMA_VERSION,
    WIB,
)


def now_wib() -> datetime:
    """Current time in the canonical market timezone."""
    return datetime.now(tz=WIB)


def to_wib(value: datetime) -> datetime:
    """Return ``value`` expressed in WIB, localizing naive datetimes."""
    if value.tzinfo is None:
        return value.replace(tzinfo=WIB)
    return value.astimezone(WIB)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def compute_payload_hash(payload: Any) -> str:
    """Deterministic short hash of a raw provider payload for provenance."""
    if payload is None:
        return ""
    try:
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        text = str(payload)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass
class FieldProvenance:
    """Provenance for a single important field."""

    field_name: str
    source: str
    value: Any
    event_timestamp: str | None = None
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "source": self.source,
            "value": _iso(self.value),
            "event_timestamp": self.event_timestamp,
            "confidence": self.confidence,
        }


@dataclass
class CanonicalRecord:
    """Common provenance envelope shared by every canonical record type.

    Subclasses append their domain fields.  Every field here has a default so
    subclasses can add required-looking fields without dataclass ordering
    errors; callers are expected to always set ``symbol``, ``market_date``,
    ``source`` and the event timestamp.
    """

    symbol: str = ""
    market_date: str = ""  # ISO date (YYYY-MM-DD), the trading session date
    event_timestamp: str = ""  # ISO datetime (WIB) of the underlying event
    received_at: str = ""  # ISO datetime (WIB) when we ingested it
    source: str = ""  # provenance only; never used to branch decision logic
    source_record_id: str = ""
    freshness_seconds: float | None = None
    quality_status: str = QUALITY_UNVALIDATED
    quality_reasons: list[str] = field(default_factory=list)
    fallback_used: bool = False
    conflict_status: str = CONFLICT_NONE
    raw_payload_hash: str = ""
    schema_version: str = SCHEMA_VERSION
    field_provenance: dict[str, dict[str, Any]] = field(default_factory=dict)

    # -- helpers -----------------------------------------------------------
    @property
    def record_type(self) -> str:
        return type(self).__name__

    def domain_fields(self) -> list[str]:
        """Field names that belong to the subclass (not the envelope)."""
        base = {f.name for f in fields(CanonicalRecord)}
        return [f.name for f in fields(self) if f.name not in base]

    def set_provenance(
        self,
        field_name: str,
        source: str,
        value: Any,
        *,
        event_timestamp: str | None = None,
        confidence: float = 1.0,
    ) -> None:
        self.field_provenance[field_name] = FieldProvenance(
            field_name=field_name,
            source=source,
            value=value,
            event_timestamp=event_timestamp or self.event_timestamp,
            confidence=confidence,
        ).to_dict()

    def compute_freshness(self, *, at: datetime | None = None) -> float | None:
        """Populate ``freshness_seconds`` = received_at - event_timestamp."""
        if not self.event_timestamp:
            return self.freshness_seconds
        try:
            event = to_wib(datetime.fromisoformat(self.event_timestamp))
        except ValueError:
            return self.freshness_seconds
        reference = at or (
            to_wib(datetime.fromisoformat(self.received_at))
            if self.received_at
            else now_wib()
        )
        self.freshness_seconds = max(0.0, (reference - event).total_seconds())
        return self.freshness_seconds

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return {key: _iso(value) for key, value in raw.items()}


# ---------------------------------------------------------------------------
# Concrete record types
# ---------------------------------------------------------------------------
@dataclass
class DailyBar(CanonicalRecord):
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    value: float | None = None  # turnover in IDR
    frequency: float | None = None
    previous_close: float | None = None
    adjusted_close: float | None = None
    # True only for a fully closed daily candle. A partial intraday candle
    # must set this False so it can never become the last closed candle.
    is_closed: bool = True


@dataclass
class IntradayQuote(CanonicalRecord):
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_volume: float | None = None
    ask_volume: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    cumulative_volume: float | None = None
    cumulative_value: float | None = None
    # An intraday quote is by definition a partial daily candle.
    is_partial_candle: bool = True


@dataclass
class OrderBookSnapshot(CanonicalRecord):
    # Ladder levels, best (index 0) first.
    bid_prices: list[float] = field(default_factory=list)
    bid_volumes: list[float] = field(default_factory=list)
    ask_prices: list[float] = field(default_factory=list)
    ask_volumes: list[float] = field(default_factory=list)
    best_bid: float | None = None
    best_ask: float | None = None
    spread: float | None = None
    depth_levels: int = 0
    total_bid_volume: float | None = None
    total_ask_volume: float | None = None


@dataclass
class BrokerFlow(CanonicalRecord):
    broker_code: str = ""
    broker_type: str = ""  # ASING / DOMESTIC / UNKNOWN
    side: str = ""  # BUY / SELL
    rank: float | None = None
    net_value: float | None = None
    net_lot: float | None = None
    gross_value: float | None = None
    gross_lot: float | None = None
    frequency: float | None = None
    avg_price: float | None = None


@dataclass
class ForeignFlow(CanonicalRecord):
    foreign_net_value: float | None = None
    foreign_gross_value: float | None = None
    foreign_buy_value: float | None = None
    foreign_sell_value: float | None = None
    foreign_net_pct: float | None = None
    foreign_participation: float | None = None
    # Provenance discriminator preventing double counting against BrokerFlow
    # foreign-broker rows: AGGREGATE_FEED vs DERIVED_FROM_BROKER.
    flow_origin: str = "AGGREGATE_FEED"


@dataclass
class TradingStatus(CanonicalRecord):
    # NORMAL / SUSPEND / HALT / PRE_OPENING / CLOSING / UNKNOWN
    status: str = "UNKNOWN"
    is_tradable: bool = True
    is_suspended: bool = False
    suspend_reason: str = ""
    # True when sources disagree or status is ambiguous -> must fail closed.
    ambiguous: bool = False


@dataclass
class CorporateAction(CanonicalRecord):
    action_type: str = ""  # DIVIDEND / SPLIT / REVERSE_SPLIT / RIGHTS / BONUS
    ex_date: str = ""
    ratio: float | None = None
    cash_amount: float | None = None
    adjustment_factor: float | None = None
    description: str = ""


@dataclass
class MarketIndex(CanonicalRecord):
    index_code: str = ""  # e.g. IHSG / COMPOSITE
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    value: float | None = None
    change_pct: float | None = None
    is_closed: bool = True


@dataclass
class SymbolMetadata(CanonicalRecord):
    name: str = ""
    board: str = ""  # RG / TN / NG
    sector: str = ""
    sub_sector: str = ""
    listed_shares: float | None = None
    is_syariah: bool = False
    is_tradable: bool = True


# Registry so the mapper/quality/router can look up types by name.
RECORD_TYPES: dict[str, type[CanonicalRecord]] = {
    cls.__name__: cls
    for cls in (
        DailyBar,
        IntradayQuote,
        OrderBookSnapshot,
        BrokerFlow,
        ForeignFlow,
        TradingStatus,
        CorporateAction,
        MarketIndex,
        SymbolMetadata,
    )
}

# Required fields for schema validation per record type (beyond the envelope's
# symbol/market_date/source, which the quality engine always requires).
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "DailyBar": ("open", "high", "low", "close", "volume"),
    "IntradayQuote": ("last_price",),
    "OrderBookSnapshot": ("best_bid", "best_ask"),
    "BrokerFlow": ("broker_code", "side"),
    "ForeignFlow": ("foreign_net_value",),
    "TradingStatus": ("status",),
    "CorporateAction": ("action_type", "ex_date"),
    "MarketIndex": ("close",),
    "SymbolMetadata": ("name",),
}
