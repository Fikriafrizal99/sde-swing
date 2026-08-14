from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

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


def test_active_and_waiting_are_one_card_with_compact_mobile_tables() -> None:
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
            "tp1_hit": 1,
            "market_session_age": 5,
            "age_sessions": 1,
            "scan_staleness_sessions": 2,
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
            "market_session_age": 2,
            "age_sessions": 0,
            "source_json": json.dumps({"plan": {"Risk_Reward": 2.1}}),
        },
        {
            "symbol": "CLOSED1",
            "current_status": "CLOSED",
        },
        {
            "symbol": "EXPIRED1",
            "current_status": "EXPIRED",
        },
        {
            "symbol": "INVALID1",
            "current_status": "INVALIDATED_BEFORE_ENTRY",
        },
    ])

    text = build_active_message(active)

    assert "📊 <b>SDE SWING — ACTIVE RECOMMENDATIONS</b>" in text
    assert "Total actionable: 2 saham" in text
    assert "📈 <b>ACTIVE — 1</b>" in text
    assert "⏳ <b>WAITING ENTRY — 1</b>" in text

    # ACTIVE: short headers, no thousands separators, still IDX-snapped.
    for token in ("EMT", "ENTRY", "NOW", "P/L", "SL", "TP1", "TP2", "AGE"):
        assert token in text
    for token in ("LSIP", "1465", "+0,03%", "1405", "1525", "1530", "5"):
        assert token in text
    assert "1.465" not in text
    assert "4D" not in text
    assert "TP1 HIT · 🟢 TRAILING ACTIVE" in text
    assert "Last Scan: LSIP 2 sesi lalu" in text

    # WAITING: compact entry zone and RANGE label.
    for token in ("BAIK", "760-770", "770", "RANGE", "REC", "AGE", "1x"):
        assert token in text
    assert "IN RANGE" not in text
    assert "760–770" not in text
    assert "Status scan" not in text
    assert "Sinyal" not in text
    for terminal_symbol in ("CLOSED1", "EXPIRED1", "INVALID1"):
        assert terminal_symbol not in text

    # Active + Waiting stay one Telegram message/card with two monospace tables.
    assert text.count("<pre>") == 2
    assert text.count("</pre>") == 2
    assert text.count("EMT") == 2
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

    assert "Total actionable: 21 saham" in text
    assert text.count("<pre>") == 2
    assert text.count("</pre>") == 2
    assert text.count("EMT") == 2
    # Compact representation should stay comfortably below the sender split limit.
    assert len(text) < 3000


def test_active_card_rejects_duplicate_actionable_symbol_instead_of_hiding_owner() -> None:
    active = pd.DataFrame([
        {"symbol": "BBRI", "current_status": "OPEN"},
        {"symbol": "BBRI.JK", "current_status": "WAITING_TRIGGER"},
    ])

    with pytest.raises(RuntimeError, match="DUPLICATE_ACTIONABLE_LIFECYCLE:BBRI"):
        build_active_message(active)


def test_lifecycle_digest_is_a_separate_monospace_card() -> None:
    events = [
        {
            "event_id": "E1",
            "signal_id": "S1",
            "symbol": "LSIP",
            "event_type": "TP1_HIT",
            "previous_status": "OPEN",
            "new_status": "OPEN",
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
        {
            "event_id": "E3",
            "signal_id": "S1",
            "symbol": "LSIP",
            "event_type": "SIGNAL_RECONFIRMED",
            "previous_status": "OPEN",
            "new_status": "OPEN",
            "event_date": "2026-08-07",
            "event_price": 1523,
            "event_reason": "SCAN:BUY",
        },
        {
            "event_id": "E4",
            "signal_id": "S1",
            "symbol": "LSIP",
            "event_type": "CLOSED",
            "previous_status": "OPEN",
            "new_status": "CLOSED",
            "event_date": "2026-08-07",
            "event_price": 1523,
            "event_reason": "TP2_HIT",
        },
    ]

    text = build_lifecycle_message(events)

    assert text.startswith("🔔 <b>SDE SWING — LIFECYCLE DIGEST</b>")
    assert "REKOMENDASI AKTIF" not in text
    assert "🎯 TP1 HIT — 1" in text
    assert "◆ LSIP @ 1.525 · 07 Aug 2026" in text
    assert "→ Trailing active" in text
    assert "⌛ SIGNAL EXPIRED — 1" in text
    assert "◆ BBRI · 07 Aug 2026" in text
    assert "Waiting 7 sesi perdagangan tanpa entry trigger" in text
    assert "REC selama lifecycle: 0x" in text
    assert "SIGNAL_RECONFIRMED" not in text
    assert "SCAN:BUY" not in text
    assert "CLOSED" not in text
    assert text.count("<pre>") == 1
    assert text.count("</pre>") == 1


def test_lifecycle_digest_groups_only_material_delta_types() -> None:
    event_types = [
        "ENTRY_TRIGGERED",
        "ENTRY_TRIGGERED",
        "TP1_HIT",
        "STOP_LOSS_HIT",
        "TP2_HIT",
        "MAX_HOLD_EXIT",
        "EXPIRED",
        "INVALIDATED_BEFORE_ENTRY",
    ]
    events = [
        {
            "event_id": f"E{index}",
            "signal_id": f"S{index}",
            "symbol": f"T{index}",
            "event_type": event_type,
            "previous_status": "WAITING_TRIGGER" if event_type == "ENTRY_TRIGGERED" else "OPEN",
            "new_status": "OPEN" if event_type in {"ENTRY_TRIGGERED", "TP1_HIT"} else "CLOSED",
            "event_date": "2026-08-12",
            "event_price": 1000 + index,
            "event_reason": event_type,
        }
        for index, event_type in enumerate(event_types)
    ]
    events.append({
        "event_id": "NOISE",
        "signal_id": "NOISE",
        "symbol": "NOISE",
        "event_type": "SIGNAL_RECONFIRMED",
        "previous_status": "OPEN",
        "new_status": "OPEN",
        "event_date": "2026-08-12",
        "event_price": 999,
        "event_reason": "SCAN:BUY",
    })

    text = build_lifecycle_message(events)

    for heading in (
        "ENTRY TRIGGERED — 2",
        "TP1 HIT — 1",
        "STOP LOSS HIT — 1",
        "TP2 HIT — 1",
        "MAX HOLD EXIT — 1",
        "SIGNAL EXPIRED — 1",
        "SIGNAL INVALIDATED — 1",
    ):
        assert heading in text
    assert text.count("◆") == len(event_types)
    assert "NOISE" not in text


def test_performance_menu_keeps_active_and_lifecycle_as_separate_sends() -> None:
    menu = (ROOT / "maintenance/PERFORMANCE_MENU.bat").read_text(encoding="utf-8-sig")
    assert "%SDE_PYTHON_CMD% tools\\send_active_recommendations.py" in menu
    assert "%SDE_PYTHON_CMD% tools\\send_lifecycle_digest.py" in menu
    assert "Kirim lifecycle digest material ke Telegram?" in menu
    assert "Kirim active recommendations ke Telegram?" in menu
