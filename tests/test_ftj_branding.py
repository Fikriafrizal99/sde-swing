from __future__ import annotations

import pytest

from modules.branding import apply_ftj_branding
from modules.job_runner.reports import ReportPayload
from modules.news import news_monitor
from tools import send_active_recommendations, send_lifecycle_digest


@pytest.mark.parametrize(
    ("legacy_title", "ftj_title"),
    [
        ("SDE SWING — MARKET OUTLOOK", "FTJ — MARKET PULSE"),
        ("SDE SWING — POST MARKET", "FTJ — CLOSING PULSE"),
        ("SDE SWING — BROKER SUMMARY", "FTJ — BROKER FLOW"),
        ("SDE SWING — BROKER MULTI-DAY", "FTJ — SMART MONEY FLOW"),
        ("SDE SWING — FINAL WATCHLIST", "FTJ — SWING WATCHLIST"),
        ("SDE SWING — ACTIVE RECOMMENDATIONS", "FTJ — ACTIVE SETUPS"),
        ("SDE SWING — LIFECYCLE DIGEST", "FTJ — POSITION UPDATE"),
        ("SDE SWING — MORNING NEWS", "FTJ — MORNING BRIEF"),
        ("SDE SWING — POST MARKET NEWS", "FTJ — MARKET NEWS"),
        ("SDE SWING — MARKET HEATMAP", "FTJ — MARKET HEATMAP"),
    ],
)
def test_every_canonical_title_mapping_is_deterministic_and_idempotent(
    legacy_title: str,
    ftj_title: str,
) -> None:
    original = f"<b>{legacy_title}</b>\nFakta engine tetap sama."
    expected = f"<b>{ftj_title}</b>\nFakta engine tetap sama."

    branded = apply_ftj_branding(original)

    assert branded == expected
    assert apply_ftj_branding(branded) == expected


def test_generic_legacy_sde_title_uses_ftj_namespace_without_changing_body() -> None:
    original = "SDE SWING — CUSTOM REPORT\nScore 88 | BUY ON TRIGGER"

    assert apply_ftj_branding(original) == "FTJ — CUSTOM REPORT\nScore 88 | BUY ON TRIGGER"


@pytest.mark.parametrize(
    "idx_marker",
    [
        "SDE SWING — IDX DISCLOSURE",
        "IDX DISCLOSURE WATCHER",
        "📢 IDX KETERBUKAAN INFORMASI",
    ],
)
def test_idx_disclosure_markers_are_byte_for_byte_excluded(idx_marker: str) -> None:
    original = f"<b>{idx_marker}</b>\nSDE SWING — MARKET OUTLOOK\nDokumen resmi IDX"

    rendered = apply_ftj_branding(original)

    assert rendered == original
    assert rendered.encode("utf-8") == original.encode("utf-8")


def test_idx_exclusion_is_case_insensitive() -> None:
    original = "Idx Disclosure Watcher\nSDE SWING — MARKET OUTLOOK"

    assert apply_ftj_branding(original) == original


def test_report_payload_applies_branding_at_runtime_boundary_only() -> None:
    payload = ReportPayload(
        report_type="market_outlook",
        filename="MARKET_OUTLOOK.txt",
        text="<b>SDE SWING — MARKET OUTLOOK</b>\nRegime: RISK_ON",
        topic="report",
        material_signature="engine-owned-signature",
    )

    assert payload.text == "<b>FTJ — MARKET PULSE</b>\nRegime: RISK_ON"
    assert payload.report_type == "market_outlook"
    assert payload.filename == "MARKET_OUTLOOK.txt"
    assert payload.topic == "report"
    assert payload.material_signature == "engine-owned-signature"


def test_direct_lifecycle_senders_brand_after_canonical_formatting(monkeypatch) -> None:
    monkeypatch.setattr(
        send_active_recommendations,
        "_build_active_message",
        lambda *_args, **_kwargs: "SDE SWING — ACTIVE RECOMMENDATIONS\nBBRI WAITING_TRIGGER",
    )
    monkeypatch.setattr(
        send_lifecycle_digest,
        "_build_lifecycle_message",
        lambda *_args, **_kwargs: "SDE SWING — LIFECYCLE DIGEST\nBBRI ENTRY_TRIGGERED",
    )

    assert send_active_recommendations.build_active_message(object()) == (
        "FTJ — ACTIVE SETUPS\nBBRI WAITING_TRIGGER"
    )
    assert send_lifecycle_digest.build_lifecycle_message([]) == (
        "FTJ — POSITION UPDATE\nBBRI ENTRY_TRIGGERED"
    )


def test_news_branding_is_delivery_only_and_keeps_prebranding_signature() -> None:
    original = "<b>SDE SWING — MORNING NEWS</b>\nBI mempertahankan suku bunga."
    signature_before_delivery = news_monitor._signature(original)

    delivered_parts = news_monitor._split_text(original)

    assert delivered_parts == ["<b>FTJ — MORNING BRIEF</b>\nBI mempertahankan suku bunga."]
    assert news_monitor._signature(original) == signature_before_delivery
    assert news_monitor._signature(delivered_parts[0]) != signature_before_delivery
