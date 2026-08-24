from __future__ import annotations

from pathlib import Path

import pandas as pd

from modules.analytics import lifecycle_presentation as presentation
from modules.analytics import outcome_tracker
from modules.analytics import outcome_tracker_baseline as baseline
from tools import preview_lifecycle_digest
from tools import send_active_recommendations
from tools import send_lifecycle_digest


ROOT = Path(__file__).resolve().parents[1]


def test_all_user_facing_tools_reuse_canonical_formatters_before_branding() -> None:
    assert send_active_recommendations._build_active_message is presentation.build_active_message
    assert send_lifecycle_digest._build_lifecycle_message is presentation.build_lifecycle_message
    assert preview_lifecycle_digest.build_lifecycle_message is presentation.build_lifecycle_message
    assert send_active_recommendations.build_active_message is not presentation.build_active_message
    assert send_lifecycle_digest.build_lifecycle_message is not presentation.build_lifecycle_message


def test_outcome_tracker_sync_hooks_use_canonical_formatters() -> None:
    assert baseline._active_recommendations_telegram is outcome_tracker._active_recommendations_telegram
    assert baseline._status_changes_telegram is outcome_tracker._status_changes_telegram

    active = pd.DataFrame([
        {
            "symbol": "BBRI",
            "current_status": "WAITING_TRIGGER",
            "entry_zone_low": 4000,
            "entry_zone_high": 4050,
            "reference_price": 3980,
            "current_price": 3980,
            "stop_loss": 3900,
            "take_profit_1": 4200,
            "take_profit_2": 4400,
            "market_session_age": 3,
            "scan_staleness_sessions": 2,
            "recommendation_count": 2,
        }
    ])
    expected = presentation.build_active_message(active)
    assert baseline._active_recommendations_telegram(active) == expected
    assert "Last Scan:" not in expected


def test_final_watchlist_entrypoint_sends_active_before_watchlist_ai() -> None:
    source = (ROOT / "tools/run_final_watchlist_entrypoint.py").read_text(encoding="utf-8")
    active_call = source.index("_run_active_recommendations(forwarded)")
    ai_call = source.index("_run_watchlist_ai(forwarded, trade_date)")
    assert active_call < ai_call
    assert "tools/send_active_recommendations.py" in source
    assert "Final Watchlist tetap sukses" in source


def test_final_watchlist_active_send_skips_no_telegram_and_dry_run() -> None:
    source = (ROOT / "tools/run_final_watchlist_entrypoint.py").read_text(encoding="utf-8")
    assert 'if "--no-telegram" in forwarded:' in source
    assert 'if "--dry-run" in forwarded:' in source
    assert "--dry-run has no fresh persisted lifecycle state" in source


def test_presentation_contract_document_exists_and_names_single_owner() -> None:
    path = ROOT / "docs/LIFECYCLE_PRESENTATION_CONTRACT.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "modules/analytics/lifecycle_presentation.py" in text
    assert "single source of truth" in text.lower()
    assert "Frozen baseline exception" in text
    assert "Last Scan" in text
