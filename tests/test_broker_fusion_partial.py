from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from modules.broker_fusion.broker_fusion import fuse


class BrokerFusionPartialCoverageTests(unittest.TestCase):
    @staticmethod
    def _inputs(root: Path, matched: int) -> tuple[Path, Path, Path]:
        technical_path = root / "technical.csv"
        broker_path = root / "broker.csv"
        output_path = root / "fusion.csv"
        symbols = ["AAA", "BBB", "CCC", "DDD", "EEE"]
        pd.DataFrame({"Symbol": symbols, "Technical_Score": [70] * len(symbols)}).to_csv(technical_path, index=False)
        pd.DataFrame([
            {
                "EMITEN": symbol,
                "TO_DATE": "2026-08-03",
                "TOTAL_BUY": 100,
                "TOTAL_SELL": 80,
                "NET_FLOW": 20,
                "BUYER_CONCENTRATION": 0.4,
                "SELLER_CONCENTRATION": 0.3,
                "BROKER_ACCDIST": "Acc",
                "AVG_ACCDIST": "Acc",
                "TOP3_ACCDIST": "Acc",
            }
            for symbol in symbols[:matched]
        ]).to_csv(broker_path, index=False)
        return technical_path, broker_path, output_path

    def test_80_percent_is_partial_but_reportable_and_missing_defaults_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            technical, broker, output = self._inputs(Path(tmp), 4)
            fused = fuse(technical, broker, output, min_coverage=0.8, allow_partial_broker=True, expected_broker_date="2026-08-03")
            missing = fused.loc[fused["Symbol"].eq("EEE")].iloc[0]
            self.assertEqual(set(fused["Data_Quality_Status"]), {"PARTIAL_COVERAGE"})
            self.assertFalse(bool(missing["Broker_Data_Available"]))
            self.assertEqual(str(missing["Broker_Confirmation"]), "NO DATA")
            self.assertEqual(float(missing["Broker_Score"]), 0.0)
            self.assertEqual(float(missing["NET_FLOW"]), 0.0)

    def test_below_floor_fails_even_when_partial_flag_is_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            technical, broker, output = self._inputs(Path(tmp), 3)
            with self.assertRaises(RuntimeError):
                fuse(technical, broker, output, min_coverage=0.8, allow_partial_broker=True, expected_broker_date="2026-08-03")


if __name__ == "__main__":
    unittest.main()
