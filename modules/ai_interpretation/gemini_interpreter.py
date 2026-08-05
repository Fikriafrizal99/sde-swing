from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
    "technical_quality",
    "entry_readiness",
    "broker_score",
    "broker_confidence",
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

ACTIVE_WATCHLIST_DECISIONS = {
    "BUY",
    "BUY READY",
    "BUY CONFIRMED",
    "BUY CANDIDATE",
    "BUY ON TRIGGER",
    "WATCH",
    "WATCH HIGH",
}

CACHE_VERSION = "SDE_GEMINI_CACHE_V2"


@dataclass(frozen=True)
class InterpretationResult:
    main_reason: str
    main_risk: str
    execution_note: str = ""
    source: str = "DETERMINISTIC"
    status: str = "FALLBACK"
    warning: str = ""


class GeminiHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = int(status_code)
        super().__init__(f"Gemini HTTP {self.status_code}: {detail}")


class GeminiInterpreter:
    """Quota-aware presentation-only Gemini layer.

    Gemini may only write three narrative fields and never owns an engine fact.
    Calls are deliberately limited to one Market Outlook and at most five active
    Final Watchlist symbols per interpreter instance. Broker Multi-Day and other
    report types always use deterministic narratives.
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
        self.api_key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = (model if model is not None else os.getenv("GEMINI_MODEL", "gemini-2.5-flash")).strip()
        self.timeout_seconds = max(3, int(timeout_seconds))
        self.max_retries = max(0, int(max_retries))
        self.temperature = min(0.4, max(0.0, float(temperature)))
        self.enabled = bool(enabled)
        env_limit = os.getenv("GEMINI_MAX_WATCHLIST_CALLS", "5")
        requested_limit = env_limit if max_watchlist_calls is None else max_watchlist_calls
        try:
            self.max_watchlist_calls = max(0, min(5, int(requested_limit)))
        except Exception:
            self.max_watchlist_calls = 5
        raw_cache_dir = cache_dir if cache_dir is not None else os.getenv("GEMINI_CACHE_DIR", "data/state/ai_cache")
        self.cache_dir = Path(raw_cache_dir) if str(raw_cache_dir or "").strip() else None
        self.cache_enabled = bool(cache_enabled and self.cache_dir is not None)
        self._market_calls = 0
        self._watchlist_calls = 0

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.api_key and self.model)

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
            except GeminiHTTPError as exc:
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
            warning=f"GEMINI_FALLBACK: {last_error}"[:300],
        )

    @staticmethod
    def _decision(value: Any) -> str:
        return str(value or "").upper().replace("_", " ").strip()

    @classmethod
    def _report_kind(cls, facts: dict[str, Any]) -> str:
        if any(key in facts for key in ("state_1d", "state_3d", "state_5d")) and not facts.get("decision"):
            return "BROKER_MULTIDAY"
        if facts.get("symbol") and facts.get("decision"):
            return "FINAL_WATCHLIST"
        if facts.get("market_regime") and any(
            facts.get(key) not in (None, "", [], {})
            for key in ("execution_mode", "ihsg_trend", "breadth", "global_sentiment", "global_tone")
        ):
            return "MARKET_OUTLOOK"
        return "OTHER"

    def _eligible(self, report_kind: str, facts: dict[str, Any]) -> bool:
        if report_kind == "MARKET_OUTLOOK":
            return True
        if report_kind != "FINAL_WATCHLIST":
            return False
        if self._decision(facts.get("decision")) not in ACTIVE_WATCHLIST_DECISIONS:
            return False
        rank = facts.get("rank")
        if rank not in (None, ""):
            try:
                if int(float(rank)) > self.max_watchlist_calls:
                    return False
            except Exception:
                pass
        return self.max_watchlist_calls > 0

    def _consume_budget(self, report_kind: str) -> bool:
        if report_kind == "MARKET_OUTLOOK":
            if self._market_calls >= 1:
                return False
            self._market_calls += 1
            return True
        if report_kind == "FINAL_WATCHLIST":
            if self._watchlist_calls >= self.max_watchlist_calls:
                return False
            self._watchlist_calls += 1
            return True
        return False

    def _sanitize_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "trade_date", "rank", "symbol", "decision", "confidence", "setup",
            "entry_low", "entry_high", "stop_loss", "target_1", "target_2",
            "risk_reward", "trend", "technical_state", "technical_score",
            "technical_quality", "entry_readiness", "momentum_status", "rsi",
            "volume_ratio_ma20", "broker_state", "broker_direction", "broker_score",
            "broker_confidence", "broker_alignment", "sector_state", "market_regime",
            "facts", "risks", "provider", "source_mode", "coverage", "rotating_in",
            "leading", "weakening", "rotating_out", "lagging", "ihsg_change",
            "ihsg_trend", "ihsg_momentum", "ihsg_reason", "confidence_pct", "breadth",
            "execution_mode", "global_tone", "global_sentiment", "yahoo_status",
            "historical_status", "zapi_status", "zapi_coverage", "zapi_freshness_days",
            "reconciliation_status", "stockbit_status", "broker_status", "degraded_reason",
            "main_reason_technical", "main_reason_broker", "main_reason_entry",
            "trigger_description", "invalidation",
        }
        sanitized = {key: value for key, value in facts.items() if key in allowed}
        instruments: list[dict[str, Any]] = []
        for item in facts.get("global_instruments", []) or []:
            if not isinstance(item, dict):
                continue
            compact = {
                key: item.get(key)
                for key in ("display_name", "change_pct", "freshness_status")
                if item.get(key) not in (None, "")
            }
            if compact:
                instruments.append(compact)
            if len(instruments) >= 14:
                break
        if instruments:
            sanitized["global_instruments"] = instruments
        return sanitized

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
                source="GEMINI_CACHE",
                status="CACHE_HIT",
            )
        except Exception:
            return None

    @staticmethod
    def _write_cache(path: Path | None, report_kind: str, result: InterpretationResult) -> None:
        if path is None or result.source != "GEMINI" or result.status != "SUCCESS":
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
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
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
            "spesifik, tidak generik, dan mudah dieksekusi. " + task
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
            "contents": [{
                "role": "user",
                "parts": [{"text": json.dumps({"report_kind": report_kind, "facts": facts}, ensure_ascii=False)}],
            }],
            "generationConfig": {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "maxOutputTokens": 240,
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
            raise GeminiHTTPError(exc.code, detail) from exc
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
