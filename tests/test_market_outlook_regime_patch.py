from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from modules.market_data.market_outlook_regime import calculate_market_outlook_regime
from modules.telegram.professional_ui import format_market_outlook


class MarketOutlookRegimePatchTests(unittest.TestCase):
    def _write_ihsg(self, path: Path, closes: list[float], end: date = date(2026, 7, 27)) -> None:
        dates = pd.bdate_range(end=end, periods=len(closes))
        frame = pd.DataFrame({
            "Date": dates,
            "Open": closes,
            "High": [value * 1.01 for value in closes],
            "Low": [value * 0.99 for value in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        })
        frame.to_csv(path, index=False)

    def test_mixed_recovery_is_early_bullish_not_sideways(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "IHSG.csv"
            base = [7000 - idx * 8 for idx in range(80)]
            recovery = [6380 + idx * 25 for idx in range(20)]
            self._write_ihsg(path, base + recovery)
            status = calculate_market_outlook_regime(path, as_of_date=date(2026, 7, 27))
            self.assertIn(status["market_regime"], {"EARLY BULLISH", "BULLISH", "STRONG BULLISH"})
            self.assertNotEqual(status["market_regime"], "SIDEWAYS")
            self.assertIn("MA20", status["reason"])

    def test_full_downtrend_is_bearish(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "IHSG.csv"
            closes = [8000 - idx * 15 for idx in range(120)]
            self._write_ihsg(path, closes)
            status = calculate_market_outlook_regime(path, as_of_date=date(2026, 7, 27))
            self.assertIn(status["market_regime"], {"BEARISH", "STRONG BEARISH"})

    def test_market_outlook_uses_semantic_vix_and_usdidr_icons(self) -> None:
        snapshot = {
            "snapshot_id": "TEST",
            "global_sentiment": {"sentiment_state": "RISK_ON", "coverage_ratio": 1.0},
            "instruments": [
                {"instrument": "vix", "freshness_status": "VALID", "close": 18.0, "change_pct": -1.0},
                {"instrument": "usd_idr", "freshness_status": "VALID", "close": 17000, "change_pct": 0.5},
            ],
        }
        text = format_market_outlook(
            trade_date="2026-07-27",
            market_status={
                "market_regime": "EARLY BULLISH",
                "reason": "pemulihan terkonfirmasi sebagian",
                "confidence_pct": 70,
                "data_date": "2026-07-27",
                "is_stale": False,
            },
            global_snapshot=snapshot,
            broker_flow="netral",
            plan_summary="selektif",
            focus=["setup valid"],
            risks=["false breakout"],
        )
        self.assertIn("🟢 VIX", text)
        self.assertIn("🔴 USD/IDR", text)
        self.assertIn("Snapshot diperbarui", text)
        self.assertIn("Validasi IHSG", text)
        self.assertIn("1 mendukung | 1 menekan", text)


if __name__ == "__main__":
    unittest.main()
