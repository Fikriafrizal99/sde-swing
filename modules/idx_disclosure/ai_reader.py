"""Optional AI document reader for the isolated IDX disclosure watcher.

The reader is deliberately downstream of official IDX delivery. It never writes
into SDE scoring/decision state and it can fail without blocking the watcher.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests

from .models import DisclosureAttachment, IDXDisclosure


class AIReaderError(RuntimeError):
    """Retryable AI/document-reader failure."""


class AIReaderPermanentError(AIReaderError):
    """Non-retryable failure for the current disclosure."""


@dataclass(frozen=True, slots=True)
class AISummary:
    summary: str
    key_points: tuple[str, ...] = ()
    important_dates: tuple[str, ...] = ()
    important_values: tuple[str, ...] = ()
    related_parties: tuple[str, ...] = ()
    document_type: str = ""
    documents_read: tuple[str, ...] = ()
    model: str = ""
    source_hash: str = ""
    input_chars: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "summary": self.summary,
                "key_points": list(self.key_points),
                "important_dates": list(self.important_dates),
                "important_values": list(self.important_values),
                "related_parties": list(self.related_parties),
                "document_type": self.document_type,
                "documents_read": list(self.documents_read),
                "model": self.model,
                "source_hash": self.source_hash,
                "input_chars": self.input_chars,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, value: str) -> "AISummary":
        payload = json.loads(value)
        if not isinstance(payload, Mapping):
            raise ValueError("AI summary JSON must be an object")
        return cls(
            summary=str(payload.get("summary") or "").strip(),
            key_points=_string_tuple(payload.get("key_points")),
            important_dates=_string_tuple(payload.get("important_dates")),
            important_values=_string_tuple(payload.get("important_values")),
            related_parties=_string_tuple(payload.get("related_parties")),
            document_type=str(payload.get("document_type") or "").strip(),
            documents_read=_string_tuple(payload.get("documents_read")),
            model=str(payload.get("model") or "").strip(),
            source_hash=str(payload.get("source_hash") or "").strip(),
            input_chars=int(payload.get("input_chars") or 0),
        )


def _string_tuple(value: Any, *, limit: int = 8) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    result: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text:
            result.append(text[:500])
        if len(result) >= limit:
            break
    return tuple(result)


def load_environment_file(path: str | Path = ".env") -> bool:
    """Load a tiny dotenv subset without overriding explicit environment values."""

    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return False
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return False

    pattern = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        key, value = match.groups()
        if key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
    return True


def _clip(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def format_idx_ai_summary(disclosure: IDXDisclosure, result: AISummary) -> str:
    """Render a compact factual AI summary for the existing NEWS topic."""

    from html import escape

    parts = [
        "🤖 <b>RINGKASAN DOKUMEN IDX</b>",
        "",
        f"<b>{escape(disclosure.ticker)}</b> — {escape(_clip(disclosure.title, 350))}",
        "",
        "<b>Ringkasan</b>",
        escape(_clip(result.summary, 900)),
    ]

    def add_list(label: str, values: Sequence[str], *, max_items: int, item_limit: int) -> None:
        rows = [_clip(item, item_limit) for item in values[:max_items] if _clip(item, item_limit)]
        if rows:
            parts.extend(["", f"<b>{label}</b>"])
            parts.extend(f"• {escape(item)}" for item in rows)

    add_list("Poin penting", result.key_points, max_items=4, item_limit=180)
    add_list("Tanggal penting", result.important_dates, max_items=3, item_limit=160)
    add_list("Nilai / nominal penting", result.important_values, max_items=3, item_limit=160)
    add_list("Pihak terkait", result.related_parties, max_items=3, item_limit=160)

    if result.document_type:
        parts.extend(["", f"<b>Jenis dokumen:</b> {escape(_clip(result.document_type, 220))}"])
    if result.documents_read:
        names = ", ".join(_clip(item, 80) for item in result.documents_read[:3])
        parts.append(f"<b>Dokumen dibaca:</b> {escape(names)}")

    parts.extend(
        [
            "",
            "<i>Ringkasan AI dari dokumen resmi IDX. Bukan rekomendasi trading; verifikasi detail pada dokumen asli.</i>",
        ]
    )
    return "\n".join(parts).strip()


class GroqDisclosureAIReader:
    """Read selected IDX PDFs locally, then ask Groq for a factual summary."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        base_url: str = "https://api.groq.com/openai/v1/chat/completions",
        timeout_seconds: float = 35,
        max_output_tokens: int = 850,
        temperature: float = 0.0,
        max_pdf_bytes: int = 25_000_000,
        max_main_pages: int = 20,
        max_attachment_pages: int = 8,
        max_input_chars: int = 50_000,
        min_main_text_chars: int = 1000,
        read_attachments_if_main_insufficient: bool = True,
        max_fallback_attachments: int = 2,
        session: Any | None = None,
        pdf_text_extractor: Any | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise AIReaderPermanentError("GROQ_API_KEY_MISSING")
        self.model = str(model or "llama-3.3-70b-versatile").strip()
        self.base_url = str(base_url).strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_tokens = max(128, int(max_output_tokens))
        self.temperature = float(temperature)
        self.max_pdf_bytes = max(250_000, int(max_pdf_bytes))
        self.max_main_pages = max(1, int(max_main_pages))
        self.max_attachment_pages = max(1, int(max_attachment_pages))
        self.max_input_chars = max(4_000, int(max_input_chars))
        self.min_main_text_chars = max(100, int(min_main_text_chars))
        self.read_attachments_if_main_insufficient = bool(read_attachments_if_main_insufficient)
        self.max_fallback_attachments = max(0, int(max_fallback_attachments))
        self.session = session or requests.Session()
        self.pdf_text_extractor = pdf_text_extractor or self._extract_pdf_text

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        environ: Mapping[str, str] | None = None,
        session: Any | None = None,
        pdf_text_extractor: Any | None = None,
    ) -> "GroqDisclosureAIReader":
        env = os.environ if environ is None else environ
        key_name = str(config.get("api_key_env") or "GROQ_API_KEY").strip()
        api_key = str(env.get(key_name, "") or "").strip()
        return cls(
            api_key=api_key,
            model=str(env.get("GROQ_IDX_MODEL", "") or config.get("model") or "llama-3.3-70b-versatile"),
            base_url=str(config.get("base_url") or "https://api.groq.com/openai/v1/chat/completions"),
            timeout_seconds=float(config.get("request_timeout_seconds", 35)),
            max_output_tokens=int(config.get("max_output_tokens", 850)),
            temperature=float(config.get("temperature", 0)),
            max_pdf_bytes=int(config.get("max_pdf_bytes", 25_000_000)),
            max_main_pages=int(config.get("max_main_pages", 20)),
            max_attachment_pages=int(config.get("max_attachment_pages", 8)),
            max_input_chars=int(config.get("max_input_chars", 50_000)),
            min_main_text_chars=int(config.get("min_main_text_chars", 1000)),
            read_attachments_if_main_insufficient=bool(
                config.get("read_attachments_if_main_insufficient", True)
            ),
            max_fallback_attachments=int(config.get("max_fallback_attachments", 2)),
            session=session,
            pdf_text_extractor=pdf_text_extractor,
        )

    @staticmethod
    def dependency_available() -> tuple[bool, str]:
        try:
            import pypdf  # noqa: F401
        except ImportError:
            return False, "PYPDF_NOT_INSTALLED"
        return True, ""

    @staticmethod
    def _extract_pdf_text(data: bytes, max_pages: int) -> str:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise AIReaderPermanentError("PYPDF_NOT_INSTALLED") from exc

        try:
            reader = PdfReader(BytesIO(data))
        except Exception as exc:
            raise AIReaderPermanentError(f"PDF_PARSE_FAILED:{type(exc).__name__}") from exc

        parts: list[str] = []
        for page in reader.pages[:max_pages]:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts).strip()

    def _download(self, item: DisclosureAttachment) -> bytes:
        try:
            response = self.session.get(
                item.url,
                timeout=self.timeout_seconds,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/pdf,*/*;q=0.8",
                    "Referer": "https://www.idx.co.id/",
                },
            )
        except requests.RequestException as exc:
            raise AIReaderError(f"PDF_DOWNLOAD_FAILED:{type(exc).__name__}") from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            raise AIReaderError(f"PDF_HTTP_{status}")

        headers = getattr(response, "headers", {}) or {}
        content_length = str(headers.get("Content-Length", "") or "")
        if content_length.isdigit() and int(content_length) > self.max_pdf_bytes:
            raise AIReaderPermanentError("PDF_TOO_LARGE")

        data = bytes(getattr(response, "content", b"") or b"")
        if not data:
            raise AIReaderPermanentError("PDF_EMPTY")
        if len(data) > self.max_pdf_bytes:
            raise AIReaderPermanentError("PDF_TOO_LARGE")
        return data

    @staticmethod
    def _mentions_attachment(text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in ("terlampir", "lampiran", "sebagaimana terlampir"))

    def _documents(self, disclosure: IDXDisclosure) -> tuple[str, tuple[str, ...], str]:
        main = [item for item in disclosure.attachments if not item.is_attachment]
        attachments = [item for item in disclosure.attachments if item.is_attachment]

        if main:
            first = main[0]
        elif attachments:
            first = attachments.pop(0)
        else:
            raise AIReaderPermanentError("NO_DOCUMENT_LINK")

        text_parts: list[str] = []
        document_names: list[str] = []
        hash_builder = hashlib.sha256()

        def consume(item: DisclosureAttachment, pages: int) -> str:
            data = self._download(item)
            hash_builder.update(hashlib.sha256(data).digest())
            extracted = str(self.pdf_text_extractor(data, pages) or "").strip()
            if extracted:
                document_names.append(item.filename or "document.pdf")
                text_parts.append(f"### {item.filename or 'document.pdf'}\n{extracted}")
            return extracted

        main_text = consume(first, self.max_main_pages)
        should_fallback = (
            self.read_attachments_if_main_insufficient
            and attachments
            and (
                len(main_text) < self.min_main_text_chars
                or self._mentions_attachment(main_text)
            )
        )
        if should_fallback:
            for item in attachments[: self.max_fallback_attachments]:
                consume(item, self.max_attachment_pages)

        combined = "\n\n".join(text_parts).strip()
        if len(combined) < 100:
            raise AIReaderPermanentError("NO_EXTRACTABLE_PDF_TEXT")

        combined = combined[: self.max_input_chars]
        return combined, tuple(document_names), hash_builder.hexdigest()

    @staticmethod
    def _extract_json_text(text: str) -> Mapping[str, Any]:
        cleaned = str(text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start < 0 or end <= start:
                raise AIReaderError("GROQ_RESPONSE_NOT_JSON")
            try:
                payload = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as exc:
                raise AIReaderError("GROQ_RESPONSE_NOT_JSON") from exc
        if not isinstance(payload, Mapping):
            raise AIReaderError("GROQ_RESPONSE_NOT_OBJECT")
        return payload

    def _groq(self, disclosure: IDXDisclosure, document_text: str) -> Mapping[str, Any]:
        system = (
            "Anda adalah pembaca dokumen keterbukaan informasi resmi Bursa Efek Indonesia. "
            "Ringkas HANYA fakta yang tertulis di dokumen. Dilarang memberi sentimen, skor, "
            "prediksi harga, rekomendasi BUY/SELL/HOLD, atau keputusan trading. "
            "Jika fakta tidak disebutkan, jangan mengarang. Balas JSON valid saja dengan keys: "
            "summary, key_points, important_dates, important_values, related_parties, document_type. "
            "summary maksimal 3 kalimat; setiap array maksimal 5 item dan ringkas."
        )
        user = (
            f"Emiten: {disclosure.ticker}\n"
            f"Judul IDX: {disclosure.title}\n"
            f"No Pengumuman: {disclosure.announcement_no}\n\n"
            "TEKS DOKUMEN RESMI:\n"
            f"{document_text}"
        )
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
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
            raise AIReaderPermanentError(f"GROQ_HTTP_{status}")

        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except Exception as exc:
            raise AIReaderError("GROQ_RESPONSE_INVALID") from exc
        return self._extract_json_text(str(text))

    def summarize(self, disclosure: IDXDisclosure) -> AISummary:
        document_text, documents_read, source_hash = self._documents(disclosure)
        payload = self._groq(disclosure, document_text)
        summary = re.sub(r"\s+", " ", str(payload.get("summary") or "")).strip()
        if not summary:
            raise AIReaderError("GROQ_SUMMARY_EMPTY")
        return AISummary(
            summary=summary[:1400],
            key_points=_string_tuple(payload.get("key_points"), limit=5),
            important_dates=_string_tuple(payload.get("important_dates"), limit=5),
            important_values=_string_tuple(payload.get("important_values"), limit=5),
            related_parties=_string_tuple(payload.get("related_parties"), limit=5),
            document_type=str(payload.get("document_type") or "").strip()[:300],
            documents_read=documents_read,
            model=self.model,
            source_hash=source_hash,
            input_chars=len(document_text),
        )
