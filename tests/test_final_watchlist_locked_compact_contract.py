from modules.telegram.daily_report_ui import format_watchlist_detail


def test_bipi_locked_compact_contract_matches_operator_golden_format():
    row = {
        "symbol": "BIPI",
        "decision": "BUY ON TRIGGER",
        "confidence": 76,
        "setup": "PULLBACK",
        "analysis_date": "2026-08-21",
        "last_price": 152,
        "entry_low": 147,
        "entry_high": 152,
        "active_stop_loss": 142,
        "target_1": 163,
        "target_2": 164,
        "risk_reward": 1.08,
        "trend": "BULLISH",
        "phase": "WAIT TRIGGER",
        "support": 119,
        "resistance": 158,
        "broker_status": "INSUFFICIENT DATA",
        "broker_score": 0,
        "broker_net_flow": 57_810_000_000,
        "buy_days": 1,
        "sell_days": 0,
        "bandar_buy_cost": 156,
        "distance_to_buy_cost": -2.34,
        "top_buyers": [
            {"broker": "XL", "value": 43_300_000_000},
            {"broker": "ZP", "value": 8_650_000_000},
            {"broker": "PD", "value": 6_520_000_000},
        ],
        "top_sellers": [
            {"broker": "LG", "value": 7_860_000_000},
            {"broker": "GR", "value": 5_220_000_000},
            {"broker": "II", "value": 2_800_000_000},
        ],
    }

    expected = """📈 <b>BIPI | BUY ON TRIGGER | 76%</b>
PULLBACK • 21 Aug 2026

💰 152 | Entry 147–152
🛑 142 | 🎯 163 / 164 | RR 1:1,08

📊 Bullish | WAIT TRIGGER
S 119 | R 158

🏦 INSUFFICIENT DATA 0/100
Net +Rp57,81B | B/S 1/0
Cost 156 (-2,34%)

🟢 XL 43,3B • ZP 8,65B • PD 6,52B
🔴 LG 7,86B • GR 5,22B • II 2,8B

⚠️ Tunggu break >158. Jangan chase."""

    assert format_watchlist_detail(row) == expected
    assert len(expected) < 1024


def test_locked_compact_contract_does_not_expand_verbose_sections():
    text = format_watchlist_detail({
        "symbol": "TEST",
        "decision": "BUY CANDIDATE",
        "confidence": 70,
        "setup": "BREAKOUT",
        "analysis_date": "2026-08-26",
        "last_price": 100,
        "entry_low": 98,
        "entry_high": 100,
        "active_stop_loss": 94,
        "target_1": 110,
        "target_2": 115,
        "risk_reward": 2,
        "trend": "BULLISH",
        "phase": "WAIT TRIGGER",
        "support": 95,
        "resistance": 102,
        "broker_status": "ACCUMULATION",
        "broker_score": 70,
        "broker_net_flow": 1_000_000_000,
        "buy_days": 1,
        "sell_days": 0,
        "bandar_buy_cost": 99,
        "distance_to_buy_cost": 1.01,
    })

    assert "BROKER PRIMARY" not in text
    assert "TODAY PULSE" not in text
    assert "SETUP CONTEXT" not in text
    assert "Reason:" not in text
    assert "SDE SWING — FINAL WATCHLIST" not in text
    assert len(text) < 1024
