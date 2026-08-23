from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import requests

from swing_utils import write_json


OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "conclusion": {"type": "string"},
    },
    "required": ["analysis", "conclusion"],
    "additionalProperties": False,
}

_ALLOWED_FACT_FIELDS = (
    "trade_date", "rank", "symbol", "decision", "confidence", "setup",
    "trend", "technical_quality", "technical_score", "technical_state",
    "technical_status", "entry_readiness", "momentum_status", "rsi",
    "volume_description", "volume_ratio_ma20", "last_price", "entry_low",
    "entry_high", "entry_distance_pct", "stop_loss", "active_stop_loss",
    "target_1", "target_2", "risk_reward", "support", "resistance",
    "phase", "fib_status", "swing_high", "swing_low", "broker_status",
    "broker_direction", "broker_confidence", "broker_score", "broker_state",
    "broker_net_flow", "broker_buy_ratio", "broker_sell_ratio", "buy_days",
    "sell_days", "buyer_concentration", "seller_concentration",
    "broker_pattern", "bandar_buy_cost", "distance_to_buy_cost",
    "distance_to_buyer_avg_pct", "top_buyers", "top_sellers",
    "multi_day_flow", "flow_persistence", "broker_alignment",
    "broker_period_type", "broker_period_start", "broker_period_end",
    "broker_trading_days", "broker_session_dates", "broker_period_source",
    "broker_coverage", "broker_period_coverage", "broker_coverage_text",
    "broker_coverage_status", "broker_freshness_status",
    "today_pulse_status", "today_pulse_net_flow", "today_pulse_direction",
    "sector_state", "market_regime", "trigger_description", "waiting_triggers",
    "main_reason_technical", "main_reason_broker", "main_reason_entry",
    "engine_final_reason", "main_reason", "risk_items", "main_risk",
    "invalidation", "execution_note", "data_status", "data_conflict",
    "exchange_status", "exchange_veto", "risk_flags", "yahoo_status",
    "zapi_status", "reconciliation_status", "zapi_freshness_days",
    "foreign_buy", "foreign_sell", "foreign_net", "provider", "source_mode",
    "coverage", "generated_at",
)


@dataclass(frozen=True)
class ProviderAttempt:
    provider: str
    model: str
    status: str
    reason: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WatchlistAIResult:
    status: str
    symbol: str
    trade_date: str
    decision: str
    analysis: str = ""
    conclusion: str = ""
    provider: str = ""
    model: str = ""
    attempts: tuple[ProviderAttempt, ...] = ()
    context_hash: str = ""
    artifact_path: str = ""

    @property
    def success(self) -> bool:
        return self.status in {"SUCCESS", "CACHE_HIT", "FALLBACK_SUCCESS"}


def _clean_scalar(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.lower() in {"", "nan", "none", "null"}:
            return ""
        if text[:1] in {"[", "{"}:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, (list, dict)):
                    return parsed
            except Exception:
                pass
        return text
    return value


def build_watchlist_context(
    row: Mapping[str, Any],
    *,
    chart_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build one read-only AI context from the official Final Watchlist CSV row."""
    facts = {
        key: _clean_scalar(row.get(key))
        for key in _ALLOWED_FACT_FIELDS
        if _clean_scalar(row.get(key)) not in ("", [], {})
    }
    symbol = str(facts.get("symbol") or row.get("symbol") or "").strip().upper()
    trade_date = str(facts.get("trade_date") or row.get("trade_date") or "").strip()[:10]
    decision = str(facts.get("decision") or row.get("decision") or "").strip().upper()
    context: dict[str, Any] = {
        "identity": {"symbol": symbol, "trade_date": trade_date},
        "final_result": {
            "decision": decision,
            "final_score": facts.get("confidence", ""),
        },
        "facts": facts,
    }
    chart = Path(chart_path) if chart_path else None
    if chart is not None and chart.exists() and chart.is_file():
        context["chart"] = {
            "path": str(chart),
            "role": "visual_context_only",
            "numeric_authority": "structured_sde_facts",
        }
    return context


def _numeric_variants(token: str) -> set[str]:
    value = str(token or "").strip().lower()
    value = value.replace("rp", "").replace("%", "").replace("x", "")
    value = value.lstrip("+-").strip()
    if not value:
        return set()
    variants = {value}
    compact = value.replace(".", "").replace(",", "")
    if compact:
        variants.add(compact)
    variants.add(value.replace(",", "."))
    variants.add(value.replace(".", ","))
    return {item.strip(".,") for item in variants if item.strip(".,")}


def _number_tokens(text: str) -> list[str]:
    return re.findall(r"(?<![A-Za-z])[-+]?\d[\d.,]*(?:%|x)?", str(text or ""))


def _validate_numbers(text: str, context: Mapping[str, Any]) -> None:
    rendered = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    allowed: set[str] = set()
    for token in _number_tokens(rendered):
        allowed.update(_numeric_variants(token))
    # Ratio prose commonly renders the implicit leading `1:` even when the SDE
    # stores only the numeric RR. This does not create a new engine level.
    allowed.add("1")
    for token in _number_tokens(text):
        variants = _numeric_variants(token)
        if variants and variants.isdisjoint(allowed):
            raise ValueError(f"AI introduced unsupported number: {token}")


def _sanitize_reason(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", text)
    text = re.sub(r"AIza[A-Za-z0-9_-]+", "[REDACTED]", text)
    return text[:240]


def _response_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in payload.get("output", []) or []:
        if not isinstance(item, Mapping):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, Mapping):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _image_data_url(path: Path, max_bytes: int) -> str:
    if not path.exists() or not path.is_file() or path.stat().st_size > max_bytes:
        return ""
    suffix = path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/webp"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class WatchlistAIService:
    """Provider-neutral, failover-capable Final Watchlist AI interpreter.

    This service owns only the `watchlist_ai` configuration/cache/artifact
    namespace. It has no dependency on News Monitor or IDX Disclosure AI state.
    """

    def __init__(
        self,
        scheduler_config: Mapping[str, Any],
        *,
        environ: Mapping[str, str] | None = None,
        session: Any | None = None,
    ) -> None:
        self.scheduler_config = dict(scheduler_config or {})
        self.config = dict(self.scheduler_config.get("watchlist_ai", {}) or {})
        self.environ = os.environ if environ is None else environ
        self.session = session or requests.Session()
        self.timeout_seconds = max(3, int(self.config.get("timeout_seconds", 30) or 30))
        self.max_output_tokens = max(256, int(self.config.get("max_output_tokens", 900) or 900))
        self.max_chart_bytes = max(250_000, int(self.config.get("max_chart_bytes", 4_000_000) or 4_000_000))
        self.cache_root = Path(str(self.config.get("cache_root") or "data/state/ai_cache/watchlist"))
        self.output_root = Path(str(self.config.get("output_root") or "data/output/ai_interpretation/watchlist"))

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    @property
    def max_symbols(self) -> int:
        return max(0, int(self.config.get("max_symbols", 5) or 0))

    def _providers(self) -> list[dict[str, Any]]:
        raw = self.config.get("providers", [])
        providers = [dict(item) for item in raw if isinstance(item, Mapping) and item.get("enabled", True)]
        providers.sort(key=lambda item: int(item.get("priority", 999) or 999))
        return providers[:3]

    @staticmethod
    def _instruction() -> str:
        return (
            "Anda adalah AI interpreter khusus Final Watchlist SDE Swing. Jelaskan perspektif Anda "
            "sebagai swing trader berdasarkan HANYA fakta SDE yang diberikan. Anda boleh menyebut "
            "angka resmi seperti harga, entry, stop loss, target, RR, score, net flow, broker cost, "
            "support/resistance, RSI, dan angka lain persis dari data. Jangan mengubah angka, jangan "
            "menciptakan level baru, jangan membuat AI Score/probability/AI Decision, dan jangan "
            "menggantikan keputusan SDE. Hubungkan chart, technical, plan, broker, multi-day flow, "
            "market context, dan risiko secara natural bila datanya tersedia. Tulis Bahasa Indonesia "
            "dalam 2-4 paragraf yang benar-benar menjelaskan pemikiran, bukan daftar poin. Kesimpulan "
            "harus singkat. Keluarkan JSON valid dengan tepat dua key: analysis dan conclusion."
        )

    def _cache_path(self, provider: str, model: str, context_hash: str) -> Path:
        safe_provider = re.sub(r"[^a-z0-9_-]+", "_", provider.lower())
        safe_model = re.sub(r"[^a-zA-Z0-9_.-]+", "_", model)[:100]
        return self.cache_root / safe_provider / safe_model / f"{context_hash}.json"

    def _read_cache(self, path: Path) -> tuple[str, str] | None:
        if not bool(self.config.get("cache_enabled", True)) or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            analysis = str(payload.get("analysis") or "").strip()
            conclusion = str(payload.get("conclusion") or "").strip()
            if analysis and conclusion:
                return analysis, conclusion
        except Exception:
            return None
        return None

    def _write_cache(self, path: Path, analysis: str, conclusion: str) -> None:
        if not bool(self.config.get("cache_enabled", True)):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, {
            "analysis": analysis,
            "conclusion": conclusion,
            "cached_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    def _request_openai(self, cfg: Mapping[str, Any], context: Mapping[str, Any], chart_path: Path | None) -> dict[str, Any]:
        key = str(self.environ.get(str(cfg.get("api_key_env") or "OPENAI_API_KEY"), "") or "").strip()
        model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_OPENAI_MODEL") or "gpt-5.6-luna").strip()
        if not key:
            raise RuntimeError("MISSING_API_KEY")
        content: list[dict[str, Any]] = [{
            "type": "input_text",
            "text": json.dumps(context, ensure_ascii=False, default=str),
        }]
        if bool(cfg.get("vision", True)) and chart_path is not None:
            image = _image_data_url(chart_path, self.max_chart_bytes)
            if image:
                content.append({"type": "input_image", "image_url": image})
        body = {
            "model": model,
            "instructions": self._instruction(),
            "input": [{"role": "user", "content": content}],
            "text": {"format": {
                "type": "json_schema",
                "name": "watchlist_ai_interpretation",
                "schema": OUTPUT_SCHEMA,
                "strict": True,
            }},
            "max_output_tokens": self.max_output_tokens,
        }
        response = self.session.post(
            str(cfg.get("base_url") or "https://api.openai.com/v1/responses"),
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP_{response.status_code}:{response.text[:180]}")
        payload = response.json()
        text = _response_text(payload)
        if not text:
            raise RuntimeError("EMPTY_RESPONSE")
        return json.loads(text)

    def _request_gemini(self, cfg: Mapping[str, Any], context: Mapping[str, Any], chart_path: Path | None) -> dict[str, Any]:
        key = str(self.environ.get(str(cfg.get("api_key_env") or "GEMINI_API_KEY"), "") or "").strip()
        model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_GEMINI_MODEL") or "gemini-2.5-flash").strip()
        if not key:
            raise RuntimeError("MISSING_API_KEY")
        parts: list[dict[str, Any]] = [{"text": json.dumps(context, ensure_ascii=False, default=str)}]
        if bool(cfg.get("vision", True)) and chart_path is not None and chart_path.exists() and chart_path.stat().st_size <= self.max_chart_bytes:
            suffix = chart_path.suffix.lower()
            mime = "image/png" if suffix == ".png" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/webp" if suffix == ".webp" else ""
            if mime:
                parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(chart_path.read_bytes()).decode("ascii")}})
        body = {
            "systemInstruction": {"parts": [{"text": self._instruction()}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "analysis": {"type": "STRING"},
                        "conclusion": {"type": "STRING"},
                    },
                    "required": ["analysis", "conclusion"],
                },
                "maxOutputTokens": self.max_output_tokens,
                "temperature": float(self.config.get("temperature", 0.25) or 0.25),
            },
        }
        base = str(cfg.get("base_url") or "https://generativelanguage.googleapis.com/v1beta/models").rstrip("/")
        response = self.session.post(
            f"{base}/{model}:generateContent?key={key}",
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP_{response.status_code}:{response.text[:180]}")
        payload = response.json()
        candidates = payload.get("candidates") or []
        if not candidates:
            raise RuntimeError("EMPTY_RESPONSE")
        text = "".join(
            str(part.get("text") or "")
            for part in candidates[0].get("content", {}).get("parts", [])
            if isinstance(part, Mapping)
        ).strip()
        if not text:
            raise RuntimeError("EMPTY_RESPONSE")
        return json.loads(text)

    def _request_groq(self, cfg: Mapping[str, Any], context: Mapping[str, Any], chart_path: Path | None) -> dict[str, Any]:
        key = str(self.environ.get(str(cfg.get("api_key_env") or "GROQ_API_KEY"), "") or "").strip()
        model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_GROQ_MODEL") or "llama-3.3-70b-versatile").strip()
        if not key:
            raise RuntimeError("MISSING_API_KEY")
        user_content: Any = json.dumps(context, ensure_ascii=False, default=str)
        if bool(cfg.get("vision", False)) and chart_path is not None:
            image = _image_data_url(chart_path, self.max_chart_bytes)
            if image:
                user_content = [
                    {"type": "text", "text": user_content},
                    {"type": "image_url", "image_url": {"url": image}},
                ]
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._instruction()},
                {"role": "user", "content": user_content},
            ],
            "temperature": float(self.config.get("temperature", 0.25) or 0.25),
            "max_completion_tokens": self.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        response = self.session.post(
            str(cfg.get("base_url") or "https://api.groq.com/openai/v1/chat/completions"),
            json=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP_{response.status_code}:{response.text[:180]}")
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("EMPTY_RESPONSE")
        text = str(choices[0].get("message", {}).get("content") or "").strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").strip()
            if text.endswith("```"):
                text = text[:-3].strip()
        if not text:
            raise RuntimeError("EMPTY_RESPONSE")
        return json.loads(text)

    def _request(self, cfg: Mapping[str, Any], context: Mapping[str, Any], chart_path: Path | None) -> tuple[str, str, dict[str, Any]]:
        provider = str(cfg.get("provider") or "").strip().upper()
        if provider == "OPENAI":
            model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_OPENAI_MODEL") or "gpt-5.6-luna").strip()
            return provider, model, self._request_openai(cfg, context, chart_path)
        if provider == "GEMINI":
            model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_GEMINI_MODEL") or "gemini-2.5-flash").strip()
            return provider, model, self._request_gemini(cfg, context, chart_path)
        if provider == "GROQ":
            model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_GROQ_MODEL") or "llama-3.3-70b-versatile").strip()
            return provider, model, self._request_groq(cfg, context, chart_path)
        raise RuntimeError(f"UNSUPPORTED_PROVIDER:{provider or 'EMPTY'}")

    @staticmethod
    def _validate(payload: Mapping[str, Any], context: Mapping[str, Any]) -> tuple[str, str]:
        if not isinstance(payload, Mapping):
            raise ValueError("RESPONSE_NOT_OBJECT")
        if set(payload) - {"analysis", "conclusion"}:
            raise ValueError("UNSUPPORTED_RESPONSE_FIELDS")
        analysis = re.sub(r"\s+", " ", str(payload.get("analysis") or "")).strip()
        conclusion = re.sub(r"\s+", " ", str(payload.get("conclusion") or "")).strip()
        if len(analysis) < 80:
            raise ValueError("ANALYSIS_TOO_SHORT")
        if not conclusion:
            raise ValueError("CONCLUSION_EMPTY")
        _validate_numbers(f"{analysis} {conclusion}", context)
        return analysis[:3000], conclusion[:700]

    def interpret(self, context: Mapping[str, Any]) -> WatchlistAIResult:
        symbol = str((context.get("identity") or {}).get("symbol") or "").upper()
        trade_date = str((context.get("identity") or {}).get("trade_date") or "")[:10]
        decision = str((context.get("final_result") or {}).get("decision") or "").upper()
        context_hash = hashlib.sha256(
            json.dumps(context, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        chart_path_text = str((context.get("chart") or {}).get("path") or "")
        chart_path = Path(chart_path_text) if chart_path_text else None
        attempts: list[ProviderAttempt] = []

        providers = self._providers()
        if not providers:
            return WatchlistAIResult(
                status="ALL_PROVIDERS_FAILED",
                symbol=symbol,
                trade_date=trade_date,
                decision=decision,
                attempts=(ProviderAttempt("CONFIG", "", "FAILED", "NO_PROVIDER_CONFIGURED"),),
                context_hash=context_hash,
            )

        for index, cfg in enumerate(providers, start=1):
            provider = str(cfg.get("provider") or "").strip().upper()
            if provider == "OPENAI":
                model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_OPENAI_MODEL") or "gpt-5.6-luna").strip()
            elif provider == "GEMINI":
                model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_GEMINI_MODEL") or "gemini-2.5-flash").strip()
            elif provider == "GROQ":
                model = str(cfg.get("model") or self.environ.get("WATCHLIST_AI_GROQ_MODEL") or "llama-3.3-70b-versatile").strip()
            else:
                model = str(cfg.get("model") or "")
            cache_path = self._cache_path(provider or "unknown", model or "unknown", context_hash)
            cached = self._read_cache(cache_path)
            if cached is not None:
                analysis, conclusion = cached
                attempts.append(ProviderAttempt(provider, model, "CACHE_HIT"))
                result = WatchlistAIResult(
                    status="CACHE_HIT" if index == 1 else "FALLBACK_SUCCESS",
                    symbol=symbol,
                    trade_date=trade_date,
                    decision=decision,
                    analysis=analysis,
                    conclusion=conclusion,
                    provider=provider,
                    model=model,
                    attempts=tuple(attempts),
                    context_hash=context_hash,
                )
                return self._persist_result(result, context)
            try:
                provider, model, payload = self._request(cfg, context, chart_path)
                analysis, conclusion = self._validate(payload, context)
                self._write_cache(cache_path, analysis, conclusion)
                attempts.append(ProviderAttempt(provider, model, "SUCCESS"))
                result = WatchlistAIResult(
                    status="SUCCESS" if index == 1 else "FALLBACK_SUCCESS",
                    symbol=symbol,
                    trade_date=trade_date,
                    decision=decision,
                    analysis=analysis,
                    conclusion=conclusion,
                    provider=provider,
                    model=model,
                    attempts=tuple(attempts),
                    context_hash=context_hash,
                )
                return self._persist_result(result, context)
            except Exception as exc:
                attempts.append(ProviderAttempt(provider, model, "FAILED", _sanitize_reason(exc)))

        result = WatchlistAIResult(
            status="ALL_PROVIDERS_FAILED",
            symbol=symbol,
            trade_date=trade_date,
            decision=decision,
            attempts=tuple(attempts),
            context_hash=context_hash,
        )
        return self._persist_result(result, context)

    def _persist_result(self, result: WatchlistAIResult, context: Mapping[str, Any]) -> WatchlistAIResult:
        target_dir = self.output_root / result.trade_date
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{result.symbol or 'UNKNOWN'}.json"
        payload = {
            "schema_version": "WATCHLIST_AI_V1",
            "symbol": result.symbol,
            "trade_date": result.trade_date,
            "sde_decision": result.decision,
            "status": result.status,
            "provider_used": result.provider,
            "model": result.model,
            "analysis": result.analysis,
            "conclusion": result.conclusion,
            "attempts": [item.to_dict() for item in result.attempts],
            "context_hash": result.context_hash,
            "source": "FINAL_WATCHLIST_FACTS",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "context": context,
        }
        write_json(target, payload)
        return WatchlistAIResult(
            **{**result.__dict__, "artifact_path": str(target)}
        )

    def write_manifest(self, trade_date: str, results: Iterable[WatchlistAIResult]) -> Path:
        items = list(results)
        target_dir = self.output_root / trade_date
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "manifest.json"
        write_json(target, {
            "schema_version": "WATCHLIST_AI_MANIFEST_V1",
            "trade_date": trade_date,
            "status": (
                "SUCCESS" if items and all(item.success for item in items)
                else "PARTIAL" if any(item.success for item in items)
                else "ALL_PROVIDERS_FAILED"
            ),
            "results": [{
                "symbol": item.symbol,
                "status": item.status,
                "provider": item.provider,
                "model": item.model,
                "artifact_path": item.artifact_path,
                "attempts": [attempt.to_dict() for attempt in item.attempts],
            } for item in items],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        return target
