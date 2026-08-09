#!/usr/bin/env python3
"""History-aware runtime for actual OPEN portfolio positions.

The stable Position Management decision rules remain in
``position_management_engine.py``. This wrapper enriches the portfolio-only
broker context, applies optional manual initial levels only to blank plan fields,
and builds a compact presentation report. Discovery, Broker Fusion, Decision
Engine, Exit Engine, and Final Watchlist are not invoked or modified here.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.job_runner.delivery import deliver
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import load_context, resolve
from modules.portfolio import position_management_engine as base
from modules.portfolio.broker_history_context import (
    build_position_broker_context,
    ensure_schema,
    persist_position_broker_context,
    sync_latest_broker_summary,
)
from modules.portfolio.manual_position_plan import apply_manual_plan_to_initial_plan
from modules.portfolio.portfolio_report_interpreter import PortfolioGroqInterpreter


def fmt_money(value: Any) -> str:
    number = base.as_float(value)
    if number is None:
        return "-"
    absolute = abs(number)
    sign = "+" if number > 0 else "-" if number < 0 else ""
    if absolute >= 1_000_000_000_000:
        return f"{sign}Rp{absolute / 1_000_000_000_000:.2f} T".replace(".", ",")
    if absolute >= 1_000_000_000:
        return f"{sign}Rp{absolute / 1_000_000_000:.2f} M".replace(".", ",")
    if absolute >= 1_000_000:
        return f"{sign}Rp{absolute / 1_000_000:.2f} jt".replace(".", ",")
    return f"{sign}Rp{absolute:,.0f}".replace(",", ".")


def _fallback_context_from_latest(broker_path: Path, symbol: str, analysis_date: str) -> dict[str, Any]:
    latest = base.load_broker_row(broker_path, symbol)
    state = str(latest.get("state") or "UNAVAILABLE").upper()
    return {
        "symbol": symbol,
        "buy_date": "",
        "analysis_date": analysis_date,
        "broker_data_date": "",
        "observation_count": 0,
        "current_state": state,
        "current_score": latest.get("score"),
        "current_confidence": None,
        "current_net_flow": latest.get("net_flow"),
        # Latest-only broker evidence is warning-only. It must not change the
        # deterministic management action without historical confirmation.
        "effective_state": "UNAVAILABLE" if state == "UNAVAILABLE" else "NEUTRAL",
        "effective_reason": "historical Broker Summary belum cukup; latest broker state hanya warning",
        "3D": {"context": "INSUFFICIENT_DATA", "observation_count": 0, "required_observations": 3},
        "5D": {"context": "INSUFFICIENT_DATA", "observation_count": 0, "required_observations": 5},
        "7D": {"context": "INSUFFICIENT_DATA", "observation_count": 0, "required_observations": 7},
        "since_entry": {
            "context": "UNAVAILABLE",
            "observation_count": 0,
            "net_flow": None,
            "avg_net_flow": None,
            "score_avg": None,
            "buy_days": 0,
            "sell_days": 0,
            "accumulation_days": 0,
            "distribution_days": 0,
            "persistence_pct": None,
        },
        "broker_score_trend": "INSUFFICIENT_DATA",
        "flow_trend": "INSUFFICIENT_DATA",
        "top_accumulation": [],
        "top_distribution": [],
        "current_top_accumulation": [],
        "current_top_distribution": [],
        "actor_data_status": "UNAVAILABLE",
    }


def _broker_reason(context: dict[str, Any]) -> str:
    since = context.get("since_entry") or {}
    d3 = context.get("3D") or {}
    d5 = context.get("5D") or {}
    d7 = context.get("7D") or {}
    parts = [
        f"Broker history: current={context.get('current_state', 'UNAVAILABLE')}",
        f"3D={d3.get('context', 'UNAVAILABLE')}({d3.get('observation_count', 0)}/{d3.get('required_observations', 3)})",
        f"5D={d5.get('context', 'UNAVAILABLE')}({d5.get('observation_count', 0)}/{d5.get('required_observations', 5)})",
        f"7D={d7.get('context', 'UNAVAILABLE')}({d7.get('observation_count', 0)}/{d7.get('required_observations', 7)})",
        f"since-entry={since.get('context', 'UNAVAILABLE')}",
        f"flow-trend={context.get('flow_trend', 'INSUFFICIENT_DATA')}",
        f"effective={context.get('effective_state', 'UNAVAILABLE')}",
    ]
    reason = str(context.get("effective_reason") or "").strip()
    return "; ".join(parts) + (f" ({reason})." if reason else ".")


def analyze_position(
    conn,
    position,
    *,
    analysis_date: str,
    historical_dir: Path,
    broker_path: Path,
    sector_metadata_path: Path,
    sector_rotation_path: Path,
    market: str,
) -> dict[str, Any]:
    symbol = base.normalize_symbol(position["symbol"])
    plan = base.get_or_create_initial_plan(conn, position)
    manual_applied = apply_manual_plan_to_initial_plan(conn, str(position["position_id"]))
    if manual_applied:
        plan = manual_applied
    previous = base.previous_state(conn, position["position_id"])
    tech = base.technical_snapshot(historical_dir, symbol, plan["buy_date"])

    try:
        broker_context = build_position_broker_context(
            conn,
            symbol=symbol,
            buy_date=plan["buy_date"],
            analysis_date=analysis_date,
        )
    except Exception as exc:
        broker_context = _fallback_context_from_latest(broker_path, symbol, analysis_date)
        broker_context["effective_reason"] = (
            f"broker history query gagal ({type(exc).__name__}); latest broker state hanya warning"
        )

    if not int(broker_context.get("observation_count") or 0):
        fallback = _fallback_context_from_latest(broker_path, symbol, analysis_date)
        fallback["buy_date"] = plan["buy_date"]
        broker_context = fallback

    effective_broker = {
        "state": broker_context.get("effective_state", "UNAVAILABLE"),
        "score": broker_context.get("current_score"),
        "net_flow": broker_context.get("current_net_flow"),
        "raw_signal": broker_context.get("current_state", ""),
    }
    sector_bucket, sector_name = base.sector_state(
        symbol, sector_metadata_path, sector_rotation_path
    )
    decision = base.choose_management(
        plan=plan,
        tech=tech,
        broker=effective_broker,
        sector=sector_bucket,
        market=market,
        previous=previous,
    )
    decision["reason"] = (decision.get("reason") or "").strip() + " " + _broker_reason(broker_context)

    current = base.as_float(tech.get("current_price"))
    buy_price = base.as_float(plan.get("buy_price"))
    pnl_pct = ((current / buy_price - 1.0) * 100.0) if current is not None and buy_price else None

    # PROTECT_PROFIT is semantically reserved for an already profitable
    # pre-target position. The stop logic from the stable engine is preserved;
    # only the portfolio-facing action label is clarified when P/L <= 0.
    if (
        decision.get("action") == "PROTECT_PROFIT"
        and decision.get("milestone") == "PRE_TARGET"
        and pnl_pct is not None
        and pnl_pct <= 0
    ):
        decision["action"] = "TIGHTEN_RISK"
        decision["reason"] = "Risiko meningkat sebelum posisi menghasilkan profit; batas risiko diperketat. " + decision["reason"]

    data_date = base.norm_text(tech.get("data_date"))
    data_quality = decision["data_quality_status"]
    if data_date and analysis_date and data_date != analysis_date:
        data_quality = "STALE_PRICE_DATA"
        decision["action"] = "REVIEW_DATA"
        decision["reason"] = (
            f"Data harga terakhir {data_date}, berbeda dari analysis date {analysis_date}. "
            + decision["reason"]
        )

    broker_data_date = str(broker_context.get("broker_data_date") or "").strip()
    if (
        broker_data_date
        and analysis_date
        and broker_data_date != analysis_date
        and data_quality not in {"STALE_PRICE_DATA", "INVALID_TECHNICAL_DATA"}
    ):
        data_quality = "VALID_WITH_BROKER_WARNING"
        decision["reason"] += f" Broker Summary terakhir {broker_data_date}, bukan {analysis_date}."

    since = broker_context.get("since_entry") or {}
    d3 = broker_context.get("3D") or {}
    d5 = broker_context.get("5D") or {}
    d7 = broker_context.get("7D") or {}
    result = {
        "position_id": position["position_id"],
        "symbol": symbol,
        "analysis_date": analysis_date,
        "data_date": data_date,
        "buy_date": plan["buy_date"],
        "buy_price": buy_price,
        "current_price": current,
        "pnl_pct": pnl_pct,
        "initial_stop_loss": base.as_float(plan.get("initial_stop_loss")),
        "initial_tp1": base.as_float(plan.get("initial_tp1")),
        "initial_tp2": base.as_float(plan.get("initial_tp2")),
        "initial_setup": base.norm_text(plan.get("initial_setup")),
        "linked_signal_id": base.norm_text(plan.get("linked_signal_id")),
        "milestone": decision["milestone"],
        "technical_state": base.norm_text(tech.get("technical_regime"), "UNAVAILABLE"),
        "broker_state": str(broker_context.get("effective_state") or "UNAVAILABLE"),
        "broker_score": broker_context.get("current_score"),
        "broker_current_state": broker_context.get("current_state"),
        "broker_effective_state": broker_context.get("effective_state"),
        "broker_data_date": broker_data_date,
        "broker_observation_count": int(broker_context.get("observation_count") or 0),
        "broker_context_3d": d3.get("context"),
        "broker_context_3d_observations": int(d3.get("observation_count") or 0),
        "broker_context_5d": d5.get("context"),
        "broker_context_5d_observations": int(d5.get("observation_count") or 0),
        "broker_context_7d": d7.get("context"),
        "broker_context_7d_observations": int(d7.get("observation_count") or 0),
        "broker_context_since_entry": since.get("context"),
        "broker_history_status": since.get("coverage_status"),
        "broker_net_flow_since_entry": since.get("net_flow"),
        "broker_avg_daily_net_flow": since.get("avg_net_flow"),
        "broker_buy_days": int(since.get("buy_days") or 0),
        "broker_sell_days": int(since.get("sell_days") or 0),
        "broker_accumulation_days": int(since.get("accumulation_days") or 0),
        "broker_distribution_days": int(since.get("distribution_days") or 0),
        "broker_persistence_pct": since.get("persistence_pct"),
        "broker_score_avg": since.get("score_avg"),
        "broker_score_trend": broker_context.get("broker_score_trend"),
        "broker_flow_trend": broker_context.get("flow_trend"),
        "broker_top_accumulation": broker_context.get("top_accumulation") or [],
        "broker_top_distribution": broker_context.get("top_distribution") or [],
        "broker_current_top_accumulation": broker_context.get("current_top_accumulation") or [],
        "broker_current_top_distribution": broker_context.get("current_top_distribution") or [],
        "broker_actor_data_status": broker_context.get("actor_data_status"),
        "sector_state": sector_bucket,
        "sector_name": sector_name,
        "market_state": market,
        "management_action": decision["action"],
        "active_stop_loss": decision["active_stop_loss"],
        "extended_target": decision["extended_target"],
        "reason": decision["reason"].strip(),
        "data_quality_status": data_quality,
    }

    base.persist_result(conn, result, previous)
    try:
        persist_position_broker_context(
            conn, position_id=position["position_id"], context=broker_context
        )
    except Exception as exc:
        print(
            f"[WARNING] Broker context history persist gagal {symbol}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    return result


def _formatted_actors(items: list[dict[str, Any]], limit: int = 2) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for item in (items or [])[:limit]:
        broker = str(item.get("broker") or "").strip().upper()
        value = base.as_float(item.get("net_value"))
        if broker and value is not None:
            output.append({"broker": broker, "value": fmt_money(value)})
    return output


def _actor_phrase(items: list[dict[str, Any]]) -> str:
    actors = _formatted_actors(items)
    if not actors:
        return ""
    return " dan ".join(f"{item['broker']} {item['value']}" for item in actors)


def _engine_reason_text(row: dict[str, Any]) -> str:
    """Extract the deterministic action reason without the appended broker trace."""
    reason = str(row.get("reason") or "").strip()
    if not reason:
        return ""
    return reason.split("Broker history:", 1)[0].strip()


def _broker_conclusion(row: dict[str, Any]) -> str:
    """Progressive broker explanation for Telegram; never changes engine state.

    1D and 2D are useful evidence immediately. The existing effective broker
    state remains untouched and still requires the stable 3D confirmation
    rules before it can affect Position Management.
    """
    observations = int(row.get("broker_observation_count") or 0)
    current = str(row.get("broker_current_state") or "UNAVAILABLE").upper()
    since = str(row.get("broker_context_since_entry") or "UNAVAILABLE").upper()
    d3 = str(row.get("broker_context_3d") or "INSUFFICIENT_DATA").upper()
    d5 = str(row.get("broker_context_5d") or "INSUFFICIENT_DATA").upper()
    d7 = str(row.get("broker_context_7d") or "INSUFFICIENT_DATA").upper()

    current_accum = _actor_phrase(row.get("broker_current_top_accumulation") or [])
    current_dist = _actor_phrase(row.get("broker_current_top_distribution") or [])
    since_accum = _actor_phrase(row.get("broker_top_accumulation") or [])
    since_dist = _actor_phrase(row.get("broker_top_distribution") or [])

    if observations <= 0:
        return "Broker history belum tersedia; broker tidak dipakai sebagai pemicu action."

    if observations == 1:
        if current == "DISTRIBUTION":
            actors = f", terutama {current_dist or since_dist}" if (current_dist or since_dist) else ""
            return f"Distribusi muncul pada 1 sesi terbaru{actors}; tekanan jual terlihat tetapi persistence belum terkonfirmasi."
        if current == "ACCUMULATION":
            actors = f", terutama {current_accum or since_accum}" if (current_accum or since_accum) else ""
            return f"Akumulasi muncul pada 1 sesi terbaru{actors}; dukungan beli terlihat tetapi persistence belum terkonfirmasi."
        return "Flow broker 1 sesi masih netral/campuran; belum ada arah yang cukup konsisten."

    if observations == 2:
        if since == "DISTRIBUTION" or current == "DISTRIBUTION":
            actors = f", didominasi {since_dist or current_dist}" if (since_dist or current_dist) else ""
            qualifier = "mendominasi" if since == "DISTRIBUTION" else "muncul"
            return f"Distribusi {qualifier} dalam 2 sesi sejak entry{actors}; tekanan jual mulai terbaca, tetapi belum menjadi konfirmasi 3D untuk action engine."
        if since == "ACCUMULATION" or current == "ACCUMULATION":
            actors = f", didominasi {since_accum or current_accum}" if (since_accum or current_accum) else ""
            qualifier = "mendominasi" if since == "ACCUMULATION" else "muncul"
            return f"Akumulasi {qualifier} dalam 2 sesi sejak entry{actors}; dukungan beli mulai terbaca, tetapi belum menjadi konfirmasi 3D untuk action engine."
        mixed_parts: list[str] = []
        if since_dist:
            mixed_parts.append(f"distribusi {since_dist}")
        if since_accum:
            mixed_parts.append(f"akumulasi {since_accum}")
        detail = "; " + " sementara ".join(mixed_parts) if mixed_parts else ""
        return f"Flow broker 2 sesi masih campuran{detail}; belum ada persistence yang cukup untuk mengubah action engine."

    window = "3D"
    context = d3
    if observations >= 7 and d7 not in {"", "INSUFFICIENT_DATA", "UNAVAILABLE"}:
        window, context = "7D", d7
    elif observations >= 5 and d5 not in {"", "INSUFFICIENT_DATA", "UNAVAILABLE"}:
        window, context = "5D", d5

    if context == "DISTRIBUTION":
        actors = f"; sejak entry distributor utama {since_dist}" if since_dist else ""
        return f"Context broker {window} DISTRIBUTION{actors}; tekanan jual sudah memiliki persistence pada window yang tersedia."
    if context == "ACCUMULATION":
        actors = f"; sejak entry accumulator utama {since_accum}" if since_accum else ""
        return f"Context broker {window} ACCUMULATION{actors}; dukungan beli sudah memiliki persistence pada window yang tersedia."

    parts: list[str] = []
    if since_dist:
        parts.append(f"distributor utama {since_dist}")
    if since_accum:
        parts.append(f"accumulator utama {since_accum}")
    detail = "; " + ", sedangkan ".join(parts) if parts else ""
    return f"Context broker {window} masih {context or 'NEUTRAL'}{detail}; flow belum menunjukkan dominasi satu arah yang kuat."


def _deterministic_interpretation(row: dict[str, Any]) -> dict[str, str]:
    observations = int(row.get("broker_observation_count") or 0)
    current_broker = str(row.get("broker_current_state") or "UNAVAILABLE").upper()
    broker_sentence = _broker_conclusion(row)
    engine_reason = _engine_reason_text(row)
    action = str(row.get("management_action") or "HOLD").upper()
    technical = str(row.get("technical_state") or "UNAVAILABLE").upper()
    sector = str(row.get("sector_state") or "UNAVAILABLE").upper()
    market = str(row.get("market_state") or "UNAVAILABLE").upper()

    if engine_reason:
        action_sentence = f"Keputusan {action.replace('_', ' ')} mengikuti engine: {engine_reason}"
    else:
        action_sentence = (
            f"Keputusan {action.replace('_', ' ')} tetap mengikuti engine dengan technical {technical}, "
            f"sektor {sector}, dan market {market}."
        )
    main_reason = f"{broker_sentence} {action_sentence}".strip()

    plan_missing = not all(row.get(key) is not None for key in ("initial_stop_loss", "initial_tp1", "initial_tp2"))
    if row.get("data_quality_status") not in {"VALID", "VALID_WITH_BROKER_WARNING"}:
        main_risk = f"Kualitas data {row.get('data_quality_status')}; jangan mengambil keputusan baru dari data yang belum valid."
    elif plan_missing:
        main_risk = "Initial TP/SL belum lengkap; batas risiko/target awal perlu dilengkapi jika posisi berasal dari luar rekomendasi mesin."
    elif current_broker == "DISTRIBUTION" and observations < 3:
        main_risk = "Tekanan distribusi sudah terlihat; pantau apakah arah dan broker dominan yang sama berlanjut, tetapi 1–2 sesi tidak mengubah action sendirian."
    else:
        main_risk = "Pantau perubahan teknikal, broker terkonfirmasi, dan active stop sebagai batas risiko."

    notes = {
        "HOLD_STRONG": "Pertahankan posisi sesuai plan; tidak perlu menambah posisi hanya karena report ini.",
        "HOLD": "Pertahankan posisi dan pantau konfirmasi berikutnya.",
        "TIGHTEN_RISK": "Pertahankan posisi dengan batas risiko lebih ketat.",
        "PROTECT_PROFIT": "Pertahankan posisi sambil mengunci profit dengan active stop.",
        "HOLD_AFTER_TP1": "Pertahankan sisa posisi setelah TP1 dengan proteksi yang sudah dinaikkan.",
        "HOLD_AFTER_TP2": "Pertahankan sisa posisi hanya selama continuation tetap valid.",
        "EXIT": "Prioritaskan keluar dari posisi sesuai aturan management.",
        "REVIEW_DATA": "Tunggu data valid sebelum mengubah posisi berdasarkan report ini.",
    }
    return {
        "main_reason": main_reason,
        "main_risk": main_risk,
        "execution_note": notes.get(action, "Ikuti action Position Management dan batas risiko yang tersedia."),
    }


def _ai_facts(row: dict[str, Any]) -> dict[str, Any]:
    observations = int(row.get("broker_observation_count") or 0)
    plan_complete = all(row.get(key) is not None for key in ("initial_stop_loss", "initial_tp1", "initial_tp2"))
    return {
        "position_id": str(row.get("position_id") or ""),
        "analysis_date": str(row.get("analysis_date") or ""),
        "symbol": str(row.get("symbol") or ""),
        "buy_price": base.fmt_price(row.get("buy_price")),
        "current_price": base.fmt_price(row.get("current_price")),
        "pnl_pct": base.fmt_pct(row.get("pnl_pct")),
        "management_action": str(row.get("management_action") or ""),
        "milestone": str(row.get("milestone") or ""),
        "initial_tp1": base.fmt_price(row.get("initial_tp1")),
        "initial_tp2": base.fmt_price(row.get("initial_tp2")),
        "active_stop_loss": base.fmt_price(row.get("active_stop_loss") or row.get("initial_stop_loss")),
        "extended_target": base.fmt_price(row.get("extended_target")),
        "technical_state": str(row.get("technical_state") or ""),
        "sector_state": str(row.get("sector_state") or ""),
        "market_state": str(row.get("market_state") or ""),
        "broker_current_state": str(row.get("broker_current_state") or ""),
        "broker_effective_state": str(row.get("broker_effective_state") or ""),
        "broker_observation_count": observations,
        "broker_context_3d": str(row.get("broker_context_3d") or ""),
        "broker_context_5d": str(row.get("broker_context_5d") or ""),
        "broker_context_7d": str(row.get("broker_context_7d") or ""),
        "broker_context_since_entry": str(row.get("broker_context_since_entry") or ""),
        "broker_net_flow_since_entry": fmt_money(row.get("broker_net_flow_since_entry")),
        "broker_flow_trend": str(row.get("broker_flow_trend") or ""),
        "top_accumulation": _formatted_actors(row.get("broker_top_accumulation") or []),
        "top_distribution": _formatted_actors(row.get("broker_top_distribution") or []),
        "current_top_accumulation": _formatted_actors(row.get("broker_current_top_accumulation") or []),
        "current_top_distribution": _formatted_actors(row.get("broker_current_top_distribution") or []),
        "data_quality_status": str(row.get("data_quality_status") or ""),
        "initial_plan_status": "COMPLETE" if plan_complete else "INCOMPLETE",
        "broker_history_note": (
            f"3D history insufficient {observations}/3; current broker is warning-only"
            if observations < 3 else "3D history available for confirmation"
        ),
    }


def apply_report_interpretation(
    results: list[dict[str, Any]],
    interpreter: PortfolioGroqInterpreter | None = None,
) -> list[dict[str, Any]]:
    for row in results:
        fallback = _deterministic_interpretation(row)
        if interpreter is None:
            row["interpretation_main_reason"] = fallback["main_reason"]
            row["interpretation_main_risk"] = fallback["main_risk"]
            row["interpretation_execution_note"] = fallback["execution_note"]
            row["interpretation_source"] = "DETERMINISTIC"
            row["interpretation_status"] = "FALLBACK"
            row["interpretation_warning"] = ""
            continue
        interpreted = interpreter.interpret(_ai_facts(row), fallback)
        # Keep the portfolio conclusion deterministic so the engine reason,
        # broker actors and nominal values cannot be lost or hallucinated by AI.
        # AI remains presentation-only for risk/execution wording.
        row["interpretation_main_reason"] = fallback["main_reason"]
        row["interpretation_main_risk"] = interpreted.main_risk
        row["interpretation_execution_note"] = interpreted.execution_note
        row["interpretation_source"] = interpreted.source
        row["interpretation_status"] = interpreted.status
        row["interpretation_warning"] = interpreted.warning
    return results


def _action_emoji(action: str) -> str:
    return {
        "HOLD_STRONG": "🟢",
        "HOLD": "🟢",
        "HOLD_AFTER_TP1": "🟢",
        "HOLD_AFTER_TP2": "🟢",
        "TIGHTEN_RISK": "🟡",
        "PROTECT_PROFIT": "🟠",
        "EXIT": "🔴",
        "REVIEW_DATA": "⚪",
    }.get(str(action or "").upper(), "⚪")


def telegram_text(results: list[dict[str, Any]], analysis_date: str) -> str:
    lines = [
        "📊 <b>SDE SWING — ACTIVE PORTFOLIO</b>",
        f"🕒 {html.escape(analysis_date)}",
        "━━━━━━━━━━━━━━━━━━━",
    ]
    for index, row in enumerate(results):
        if index:
            lines.extend(["", "━━━━━━━━━━━━━━━━━━━"])
        tp1_mark = " ✅" if base.MILESTONE_RANK.get(row["milestone"], 0) >= 1 and row.get("initial_tp1") else ""
        tp2_mark = " ✅" if base.MILESTONE_RANK.get(row["milestone"], 0) >= 2 and row.get("initial_tp2") else ""
        action = str(row.get("management_action") or "-").upper()
        action_label = action.replace("_", " ")
        active_sl = row.get("active_stop_loss") if row.get("active_stop_loss") is not None else row.get("initial_stop_loss")
        lines.extend([
            f"📌 <b>{html.escape(row['symbol'])}</b> | {base.fmt_price(row.get('current_price'))} | {base.fmt_pct(row.get('pnl_pct'))}",
            f"{_action_emoji(action)} <b>{html.escape(action_label)}</b>",
            "",
            f"🎯 TP1 : {base.fmt_price(row.get('initial_tp1'))}{tp1_mark}",
            f"🚀 TP2 : {base.fmt_price(row.get('initial_tp2'))}{tp2_mark}",
            f"🛡️ SL  : {base.fmt_price(active_sl)}",
        ])
        if row.get("extended_target") is not None:
            lines.append(f"🎯 Extended : {base.fmt_price(row.get('extended_target'))}")
        lines.extend([
            "",
            "🏦 " + html.escape(str(row.get("interpretation_main_reason") or "-")),
        ])
        risk = str(row.get("interpretation_main_risk") or "").strip()
        if risk:
            lines.append("⚠️ " + html.escape(risk))
        note = str(row.get("interpretation_execution_note") or "").strip()
        if note:
            lines.append("➡️ " + html.escape(note))
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="History-aware actual portfolio management without changing the main SDE engines"
    )
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--db", default="")
    parser.add_argument("--historical-dir", default="")
    parser.add_argument("--broker-summary", default="")
    parser.add_argument("--output-dir", default=str(base.DEFAULT_OUTPUT))
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = resolve(args.config)
    config = base.load_config(config_path)
    paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    db_path = resolve(args.db or paths.get("swing_database", str(base.DEFAULT_DB)))
    historical_dir = resolve(args.historical_dir or paths.get("historical_dir", str(base.DEFAULT_HISTORICAL)))
    broker_path = resolve(args.broker_summary or paths.get("broker_summary_latest", str(base.DEFAULT_BROKER)))
    sector_metadata_path = resolve(paths.get("sector_rotation_metadata", str(base.DEFAULT_SECTOR_METADATA)))
    sector_rotation_path = resolve(paths.get("sector_rotation_output", str(base.DEFAULT_SECTOR_ROTATION)))
    output_root = resolve(args.output_dir)

    ctx = load_context(
        job="position_management",
        config_path=str(config_path),
        scheduler_config_path=args.scheduler_config,
        trade_date=args.trade_date or None,
        dry_run=args.dry_run,
        preview_existing=False,
        no_telegram=(args.no_telegram or not args.telegram),
        force=args.force,
        debug=False,
        interactive_broker=False,
    )
    analysis_date = ctx.trade_date.isoformat()
    market = base.market_state(analysis_date)

    conn = base.connect(db_path)
    try:
        ensure_schema(conn)
        try:
            snapshot_id = sync_latest_broker_summary(conn, broker_path)
            if snapshot_id:
                print(f"BROKER HISTORY: latest summary archived ({snapshot_id})")
            else:
                print("BROKER HISTORY: latest summary unavailable; using existing archive/fallback")
        except Exception as exc:
            print(
                f"[WARNING] Broker Summary archive gagal: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

        positions = base.open_positions(conn)
        if not positions:
            output_root.mkdir(parents=True, exist_ok=True)
            message = "📊 <b>SDE SWING — ACTIVE PORTFOLIO</b>\n\nTidak ada posisi aktual OPEN."
            outputs = base.write_outputs(output_root, analysis_date, [], message)
            print("POSITION MANAGEMENT: SKIPPED_NO_OPEN_POSITION")
            print(f"Output: {outputs['json']}")
            return 0

        results = [
            analyze_position(
                conn,
                position,
                analysis_date=analysis_date,
                historical_dir=historical_dir,
                broker_path=broker_path,
                sector_metadata_path=sector_metadata_path,
                sector_rotation_path=sector_rotation_path,
                market=market,
            )
            for position in positions
        ]
    finally:
        conn.close()

    ai_cfg = ctx.scheduler_config.get("enhanced_reporting", {}).get("ai_interpretation", {})
    ai_enabled = bool(ai_cfg.get("enabled", True)) and str(ai_cfg.get("provider", "GROQ")).upper() == "GROQ"
    interpreter = PortfolioGroqInterpreter(
        enabled=ai_enabled,
        model=str(ai_cfg.get("model") or "llama-3.3-70b-versatile"),
        max_portfolio_calls=min(max(len(results), 1), 20),
    )
    apply_report_interpretation(results, interpreter)

    message = telegram_text(results, analysis_date)
    outputs = base.write_outputs(output_root, analysis_date, results, message)
    print(f"POSITION MANAGEMENT: {len(results)} OPEN position(s) analyzed")
    for row in results:
        print(
            f"  {row['symbol']}: {row['management_action']} | P/L={base.fmt_pct(row['pnl_pct'])} | "
            f"broker={row['broker_effective_state']} | obs={row['broker_observation_count']} | "
            f"AI={row.get('interpretation_source', 'DETERMINISTIC')}"
        )
    print(f"Output: {outputs['json']}")

    if args.telegram and not args.no_telegram:
        payload = ReportPayload(
            report_type="POSITION_MANAGEMENT",
            filename="active_portfolio_management.txt",
            text=message,
            topic="report",
            signal_status="ACTIVE_PORTFOLIO",
            signal_version=analysis_date,
            input_paths=(str(db_path), str(historical_dir), str(broker_path)),
            source_of_truth=(
                "portfolio_positions",
                "shared_technical_features",
                "broker_summary_latest",
                "broker_summary_history",
                "position_broker_context_history",
            ),
            row_count=len(results),
        )
        delivery = deliver(ctx, [payload])
        failed = [item for item in delivery if item.get("status") == "FAILED"]
        if failed:
            print(f"Telegram delivery failed: {failed}", file=sys.stderr)
            return 50
        print("Telegram: processed -> topic report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
