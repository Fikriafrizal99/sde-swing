from __future__ import annotations

import json
from pathlib import Path

from modules.ai_interpretation.watchlist.service import WatchlistAIService, build_watchlist_context


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload


class FailoverSession:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def post(self, url, **kwargs):
        self.urls.append(str(url))
        if "api.openai.com" in str(url):
            return FakeResponse(429, {"error": "quota"}, "quota")
        if "generativelanguage.googleapis.com" in str(url):
            content = json.dumps({
                "analysis": (
                    "Menurut saya setup masih layak dipantau karena data teknikal dan broker yang diberikan "
                    "belum menunjukkan kerusakan, tetapi eksekusi tetap perlu mengikuti plan resmi SDE."
                ),
                "conclusion": "Saya memilih menunggu konfirmasi sesuai plan SDE.",
            })
            return FakeResponse(200, {
                "candidates": [{"content": {"parts": [{"text": content}]}}]
            })
        raise AssertionError(f"unexpected URL: {url}")


class AlwaysFailSession:
    def post(self, url, **kwargs):
        return FakeResponse(503, {"error": "down"}, "down")


def _config(tmp_path: Path) -> dict:
    return {
        "watchlist_ai": {
            "enabled": True,
            "max_symbols": 5,
            "cache_enabled": False,
            "cache_root": str(tmp_path / "cache"),
            "output_root": str(tmp_path / "output"),
            "providers": [
                {
                    "provider": "OPENAI",
                    "model": "test-openai",
                    "api_key_env": "OPENAI_API_KEY",
                    "priority": 1,
                    "vision": False,
                },
                {
                    "provider": "GEMINI",
                    "model": "test-gemini",
                    "api_key_env": "GEMINI_API_KEY",
                    "priority": 2,
                    "vision": False,
                },
                {
                    "provider": "GROQ",
                    "model": "test-groq",
                    "api_key_env": "GROQ_API_KEY",
                    "priority": 3,
                    "vision": False,
                },
            ],
        }
    }


def _context() -> dict:
    return build_watchlist_context({
        "trade_date": "2026-08-21",
        "symbol": "BBCA",
        "decision": "WATCH",
        "confidence": "74",
        "last_price": "8975",
        "entry_low": "8900",
        "entry_high": "9000",
        "stop_loss": "8650",
        "target_1": "9500",
        "target_2": "9900",
        "broker_status": "ACCUMULATION",
    })


def test_provider_failover_stops_after_second_provider_success(tmp_path):
    service = WatchlistAIService(
        _config(tmp_path),
        environ={
            "OPENAI_API_KEY": "openai-test",
            "GEMINI_API_KEY": "gemini-test",
            "GROQ_API_KEY": "groq-test",
        },
        session=FailoverSession(),
    )
    result = service.interpret(_context())

    assert result.status == "FALLBACK_SUCCESS"
    assert result.provider == "GEMINI"
    assert [item.status for item in result.attempts] == ["FAILED", "SUCCESS"]
    assert Path(result.artifact_path).exists()


def test_all_provider_failures_are_returned_not_raised(tmp_path):
    service = WatchlistAIService(
        _config(tmp_path),
        environ={
            "OPENAI_API_KEY": "openai-test",
            "GEMINI_API_KEY": "gemini-test",
            "GROQ_API_KEY": "groq-test",
        },
        session=AlwaysFailSession(),
    )
    result = service.interpret(_context())

    assert result.status == "ALL_PROVIDERS_FAILED"
    assert len(result.attempts) == 3
    assert all(item.status == "FAILED" for item in result.attempts)
    assert Path(result.artifact_path).exists()


def test_context_contains_no_company_name_and_chart_is_visual_only(tmp_path):
    chart = tmp_path / "BBCA_setup.png"
    chart.write_bytes(b"not-a-real-image")
    context = build_watchlist_context({
        "trade_date": "2026-08-21",
        "symbol": "BBCA",
        "decision": "WATCH",
        "company_name": "PT Bank Central Asia Tbk",
        "entry_low": "8900",
    }, chart_path=chart)

    assert context["identity"] == {"symbol": "BBCA", "trade_date": "2026-08-21"}
    assert "company_name" not in context["facts"]
    assert context["chart"]["role"] == "visual_context_only"
    assert context["chart"]["numeric_authority"] == "structured_sde_facts"
