from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from modules.data_sources.base import Transport, TransportResponse
from modules.data_sources.config import SourceConfig
from modules.data_sources.zapi_idx_adapter import ZapiIdxClient
from modules.market_data.zapi_sector_metadata import refresh_sector_metadata


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
        else:
            securities = [{"Code": row["KodeEmiten"], "Name": row["KodeEmiten"]} for row in companies]
            payload = {"data": securities, "recordsTotal": 2, "recordsFiltered": 2}
        return TransportResponse(status_code=200, payload=payload)


def test_refresh_sector_metadata_paginates_companies_and_writes_cache(tmp_path: Path):
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

    result = refresh_sector_metadata(
        output,
        config_path=cfg,
        trade_date=date(2026, 8, 4),
        client=client,
        page_size=1,
        event_callback=lambda event, detail: events.append(event),
    )

    assert result["status"] == "SUCCESS"
    assert result["records_written"] == 2
    assert result["request_count"] == 2  # companies only; securities are not needed for sectors
    assert [path for path, _ in transport.requests] == ["/companies", "/companies"]
    assert "ZAPI_SECTOR_METADATA_COMPLETE" in events
    content = output.read_text(encoding="utf-8-sig")
    assert "BBCA,Keuangan,Bank" in content
    assert "BBRI,Keuangan,Bank" in content


def test_missing_sector_metadata_credentials_is_immediate_and_does_not_write(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TEST_ZAPI_META_KEY", raising=False)
    monkeypatch.delenv("TEST_ZAPI_META_URL", raising=False)
    cfg = tmp_path / "sources.json"
    _config(cfg)
    output = tmp_path / "sector_metadata.csv"

    result = refresh_sector_metadata(
        output,
        config_path=cfg,
        trade_date=date(2026, 8, 4),
    )

    assert result["status"] == "NOT_CONFIGURED"
    assert result["request_count"] == 0
    assert not output.exists()
