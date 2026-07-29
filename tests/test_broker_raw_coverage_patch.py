from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from modules.broker_bridge.broker_raw import normalize_broker_raw_frame, validate_broker_raw
from modules.telegram.professional_ui import (
    UiConfig,
    broker_party_rows,
    broker_raw_coverage,
    format_signal_detail,
    format_watchlist,
)


class BrokerRawCoveragePatchTests(unittest.TestCase):
    def test_alias_side_and_average_are_normalized_and_derived(self) -> None:
        raw = pd.DataFrame([
            {
                "EMITEN": "INDF.JK", "DATE": "2026-07-27", "BUY_SELL": "Buyer",
                "POSITION": 1, "BROKER": "ZP", "BUY_VALUE": 6_869_000_000,
                "BUY_LOT": 10_000, "AVERAGE_PRICE": 0,
            },
            {
                "EMITEN": "INDF", "DATE": "2026-07-27", "BUY_SELL": "SELLER",
                "POSITION": 1, "BROKER": "RB", "SELL_VALUE": 3_437_500_000,
                "SELL_LOT": 5_000, "AVERAGE_PRICE": "",
            },
        ])
        clean = normalize_broker_raw_frame(raw)
        self.assertEqual(clean["SYMBOL"].tolist(), ["INDF", "INDF"])
        self.assertEqual(set(clean["SIDE"]), {"BUY", "SELL"})
        prices = dict(zip(clean["SIDE"], clean["AVG_PRICE"]))
        self.assertAlmostEqual(prices["BUY"], 6869.0)
        self.assertAlmostEqual(prices["SELL"], 6875.0)

    def test_duplicate_capture_prefers_row_with_average(self) -> None:
        raw = pd.DataFrame([
            {"SYMBOL": "AADI", "TO_DATE": "2026-07-27", "SIDE": "BUY", "RANK": 1,
             "BROKER_CODE": "DX", "NET_VALUE": 4_000_000_000, "AVG_PRICE": 0},
            {"SYMBOL": "AADI", "TO_DATE": "2026-07-27", "SIDE": "BUY", "RANK": 1,
             "BROKER_CODE": "DX", "NET_VALUE": 4_000_000_000, "AVG_PRICE": 9075},
        ])
        clean = normalize_broker_raw_frame(raw)
        self.assertEqual(len(clean), 1)
        self.assertEqual(float(clean.iloc[0]["AVG_PRICE"]), 9075.0)

    def test_partial_raw_preserves_summary_top_three_and_reports_coverage(self) -> None:
        row = pd.Series({
            "Symbol": "AADI",
            "TOP_BUYER_1": "DX", "TOP_BUYER_2": "YP", "TOP_BUYER_3": "AK",
            "TOP_SELLER_1": "CC", "TOP_SELLER_2": "ZP", "TOP_SELLER_3": "PD",
        })
        raw = pd.DataFrame([
            {"SYMBOL": "AADI", "TO_DATE": "2026-07-27", "SIDE": "BUY", "RANK": 1,
             "BROKER_CODE": "DX", "NET_VALUE": 4_120_000_000, "AVG_PRICE": 9075},
            {"SYMBOL": "AADI", "TO_DATE": "2026-07-27", "SIDE": "SELL", "RANK": 1,
             "BROKER_CODE": "CC", "NET_VALUE": -2_840_000_000, "AVG_PRICE": 9180},
        ])
        buyers = broker_party_rows(row, raw, "BUY", 3)
        self.assertEqual([item["broker"] for item in buyers], ["DX", "YP", "AK"])
        coverage = broker_raw_coverage(row, raw, 3)
        self.assertEqual(coverage, {"total": 6, "matched": 2, "avg": 2, "value": 2})

    def test_watchlist_displays_top_three_and_does_not_mutate_decision(self) -> None:
        decisions = pd.DataFrame([{
            "Symbol": "AADI", "Decision_V3": "BUY CANDIDATE", "Final_Score_V3": 79.3,
            "Setup_Type": "TREND CONTINUATION", "Broker_Confirmation": "STRONG ACCUMULATION",
            "Broker_Confidence_Final": 93, "NET_FLOW": 9_890_000_000,
            "TOP_BUYER_1": "DX", "TOP_BUYER_2": "YP", "TOP_BUYER_3": "AK",
            "TOP_SELLER_1": "CC", "TOP_SELLER_2": "ZP", "TOP_SELLER_3": "PD",
        }])
        original = decisions.copy(deep=True)
        plans = pd.DataFrame([{
            "Symbol": "AADI", "Plan_Status": "WAIT", "Entry_Status": "WAITING",
            "Entry_Trigger_Text": "tunggu close > 9.200", "Plan_Reason": "resistance minor masih dekat",
        }])
        raw_rows = []
        for side, codes, start in (("BUY", ["DX", "YP", "AK"], 9075), ("SELL", ["CC", "ZP", "PD"], 9180)):
            for rank, code in enumerate(codes, 1):
                raw_rows.append({
                    "SYMBOL": "AADI", "TO_DATE": "2026-07-27", "SIDE": side, "RANK": rank,
                    "BROKER_CODE": code, "NET_VALUE": (4_000_000_000 / rank) * (1 if side == "BUY" else -1),
                    "AVG_PRICE": start + rank,
                })
        raw = pd.DataFrame(raw_rows)
        text = format_watchlist(
            "2026-07-27", decisions, plans, raw,
            config=UiConfig(max_buy_candidate=3),
        )
        self.assertIn("🌊 Strong Acc | 93% | +Rp9,89 M", text)
        self.assertNotIn("DX Rp4,00 miliar", text)

        detail = format_signal_detail(
            "2026-07-27", decisions.iloc[0], plans.iloc[0], raw,
            config=UiConfig(max_broker_detail_rows=3),
        )
        self.assertIn("1. DX — Rp4,00 miliar | Avg Rp9.076", detail)
        self.assertIn("2. YP — Rp2,00 miliar | Avg Rp9.077", detail)
        self.assertIn("3. AK — Rp1,33 miliar | Avg Rp9.078", detail)
        self.assertIn("1. CC — Rp4,00 miliar | Avg Rp9.181", detail)
        self.assertIn("6/6 broker | nilai 6/6 | avg 6/6", detail)
        pd.testing.assert_frame_equal(decisions, original)

    def test_validation_accepts_alias_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BROKER_RAW_COMBINED_2026-07-27.csv"
            pd.DataFrame([{
                "EMITEN": "ITMG", "DATE": "2026-07-27", "DIRECTION": "BELI",
                "BROKER": "YU", "AMOUNT": 2_000_000_000, "LOT": 800,
            }]).to_csv(path, index=False)
            valid, frame, warning = validate_broker_raw(path, "2026-07-27")
            self.assertTrue(valid, warning)
            self.assertEqual(frame.iloc[0]["SIDE"], "BUY")
            self.assertAlmostEqual(float(frame.iloc[0]["AVG_PRICE"]), 25_000.0)


if __name__ == "__main__":
    unittest.main()
