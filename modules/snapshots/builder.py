from __future__ import annotations

"""Metadata-first snapshot builder for market, technical and broker stages."""

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from modules.runtime.context import RuntimeContext
from swing_utils import write_json


@dataclass
class SnapshotBuilder:
    context: RuntimeContext
    category: str

    def build(
        self,
        payload: Any,
        *,
        snapshot_id: str,
        source_metadata: Mapping[str, Any] | None = None,
        symbols_requested: int = 0,
        symbols_loaded: int = 0,
        symbols_valid: int = 0,
        symbols_failed: int = 0,
        symbols_skipped: int = 0,
    ) -> dict[str, Any]:
        metadata = self.context.artifact_metadata(snapshot_id=snapshot_id, source=dict(source_metadata or {}))
        result = {
            **metadata,
            "category": self.category,
            "symbols_requested": symbols_requested,
            "symbols_loaded": symbols_loaded,
            "symbols_valid": symbols_valid,
            "symbols_failed": symbols_failed,
            "symbols_skipped": symbols_skipped,
            "payload": payload,
        }
        canonical = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
        result["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return result

    def write(self, document: Mapping[str, Any], *, name: str | None = None) -> Path:
        snapshot_id = str(document.get("snapshot_id") or "snapshot")
        file_name = name or snapshot_id
        target = self.context.paths.output(f"snapshots/{self.category}", self.context.trade_date) / f"{file_name}.json"
        write_json(target, dict(document))
        return target


def build_snapshot(context: RuntimeContext, category: str, payload: Any, *, snapshot_id: str, **kwargs: Any) -> dict[str, Any]:
    return SnapshotBuilder(context, category).build(payload, snapshot_id=snapshot_id, **kwargs)
