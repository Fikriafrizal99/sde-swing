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


def test_active_and_waiting_are_one_card_with_agreed_monospace_tables() -> None:
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

    # ACTIVE is one fixed-width table: one header row, one row per issuer.
    for header in ("EMITEN", "ENTRY", "NOW", "P/L", "SL", "TP1", "TP2", "AGE"):
        assert header in text
    assert "LSIP" in text
    assert "1.465" in text
    assert "+0,03%" in text
    assert "1.405" in text
    assert "1.525" in text
    assert "1.530" in text
    assert "4D" in text

    # WAITING uses the agreed execution-focused columns.
    for header in ("GAP", "RR"):
        assert header in text
    assert "BAIK" in text
    assert "760–770" in text
    assert "IN RANGE" in text
    assert "1:2.10" in text
    assert "Status scan" not in text
    assert "Sinyal" not in text

    # Active + Waiting stay one Telegram message/card with two monospace tables.
    assert text.count("<pre>") == 2
    assert text.count("</pre>") == 2
    assert text.count("EMITEN") == 2
    assert len(text) < 4000


def test_current_scale_21_recommendations_stays_one_card() -> None:
    rows: list[dict] = []
    for index in range(6):
        rows.append({
            "symbol": f"A{index}",
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
        })
    for index in range(15):
        rows.append({
            "symbol": f"W{index}",
            "current_status": "WAITING_TRIGGER",
            "entry_zone_low": 1431,
            "entry_zone_high": 1459,
            "reference_price": 1420,
            "current_price": 1420,
            "stop_loss": 1380,
            "take_profit_1": 1520,
            "take_profit_2": 1600,
            "age_sessions": 4,
            "source_json": json.dumps({"plan": {"Risk_Reward": 2.1}}),
        })

    text = build_active_message(pd.DataFrame(rows))

    assert "Total aktif: 21 saham" in text
    assert text.count("<pre>") == 2
    assert text.count("</pre>") == 2
    assert text.count("EMITEN") == 2
    # tracker.send_telegram splits at 4000 raw characters; this must remain one card.
    assert len(text) < 4000


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
    assert "🎯 LSIP | TP1 HIT" in text
    assert "Exit      1.525" in text
    assert "⌛ BBRI | SIGNAL EXPIRED" in text
    assert "Reason    Trigger Not Reached Within Window" in text
    assert "Price     3.130" in text
    assert "Date      07 Aug 2026" in text
    assert text.count("<pre>") == 1
    assert text.count("</pre>") == 1


def test_performance_menu_keeps_active_and_lifecycle_as_separate_sends() -> None:
    menu = (ROOT / "maintenance/PERFORMANCE_MENU.bat").read_text(encoding="utf-8-sig")
    assert "%SDE_PYTHON_CMD% tools\\send_active_recommendations.py" in menu
    assert "%SDE_PYTHON_CMD% tools\\send_lifecycle_digest.py" in menu
    assert "Kirim lifecycle digest material ke Telegram?" in menu
    assert "Kirim active recommendations ke Telegram?" in menu
