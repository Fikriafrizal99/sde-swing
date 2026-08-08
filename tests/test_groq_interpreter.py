from __future__ import annotations

from pathlib import Path

from modules.ai_interpretation import GeminiInterpreter, GroqInterpreter
from modules.ai_interpretation.groq_interpreter import GroqHTTPError


def _fallback() -> dict[str, str]:
    return {
        "main_reason": "Alasan deterministic",
        "main_risk": "Risiko deterministic",
        "execution_note": "Ikuti trigger engine.",
    }


def _market_facts(trade_date: str = "2026-08-08") -> dict:
    return {
        "trade_date": trade_date,
        "market_regime": "STRONG BULLISH",
        "execution_mode": "SELECTIVE AGGRESSIVE",
        "ihsg_trend": "BULLISH",
        "breadth": "POSITIVE",
    }


def _watchlist_facts(rank: int) -> dict:
    return {
        "trade_date": "2026-08-08",
        "rank": rank,
        "symbol": f"S{rank}",
        "decision": "BUY_CANDIDATE",
        "setup": "BREAKOUT_RETEST",
        "market_regime": "STRONG BULLISH",
    }


def test_bare_legacy_import_now_defaults_to_groq() -> None:
    interpreter = GeminiInterpreter()
    assert isinstance(interpreter, GroqInterpreter)
    assert interpreter.model == "llama-3.3-70b-versatile"


def test_groq_without_key_uses_deterministic_fallback() -> None:
    interpreter = GroqInterpreter(api_key="", cache_enabled=False)
    result = interpreter.interpret(_market_facts(), _fallback())
    assert result.source == "DETERMINISTIC"
    assert result.status == "FALLBACK"
    assert result.main_reason == "Alasan deterministic"
    assert result.main_risk == "Risiko deterministic"


class CountingGroq(GroqInterpreter):
    def __init__(self, **kwargs) -> None:
        cache_enabled = bool(kwargs.pop("cache_enabled", False))
        super().__init__(
            api_key="test-key",
            model="llama-3.3-70b-versatile",
            cache_enabled=cache_enabled,
            **kwargs,
        )
        self.requests: list[str] = []

    def _request(self, facts: dict, report_kind: str) -> dict:
        self.requests.append(report_kind)
        return {
            "main_reason": "Konteks mendukung eksekusi selektif.",
            "main_risk": "Volatilitas tetap perlu diwaspadai.",
            "execution_note": "Tunggu konfirmasi yang sudah ditetapkan engine.",
        }


def test_groq_request_budget_matches_existing_sde_contract() -> None:
    interpreter = CountingGroq(max_watchlist_calls=5)

    interpreter.interpret(_market_facts("2026-08-08"), _fallback())
    interpreter.interpret(_market_facts("2026-08-09"), _fallback())
    assert interpreter.requests.count("MARKET_OUTLOOK") == 1

    for rank in range(1, 8):
        interpreter.interpret(_watchlist_facts(rank), _fallback())

    assert interpreter.requests.count("FINAL_WATCHLIST") == 5
    assert len(interpreter.requests) == 6


class QuotaGroq(GroqInterpreter):
    def __init__(self) -> None:
        super().__init__(
            api_key="test-key",
            model="llama-3.3-70b-versatile",
            max_retries=3,
            cache_enabled=False,
        )
        self.request_count = 0

    def _request(self, facts: dict, report_kind: str) -> dict:
        self.request_count += 1
        raise GroqHTTPError(429, "quota exceeded")


def test_groq_429_falls_back_without_retry() -> None:
    interpreter = QuotaGroq()
    result = interpreter.interpret(_market_facts(), _fallback())
    assert interpreter.request_count == 1
    assert result.source == "DETERMINISTIC"
    assert result.status == "FALLBACK"
    assert "Groq HTTP 429" in result.warning


def test_groq_success_cache_avoids_repeat_request(tmp_path: Path) -> None:
    cache_dir = tmp_path / "ai-cache"
    facts = _market_facts()

    first = CountingGroq(cache_enabled=True, cache_dir=cache_dir)
    first_result = first.interpret(facts, _fallback())
    assert first_result.source == "GROQ"
    assert len(first.requests) == 1

    second = CountingGroq(cache_enabled=True, cache_dir=cache_dir)
    second_result = second.interpret(facts, _fallback())
    assert second.requests == []
    assert second_result.source == "GROQ_CACHE"
    assert second_result.status == "CACHE_HIT"
