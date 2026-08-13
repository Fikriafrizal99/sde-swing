from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from modules.portfolio.broker_history_context import (
    build_position_broker_context,
    ensure_schema,
    sync_latest_broker_summary,
)
from modules.portfolio.position_management_runtime import apply_report_interpretation, telegram_text


def _daily_payload(symbol: str, direction: str = "ACCUMULATION") -> dict:
    positive = direction == "ACCUMULATION"
    return {
        "EMITEN": symbol,
        "TOTAL_BUY": 150.0 if positive else 50.0,
        "TOTAL_SELL": 50.0 if positive else 150.0,
        "NET_FLOW": 100.0 if positive else -100.0,
        "TOTAL_VALUE": 200.0,
        "TOTAL_VOLUME": 1000.0,
        "BUYER_CONCENTRATION": 0.7 if positive else 0.3,
        "SELLER_CONCENTRATION": 0.3 if positive else 0.7,
        "BROKER_ACCDIST": "BIG ACC" if positive else "BIG DIST",
        "AVG_ACCDIST": "BIG ACC" if positive else "BIG DIST",
        "TOP3_ACCDIST": "BIG ACC" if positive else "BIG DIST",
        "TOP_BUYER_1": "YP",
        "TOP_BUYER_1_VALUE": 70.0,
        "TOP_SELLER_1": "LG",
        "TOP_SELLER_1_VALUE": -80.0 if not positive else -20.0,
    }


def _write_summary(
    root: Path,
    *,
    start: str,
    end: str,
    source: str | None = None,
    daily: bool | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "BROKER_SUMMARY_LATEST.csv"
    row = _daily_payload("BBCA")
    row.update({"FROM_DATE": start, "TO_DATE": end})
    pd.DataFrame([row]).to_csv(path, index=False)
    if source is not None:
        path.with_suffix(".manifest.json").write_text(
            json.dumps({
                "broker_period_type": "1D" if start == end else "5D",
                "broker_period_start": start,
                "broker_period_end": end,
                "broker_trading_days": 1 if start == end else 5,
                "broker_period_source": source,
                "aggregate_snapshot": not bool(daily),
                "daily_history_eligible": bool(daily),
            }),
            encoding="utf-8",
        )
    return path


def test_aggregate_3d_5d_and_custom_never_enter_portfolio_daily_history(tmp_path: Path) -> None:
    for source, start, end in (
        ("STOCKBIT_AGGREGATE_EXPORT", "2026-08-10", "2026-08-12"),
        ("STOCKBIT_AGGREGATE_EXPORT", "2026-08-06", "2026-08-12"),
        ("CUSTOM_AGGREGATE", "2026-08-04", "2026-08-12"),
    ):
        db = tmp_path / f"{source}-{start}.db"
        conn = sqlite3.connect(db)
        try:
            ensure_schema(conn)
            summary = _write_summary(
                tmp_path / source,
                start=start,
                end=end,
                source=source,
                daily=False,
            )
            assert sync_latest_broker_summary(conn, summary) == ""
            assert conn.execute("SELECT COUNT(*) FROM broker_snapshots").fetchone()[0] == 0
        finally:
            conn.close()


def test_real_1d_archive_is_idempotent_and_keeps_provenance(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    conn = sqlite3.connect(db)
    try:
        ensure_schema(conn)
        summary = _write_summary(
            tmp_path / "daily",
            start="2026-08-12",
            end="2026-08-12",
            source="STOCKBIT_1D",
            daily=True,
        )
        first = sync_latest_broker_summary(conn, summary)
        second = sync_latest_broker_summary(conn, summary)
        assert first and first == second
        row = conn.execute(
            "SELECT broker_date, from_date, to_date, snapshot_hash, manifest_json FROM broker_snapshots"
        ).fetchone()
        assert row[0:3] == ("2026-08-12", "2026-08-12", "2026-08-12")
        assert row[3]
        manifest = json.loads(row[4])
        assert manifest["broker_period_type"] == "1D"
        assert manifest["broker_period_source"] == "STOCKBIT_1D"
        assert manifest["daily_history_eligible"] is True
        assert conn.execute("SELECT COUNT(*) FROM broker_snapshots").fetchone()[0] == 1
    finally:
        conn.close()


def _insert_daily(conn: sqlite3.Connection, day: str, direction: str = "ACCUMULATION") -> None:
    snapshot_id = f"daily-{day}"
    payload = _daily_payload("BBCA", direction)
    conn.execute(
        """
        INSERT INTO broker_snapshots (
            broker_snapshot_id, broker_date, from_date, to_date, source_files,
            coverage, snapshot_hash, data_quality_status, manifest_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id, day, day, day, "STOCKBIT_1D", 1.0, snapshot_id,
            "VALID", "{}", day + "T18:00:00+07:00",
        ),
    )
    conn.execute(
        "INSERT INTO broker_summary (broker_snapshot_id, symbol, row_json) VALUES (?, ?, ?)",
        (snapshot_id, "BBCA", json.dumps(payload)),
    )


def test_missing_exact_idx_session_does_not_backfill_older_session(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "history.db")
    try:
        ensure_schema(conn)
        for day in ("2026-08-05", "2026-08-06", "2026-08-10", "2026-08-11", "2026-08-12"):
            _insert_daily(conn, day)
        conn.commit()
        context = build_position_broker_context(
            conn, symbol="BBCA", buy_date="2026-08-05", analysis_date="2026-08-12"
        )
        assert context["3D"]["coverage_status"] == "COMPLETE"
        assert context["3D"]["coverage_text"] == "3/3"
        assert context["5D"]["coverage_status"] == "INSUFFICIENT_DATA"
        assert context["5D"]["coverage_text"] == "4/5"
        assert "2026-08-07" in context["5D"]["missing_sessions"]
    finally:
        conn.close()


def test_exact_complete_3d_5d_and_since_entry_use_real_daily_sessions(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "history.db")
    try:
        ensure_schema(conn)
        for day in ("2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12"):
            _insert_daily(conn, day)
        conn.commit()
        context = build_position_broker_context(
            conn, symbol="BBCA", buy_date="2026-08-06", analysis_date="2026-08-12"
        )
        assert context["3D"]["coverage_status"] == "COMPLETE"
        assert context["5D"]["coverage_status"] == "COMPLETE"
        assert context["since_entry"]["actual_session_count"] == 5
        assert context["since_entry"]["observed_sessions"] == [
            "2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12"
        ]
    finally:
        conn.close()


def test_stale_latest_daily_row_is_not_presented_as_today_pulse(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "history.db")
    try:
        ensure_schema(conn)
        _insert_daily(conn, "2026-08-11")
        conn.commit()
        context = build_position_broker_context(
            conn, symbol="BBCA", buy_date="2026-08-11", analysis_date="2026-08-12"
        )
        assert context["today_pulse_available"] is False
        assert context["current_state"] == "UNAVAILABLE"
        assert context["latest_historical_date"] == "2026-08-11"
        assert context["broker_data_date"] == "2026-08-11"
    finally:
        conn.close()


def _reason_row() -> dict:
    return {
        "position_id": "p-bbca",
        "symbol": "BBCA",
        "analysis_date": "2026-08-12",
        "buy_price": 100.0,
        "current_price": 102.0,
        "pnl_pct": 2.0,
        "initial_stop_loss": 98.0,
        "initial_tp1": 110.0,
        "initial_tp2": 120.0,
        "active_stop_loss": 98.0,
        "extended_target": None,
        "milestone": "PRE_TARGET",
        "technical_state": "BULLISH",
        "broker_current_state": "ACCUMULATION",
        "broker_effective_state": "ACCUMULATION",
        "broker_observation_count": 5,
        "broker_context_3d": "ACCUMULATION",
        "broker_context_5d": "ACCUMULATION",
        "broker_context_7d": "INSUFFICIENT_DATA",
        "broker_context_since_entry": "ACCUMULATION",
        "broker_context_3d_coverage_status": "COMPLETE",
        "broker_context_3d_coverage_text": "3/3",
        "broker_context_5d_coverage_status": "COMPLETE",
        "broker_context_5d_coverage_text": "5/5",
        "broker_net_flow_since_entry": 500.0,
        "broker_flow_trend": "STABLE",
        "broker_top_accumulation": [],
        "broker_top_distribution": [],
        "broker_current_top_accumulation": [],
        "broker_current_top_distribution": [],
        "management_action": "EXIT",
        "reason": "Initial stop pernah terlewati sejak posisi dibuka. Broker history: current=ACCUMULATION.",
        "data_quality_status": "VALID",
    }


def test_exit_driver_precedes_accumulation_conflict_and_ai_cannot_invent_number() -> None:
    row = _reason_row()

    class InvalidAI:
        def interpret(self, facts, fallback):
            return SimpleNamespace(
                main_reason="EXIT karena stop 99999 tetap aman.",
                main_risk="Risiko 123456.",
                execution_note="Ikuti EXIT.",
                source="GROQ",
                status="SUCCESS",
                warning="",
            )

    result = apply_report_interpretation([row], interpreter=InvalidAI())[0]
    assert result["interpretation_main_reason"].startswith("Keputusan EXIT mengikuti engine:")
    assert "99999" not in result["interpretation_main_reason"]
    assert "Broker accumulation tidak mengaktifkan kembali" in result["interpretation_main_reason"]
    assert result["management_action"] == "EXIT"
    assert result["initial_tp1"] == 110.0


def test_ai_exception_falls_back_without_changing_engine_owned_fields() -> None:
    row = _reason_row()
    original = {key: row[key] for key in ("management_action", "initial_stop_loss", "initial_tp1", "initial_tp2")}

    class FailingAI:
        def interpret(self, facts, fallback):
            raise TimeoutError("provider timeout")

    result = apply_report_interpretation([row], interpreter=FailingAI())[0]
    assert result["interpretation_source"] == "DETERMINISTIC"
    assert result["interpretation_status"] == "FALLBACK"
    assert "Keputusan EXIT mengikuti engine:" in result["interpretation_main_reason"]
    assert all(result[key] == value for key, value in original.items())


def test_hold_with_unconfirmed_distribution_keeps_reason_in_data_not_compact_telegram() -> None:
    row = _reason_row()
    row.update({
        "management_action": "HOLD",
        "broker_current_state": "DISTRIBUTION",
        "broker_effective_state": "NEUTRAL",
        "broker_observation_count": 2,
        "broker_context_3d": "INSUFFICIENT_DATA",
        "broker_context_5d": "INSUFFICIENT_DATA",
        "broker_context_since_entry": "DISTRIBUTION",
        "broker_context_3d_coverage_status": "INSUFFICIENT_DATA",
        "broker_context_3d_coverage_text": "2/3",
        "broker_context_5d_coverage_status": "INSUFFICIENT_DATA",
        "broker_context_5d_coverage_text": "2/5",
        "reason": "Thesis belum invalid dan target awal belum tercapai. Broker history: current=DISTRIBUTION.",
    })
    results = apply_report_interpretation([row], interpreter=None)
    reason = results[0]["interpretation_main_reason"]
    text = telegram_text(results, "2026-08-12")
    assert reason.index("Keputusan HOLD mengikuti engine:") < reason.index("Distribution adalah warning")
    assert "belum cukup terkonfirmasi" in reason
    assert "Broker 5D" not in reason
    assert reason not in text
    assert "Tidak ada posisi yang membutuhkan tindakan khusus" in text
