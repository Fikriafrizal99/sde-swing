from __future__ import annotations

"""Compatibility entry point for the shared sector analytics engine.

The calculation itself lives in :mod:`modules.market_data.sector_analytics` so
Market Outlook and Post Market do not maintain competing sector formulas.
"""

from datetime import date
from pathlib import Path
from typing import Any

from modules.market_data.sector_analytics import build_multiday_sector_rotation


def produce_sector_rotation(
    technical_path: Path | str,
    output_path: Path | str,
    trade_date: date,
    *,
    metadata_path: Path | str | None = None,
    ihsg_path: Path | str | None = None,
) -> dict[str, Any]:
    """Build the canonical Market Outlook multi-day sector rotation artifact.

    ``metadata_path`` is a local symbol->sector master. No live metadata API is
    called here. ``ihsg_path`` is optional for backward-compatible callers and
    defaults to ``data/input/IHSG.csv`` inside the shared engine.
    """
    return build_multiday_sector_rotation(
        technical_path=technical_path,
        output_path=output_path,
        trade_date=trade_date,
        metadata_path=metadata_path,
        ihsg_path=ihsg_path,
    )


__all__ = ["produce_sector_rotation"]
