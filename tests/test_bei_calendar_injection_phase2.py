from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from modules.data_sources import constants as C
from modules.data_sources.canonical import DailyBar, SymbolMetadata
from modules.data_sources.config import DataSourceConfig, RecordOwnership, SourceConfig
from modules.data_sources.data_quality import DataQualityEngine
from modules.runtime.data_source_manager import DataSourceManager


FIXED_NOW = datetime(2026, 1, 5, 17, 0, tzinfo=C.WIB)


def _bar(market_date: str) -> DailyBar:
    return DailyBar(
        symbol="BBCA",
        market_date=market_date,
        event_timestamp=f"{market_date}T16:15:00+07:00",
        received_at=f"{market_date}T16:16:00+07:00",
        source="HISTORICAL_PROVIDER",
        open=1000.0,
        high=1050.0,
        low=990.0,
        close=1030.0,
        volume=5_000_000.0,
        is_closed=True,
    )


def _manager(tmp_path: Path) -> DataSourceManager:
    calendar_path = tmp_path / "config" / "trading_calendar.json"
    calendar_path.parent.mkdir(parents=True)
    calendar_path.write_text(
        json.dumps(
            {
                "holidays": {"2026-01-01": {"holiday": True}},
                "special_trading_days": ["2026-01-03"],
            }
        ),
        encoding="utf-8",
    )
    source = SourceConfig(
        name="HISTORICAL_PROVIDER",
        enabled=True,
        maximum_stale_seconds=0,
    )
    config = DataSourceConfig(
        sources={source.name: source},
        ownership={
            "DailyBar": RecordOwnership(
                record_type="DailyBar",
                primary=source.name,
            )
        },
    )
    return DataSourceManager(config=config, root=tmp_path)


@pytest.mark.parametrize(
    ("market_date", "accepted", "reason_expected"),
    [
        ("2026-01-02", True, False),   # normal weekday
        ("2026-01-01", False, True),  # configured BEI holiday
        ("2026-01-04", False, True),  # ordinary weekend
        ("2026-01-03", True, False),  # configured special Saturday session
    ],
)
def test_manager_injects_bei_calendar_into_session_validation(
    tmp_path: Path,
    market_date: str,
    accepted: bool,
    reason_expected: bool,
) -> None:
    manager = _manager(tmp_path)
    bar = _bar(market_date)
    result = manager.route(
        "DailyBar",
        "BBCA",
        market_date,
        candidates={"HISTORICAL_PROVIDER": bar},
        expected_market_date=market_date,
    )

    assert (result.record is not None and bool(result.quality and result.quality.accepted)) is accepted
    assert (C.NON_TRADING_DAY in bar.quality_reasons) is reason_expected
    assert manager.calendar_path == tmp_path / "config" / "trading_calendar.json"


@pytest.mark.parametrize("market_date", ["2026-01-01", "2026-01-04"])
def test_non_market_metadata_is_not_rejected_as_non_trading_day(market_date: str) -> None:
    engine = DataQualityEngine(
        holidays={"2026-01-01": {"holiday": True}},
        special_trading_days=[],
    )
    metadata = SymbolMetadata(
        symbol="BBCA",
        market_date=market_date,
        event_timestamp=f"{market_date}T09:00:00+07:00",
        received_at=f"{market_date}T09:01:00+07:00",
        source="ZAPI_IDX",
        name="Bank Central Asia Tbk",
    )

    result = engine.validate(metadata, expected_market_date=market_date, at=FIXED_NOW)

    assert result.accepted
    assert C.NON_TRADING_DAY not in result.reasons


def test_daily_bar_for_expected_closed_session_fails_closed(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    bar = _bar("2026-01-01")

    result = manager.route(
        "DailyBar",
        "BBCA",
        "2026-01-01",
        candidates={"HISTORICAL_PROVIDER": bar},
        expected_market_date="2026-01-01",
    )

    assert result.record is None
    assert C.NON_TRADING_DAY in bar.quality_reasons
    assert C.WRONG_MARKET_DATE not in bar.quality_reasons
