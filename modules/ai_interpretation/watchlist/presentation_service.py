from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .normalizer import build_presentation_context
from .service import WatchlistAIResult, WatchlistAIService as BaseWatchlistAIService


_PROMPT_CONTRACT = "WATCHLIST_AI_NARRATIVE_CONFLICT_AWARE"


class PresentationWatchlistAIService(BaseWatchlistAIService):
    """Watchlist AI service that exposes only humanized facts to providers.

    The raw context remains attached to the call for cache hashing, persistence,
    and post-response validation. Providers receive only `presentation_context`.
    """

    @staticmethod
    def _instruction() -> str:
        return (
            "Anda adalah AI interpreter khusus Final Watchlist SDE Swing. Jelaskan perspektif Anda "
            "sebagai analis swing berdasarkan HANYA data presentation SDE yang diberikan. Data sudah "
            "dirapikan agar mudah dibaca; gunakan angka tersebut apa adanya dan jangan membuat angka, "
            "level, entry, stop loss, target, score, probability, atau keputusan baru. Keputusan dan "
            "trade plan SDE tetap otoritatif. Jangan membacakan semua data seperti tabel/JSON dan jangan "
            "menyebut nama field internal, token internal, atau snake_case. Pilih hanya fakta yang paling "
            "material bagi setup. Prioritaskan hubungan sebab-akibat: jelaskan data mana yang saling "
            "mengonfirmasi dan, yang lebih penting, konflik utama antar-data seperti technical kuat tetapi "
            "broker lemah, momentum baik tetapi trigger belum aktif, atau market mendukung sementara flow "
            "belum mengonfirmasi. Konflik material harus memengaruhi tingkat kehati-hatian dalam narasi. "
            "Jangan membuka analisis dengan kalimat generik seperti 'Sebagai swing trader', 'Berdasarkan "
            "data', atau 'Secara keseluruhan'. Mulai langsung dengan pandangan terhadap emitennya, "
            "misalnya 'Menurut saya TINS...' atau bentuk natural setara. Hindari mengulang angka yang sama "
            "berkali-kali dan jangan mengulang keputusan SDE sebagai pengganti analisis. Jika keputusan "
            "adalah BUY ON TRIGGER, jelaskan bahwa entry belum aktif sampai trigger SDE terpenuhi dan "
            "terangkan faktor apa yang membuat Anda nyaman atau belum nyaman menunggu trigger tersebut. "
            "Tulis Bahasa Indonesia natural dalam 2-4 paragraf analisis, bukan daftar poin. Conclusion "
            "harus menjadi pendapat singkat AI tentang kualitas setup, konflik utama, dan kenyamanan timing "
            "berdasarkan fakta yang tersedia; jangan sekadar menulis ulang status SDE seperti 'BUY ON "
            "TRIGGER: entry belum aktif'. Conclusion tidak boleh membuat plan atau level baru. Keluarkan "
            "JSON valid dengan tepat dua key: analysis dan conclusion."
        )

    @staticmethod
    def _with_presentation_context(context: Mapping[str, Any]) -> dict[str, Any]:
        enriched = dict(context)
        enriched["presentation_context"] = build_presentation_context(context)
        # Cache/result signatures are based on the full enriched context. Keep
        # the narrative contract at the root so prompt changes invalidate old
        # cached prose without exposing this implementation marker to providers.
        enriched["prompt_contract"] = _PROMPT_CONTRACT
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
