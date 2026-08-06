from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from modules.candidate_selector.technical_candidate_selector import apply_exchange_status_filter
from modules.data_sources.base import SourceTimeout, Transport, TransportResponse
from modules.data_sources.config import SourceConfig
from modules.data_sources.zapi_idx_adapter import ZapiIdxClient
from modules.decision.exchange_status import apply_exchange_status_to_decisions
from modules.market_data.zapi_enrichment import ZapiEnrichmentService


class EnrichmentTransport(Transport):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[dict] = []

    def request(self, method: str, path: str, *, params=None, headers=None, timeout=None) -> TransportResponse:
        self.requests.append({"method": method, "path": path, "params": dict(params or {})})
        if self.fail:
            raise SourceTimeout("fixture timeout")
        params = params or {}
        if path == "/companies":
            payload = {"data": [
                {"KodeEmiten": "BBCA", "NamaEmiten": "Bank Central", "Sektor": "Keuangan", "SubSektor": "Bank", "Status": "ACTIVE"},
                {"KodeEmiten": "BMRI", "NamaEmiten": "Bank Mandiri", "Sektor": "Keuangan", "SubSektor": "Bank", "Status": "ACTIVE"},
            ], "recordsTotal": 2, "start": 0, "length": 5000}
        elif path == "/securities":
            payload = {"data": [
                {"Code": "BBCA", "Name": "Bank Central", "ListingBoard": "Utama", "ListingDate": "2000-01-01", "Shares": 1_000_000},
                {"Code": "BMRI", "Name": "Bank Mandiri", "ListingBoard": "Utama", "ListingDate": "2003-01-01", "Shares": 2_000_000},
            ], "recordsTotal": 2, "start": 0, "length": 5000}
        elif path == "/market-activity":
            activity_type = str(params.get("type", "")).lower()
            code = "BBCA" if activity_type == "suspend" else "BMRI"
            payload = {"data": {"Results": [{
                "CompanyID": code,
                "CompanyName": f"{code} Tbk",
                "Judul": f"{activity_type.upper()} {code}",
                "UMADate": "2026-08-06",
            }]}}
        else:
            raise AssertionError(f"unexpected endpoint: {path}")
        return TransportResponse(status_code=200, payload=payload)


def _service(tmp_path: Path, transport: EnrichmentTransport) -> tuple[ZapiEnrichmentService, ZapiIdxClient]:
    cfg = SourceConfig(
        name="ZAPI_IDX",
        enabled=True,
        retry=2,
        documentation_configured=True,
        api_key_env="ZAPI_TEST_KEY",
        base_url_env="ZAPI_TEST_URL",
        max_requests_per_process=5,
    )
    client = ZapiIdxClient(transport, cfg, explicit_mock=True, max_requests_per_process=5)
    return ZapiEnrichmentService(cache_root=tmp_path / "zapi", client=client), client


def test_enrichment_uses_only_metadata_and_activity_with_persistent_cache(tmp_path: Path) -> None:
    transport = EnrichmentTransport()
    service, client = _service(tmp_path, transport)
    result = service.enrich(["BBCA.JK", "BMRI"], trade_date=date(2026, 8, 6), historical_dir=tmp_path)

    assert result["status"] == "SUCCESS"
    assert result["request_count"] == 5
    assert len(transport.requests) == 5
    assert all(request["path"] != "/stock-summary" for request in transport.requests)
    assert {request["path"] for request in transport.requests} == {"/companies", "/securities", "/market-activity"}
    assert result["suspended_symbols"] == ["BBCA"]
    assert result["uma_symbols"] == ["BMRI"]
    assert result["relisting_symbols"] == ["BMRI"]
    assert (tmp_path / "zapi" / "metadata_cache.json").exists()
    assert (tmp_path / "zapi" / "market_activity_cache.json").exists()

    cached = service.enrich(["BBCA", "BMRI"], trade_date=date(2026, 8, 6), historical_dir=tmp_path)
    assert cached["metadata_cache_status"] == "HIT"
    assert cached["market_activity_cache_status"] == "HIT"
    assert client.request_attempt_count == 5
    assert len(transport.requests) == 5

    expired = service.enrich(["BBCA"], trade_date=date(2026, 8, 13), historical_dir=tmp_path)
    assert expired["metadata_cache_status"] in {"REFRESHED", "STALE_FALLBACK", "UNAVAILABLE"}


def test_enrichment_degrades_without_stopping_yahoo_path_and_honors_hard_cap(tmp_path: Path) -> None:
    transport = EnrichmentTransport(fail=True)
    service, client = _service(tmp_path, transport)
    result = service.enrich(["BBCA"], trade_date=date(2026, 8, 6), historical_dir=tmp_path)

    assert result["status"] == "DEGRADED"
    assert result["degraded"] is True
    assert result["request_cap"] == 5
    assert result["request_count"] == 5
    assert client.request_cap_reached is True
    assert len(transport.requests) == 5
    assert result["symbols"]["BBCA"]["status"] == "NORMAL"


def test_exchange_flags_filter_candidates_and_veto_final_decisions(tmp_path: Path) -> None:
    states = {
        "BBCA": {"status": "SUSPENDED", "risk_flags": [], "veto": "SUSPENDED", "history_candle_count": 400},
        "BMRI": {"status": "NORMAL", "risk_flags": ["UMA"], "veto": "", "history_candle_count": 400},
        "TLKM": {"status": "NORMAL", "risk_flags": ["RELISTING"], "veto": "RELISTING_HISTORY_INSUFFICIENT", "history_candle_count": 20},
    }
    ranking = pd.DataFrame([
        {"Symbol": "BBCA", "Candidate_Status": "PASS", "Technical_Score": 90},
        {"Symbol": "BMRI", "Candidate_Status": "PASS", "Technical_Score": 88},
        {"Symbol": "TLKM", "Candidate_Status": "PASS", "Technical_Score": 87},
    ])
    filtered = apply_exchange_status_filter(ranking, states)
    assert set(filtered.loc[filtered["Candidate_Status"] == "FILTERED", "Symbol"]) == {"BBCA", "TLKM"}
    assert filtered.loc[filtered["Symbol"] == "BMRI", "Risk_Flags"].iloc[0] == "UMA"
    assert filtered.loc[filtered["Symbol"] == "BMRI", "Technical_Score"].iloc[0] == 88

    decision_path = tmp_path / "FINAL_DECISION_V3.csv"
    pd.DataFrame([
        {"Symbol": "BBCA", "Decision_Status_Final": "BUY READY"},
        {"Symbol": "BMRI", "Decision_Status_Final": "BUY READY"},
        {"Symbol": "TLKM", "Decision_Status_Final": "WATCH"},
    ]).to_csv(decision_path, index=False)
    enrichment_path = tmp_path / "enrichment.json"
    enrichment_path.write_text(json.dumps({"symbols": states}), encoding="utf-8")
    result = apply_exchange_status_to_decisions(decision_path, enrichment_path)
    assert result["suspended_count"] == 1
    assert result["uma_count"] == 1
    decisions = pd.read_csv(decision_path)
    assert decisions.loc[decisions["Symbol"] == "BBCA", "Decision_Status_Final"].iloc[0] == "BLOCKED"
    assert decisions.loc[decisions["Symbol"] == "BMRI", "Decision_Status_Final"].iloc[0] == "BUY CANDIDATE"
    assert decisions.loc[decisions["Symbol"] == "TLKM", "Decision_Status_Final"].iloc[0] == "BLOCKED"
