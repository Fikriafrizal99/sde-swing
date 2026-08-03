from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.market_data.sector_rotation import produce_sector_rotation


class SectorRotationProducerTests(unittest.TestCase):
    def test_missing_sector_metadata_is_explicitly_insufficient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            technical = root / "technical.csv"
            output = root / "SECTOR_ROTATION.json"
            pd.DataFrame([{"Symbol": "BBCA", "Date": "2026-08-03", "Return_5D": 1.0, "Return_20D": 4.0}]).to_csv(technical, index=False)
            payload = produce_sector_rotation(technical, output, date(2026, 8, 3))
            self.assertEqual(payload["status"], "INSUFFICIENT_DATA")
            self.assertEqual(set(payload) & {"leading", "improving", "weakening", "lagging"}, {"leading", "improving", "weakening", "lagging"})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["coverage"], 0.0)

    def test_metadata_join_produces_canonical_buckets_without_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            technical = root / "technical.csv"
            metadata = root / "metadata.csv"
            output = root / "SECTOR_ROTATION.json"
            pd.DataFrame([
                {"Symbol": "BBCA", "Date": "2026-08-03", "Return_5D": 4.0, "Return_20D": 8.0},
                {"Symbol": "BBRI", "Date": "2026-08-03", "Return_5D": -2.0, "Return_20D": -5.0},
            ]).to_csv(technical, index=False)
            pd.DataFrame([{"Symbol": "BBCA", "Sector": "Financials"}, {"Symbol": "BBRI", "Sector": "Financials"}]).to_csv(metadata, index=False)
            payload = produce_sector_rotation(technical, output, date(2026, 8, 3), metadata_path=metadata)
            self.assertEqual(payload["status"], "VALID")
            self.assertEqual(payload["trade_date"], "2026-08-03")
            self.assertEqual(set(payload) & {"leading", "improving", "weakening", "lagging"}, {"leading", "improving", "weakening", "lagging"})
            self.assertNotIn("Decision", json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
