from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .normalizer import build_presentation_context
from .service import WatchlistAIResult, WatchlistAIService as BaseWatchlistAIService


_PROMPT_CONTRACT = "WATCHLIST_AI_PRIMARY_TODAY_V2"
_ALIGNMENT_TEXT = {
    "ALIGNED_POSITIVE": "PRIMARY dan TODAY 1D searah positif",
    "ALIGNED_NEGATIVE": "PRIMARY dan TODAY 1D searah negatif",
    "POSITIVE_DIVERGENCE": "PRIMARY negatif, tetapi TODAY 1D positif",
    "NEGATIVE_DIVERGENCE": "PRIMARY positif, tetapi TODAY 1D negatif",
}
_PRIMARY_KEYS = (
    "arah",
    "status",
    "skor broker",
    "net flow",
    "konsentrasi buyer",
    "konsentrasi seller",
    "buy ratio",
    "sell ratio",
    "rata-rata biaya buyer",
    "jarak harga ke biaya buyer",
    "periode",
    "top buyers",
    "top sellers",
)
_TODAY_KEY_MAP = {
    "data broker hari ini": "status",
    "net flow hari ini": "net flow",
    "arah hari ini": "arah",
    "konsentrasi buyer hari ini": "konsentrasi buyer",
    "konsentrasi seller hari ini": "konsentrasi seller",
}
_UNAVAILABLE_PRIMARY_STATUS = {"data belum cukup", "data belum tersedia", "missing", "unknown"}


def _is_available_today(facts: Mapping[str, Any]) -> bool:
    status = str(facts.get("today_pulse_status") or "").strip().upper().replace(" ", "_")
    if status in {"AVAILABLE", "VALID", "CURRENT"}:
        return True
    if status in {"NOT_APPLICABLE", "NOT_AVAILABLE", "MISSING", ""}:
        return False
    value = facts.get("today_pulse_available")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _structured_broker_context(
    flat_broker: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Expose one PRIMARY broker view plus an optional TODAY pulse to providers.

    Coverage/raw provenance remains in the audit context but is intentionally
    omitted from the narrative prompt. Missing operational metadata is not a
    bearish broker signal and must not become a second source of interpretation.
    """
    primary: dict[str, Any] = {}
    for key in _PRIMARY_KEYS:
        value = flat_broker.get(key)
        if value not in (None, "", [], {}):
            primary[key] = value

    start = str(facts.get("broker_period_start") or "").strip()[:10]
    end = str(facts.get("broker_period_end") or "").strip()[:10]
    if start and end:
        primary["rentang"] = start if start == end else f"{start} s/d {end}"
    trading_days = facts.get("broker_trading_days")
    if trading_days not in (None, "", [], {}):
        primary["sesi perdagangan"] = trading_days

    # A sentinel 0/100 paired with an explicit insufficient/no-data status is
    # not presented as an analytical zero. The official raw fact remains in the
    # audit/validation context, but the provider sees "belum cukup" only once.
    status_text = str(primary.get("status") or "").strip().lower()
    if primary.get("skor broker") == "0/100" and status_text in _UNAVAILABLE_PRIMARY_STATUS:
        primary.pop("skor broker", None)

    broker: dict[str, Any] = {"PRIMARY": primary}
    if not _is_available_today(facts):
        return broker

    today: dict[str, Any] = {}
    pulse_date = str(facts.get("today_pulse_date") or "").strip()[:10]
    if pulse_date:
        today["tanggal"] = pulse_date
    for source_key, target_key in _TODAY_KEY_MAP.items():
        value = flat_broker.get(source_key)
        if value not in (None, "", [], {}):
            today[target_key] = value
    if today:
        broker["TODAY 1D"] = today

    alignment_code = str(facts.get("broker_alignment") or "").strip().upper().replace(" ", "_")
    alignment_text = _ALIGNMENT_TEXT.get(alignment_code)
    if alignment_text:
        broker["ALIGNMENT"] = {"status": alignment_text}
    return broker


class PresentationWatchlistAIService(BaseWatchlistAIService):
    """Watchlist AI service that exposes only humanized facts to providers.

    The raw context remains attached to the call for cache hashing, persistence,
    and post-response validation. Providers receive only the normalized
    presentation context, with broker facts separated into PRIMARY, optional
    TODAY 1D, and alignment blocks.
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

    @staticmethod
    def _provider_context(context: Mapping[str, Any]) -> dict[str, Any]:
        presentation = context.get("presentation_context")
        prompt_context = (
            dict(presentation)
            if isinstance(presentation, Mapping)
            else build_presentation_context(context)
        )
        flat_broker = prompt_context.get("broker")
        facts = context.get("facts")
        if isinstance(flat_broker, Mapping):
            prompt_context["broker"] = _structured_broker_context(
                flat_broker,
                facts if isinstance(facts, Mapping) else {},
            )
        return prompt_context

    def interpret(self, context: Mapping[str, Any]) -> WatchlistAIResult:
        return super().interpret(self._with_presentation_context(context))

    def _request(
        self,
        cfg: Mapping[str, Any],
        context: Mapping[str, Any],
        chart_path: Path | None,
    ) -> tuple[str, str, dict[str, Any]]:
        return super()._request(cfg, self._provider_context(context), chart_path)


__all__ = ["PresentationWatchlistAIService"]