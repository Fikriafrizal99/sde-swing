from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.broker_fusion.broker_fusion import broker_score_frame
from modules.candidate_selector.technical_candidate_selector import score_candidates
from modules.exit_engine.exit_engine import build_entry_plan, should_build_plan
from modules.telegram.professional_ui import effective_public_status


class SignalQualityV160Tests(unittest.TestCase):
    def test_extended_stock_keeps_quality_but_loses_entry_readiness(self) -> None:
        frame = pd.DataFrame([{
            "Symbol": "TEST",
            "Open": 118,
            "High": 122,
            "Low": 117,
            "Close": 120,
            "SMA_5": 116,
            "SMA_20": 104,
            "SMA_50": 95,
            "SMA_200": 80,
            "EMA_20": 100,
            "RSI_14": 72,
            "MACD": 4,
            "MACD_Signal": 2,
            "MACD_Hist": 2,
            "Volume": 2_000_000,
            "Volume_MA_20": 1_000_000,
            "ATR_14_Pct": 3,
            "Return_5D": 5,
            "Return_20D": 12,
            "Slope_Close_20": 1,
            "Distance_High_252_Pct": -2,
            "Drawdown_252_Pct": -3,
            "Volatility_20_Annualized_Pct": 40,
            "Turnover_MA_20": 25_000_000_000,
            "Technical_Regime": "bullish",
            "Breakout_20D": 1,
            "Gap_Pct": 1,
        }])
        result = score_candidates(frame, 1_000_000_000).iloc[0]
        self.assertGreaterEqual(float(result["Technical_Quality_Score"]), 70)
        self.assertLess(float(result["Entry_Readiness_PreScore"]), 60)
        self.assertEqual(result["Entry_Soft_Warning"], "PRICE_EXTENDED")

    def test_broker_divergence_reduces_confidence(self) -> None:
        frame = pd.DataFrame([
            {
                "EMITEN": "ALIGN",
                "TOTAL_BUY": 1100,
                "TOTAL_SELL": 900,
                "NET_FLOW": 200,
                "BUYER_CONCENTRATION": 0.50,
                "SELLER_CONCENTRATION": 0.30,
                "BROKER_ACCDIST": "Acc",
                "AVG_ACCDIST": "Big Acc",
                "TOP3_ACCDIST": "Acc",
            },
            {
                "EMITEN": "DIVERGE",
                "TOTAL_BUY": 900,
                "TOTAL_SELL": 1100,
                "NET_FLOW": -200,
                "BUYER_CONCENTRATION": 0.50,
                "SELLER_CONCENTRATION": 0.30,
                "BROKER_ACCDIST": "Acc",
                "AVG_ACCDIST": "Big Acc",
                "TOP3_ACCDIST": "Acc",
            },
        ])
        scored = broker_score_frame(frame).set_index("EMITEN")
        self.assertFalse(bool(scored.loc["ALIGN", "Broker_Divergence"]))
        self.assertTrue(bool(scored.loc["DIVERGE", "Broker_Divergence"]))
        self.assertGreater(
            float(scored.loc["ALIGN", "Broker_Confidence"]),
            float(scored.loc["DIVERGE", "Broker_Confidence"]),
        )

    def test_new_decision_path_exposes_buy_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.csv"
            pd.DataFrame([{
                "Symbol": "BBCA",
                "Turnover_MA_20": 15_000_000_000,
                "Volume_MA_20": 10_000_000,
                "Turnover_Value": 5_000_000_000,
                "Volume": 5_000_000,
                "Technical_Score_Final": 78,
                "Technical_Quality_Score": 78,
                "Entry_Readiness_PreScore": 68,
                "Broker_Score": 65,
                "Broker_Confidence": 60,
                "Broker_Direction": "ACCUMULATION",
                "Broker_Confirmation": "ACCUMULATION",
                "Broker_Divergence": False,
                "Synergy_Bonus": 2,
                "Risk_Penalty": 0,
            }]).to_csv(source, index=False)
            output = root / "decision"
            result = subprocess.run([
                sys.executable,
                str(ROOT / "modules/decision_engine/decision_engine.py"),
                str(source),
                str(output),
                "--run-id",
                "V160-TEST",
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            row = pd.read_csv(output / "FINAL_DECISION_V3.csv").iloc[0]
            self.assertEqual(row["Decision_V3"], "BUY CANDIDATE")
            self.assertEqual(row["Decision_Reason_Code"], "AWAITING_ENTRY_PLAN_CONFIRMATION")

    def test_watch_plan_requires_quality_and_non_distribution(self) -> None:
        good = pd.Series({
            "Decision": "WATCH",
            "Technical_Quality_Score": 80,
            "Entry_Readiness_PreScore": 65,
            "Liquidity_Class": "LIQUID",
            "Broker_Direction": "ACCUMULATION",
            "Broker_Confirmation": "ACCUMULATION",
        })
        bad = good.copy()
        bad["Broker_Direction"] = "DISTRIBUTION"
        bad["Broker_Confirmation"] = "DISTRIBUTION"
        self.assertTrue(should_build_plan(good))
        self.assertFalse(should_build_plan(bad))


    def test_pullback_plan_uses_planned_entry_for_risk_and_targets(self) -> None:
        dates = pd.date_range("2026-05-01", periods=40, freq="B")
        closes = [100.0] * 39 + [104.0]
        px = pd.DataFrame({
            "Date": dates,
            "Open": [99.0] * 39 + [103.0],
            "High": [102.0] * 39 + [105.0],
            "Low": [98.0] * 39 + [102.0],
            "Close": closes,
            "ATR14": [2.0] * 40,
            "EMA20": [100.0] * 40,
        })
        row = pd.Series({
            "Symbol": "TEST",
            "Decision": "BUY",
            "Setup_Type": "PULLBACK",
            "Entry_Readiness_PreScore": 75,
            "Technical_Quality_Score": 80,
            "Liquidity_Class": "LIQUID",
            "Market_Regime": "SIDEWAYS",
            "Broker_Direction": "ACCUMULATION",
            "Broker_Confirmation": "ACCUMULATION",
        })
        plan = build_entry_plan(row, px, 1.0, 2.0, 7.0, 20)
        self.assertAlmostEqual(
            float(plan["Risk_Per_Share"]),
            float(plan["Entry_Reference_Price"]) - float(plan["Initial_Stop"]),
            places=6,
        )
        self.assertAlmostEqual(
            float(plan["Target_1"]),
            float(plan["Entry_Reference_Price"]) + float(plan["Risk_Per_Share"]),
            places=6,
        )
        self.assertLess(float(plan["Entry_Reference_Price"]), float(plan["Reference_Close"]))
        self.assertEqual(plan["Price_Position_To_Entry_Zone"], "ABOVE_ZONE")
        self.assertEqual(plan["Plan_Status"], "CONDITIONAL")
        self.assertEqual(plan["Rejection_Reason"], "WAIT_FOR_ENTRY_ZONE")

    def test_ready_buy_is_presented_as_confirmed(self) -> None:
        accepted = pd.Series({
            "Plan_Status": "ACCEPT",
            "Entry_Zone_Low": 100,
            "Entry_Zone_High": 102,
            "Initial_Stop": 95,
            "Target_1": 108,
        })
        self.assertEqual(effective_public_status("BUY", accepted), "BUY CONFIRMED")
        self.assertEqual(effective_public_status("BUY CANDIDATE", pd.Series(dtype=object)), "BUY CANDIDATE")


if __name__ == "__main__":
    unittest.main()
