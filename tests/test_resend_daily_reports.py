from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import tools.resend_daily_report as resend


def test_market_outlook_resend_reads_dated_existing_artifacts(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "data" / "output"
    global_path = output_root / "global_market" / "2026-08-07" / "global_market_snapshot.json"
    regime_path = output_root / "market_regime" / "2026-08-07" / "market_outlook_regime.json"
    global_path.parent.mkdir(parents=True)
    regime_path.parent.mkdir(parents=True)
    global_path.write_text(json.dumps({"snapshot_id": "GLOBAL-0807"}), encoding="utf-8")
    regime_path.write_text(json.dumps({"market_regime": "BULLISH"}), encoding="utf-8")

    monkeypatch.setattr(
        resend,
        "resolve",
        lambda value: output_root / "global_market" if str(value) == "data/output/global_market" else Path(value),
    )
    monkeypatch.setattr(resend, "enhanced_market_outlook_payloads", lambda ctx, global_snapshot, market_status: [
        (global_snapshot["snapshot_id"], market_status["market_regime"])
    ])

    ctx = SimpleNamespace(
        trade_date=date(2026, 8, 7),
        previews_root=output_root / "telegram_preview",
    )
    payloads, sources = resend._market_outlook_payloads(ctx)

    assert payloads == [("GLOBAL-0807", "BULLISH")]
    assert Path(sources["source_global_market"]).resolve() == global_path.resolve()
    assert Path(sources["source_market_regime"]).resolve() == regime_path.resolve()


def test_post_market_resend_selects_successful_manifest_for_requested_date(tmp_path: Path, monkeypatch) -> None:
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    (manifest_dir / "SWING_RUN_MANIFEST_BAD.json").write_text(
        json.dumps({"Technical_Date": "2026-08-07", "Pipeline_Status": "FAILED", "Run_ID": "BAD"}),
        encoding="utf-8",
    )
    target = manifest_dir / "SWING_RUN_MANIFEST_TARGET.json"
    target.write_text(
        json.dumps({
            "Technical_Date": "2026-08-07",
            "Pipeline_Status": "SUCCESS",
            "Run_ID": "TARGET",
            "Snapshot_Manifest": "data/output/snapshots/2026-08-07/manifest.json",
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(resend, "enhanced_post_market_payloads", lambda ctx, manifest: [manifest["Run_ID"]])
    ctx = SimpleNamespace(
        trade_date=date(2026, 8, 7),
        path=lambda key, default: manifest_dir,
    )
    payloads, sources = resend._post_market_payloads(ctx)

    assert payloads == ["TARGET"]
    assert sources["source_run_id"] == "TARGET"
    assert Path(sources["source_manifest"]).name == target.name


def test_market_and_post_market_mode_three_use_delivery_only_resend() -> None:
    market = Path("RUN_MARKET_OUTLOOK.bat").read_text(encoding="utf-8")
    post = Path("RUN_POST_MARKET.bat").read_text(encoding="utf-8")

    for text, job in ((market, "market_outlook"), (post, "post_market")):
        assert "tools\\resolve_last_trading_day.py" in text
        assert f"tools\\resend_daily_report.py --job {job} --trade-date %RESEND_DATE%" in text
        assert "!RESEND_DATE!" not in text
        resend_block = text.split(":RESEND", 1)[1].split(":RESEND_DATE_FAILED", 1)[0]
        assert "run_sde_job.py" not in resend_block
