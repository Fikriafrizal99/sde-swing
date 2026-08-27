from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from modules.analytics.outcome_tracker import is_material_lifecycle_event
from modules.broker_bridge.broker_period_view import BrokerPeriodView, load_broker_period_view
from modules.job_runner.enhanced_daily_reports import DailyReportArtifact, EnhancedDailyReportBuilder
from modules.job_runner.reports import ReportPayload
from modules.job_runner.report_validation import (
    ReportSourceValidationError,
    read_required_csv,
    read_required_json,
    validate_broker_summary_source,
    validate_final_watchlist_sources,
    validate_market_outlook_sources,
    validate_post_market_sources,
)
from modules.job_runner.runtime import RunnerContext, read_json, resolve


REPORTABLE_DECISION_QUALITY = {
    "VALID",
    "PARTIAL_COVERAGE",
    "SUCCESS_WITH_WARNING",
    "VALID_WITH_REFRESH_FALLBACK",
    "VALID_WITH_ZAPI_WARNING",
}


def _norm(value: Any) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def _value(row: Mapping[str, Any], *aliases: str, default: Any = "") -> Any:
    lookup = {_norm(key): value for key, value in row.items()}
    for alias in aliases:
        value = lookup.get(_norm(alias))
        if value is not None and str(value).strip().lower() not in {"", "nan", "none", "null"}:
            return value
    return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _missing_final_fact(value: Any) -> bool:
    return value is None or str(value).strip().lower() in {
        "", "nan", "none", "null", "engine_data_not_available", "data_not_available",
    }


def _optional_float(value: Any) -> float | None:
    if _missing_final_fact(value):
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _final_watchlist_plan_rr(plan: Mapping[str, Any]) -> Any:
    return _value(plan, "Target_2_RR", "Risk_Reward", "RR", "Target_1_RR", default="")


def _final_watchlist_buy_cost(primary: Mapping[str, Any], raw: Mapping[str, Any]) -> Any:
    primary_cost = primary.get("avg_buyer_price")
    if not _missing_final_fact(primary_cost):
        return primary_cost
    return _value(
        raw,
        "Bandar_Buy_Cost",
        "AVG_BUYER_PRICE",
        "Average_Buyer_Price",
        "Weighted_Broker_Buy_Cost",
        "Buyer_Weighted_Avg",
        "Weighted_Buyer_Avg",
        "Broker_Buy_Cost",
        "Buyer_Cost",
        "Buy_Cost",
        "Avg_Buy_Price",
        "Average_Buy_Price",
        default="",
    )


def _final_watchlist_distance(primary: Mapping[str, Any], raw: Mapping[str, Any]) -> Any:
    existing = _value(
        raw,
        "DISTANCE_TO_BUY_COST",
        "Distance_To_Buy_Cost_Pct",
        "Distance_To_Buyer_Avg_Pct",
        default="",
    )
    if not _missing_final_fact(existing):
        return existing
    buy_cost = _optional_float(primary.get("avg_buyer_price"))
    if buy_cost is None:
        buy_cost = _optional_float(
            _value(raw, "Bandar_Buy_Cost", "AVG_BUYER_PRICE", "Average_Buyer_Price", default="")
        )
    current_price = _optional_float(
        _value(raw, "Last_Price", "Current_Price", "Close", "Price", default="")
    )
    if buy_cost is None or buy_cost <= 0 or current_price is None:
        return ""
    return round(100.0 * (current_price - buy_cost) / buy_cost, 4)


def _coverage(valid: int, requested: int) -> float:
    return round((valid / requested * 100.0), 1) if requested > 0 else 0.0


def _broker_period_view(ctx: RunnerContext) -> BrokerPeriodView:
    summary = ctx.path("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv")
    return load_broker_period_view(
        summary,
        trade_date=ctx.trade_date.isoformat(),
        project_root=Path.cwd(),
        latest_selected_path=resolve("data/output/broker_snapshots/latest_selected.json"),
    )


def _broker_period_metadata(ctx: RunnerContext) -> dict[str, Any]:
    """Return exact PRIMARY period lineage; no rolling history fallback exists."""
    view = _broker_period_view(ctx)
    metadata = dict(view.metadata)
    metadata["today_pulse_available"] = bool(view.has_separate_today and view.today_by_symbol)
    metadata["today_pulse_status"] = (
        "AVAILABLE"
        if metadata["today_pulse_available"]
        else ("NOT_APPLICABLE" if not view.has_separate_today else "NOT_AVAILABLE")
    )
    return metadata


def _zapi_lineage(ctx: RunnerContext) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    latest = resolve("data/output/snapshots") / ctx.trade_date.isoformat() / "latest_snapshot.json"
    snapshot = read_json(latest) if latest.exists() else {}
    summary = snapshot.get("reconciliation") if isinstance(snapshot.get("reconciliation"), dict) else {}
    rows: dict[str, dict[str, Any]] = {}
    inputs = [str(latest)] if latest.exists() else []
    reconciliation_path = str(summary.get("path") or summary.get("json_path") or source_meta_path(snapshot) or "")
    if reconciliation_path and Path(reconciliation_path).exists():
        payload = read_json(Path(reconciliation_path))
        if isinstance(payload.get("symbols"), dict):
            for symbol, item in payload["symbols"].items():
                if isinstance(item, dict) and _symbol(symbol):
                    flattened = dict(item)
                    flattened["metadata"] = item.get("metadata", {})
                    rows[_symbol(symbol)] = flattened
        for item in payload.get("rows", []) if isinstance(payload.get("rows"), list) else []:
            if isinstance(item, dict) and _symbol(item.get("symbol")):
                rows[_symbol(item.get("symbol"))] = item
        inputs.append(reconciliation_path)
    return summary, rows, inputs


def source_meta_path(snapshot: Mapping[str, Any]) -> str:
    metadata = snapshot.get("source_metadata") if isinstance(snapshot.get("source_metadata"), Mapping) else {}
    return str(metadata.get("zapi_enrichment_path") or "")


def market_outlook_payloads(
    ctx: RunnerContext,
    global_snapshot: dict[str, Any],
    market_status: dict[str, Any],
) -> list[ReportPayload]:
    rotation: Mapping[str, Any] | None = None
    rotation_path = market_status.get("sector_rotation_path")
    if rotation_path:
        rotation = read_required_json(rotation_path, "market_outlook_sector_rotation")
    elif isinstance(market_status.get("sector_rotation"), dict):
        rotation = market_status.get("sector_rotation")
    validated = validate_market_outlook_sources(
        global_snapshot,
        market_status,
        rotation=rotation,
        input_paths=[
            str(resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"),
            str(ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"),
            str(rotation_path or "engine:market_status.sector_rotation"),
        ],
    )
    provider = str(validated["provider"])
    mode = str(validated["source_mode"])
    coverage = float(validated["coverage"])
    regime = str(validated["market_regime"]).replace("_", " ").upper()
    rotation = dict(validated["sector_rotation"])
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "market_regime": regime,
        "execution_mode": validated["execution_mode"],
        "ihsg_change": validated["ihsg_change"],
        "ihsg_trend": validated["ihsg_trend"],
        "ihsg_momentum": validated["ihsg_momentum"],
        "breadth": validated["breadth"],
        "rotating_in": rotation.get("improving", rotation.get("rotating_in", [])),
        "leading": rotation.get("leading", []),
        "weakening": rotation.get("weakening", []),
        "rotating_out": rotation.get("lagging", rotation.get("rotating_out", [])),
        "improving": rotation.get("improving", rotation.get("rotating_in", [])),
        "lagging": rotation.get("lagging", rotation.get("rotating_out", [])),
        "focus_tomorrow": market_status.get("focus_tomorrow") or rotation.get("focus_tomorrow", ""),
        "avoid_guidance": market_status.get("avoid_guidance") or rotation.get("avoid_guidance", ""),
        "provider": provider,
        "source_mode": mode,
        "coverage": coverage,
        "zapi_status": market_status.get("zapi_status", "ZAPI_ENRICHMENT_UNAVAILABLE"),
        "zapi_request_count": market_status.get("zapi_request_count", 0),
        "zapi_request_cap": market_status.get("zapi_request_cap", 5),
        "metadata_cache_status": market_status.get("metadata_cache_status", ""),
        "metadata_cache_date": market_status.get("metadata_cache_date", ""),
        "market_activity_cache_status": market_status.get("market_activity_cache_status", ""),
        "suspended_count": market_status.get("suspended_count", 0),
        "uma_count": market_status.get("uma_count", 0),
        "relisting_count": market_status.get("relisting_count", 0),
        "zapi_degraded": market_status.get("zapi_degraded", False),
        "degraded_reason": market_status.get("zapi_degraded_reason", ""),
    }
    artifact = _builder(ctx).build_market_outlook(data)
    global_path = resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"
    regime_path = ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
    rotation_source = rotation_path or "engine:market_status.sector_rotation"
    artifact = _artifact_with_lineage(
        artifact,
        input_paths=[str(global_path), str(regime_path), str(rotation_source)],
        source_of_truth=[str(regime_path), str(global_path), str(rotation_source)],
        row_count=len(global_snapshot.get("instruments", []) or []),
        validation_details=validated,
    )
    return [_artifact_payload(artifact)]


def validated_post_market_payloads(ctx: RunnerContext, manifest: dict[str, Any]) -> list[ReportPayload]:
    snapshot_path = str(manifest.get("Snapshot_Manifest") or manifest.get("snapshot_manifest") or "")
    snapshot = read_required_json(snapshot_path, "post_market_snapshot") if snapshot_path else {}
    manifest_path = str(
        manifest.get("Manifest_Path")
        or ctx.path("manifest_dir", "data/output/manifests") / f"SWING_RUN_MANIFEST_{manifest.get('Run_ID', ctx.run_id)}.json"
    )
    validated = validate_post_market_sources(manifest, snapshot, input_paths=[snapshot_path, manifest_path])
    requested = int(validated["symbols_requested"])
    loaded = int(validated["symbols_loaded"])
    valid = int(validated["symbols_valid"])
    failed = int(validated["symbols_failed"])
    skipped = int(validated["symbols_skipped"])
    provider = str(validated["provider"])
    mode = str(validated["source_mode"])
    coverage = float(validated["coverage"])
    status = "SUCCESS" if coverage >= 90 else "PARTIAL" if valid > 0 else "FAILED"
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "finished_time": "POST MARKET",
        "process_status": status,
        "symbols_requested": requested,
        "symbols_loaded": loaded,
        "symbols_valid": valid,
        "symbols_failed": failed,
        "symbols_skipped": skipped,
        "coverage": coverage,
        "technical_status": "READY" if valid > 0 else "NOT READY",
        "universe_status": "READY" if requested > 0 else "NOT READY",
        "candidate_status": "READY" if int(manifest.get("Candidate_Count", snapshot.get("candidate_count", 0)) or 0) > 0 else "EMPTY",
        "broker_status": "READY" if manifest.get("Broker_Navigator_Path") else "WAITING",
        "data_note": f"{failed} saham gagal validasi dan {skipped} saham dilewati.",
        "next_process": "Data siap digunakan untuk Final Watchlist." if valid > 0 else "Periksa sumber data sebelum melanjutkan.",
        "provider": provider,
        "source_mode": mode,
        "historical_status": "VALID" if valid > 0 else "FAILED",
        "zapi_status": validated["zapi_status"],
        "zapi_coverage": validated["zapi_coverage"],
        "reconciliation_status": validated["zapi_status"],
        "degraded_reason": validated["degraded_reason"],
        "zapi_request_count": validated.get("zapi_request_count", manifest.get("Zapi_Request_Count", 0)),
        "zapi_request_cap": validated.get("zapi_request_cap", manifest.get("Zapi_Request_Cap", 5)),
        "metadata_cache_status": validated.get("metadata_cache_status", manifest.get("Zapi_Metadata_Cache_Status", "")),
        "metadata_cache_date": validated.get("metadata_cache_date", manifest.get("Zapi_Metadata_Cache_Date", "")),
        "market_activity_cache_status": validated.get("market_activity_cache_status", manifest.get("Zapi_Market_Activity_Cache_Status", "")),
        "suspended_count": validated.get("suspended_count", manifest.get("Suspended_Symbol_Count", 0)),
        "uma_count": validated.get("uma_count", manifest.get("Uma_Symbol_Count", 0)),
        "relisting_count": validated.get("relisting_count", manifest.get("Relisting_Symbol_Count", 0)),
        "zapi_degraded": validated.get("zapi_degraded", manifest.get("Zapi_Degraded", False)),
        "stockbit_status": "WAITING",
    }
    artifact = _builder(ctx).build_post_market(data)
    artifact = _artifact_with_lineage(
        artifact,
        input_paths=[snapshot_path, manifest_path],
        source_of_truth=[snapshot_path, manifest_path],
        row_count=valid,
        validation_details=validated,
    )
    return [_artifact_payload(artifact)]


def post_market_payloads(ctx: RunnerContext, manifest: dict[str, Any]) -> list[ReportPayload]:
    from modules.job_runner.post_market_live import post_market_live_payloads

    return post_market_live_payloads(ctx, manifest)


def _broker_summary_rows(ctx: RunnerContext) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    source = ctx.path("broker_summary_engine", "data/input/FINAL_DECISION_V2.csv")
    frame = read_required_csv(source, "broker_summary")
    validation = validate_broker_summary_source(frame, source)
    period_metadata = _broker_period_metadata(ctx)
    rows: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        state = str(_value(raw, "Broker_Confirmation", "broker_state", "Broker_Direction_Final", "Broker_Direction", default="")).upper()
        rows.append({
            **period_metadata,
            "trade_date": ctx.trade_date.isoformat(),
            "symbol": _symbol(_value(raw, "Symbol", "EMITEN", "Ticker")),
            "broker_state": state,
            "broker_score": _value(raw, "Broker_Score", "Broker_Confidence_Final", "Broker_Confidence", default=""),
            "net_flow": _value(raw, "NET_FLOW", "Net_Flow", "Net Flow", default=""),
            "buy_ratio": _value(raw, "BUY_RATIO", "Buy_Ratio", "Buy Ratio", default=""),
            "sell_ratio": _value(raw, "SELL_RATIO", "Sell_Ratio", "Sell Ratio", default=""),
            "top_buyer": _value(raw, "TOP_BUYER_1", "TOP_BUYER", default=""),
            "top_seller": _value(raw, "TOP_SELLER_1", "TOP_SELLER", default=""),
            "data_status": _value(raw, "Data_Quality_Status", "Data_Status", default=""),
            "source": _value(raw, "Source", "Provider", default="BROKER_FUSION"),
        })
    return [row for row in rows if row["symbol"]], validation, source


def broker_summary_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    rows, validation, source = _broker_summary_rows(ctx)
    period_metadata = _broker_period_metadata(ctx)
    counts = {
        "accumulation_count": sum("ACC" in str(row["broker_state"]).upper() for row in rows),
        "distribution_count": sum("DIST" in str(row["broker_state"]).upper() for row in rows),
        "no_data_count": sum(str(row["broker_state"]).upper() in {"", "NO_DATA", "NO DATA", "UNKNOWN", "INSUFFICIENT_DATA"} for row in rows),
    }
    counts["neutral_count"] = max(0, len(rows) - counts["accumulation_count"] - counts["distribution_count"] - counts["no_data_count"])
    valid = len(rows) - counts["no_data_count"]
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        **period_metadata,
        "process_status": "SUCCESS" if valid > 0 else "PARTIAL",
        **counts,
        "rows": rows,
        "top_accumulation": sorted([r for r in rows if "ACC" in str(r["broker_state"]).upper()], key=lambda r: -_float(r["broker_score"]))[:3],
        "top_distribution": sorted([r for r in rows if "DIST" in str(r["broker_state"]).upper()], key=lambda r: _float(r["broker_score"]))[:3],
        "provider": "BROKER_FUSION",
        "source_mode": "ENGINE_OUTPUT",
        "coverage": _coverage(valid, len(rows)),
    }
    summary, csv_artifact = _builder(ctx).build_broker_summary(data)
    return [
        _artifact_payload(_artifact_with_lineage(
            artifact,
            input_paths=[source],
            source_of_truth=[source],
            row_count=len(rows),
            validation_details=validation,
        ))
        for artifact in (summary, csv_artifact)
    ]


def _entry_plan_map(ctx: RunnerContext) -> tuple[dict[str, dict[str, Any]], pd.DataFrame, Path]:
    path = ctx.path("exit_output_dir", "data/output/exit") / "ENTRY_PLANS.csv"
    frame = read_required_csv(path, "final_watchlist_entry_plans")
    result: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        symbol = _symbol(_value(row, "Symbol", "EMITEN", "Ticker"))
        if symbol:
            result[symbol] = row
    return result, frame, path


def final_watchlist_payloads(ctx: RunnerContext, manifest: dict[str, Any] | None = None) -> list[ReportPayload]:
    """Build Final Watchlist from official decisions + exact Broker Period view."""
    decisions_path = ctx.path("decision_output_dir", "data/output/decision") / "FINAL_DECISION_V3.csv"
    decisions = read_required_csv(decisions_path, "final_watchlist_decision")
    plans, entry_frame, entry_path = _entry_plan_map(ctx)
    validation = validate_final_watchlist_sources(
        decisions,
        entry_frame,
        decision_path=decisions_path,
        entry_plan_path=entry_path,
    )
    decision_manifest_path = ctx.path("manifest_dir", "data/output/manifests") / f"DECISION_ENGINE_MANIFEST_{(manifest or {}).get('Run_ID', ctx.run_id)}.json"
    decision_manifest = read_required_json(decision_manifest_path, "final_watchlist_decision_manifest")
    decision_quality = str(decision_manifest.get("Data_Quality_Status", "")).upper()
    if decision_quality not in REPORTABLE_DECISION_QUALITY:
        raise ReportSourceValidationError(
            "final_watchlist",
            [f"DATA_QUALITY_NOT_VALID:{decision_quality}"],
            input_paths=[decisions_path, entry_path, decision_manifest_path],
            source_of_truth=[decisions_path, entry_path, decision_manifest_path],
        )

    provider = str(decision_manifest.get("Decision_Owner") or "")
    coverage = decision_manifest.get("coverage_ratio", decision_manifest.get("coverage"))
    coverage = _float(coverage, 0.0) if coverage is not None else None
    if coverage is not None and 0 <= coverage <= 1:
        coverage *= 100

    zapi_summary, zapi_rows, zapi_inputs = _zapi_lineage(ctx)
    period_view = _broker_period_view(ctx)
    global_period_metadata = dict(period_view.metadata)
    rows: list[dict[str, Any]] = []

    for index, raw in enumerate(decisions.to_dict(orient="records"), start=1):
        symbol = _symbol(_value(raw, "Symbol", "EMITEN", "Ticker"))
        if not symbol:
            continue
        raw_decision = str(_value(raw, "Decision_Status_Final", "Decision_V3", "Decision", default="")).upper().replace("_", " ")
        exchange_veto = str(_value(raw, "Exchange_Veto", "Veto", "Veto_Reason", default="")).upper()
        zapi = zapi_rows.get(symbol, {})
        exchange_status = str(_value(raw, "Exchange_Status", default=zapi.get("status", "NORMAL"))).upper()
        exchange_veto = exchange_veto or str(zapi.get("veto") or "").upper()
        if raw_decision in {"BLOCKED", "SUSPENDED"} or exchange_status in {"SUSPENDED", "BLOCKED"} or exchange_veto in {"SUSPENDED", "RELISTING_HISTORY_INSUFFICIENT"}:
            continue

        plan = plans.get(symbol, {})
        period = period_view.symbol(symbol)
        primary = dict(period.get("primary") or {})
        today = dict(period.get("today") or {})
        technical_state = _value(
            raw,
            "Technical_Confirmation",
            "Technical_Regime",
            default=_value(plan, "Plan_Status", "Execution_Status", default=_value(raw, "Technical_State", "Technical_Grade", default="")),
        )
        trend = _value(
            raw,
            "Technical_Regime",
            "Trend",
            "Trend_State",
            "Trend_Direction",
            "Technical_Trend",
            "Trend_Final",
            "Trend_Label",
            default="",
        )
        buy_cost = _final_watchlist_buy_cost(primary, raw)
        broker_fallback = _value(raw, "Broker_Confirmation", "Broker_Direction_Final", default="")
        broker_status = broker_fallback or str(primary.get("broker_accdist") or "") or "MISSING"
        broker_net_flow = primary.get("net_flow", _value(raw, "NET_FLOW", "Net_Flow", "Broker_Net_Flow", default=""))
        buyer_concentration = primary.get("buyer_concentration", _value(raw, "BUYER_CONCENTRATION", "Buyer_Concentration", default=""))
        seller_concentration = primary.get("seller_concentration", _value(raw, "SELLER_CONCENTRATION", "Seller_Concentration", default=""))
        top_buyers = list(primary.get("top_buyers") or [])
        top_sellers = list(primary.get("top_sellers") or [])

        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "rank": _value(raw, "Rank_V3", "Rank", default=index),
            "symbol": symbol,
            "decision": _value(raw, "Decision_Status_Final", "Decision_V3", "Decision", default=""),
            "risk_flags": _value(raw, "Risk_Flags", default=",".join(zapi.get("risk_flags") or [])),
            "exchange_status": exchange_status,
            "exchange_veto": exchange_veto,
            "confidence": _value(raw, "Final_Score_V3", "Final_Score", "Confidence", default=""),
            "setup": _value(raw, "Setup_Type", "Setup_Label", default=""),
            "entry_low": _value(plan, "Entry_Zone_Low", "Entry_Low", "Entry_Min", default=""),
            "entry_high": _value(plan, "Entry_Zone_High", "Entry_High", "Entry_Max", default=""),
            "stop_loss": _value(plan, "Initial_Stop", "Stop_Loss", "Stop", default=""),
            "target_1": _value(plan, "Target_1", "TP1", default=""),
            "target_2": _value(plan, "Target_2", "TP2", default=""),
            "risk_reward": _final_watchlist_plan_rr(plan),
            "trend": trend,
            "technical_score": _value(raw, "Technical_Score_Final", "Technical_Score", default=""),
            "technical_state": technical_state,
            # PRIMARY broker facts. Score/status remain Broker Fusion owned.
            "broker_score": _value(raw, "Broker_Score", "Broker_Confidence_Final", "Broker_Confidence", default=""),
            "broker_state": broker_status,
            "broker_status": broker_status,
            "broker_direction": _value(raw, "Broker_Direction_Final", "Broker_Direction", default=broker_status),
            "broker_net_flow": broker_net_flow,
            "broker_buy_ratio": _value(raw, "BUY_RATIO", "Buy_Ratio", default=""),
            "broker_sell_ratio": _value(raw, "SELL_RATIO", "Sell_Ratio", default=""),
            "buyer_concentration": buyer_concentration,
            "seller_concentration": seller_concentration,
            "broker_pattern": primary.get("broker_accdist", _value(raw, "BROKER_ACCDIST", "Broker_AccDist", default="")),
            "avg_accdist": primary.get("avg_accdist", ""),
            "bandar_buy_cost": buy_cost,
            "avg_buyer_price": buy_cost,
            "avg_seller_price": primary.get(
                "avg_seller_price",
                _value(raw, "AVG_SELLER_PRICE", "Average_Seller_Price", default=""),
            ),
            "distance_to_buy_cost": _final_watchlist_distance(primary, raw),
            "distance_to_buyer_avg_pct": _final_watchlist_distance(primary, raw),
            "top_buyers": top_buyers,
            "top_sellers": top_sellers,
            # Exact PRIMARY period lineage.
            **global_period_metadata,
            # Exact TODAY 1D pulse. Empty/not-applicable for PRIMARY 1D.
            "today_pulse_available": bool(today),
            "today_pulse_date": ctx.trade_date.isoformat() if today else "",
            "today_pulse_snapshot_id": str(period.get("today_pulse_snapshot_id") or "") if today else "",
            "today_pulse_source": str(period.get("today_pulse_source") or "STOCKBIT_1D") if today else "",
            "today_pulse_status": "AVAILABLE" if today else ("NOT_APPLICABLE" if not period_view.has_separate_today else "NOT_AVAILABLE"),
            "today_pulse_net_flow": today.get("net_flow", ""),
            "today_pulse_broker_state": today.get("broker_accdist", ""),
            "today_pulse_direction": today.get("broker_accdist", ""),
            "today_pulse_avg_accdist": today.get("avg_accdist", ""),
            "today_pulse_buyer_concentration": today.get("buyer_concentration", ""),
            "today_pulse_seller_concentration": today.get("seller_concentration", ""),
            "today_pulse_top_buyers": list(today.get("top_buyers") or []),
            "today_pulse_top_sellers": list(today.get("top_sellers") or []),
            "broker_alignment": period.get("alignment", ""),
            "sector_state": _value(raw, "Sector_State", "Sector_Rotation_State", default=""),
            "market_regime": _value(raw, "Market_Regime", default=""),
            "main_reason": _value(raw, "Main_Reason", "Decision_Reason", "Reason", default=""),
            "main_risk": _value(raw, "Main_Risk", "Risk_Note", "Warnings", default=""),
            "data_status": _value(raw, "Data_Quality_Status", "Data_Status", default=""),
            "source": _value(raw, "Source", default=provider),
            "provider": provider,
            "source_mode": "ENGINE_OUTPUT",
            "coverage": coverage,
            "yahoo_status": "VALID",
            "zapi_status": zapi.get("status") or zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
            "reconciliation_status": zapi.get("status") or zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
            "zapi_freshness_days": zapi.get("freshness_days"),
        })

    data = {
        "trade_date": ctx.trade_date.isoformat(),
        **global_period_metadata,
        "rows": rows,
        "provider": provider,
        "source_mode": "ENGINE_OUTPUT",
        "coverage": coverage,
        "zapi_status": zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
        "reconciliation_status": zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
        "zapi_request_count": zapi_summary.get("request_count", 0),
        "zapi_request_cap": zapi_summary.get("request_cap", 5),
        "metadata_cache_status": zapi_summary.get("metadata_cache_status", ""),
        "metadata_cache_date": zapi_summary.get("metadata_cache_date", ""),
        "market_activity_cache_status": zapi_summary.get("market_activity_cache_status", ""),
        "suspended_count": zapi_summary.get("suspended_count", 0),
        "uma_count": zapi_summary.get("uma_count", 0),
        "relisting_count": zapi_summary.get("relisting_count", 0),
        "zapi_degraded": zapi_summary.get("degraded", False),
    }
    artifacts = _builder(ctx).build_final_watchlist(data)
    lineage_inputs = [decisions_path, entry_path, decision_manifest_path, *zapi_inputs, *period_view.input_paths]
    # Deduplicate lineage while preserving order.
    lineage_inputs = list(dict.fromkeys(str(path) for path in lineage_inputs if str(path)))
    return [
        _artifact_payload(_artifact_with_lineage(
            artifact,
            input_paths=lineage_inputs,
            source_of_truth=lineage_inputs,
            row_count=len(rows),
            validation_details=validation,
        ))
        for artifact in artifacts
    ]


def lifecycle_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    analytics_root = resolve(
        ctx.config.get("paths", {}).get("analytics_output_root", "data/output/analytics")
    ) / "performance"
    status_path = analytics_root / "STATUS_CHANGES_TELEGRAM.txt"
    active_csv = analytics_root / "ACTIVE_RECOMMENDATIONS.csv"
    events_csv = analytics_root / "LIFECYCLE_EVENTS.csv"
    if not status_path.exists():
        return []
    source_paths = [analytics_root / "SIGNAL_OUTCOME_LEDGER.csv", active_csv, events_csv]
    status_text = status_path.read_text(encoding="utf-8").strip()
    if not status_text:
        return []
    event_ids: list[str] = []
    row_count = None
    if events_csv.exists():
        try:
            events = pd.read_csv(events_csv, low_memory=False)
            pending = events[
                events.apply(is_material_lifecycle_event, axis=1)
                & events.get("telegram_notified_at", pd.Series(index=events.index)).fillna("").astype(str).str.strip().eq("")
            ]
            event_ids = [str(value) for value in pending.get("event_id", pd.Series(dtype=str)).tolist() if str(value).strip()]
            row_count = len(pending)
        except Exception:
            event_ids = []
    status_payload = ReportPayload(
        "status_changes",
        "status_changes.txt",
        status_text,
        topic="report",
        signal_version=hashlib.sha256("|".join(sorted(event_ids)).encode("utf-8")).hexdigest()[:24],
        input_paths=tuple(str(path) for path in source_paths if path.exists()),
        source_of_truth=tuple(str(path) for path in source_paths if path.exists()),
        row_count=row_count,
    )
    setattr(status_payload, "lifecycle_event_ids", tuple(event_ids))
    return [status_payload]


def complete_daily_payloads(
    ctx: RunnerContext,
    *,
    global_snapshot: dict[str, Any] | None = None,
    market_status: dict[str, Any] | None = None,
    post_manifest: dict[str, Any] | None = None,
    include_market: bool = True,
    include_post: bool = True,
    include_broker: bool = True,
    include_final: bool = True,
) -> list[ReportPayload]:
    payloads: list[ReportPayload] = []
    if include_market and global_snapshot is not None and market_status is not None:
        payloads.extend(market_outlook_payloads(ctx, global_snapshot, market_status))
    if include_post and post_manifest is not None:
        payloads.extend(post_market_payloads(ctx, post_manifest))
    if include_broker:
        payloads.extend(broker_summary_payloads(ctx))
    if include_final:
        payloads.extend(final_watchlist_payloads(ctx))
        payloads.extend(lifecycle_payloads(ctx))
    return payloads


# FINAL_WATCHLIST_RUNTIME_BRIDGE_V2
def _builder(ctx: RunnerContext) -> EnhancedDailyReportBuilder:
    cfg = ctx.scheduler_config.get("enhanced_reporting", {})
    builder = EnhancedDailyReportBuilder(
        output_root=resolve(cfg.get("output_root", "data/output")),
        max_watchlist_messages=int(cfg.get("max_watchlist_messages", 5)),
    )
    builder.historical_dir = ctx.path("historical_dir", "data/output/historical/by_symbol")
    builder.chart_output_root = resolve(cfg.get("final_watchlist_chart_output_root", "output/final_watchlist"))
    return builder


def _artifact_payload(artifact: DailyReportArtifact) -> ReportPayload:
    suffix = f"_{artifact.symbol}" if artifact.symbol else ""
    payload = ReportPayload(
        report_type=artifact.report_type,
        filename=f"{artifact.report_type}{suffix}.txt",
        text=artifact.text,
        topic="report",
        symbol=artifact.symbol,
    )
    if artifact.attachment_path is not None:
        setattr(payload, "attachment_path", artifact.attachment_path)
        setattr(payload, "caption", artifact.caption)
    setattr(payload, "input_paths", tuple(artifact.input_paths))
    setattr(payload, "source_of_truth", tuple(artifact.source_of_truth))
    setattr(payload, "row_count", artifact.row_count)
    details = dict(artifact.validation_details or {})
    setattr(payload, "validation_details", details)
    material = str(details.get("material_signature") or "").strip()
    if material:
        payload.material_signature = material
        payload.signal_version = material
    return payload


def _artifact_with_lineage(
    artifact: DailyReportArtifact,
    *,
    input_paths: Iterable[str | Path],
    source_of_truth: Iterable[str | Path],
    row_count: int | None = None,
    validation_details: dict[str, Any] | None = None,
) -> DailyReportArtifact:
    merged = dict(artifact.validation_details or {})
    merged.update(dict(validation_details or {}))
    return DailyReportArtifact(
        report_type=artifact.report_type,
        text=artifact.text,
        topic=artifact.topic,
        symbol=artifact.symbol,
        attachment_path=artifact.attachment_path,
        caption=artifact.caption,
        input_paths=tuple(str(path) for path in input_paths),
        source_of_truth=tuple(str(path) for path in source_of_truth),
        row_count=row_count,
        validation_details=merged,
    )
