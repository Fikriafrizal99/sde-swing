from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from modules.data_sources.legacy_daily_bar_adapter import (
    CANONICAL_DAILY_HISTORY_CONTRACT,
    materialize_legacy_daily_history,
)
from modules.technical_feature_engine import post_market_validated_runner as runner


class AcceptingManager:
    def __init__(self):
        self.calls = []

    def route(self, record_type, symbol, market_date, *, candidates, expected_market_date=None):
        self.calls.append((record_type, symbol, market_date, expected_market_date))
        record = candidates["HISTORICAL_PROVIDER"]
        quality = SimpleNamespace(accepted=True, findings=[])
        return SimpleNamespace(record=record, quality=quality)

    def provider_metadata(self, **kwargs):
        return {
            "primary_provider": "HISTORICAL_PROVIDER",
            "provider_status": "READY",
            "data_source_mode": "FILE",
            "providers_attempted": ["HISTORICAL_PROVIDER"],
            "source_coverage_ratio": kwargs.get("coverage_ratio", 0.0),
        }


def _write_history(path: Path, dates: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame({
        "Symbol": ["BBCA"] * len(dates),
        "Ticker": ["BBCA.JK"] * len(dates),
        "Date": dates,
        "Open": [1000.0 + i for i in range(len(dates))],
        "High": [1010.0 + i for i in range(len(dates))],
        "Low": [990.0 + i for i in range(len(dates))],
        "Close": [1005.0 + i for i in range(len(dates))],
        "Adj Close": [1004.5 + i for i in range(len(dates))],
        "Volume": [1_000_000.0 + i for i in range(len(dates))],
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def test_canonical_materializer_preserves_ohlcv_and_never_returns_raw_dir(tmp_path: Path):
    input_dir = tmp_path / "historical" / "by_symbol"
    manifest_dir = tmp_path / "manifests"
    source = _write_history(
        input_dir / "BBCA.csv",
        ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"],
    )
    manager = AcceptingManager()

    canonical_dir, audit = materialize_legacy_daily_history(
        manager,
        input_dir=input_dir,
        symbols=["BBCA"],
        expected_market_date="2026-08-12",
        run_id="RUN-C5",
        manifest_dir=manifest_dir,
    )

    assert canonical_dir != input_dir
    assert canonical_dir.name == "RUN-C5"
    assert audit["Contract"] == CANONICAL_DAILY_HISTORY_CONTRACT
    assert audit["Boundary"] == "DataSourceManager.route"
    assert audit["Rows_After_Expected_Date_Blocked"] == 1
    assert audit["Canonical_Coverage_Ratio"] == 1.0

    canonical = pd.read_csv(canonical_dir / "BBCA.csv")
    expected = source[source["Date"] <= "2026-08-12"].reset_index(drop=True)
    assert canonical["Date"].astype(str).tolist() == expected["Date"].astype(str).tolist()
    for column in ("Open", "High", "Low", "Close", "Volume"):
        assert canonical[column].tolist() == expected[column].astype(float).tolist()
    assert manager.calls
    assert all(call[0] == "DailyBar" for call in manager.calls)
    assert all(call[2] == call[3] for call in manager.calls)


def test_symbol_without_expected_closed_date_is_excluded_not_substituted(tmp_path: Path):
    input_dir = tmp_path / "historical" / "by_symbol"
    manifest_dir = tmp_path / "manifests"
    _write_history(input_dir / "BBCA.csv", ["2026-08-11", "2026-08-12"])
    stale = _write_history(input_dir / "TLKM.csv", ["2026-08-10", "2026-08-11"])
    stale["Symbol"] = "TLKM"
    stale["Ticker"] = "TLKM.JK"
    stale.to_csv(input_dir / "TLKM.csv", index=False)
    manager = AcceptingManager()

    canonical_dir, audit = materialize_legacy_daily_history(
        manager,
        input_dir=input_dir,
        symbols=["BBCA", "TLKM"],
        expected_market_date="2026-08-12",
        run_id="RUN-C5-PARTIAL",
        manifest_dir=manifest_dir,
    )

    assert (canonical_dir / "BBCA.csv").exists()
    assert not (canonical_dir / "TLKM.csv").exists()
    assert audit["Rejected_Symbols"]["TLKM"].startswith("EXPECTED_DATE_NOT_CANONICAL:")
    assert audit["Canonical_Coverage_Ratio"] == 0.5


def test_wrapper_builds_engine_input_from_datasource_manager_boundary(tmp_path: Path, monkeypatch):
    raw_dir = tmp_path / "historical" / "by_symbol"
    raw_dir.mkdir(parents=True)
    (raw_dir / "BBCA.csv").write_text("Date,Open,High,Low,Close,Volume\n", encoding="utf-8")
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir()
    canonical_dir = tmp_path / "historical" / "canonical_runs" / "RUN"
    canonical_dir.mkdir(parents=True)

    manifest = {
        "Latest_Expected_Trading_Date": "2026-08-12",
        "Data_Quality_Status": "VALID",
        "Symbol_Plans": [{
            "symbol": "BBCA",
            "status": "ALREADY_CURRENT",
            "local_last_date_after": "2026-08-12",
        }],
    }

    sentinel_manager = object()
    constructed = {}

    def fake_manager(*args, **kwargs):
        constructed["args"] = args
        constructed["kwargs"] = kwargs
        return sentinel_manager

    def fake_materialize(manager, **kwargs):
        assert manager is sentinel_manager
        return canonical_dir, {
            "Legacy_Adapter": "LegacyHistoricalProviderAdapter",
            "Manifest_Path": str(manifest_dir / "CANONICAL_DAILY_HISTORY_RUN.json"),
            "Accepted_Symbols": ["BBCA"],
            "Rejected_Symbols": {},
            "Canonical_Coverage_Ratio": 1.0,
        }

    monkeypatch.setattr(runner, "DataSourceManager", fake_manager)
    monkeypatch.setattr(runner, "materialize_legacy_daily_history", fake_materialize)

    result_dir, audit = runner.build_validated_input(raw_dir, manifest, "RUN", manifest_dir)

    assert result_dir == canonical_dir
    assert result_dir != raw_dir
    assert audit["Canonical_Boundary"] == "DataSourceManager.route"
    assert audit["Engine_Input_Is_Raw_Provider_Directory"] is False
    assert audit["Link_Mode_Counts"]["hardlink"] == 0
    assert constructed["kwargs"]["file_roots"]["historical"] == raw_dir


def test_pipeline_routes_technical_stage_through_canonical_wrapper():
    payload = json.loads(Path("config/pipeline.json").read_text(encoding="utf-8"))
    assert payload["paths"]["technical_feature_engine"] == (
        "modules/technical_feature_engine/post_market_validated_runner.py"
    )

    source = Path("modules/technical_feature_engine/post_market_validated_runner.py").read_text(
        encoding="utf-8"
    )
    assert "DataSourceManager(" in source
    assert "materialize_legacy_daily_history(" in source
    assert "Engine_Input_Is_Raw_Provider_Directory" in source
