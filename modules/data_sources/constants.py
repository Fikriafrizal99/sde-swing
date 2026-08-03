from __future__ import annotations

"""Shared string constants for the multi-source data layer.

Keeping every status/reason code in one place makes them testable and keeps
telemetry consistent across the client -> adapter -> quality -> conflict ->
router chain.
"""

from zoneinfo import ZoneInfo

# Canonical timezone for every market date / timestamp in this layer.
WIB = ZoneInfo("Asia/Jakarta")

SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Quality status (per record)
# ---------------------------------------------------------------------------
QUALITY_UNVALIDATED = "UNVALIDATED"
QUALITY_OK = "OK"
QUALITY_WARNING = "WARNING"
QUALITY_REJECTED = "REJECTED"

# ---------------------------------------------------------------------------
# Data-quality reason codes (see section G of the Stage 3 brief)
# ---------------------------------------------------------------------------
INVALID_SCHEMA = "INVALID_SCHEMA"
MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
STALE_DATA = "STALE_DATA"
FUTURE_DATED_DATA = "FUTURE_DATED_DATA"
WRONG_MARKET_DATE = "WRONG_MARKET_DATE"
NON_TRADING_DAY = "NON_TRADING_DAY"
DUPLICATE_RECORD = "DUPLICATE_RECORD"
SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
TIMEZONE_MISMATCH = "TIMEZONE_MISMATCH"
INVALID_OHLC = "INVALID_OHLC"
NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
SOURCE_CONFLICT = "SOURCE_CONFLICT"
SUSPEND_STATUS_AMBIGUOUS = "SUSPEND_STATUS_AMBIGUOUS"
INSUFFICIENT_MICROSTRUCTURE_DATA = "INSUFFICIENT_MICROSTRUCTURE_DATA"
PARTIAL_DAILY_CANDLE = "PARTIAL_DAILY_CANDLE"

ALL_REASON_CODES = (
    INVALID_SCHEMA,
    MISSING_REQUIRED_FIELD,
    STALE_DATA,
    FUTURE_DATED_DATA,
    WRONG_MARKET_DATE,
    NON_TRADING_DAY,
    DUPLICATE_RECORD,
    SYMBOL_MISMATCH,
    TIMEZONE_MISMATCH,
    INVALID_OHLC,
    NEGATIVE_VOLUME,
    SOURCE_CONFLICT,
    SUSPEND_STATUS_AMBIGUOUS,
    INSUFFICIENT_MICROSTRUCTURE_DATA,
    PARTIAL_DAILY_CANDLE,
)

# Reason codes that always force fail-closed rejection.
HARD_REJECT_CODES = frozenset(
    {
        INVALID_SCHEMA,
        MISSING_REQUIRED_FIELD,
        FUTURE_DATED_DATA,
        WRONG_MARKET_DATE,
        NON_TRADING_DAY,
        INVALID_OHLC,
        NEGATIVE_VOLUME,
        SUSPEND_STATUS_AMBIGUOUS,
    }
)

# ---------------------------------------------------------------------------
# Conflict status / resolution
# ---------------------------------------------------------------------------
CONFLICT_NONE = "NONE"
CONFLICT_RESOLVED = "RESOLVED"
CONFLICT_UNRESOLVED = "UNRESOLVED"
CONFLICT_FAIL_CLOSED = "FAIL_CLOSED"

SEVERITY_NONE = "NONE"
SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

# Resolver / router modes
MODE_PRIMARY_ONLY = "PRIMARY_ONLY"
MODE_PRIMARY_WITH_FALLBACK = "PRIMARY_WITH_FALLBACK"
MODE_CONSENSUS = "CONSENSUS"
MODE_SHADOW_COMPARE = "SHADOW_COMPARE"
ALL_RESOLVER_MODES = (
    MODE_PRIMARY_ONLY,
    MODE_PRIMARY_WITH_FALLBACK,
    MODE_CONSENSUS,
    MODE_SHADOW_COMPARE,
)

# ---------------------------------------------------------------------------
# Source health status
# ---------------------------------------------------------------------------
HEALTH_HEALTHY = "HEALTHY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_UNAVAILABLE = "UNAVAILABLE"
HEALTH_NOT_CONFIGURED = "NOT_CONFIGURED"

# ---------------------------------------------------------------------------
# Fallback / config policy
# ---------------------------------------------------------------------------
FAIL_OPEN = "FAIL_OPEN"
FAIL_CLOSED = "FAIL_CLOSED"

FALLBACK_ALLOW = "ALLOW"
FALLBACK_DENY = "DENY"

# ZAPI configuration status
ZAPI_DOCUMENTATION_NOT_CONFIGURED = "ZAPI_DOCUMENTATION_NOT_CONFIGURED"
ZAPI_LIVE = "ZAPI_LIVE"
ZAPI_MOCK = "ZAPI_MOCK"
