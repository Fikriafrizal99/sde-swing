from __future__ import annotations

"""Runtime artifact writer with one metadata envelope and content hash."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .context import RuntimeContext


def write_artifact(
    context: RuntimeContext,
    category: str,
    name: str,
    payload: Any,
    *,
    snapshot_id: str = "",
    source_metadata: dict[str, Any] | None = None,
    extension: str = ".json",
) -> Path:
    """Write a JSON artifact under ``data/output/<category>/<trade_date>``.

    Runtime outputs remain ignored by Git; this helper is intentionally used
    by integrations and tests with a temporary ``RuntimeContext.root``.
    """

    body = {
        **context.artifact_metadata(snapshot_id=snapshot_id, source=source_metadata),
        "payload": payload,
    }
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    body["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    target = context.paths.output(category, context.trade_date) / f"{name}{extension}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target

