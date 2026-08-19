from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from modules.job_runner.delivery import _expand_post_market_heatmap_payloads
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import RunnerContext
from modules.telegram import market_heatmap


def _ctx(tmp_path: Path, *, trade_date: date = date(2026, 8, 18)) -> RunnerContext:
    technical_dir = tmp_path / "technical"
    post_market_dir = tmp_path / "post_market"
    ihsg_path = tmp_path / "IHSG.csv"
    return RunnerContext(
        job="post_market",
        config_path=tmp_path / "pipeline.json",
        scheduler_config_path=tmp_path / "scheduler.json",
        trade_date=trade_date,
        run_id="SDE-POST-MARKET-TEST",
        no_telegram=True,
        debug=True,
        config={
            "paths": {
                "technical_output_dir": str(technical_dir),
                "post_market_output_dir": str(post_market_dir),
                "ihsg_csv": str(ihsg_path),
            }
        },
        scheduler_config={
            "post_market": {
                "market_heatmap": {"enabled": True, "max_symbols": 0},
            },
            "paths": {
                "preview_root": str(tmp_path / "previews"),
                "job_status_root": str(tmp_path / "status"),
                "state_root": str(tmp_path / "state"),
                "job_log": str(tmp_path / "job.log"),
            },
            "delivery": {
                "idempotency_index": str(tmp_path / "state" / "idempotency.json"),
                "delivery_log": str(tmp_path / "state" / "delivery.jsonl"),
                "failed_root": str(tmp_path / "failed"),
                "topic_routing": {"post_market": "9"},
            },
            "telegram": {"maximum_message_length": 4000},
        },
        calendar_config={"holidays": [], "special_trading_days": []},
        config_provenance={"config_version": "1.7.1"},
    )


def _write_market_data(ctx: RunnerContext, *, data_date: str = "2026-08-18") -> Path:
    technical_dir = Path(ctx.config["paths"]["technical_output_dir"])
    technical_dir.mkdir(parents=True, exist_ok=True)
    technical_path = technical_dir / "latest_technical_features.csv"
    pd.DataFrame([
        {"Symbol": "BBCA", "Date": data_date, "Return_1D": -0.77, "Turnover_Value": 2_000_000_000_000, "Close": 9000, "Volume": 222_222_222},
        {"Symbol": "BREN", "Date": data_date, "Return_1D": 2.15, "Turnover_Value": 1_500_000_000_000, "Close": 8500, "Volume": 176_470_588},
        {"Symbol": "ANTM", "Date": data_date, "Return_1D": 6.23, "Turnover_Value": 900_000_000_000, "Close": 3200, "Volume": 281_250_000},
        {"Symbol": "BBRI", "Date": data_date, "Return_1D": -1.31, "Turnover_Value": 700_000_000_000, "Close": 4100, "Volume": 170_731_707},
        {"Symbol": "TLKM", "Date": data_date, "Return_1D": -0.44, "Turnover_Value": 500_000_000_000, "Close": 3300, "Volume": 151_515_151},
        {"Symbol": "ENRG", "Date": data_date, "Return_1D": 7.14, "Turnover_Value": 250_000_000_000, "Close": 560, "Volume": 446_428_571},
    ]).to_csv(technical_path, index=False)

    ihsg_path = Path(ctx.config["paths"]["ihsg_csv"])
    pd.DataFrame([
        {"Date": "2026-08-17", "Close": 6330.98},
        {"Date": data_date, "Close": 6378.46},
    ]).to_csv(ihsg_path, index=False)
    return technical_path


def test_renderer_creates_png_from_existing_current_snapshot_without_mutating_source(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    source = _write_market_data(ctx)
    before = source.read_bytes()

    output = market_heatmap.render_market_heatmap(ctx)

    assert output.exists()
    assert output.suffix.lower() == ".png"
    assert output.stat().st_size > 0
    assert source.read_bytes() == before


def test_renderer_rejects_stale_snapshot(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    _write_market_data(ctx, data_date="2026-08-17")

    with pytest.raises(ValueError, match="HEATMAP_DATA_NOT_CURRENT"):
        market_heatmap.render_market_heatmap(ctx)


def test_delivery_inserts_heatmap_immediately_before_post_market(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    image = tmp_path / "market_heatmap.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(market_heatmap, "heatmap_enabled", lambda _ctx: True)
    monkeypatch.setattr(market_heatmap, "render_market_heatmap", lambda _ctx: image)

    post = ReportPayload(
        report_type="post_market",
        filename="post_market.txt",
        text="🌆 SDE SWING — POST MARKET",
        topic="report",
    )
    payloads = _expand_post_market_heatmap_payloads(ctx, [post])

    assert [payload.report_type for payload in payloads] == ["post_market_heatmap", "post_market"]
    assert getattr(payloads[0], "attachment_path") == image
    assert getattr(payloads[0], "caption") == ""
    assert payloads[0].topic == "post_market"
    assert payloads[1] is post


def test_heatmap_render_failure_never_blocks_post_market(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(market_heatmap, "heatmap_enabled", lambda _ctx: True)

    def fail(_ctx):
        raise RuntimeError("renderer boom")

    monkeypatch.setattr(market_heatmap, "render_market_heatmap", fail)
    post = ReportPayload(
        report_type="post_market",
        filename="post_market.txt",
        text="🌆 SDE SWING — POST MARKET",
        topic="report",
    )

    payloads = _expand_post_market_heatmap_payloads(ctx, [post])

    assert payloads == [post]
    delivery_log = Path(ctx.scheduler_config["delivery"]["delivery_log"])
    assert delivery_log.exists()
    assert "HEATMAP_RENDER_SKIPPED" in delivery_log.read_text(encoding="utf-8")


def test_heatmap_uses_half_percent_neutral_band() -> None:
    assert market_heatmap._color(3.01) == market_heatmap._GREEN_STRONG
    assert market_heatmap._color(0.98) == market_heatmap._GREEN
    assert market_heatmap._color(0.49) == market_heatmap._NEUTRAL
    assert market_heatmap._color(-0.49) == market_heatmap._NEUTRAL
    assert market_heatmap._color(-0.79) == market_heatmap._RED
    assert market_heatmap._color(-3.01) == market_heatmap._RED_STRONG


def test_breadth_counts_include_unchanged_symbols() -> None:
    items = [
        market_heatmap.HeatmapItem("AAA", 1.0, 100.0),
        market_heatmap.HeatmapItem("BBB", -1.0, 90.0),
        market_heatmap.HeatmapItem("CCC", 0.0, 80.0),
    ]
    assert market_heatmap._breadth_counts(items) == (1, 1, 1)


def test_tiny_tiles_hide_labels_and_medium_tiles_keep_readable_typography() -> None:
    assert market_heatmap._tile_typography(0.0010, 0.020, 0.050) is None
    style = market_heatmap._tile_typography(0.0040, 0.050, 0.080)
    assert style is not None
    assert style.symbol_size == 10.0
    assert style.show_change is True
