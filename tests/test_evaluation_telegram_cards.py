from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from modules.telegram.idx_price import fmt_idx_price, fmt_idx_zone, idx_price_fraction
from tools.send_active_recommendations import build_active_message
from tools.send_lifecycle_digest import build_lifecycle_message


ROOT = Path(__file__).resolve().parents[1]


def test_idx_price_fraction_and_snapping_match_regular_market_rules() -> None:
    assert idx_price_fraction(199) == 1
    assert idx_price_fraction(200) == 2
    assert idx_price_fraction(500) == 5
    assert idx_price_fraction(2000) == 10
    assert idx_price_fraction(5000) == 25

    # Machine levels are presentation-snapped to executable IDX ticks.
    assert fmt_idx_price(1464, anchor_price=1465) == "1.465"
    assert fmt_idx_price(455, anchor_price=462) == "456"
    assert fmt_idx_price(7566, anchor_price=7750) == "7.575"
    # Entry zones retain only valid ticks inside the original zone.
    assert fmt_idx_zone(1431, 1459, anchor_price=1420) == "1.435–1.455"


def test_active_and_waiting_are_one_card_with_agreed_monospace_fields() -> None:
    active = pd.DataFrame([
        {
            "symbol": "LSIP",
            "current_status": "OPEN",
            "entry_price": 1464,
            "reference_price": 1464,
            "current_price": 1465,
            "simulated_return_pct": 0.03,
            "take_profit_1": 1523,
            "take_profit_2": 1530,
            "stop_loss": 1406,
            "age_sessions": 4,
            "source_json": json.dumps({"plan": {}}),
        },
        {
            "symbol": "BAIK",
            "current_status": "WAITING_TRIGGER",
            "entry_zone_low": 757,
            "entry_zone_high": 773,
            "reference_price": 770,
            "current_price": 770,
            "stop_loss": 735,
            "take_profit_1": 810,
            "take_profit_2": 850,
            "age_sessions": 4,
            "source_json": json.dumps({"plan": {"Risk_Reward": 2.1}}),
        },
    ])

    text = build_active_message(active)

    assert "📌 <b>REKOMENDASI AKTIF</b>" in text
    assert "📈 <b>ACTIVE</b>" in text
    assert "⏳ <b>WAITING ENTRY</b>" in text
    assert "<code>LSIP | ACTIVE</code>" in text
    assert "<code>Entry   1.465</code>" in text
    assert "<code>P/L     +0,03%</code>" in text
    assert "<code>TP1     1.525</code>" in text
    assert "<code>SL      1.405</code>" in text
    assert "<code>Age     4D</code>" in text
    assert "<code>BAIK | WAITING</code>" in text
    assert "<code>Entry   760–770</code>" in text
    assert "<code>Gap     IN RANGE</code>" in text
    assert "<code>RR      1:2.10</code>" in text
    assert "Status scan" not in text
    assert "Sinyal" not in text
    assert text.count("<code>") == text.count("</code>")


def test_lifecycle_digest_is_a_separate_monospace_card() -> None:
    events = [
        {
            "event_id": "E1",
            "signal_id": "S1",
            "symbol": "LSIP",
            "event_type": "TP1_HIT",
            "previous_status": "OPEN",
            "new_status": "CLOSED",
            "event_date": "2026-08-07",
            "event_price": 1523,
            "event_reason": "TP1_HIT",
        },
        {
            "event_id": "E2",
            "signal_id": "S2",
            "symbol": "BBRI",
            "event_type": "EXPIRED",
            "previous_status": "WAITING_TRIGGER",
            "new_status": "EXPIRED",
            "event_date": "2026-08-07",
            "event_price": 3130,
            "event_reason": "TRIGGER_NOT_REACHED_WITHIN_WINDOW",
        },
    ]

    text = build_lifecycle_message(events)

    assert text.startswith("🔔 <b>LIFECYCLE DIGEST</b>")
    assert "REKOMENDASI AKTIF" not in text
    assert "<code>🎯 LSIP | TP1 HIT</code>" in text
    assert "<code>Exit      1.525</code>" in text
    assert "<code>⌛ BBRI | SIGNAL EXPIRED</code>" in text
    assert "<code>Reason    Trigger Not Reached Within Window</code>" in text
    assert "<code>Price     3.130</code>" in text
    assert "<code>Date      07 Aug 2026</code>" in text
    assert text.count("<code>") == text.count("</code>")


def test_performance_menu_keeps_active_and_lifecycle_as_separate_sends() -> None:
    menu = (ROOT / "maintenance/PERFORMANCE_MENU.bat").read_text(encoding="utf-8-sig")
    assert "%SDE_PYTHON_CMD% tools\\send_active_recommendations.py" in menu
    assert "%SDE_PYTHON_CMD% tools\\send_lifecycle_digest.py" in menu
    assert "Kirim lifecycle digest material ke Telegram?" in menu
    assert "Kirim active recommendations ke Telegram?" in menu
