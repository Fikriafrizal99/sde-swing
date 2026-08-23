from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .normalizer import build_presentation_context
from .service import WatchlistAIResult, WatchlistAIService as BaseWatchlistAIService


class PresentationWatchlistAIService(BaseWatchlistAIService):
    """Watchlist AI service that exposes only humanized facts to providers.

    The raw context remains attached to the call for cache hashing, persistence,
    and post-response validation. Providers receive only `presentation_context`.
    """

    @staticmethod
    def _instruction() -> str:
        return (
            "Anda adalah AI interpreter khusus Final Watchlist SDE Swing. Jelaskan perspektif Anda "
            "sebagai swing trader berdasarkan HANYA data presentation SDE yang diberikan. Data sudah "
            "dirapikan agar mudah dibaca; gunakan angka tersebut apa adanya dan jangan membuat angka, "
            "level, entry, stop loss, target, score, probability, atau keputusan baru. Keputusan SDE "
            "tetap otoritatif. Jangan membacakan semua data seperti tabel/JSON dan jangan menyebut nama "
            "field internal, token seperti ENTRY_NOT_TRIGGERED/ALIGNED_POSITIVE/INSUFFICIENT_DATA, atau "
            "snake_case. Pilih fakta yang paling relevan lalu jelaskan hubungan sebab-akibat antara "
            "technical, chart, trade plan, broker, multi-day flow, market context, dan risiko. Hindari "
            "mengulang angka yang sama berkali-kali. Jika keputusan adalah BUY ON TRIGGER, jelaskan "
            "bahwa entry belum aktif sampai trigger SDE terpenuhi. Tulis Bahasa Indonesia natural dalam "
            "2-4 paragraf analisis, bukan daftar poin. Kesimpulan harus singkat dan tidak membuat plan "
            "baru. Keluarkan JSON valid dengan tepat dua key: analysis dan conclusion."
        )

    @staticmethod
    def _with_presentation_context(context: Mapping[str, Any]) -> dict[str, Any]:
        enriched = dict(context)
        enriched["presentation_context"] = build_presentation_context(context)
        return enriched

    def interpret(self, context: Mapping[str, Any]) -> WatchlistAIResult:
        return super().interpret(self._with_presentation_context(context))

    def _request(
        self,
        cfg: Mapping[str, Any],
        context: Mapping[str, Any],
        chart_path: Path | None,
    ) -> tuple[str, str, dict[str, Any]]:
        presentation = context.get("presentation_context")
        prompt_context = (
            dict(presentation)
            if isinstance(presentation, Mapping)
            else build_presentation_context(context)
        )
        return super()._request(cfg, prompt_context, chart_path)


__all__ = ["PresentationWatchlistAIService"]
