from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.market_data.sector_rotation import produce_sector_rotation


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    technical = root / "technical.csv"
    metadata = root / "metadata.csv"
    ihsg = root / "IHSG.csv"

    technical_rows = []
    metadata_rows = []
    for sector, r5, r20 in (
        ("ENERGY", 5.0, 12.0),
        ("BANKING", 2.0, 6.0),
    ):
        for index in range(3):
            symbol = f"{sector[:2]}{index}"
            technical_rows.append({
                "Symbol": symbol,
                "Date": "2026-08-24",
                "Return_5D": r5 + index * 0.1,
                "Return_20D": r20 + index * 0.2,
            })
            metadata_rows.append({"Symbol": symbol, "Sector": sector})

    pd.DataFrame(technical_rows).to_csv(technical, index=False)
    pd.DataFrame(metadata_rows).to_csv(metadata, index=False)

    dates = pd.bdate_range(end="2026-08-24", periods=35)
    benchmark = pd.DataFrame({
        "Date": dates,
        "Close": [1000 + index * 5 for index in range(len(dates))],
    })
    # Simulate Yahoo exposing an in-progress 26 Aug daily candle while the
    # canonical technical snapshot still represents the last completed session
    # (24 Aug; 25 Aug is an IDX holiday in the 2026 SDE trading calendar).
    benchmark = pd.concat([
        benchmark,
        pd.DataFrame([{"Date": "2026-08-26", "Close": 1210.0}]),
    ], ignore_index=True)
    benchmark.to_csv(ihsg, index=False)
    return technical, metadata, ihsg


def test_sector_rotation_ignores_intraday_ihsg_row_newer_than_technical_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        technical, metadata, ihsg = _write_fixture(root)
        output = root / "SECTOR_ROTATION.json"

        payload = produce_sector_rotation(
            technical,
            output,
            date(2026, 8, 26),
            metadata_path=metadata,
            ihsg_path=ihsg,
        )

        assert payload["status"] == "VALID"
        assert payload["data_date"] == "2026-08-24"
        assert payload["ihsg_data_date"] == "2026-08-24"
        assert payload["ihsg_alignment_cutoff"] == "2026-08-24"
        assert payload["coverage"] == 1.0

        # The canonical IHSG source must remain untouched; alignment is a
        # calculation-only view, not destructive source-data rewriting.
        persisted = pd.read_csv(ihsg)
        assert "2026-08-26" in persisted["Date"].astype(str).tolist()
