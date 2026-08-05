from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from modules.analytics.outcome_tracker import is_material_lifecycle_event
from modules.job_runner.enhanced_daily_reports import DailyReportArtifact, EnhancedDailyReportBuilder
from modules.job_runner.reports import ReportPayload
from modules.job_runner.report_validation import (
    ReportSourceValidationError,
    read_required_csv,
    read_required_json,
    validate_broker_multiday_source,
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
    # ZAPI price variance is explicitly non-blocking; the decision engine
    # labels the resulting artifact as valid with a source warning.
    "VALID_WITH_ZAPI_WARNING",
}


def _norm(value: Any) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def _value(row: dict[str, Any], *aliases: str, default: Any = "") -> Any:
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


def _coverage(valid: int, requested: int) -> float:
    return round((valid / requested * 100.0), 1) if requested > 0 else 0.0


def _artifact_payload(artifact: DailyReportArtifact) -> ReportPayload:
    suffix = f"_{artifact.symbol}" if artifact.symbol else ""
    filename = f"{artifact.report_type}{suffix}.txt"
    payload = ReportPayload(
        report_type=artifact.report_type,
        filename=filename,
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
    setattr(payload, "validation_details", dict(artifact.validation_details or {}))
    return payload


def _artifact_with_lineage(
    artifact: DailyReportArtifact,
    *,
    input_paths: Iterable[str | Path],
    source_of_truth: Iterable[str | Path],
    row_count: int | None = None,
    validation_details: dict[str, Any] | None = None,
) -> DailyReportArtifact:
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
        validation_details=dict(validation_details or {}),
    )


def _builder(ctx: RunnerContext) -> EnhancedDailyReportBuilder:
    cfg = ctx.scheduler_config.get("enhanced_reporting", {})
    return EnhancedDailyReportBuilder(
        output_root=resolve(cfg.get("output_root", "data/output")),
        max_watchlist_messages=int(cfg.get("max_watchlist_messages", 5)),
    )


def _zapi_lineage(ctx: RunnerContext) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    latest = resolve("data/output/snapshots") / ctx.trade_date.isoformat() / "latest_snapshot.json"
    snapshot = read_json(latest) if latest.exists() else {}
    summary = snapshot.get("reconciliation") if isinstance(snapshot.get("reconciliation"), dict) else {}
    rows: dict[str, dict[str, Any]] = {}
    inputs = [str(latest)] if latest.exists() else []
    reconciliation_path = str(summary.get("json_path") or "")
    if reconciliation_path and Path(reconciliation_path).exists():
        payload = read_json(Path(reconciliation_path))
        for item in payload.get("rows", []) if isinstance(payload.get("rows"), list) else []:
            if isinstance(item, dict) and _symbol(item.get("symbol")):
                rows[_symbol(item.get("symbol"))] = item
        inputs.append(reconciliation_path)
    return summary, rows, inputs


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
        # The artifact contract is leading/improving/weakening/lagging.  The
        # UI keeps its historical labels, so aliases are mapped here only at
        # presentation time.
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
    }
    artifact = _builder(ctx).build_market_outlook(data)
    global_path = resolve("data/output/global_market") / ctx.trade_date.isoformat() / "global_market_snapshot.json"
    regime_path = ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
    rotation_source = rotation_path or "engine:market_status.sector_rotation"
    artifact = _artifact_with_lineage(
        artifact,
        input_paths=[
            str(global_path),
            str(regime_path),
            str(rotation_source),
        ],
        source_of_truth=[
            str(regime_path),
            str(global_path),
            str(rotation_source),
        ],
        row_count=len(global_snapshot.get("instruments", []) or []),
        validation_details=validated,
    )
    return [_artifact_payload(artifact)]


def post_market_payloads(ctx: RunnerContext, manifest: dict[str, Any]) -> list[ReportPayload]:
    snapshot_path = str(manifest.get("Snapshot_Manifest") or manifest.get("snapshot_manifest") or "")
    snapshot = read_required_json(snapshot_path, "post_market_snapshot") if snapshot_path else {}
    manifest_path = str(
        manifest.get("Manifest_Path")
        or ctx.path("manifest_dir", "data/output/manifests") / f"SWING_RUN_MANIFEST_{manifest.get('Run_ID', ctx.run_id)}.json"
    )
    validated = validate_post_market_sources(
        manifest,
        snapshot,
        input_paths=[snapshot_path, manifest_path],
    )
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


def _broker_summary_rows(ctx: RunnerContext) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    # Broker Summary is published by Broker Fusion.  The raw Stockbit export
    # remains an input to that engine, never a second report source.
    source = ctx.path("broker_summary_engine", "data/input/FINAL_DECISION_V2.csv")
    frame = read_required_csv(source, "broker_summary")
    validation = validate_broker_summary_source(frame, source)
    rows: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        state = str(_value(raw, "Broker_Confirmation", "broker_state", "Broker_Direction_Final", "Broker_Direction", default="")).upper()
        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "symbol": _symbol(_value(raw, "Symbol", "EMITEN", "Ticker")),
            "broker_state": state,
            "broker_score": _value(raw, "Broker_Score", "Broker_Confidence_Final", "Broker_Confidence", default=""),
            "net_flow": _value(raw, "NET_FLOW", "Net_Flow", "Net Flow", default=""),
            "buy_ratio": _value(raw, "BUY_RATIO", "Buy_Ratio", "Buy Ratio", default=""),
            "sell_ratio": _value(raw, "SELL_RATIO", "Sell_Ratio", "Sell Ratio", default=""),
            "top_buyer": _value(raw, "TOP_BUYER_1", "TOP_BUYER", default=""),
            "top_seller": _value(raw, "TOP_SELLER_1", "TOP_SELLER", default=""),
            "broker_1d": _value(raw, "broker_1d", "state_1d", default=""),
            "broker_3d": _value(raw, "broker_3d", "state_3d", default=""),
            "broker_5d": _value(raw, "broker_5d", "state_5d", default=""),
            "data_status": _value(raw, "Data_Quality_Status", "Data_Status", default=""),
            "source": _value(raw, "Source", "Provider", default="BROKER_FUSION"),
        })
    return [row for row in rows if row["symbol"]], validation, source


def broker_summary_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    rows, validation, source = _broker_summary_rows(ctx)
    counts = {
        "accumulation_count": sum("ACC" in str(row["broker_state"]).upper() for row in rows),
        "distribution_count": sum("DIST" in str(row["broker_state"]).upper() for row in rows),
        "no_data_count": sum(str(row["broker_state"]).upper() in {"", "NO_DATA", "NO DATA", "UNKNOWN", "INSUFFICIENT_DATA"} for row in rows),
    }
    counts["neutral_count"] = max(0, len(rows) - counts["accumulation_count"] - counts["distribution_count"] - counts["no_data_count"])
    valid = len(rows) - counts["no_data_count"]
    data = {
        "trade_date": ctx.trade_date.isoformat(),
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
    artifacts = [summary, csv_artifact]
    return [_artifact_payload(_artifact_with_lineage(
        artifact,
        input_paths=[source],
        source_of_truth=[source],
        row_count=len(rows),
        validation_details=validation,
    )) for artifact in artifacts]


def _multiday_rows(ctx: RunnerContext) -> tuple[list[dict[str, Any]], dict[str, Any], tuple[Path, Path, Path]]:
    output_dir = ctx.path("broker_multiday_output_dir", "data/output/broker_multiday")
    summary_path = output_dir / "BROKER_MULTIDAY_SUMMARY.csv"
    detail_path = output_dir / "BROKER_MULTIDAY_DETAIL.csv"
    manifest_path = output_dir / "BROKER_MULTIDAY_MANIFEST.json"
    multiday_manifest = read_required_json(manifest_path, "broker_multi_day_manifest")
    expected_run_ids = {ctx.run_id}
    if ctx.job != "broker_multi_day":
        dependency_status = read_required_json(
            ctx.status_root / "broker_multi_day_latest.json",
            "broker_multi_day_dependency_status",
        )
        if dependency_status.get("run_id"):
            expected_run_ids.add(str(dependency_status["run_id"]))
    if str(multiday_manifest.get("run_id", "")) not in expected_run_ids:
        from modules.job_runner.report_validation import ReportSourceValidationError

        raise ReportSourceValidationError(
            "broker_multi_day",
            [f"RUN_ID_MISMATCH:{multiday_manifest.get('run_id', '')} not in {sorted(expected_run_ids)}"],
            input_paths=[manifest_path],
            source_of_truth=[manifest_path],
        )
    if str(multiday_manifest.get("data_quality_status", "")).upper() != "VALID":
        from modules.job_runner.report_validation import ReportSourceValidationError

        raise ReportSourceValidationError(
            "broker_multi_day",
            [f"DATA_QUALITY_NOT_VALID:{multiday_manifest.get('data_quality_status', '')}"],
            input_paths=[manifest_path],
            source_of_truth=[manifest_path],
        )
    frame = read_required_csv(summary_path, "broker_multi_day_summary")
    detail = read_required_csv(detail_path, "broker_multi_day_detail")
    validation = {
        "summary": validate_broker_multiday_source(frame, summary_path),
        "detail": validate_broker_multiday_source(detail, detail_path),
    }
    detail_map = {
        _symbol(_value(raw, "Symbol", "EMITEN", "Ticker")): raw
        for raw in detail.to_dict(orient="records")
    }
    summary_symbols = {
        _symbol(_value(raw, "Symbol", "EMITEN", "Ticker"))
        for raw in frame.to_dict(orient="records")
    }
    missing_detail = sorted(symbol for symbol in summary_symbols if symbol and symbol not in detail_map)
    if missing_detail:
        from modules.job_runner.report_validation import ReportSourceValidationError

        raise ReportSourceValidationError(
            "broker_multi_day",
            [f"DETAIL_SYMBOL_MISSING:{','.join(missing_detail)}"],
            input_paths=[summary_path, detail_path],
            source_of_truth=[summary_path, detail_path],
        )
    rows: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        symbol = _symbol(_value(raw, "symbol", "emiten", "ticker"))
        detail_row = detail_map.get(symbol, {})
        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "symbol": symbol,
            "state_1d": _value(detail_row, "Broker_Context_1D", "state_1d", default=""),
            "state_3d": _value(detail_row, "Broker_Context_3D", "state_3d", default=""),
            "state_5d": _value(detail_row, "Broker_Context_5D", "state_5d", default=""),
            "state_10d": _value(detail_row, "Broker_Context_10D", "state_10d", default=""),
            "state_20d": _value(detail_row, "Broker_Context_20D", "state_20d", default=""),
            "overall_state": _value(raw, "Context", "overall_state", "Broker_MultiDay_Context", default=""),
            "broker_score": _value(raw, "Score", "broker_score", "Broker_MultiDay_Score", default=""),
            "coverage_days": _value(raw, "coverage_days", "Coverage_Days", default=""),
            "missing_days": _value(raw, "missing_days", "Missing_Days", default=""),
            "data_status": _value(raw, "Data_Quality_Status", "data_status", default=""),
            "source": _value(raw, "Source", "source", default="BROKER_MULTIDAY_ENGINE"),
        })
    validation["manifest"] = multiday_manifest
    return [row for row in rows if row["symbol"]], validation, (summary_path, detail_path, manifest_path)


def broker_multiday_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    rows, validation, paths = _multiday_rows(ctx)
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "process_status": "SUCCESS" if rows else "PARTIAL",
        "rows": rows,
        "top_accumulation": [row for row in rows if "ACC" in str(row["overall_state"]).upper()][:3],
        "top_distribution": [row for row in rows if "DIST" in str(row["overall_state"]).upper()][:3],
        "provider": "BROKER_MULTIDAY_ENGINE",
        "source_mode": "ENGINE_OUTPUT",
        "coverage": _coverage(sum(str(row["data_status"]).upper() == "VALID" for row in rows), len(rows)),
    }
    summary, csv_artifact = _builder(ctx).build_broker_multiday(data)
    return [_artifact_payload(_artifact_with_lineage(
        artifact,
        input_paths=paths,
        source_of_truth=paths,
        row_count=len(rows),
        validation_details=validation,
    )) for artifact in (summary, csv_artifact)]


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
    mode = "ENGINE_OUTPUT"
    coverage = decision_manifest.get("coverage_ratio", decision_manifest.get("coverage"))
    coverage = _float(coverage, 0.0) if coverage is not None else None
    if coverage is not None and 0 <= coverage <= 1:
        coverage *= 100
    zapi_summary, zapi_rows, zapi_inputs = _zapi_lineage(ctx)
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(decisions.to_dict(orient="records"), start=1):
        symbol = _symbol(_value(raw, "Symbol", "EMITEN", "Ticker"))
        if not symbol:
            continue
        plan = plans.get(symbol, {})
        zapi = zapi_rows.get(symbol, {})
        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "rank": _value(raw, "Rank_V3", "Rank", default=index),
            "symbol": symbol,
            "decision": _value(raw, "Decision_Status_Final", "Decision_V3", "Decision", default=""),
            "confidence": _value(raw, "Confidence", "Final_Score_V3", "Final_Score", default=""),
            "setup": _value(raw, "Setup_Type", "Setup_Label", default=""),
            "entry_low": _value(plan, "Entry_Zone_Low", "Entry_Low", "Entry_Min", default=""),
            "entry_high": _value(plan, "Entry_Zone_High", "Entry_High", "Entry_Max", default=""),
            "stop_loss": _value(plan, "Initial_Stop", "Stop_Loss", "Stop", default=""),
            "target_1": _value(plan, "Target_1", "TP1", default=""),
            "target_2": _value(plan, "Target_2", "TP2", default=""),
            "risk_reward": _value(plan, "RR_To_Resistance", "RR_To_Minor_Resistance", "Risk_Reward", "RR", default=""),
            "technical_score": _value(raw, "Technical_Score_Final", "Technical_Score", default=""),
            "technical_state": _value(raw, "Technical_State", "Technical_Confirmation", "Technical_Grade", "Technical_Regime", default=""),
            "broker_score": _value(raw, "Broker_Score", "Broker_Confidence_Final", default=""),
            "broker_state": _value(raw, "Broker_Confirmation", "Broker_Direction_Final", default=""),
            "sector_state": _value(raw, "Sector_State", "Sector_Rotation_State", default=""),
            "market_regime": _value(raw, "Market_Regime", default=""),
            "main_reason": _value(raw, "Main_Reason", "Decision_Reason", "Decision_Reasons", "Reason", default=""),
            "main_risk": _value(raw, "Main_Risk", "Risk_Note", "Warnings", default=""),
            "data_status": _value(raw, "Data_Quality_Status", "Data_Status", default=""),
            "source": _value(raw, "Source", default=provider),
            "provider": provider,
            "source_mode": mode,
            "coverage": coverage,
            "yahoo_status": "VALID",
            "zapi_status": zapi.get("status") or zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
            "reconciliation_status": zapi.get("status") or zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
            "zapi_freshness_days": zapi.get("freshness_days"),
            "broker_status": "AVAILABLE" if _value(raw, "Broker_Confirmation", "Broker_Direction_Final", default="") else "MISSING",
        })
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "rows": rows,
        "provider": provider,
        "source_mode": mode,
        "coverage": coverage,
        "zapi_status": zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
        "reconciliation_status": zapi_summary.get("status") or "ZAPI_LINEAGE_MISSING",
    }
    artifacts = _builder(ctx).build_final_watchlist(data)
    return [_artifact_payload(_artifact_with_lineage(
        artifact,
        input_paths=[decisions_path, entry_path, decision_manifest_path, *zapi_inputs],
        source_of_truth=[decisions_path, entry_path, decision_manifest_path, *zapi_inputs],
        row_count=len(rows),
        validation_details=validation,
    )) for artifact in artifacts]


def lifecycle_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    """Load persistent recommendation/portfolio lifecycle reports.

    The outcome tracker writes these files after the engine stage.  Keeping the
    bridge file-backed means Telegram delivery cannot mutate engine decisions,
    and a failed delivery leaves lifecycle events pending for the next retry.
    """
    analytics_root = resolve(
        ctx.config.get("paths", {}).get("analytics_output_root", "data/output/analytics")
    ) / "performance"
    active_path = analytics_root / "ACTIVE_RECOMMENDATIONS_TELEGRAM.txt"
    status_path = analytics_root / "STATUS_CHANGES_TELEGRAM.txt"
    active_csv = analytics_root / "ACTIVE_RECOMMENDATIONS.csv"
    events_csv = analytics_root / "LIFECYCLE_EVENTS.csv"
    if not active_path.exists():
        return []
    source_paths = [analytics_root / "SIGNAL_OUTCOME_LEDGER.csv", active_csv, events_csv]
    payloads: list[ReportPayload] = [
        ReportPayload(
            "active_recommendations",
            "active_recommendations.txt",
            active_path.read_text(encoding="utf-8"),
            topic="report",
            input_paths=tuple(str(path) for path in source_paths if path.exists()),
            source_of_truth=tuple(str(path) for path in source_paths if path.exists()),
        )
    ]
    if status_path.exists():
        status_text = status_path.read_text(encoding="utf-8").strip()
        if status_text:
            event_ids: list[str] = []
            row_count = None
            if events_csv.exists():
                try:
                    events = pd.read_csv(events_csv, low_memory=False)
                    pending = events[
                        events.apply(is_material_lifecycle_event, axis=1)
                        &
                        events.get("telegram_notified_at", pd.Series(index=events.index)).fillna("").astype(str).str.strip().eq("")
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
            payloads.append(status_payload)
    return payloads


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
        payloads.extend(broker_multiday_payloads(ctx))
    if include_final:
        payloads.extend(final_watchlist_payloads(ctx))
        payloads.extend(lifecycle_payloads(ctx))
    return payloads
