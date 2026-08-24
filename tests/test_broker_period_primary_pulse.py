from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from modules.broker_bridge.broker_period_context import primary_pulse_alignment
from modules.broker_bridge.broker_period_view import (
    active_primary_raw_snapshot_path,
    load_broker_period_view,
)


def _write_summary(path: Path, *, start: str, end: str, net_flow: float, buyer: str) -> None:
    pd.DataFrame([
        {
            "EMITEN": "AAA",
            "FROM_DATE": start,
            "TO_DATE": end,
            "TOTAL_BUY": max(net_flow, 0) + 2_000,
            "TOTAL_SELL": max(-net_flow, 0) + 1_000,
            "NET_FLOW": net_flow,
            "BROKER_ACCDIST": "ACCUMULATION" if net_flow > 0 else "DISTRIBUTION",
            "BUYER_CONCENTRATION": 0.61,
            "SELLER_CONCENTRATION": 0.31,
            "AVG_BUYER_PRICE": 1_010,
            "AVG_SELLER_PRICE": 1_005,
            "TOP_BUYER_1": buyer,
            "TOP_SELLER_1": "SUMMARY_SELLER",
        }
    ]).to_csv(path, index=False)


def _write_raw(path: Path, *, start: str, end: str, buyer: str, seller: str) -> None:
    pd.DataFrame([
        {
            "SYMBOL": "AAA", "FROM_DATE": start, "TO_DATE": end,
            "SIDE": "BUY", "RANK": 1, "BROKER_CODE": buyer,
            "BROKER_TYPE": "ASING", "NET_VALUE": 3_000_000,
            "NET_LOT": 30, "GROSS_VALUE": 3_000_000,
            "GROSS_LOT": 30, "FREQUENCY": 3, "AVG_PRICE": 1_010,
        },
        {
            "SYMBOL": "AAA", "FROM_DATE": start, "TO_DATE": end,
            "SIDE": "SELL", "RANK": 1, "BROKER_CODE": seller,
            "BROKER_TYPE": "DOMESTIK", "NET_VALUE": -1_000_000,
            "NET_LOT": -10, "GROSS_VALUE": 1_000_000,
            "GROSS_LOT": 10, "FREQUENCY": 2, "AVG_PRICE": 1_005,
        },
    ]).to_csv(path, index=False)


def _write_selected_sidecar(
    canonical: Path,
    *,
    period_type: str,
    primary_summary: Path,
    primary_raw: Path | None,
    daily_summary: Path | None = None,
    daily_raw: Path | None = None,
) -> None:
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("EMITEN\nAAA\n", encoding="utf-8")
    payload = {
        "snapshot_id": f"PRIMARY-{period_type}",
        "broker_period_type": period_type,
        "broker_period_start": "2026-08-05" if period_type != "1D" else "2026-08-07",
        "broker_period_end": "2026-08-07",
        "broker_period_source": "STOCKBIT_AGGREGATE_EXPORT" if period_type != "1D" else "STOCKBIT_1D",
        "primary_summary_snapshot_path": str(primary_summary),
        "primary_raw_snapshot_path": str(primary_raw) if primary_raw else "",
        "today_pulse_snapshot_id": "TODAY-1D" if period_type != "1D" else "",
        "today_pulse_source": "STOCKBIT_1D" if period_type != "1D" else "",
        "daily_capture_summary_snapshot_path": str(daily_summary) if daily_summary else "",
        "daily_capture_raw_snapshot_path": str(daily_raw) if daily_raw else "",
    }
    canonical.with_suffix(".manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def test_exact_3d_primary_facts_win_over_exact_today_pulse(tmp_path: Path) -> None:
    primary_summary = tmp_path / "PRIMARY_3D_SUMMARY.csv"
    primary_raw = tmp_path / "PRIMARY_3D_RAW.csv"
    today_summary = tmp_path / "TODAY_1D_SUMMARY.csv"
    today_raw = tmp_path / "TODAY_1D_RAW.csv"
    canonical = tmp_path / "broker" / "BROKER_SUMMARY_LATEST.csv"
    _write_summary(primary_summary, start="2026-08-05", end="2026-08-07", net_flow=9_000_000, buyer="PRIMARY_SUMMARY")
    _write_raw(primary_raw, start="2026-08-05", end="2026-08-07", buyer="PX", seller="PS")
    _write_summary(today_summary, start="2026-08-07", end="2026-08-07", net_flow=-2_000_000, buyer="TODAY_SUMMARY")
    _write_raw(today_raw, start="2026-08-07", end="2026-08-07", buyer="TD", seller="TS")
    _write_selected_sidecar(
        canonical,
        period_type="3D",
        primary_summary=primary_summary,
        primary_raw=primary_raw,
        daily_summary=today_summary,
        daily_raw=today_raw,
    )
    # A canonical raw file exists, but it is TODAY-like and must never replace
    # the selected PRIMARY raw snapshot.
    _write_raw(canonical.parent / "BROKER_RAW_LATEST.csv", start="2026-08-07", end="2026-08-07", buyer="WRONG", seller="WRONG")

    view = load_broker_period_view(canonical, trade_date="2026-08-07", project_root=tmp_path)
    facts = view.symbol("AAA")

    assert facts["primary"]["net_flow"] == 9_000_000
    assert facts["primary"]["top_buyers"][0]["broker"] == "PX"
    assert facts["primary"]["top_sellers"][0]["broker"] == "PS"
    assert facts["today"]["net_flow"] == -2_000_000
    assert facts["today"]["top_buyers"][0]["broker"] == "TD"
    assert facts["alignment"] == "NEGATIVE_DIVERGENCE"
    assert facts["primary_raw_status"] == "AVAILABLE"
    assert facts["today_raw_status"] == "AVAILABLE"
    assert str(primary_raw.resolve()) in view.input_paths
    assert all("WRONG" not in str(item) for item in facts["primary"]["top_buyers"])


def test_primary_1d_suppresses_duplicate_today_context(tmp_path: Path) -> None:
    primary_summary = tmp_path / "PRIMARY_1D_SUMMARY.csv"
    primary_raw = tmp_path / "PRIMARY_1D_RAW.csv"
    daily_summary = tmp_path / "TODAY_DUPLICATE_SUMMARY.csv"
    daily_raw = tmp_path / "TODAY_DUPLICATE_RAW.csv"
    canonical = tmp_path / "broker" / "BROKER_SUMMARY_LATEST.csv"
    _write_summary(primary_summary, start="2026-08-07", end="2026-08-07", net_flow=1_000_000, buyer="PRIMARY_SUMMARY")
    _write_raw(primary_raw, start="2026-08-07", end="2026-08-07", buyer="P1", seller="S1")
    _write_summary(daily_summary, start="2026-08-07", end="2026-08-07", net_flow=-1_000_000, buyer="DUPLICATE")
    _write_raw(daily_raw, start="2026-08-07", end="2026-08-07", buyer="DUPLICATE", seller="DUPLICATE")
    _write_selected_sidecar(
        canonical,
        period_type="1D",
        primary_summary=primary_summary,
        primary_raw=primary_raw,
        daily_summary=daily_summary,
        daily_raw=daily_raw,
    )

    view = load_broker_period_view(canonical, trade_date="2026-08-07", project_root=tmp_path)
    facts = view.symbol("AAA")

    assert view.has_separate_today is False
    assert facts["primary"]["top_buyers"][0]["broker"] == "P1"
    assert facts["today"] == {}
    assert facts["today_pulse_status"] == "NOT_APPLICABLE"
    assert facts["alignment"] == ""


def test_missing_or_wrong_date_primary_raw_fails_closed(tmp_path: Path) -> None:
    primary_summary = tmp_path / "PRIMARY_SUMMARY.csv"
    canonical = tmp_path / "broker" / "BROKER_SUMMARY_LATEST.csv"
    _write_summary(primary_summary, start="2026-08-05", end="2026-08-07", net_flow=9_000_000, buyer="SUMMARY_ONLY")
    _write_selected_sidecar(
        canonical,
        period_type="3D",
        primary_summary=primary_summary,
        primary_raw=None,
    )
    _write_raw(canonical.parent / "BROKER_RAW_LATEST.csv", start="2026-08-07", end="2026-08-07", buyer="WRONG", seller="WRONG")

    missing = load_broker_period_view(canonical, trade_date="2026-08-07", project_root=tmp_path).symbol("AAA")
    assert active_primary_raw_snapshot_path(canonical, trade_date="2026-08-07", project_root=tmp_path) is None
    assert missing["primary_raw_status"] == "MISSING"
    assert missing["primary"]["top_buyers"] == []
    assert missing["primary"]["top_sellers"] == []

    wrong_date_raw = tmp_path / "PRIMARY_WRONG_DATE_RAW.csv"
    _write_raw(wrong_date_raw, start="2026-08-04", end="2026-08-06", buyer="STALE", seller="STALE")
    _write_selected_sidecar(
        canonical,
        period_type="3D",
        primary_summary=primary_summary,
        primary_raw=wrong_date_raw,
    )
    mismatch = load_broker_period_view(canonical, trade_date="2026-08-07", project_root=tmp_path).symbol("AAA")
    assert mismatch["primary_raw_status"] == "DATE_MISMATCH"
    assert mismatch["primary"]["top_buyers"] == []


def test_primary_today_alignment_is_context_only_and_deterministic() -> None:
    assert primary_pulse_alignment(100, 50, pulse_status="AVAILABLE") == "ALIGNED_POSITIVE"
    assert primary_pulse_alignment(-100, 50, pulse_status="AVAILABLE") == "POSITIVE_DIVERGENCE"
    assert primary_pulse_alignment(100, None, pulse_status="NOT_AVAILABLE") == "INSUFFICIENT"
