from __future__ import annotations

"""Source configuration loader for the multi-source data layer.

This is intentionally separate from ``modules/runtime_config.py`` (which loads
``config/pipeline.json`` under strict trading-invariant validation).  Keeping
source ownership in ``config/data_sources.json`` avoids perturbing that strict
validator while still giving us typed, validated source metadata.

API keys are ONLY read from environment variables named by ``api_key_env`` /
``base_url_env`` — never from the JSON itself.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from swing_utils import read_json
from modules.data_sources.constants import (
    ALL_RESOLVER_MODES,
    FAIL_CLOSED,
    FALLBACK_ALLOW,
    FALLBACK_DENY,
    MODE_PRIMARY_WITH_FALLBACK,
)

DEFAULT_CONFIG_PATH = Path("config/data_sources.json")


class DataSourceConfigError(ValueError):
    pass


@dataclass
class SourceConfig:
    name: str
    enabled: bool = False
    priority: int = 100
    timeout: float = 30.0
    retry: int = 2
    maximum_stale_seconds: float = 172800.0
    fallback_policy: str = FALLBACK_DENY
    conflict_tolerance: float = 0.0
    required_fields: tuple[str, ...] = ()
    fail_open_or_closed: str = FAIL_CLOSED
    api_key_env: str = ""
    base_url_env: str = ""
    documentation_configured: bool = False
    note: str = ""

    def api_key(self) -> str | None:
        """Read the API key from the environment (never from disk)."""
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None

    def base_url(self) -> str | None:
        if not self.base_url_env:
            return None
        return os.environ.get(self.base_url_env) or None

    def has_credentials(self) -> bool:
        if self.api_key_env and not self.api_key():
            return False
        if self.base_url_env and not self.base_url():
            return False
        return bool(self.api_key_env or self.base_url_env)


@dataclass
class RecordOwnership:
    record_type: str
    primary: str
    fallback: tuple[str, ...] = ()
    fail_open_or_closed: str | None = None
    note: str = ""


@dataclass
class DataSourceConfig:
    schema_version: str = "1.0.0"
    resolver_mode: str = MODE_PRIMARY_WITH_FALLBACK
    primary_broker_window: str = "5D"
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    ownership: dict[str, RecordOwnership] = field(default_factory=dict)
    conflict_defaults: dict[str, Any] = field(default_factory=dict)

    def source(self, name: str) -> SourceConfig | None:
        return self.sources.get(name)

    def ownership_for(self, record_type: str) -> RecordOwnership | None:
        return self.ownership.get(record_type)

    def resolution_chain(self, record_type: str) -> list[str]:
        """Ordered [primary, *fallback] source names for a record type."""
        owner = self.ownership.get(record_type)
        if owner is None:
            return []
        return [owner.primary, *owner.fallback]


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_config(payload: dict[str, Any]) -> DataSourceConfig:
    if not isinstance(payload, dict) or not payload:
        raise DataSourceConfigError("data_sources config kosong atau bukan objek JSON")

    resolver_mode = str(payload.get("resolver_mode", MODE_PRIMARY_WITH_FALLBACK)).strip().upper()
    if resolver_mode not in ALL_RESOLVER_MODES:
        raise DataSourceConfigError(f"RESOLVER_MODE_INVALID: {resolver_mode}")

    sources: dict[str, SourceConfig] = {}
    for name, raw in (payload.get("sources") or {}).items():
        if not isinstance(raw, dict):
            raise DataSourceConfigError(f"SOURCE_CONFIG_INVALID: {name}")
        policy = str(raw.get("fallback_policy", FALLBACK_DENY)).strip().upper()
        if policy not in {FALLBACK_ALLOW, FALLBACK_DENY}:
            raise DataSourceConfigError(f"FALLBACK_POLICY_INVALID[{name}]: {policy}")
        sources[name] = SourceConfig(
            name=name,
            enabled=_as_bool(raw.get("enabled"), False),
            priority=int(raw.get("priority", 100) or 100),
            timeout=float(raw.get("timeout", 30.0) or 30.0),
            retry=int(raw.get("retry", 2) or 0),
            maximum_stale_seconds=float(raw.get("maximum_stale_seconds", 172800.0) or 0.0),
            fallback_policy=policy,
            conflict_tolerance=float(raw.get("conflict_tolerance", 0.0) or 0.0),
            required_fields=tuple(raw.get("required_fields", []) or ()),
            fail_open_or_closed=str(raw.get("fail_open_or_closed", FAIL_CLOSED)).strip().upper(),
            api_key_env=str(raw.get("api_key_env", "") or ""),
            base_url_env=str(raw.get("base_url_env", "") or ""),
            documentation_configured=_as_bool(raw.get("documentation_configured"), False),
            note=str(raw.get("note", "") or ""),
        )

    ownership: dict[str, RecordOwnership] = {}
    for record_type, raw in (payload.get("record_ownership") or {}).items():
        if not isinstance(raw, dict):
            raise DataSourceConfigError(f"OWNERSHIP_CONFIG_INVALID: {record_type}")
        primary = str(raw.get("primary", "") or "").strip()
        if not primary:
            raise DataSourceConfigError(f"OWNERSHIP_PRIMARY_MISSING: {record_type}")
        fallback = tuple(str(x).strip() for x in (raw.get("fallback") or ()) if str(x).strip())
        ownership[record_type] = RecordOwnership(
            record_type=record_type,
            primary=primary,
            fallback=fallback,
            fail_open_or_closed=(
                str(raw["fail_open_or_closed"]).strip().upper()
                if raw.get("fail_open_or_closed")
                else None
            ),
            note=str(raw.get("note", "") or ""),
        )

    # Referential integrity: every named source in ownership must exist
    # (INTERNAL is a reserved virtual source for internally-computed data).
    known = set(sources) | {"INTERNAL"}
    for record_type, owner in ownership.items():
        for referenced in [owner.primary, *owner.fallback]:
            if referenced not in known:
                raise DataSourceConfigError(
                    f"OWNERSHIP_UNKNOWN_SOURCE[{record_type}]: {referenced}"
                )

    return DataSourceConfig(
        schema_version=str(payload.get("schema_version", "1.0.0")),
        resolver_mode=resolver_mode,
        primary_broker_window=str(payload.get("primary_broker_window", "5D")).strip().upper(),
        sources=sources,
        ownership=ownership,
        conflict_defaults=dict(payload.get("conflict_defaults") or {}),
    )


def load_data_source_config(path: Path | str = DEFAULT_CONFIG_PATH) -> DataSourceConfig:
    resolved = Path(path).resolve()
    payload = read_json(resolved)
    if not payload:
        raise DataSourceConfigError(f"Konfigurasi data source tidak ditemukan/kosong: {resolved}")
    return parse_config(payload)