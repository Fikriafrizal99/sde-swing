"""Structured-output hardening for the isolated IDX Groq reader.

This module keeps the base document reader unchanged while enforcing Groq JSON
mode for the configured GPT-OSS model. It is installed by the package init so
all existing runner/watcher imports keep the same public API.
"""

from __future__ import annotations

from typing import Any, Mapping

import requests

from .ai_reader import AIReaderError, AIReaderPermanentError, GroqDisclosureAIReader
from .models import IDXDisclosure


def _structured_groq(
    self: GroqDisclosureAIReader,
    disclosure: IDXDisclosure,
    document_text: str,
) -> Mapping[str, Any]:
    system = (
        "Anda adalah pembaca dokumen keterbukaan informasi resmi Bursa Efek Indonesia. "
        "Ringkas HANYA fakta yang tertulis di dokumen. Dilarang memberi sentimen, skor, "
        "prediksi harga, rekomendasi BUY/SELL/HOLD, atau keputusan trading. "
        "Jika fakta tidak disebutkan, jangan mengarang. Balas JSON valid saja dengan keys: "
        "summary, key_points, important_dates, important_values, related_parties, document_type. "
        "summary maksimal 2 kalimat dan fokus pada inti kejadian. key_points maksimal 4 item, "
        "tanpa mengulang kalimat summary. important_dates maksimal 4 item dan WAJIB prioritaskan "
        "tanggal pelaksanaan corporate action/RUPS/event, record date/DPS, deadline/pemanggilan, "
        "tanggal efektif atau pembayaran. Tanggal pembuatan dokumen hanya dimasukkan jika memang "
        "material dan masih ada slot. important_values hanya nominal/rasio material. "
        "related_parties hanya pihak yang material terhadap kejadian; jangan masukkan corporate "
        "secretary, KSEI, IDX, atau regulator rutin kecuali mereka merupakan pihak transaksi."
    )
    user = (
        f"Emiten: {disclosure.ticker}\n"
        f"Judul IDX: {disclosure.title}\n"
        f"No Pengumuman: {disclosure.announcement_no}\n\n"
        "TEKS DOKUMEN RESMI:\n"
        f"{document_text}"
    )

    payload: dict[str, Any] = {
        "model": self.model,
        "temperature": self.temperature,
        "max_tokens": self.max_output_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    # Groq requires parsed/hidden reasoning when GPT-OSS is combined with JSON
    # mode. Low effort is enough for factual document extraction and controls
    # latency/cost.
    if str(self.model).startswith("openai/gpt-oss-"):
        payload["reasoning_format"] = "hidden"
        payload["reasoning_effort"] = "low"

    try:
        response = self.session.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_seconds,
        )
    except requests.RequestException as exc:
        raise AIReaderError(f"GROQ_REQUEST_FAILED:{type(exc).__name__}") from exc

    status = int(getattr(response, "status_code", 0) or 0)
    if status in {401, 403}:
        raise AIReaderPermanentError(f"GROQ_AUTH_HTTP_{status}")
    if status == 429 or status >= 500:
        raise AIReaderError(f"GROQ_HTTP_{status}")
    if status != 200:
        detail = ""
        try:
            detail = str(response.json())[:500]
        except Exception:
            detail = str(getattr(response, "text", ""))[:500]
        raise AIReaderPermanentError(f"GROQ_HTTP_{status}:{detail}")

    try:
        body = response.json()
        text = body["choices"][0]["message"]["content"]
    except Exception as exc:
        raise AIReaderError("GROQ_RESPONSE_INVALID") from exc
    return self._extract_json_text(str(text))


def install_groq_json_mode_patch() -> None:
    """Install JSON-mode request handling without changing the public class."""

    if getattr(GroqDisclosureAIReader, "_idx_json_mode_installed", False):
        return
    GroqDisclosureAIReader._groq = _structured_groq  # type: ignore[method-assign]
    GroqDisclosureAIReader._idx_json_mode_installed = True  # type: ignore[attr-defined]
