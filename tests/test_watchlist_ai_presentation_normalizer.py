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
        "broker_status": "ACCUMULATION",
        "broker_score": "68.0",
        "buyer_concentration": "0.2304",
        "seller_concentration": "0.0971",
        "bandar_buy_cost": "3698.5578",
        "distance_to_buy_cost": "0.8501",
        "broker_alignment": "ALIGNED_POSITIVE",
        "broker_period_type": "3D",
        "broker_period_start": "2026-08-19",
        "broker_period_end": "2026-08-21",
        "broker_trading_days": "3",
        "broker_period_coverage": "3/3",
        "today_pulse_available": True,
        "today_pulse_date": "2026-08-21",
        "today_pulse_status": "AVAILABLE",
        "today_pulse_net_flow": "15400000000.0",
        "today_pulse_direction": "ACCUMULATION",
        "today_pulse_buyer_concentration": "0.202",
        "today_pulse_seller_concentration": "0.121",
        # Legacy inputs can still appear in an old CSV, but the allow-list must
        # keep them out of Watchlist AI facts and therefore out of the provider.
        "multi_day_flow": "INSUFFICIENT_DATA",
        "flow_persistence": "STABLE_DOMINANCE",
        "market_regime": "BULL",
        "main_risk": "Setup batal jika harga menembus stop loss 3638.928571428572.",
    })


def test_presentation_context_humanizes_bbni_raw_facts():
    context = _bbni_context()
    presentation = build_presentation_context(context)
    rendered = json.dumps(presentation, ensure_ascii=False)

    assert "3693.571428571429" not in rendered
    assert "3638.928571428572" not in rendered
    assert "ENTRY_NOT_TRIGGERED" not in rendered
    assert "ALIGNED_POSITIVE" not in rendered

    assert presentation["trade plan"]["harga terakhir"] == "3.730"
    assert presentation["trade plan"]["area entry"] == "3.690–3.750"
    assert presentation["trade plan"]["stop loss"] == "3.640"
    assert presentation["trade plan"]["TP1"] == "3.860"
    assert set(presentation["broker"]) == {"PRIMARY", "TODAY 1D", "ALIGNMENT"}
    assert presentation["broker"]["PRIMARY"]["net flow"] == "Rp138,34 miliar"
    assert presentation["broker"]["PRIMARY"]["skor broker"] == "68/100"
    assert presentation["broker"]["PRIMARY"]["konsentrasi buyer"] == "23,04%"
    assert presentation["broker"]["PRIMARY"]["konsentrasi seller"] == "9,71%"
    assert presentation["broker"]["TODAY 1D"]["net flow"] == "Rp15,4 miliar"
    assert presentation["broker"]["ALIGNMENT"]["status"] == "PRIMARY dan TODAY 1D searah positif"
    assert presentation["hasil SDE"]["keputusan SDE"] == "BUY ON TRIGGER"
    assert "multi_day_flow" not in context["facts"]
    assert "flow_persistence" not in context["facts"]
    assert "STABLE_DOMINANCE" not in rendered


def test_humanized_numbers_remain_authorized_by_raw_plus_presentation_context():
    raw = _bbni_context()
    enriched = dict(raw)
    enriched["presentation_context"] = build_presentation_context(raw)
    payload = {
        "analysis": (
            "Menurut saya BBNI masih menarik karena harga 3.730 berada di area entry 3.690–3.750, "
            "technical score 91/100 dan RSI sekitar 62,05 masih mendukung. Broker PRIMARY juga positif "
            "dengan net flow Rp138,34 miliar dan buyer concentration 23,04%; pulse hari ini tetap "
            "positif sehingga flow broker saling mengonfirmasi. Entry tetap harus menunggu trigger SDE."
        ),
        "conclusion": "Setup menarik, tetapi tetap tunggu trigger dan hormati stop loss 3.640.",
    }

    analysis, conclusion = validate_response(payload, enriched)
    assert "Rp138,34 miliar" in analysis
    assert "3.640" in conclusion


def test_provider_receives_only_structured_presentation_context(monkeypatch, tmp_path):
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
    assert set(captured["broker"]) == {"PRIMARY", "TODAY 1D", "ALIGNMENT"}
    assert captured["broker"]["PRIMARY"]["net flow"] == "Rp138,34 miliar"
    assert captured["broker"]["PRIMARY"]["periode"] == "3D"
    assert captured["broker"]["TODAY 1D"]["net flow"] == "Rp15,4 miliar"
    assert captured["broker"]["ALIGNMENT"]["status"] == "PRIMARY dan TODAY 1D searah positif"
    assert "cakupan periode" not in captured["broker"]["PRIMARY"]
    assert "status raw PRIMARY" not in captured["broker"]["PRIMARY"]


def test_primary_1d_omits_duplicate_today_and_alignment():
    context = build_watchlist_context({
        "trade_date": "2026-08-21",
        "symbol": "AAA",
        "decision": "BUY CANDIDATE",
        "broker_period_type": "1D",
        "broker_period_start": "2026-08-21",
        "broker_period_end": "2026-08-21",
        "broker_net_flow": "1200000000",
        "broker_status": "ACCUMULATION",
        "today_pulse_available": False,
        "today_pulse_status": "NOT_APPLICABLE",
        # Even malformed duplicate TODAY values must not create a second block.
        "today_pulse_net_flow": "1200000000",
        "broker_alignment": "ALIGNED_POSITIVE",
    })
    service = PresentationWatchlistAIService({"watchlist_ai": {"enabled": True}})
    provider = service._with_presentation_context(context)["presentation_context"]

    assert set(provider["broker"]) == {"PRIMARY"}
    assert provider["broker"]["PRIMARY"]["periode"] == "1D"


def test_insufficient_primary_does_not_turn_sentinel_zero_into_zero_quality_score():
    context = build_watchlist_context({
        "trade_date": "2026-08-21",
        "symbol": "AAA",
        "decision": "BUY ON TRIGGER",
        "broker_period_type": "3D",
        "broker_status": "INSUFFICIENT_DATA",
        "broker_score": "0",
        "broker_net_flow": "1200000000",
        "today_pulse_available": False,
        "today_pulse_status": "NOT_AVAILABLE",
    })
    service = PresentationWatchlistAIService({"watchlist_ai": {"enabled": True}})
    provider = service._with_presentation_context(context)["presentation_context"]

    assert provider["broker"]["PRIMARY"]["status"] == "data belum cukup"
    assert "skor broker" not in provider["broker"]["PRIMARY"]
    assert set(provider["broker"]) == {"PRIMARY"}


def test_narrative_contract_uses_primary_today_without_forcing_conflict():
    instruction = PresentationWatchlistAIService._instruction()

    assert "PRIMARY adalah satu-satunya konteks broker otoritatif" in instruction
    assert "TODAY 1D, bila tersedia, hanya pulse sesi terakhir" in instruction
    assert "bukan score kedua" in instruction
    assert "Jangan membahas database rolling" in instruction
    assert "multi-day belum tersedia" in instruction
    assert "Hanya sebut konflik jika fakta yang diberikan memang berlawanan" in instruction
    assert "jangan mencari atau menciptakan konflik" in instruction
    assert "Jika PRIMARY dan TODAY 1D searah" in instruction
    assert "Mulai langsung dengan pandangan terhadap emitennya" in instruction
    assert "Menurut saya TINS" in instruction
    assert "Conclusion" in instruction
    assert "Jangan sekadar menulis ulang status SDE" in instruction


def test_prompt_contract_is_hashed_but_not_exposed_as_prompt_fact():
    service = PresentationWatchlistAIService({"watchlist_ai": {"enabled": True}})
    enriched = service._with_presentation_context(_bbni_context())

    assert enriched["prompt_contract"] == "WATCHLIST_AI_PRIMARY_TODAY_V2"
    assert "prompt_contract" not in enriched["presentation_context"]