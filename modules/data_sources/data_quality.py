from __future__ import annotations

"""Data quality engine for canonical records.

Every canonical record is validated here before it can reach the conflict
resolver or feature engine.  Each rejection carries a reason code and enough
telemetry for the source health monitor.  The rules encode the Stage 3 brief:

* Future-dated data is rejected.
* A partial daily candle can never be the last closed candle.
* Suspend ambiguity fails closed.
* Missing microstructure (spread / depth / frequency) can never be NORMAL
  liquidity — it is surfaced as INSUFFICIENT_MICROSTRUCTURE_DATA.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

from modules.data_sources import constants as C
from modules.data_sources.canonical import CanonicalRecord, now_wib, to_wib
from modules.data_sources.config import SourceConfig
from modules.market_calendar.idx_calendar import is_idx_trading_day
from modules.data_sources.canonical import REQUIRED_FIELDS


MARKET_SESSION_RECORD_TYPES = frozenset(
    {
        "DailyBar",
        "IntradayQuote",
        "OrderBookSnapshot",
        "BrokerFlow",
        "ForeignFlow",
        "TradingStatus",
        "MarketIndex",
    }
)


@dataclass
class QualityFinding:
    reason: str
    severity: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "severity": self.severity, "detail": self.detail}


@dataclass
class QualityResult:
    accepted: bool
    status: str
    findings: list[QualityFinding] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [f.reason for f in self.findings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "findings": [f.to_dict() for f in self.findings],
        }


def _as_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


class DataQualityEngine:
    """Stateless validator (holds only config-derived context)."""

    def __init__(
        self,
        *,
        holidays: Iterable[Any] = (),
        special_trading_days: Iterable[Any] = (),
        min_microstructure_metrics: int = 2,
    ) -> None:
        self.holidays = list(holidays)
        self.special_trading_days = list(special_trading_days)
        self.min_microstructure_metrics = int(min_microstructure_metrics)

    # -- public API --------------------------------------------------------
    def validate(
        self,
        record: CanonicalRecord,
        *,
        source_config: SourceConfig | None = None,
        expected_market_date: str | None = None,
        seen_keys: set[tuple[str, ...]] | None = None,
        at: datetime | None = None,
    ) -> QualityResult:
        findings: list[QualityFinding] = []
        now = at or now_wib()

        findings += self._check_schema(record)
        findings += self._check_required(record, source_config)
        findings += self._check_symbol(record)
        findings += self._check_dates(record, expected_market_date, now)
        findings += self._check_timezone(record)
        findings += self._check_freshness(record, source_config, now)
        findings += self._check_duplicate(record, seen_keys)
        findings += self._check_domain(record)

        # Determine outcome. Any hard-reject reason => fail closed.
        reasons = {f.reason for f in findings}
        hard = reasons & C.HARD_REJECT_CODES
        if hard:
            status = C.QUALITY_REJECTED
            accepted = False
        elif findings:
            status = C.QUALITY_WARNING
            accepted = True
        else:
            status = C.QUALITY_OK
            accepted = True

        record.quality_status = status
        record.quality_reasons = sorted(reasons)
        return QualityResult(accepted=accepted, status=status, findings=findings)

    # -- individual checks -------------------------------------------------
    def _check_schema(self, record: CanonicalRecord) -> list[QualityFinding]:
        if record.record_type not in REQUIRED_FIELDS and not isinstance(record, CanonicalRecord):
            return [QualityFinding(C.INVALID_SCHEMA, C.SEVERITY_CRITICAL, record.record_type)]
        if not record.schema_version:
            return [QualityFinding(C.INVALID_SCHEMA, C.SEVERITY_HIGH, "missing schema_version")]
        return []

    def _check_required(
        self, record: CanonicalRecord, source_config: SourceConfig | None
    ) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        # Envelope essentials.
        if not record.symbol:
            findings.append(QualityFinding(C.MISSING_REQUIRED_FIELD, C.SEVERITY_HIGH, "symbol"))
        if not record.market_date:
            findings.append(QualityFinding(C.MISSING_REQUIRED_FIELD, C.SEVERITY_HIGH, "market_date"))
        if not record.source:
            findings.append(QualityFinding(C.MISSING_REQUIRED_FIELD, C.SEVERITY_MEDIUM, "source"))
        # Per-type domain requireds.
        for name in REQUIRED_FIELDS.get(record.record_type, ()):  # type: ignore[arg-type]
            if getattr(record, name, None) in (None, ""):
                findings.append(
                    QualityFinding(C.MISSING_REQUIRED_FIELD, C.SEVERITY_HIGH, f"{record.record_type}.{name}")
                )
        # Source-declared requireds.
        if source_config is not None:
            for name in source_config.required_fields:
                if getattr(record, name, None) in (None, ""):
                    findings.append(
                        QualityFinding(C.MISSING_REQUIRED_FIELD, C.SEVERITY_MEDIUM, f"required:{name}")
                    )
        return findings

    def _check_symbol(self, record: CanonicalRecord) -> list[QualityFinding]:
        symbol = str(record.symbol or "")
        if symbol and symbol != symbol.strip().upper():
            return [QualityFinding(C.SYMBOL_MISMATCH, C.SEVERITY_MEDIUM, symbol)]
        return []

    def _check_dates(
        self, record: CanonicalRecord, expected_market_date: str | None, now: datetime
    ) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        market = _as_date(record.market_date)
        if market is None:
            return findings  # missing-field already handled

        # Future-dated data is rejected.
        if market > now.date():
            findings.append(QualityFinding(C.FUTURE_DATED_DATA, C.SEVERITY_CRITICAL, market.isoformat()))

        # Event timestamp cannot be in the future either.
        event = record.event_timestamp
        if event:
            try:
                event_dt = to_wib(datetime.fromisoformat(event))
                if event_dt > now:
                    findings.append(
                        QualityFinding(C.FUTURE_DATED_DATA, C.SEVERITY_CRITICAL, f"event {event}")
                    )
            except ValueError:
                findings.append(QualityFinding(C.INVALID_SCHEMA, C.SEVERITY_MEDIUM, "event_timestamp"))

        # Only exchange-session records require an open BEI session. Metadata
        # and corporate-action records may legitimately arrive on closed days.
        if (
            record.record_type in MARKET_SESSION_RECORD_TYPES
            and not is_idx_trading_day(market, self.holidays, self.special_trading_days)
        ):
            findings.append(QualityFinding(C.NON_TRADING_DAY, C.SEVERITY_HIGH, market.isoformat()))

        # Expected market date mismatch.
        if expected_market_date:
            expected = _as_date(expected_market_date)
            if expected is not None and market != expected:
                findings.append(
                    QualityFinding(
                        C.WRONG_MARKET_DATE,
                        C.SEVERITY_HIGH,
                        f"got {market.isoformat()} expected {expected.isoformat()}",
                    )
                )
        return findings

    def _check_timezone(self, record: CanonicalRecord) -> list[QualityFinding]:
        for attr in ("event_timestamp", "received_at"):
            value = getattr(record, attr, "")
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return [QualityFinding(C.INVALID_SCHEMA, C.SEVERITY_MEDIUM, attr)]
            if parsed.tzinfo is None:
                return [QualityFinding(C.TIMEZONE_MISMATCH, C.SEVERITY_MEDIUM, f"{attr} naive")]
        return []

    def _check_freshness(
        self, record: CanonicalRecord, source_config: SourceConfig | None, now: datetime
    ) -> list[QualityFinding]:
        if source_config is None:
            return []
        record.compute_freshness(at=now)
        limit = source_config.maximum_stale_seconds
        if record.freshness_seconds is not None and limit and record.freshness_seconds > limit:
            return [
                QualityFinding(
                    C.STALE_DATA,
                    C.SEVERITY_MEDIUM,
                    f"{record.freshness_seconds:.0f}s > {limit:.0f}s",
                )
            ]
        return []

    def _check_duplicate(
        self, record: CanonicalRecord, seen_keys: set[tuple[str, ...]] | None
    ) -> list[QualityFinding]:
        if seen_keys is None:
            return []
        key = self.dedup_key(record)
        if key in seen_keys:
            return [QualityFinding(C.DUPLICATE_RECORD, C.SEVERITY_MEDIUM, "|".join(key))]
        seen_keys.add(key)
        return []

    @staticmethod
    def dedup_key(record: CanonicalRecord) -> tuple[str, ...]:
        extra = ""
        broker = getattr(record, "broker_code", "")
        side = getattr(record, "side", "")
        if broker or side:
            extra = f"{broker}:{side}"
        return (
            record.record_type,
            str(record.symbol),
            str(record.market_date),
            str(record.source),
            extra,
        )

    def _check_domain(self, record: CanonicalRecord) -> list[QualityFinding]:
        rtype = record.record_type
        if rtype in {"DailyBar", "MarketIndex"}:
            return self._check_ohlc(record)
        if rtype == "IntradayQuote":
            return self._check_intraday(record)
        if rtype == "OrderBookSnapshot":
            return self._check_orderbook(record)
        if rtype == "BrokerFlow":
            return self._check_broker(record)
        if rtype == "TradingStatus":
            return self._check_trading_status(record)
        return []

    def _check_ohlc(self, record: CanonicalRecord) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        o = getattr(record, "open", None)
        h = getattr(record, "high", None)
        low = getattr(record, "low", None)
        c = getattr(record, "close", None)
        v = getattr(record, "volume", None)
        vals = [x for x in (o, h, low, c) if x is not None]
        if vals:
            if any(x < 0 for x in vals):
                findings.append(QualityFinding(C.INVALID_OHLC, C.SEVERITY_HIGH, "negative price"))
            if h is not None and low is not None and h < low:
                findings.append(QualityFinding(C.INVALID_OHLC, C.SEVERITY_HIGH, "high<low"))
            if h is not None:
                hi_ok = all(h + 1e-9 >= x for x in (o, c, low) if x is not None)
                if not hi_ok:
                    findings.append(QualityFinding(C.INVALID_OHLC, C.SEVERITY_HIGH, "high<max(o,c,l)"))
            if low is not None:
                lo_ok = all(low - 1e-9 <= x for x in (o, c, h) if x is not None)
                if not lo_ok:
                    findings.append(QualityFinding(C.INVALID_OHLC, C.SEVERITY_HIGH, "low>min(o,c,h)"))
        if v is not None and v < 0:
            findings.append(QualityFinding(C.NEGATIVE_VOLUME, C.SEVERITY_HIGH, str(v)))
        # Partial daily candle must never masquerade as a closed candle.
        if getattr(record, "is_closed", True) is False:
            findings.append(
                QualityFinding(C.PARTIAL_DAILY_CANDLE, C.SEVERITY_MEDIUM, "is_closed=False")
            )
        return findings

    def _check_intraday(self, record: CanonicalRecord) -> list[QualityFinding]:
        # An intraday quote is a partial candle by definition; flag if someone
        # mislabels it as a closed daily candle.
        if getattr(record, "is_partial_candle", True) is False:
            return [QualityFinding(C.INVALID_SCHEMA, C.SEVERITY_MEDIUM, "intraday marked non-partial")]
        vol = getattr(record, "cumulative_volume", None)
        if vol is not None and vol < 0:
            return [QualityFinding(C.NEGATIVE_VOLUME, C.SEVERITY_MEDIUM, str(vol))]
        return []

    def _check_orderbook(self, record: CanonicalRecord) -> list[QualityFinding]:
        findings: list[QualityFinding] = []
        best_bid = getattr(record, "best_bid", None)
        best_ask = getattr(record, "best_ask", None)
        depth = int(getattr(record, "depth_levels", 0) or 0)
        # Missing spread/depth cannot become NORMAL liquidity — surface it.
        metrics_present = sum(
            1 for x in (best_bid, best_ask, getattr(record, "spread", None)) if x is not None
        )
        if metrics_present < self.min_microstructure_metrics or depth <= 0:
            findings.append(
                QualityFinding(
                    C.INSUFFICIENT_MICROSTRUCTURE_DATA,
                    C.SEVERITY_MEDIUM,
                    f"metrics={metrics_present} depth={depth}",
                )
            )
        if best_bid is not None and best_ask is not None and best_ask < best_bid:
            findings.append(QualityFinding(C.INVALID_OHLC, C.SEVERITY_MEDIUM, "ask<bid"))
        return findings

    def _check_broker(self, record: CanonicalRecord) -> list[QualityFinding]:
        side = str(getattr(record, "side", "")).upper()
        if side not in {"BUY", "SELL"}:
            return [QualityFinding(C.INVALID_SCHEMA, C.SEVERITY_MEDIUM, f"side={side}")]
        return []

    def _check_trading_status(self, record: CanonicalRecord) -> list[QualityFinding]:
        # Suspend ambiguity must fail closed.
        if getattr(record, "ambiguous", False):
            return [
                QualityFinding(C.SUSPEND_STATUS_AMBIGUOUS, C.SEVERITY_CRITICAL, "ambiguous suspend")
            ]
        return []
