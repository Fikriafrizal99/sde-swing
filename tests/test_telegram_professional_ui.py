from __future__ import annotations

import unittest

import pandas as pd

from modules.telegram.professional_ui import (
    UiConfig,
    broker_flow_divergence,
    format_data_warning,
    format_daily_signal_recap,
    format_post_market_summary,
    format_signal_detail,
    format_watchlist,
    public_counts,
    signal_bar,
    unique_final_decisions,
)


class TelegramProfessionalUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = UiConfig(max_watchlist_items=5)
        self.market = {"market_regime": "SIDEWAYS", "reason": "Trend conditions are mixed"}

    def test_recap_counts_unique_final_symbol_only(self) -> None:
        decisions = pd.DataFrame([
            {"Symbol": "BBCA", "Rank_V3": 1, "Decision_V3": "STRONG BUY"},
            {"Symbol": "BBCA", "Rank_V3": 2, "Decision_V3": "WATCH"},
            {"Symbol": "BMRI", "Rank_V3": 3, "Decision_V3": "BUY"},
            {"Symbol": "TLKM", "Rank_V3": 4, "Decision_V3": "AVOID"},
        ])
        unique = unique_final_decisions(decisions)
        self.assertEqual(len(unique), 3)
        self.assertEqual(public_counts(decisions), {
            "BUY CONFIRMED": 0,
            "BUY CANDIDATE": 2,
            "WATCH HIGH": 0,
            "WATCH": 0,
            "AVOID": 1,
        })

    def test_zero_signal_recap_does_not_divide_by_zero(self) -> None:
        text = format_daily_signal_recap(
            "2026-07-24",
            pd.DataFrame(),
            self.market,
            pd.DataFrame(),
            config=self.cfg,
        )
        self.assertIn("BUY / WATCH <b>0%</b>", text)
        self.assertNotIn("nan", text.lower())
        self.assertNotIn("none", text.lower())

    def test_signal_bar_is_never_longer_than_twenty(self) -> None:
        bar = signal_bar({"BUY CONFIRMED": 100, "WATCH HIGH": 30, "WATCH": 20, "AVOID": 50})
        self.assertEqual(len(bar), 20)

    def test_watchlist_does_not_invent_entry_target_or_stop(self) -> None:
        decisions = pd.DataFrame([{
            "Symbol": "BBCA",
            "Decision_V3": "STRONG BUY",
            "Final_Score_V3": 88.5,
            "Broker_Confirmation": "ACCUMULATION",
        }])
        text = format_watchlist("2026-07-24", decisions, pd.DataFrame(), config=self.cfg)
        self.assertIn("BUY CANDIDATE", text)
        self.assertIn("🎯 Menunggu konfirmasi", text)
        self.assertNotIn("TP ", text)
        self.assertNotIn("SL ", text)

    def test_accept_plan_is_ready_and_displays_levels(self) -> None:
        decisions = pd.DataFrame([{
            "Symbol": "TAPG",
            "Decision_V3": "BUY",
            "Final_Score_V3": 84.6,
            "Broker_Confirmation": "ACCUMULATION",
        }])
        plans = pd.DataFrame([{
            "Symbol": "TAPG",
            "Plan_Status": "ACCEPT",
            "Entry_Zone_Low": 1708,
            "Entry_Zone_High": 1742,
            "Initial_Stop": 1651,
            "Target_1": 1799,
            "Target_2": 1873,
        }])
        text = format_watchlist("2026-07-27", decisions, plans, config=self.cfg)
        self.assertIn("BUY CONFIRMED", text)
        self.assertIn("🎯 Entry Rp1.708–1.742", text)
        self.assertNotIn("TP 1.799 / 1.873", text)
        self.assertIn("1 Buy Confirmed", text)

    def test_watchlist_limits_items_and_excludes_avoid(self) -> None:
        cfg = UiConfig(max_buy_confirmed=0, max_buy_candidate=3, max_watch_high=2)
        rows = [{"Symbol": "BAD", "Decision_V3": "AVOID", "Final_Score_V3": 99}]
        rows += [{"Symbol": f"C{index}", "Decision_V3": "BUY CANDIDATE", "Final_Score_V3": 90-index} for index in range(5)]
        rows += [{"Symbol": f"W{index}", "Decision_V3": "WATCH HIGH", "Final_Score_V3": 80-index} for index in range(5)]
        text = format_watchlist("2026-07-24", pd.DataFrame(rows), pd.DataFrame(), config=cfg)
        self.assertNotIn("BAD", text)
        self.assertEqual(text.count("🎯"), 3)
        self.assertEqual(text.count("• <b>W"), 2)
        self.assertNotIn("C3", text)
        self.assertNotIn("W2", text)

    def test_stale_warning_has_human_explanation_and_impact(self) -> None:
        text = format_data_warning(
            run_id="TEST",
            expected_date="2026-07-24",
            latest_valid_date="2026-07-23",
            broker_date="2026-07-23",
            fallback_used=True,
            broker_override=False,
            data_status="STALE_ACCEPTED",
            warnings=["STALE_DATA_ACCEPTED_BY_USER"],
            config=self.cfg,
        )
        self.assertIn("Data yang digunakan bukan data terbaru", text)
        self.assertIn("Jangan gunakan hasil ini sebagai dasar entry", text)
        self.assertIn("STALE_ACCEPTED", text)

    def test_dynamic_html_is_escaped(self) -> None:
        decisions = pd.DataFrame([{
            "Symbol": "A&B",
            "Decision_V3": "BUY CANDIDATE",
            "Final_Score_V3": 70,
            "Broker_Confirmation": "ACCUMULATION",
            "Setup_Type": "X<Y",
        }])
        text = format_watchlist("2026-07-24", decisions, pd.DataFrame(), config=self.cfg)
        self.assertIn("A&amp;B", text)
        self.assertIn("X&lt;Y", text)

    def test_detail_distinguishes_rejected_plan_from_confirmed_status(self) -> None:
        row = pd.Series({
            "Symbol": "BBCA",
            "Decision_V3": "STRONG BUY",
            "Final_Score_V3": 90,
            "Technical_Regime": "bullish",
        })
        plan = pd.Series({
            "Plan_Status": "REJECT",
            "Rejection_Reason": "NEAREST_RESISTANCE_BELOW_MIN_RR",
            "Entry_Zone_Low": 100,
            "Entry_Zone_High": 102,
            "Initial_Stop": 95,
            "Target_1": 105,
        })
        text = format_signal_detail("2026-07-24", row, plan, config=self.cfg)
        self.assertIn("menunggu konfirmasi", text)
        self.assertIn("rencana entry gagal memenuhi guardrail", text)

    def test_broker_divergence_is_explicit_and_raw_net_flow_is_not_repeated(self) -> None:
        row = pd.Series({
            "Symbol": "TKIM",
            "Decision_V3": "WATCH",
            "Final_Score_V3": 70.7,
            "Broker_Confirmation": "STRONG ACCUMULATION",
            "NET_FLOW": -1520757500,
            "Decision_Reasons": "RSI sehat; net flow -1,520,757,500; buyer concentration dominan; Big Acc",
        })
        self.assertIn("net flow agregat negatif", broker_flow_divergence(row))
        text = format_signal_detail("2026-07-27", row, pd.Series(dtype=object), config=self.cfg)
        self.assertIn("Status     : STRONG ACCUMULATION", text)
        self.assertIn("Net Flow   : -Rp1,52 miliar", text)
        self.assertIn("Flow Status: DIVERGENCE", text)
        self.assertNotIn("1,520,757,500", text)

    def test_watchlist_ranking_matches_public_status_and_score(self) -> None:
        decisions = pd.DataFrame([
            {"Symbol": "LOWR", "Decision_V3": "BUY CANDIDATE", "Final_Score_V3": 62.0, "Broker_Confirmation": "NEUTRAL"},
            {"Symbol": "HIGH", "Decision_V3": "BUY CANDIDATE", "Final_Score_V3": 78.0, "Broker_Confirmation": "NEUTRAL"},
        ])
        text = format_watchlist("2026-07-27", decisions, pd.DataFrame(), config=self.cfg)
        self.assertLess(text.index("1. <b>HIGH</b>"), text.index("2. <b>LOWR</b>"))
        self.assertIn("2 Buy Candidate", text)

    def test_broker_raw_detail_displays_buyer_seller_value_and_average(self) -> None:
        row = pd.Series({
            "Symbol": "BBCA", "Decision_V3": "BUY CANDIDATE", "Final_Score_V3": 80,
            "Broker_Confirmation": "ACCUMULATION", "Broker_Confidence": 72, "NET_FLOW": 2_000_000_000,
            "BUYER_CONCENTRATION": 0.60, "SELLER_CONCENTRATION": 0.35, "Close": 9250,
        })
        raw = pd.DataFrame([
            {"SYMBOL": "BBCA", "TO_DATE": "2026-07-27", "SIDE": "BUY", "RANK": 1, "BROKER_CODE": "YP", "BROKER_TYPE": "LOCAL", "NET_VALUE": 3_000_000_000, "AVG_PRICE": 9200},
            {"SYMBOL": "BBCA", "TO_DATE": "2026-07-27", "SIDE": "SELL", "RANK": 1, "BROKER_CODE": "PD", "BROKER_TYPE": "LOCAL", "NET_VALUE": -2_000_000_000, "AVG_PRICE": 9300},
        ])
        text = format_signal_detail("2026-07-27", row, pd.Series(dtype=object), broker_raw=raw, config=self.cfg)
        self.assertIn("1. YP — Rp3,00 miliar | Avg Rp9.200", text)
        self.assertIn("1. PD — Rp2,00 miliar | Avg Rp9.300", text)
        self.assertIn("Avg Buyer     : Rp9.200", text)
        self.assertIn("Jarak buy avg : +0,54%", text)

    def test_post_market_is_single_compact_screening_report(self) -> None:
        technical = pd.DataFrame([{"Above_SMA20": 1}, {"Above_SMA20": 0}])
        candidates = pd.DataFrame([{
            "Symbol": "BBCA", "Candidate_Status": "PASS", "Technical_Quality_Score": 80,
            "Entry_Readiness_PreScore": 76, "Entry_Readiness_Class": "READY_ZONE", "Setup_Type": "PULLBACK",
        }])
        ihsg = pd.DataFrame([
            {"Date": "2026-07-24", "Open": 7000, "Close": 7050},
            {"Date": "2026-07-27", "Open": 7050, "Close": 7100},
        ])
        text = format_post_market_summary("2026-07-27", technical, candidates, {"date": "2026-07-27", "market_regime": "BULLISH"}, ihsg, config=self.cfg)
        self.assertIn("SDE SWING — POST MARKET", text)
        self.assertIn("Ready Zone     : 1", text)
        self.assertIn("BBCA", text)
        self.assertNotIn("CLOSING BELL", text)
        self.assertNotIn("REKAP SINYAL HARIAN", text)


if __name__ == "__main__":
    unittest.main()
