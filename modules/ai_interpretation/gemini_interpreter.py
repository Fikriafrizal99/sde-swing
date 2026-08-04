from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


IMMUTABLE_FIELDS = {
    "symbol",
    "decision",
    "confidence",
    "entry_low",
    "entry_high",
    "stop_loss",
    "target_1",
    "target_2",
    "risk_reward",
    "technical_score",
    "broker_score",
    "market_regime",
    "sector_state",
    "provider",
    "source_mode",
    "coverage",
    "yahoo_status",
    "historical_status",
    "zapi_status",
    "zapi_coverage",
    "zapi_freshness_days",
    "reconciliation_status",
    "stockbit_status",
    "broker_status",
    "degraded_reason",
}


@dataclass(frozen=True)
class InterpretationResult:
    main_reason: str
    main_risk: str
    execution_note: str = ""
    source: str = "DETERMINISTIC"
    status: str = "FALLBACK"
    warning: str = ""


class GeminiInterpreter:
    """Presentation-only Gemini layer with strict structured output and fallback.

    The class never returns changes to engine-owned fields. Only three narrative
    fields are accepted from the model. Any timeout, HTTP error, invalid JSON,
    empty response, or policy violation returns the deterministic fallback.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        temperature: float = 0.2,
        enabled: bool = True,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = (model if model is not None else os.getenv("GEMINI_MODEL", "gemini-2.5-flash")).strip()
        self.timeout_seconds = max(3, int(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.temperature = min(0.4, max(0.0, float(temperature)))
        self.enabled = bool(enabled)

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key and self.model)

    def interpret(self, facts: dict[str, Any], fallback: dict[str, str]) -> InterpretationResult:
        deterministic = InterpretationResult(
            main_reason=str(fallback.get("main_reason") or "Data engine belum cukup untuk interpretasi tambahan."),
            main_risk=str(fallback.get("main_risk") or "Gunakan level invalidasi dari engine dan disiplin terhadap stop loss."),
            execution_note=str(fallback.get("execution_note") or ""),
        )
        if not self.configured:
            return deterministic

        safe_facts = self._sanitize_facts(facts)
        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                parsed = self._request(safe_facts)
                return self._validate(parsed, safe_facts, deterministic)
            except Exception as exc:  # network/model failure must never stop SDE
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 4))
        return InterpretationResult(
            main_reason=deterministic.main_reason,
            main_risk=deterministic.main_risk,
            execution_note=deterministic.execution_note,
            source="DETERMINISTIC",
            status="FALLBACK",
            warning=f"GEMINI_FALLBACK: {last_error}"[:300],
        )

    def _sanitize_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "symbol", "decision", "confidence", "setup", "entry_low", "entry_high",
            "stop_loss", "target_1", "target_2", "risk_reward", "technical_state",
            "technical_score", "broker_state", "broker_score", "sector_state",
            "market_regime", "facts", "risks", "provider", "source_mode", "coverage",
            "state_1d", "state_3d", "state_5d", "rotating_in", "leading",
            "weakening", "rotating_out", "ihsg_change", "ihsg_trend",
            "ihsg_momentum", "breadth", "execution_mode",
            "yahoo_status", "historical_status", "zapi_status", "zapi_coverage",
            "zapi_freshness_days", "reconciliation_status", "stockbit_status",
            "broker_status", "degraded_reason",
        }
        return {key: value for key, value in facts.items() if key in allowed}

    def _request(self, facts: dict[str, Any]) -> dict[str, Any]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        instruction = (
            "Anda adalah layer interpretasi SDE Swing. Gunakan HANYA fakta JSON. "
            "Jangan mengubah atau menciptakan decision, confidence, entry, stop loss, target, "
            "risk reward, score, market regime, sector state, provider, coverage, atau angka lain. "
            "Jangan menyebut data live jika source_mode bukan LIVE. Jawab Bahasa Indonesia singkat."
        )
        schema = {
            "type": "OBJECT",
            "properties": {
                "main_reason": {"type": "STRING"},
                "main_risk": {"type": "STRING"},
                "execution_note": {"type": "STRING"},
            },
            "required": ["main_reason", "main_risk", "execution_note"],
        }
        body = {
            "systemInstruction": {"parts": [{"text": instruction}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps(facts, ensure_ascii=False)}]}],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "maxOutputTokens": 320,
            },
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc
        candidates = payload.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(str(part.get("text", "")) for part in parts).strip()
        if not text:
            raise RuntimeError("Gemini returned empty content")
        return json.loads(text)

    def _validate(
        self,
        parsed: dict[str, Any],
        facts: dict[str, Any],
        fallback: InterpretationResult,
    ) -> InterpretationResult:
        if not isinstance(parsed, dict):
            raise ValueError("Gemini response is not an object")
        if IMMUTABLE_FIELDS.intersection(parsed):
            raise ValueError("Gemini attempted to return engine-owned fields")
        allowed = {"main_reason", "main_risk", "execution_note"}
        if set(parsed) - allowed:
            raise ValueError("Gemini response contains unsupported fields")

        reason = self._clean(parsed.get("main_reason"), fallback.main_reason, 220)
        risk = self._clean(parsed.get("main_risk"), fallback.main_risk, 220)
        note = self._clean(parsed.get("execution_note"), fallback.execution_note, 180)
        self._reject_invented_numbers(reason + " " + risk + " " + note, facts)
        return InterpretationResult(reason, risk, note, source="GEMINI", status="SUCCESS")

    @staticmethod
    def _clean(value: Any, fallback: str, limit: int) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            return fallback
        return text[:limit].rstrip(" ,;:")

    @staticmethod
    def _reject_invented_numbers(text: str, facts: dict[str, Any]) -> None:
        import re

        rendered_facts = json.dumps(facts, ensure_ascii=False)
        for token in re.findall(r"(?<![A-Za-z])\d[\d.,]*(?:%|x)?", text):
            plain = token.rstrip("%x")
            variants = {plain, plain.replace(".", ""), plain.replace(",", ".")}
            if not any(item and item in rendered_facts for item in variants):
                raise ValueError(f"Gemini introduced unsupported number: {token}")
