from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from modules.ai_interpretation.gemini_interpreter import GeminiInterpreter, IMMUTABLE_FIELDS
from modules.data_sources.base import Transport, TransportResponse
from modules.data_sources.config import SourceConfig
from modules.data_sources.yahoo_zapi_validator import validate_yahoo_against_zapi
from modules.data_sources.zapi_idx_adapter import (
    MockZapiTransport,
    ZapiIdxClient,
    canonical_symbol,
    provider_symbol,
)
from modules.telegram.daily_report_ui import format_post_market, format_watchlist_detail


def _client() -> ZapiIdxClient:
    config = SourceConfig(
        name="ZAPI_IDX",
        enabled=True,
        documentation_configured=True,
        retry=0,
        timeout=1,
    )
    return ZapiIdxClient(MockZapiTransport(), config)


def _config(path: Path, *, enabled: bool = True) -> None:
    path.write_text(json.dumps({
        "config_version": "test",
        "resolver_mode": "PRIMARY_WITH_FALLBACK",
        "record_ownership": {"DailyBar": {"primary": "HISTORICAL_PROVIDER"}},
        "sources": {
            "HISTORICAL_PROVIDER": {"enabled": True},
            "ZAPI_IDX": {
                "enabled": enabled,
                "documentation_configured": True,
                "api_key_env": "TEST_ZAPI_KEY_NOT_SET",
                "base_url_env": "TEST_ZAPI_URL_NOT_SET",
            },
        },
    }), encoding="utf-8")


def _yahoo(path: Path, close: float = 1030.0, trade_date: str = "2026-01-02") -> None:
    pd.DataFrame([{
        "Date": trade_date,
        "Open": 1000.0,
        "High": 1050.0,
        "Low": 990.0,
        "Close": close,
        "Volume": 5_000_000.0,
    }]).to_csv(path, index=False)


def test_symbol_normalization_and_provider_mapping():
    assert canonical_symbol(" BBCA.JK ") == "BBCA"
    assert canonical_symbol("idx:bbca") == "BBCA"
    assert canonical_symbol("^JKSE") == "IHSG"
    assert provider_symbol("IHSG", record_type="MarketIndex") == "COMPOSITE"


def test_raw_fixture_to_reconciliation_lineage_and_report(tmp_path: Path):
    historical = tmp_path / "history"
    output = tmp_path / "out"
    historical.mkdir()
    _yahoo(historical / "BBCA.JK.csv")
    cfg = tmp_path / "sources.json"
    _config(cfg)
    events: list[tuple[str, dict]] = []

    result = validate_yahoo_against_zapi(
        historical_dir=historical,
        symbols=["IDX:BBCA", "BBCA.JK", "BBCA"],
        market_date="2026-01-02",
        output_dir=output,
        config_path=cfg,
        blocking=True,
        minimum_coverage_ratio=1.0,
        run_id="RUN-E2E",
        client=_client(),
        event_callback=lambda event, detail: events.append((event, detail)),
    )

    assert result["status"] == "SUCCESS"
    assert result["symbols_requested"] == 1
    # One unfiltered smoke request plus one paginated bulk request; there is
    # no per-symbol HTTP request for this one-symbol universe.
    assert result["request_count"] == 2
    assert result["batch_mode"] == "BULK_PAGINATED"
    assert result["reconciliation_counts"]["MATCH"] == 1
    assert result["rows"][0]["selected_source"] == "YAHOO"
    assert result["rows"][0]["enrichment_source"] == "ZAPI_IDX"
    assert Path(result["json_path"]).exists()
    assert Path(result["audit_path"]).exists()
    event_names = [event for event, _ in events]
    assert event_names == [
        "ZAPI_CREDENTIAL_STATUS",
        "ZAPI_SMOKE_TEST_START",
        "ZAPI_DATE_RESOLUTION",
        "ZAPI_SMOKE_TEST_SUCCESS",
        "ZAPI_BATCH_START",
        "ZAPI_BULK_START",
        "ZAPI_BULK_PROGRESS",
        "ZAPI_BATCH_PROGRESS",
        "ZAPI_RECONCILIATION_COMPLETE",
    ]
    assert events[7][1]["processed"] == 1
    assert events[7][1]["total"] == 1

    message = format_post_market({
        "process_status": "SUCCESS", "symbols_requested": 1, "symbols_loaded": 1,
        "symbols_valid": 1, "symbols_failed": 0, "symbols_skipped": 0,
        "coverage": 100, "historical_status": "VALID", "zapi_status": result["status"],
        "zapi_coverage": result["coverage_ratio"], "stockbit_status": "WAITING",
    })
    assert "Yahoo: VALID" in message
    assert "ZAPI IDX: SUCCESS" in message


def test_reconciliation_tolerance_mismatch_and_stale(tmp_path: Path):
    cfg = tmp_path / "sources.json"
    _config(cfg)
    historical = tmp_path / "history"
    historical.mkdir()
    _yahoo(historical / "BBCA.csv", close=1031.0)
    tolerant = validate_yahoo_against_zapi(
        historical_dir=historical, symbols=["BBCA"], market_date="2026-01-02",
        output_dir=tmp_path / "tolerant", config_path=cfg, client=_client(),
        price_tolerance_pct=0.005, run_id="TOL",
    )
    assert tolerant["rows"][0]["status"] == "MATCH_WITH_TOLERANCE"

    _yahoo(historical / "BBCA.csv", close=1100.0)
    mismatch = validate_yahoo_against_zapi(
        historical_dir=historical, symbols=["BBCA"], market_date="2026-01-02",
        output_dir=tmp_path / "mismatch", config_path=cfg, client=_client(),
        price_tolerance_pct=0.005, blocking=True, run_id="BAD",
    )
    assert mismatch["rows"][0]["status"] == "PRICE_MISMATCH"
    assert mismatch["status"] == "FAILED_BLOCKING"

    _yahoo(historical / "BBCA.csv", trade_date="2026-01-01")
    stale = validate_yahoo_against_zapi(
        historical_dir=historical, symbols=["BBCA"], market_date="2026-01-02",
        output_dir=tmp_path / "stale", config_path=cfg, client=_client(), run_id="STALE",
    )
    assert stale["rows"][0]["status"] == "STALE_YAHOO"


def test_date_resolution_uses_unfiltered_probe_and_one_source_date(tmp_path: Path):
    live_payload = json.loads(
        (Path(__file__).resolve().parents[1] / "tests/fixtures/zapi_idx/stock_summary_live_20260803.json")
        .read_text(encoding="utf-8")
    )

    class DateResolutionTransport(Transport):
        def __init__(self):
            self.requests: list[dict] = []

        def request(self, method, path, *, params=None, headers=None, timeout=None):
            current = dict(params or {})
            self.requests.append(current)
            if current.get("date") == "20260804":
                payload = {"data": {"data": [], "recordsTotal": 0, "recordsFiltered": 0}}
            else:
                payload = live_payload
            return TransportResponse(status_code=200, payload=payload)

    historical = tmp_path / "history"
    historical.mkdir()
    pd.DataFrame([{
        "Date": "2026-08-03", "Open": 9300, "High": 9300, "Low": 9075,
        "Close": 9100, "Volume": 4529500,
    }]).to_csv(historical / "AADI.csv", index=False)
    cfg = tmp_path / "sources.json"
    _config(cfg)
    transport = DateResolutionTransport()
    client = ZapiIdxClient(transport, SourceConfig(
        name="ZAPI_IDX", enabled=True, documentation_configured=True, retry=0,
    ), explicit_mock=True)
    events: list[tuple[str, dict]] = []
    result = validate_yahoo_against_zapi(
        historical_dir=historical,
        symbols=["AADI"],
        market_date="2026-08-04",
        output_dir=tmp_path / "out",
        config_path=cfg,
        client=client,
        blocking=True,
        run_id="DATE-RESOLUTION",
        event_callback=lambda event, detail: events.append((event, detail)),
    )

    assert result["status"] == "SUCCESS"
    assert result["requested_date"] == "20260804"
    assert result["resolved_source_date"] == "20260803"
    assert result["date_fallback_reason"] == "CURRENT_DATASET_EMPTY"
    assert result["records_total"] == 963
    assert transport.requests == [
        {"length": 1, "start": 0, "date": "20260804"},
        {"length": 1, "start": 0, "date": "20260803"},
        {"length": 100, "start": 0, "date": "20260803"},
    ]
    date_event = next(detail for event, detail in events if event == "ZAPI_DATE_RESOLUTION")
    assert date_event == {
        "requested_date": "20260804",
        "resolved_source_date": "20260803",
        "fallback_reason": "CURRENT_DATASET_EMPTY",
    }
    smoke = next(detail for event, detail in events if event == "ZAPI_SMOKE_TEST_SUCCESS")
    assert smoke["symbol"] == "AADI"
    assert smoke["recordsTotal"] == 963
    assert events[-1][0] == "ZAPI_RECONCILIATION_COMPLETE"
    assert events[-1][1]["status"] == "SUCCESS"


def test_bulk_pagination_avoids_per_symbol_requests_and_uses_one_source_date(tmp_path: Path):
    live_payload = json.loads(
        (Path(__file__).resolve().parents[1] / "tests/fixtures/zapi_idx/stock_summary_live_20260803.json")
        .read_text(encoding="utf-8")
    )
    first = dict(live_payload["data"]["data"][0])
    second = {**first, "StockCode": "BBCA"}
    rows = [first, second]

    class BulkFixtureTransport(Transport):
        def __init__(self):
            self.requests: list[dict] = []

        def request(self, method, path, *, params=None, headers=None, timeout=None):
            current = dict(params or {})
            self.requests.append(current)
            start = int(current.get("start", 0))
            length = int(current.get("length", 1))
            payload_rows = rows[start:start + length]
            return TransportResponse(status_code=200, payload={
                "data": {
                    "data": payload_rows,
                    "recordsTotal": len(rows),
                    "recordsFiltered": len(rows),
                },
            })

    historical = tmp_path / "history"
    historical.mkdir()
    for symbol in ("AADI", "BBCA"):
        pd.DataFrame([{
            "Date": "2026-08-03", "Open": 9300, "High": 9300, "Low": 9075,
            "Close": 9100, "Volume": 4529500,
        }]).to_csv(historical / f"{symbol}.csv", index=False)
    cfg = tmp_path / "sources.json"
    _config(cfg)
    transport = BulkFixtureTransport()
    client = ZapiIdxClient(transport, SourceConfig(
        name="ZAPI_IDX", enabled=True, documentation_configured=True, retry=0,
    ), explicit_mock=True)

    result = validate_yahoo_against_zapi(
        historical_dir=historical,
        symbols=["AADI", "BBCA"],
        market_date="2026-08-03",
        output_dir=tmp_path / "out",
        config_path=cfg,
        client=client,
        blocking=True,
        minimum_coverage_ratio=1.0,
        bulk_page_size=1,
        run_id="BULK-PAGINATION",
    )

    assert result["status"] == "SUCCESS"
    assert result["batch_mode"] == "BULK_PAGINATED"
    assert result["bulk_symbols_indexed"] == 2
    assert result["request_count"] == 3  # smoke + two pages, not two symbols
    assert transport.requests == [
        {"length": 1, "start": 0, "date": "20260803"},
        {"length": 1, "start": 0, "date": "20260803"},
        {"length": 1, "start": 1, "date": "20260803"},
    ]


def test_bulk_failure_falls_back_to_per_symbol_requests(tmp_path: Path):
    from modules.data_sources.base import SourceUnavailable

    row = {
        "StockCode": "AADI", "Date": "2026-08-03T00:00:00", "Previous": 9225,
        "OpenPrice": 9300, "High": 9300, "Low": 9075, "Close": 9100,
        "Volume": 4529500, "Value": 41430862500, "Frequency": 3273,
        "ForeignBuy": 503600, "ForeignSell": 2396100,
    }

    class BulkFailureTransport(Transport):
        def __init__(self):
            self.requests: list[dict] = []

        def request(self, method, path, *, params=None, headers=None, timeout=None):
            current = dict(params or {})
            self.requests.append(current)
            if "code" not in current:
                if current.get("length") == 2:
                    raise SourceUnavailable("bulk endpoint unavailable")
                payload_rows = [row]
            else:
                payload_rows = [{**row, "StockCode": current["code"]}]
            return TransportResponse(status_code=200, payload={
                "data": {
                    "data": payload_rows,
                    "recordsTotal": 2,
                    "recordsFiltered": 2,
                },
            })

    historical = tmp_path / "history"
    historical.mkdir()
    for symbol in ("AADI", "BBCA"):
        pd.DataFrame([{
            "Date": "2026-08-03", "Open": 9300, "High": 9300, "Low": 9075,
            "Close": 9100, "Volume": 4529500,
        }]).to_csv(historical / f"{symbol}.csv", index=False)
    cfg = tmp_path / "sources.json"
    _config(cfg)
    transport = BulkFailureTransport()
    client = ZapiIdxClient(transport, SourceConfig(
        name="ZAPI_IDX", enabled=True, documentation_configured=True, retry=0,
    ), explicit_mock=True)
    events: list[tuple[str, dict]] = []

    result = validate_yahoo_against_zapi(
        historical_dir=historical,
        symbols=["AADI", "BBCA"],
        market_date="2026-08-03",
        output_dir=tmp_path / "out",
        config_path=cfg,
        client=client,
        blocking=True,
        minimum_coverage_ratio=1.0,
        bulk_page_size=2,
        run_id="BULK-FALLBACK",
        event_callback=lambda event, detail: events.append((event, detail)),
    )

    assert result["status"] == "SUCCESS"
    assert result["batch_mode"] == "PER_SYMBOL_FALLBACK"
    assert result["request_count"] == 3  # smoke + failed bulk + BBCA fallback
    assert "ZAPI_BULK_FAILED" in [event for event, _ in events]
    assert "ZAPI_BATCH_FALLBACK_PER_SYMBOL" in [event for event, _ in events]
    assert transport.requests == [
        {"length": 1, "start": 0, "date": "20260803"},
        {"length": 2, "start": 0, "date": "20260803"},
        {"length": 10, "start": 0, "date": "20260803", "code": "BBCA"},
    ]


def test_missing_credentials_is_explicit_and_makes_no_request(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TEST_ZAPI_KEY_NOT_SET", raising=False)
    monkeypatch.delenv("TEST_ZAPI_URL_NOT_SET", raising=False)
    cfg = tmp_path / "sources.json"
    _config(cfg)
    result = validate_yahoo_against_zapi(
        historical_dir=tmp_path, symbols=["BBCA"], market_date="2026-01-02",
        output_dir=tmp_path / "out", config_path=cfg, run_id="NO-CREDS",
    )
    assert result["status"] == "ZAPI_MISSING_CREDENTIAL"
    assert result["reason"] == "ZAPI_MISSING_CREDENTIAL"
    assert result["request_count"] == 0


def test_missing_credentials_is_immediate_and_emits_no_request_smoke_failure(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TEST_ZAPI_KEY_NOT_SET", raising=False)
    monkeypatch.delenv("TEST_ZAPI_URL_NOT_SET", raising=False)
    cfg = tmp_path / "sources.json"
    _config(cfg)
    events: list[tuple[str, dict]] = []
    started = time.monotonic()
    result = validate_yahoo_against_zapi(
        historical_dir=tmp_path,
        symbols=["BBCA"],
        market_date="2026-01-02",
        output_dir=tmp_path / "out",
        config_path=cfg,
        run_id="NO-WAIT",
        blocking=True,
        event_callback=lambda event, detail: events.append((event, detail)),
    )
    assert time.monotonic() - started < 1.0
    assert result["request_count"] == 0
    assert result["blocking_failures"] == 1
    assert [event for event, _ in events] == [
        "ZAPI_CREDENTIAL_STATUS",
        "ZAPI_SMOKE_TEST_START",
        "ZAPI_SMOKE_TEST_FAILED",
        "ZAPI_RECONCILIATION_COMPLETE",
    ]
    assert events[2][1]["request_performed"] is False


def test_gemini_and_telegram_preserve_zapi_facts():
    expected = {"zapi_status", "reconciliation_status", "zapi_coverage", "degraded_reason"}
    assert expected <= IMMUTABLE_FIELDS
    safe = GeminiInterpreter(enabled=False)._sanitize_facts({
        "zapi_status": "MATCH", "reconciliation_status": "MATCH",
        "zapi_coverage": 1.0, "degraded_reason": "", "untrusted": "drop",
    })
    assert safe["zapi_status"] == "MATCH"
    assert "untrusted" not in safe
    message = format_watchlist_detail({
        "symbol": "BBCA", "decision": "WATCH", "coverage": 100,
        "yahoo_status": "VALID", "zapi_status": "MATCH", "broker_status": "AVAILABLE",
        "zapi_freshness_days": 0,
    })
    assert "ZAPI IDX: MATCH" in message
    assert "Stockbit: AVAILABLE" in message
