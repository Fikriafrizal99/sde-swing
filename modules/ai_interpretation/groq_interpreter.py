from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .gemini_interpreter import (
    IMMUTABLE_FIELDS,
    GeminiInterpreter as _LegacyGeminiInterpreter,
    InterpretationResult,
)


CACHE_VERSION = "SDE_GROQ_CACHE_V1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = int(status_code)
        super().__init__(f"Groq HTTP {self.status_code}: {detail}")


class GroqInterpreter(_LegacyGeminiInterpreter):
    """Quota-aware presentation-only Groq layer for SDE Swing.

    Groq only writes the three narrative fields already owned by the AI
    presentation layer. Engine decisions, prices, scores, regimes, provider
    state, and source facts remain immutable. Failure, timeout, quota errors,
    malformed JSON, or a missing API key always fall back to deterministic SDE
    text and must never stop the pipeline.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: int = 20,
        max_retries: int = 0,
        temperature: float = 0.2,
        enabled: bool = True,
        max_watchlist_calls: int | None = None,
        cache_dir: str | Path | None = None,
        cache_enabled: bool = True,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.getenv("GROQ_API_KEY", "")
        resolved_model = model if model is not None else os.getenv("GROQ_MODEL", DEFAULT_MODEL)
        resolved_limit = (
            max_watchlist_calls
            if max_watchlist_calls is not None
            else os.getenv("GROQ_MAX_WATCHLIST_CALLS", "5")
        )
        resolved_cache_dir = (
            cache_dir
            if cache_dir is not None
            else os.getenv("GROQ_CACHE_DIR", "data/state/ai_cache")
        )
        super().__init__(
            api_key=str(resolved_key or ""),
            model=str(resolved_model or DEFAULT_MODEL),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            temperature=temperature,
            enabled=enabled,
            max_watchlist_calls=resolved_limit,
            cache_dir=resolved_cache_dir,
            cache_enabled=cache_enabled,
        )

    def interpret(self, facts: dict[str, Any], fallback: dict[str, str]) -> InterpretationResult:
        deterministic = InterpretationResult(
            main_reason=str(fallback.get("main_reason") or "Data engine belum cukup untuk interpretasi tambahan."),
            main_risk=str(fallback.get("main_risk") or "Gunakan level invalidasi dari engine dan disiplin terhadap stop loss."),
            execution_note=str(fallback.get("execution_note") or ""),
        )
        report_kind = self._report_kind(facts)
        if not self._eligible(report_kind, facts):
            return deterministic

        safe_facts = self._sanitize_facts(facts)
        cache_path = self._cache_path(report_kind, safe_facts)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached
        if not self.configured:
            return deterministic
        if not self._consume_budget(report_kind):
            return deterministic

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                parsed = self._request(safe_facts, report_kind)
                result = self._validate(parsed, safe_facts, deterministic)
                self._write_cache(cache_path, report_kind, result)
                return result
            except GroqHTTPError as exc:
                last_error = str(exc)
                if exc.status_code == 429:
                    break
                if attempt < self.max_retries and exc.status_code in {408, 500, 502, 503, 504}:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                break
            except Exception as exc:  # network/model failure must never stop SDE
                last_error = str(exc)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                break
        return InterpretationResult(
            main_reason=deterministic.main_reason,
            main_risk=deterministic.main_risk,
            execution_note=deterministic.execution_note,
            source="DETERMINISTIC",
            status="FALLBACK",
            warning=f"GROQ_FALLBACK: {last_error}"[:300],
        )

    def _cache_path(self, report_kind: str, facts: dict[str, Any]) -> Path | None:
        if not self.cache_enabled or self.cache_dir is None:
            return None
        payload = {
            "version": CACHE_VERSION,
            "model": self.model,
            "report_kind": report_kind,
            "facts": facts,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return self.cache_dir / report_kind.lower() / f"{digest}.json"

    @staticmethod
    def _read_cache(path: Path | None) -> InterpretationResult | None:
        if path is None or not path.exists() or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != CACHE_VERSION:
                return None
            return InterpretationResult(
                main_reason=str(payload["main_reason"]),
                main_risk=str(payload["main_risk"]),
                execution_note=str(payload.get("execution_note") or ""),
                source="GROQ_CACHE",
                status="CACHE_HIT",
            )
        except Exception:
            return None

    @staticmethod
    def _write_cache(path: Path | None, report_kind: str, result: InterpretationResult) -> None:
        if path is None or result.source != "GROQ" or result.status != "SUCCESS":
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": CACHE_VERSION,
                "report_kind": report_kind,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "main_reason": result.main_reason,
                "main_risk": result.main_risk,
                "execution_note": result.execution_note,
            }
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        except Exception:
            return

    def _request(self, facts: dict[str, Any], report_kind: str) -> dict[str, Any]:
        task = (
            "Untuk MARKET_OUTLOOK, jelaskan rencana besok dan risiko utama berdasarkan konteks IHSG, "
            "sentimen global, dan rotasi sektor. Untuk FINAL_WATCHLIST, jelaskan alasan utama, risiko, "
            "dan aksi eksekusi yang disiplin berdasarkan fakta saham."
        )
        instruction = (
            "Anda adalah layer interpretasi SDE Swing. Gunakan HANYA fakta JSON. "
            "Jangan mengubah atau menciptakan decision, confidence, entry, stop loss, target, "
            "risk reward, score, market regime, sector state, provider, coverage, atau angka lain. "
            "Jangan menyebut data live jika source_mode bukan LIVE. Jawab Bahasa Indonesia singkat, "
            "spesifik, tidak generik, dan mudah dieksekusi. Keluarkan JSON object valid dengan tepat "
            "tiga key: main_reason, main_risk, execution_note. " + task
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"report_kind": report_kind, "facts": facts},
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": self.temperature,
            "max_completion_tokens": 240,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "SDE-Swing/1.7 Groq-API-Client",
        }
        try:
            response = requests.post(
                GROQ_CHAT_COMPLETIONS_URL,
                json=body,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Groq network error: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text.strip().replace("\r", " ").replace("\n", " ")[:300]
            raise GroqHTTPError(response.status_code, detail or response.reason)

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Groq returned non-JSON response") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("Groq returned no choices")
        text = str(choices[0].get("message", {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("Groq returned empty content")
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        return json.loads(text)

    def _validate(
        self,
        parsed: dict[str, Any],
        facts: dict[str, Any],
        fallback: InterpretationResult,
    ) -> InterpretationResult:
        if not isinstance(parsed, dict):
            raise ValueError("Groq response is not an object")
        if IMMUTABLE_FIELDS.intersection(parsed):
            raise ValueError("Groq attempted to return engine-owned fields")
        allowed = {"main_reason", "main_risk", "execution_note"}
        if set(parsed) - allowed:
            raise ValueError("Groq response contains unsupported fields")

        reason = self._clean(parsed.get("main_reason"), fallback.main_reason, 220)
        risk = self._clean(parsed.get("main_risk"), fallback.main_risk, 220)
        note = self._clean(parsed.get("execution_note"), fallback.execution_note, 180)
        self._reject_invented_numbers(reason + " " + risk + " " + note, facts)
        return InterpretationResult(reason, risk, note, source="GROQ", status="SUCCESS")
