from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from modules.data_sources.base import Transport, TransportResponse
from modules.data_sources.config import SourceConfig
from modules.data_sources.zapi_idx_adapter import ZapiIdxClient
from modules.market_data.zapi_enrichment import ZapiEnrichmentService


def _config(path: Path) -> None:
    path.write_text(json.dumps({
        "sources": {
            "ZAPI_IDX": {
                "enabled": True,
                "documentation_configured": True,
                "api_key_env": "TEST_ZAPI_META_KEY",
                "base_url_env": "TEST_ZAPI_META_URL",
            },
        },
    }), encoding="utf-8")


class MetadataTransport(Transport):
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []

    def request(self, method, path, *, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.requests.append((path, params))
        start = int(params.get("start", 0))
        length = int(params.get("length", 100))
        companies = [
            {"KodeEmiten": "BBCA", "Sektor": "Keuangan", "SubSektor": "Bank"},
            {"KodeEmiten": "BBRI", "Sektor": "Keuangan", "SubSektor": "Bank"},
        ][start:start + length]
        if path == "/companies":
            payload = {"data": companies, "recordsTotal": 2, "recordsFiltered": 2}
        elif path == "/securities":
            securities = [{"Code": row["KodeEmiten"], "Name": row["KodeEmiten"]} for row in companies]
            payload = {"data": securities, "recordsTotal": 2, "recordsFiltered": 2}
        elif path == "/market-activity":
            activity_type = str(params.get("type", "")).lower()
            code = "BBCA" if activity_type == "suspend" else "BBRI"
            payload = {
                "data": {"Results": [{
                    "CompanyID": code,
                    "CompanyName": f"{code} Tbk",
                    "Judul": f"{activity_type.upper()} {code}",
                    "UMADate": "2026-08-06",
                }]},
                "type": activity_type,
                "dataset": "market-activity",
                "provider": "idx",
            }
        return TransportResponse(status_code=200, payload=payload)


def test_enrichment_paginates_current_metadata_and_writes_cache(tmp_path: Path):
    cfg = tmp_path / "sources.json"
    _config(cfg)
    transport = MetadataTransport()
    client = ZapiIdxClient(
        transport,
        SourceConfig(name="ZAPI_IDX", enabled=True, documentation_configured=True, retry=0),
        explicit_mock=True,
    )
    events: list[str] = []
    output = tmp_path / "sector_metadata.csv"

    service = ZapiEnrichmentService(
        config_path=cfg,
        cache_root=tmp_path / "zapi",
        client=client,
        event_callback=lambda event, detail: events.append(event),
    )
    result = service.enrich(
        ["BBCA", "BBRI"],
        trade_date=date(2026, 8, 4),
        historical_dir=tmp_path,
        metadata_csv_path=output,
    )

    assert result["status"] == "SUCCESS"
    assert result["metadata_record_count"] == 2
    assert result["request_count"] == 5
    assert {path for path, _ in transport.requests} == {"/companies", "/securities", "/market-activity"}
    assert "ZAPI_ENRICHMENT_COMPLETE" in events
    content = output.read_text(encoding="utf-8-sig")
    assert "BBCA" in content and "Keuangan" in content and "Bank" in content
    assert "BBRI" in content


def test_missing_sector_metadata_credentials_is_immediate_and_does_not_write(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TEST_ZAPI_META_KEY", raising=False)
    monkeypatch.delenv("TEST_ZAPI_META_URL", raising=False)
    cfg = tmp_path / "sources.json"
    _config(cfg)
    output = tmp_path / "sector_metadata.csv"

    service = ZapiEnrichmentService(
        config_path=cfg,
        cache_root=tmp_path / "zapi",
    )
    result = service.enrich(["BBCA"], trade_date=date(2026, 8, 4), metadata_csv_path=output)

    assert result["status"] == "DEGRADED"
    assert result["request_count"] == 0
    assert not output.exists()
