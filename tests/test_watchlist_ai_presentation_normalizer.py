from __future__ import annotations

import json

from modules.ai_interpretation.watchlist.normalizer import build_presentation_context
from modules.ai_interpretation.watchlist.presentation_service import PresentationWatchlistAIService
from modules.ai_interpretation.watchlist.service import WatchlistAIService as BaseWatchlistAIService, build_watchlist_context
from modules.ai_interpretation.watchlist.validator import validate_response


def _bbni_context() -> dict:
    return build_watchlist_context({
        "trade_date": "2026-08-21",
        "symbol": "BBNI",
        "decision": "BUY ON TRIGGER",
        "confidence": "88.0",
        "setup": "BREAKOUT",
        "trend": "BULLISH",
        "technical_state": "BULLISH",
        "technical_score": "91.0",
        "rsi": "62.049733",
        "last_price": "3730.0",
        "entry_low": "3693.571428571429",
        "entry_high": "3748.214285714286",
        "stop_loss": "3638.928571428572",
        "active_stop_loss": "3638.928571428572",
        "target_1": "3857.5",
        "target_2": "3930.0",
        "risk_reward": "1.6633986928104578",
        "support": "3450.0",
        "resistance": "3850.0",
        "waiting_triggers": '["ENTRY_NOT_TRIGGERED"]',
        "broker_net_flow": "138343108000.0",
        "broker_direction": "ACCUMULATION",
        "buyer_concentration": "0.2304",
        "seller_concentration": "0.0971",
        "bandar_buy_cost": "3698.5578",
        "distance_to_buy_cost": "0.8501",
        "broker_alignment": "ALIGNED_POSITIVE",
        "broker_status": "INSUFFICIENT_DATA",
        "multi_day_flow": "INSUFFICIENT_DATA",
        "broker_period_type": "3D",
        "broker_period_coverage": "3/3",
        "today_pulse_status": "AVAILABLE",
        "market_regime": "BULL",
        "main_risk": "Setup batal jika harga menembus stop loss 3638.928571428572.",
    })


def test_presentation_context_humanizes_bbni_raw_facts():
    presentation = build_presentation_context(_bbni_context())
    rendered = json.dumps(presentation, ensure_ascii=False)

    assert "3693.571428571429" not in rendered
    assert "3638.928571428572" not in rendered
    assert "ENTRY_NOT_TRIGGERED" not in rendered
    assert "ALIGNED_POSITIVE" not in rendered
    assert "INSUFFICIENT_DATA" not in rendered

    assert presentation["trade plan"]["harga terakhir"] == "3.730"
    assert presentation["trade plan"]["area entry"] == "3.690–3.750"
    assert presentation["trade plan"]["stop loss"] == "3.640"
    assert presentation["trade plan"]["TP1"] == "3.860"
    assert presentation["broker"]["net flow"] == "Rp138,34 miliar"
    assert presentation["broker"]["konsentrasi buyer"] == "23,04%"
    assert presentation["broker"]["konsentrasi seller"] == "9,71%"
    assert presentation["broker"]["keselarasan"] == "broker searah positif"
    assert presentation["broker"]["status"] == "data belum cukup"
    assert presentation["hasil SDE"]["keputusan SDE"] == "BUY ON TRIGGER"


def test_humanized_numbers_remain_authorized_by_raw_plus_presentation_context():
    raw = _bbni_context()
    enriched = dict(raw)
    enriched["presentation_context"] = build_presentation_context(raw)
    payload = {
        "analysis": (
            "Menurut saya BBNI masih menarik karena harga 3.730 berada di area entry 3.690–3.750, "
            "technical score 91/100 dan RSI sekitar 62,05 masih mendukung. Broker juga positif "
            "dengan net flow Rp138,34 miliar dan buyer concentration 23,04%, tetapi data multi-day "
            "masih belum cukup sehingga entry tetap harus menunggu trigger SDE."
        ),
        "conclusion": "Setup menarik, tetapi tetap tunggu trigger dan hormati stop loss 3.640.",
    }

    analysis, conclusion = validate_response(payload, enriched)
    assert "Rp138,34 miliar" in analysis
    assert "3.640" in conclusion


def test_provider_receives_only_presentation_context(monkeypatch, tmp_path):
    captured = {}

    def fake_base_request(self, cfg, context, chart_path):
        captured.update(context)
        return "MOCK", "mock-model", {
            "analysis": "Analisis dummy yang cukup panjang untuk memastikan jalur presentation provider dapat diuji tanpa akses jaringan sama sekali.",
            "conclusion": "Kesimpulan dummy.",
        }

    monkeypatch.setattr(BaseWatchlistAIService, "_request", fake_base_request)
    service = PresentationWatchlistAIService({
        "watchlist_ai": {
            "enabled": True,
            "cache_enabled": False,
            "cache_root": str(tmp_path / "cache"),
            "output_root": str(tmp_path / "output"),
        }
    })
    enriched = service._with_presentation_context(_bbni_context())
    service._request({}, enriched, None)

    assert "facts" not in captured
    assert "presentation_context" not in captured
    assert "prompt_contract" not in captured
    assert "trade plan" in captured
    assert captured["trade plan"]["area entry"] == "3.690–3.750"


def test_narrative_contract_prioritizes_conflict_and_ai_judgment():
    instruction = PresentationWatchlistAIService._instruction()

    assert "konflik utama antar-data" in instruction
    assert "technical kuat tetapi broker lemah" in instruction
    assert "Konflik material harus memengaruhi tingkat kehati-hatian" in instruction
    assert "Mulai langsung dengan pandangan terhadap emitennya" in instruction
    assert "Menurut saya TINS" in instruction
    assert "Conclusion harus menjadi pendapat singkat AI" in instruction
    assert "jangan sekadar menulis ulang status SDE" in instruction


def test_prompt_contract_is_hashed_but_not_exposed_as_prompt_fact():
    service = PresentationWatchlistAIService({"watchlist_ai": {"enabled": True}})
    enriched = service._with_presentation_context(_bbni_context())

    assert enriched["prompt_contract"] == "WATCHLIST_AI_NARRATIVE_CONFLICT_AWARE"
    assert "prompt_contract" not in enriched["presentation_context"]
