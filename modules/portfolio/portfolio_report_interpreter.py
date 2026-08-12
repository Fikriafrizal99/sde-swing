from __future__ import annotations

"""Presentation-only AI interpreter for actual portfolio reports.

The deterministic Position Management engine remains authoritative. AI may only
rewrite three short narrative fields and cannot change action, price, P/L,
TP/SL, broker state, market/sector state, or any other engine-owned fact.
"""

import json
import os
from typing import Any

import requests

from modules.ai_interpretation.groq_interpreter import (
    GROQ_CHAT_COMPLETIONS_URL,
    GroqHTTPError,
    GroqInterpreter,
)


class PortfolioGroqInterpreter(GroqInterpreter):
    def __init__(self, *args, max_portfolio_calls: int | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        raw_limit = max_portfolio_calls if max_portfolio_calls is not None else os.getenv("GROQ_MAX_PORTFOLIO_CALLS", "20")
        try:
            self.max_portfolio_calls = max(0, min(50, int(raw_limit)))
        except Exception:
            self.max_portfolio_calls = 20
        self._portfolio_calls = 0

    @classmethod
    def _report_kind(cls, facts: dict[str, Any]) -> str:
        if facts.get("position_id") and facts.get("management_action"):
            return "POSITION_MANAGEMENT"
        return "OTHER"

    def _eligible(self, report_kind: str, facts: dict[str, Any]) -> bool:
        return report_kind == "POSITION_MANAGEMENT" and self.max_portfolio_calls > 0

    def _consume_budget(self, report_kind: str) -> bool:
        if report_kind != "POSITION_MANAGEMENT":
            return False
        if self._portfolio_calls >= self.max_portfolio_calls:
            return False
        self._portfolio_calls += 1
        return True

    def _sanitize_facts(self, facts: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "position_id", "analysis_date", "symbol", "buy_price", "current_price",
            "pnl_pct", "management_action", "milestone", "initial_stop_loss", "initial_tp1", "initial_tp2",
            "active_stop_loss", "extended_target", "technical_state", "sector_state",
            "market_state", "sector", "market", "broker_current_state", "broker_effective_state",
            "broker_data_date", "broker_source", "broker_current_score", "broker_current_confidence",
            "broker_current_net_flow",
            "today_pulse_available",
            "broker_observation_count", "broker_context_3d", "broker_context_5d",
            "broker_context_7d", "broker_context_since_entry", "broker_net_flow_since_entry",
            "broker_context_3d_coverage", "broker_context_3d_missing_sessions",
            "broker_context_5d_coverage", "broker_context_5d_missing_sessions",
            "broker_since_entry_actual_sessions",
            "broker_buy_days", "broker_sell_days", "broker_accumulation_days",
            "broker_distribution_days", "broker_persistence_pct", "broker_score_avg",
            "broker_flow_trend", "top_accumulation", "top_distribution",
            "current_top_accumulation", "current_top_distribution", "data_quality_status",
            "initial_plan_status", "broker_history_note",
        }
        return {key: facts.get(key) for key in allowed if key in facts}

    def _request(self, facts: dict[str, Any], report_kind: str) -> dict[str, Any]:
        instruction = (
            "Anda adalah layer interpretasi ringkas SDE Swing untuk ACTUAL PORTFOLIO. "
            "Gunakan HANYA fakta JSON yang diberikan. Engine deterministic sudah menentukan action dan semua angka. "
            "JANGAN mengubah, menyarankan angka baru, menghitung ulang, atau menciptakan harga, P/L, TP, SL, target, "
            "broker nominal, broker state, technical state, sector, market, atau action. "
            "Jika top_accumulation/top_distribution tersedia, sebutkan maksimal 2 broker paling material beserta nominal "
            "PERSIS seperti string pada fakta. Jika nominal broker tidak tersedia, jangan menebak. "
            "Bedakan sinyal broker hari ini dengan histori terkonfirmasi; jika observation_count kurang dari 3, jelaskan "
            "bahwa histori masih pendek dan current broker hanya warning. "
            "Jika today_pulse_available=false, jangan menyebut baris historis terakhir sebagai broker hari ini; "
            "nyatakan TODAY 1D belum tersedia. "
            "Susun main_reason dengan urutan: driver management action, fakta teknikal/stop/milestone, broker context, "
            "apakah broker mendukung/konflik/netral, lalu langkah pengguna. Jelaskan konflik secara eksplisit; misalnya "
            "broker accumulation tidak mengaktifkan kembali thesis EXIT yang sudah invalid, dan distribution yang belum "
            "cukup terkonfirmasi tidak boleh mengubah HOLD. "
            "Jawab Bahasa Indonesia sangat ringkas dan actionable. Keluarkan JSON valid dengan tepat tiga key: "
            "main_reason, main_risk, execution_note. main_reason maksimal 2 kalimat; main_risk maksimal 1 kalimat; "
            "execution_note maksimal 1 kalimat dan harus konsisten dengan management_action."
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {
                    "role": "user",
                    "content": json.dumps({"report_kind": report_kind, "facts": facts}, ensure_ascii=False),
                },
            ],
            "temperature": self.temperature,
            "max_completion_tokens": 220,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "SDE-Swing/1.7 Portfolio-Report",
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
