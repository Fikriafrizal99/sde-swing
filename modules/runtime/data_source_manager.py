from __future__ import annotations

"""One source manager shared by every SDE job.

The manager owns provider readiness and canonical routing.  Jobs receive
metadata and canonical records, never source-specific credentials or transport
objects.  File-based broker exports remain valid when the Stockbit API key is
empty; a mock is explicitly labelled MOCK and never LIVE.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from modules.broker_bridge.broker_raw import normalize_broker_raw_frame, read_normalized_broker_raw
from modules.data_sources.canonical import (
    BrokerFlow,
    CanonicalRecord,
    DailyBar,
    ForeignFlow,
    compute_payload_hash,
    now_wib,
)
from modules.data_sources.config import DataSourceConfig, SourceConfig, load_data_source_config, parse_config
from modules.data_sources.conflict_resolver import ConflictResolver
from modules.data_sources.data_quality import DataQualityEngine
from modules.data_sources.health import SourceHealthMonitor
from modules.data_sources.router import RouterResult, SourceRouter
from modules.data_sources.stockbit_adapter import StockbitAdapter
from modules.data_sources.zapi_idx_adapter import ZapiIdxAdapter, ZapiIdxClient


@dataclass
class ProviderMetadata:
    source: str
    status: str
    mode: str
    enabled: bool
    configured: bool
    reason: str = ""
    fallback_available: bool = False
    coverage_ratio: float = 0.0
    records_loaded: int = 0
    records_valid: int = 0
    providers_attempted: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "mode": self.mode,
            "enabled": self.enabled,
            "configured": self.configured,
            "reason": self.reason,
            "fallback_available": self.fallback_available,
            "coverage_ratio": round(self.coverage_ratio, 6),
            "records_loaded": self.records_loaded,
            "records_valid": self.records_valid,
            "providers_attempted": list(self.providers_attempted),
        }


class DataSourceManager:
    """Readiness, routing, canonical mapping and source telemetry facade."""

    def __init__(
        self,
        config_path: str | Path = "config/data_sources.json",
        *,
        config: DataSourceConfig | dict[str, Any] | None = None,
        root: Path | None = None,
        mode: str = "LIVE",
        run_id: str = "",
        force_mock: bool = False,
        file_roots: Mapping[str, str | Path] | None = None,
    ) -> None:
        self.root = root or Path(__file__).resolve().parents[2]
        resolved = Path(config_path)
        if not resolved.is_absolute():
            resolved = self.root / resolved
        self.config_path = resolved
        self.config: DataSourceConfig = (
            config if isinstance(config, DataSourceConfig)
            else parse_config(config) if isinstance(config, dict)
            else load_data_source_config(resolved)
        )
        self.mode = str(mode or "LIVE").upper()
        self.run_id = run_id
        self.force_mock = force_mock or self.mode == "MOCK"
        self.file_roots = {key: Path(value) for key, value in (file_roots or {}).items()}
        self.health = SourceHealthMonitor()
        self.metadata: dict[str, ProviderMetadata] = {}
        self._adapters: dict[str, Any] = {}
        self._clients: dict[str, Any] = {}
        self._build_provider_registry()
        self.router = SourceRouter(
            self.config,
            DataQualityEngine(),
            ConflictResolver(
                numeric_tolerance_pct=float(self.config.conflict_defaults.get("numeric_tolerance_pct", 0.005)),
            ),
        )

    def _build_provider_registry(self) -> None:
        for source_name, source_cfg in self.config.sources.items():
            self.health._get(source_name).configured = self._source_configured(source_cfg)
            if source_name == "ZAPI_IDX":
                client = ZapiIdxClient.from_config(source_cfg, force_mock=self.force_mock)
                self._clients[source_name] = client
                self._adapters[source_name] = ZapiIdxAdapter(client)
            elif source_name == "STOCKBIT":
                self._adapters[source_name] = StockbitAdapter()
            self.metadata[source_name] = self._metadata_for(source_name, source_cfg)

    def _source_configured(self, source_cfg: SourceConfig) -> bool:
        if source_cfg.name == "ZAPI_IDX":
            return bool(source_cfg.enabled and source_cfg.documentation_configured and source_cfg.has_credentials())
        if source_cfg.name == "STOCKBIT":
            return bool(source_cfg.api_key())
        return bool(source_cfg.enabled)

    def _metadata_for(self, source_name: str, source_cfg: SourceConfig) -> ProviderMetadata:
        if self.force_mock:
            return ProviderMetadata(source_name, "MOCK", "MOCK", source_cfg.enabled, False, "RUNTIME_MOCK_MODE", fallback_available=True)
        if self.mode in {"CACHE", "FALLBACK"} and source_cfg.enabled:
            available = self._file_source_available(source_name)
            return ProviderMetadata(
                source_name,
                "READY" if available else "NOT_CONFIGURED",
                self.mode if available else "NOT_CONFIGURED",
                source_cfg.enabled,
                available,
                "CACHE_OR_FALLBACK_ARTIFACT_NOT_FOUND" if not available else "",
                fallback_available=True,
            )
        if self.mode == "FILE" and source_cfg.enabled and self._file_source_available(source_name):
            return ProviderMetadata(source_name, "READY", "FILE", True, True, "", fallback_available=True)
        if source_name == "ZAPI_IDX":
            client = self._clients.get(source_name)
            if self.force_mock:
                return ProviderMetadata(source_name, "MOCK", "MOCK", source_cfg.enabled, False, "FORCED_MOCK", fallback_available=True)
            if client is not None and client.is_configured():
                return ProviderMetadata(source_name, "READY", "LIVE", True, True, "", fallback_available=True)
            return ProviderMetadata(source_name, "NOT_CONFIGURED", "NOT_CONFIGURED", source_cfg.enabled, False, "ZAPI_DOCUMENTATION_OR_CREDENTIALS_MISSING", fallback_available=True)
        if source_name == "STOCKBIT":
            if source_cfg.api_key():
                return ProviderMetadata(source_name, "READY", "LIVE", source_cfg.enabled, True, "", fallback_available=True)
            if self._file_source_available("STOCKBIT"):
                return ProviderMetadata(source_name, "NOT_CONFIGURED", "FILE", source_cfg.enabled, False, "STOCKBIT_API_KEY_EMPTY_FILE_FALLBACK", fallback_available=True)
            return ProviderMetadata(source_name, "NOT_CONFIGURED", "NOT_CONFIGURED", source_cfg.enabled, False, "STOCKBIT_API_KEY_EMPTY", fallback_available=False)
        if source_name == "HISTORICAL_PROVIDER":
            available = self._file_source_available(source_name)
            return ProviderMetadata(source_name, "READY" if available else "NOT_CONFIGURED", "FILE" if available else "NOT_CONFIGURED", source_cfg.enabled, available, "" if available else "HISTORICAL_FILE_NOT_FOUND", fallback_available=True)
        return ProviderMetadata(source_name, "READY" if source_cfg.enabled else "NOT_CONFIGURED", "FILE" if source_cfg.enabled else "NOT_CONFIGURED", source_cfg.enabled, source_cfg.enabled)

    def _file_source_available(self, source_name: str) -> bool:
        candidates = []
        if source_name == "STOCKBIT":
            candidates.extend([
                self.file_roots.get("broker_summary"),
                self.file_roots.get("broker_raw"),
                self.root / "data/input/broker/BROKER_SUMMARY_LATEST.csv",
                self.root / "data/input/broker/BROKER_RAW_LATEST.csv",
                self.root / "data/input/broker/BROKER_SUMMARY_LATEST.json",
                self.root / "data/input/broker/BROKER_RAW_LATEST.json",
            ])
        elif source_name == "HISTORICAL_PROVIDER":
            candidates.extend([
                self.file_roots.get("historical"),
                self.root / "data/output/historical/by_symbol",
                self.root / "data/input/IHSG.csv",
            ])
        return any(bool(path and Path(path).exists()) for path in candidates)

    def readiness(self) -> dict[str, Any]:
        return {name: metadata.to_dict() for name, metadata in sorted(self.metadata.items())}

    def _selected_metadata(self, record_type: str) -> tuple[str, ProviderMetadata | None]:
        chain = self.config.resolution_chain(record_type)
        primary = chain[0] if chain else "INTERNAL"
        return primary, self.metadata.get(primary)

    def provider_metadata(
        self,
        *,
        record_type: str | None = None,
        providers_attempted: list[str] | None = None,
        fallback_used: bool = False,
        mock_used: bool | None = None,
        coverage_ratio: float | None = None,
    ) -> dict[str, Any]:
        primary, selected = self._selected_metadata(record_type) if record_type else ("", None)
        attempted = list(providers_attempted or [])
        if not attempted and primary:
            attempted = [primary]
        modes = [self.metadata[name].mode for name in attempted if name in self.metadata]
        all_modes = [item.mode for item in self.metadata.values()]
        mode = "FALLBACK" if fallback_used else (modes[0] if modes else (selected.mode if selected else ("MOCK" if "MOCK" in all_modes else "NOT_CONFIGURED")))
        mock = bool(mock_used) if mock_used is not None else any(item == "MOCK" for item in (modes or all_modes))
        statuses = [self.metadata[name].status for name in attempted if name in self.metadata]
        provider_status = "FALLBACK" if fallback_used else (statuses[0] if statuses else (selected.status if selected else ("MOCK" if mock else "NOT_CONFIGURED")))
        return {
            "primary_provider": primary or "INTERNAL",
            "provider_status": provider_status or "NOT_CONFIGURED",
            "data_source_mode": mode or "NOT_CONFIGURED",
            "providers_attempted": attempted,
            "fallback_used": bool(fallback_used),
            "mock_used": mock,
            "source_health": self.health.snapshot(),
            "source_coverage_ratio": float(coverage_ratio if coverage_ratio is not None else 0.0),
            "provider_readiness": self.readiness(),
        }

    def _file_broker_records(self, record_type: str, market_date: str) -> list[CanonicalRecord]:
        candidates = [
            self.file_roots.get("broker_raw"),
            self.file_roots.get("broker_summary"),
            self.root / "data/input/broker/BROKER_RAW_LATEST.csv",
            self.root / "data/input/broker/BROKER_SUMMARY_LATEST.csv",
            self.root / "data/input/broker/BROKER_RAW_LATEST.json",
            self.root / "data/input/broker/BROKER_SUMMARY_LATEST.json",
        ]
        raw_path = next((Path(item) for item in candidates if item and Path(item).exists()), None)
        if raw_path is None:
            return []
        if raw_path.suffix.lower() == ".json":
            import json
            try:
                payload = json.loads(raw_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload = payload.get("rows", payload.get("data", [payload]))
                frame = normalize_broker_raw_frame(pd.DataFrame(payload))
            except Exception:
                frame = pd.DataFrame()
        else:
            frame = read_normalized_broker_raw(raw_path)
            if frame.empty:
                # Broker Summary exports are aggregate BUY/SELL totals. Keep
                # them as canonical synthetic summary rows, without pretending
                # they are individual brokers.
                try:
                    source = pd.read_csv(raw_path, low_memory=False)
                    symbol_col = next((c for c in source.columns if str(c).upper() in {"SYMBOL", "EMITEN", "TICKER"}), None)
                    date_col = next((c for c in source.columns if str(c).upper() in {"TO_DATE", "DATE", "BROKER_DATA_DATE"}), None)
                    buy_col = next((c for c in source.columns if str(c).upper() in {"TOTAL_BUY", "BUY_VALUE", "TOTAL_BUY_VALUE"}), None)
                    sell_col = next((c for c in source.columns if str(c).upper() in {"TOTAL_SELL", "SELL_VALUE", "TOTAL_SELL_VALUE"}), None)
                    rows: list[dict[str, Any]] = []
                    for _, row in source.iterrows():
                        symbol = str(row.get(symbol_col, "")).strip().upper() if symbol_col else ""
                        day = str(row.get(date_col, market_date))[:10] if date_col else market_date
                        for side, column in (("BUY", buy_col), ("SELL", sell_col)):
                            if symbol and column:
                                rows.append({"SYMBOL": symbol, "BROKER_CODE": "SUMMARY", "BROKER_TYPE": "UNKNOWN", "SIDE": side, "NET_VALUE": row.get(column), "TO_DATE": day})
                    frame = normalize_broker_raw_frame(pd.DataFrame(rows))
                except Exception:
                    frame = pd.DataFrame()
        return self._adapters["STOCKBIT"].to_canonical(record_type, frame, market_date=market_date)

    def _file_daily_record(self, symbol: str, market_date: str) -> DailyBar | None:
        folder = self.file_roots.get("historical") or self.root / "data/output/historical/by_symbol"
        candidates = [Path(folder) / f"{symbol}.csv", Path(folder) / f"{symbol}.JK.csv"]
        for path in candidates:
            if not path.exists():
                continue
            try:
                frame = pd.read_csv(path, low_memory=False)
            except Exception:
                continue
            date_col = next((c for c in frame.columns if str(c).lower() in {"date", "datetime", "market_date"}), None)
            if not date_col:
                continue
            rows = frame[pd.to_datetime(frame[date_col], errors="coerce").dt.date.astype(str).eq(market_date)]
            if rows.empty:
                continue
            row = rows.iloc[-1]
            def number(name: str) -> float | None:
                try:
                    value = row.get(name)
                    return None if pd.isna(value) else float(value)
                except (TypeError, ValueError):
                    return None
            received = now_wib().isoformat()
            return DailyBar(
                symbol=symbol,
                market_date=market_date,
                event_timestamp=f"{market_date}T16:15:00+07:00",
                received_at=received,
                source="HISTORICAL_PROVIDER",
                source_record_id=f"FILE:{path.name}:{market_date}",
                raw_payload_hash=compute_payload_hash(row.to_dict()),
                open=number("Open"), high=number("High"), low=number("Low"), close=number("Close"),
                volume=number("Volume"), value=number("Value"), is_closed=True,
            )
        return None

    def candidates_for(self, record_type: str, symbol: str, market_date: str, raw_by_source: Mapping[str, Any] | None = None) -> dict[str, CanonicalRecord]:
        candidates: dict[str, CanonicalRecord] = {}
        raw_by_source = raw_by_source or {}
        for source_name, raw in raw_by_source.items():
            adapter = self._adapters.get(source_name)
            if not adapter:
                continue
            try:
                records = adapter.to_canonical(record_type, raw, symbol=symbol, market_date=market_date)
            except Exception:
                self.health.record_failure(source_name, kind="validation")
                continue
            for record in records:
                if record.symbol == symbol or not record.symbol:
                    record.symbol = symbol
                    record.market_date = record.market_date or market_date
                    candidates[source_name] = record
                    self.health.record_success(source_name)
                    break
        if record_type in {"BrokerFlow", "ForeignFlow"} and "STOCKBIT" not in candidates:
            records = self._file_broker_records(record_type, market_date)
            for record in records:
                if record.symbol == symbol:
                    candidates["STOCKBIT"] = record
                    self.health.record_success("STOCKBIT")
                    break
        if record_type == "DailyBar" and "HISTORICAL_PROVIDER" not in candidates:
            record = self._file_daily_record(symbol, market_date)
            if record:
                candidates["HISTORICAL_PROVIDER"] = record
                self.health.record_success("HISTORICAL_PROVIDER")
        return candidates

    def route(
        self,
        record_type: str,
        symbol: str,
        market_date: str,
        *,
        candidates: Mapping[str, CanonicalRecord] | None = None,
        raw_by_source: Mapping[str, Any] | None = None,
        expected_market_date: str | None = None,
    ) -> RouterResult:
        candidate_map = dict(candidates or self.candidates_for(record_type, symbol, market_date, raw_by_source))
        attempted = list(candidate_map)
        for source_name in attempted:
            self.health.record_request(source_name)
        result = self.router.route(
            record_type,
            symbol,
            market_date,
            candidate_map,
            expected_market_date=expected_market_date or market_date,
        )
        if result.fallback_used:
            self.health.record_fallback(result.source_used)
        if result.record is None:
            for source_name in attempted:
                self.health.record_failure(source_name, kind="validation")
        else:
            self.metadata.setdefault(result.source_used, ProviderMetadata(result.source_used, "READY", "FILE", True, True)).records_loaded += 1
            self.metadata[result.source_used].records_valid += 1
        return result

    def route_many(self, record_type: str, symbols: list[str], market_date: str, *, raw_by_symbol: Mapping[str, Mapping[str, Any]] | None = None) -> tuple[list[CanonicalRecord], dict[str, Any]]:
        records: list[CanonicalRecord] = []
        attempted: set[str] = set()
        fallback = False
        for symbol in symbols:
            raw = (raw_by_symbol or {}).get(symbol, {})
            result = self.route(record_type, symbol, market_date, raw_by_source=raw)
            attempted.update(result.to_dict().get("resolution", {}).get("sources_considered", []) if result.resolution else [])
            if result.record is not None and result.quality and result.quality.accepted:
                records.append(result.record)
                fallback = fallback or result.fallback_used
        ratio = len(records) / len(symbols) if symbols else 0.0
        metadata = self.provider_metadata(record_type=record_type, providers_attempted=sorted(attempted), fallback_used=fallback, coverage_ratio=ratio)
        return records, metadata

    def health_snapshot(self) -> dict[str, Any]:
        return self.health.snapshot()

    # Explicit aliases make the manager convenient for jobs and integrations
    # without exposing the router implementation as a second source facade.
    def fetch(self, record_type: str, symbol: str, market_date: str, **kwargs: Any) -> RouterResult:
        return self.route(record_type, symbol, market_date, **kwargs)

    def get_canonical_records(self, record_type: str, symbols: list[str], market_date: str, **kwargs: Any) -> tuple[list[CanonicalRecord], dict[str, Any]]:
        return self.route_many(record_type, symbols, market_date, **kwargs)

    def status(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path),
            "config_version": self.config.config_version,
            "mode": self.mode,
            "readiness": self.readiness(),
            "health": self.health_snapshot(),
        }

    def write_health(self, output_dir: str | Path | None = None) -> Path:
        target = output_dir or self.root / "data/output/job_status"
        return self.health.write(target, run_id=self.run_id)
