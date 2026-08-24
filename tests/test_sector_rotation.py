from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.market_data.sector_analytics import build_daily_sector_pulse
from modules.market_data.sector_rotation import produce_sector_rotation


def _write_ihsg(path: Path, end_date: str = "2026-08-24") -> None:
    dates = pd.bdate_range(end=end_date, periods=35)
    closes = [1000 + index * 5 for index in range(len(dates))]
    pd.DataFrame({"Date": dates, "Close": closes}).to_csv(path, index=False)


def _sector_fixture(root: Path) -> tuple[Path, Path]:
    technical = root / "technical.csv"
    metadata = root / "metadata.csv"
    rows = []
    sectors = {
        "ENERGY": (6.0, 14.0, 1.8),
        "BASIC": (4.0, 8.0, 1.1),
        "TECH": (1.0, -1.0, 0.3),
        "HEALTH": (-5.0, -8.0, -1.4),
    }
    mapping = []
    for sector, (r5, r20, r1) in sectors.items():
        for index in range(3):
            symbol = f"{sector[:2]}{index}"
            rows.append({
                "Symbol": symbol,
                "Date": "2026-08-24",
                "Return_1D": r1 + index * 0.05,
                "Return_5D": r5 + index * 0.1,
                "Return_20D": r20 + index * 0.2,
                "Turnover_Value": 1_000_000_000 + index * 100_000_000,
            })
            mapping.append({"Symbol": symbol, "Sector": sector})
    pd.DataFrame(rows).to_csv(technical, index=False)
    pd.DataFrame(mapping).to_csv(metadata, index=False)
    return technical, metadata


def test_missing_sector_metadata_is_explicitly_insufficient() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        technical = root / "technical.csv"
        ihsg = root / "ihsg.csv"
        output = root / "SECTOR_ROTATION.json"
        pd.DataFrame([{
            "Symbol": "BBCA",
            "Date": "2026-08-24",
            "Return_5D": 1.0,
            "Return_20D": 4.0,
        }]).to_csv(technical, index=False)
        _write_ihsg(ihsg)
        payload = produce_sector_rotation(
            technical,
            output,
            date(2026, 8, 24),
            metadata_path=root / "missing.csv",
            ihsg_path=ihsg,
        )
        assert payload["status"] == "INSUFFICIENT_DATA"
        assert payload["sectors"] == []
        assert json.loads(output.read_text(encoding="utf-8"))["coverage"] == 0.0


def test_multiday_rotation_is_relative_to_ihsg_and_uses_four_quadrants() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        technical, metadata = _sector_fixture(root)
        ihsg = root / "ihsg.csv"
        output = root / "SECTOR_ROTATION.json"
        _write_ihsg(ihsg)

        payload = produce_sector_rotation(
            technical,
            output,
            date(2026, 8, 24),
            metadata_path=metadata,
            ihsg_path=ihsg,
        )

        assert payload["status"] == "VALID"
        assert payload["source_mode"] == "MULTIDAY_RELATIVE_ROTATION"
        assert payload["data_date"] == "2026-08-24"
        assert payload["coverage"] == 1.0
        assert set(payload) >= {"leading", "improving", "weakening", "lagging", "sectors"}

        all_bucketed = (
            payload["leading"]
            + payload["improving"]
            + payload["weakening"]
            + payload["lagging"]
        )
        assert sorted(all_bucketed) == ["BASIC", "ENERGY", "HEALTH", "TECH"]
        assert len(all_bucketed) == len(set(all_bucketed))

        energy = next(row for row in payload["sectors"] if row["sector"] == "ENERGY")
        assert abs(
            energy["rs_5d"]
            - (energy["return_5d"] - payload["ihsg_return_5d"])
        ) < 1e-4
        assert abs(
            energy["rs_20d"]
            - (energy["return_20d"] - payload["ihsg_return_20d"])
        ) < 1e-4
        assert 0 < energy["strength_rank"] <= 1
        assert 0 < energy["momentum_rank"] <= 1
        assert energy["quadrant"] in {"LEADING", "IMPROVING", "WEAKENING", "LAGGING"}


def test_daily_sector_pulse_ranks_current_session_from_return_breadth_and_turnover() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        technical, metadata = _sector_fixture(root)
        output = root / "DAILY_SECTOR_PULSE.json"

        payload = build_daily_sector_pulse(
            technical,
            output,
            date(2026, 8, 24),
            metadata_path=metadata,
        )

        assert payload["status"] == "VALID"
        assert payload["source_mode"] == "DAILY_SECTOR_PULSE"
        assert payload["data_date"] == "2026-08-24"
        assert payload["coverage"] == 1.0
        assert payload["sectors"][0]["sector"] == "ENERGY"
        assert payload["sectors"][-1]["sector"] == "HEALTH"
        assert payload["sectors"][0]["positive_breadth"] == 1.0
        assert payload["sectors"][-1]["positive_breadth"] == 0.0
        assert all("daily_score" in row for row in payload["sectors"])


def test_daily_sector_pulse_fails_closed_for_stale_session() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        technical, metadata = _sector_fixture(root)
        frame = pd.read_csv(technical)
        frame["Date"] = "2026-08-21"
        frame.to_csv(technical, index=False)
        output = root / "DAILY_SECTOR_PULSE.json"

        payload = build_daily_sector_pulse(
            technical,
            output,
            date(2026, 8, 24),
            metadata_path=metadata,
        )

        assert payload["status"] == "INSUFFICIENT_DATA"
        assert payload["reason"] == "TECHNICAL_SESSION_NOT_CURRENT"
        assert payload["sectors"] == []
