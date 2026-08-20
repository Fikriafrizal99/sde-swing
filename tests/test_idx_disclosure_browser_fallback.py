from datetime import date

from modules.idx_disclosure.browser_client import ResilientAnnouncementSource
from modules.idx_disclosure.client import AnnouncementPage, IDXClientError


class FailingPrimary:
    transport_name = "curl_cffi"

    def __init__(self):
        self.calls = 0

    def fetch_page(self, **kwargs):
        self.calls += 1
        raise IDXClientError("IDX HTTP 403")


class WorkingFallback:
    transport_name = "playwright:chrome:headed"

    def __init__(self):
        self.calls = 0

    def fetch_page(self, *, trade_date, index_from=0, page_size=50):
        self.calls += 1
        return AnnouncementPage(0, (), index_from, page_size)


def test_resilient_source_switches_once_and_stays_on_browser():
    primary = FailingPrimary()
    fallback = WorkingFallback()
    source = ResilientAnnouncementSource(primary, fallback)

    source.fetch_page(trade_date=date(2026, 8, 20))
    source.fetch_page(trade_date=date(2026, 8, 20))

    assert primary.calls == 1
    assert fallback.calls == 2
    assert source.transport_name == "playwright:chrome:headed"
    assert "403" in source.primary_error
