from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pandas as pd

from modules.database import swing_history_db as history


def _write_history(path: Path, symbol: str, close: float) -> None:
    pd.DataFrame(
        [
            {
                "Symbol": symbol,
                "Date": "2026-08-12",
                "Open": close - 1,
                "High": close + 1,
                "Low": close - 2,
                "Close": close,
                "Volume": 1_000_000,
            }
        ]
    ).to_csv(path, index=False)


def test_selective_price_map_reads_only_exact_normalized_filenames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_history(tmp_path / "BBCA.JK.csv", "BBCA", 100.0)
    _write_history(tmp_path / "BBRI.csv", "BBRI", 200.0)
    _write_history(tmp_path / "BBCA2.csv", "BBCA2", 300.0)

    loaded: list[str] = []
    original_load_csv = history._baseline.load_csv

    def tracked_load_csv(path: Path):
        loaded.append(Path(path).name)
        return original_load_csv(path)

    monkeypatch.setattr(history._baseline, "load_csv", tracked_load_csv)
    full = history.load_price_map(tmp_path)
    full_loaded = list(loaded)
    loaded.clear()
    selective = history.load_price_map(tmp_path, symbols=["BBCA.JK"])

    assert full_loaded == ["BBCA.JK.csv", "BBCA2.csv", "BBRI.csv"]
    assert loaded == ["BBCA.JK.csv"]
    assert set(selective) == {"BBCA"}
    pd.testing.assert_frame_equal(selective["BBCA"], full["BBCA"])


def test_archive_requests_only_new_and_continuing_symbols(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: list[set[str]] = []

    def selective_loader(_historical_dir: Path, symbols=None):
        captured.append(set(symbols or []))
        return {}

    monkeypatch.setattr(history, "load_price_map", selective_loader)
    watchlist = pd.DataFrame(
        [
            {
                "symbol": "BBCA",
                "signal_date": "2026-08-12",
                "lifecycle": "NEW",
                "decision": "BUY",
                "row_json": "{}",
            },
            {
                "symbol": "BBRI",
                "signal_date": "",
                "lifecycle": "REMOVED",
                "decision": "",
                "row_json": "{}",
            },
        ]
    )
    plans = tmp_path / "ENTRY_PLANS.csv"
    pd.DataFrame(columns=["Symbol"]).to_csv(plans, index=False)

    with closing(history.connect(tmp_path / "history.db")) as conn:
        history.init_schema(conn)
        assert history.archive_watchlist_outcomes(
            conn,
            "RUN-1",
            watchlist,
            tmp_path,
            plans,
            "VALID",
        ) == 1

    assert captured == [{"BBCA"}]
