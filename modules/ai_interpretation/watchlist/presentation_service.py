from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .normalizer import build_presentation_context
from .service import WatchlistAIResult, WatchlistAIService as BaseWatchlistAIService


_PROMPT_CONTRACT = "WATCHLIST_AI_PRIMARY_TODAY_V2"


class PresentationWatchlistAIService(BaseWatchlistAIService):
    """Watchlist AI service that exposes only humanized facts to providers.

    The raw context remains attached to the call for cache hashing, persistence,
    and post-response validation. Providers receive only the normalized
    presentation context. Broker facts are already normalized into one PRIMARY
    block, optional TODAY 1D pulse, and optional alignment block by
    ``build_presentation_context``.
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
            "material bagi setup dan hubungkan sebab-akibat secara natural. Bagian broker mempunyai "
            "kontrak khusus: PRIMARY adalah satu-satunya konteks broker otoritatif untuk periode yang "
            "dipilih; TODAY 1D, bila tersedia, hanya pulse sesi terakhir dan bukan score kedua atau "
            "pengganti PRIMARY; ALIGNMENT hanya ringkasan hubungan arah flow PRIMARY dengan TODAY 1D. "
            "Jangan membahas database rolling, persistence, histori broker internal, atau mengatakan data "
            "multi-day belum tersedia, karena konteks periodenya sudah diwakili oleh PRIMARY exact. Jika "
            "TODAY 1D tidak tersedia atau tidak berlaku, jangan perlakukan ketidakhadirannya sebagai "
            "kelemahan broker. Hanya sebut konflik jika fakta yang diberikan memang berlawanan; jangan "
            "mencari atau menciptakan konflik. Jika PRIMARY dan TODAY 1D searah, jelaskan sebagai "
            "konfirmasi; jika divergen, jelaskan perbedaannya tanpa membuat score baru. Status data yang "
            "belum cukup cukup disebut sekali dan tidak boleh dipecah menjadi beberapa kelemahan terpisah. "
            "Jangan membuka analisis dengan kalimat generik seperti 'Sebagai swing trader', 'Berdasarkan "
            "data', atau 'Secara keseluruhan'. Mulai langsung dengan pandangan terhadap emitennya, "
            "misalnya 'Menurut saya TINS...' atau bentuk natural setara. Hindari mengulang angka yang sama "
            "berkali-kali dan jangan mengulang keputusan SDE sebagai pengganti analisis. Jika keputusan "
            "adalah BUY ON TRIGGER, jelaskan bahwa entry belum aktif sampai trigger SDE terpenuhi dan "
            "terangkan faktor apa yang membuat Anda nyaman atau belum nyaman menunggu trigger tersebut. "
            "Tulis Bahasa Indonesia natural dalam 2-4 paragraf analisis, bukan daftar poin. Conclusion "
            "harus menjadi pendapat singkat AI tentang kualitas setup dan kenyamanan timing; sebut konflik "
            "hanya bila benar-benar ada pada fakta yang tersedia. Jangan sekadar menulis ulang status SDE "
            "seperti 'BUY ON TRIGGER: entry belum aktif'. Conclusion tidak boleh membuat plan atau level "
            "baru. Keluarkan JSON valid dengan tepat dua key: analysis dan conclusion."
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