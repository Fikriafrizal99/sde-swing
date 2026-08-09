from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from modules.portfolio import position_management_engine as base
from modules.portfolio.manual_position_plan import set_manual_plan
from modules.portfolio.position_management_runtime import (
    apply_report_interpretation,
    telegram_text,
)


ROOT = Path(__file__).resolve().parents[1]


def _row() -> dict:
    return {
        "position_id": "p-bnbr",
        "symbol": "BNBR",
        "analysis_date": "2026-08-07",
        "buy_price": 105.0,
        "current_price": 106.0,
        "pnl_pct": 0.95238095,
        "initial_stop_loss": None,
        "initial_tp1": None,
        "initial_tp2": None,
        "milestone": "PRE_TARGET",
        "technical_state": "BULLISH",
        "broker_current_state": "DISTRIBUTION",
        "broker_effective_state": "NEUTRAL",
        "broker_observation_count": 2,
        "broker_context_3d": "INSUFFICIENT_DATA",
        "broker_context_5d": "INSUFFICIENT_DATA",
        "broker_context_7d": "INSUFFICIENT_DATA",
        "broker_context_since_entry": "NEUTRAL",
        "broker_net_flow_since_entry": -102_230_000.0,
        "broker_flow_trend": "INSUFFICIENT_DATA",
        "broker_top_accumulation": [
            {"broker": "YP", "net_value": 18_400_000.0},
            {"broker": "CC", "net_value": 11_200_000.0},
        ],
        "broker_top_distribution": [
            {"broker": "LG", "net_value": -42_600_000.0},
            {"broker": "AK", "net_value": -27_300_000.0},
        ],
        "broker_current_top_accumulation": [],
        "broker_current_top_distribution": [],
        "sector_state": "IMPROVING",
        "market_state": "STRONG BULLISH",
        "management_action": "HOLD",
        "active_stop_loss": None,
        "extended_target": None,
        "reason": "Thesis belum invalid dan target awal belum selesai. Broker history: current=DISTRIBUTION; 3D=INSUFFICIENT_DATA(2/3).",
        "data_quality_status": "VALID",
    }


def test_compact_portfolio_telegram_keeps_execution_levels_and_progressive_broker_conclusion():
    rows = apply_report_interpretation([_row()], interpreter=None)
    text = telegram_text(rows, "2026-08-07")

    assert "📌 <b>BNBR</b> | 106 | +0,95%" in text
    assert "<b>HOLD</b>" in text
    assert "🎯 TP1 : -" in text
    assert "🚀 TP2 : -" in text
    assert "🛡️ SL  : -" in text
    assert "Distribusi muncul dalam 2 sesi sejak entry" in text
    assert "LG -Rp42,60 jt" in text
    assert "AK -Rp27,30 jt" in text
    assert "belum menjadi konfirmasi 3D untuk action engine" in text
    assert "Keputusan HOLD mengikuti engine: Thesis belum invalid dan target awal belum selesai." in text
    assert "Histori broker baru 2 sesi" not in text
    assert "Initial TP/SL belum lengkap" in text

    # Detailed broker windows remain in JSON/database, not the Telegram body.
    assert "BROKER POSITION CONTEXT" not in text
    assert "Today       :" not in text
    assert "3D          :" not in text
    assert "5D          :" not in text
    assert "7D          :" not in text
    assert "Persistence" not in text
    assert "Flow Trend" not in text


def test_one_day_broker_is_explained_immediately_with_actor_and_nominal():
    row = _row()
    row.update({
        "broker_observation_count": 1,
        "broker_context_since_entry": "DISTRIBUTION",
        "broker_current_top_distribution": [
            {"broker": "LG", "net_value": -18_400_000.0},
            {"broker": "YP", "net_value": -11_700_000.0},
        ],
        "broker_top_distribution": [
            {"broker": "LG", "net_value": -18_400_000.0},
            {"broker": "YP", "net_value": -11_700_000.0},
        ],
    })
    text = telegram_text(apply_report_interpretation([row], interpreter=None), "2026-08-07")

    assert "Distribusi muncul pada 1 sesi terbaru" in text
    assert "LG -Rp18,40 jt" in text
    assert "YP -Rp11,70 jt" in text
    assert "persistence belum terkonfirmasi" in text


def test_three_day_context_uses_confirmed_window_and_keeps_engine_reason_authoritative():
    row = _row()
    row.update({
        "broker_observation_count": 3,
        "broker_context_3d": "DISTRIBUTION",
        "broker_context_since_entry": "DISTRIBUTION",
        "management_action": "EXIT",
        "initial_stop_loss": 98.0,
        "initial_tp1": 115.0,
        "initial_tp2": 140.0,
        "reason": "Initial stop pernah terlewati; thesis awal sudah invalid. Broker history: current=DISTRIBUTION; 3D=DISTRIBUTION(3/3).",
    })
    text = telegram_text(apply_report_interpretation([row], interpreter=None), "2026-08-07")

    assert "Context broker 3D DISTRIBUTION" in text
    assert "sejak entry distributor utama LG -Rp42,60 jt dan AK -Rp27,30 jt" in text
    assert "Keputusan EXIT mengikuti engine: Initial stop pernah terlewati; thesis awal sudah invalid." in text
    assert "alasan yang tidak dijelaskan" not in text


def test_ai_cannot_overwrite_deterministic_portfolio_conclusion():
    class BadInterpreter:
        def interpret(self, facts, fallback):
            return SimpleNamespace(
                main_reason="Manajemen EXIT karena alasan yang tidak dijelaskan.",
                main_risk="Risiko AI.",
                execution_note="Ikuti action.",
                source="GROQ",
                status="SUCCESS",
                warning="",
            )

    row = _row()
    row["management_action"] = "EXIT"
    row["reason"] = "Harga berada di/bawah active stop. Broker history: current=DISTRIBUTION."
    result = apply_report_interpretation([row], interpreter=BadInterpreter())[0]

    assert "Harga berada di/bawah active stop." in result["interpretation_main_reason"]
    assert "alasan yang tidak dijelaskan" not in result["interpretation_main_reason"]
    assert result["interpretation_main_risk"] == "Risiko AI."
    assert result["interpretation_execution_note"] == "Ikuti action."


def test_manual_plan_fills_blank_initial_plan_without_overwriting_machine_levels(tmp_path):
    db = tmp_path / "history.db"
    conn = base.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE portfolio_positions (
                position_id TEXT PRIMARY KEY,
                signal_id TEXT,
                symbol TEXT NOT NULL,
                buy_date TEXT NOT NULL,
                quantity REAL NOT NULL,
                buy_price REAL NOT NULL,
                current_status TEXT NOT NULL,
                notes TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO portfolio_positions VALUES ('p1',NULL,'BNBR','2026-08-06',57500,105,'OPEN','', '', '')"
        )
        conn.execute(
            """
            INSERT INTO position_initial_plan (
                position_id, linked_signal_id, symbol, buy_date, buy_price,
                machine_entry_price, initial_stop_loss, initial_tp1, initial_tp2,
                initial_setup, initial_decision, initial_score, created_at
            ) VALUES ('p1','','BNBR','2026-08-06',105,NULL,NULL,NULL,NULL,'','','', '')
            """
        )
        conn.commit()

        set_manual_plan(
            conn,
            position_id="p1",
            stop_loss=98,
            tp1=115,
            tp2=125,
            setup="MANUAL",
        )
        first = conn.execute(
            "SELECT initial_stop_loss, initial_tp1, initial_tp2, initial_setup FROM position_initial_plan WHERE position_id='p1'"
        ).fetchone()
        assert tuple(first) == (98.0, 115.0, 125.0, "MANUAL")

        # A later manual edit may update the separate manual table, but it may
        # never rewrite initial levels that are already fixed in the plan.
        set_manual_plan(
            conn,
            position_id="p1",
            stop_loss=95,
            tp1=120,
            tp2=130,
            setup="MANUAL_EDIT",
        )
        second = conn.execute(
            "SELECT initial_stop_loss, initial_tp1, initial_tp2, initial_setup FROM position_initial_plan WHERE position_id='p1'"
        ).fetchone()
        assert tuple(second) == (98.0, 115.0, 125.0, "MANUAL")
    finally:
        conn.close()


def test_position_management_delivery_is_routed_to_report_topic_only():
    source = (ROOT / "modules/portfolio/position_management_runtime.py").read_text(encoding="utf-8")
    assert 'topic="report"' in source
    assert 'topic="position_management"' not in source


def test_manual_position_management_forces_resend_but_auto_full_daily_keeps_dedupe():
    source = (ROOT / "maintenance/RUN_POSITION_MANAGEMENT.bat").read_text(encoding="utf-8-sig")
    assert 'set "FORCE_ARG=--force"' in source
    assert 'if "%NON_BLOCKING%"=="1" set "FORCE_ARG="' in source
    assert '--telegram !FORCE_ARG!' in source
    assert 'MANUAL FORCE RESEND ke topic Report' in source
    assert 'AUTO DEDUPE ke topic Report' in source


def test_main_engines_are_not_imported_by_portfolio_report_runtime():
    source = (ROOT / "modules/portfolio/position_management_runtime.py").read_text(encoding="utf-8")
    forbidden = (
        "modules.candidate_selector",
        "modules.decision_engine",
        "modules.exit_engine",
        "run_sde_job_integrated",
    )
    assert all(item not in source for item in forbidden)
