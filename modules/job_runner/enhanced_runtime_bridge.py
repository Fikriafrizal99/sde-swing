from __future__ import annotations

import hashlib
import json
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


def _final_watchlist_plan_rr(plan: dict[str, Any]) -> Any:
    """Return the executable trade-plan RR, never a nearby resistance RR."""
    return _value(
        plan,
        "Target_2_RR",
        "Risk_Reward",
        "RR",
        "Target_1_RR",
        default="",
    )


def _final_watchlist_broker_score(multiday: dict[str, Any], raw: dict[str, Any]) -> Any:
    """Preserve a valid zero multi-day confidence instead of truthy fallback."""
    if "broker_score" in multiday:
        return multiday.get("broker_score")
    return _value(raw, "Broker_Score", "Broker_Confidence_Final", default="")


def _final_watchlist_distance(multiday: dict[str, Any], raw: dict[str, Any]) -> Any:
    """Fill display distance from the same visible current price and buy cost."""
    existing = multiday.get("distance_to_buy_cost", "")
    if not _missing_final_fact(existing):
        return existing
    buy_cost = _optional_float(multiday.get("bandar_buy_cost"))
    current_price = _optional_float(
        _value(raw, "Last_Price", "Current_Price", "Close", "Price", default="")
    )
    if buy_cost is None or buy_cost <= 0 or current_price is None:
        return existing
    return round(100.0 * (current_price - buy_cost) / buy_cost, 4)


def _final_watchlist_reason(raw: dict[str, Any]) -> Any:
    """Avoid Broker Fusion's single-session Decision_Reasons in a multi-day card."""
    return _value(raw, "Main_Reason", "Decision_Reason", "Reason", default="")


def _coverage(valid: int, requested: int) -> float:
    return round((valid / requested * 100.0), 1) if requested > 0 else 0.0


def _broker_period_metadata(
    ctx: RunnerContext,
    *,
    multiday_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the active Broker Period envelope for presentation lineage.

    The active sidecar is preferred over the output manifest because it is
    written before the dependent stages run.  Older runs may only have the
    multi-day manifest, so the lookup remains additive/backward compatible.
    This helper never supplies a score or decision value.
    """
    candidates = [
        ctx.path("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv").with_suffix(".manifest.json"),
        resolve("data/output/broker_snapshots/latest_selected.json"),
    ]
    if multiday_manifest:
        candidates.append(
            ctx.path("broker_multiday_output_dir", "data/output/broker_multiday")
            / "BROKER_MULTIDAY_MANIFEST.json"
        )
    selected: dict[str, Any] = {}
    for path in candidates:
        if not path.exists():
            continue
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        period_end = str(
            payload.get("broker_period_end")
            or payload.get("to_date")
            or payload.get("broker_date")
            or ""
        )[:10]
        if period_end and period_end != ctx.trade_date.isoformat():
            continue
        if payload.get("broker_period_type") or payload.get("period_type"):
            selected = payload
            break

    if not selected and isinstance(multiday_manifest, Mapping):
        selected = dict(multiday_manifest)
    if not selected:
        return {}

    session_dates = _value(
        selected,
        "broker_session_dates",
        "Broker_Session_Dates",
        "session_dates",
        default=[],
    )
    if isinstance(session_dates, str):
        try:
            parsed = json.loads(session_dates)
            session_dates = parsed if isinstance(parsed, list) else [session_dates]
        except Exception:
            session_dates = [item.strip() for item in session_dates.split(",") if item.strip()]
    if not isinstance(session_dates, (list, tuple)):
        session_dates = []

    normalized = {
        "broker_period_type": str(_value(selected, "broker_period_type", "Broker_Period_Type", "period_type", default="")).upper(),
        "broker_period_start": str(_value(selected, "broker_period_start", "Broker_Period_Start", "from_date", default=""))[:10],
        "broker_period_end": str(_value(selected, "broker_period_end", "Broker_Period_End", "to_date", "broker_date", default=""))[:10],
        "broker_trading_days": _value(selected, "broker_trading_days", "Broker_Trading_Days", "trading_sessions", default=""),
        "broker_session_dates": [str(item)[:10] for item in session_dates if str(item).strip()],
        "broker_snapshot_id": _value(selected, "broker_snapshot_id", "snapshot_id", "Broker_Snapshot_ID", default=""),
        "broker_period_source": str(_value(selected, "broker_period_source", "Broker_Period_Source", "source", default="")).upper(),
        "broker_coverage": _value(selected, "broker_coverage", "Broker_Coverage", "coverage_ratio", "coverage", default=""),
        "broker_session_coverage": _value(selected, "broker_session_coverage", "Broker_Session_Coverage", default=""),
        "broker_coverage_text": _value(selected, "broker_coverage_text", "Broker_Coverage_Text", default=""),
        "broker_coverage_status": _value(selected, "broker_coverage_status", "Broker_Coverage_Status", "data_quality_status", default=""),
        "broker_freshness_status": str(_value(selected, "broker_freshness_status", "freshness_status", default="")).upper(),
    }
    return {key: value for key, value in normalized.items() if value not in ("", [], {})}


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


def _broker_summary_rows(ctx: RunnerContext) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    # Broker Summary is published by Broker Fusion.  The raw Stockbit export
    # remains an input to that engine, never a second report source.
    source = ctx.path("broker_summary_engine", "data/input/FINAL_DECISION_V2.csv")
    frame = read_required_csv(source, "broker_summary")
    validation = validate_broker_summary_source(frame, source)
    period_metadata = _broker_period_metadata(ctx)
    rows: list[dict[str, Any]] = []
    for raw in frame.to_dict(orient="records"):
        state = str(_value(raw, "Broker_Confirmation", "broker_state", "Broker_Direction_Final", "Broker_Direction", default="")).upper()
        rows.append({**period_metadata,
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
    period_metadata = _broker_period_metadata(ctx, multiday_manifest=multiday_manifest)
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
        row_period = {
            key: _value(detail_row, key, default=value)
            for key, value in period_metadata.items()
        }
        rows.append({**row_period,
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
            "today_pulse_date": _value(detail_row, "today_pulse_date", default=""),
            "today_pulse_snapshot_id": _value(detail_row, "today_pulse_snapshot_id", default=""),
            "today_pulse_status": _value(detail_row, "today_pulse_status", default=""),
            "today_pulse_net_flow": _value(detail_row, "today_pulse_net_flow", default=""),
            "today_pulse_buy_days": _value(detail_row, "today_pulse_buy_days", default=""),
            "today_pulse_sell_days": _value(detail_row, "today_pulse_sell_days", default=""),
            "today_pulse_direction": _value(detail_row, "today_pulse_direction", default=""),
            "broker_alignment": _value(detail_row, "broker_alignment", "Broker_Period_Alignment", default=""),
        })
    validation["manifest"] = multiday_manifest
    validation["period_metadata"] = period_metadata
    return [row for row in rows if row["symbol"]], validation, (summary_path, detail_path, manifest_path)


def broker_multiday_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    rows, validation, paths = _multiday_rows(ctx)
    period_metadata = validation.get("period_metadata", {})
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        **period_metadata,
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


def _final_watchlist_multiday_map(ctx: RunnerContext) -> dict[str, dict[str, Any]]:
    """Read the primary broker window for presentation without changing decisions.

    Final Decision remains the sole decision owner.  This map only makes the
    Telegram broker card internally consistent: classification, flow, session
    counts, concentration and cost all come from the same primary window.
    Missing historical multi-day artifacts simply return an empty map so a
    delivery-only resend can still use its stored decision/entry artifacts.
    """
    output_dir = ctx.path("broker_multiday_output_dir", "data/output/broker_multiday")
    window_path = output_dir / "BROKER_WINDOW_COMPARISON.csv"
    summary_path = output_dir / "BROKER_MULTIDAY_SUMMARY.csv"
    detail_path = output_dir / "BROKER_MULTIDAY_DETAIL.csv"
    manifest_path = output_dir / "BROKER_MULTIDAY_MANIFEST.json"
    if not window_path.exists() or not summary_path.exists():
        return {}
    try:
        windows = pd.read_csv(window_path, low_memory=False)
        summaries = pd.read_csv(summary_path, low_memory=False)
        details = pd.read_csv(detail_path, low_memory=False) if detail_path.exists() else pd.DataFrame()
    except Exception:
        return {}
    multiday_manifest = read_json(manifest_path) if manifest_path.exists() else {}
    period_metadata = _broker_period_metadata(ctx, multiday_manifest=multiday_manifest)
    period_type = str(period_metadata.get("broker_period_type", "")).upper()
    configured_window = str(ctx.config.get("broker", {}).get("primary_window", "5D")).upper()
    if period_type in {"1D", "3D", "5D", "10D", "20D", "CUSTOM"}:
        target_window = period_type
        if period_type == "CUSTOM":
            # CUSTOM is represented explicitly when the engine supports the
            # requested session count; old output falls back to the configured
            # comparison row without changing the selected provenance.
            target_window = "CUSTOM"
            if not any(
                str(_value(item, "Window", default="")).upper() == "CUSTOM"
                for item in windows.to_dict(orient="records")
            ):
                target_window = configured_window if configured_window in {"1D", "3D", "5D", "10D", "20D"} else "5D"
    else:
        target_window = ""

    summary_map = {
        _symbol(_value(row, "Symbol", "EMITEN", "Ticker")): row
        for row in summaries.to_dict(orient="records")
        if _symbol(_value(row, "Symbol", "EMITEN", "Ticker"))
    }
    detail_map = {
        _symbol(_value(row, "Symbol", "EMITEN", "Ticker")): row
        for row in details.to_dict(orient="records")
        if _symbol(_value(row, "Symbol", "EMITEN", "Ticker"))
    }
    result: dict[str, dict[str, Any]] = {}
    placeholders = {"", "UNKNOWN", "NO_DATA", "NO DATA", "INSUFFICIENT_DATA", "INSUFFICIENT DATA"}
    for row in windows.to_dict(orient="records"):
        symbol = _symbol(_value(row, "Symbol", "EMITEN", "Ticker"))
        if not symbol:
            continue
        primary = str(_value(row, "Primary_Window", default="5D") or "5D").upper()
        window = str(_value(row, "Window", default="")).upper()
        expected_window = target_window or primary
        if window != expected_window:
            continue
        summary = summary_map.get(symbol, {})
        detail = detail_map.get(symbol, {})
        classification = str(_value(row, "Classification", default=_value(summary, "Context", default="INSUFFICIENT_DATA"))).upper()
        available = int(round(_float(_value(row, "available_sessions", "Available_Sessions", default=0), 0.0)))
        positive_ratio = _float(_value(row, "positive_day_ratio", "Positive_Day_Ratio", default=0), 0.0)
        negative_ratio = _float(_value(row, "negative_day_ratio", "Negative_Day_Ratio", default=0), 0.0)
        net_flow = _float(_value(row, "cumulative_net_value", "Cumulative_Net_Value", default=0), 0.0)
        pattern = str(_value(detail, "Divergence_Label", default="")).upper()
        if pattern in placeholders:
            pattern = classification
        if classification in placeholders:
            persistence = "INSUFFICIENT_DATA"
        else:
            persistence_alias = "Buyer_Rotation_Status" if net_flow >= 0 else "Seller_Rotation_Status"
            persistence = str(_value(detail, persistence_alias, default="")).upper()
            if persistence in placeholders:
                persistence = classification
        def period_value(key: str) -> Any:
            return _value(
                row,
                key,
                default=_value(
                    detail,
                    key,
                    default=_value(summary, key, default=period_metadata.get(key, "")),
                ),
            )

        result[symbol] = {
            "primary_window": str(period_value("broker_period_type") or primary).upper(),
            "broker_status": classification,
            # The summary Confidence is 0-100; the signed classification Score
            # is intentionally not displayed as a /100 score.
            "broker_score": _value(summary, "Confidence", "Broker_MultiDay_Confidence", default=""),
            "broker_net_flow": _value(row, "cumulative_net_value", "Cumulative_Net_Value", default=""),
            "buy_days": int(round(positive_ratio * available)),
            "sell_days": int(round(negative_ratio * available)),
            "buyer_concentration": _value(row, "buyer_concentration", "Buyer_Concentration", default=""),
            "seller_concentration": _value(row, "seller_concentration", "Seller_Concentration", default=""),
            "broker_pattern": pattern,
            "bandar_buy_cost": _value(row, "weighted_broker_buy_cost", "Weighted_Broker_Buy_Cost", default=""),
            "distance_to_buy_cost": _value(row, "distance_to_buy_cost_pct", "Distance_To_Buy_Cost_Pct", default=""),
            "multi_day_flow": classification,
            "flow_persistence": persistence,
            "broker_period_type": period_value("broker_period_type"),
            "broker_period_start": period_value("broker_period_start"),
            "broker_period_end": period_value("broker_period_end"),
            "broker_trading_days": period_value("broker_trading_days"),
            "broker_session_dates": period_value("broker_session_dates"),
            "broker_snapshot_id": period_value("broker_snapshot_id"),
            "broker_period_source": period_value("broker_period_source"),
            "broker_coverage": period_value("broker_coverage"),
            "broker_session_coverage": period_value("broker_session_coverage"),
            "broker_coverage_text": period_value("broker_coverage_text"),
            "broker_coverage_status": period_value("broker_coverage_status"),
            "broker_freshness_status": period_value("broker_freshness_status"),
            "today_pulse_date": _value(detail, "today_pulse_date", default=""),
            "today_pulse_snapshot_id": _value(detail, "today_pulse_snapshot_id", default=""),
            "today_pulse_status": _value(detail, "today_pulse_status", default=""),
            "today_pulse_net_flow": _value(detail, "today_pulse_net_flow", default=""),
            "today_pulse_buy_days": _value(detail, "today_pulse_buy_days", default=""),
            "today_pulse_sell_days": _value(detail, "today_pulse_sell_days", default=""),
            "today_pulse_direction": _value(detail, "today_pulse_direction", default=""),
            "broker_alignment": _value(
                detail,
                "broker_alignment",
                "Broker_Period_Alignment",
                default="",
            ),
        }
    return result


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
    multiday_map = _final_watchlist_multiday_map(ctx)
    global_period_metadata = _broker_period_metadata(ctx)
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
            # A veto is auditable in the decision CSV, but never a final
            # watchlist item.
            continue
        plan = plans.get(symbol, {})
        multiday = multiday_map.get(symbol, {})
        technical_state = _value(
            raw,
            "Technical_Confirmation",
            "Technical_Regime",
            default=_value(
                plan,
                "Plan_Status",
                "Execution_Status",
                default=_value(raw, "Technical_State", "Technical_Grade", default=""),
            ),
        )
        broker_fallback = _value(raw, "Broker_Confirmation", "Broker_Direction_Final", default="")
        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "rank": _value(raw, "Rank_V3", "Rank", default=index),
            "symbol": symbol,
            "decision": _value(raw, "Decision_Status_Final", "Decision_V3", "Decision", default=""),
            "risk_flags": _value(raw, "Risk_Flags", default=",".join(zapi.get("risk_flags") or [])),
            "exchange_status": exchange_status,
            "exchange_veto": exchange_veto,
            # Final_Score_V3 is the canonical decision-engine confidence.  The
            # generic Confidence column is retained only as legacy lineage.
            "confidence": _value(raw, "Final_Score_V3", "Final_Score", "Confidence", default=""),
            "setup": _value(raw, "Setup_Type", "Setup_Label", default=""),
            "entry_low": _value(plan, "Entry_Zone_Low", "Entry_Low", "Entry_Min", default=""),
            "entry_high": _value(plan, "Entry_Zone_High", "Entry_High", "Entry_Max", default=""),
            "stop_loss": _value(plan, "Initial_Stop", "Stop_Loss", "Stop", default=""),
            "target_1": _value(plan, "Target_1", "TP1", default=""),
            "target_2": _value(plan, "Target_2", "TP2", default=""),
            # The generic RR line belongs to the executable target plan. A
            # nearby resistance RR is separate context and must not replace it.
            "risk_reward": _final_watchlist_plan_rr(plan),
            "technical_score": _value(raw, "Technical_Score_Final", "Technical_Score", default=""),
            "technical_state": technical_state,
            "broker_score": _final_watchlist_broker_score(multiday, raw),
            "broker_state": multiday.get("broker_status") or broker_fallback,
            "broker_status": multiday.get("broker_status") or broker_fallback or "MISSING",
            "broker_net_flow": multiday.get("broker_net_flow", ""),
            "buy_days": multiday.get("buy_days", ""),
            "sell_days": multiday.get("sell_days", ""),
            "buyer_concentration": multiday.get("buyer_concentration", ""),
            "seller_concentration": multiday.get("seller_concentration", ""),
            "broker_pattern": multiday.get("broker_pattern", ""),
            "bandar_buy_cost": multiday.get("bandar_buy_cost", ""),
            "distance_to_buy_cost": _final_watchlist_distance(multiday, raw),
            "multi_day_flow": multiday.get("multi_day_flow", ""),
            "flow_persistence": multiday.get("flow_persistence", ""),
            "broker_period_type": multiday.get("broker_period_type", global_period_metadata.get("broker_period_type", "")),
            "broker_period_start": multiday.get("broker_period_start", global_period_metadata.get("broker_period_start", "")),
            "broker_period_end": multiday.get("broker_period_end", global_period_metadata.get("broker_period_end", "")),
            "broker_trading_days": multiday.get("broker_trading_days", global_period_metadata.get("broker_trading_days", "")),
            "broker_session_dates": multiday.get("broker_session_dates", global_period_metadata.get("broker_session_dates", [])),
            "broker_snapshot_id": multiday.get("broker_snapshot_id", global_period_metadata.get("broker_snapshot_id", "")),
            "broker_period_source": multiday.get("broker_period_source", global_period_metadata.get("broker_period_source", "")),
            "broker_coverage": multiday.get("broker_coverage", global_period_metadata.get("broker_coverage", "")),
            "broker_session_coverage": multiday.get("broker_session_coverage", global_period_metadata.get("broker_session_coverage", "")),
            "broker_coverage_text": multiday.get("broker_coverage_text", global_period_metadata.get("broker_coverage_text", "")),
            "broker_coverage_status": multiday.get("broker_coverage_status", global_period_metadata.get("broker_coverage_status", "")),
            "broker_freshness_status": multiday.get("broker_freshness_status", global_period_metadata.get("broker_freshness_status", "")),
            "today_pulse_date": multiday.get("today_pulse_date", ""),
            "today_pulse_snapshot_id": multiday.get("today_pulse_snapshot_id", ""),
            "today_pulse_status": multiday.get("today_pulse_status", ""),
            "today_pulse_net_flow": multiday.get("today_pulse_net_flow", ""),
            "today_pulse_buy_days": multiday.get("today_pulse_buy_days", ""),
            "today_pulse_sell_days": multiday.get("today_pulse_sell_days", ""),
            "today_pulse_direction": multiday.get("today_pulse_direction", ""),
            "broker_alignment": multiday.get("broker_alignment", _value(raw, "broker_alignment", default="")),
            "sector_state": _value(raw, "Sector_State", "Sector_Rotation_State", default=""),
            "market_regime": _value(raw, "Market_Regime", default=""),
            # Decision_Reasons is produced by the single-session Broker Fusion
            # and can contradict the multi-day broker facts displayed above.
            # Leave it out so the existing builder uses its consistent
            # technical + multi-day fallback/interpreter path instead.
            "main_reason": _final_watchlist_reason(raw),
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
        })
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        **global_period_metadata,
        "rows": rows,
        "provider": provider,
        "source_mode": mode,
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
    lineage_inputs = [decisions_path, entry_path, decision_manifest_path, *zapi_inputs]
    multiday_dir = ctx.path("broker_multiday_output_dir", "data/output/broker_multiday")
    for candidate in (
        multiday_dir / "BROKER_WINDOW_COMPARISON.csv",
        multiday_dir / "BROKER_MULTIDAY_SUMMARY.csv",
        multiday_dir / "BROKER_MULTIDAY_DETAIL.csv",
    ):
        if candidate.exists():
            lineage_inputs.append(candidate)
    return [_artifact_payload(_artifact_with_lineage(
        artifact,
        input_paths=lineage_inputs,
        source_of_truth=lineage_inputs,
        row_count=len(rows),
        validation_details=validation,
    )) for artifact in artifacts]


def lifecycle_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    """Load persistent material lifecycle status changes.

    Active recommendations are intentionally excluded here.  They are a
    maintenance report and are sent from the Performance & Evaluation menu,
    while this bridge only carries material status changes from scheduled and
    full-manual engine deliveries.
    """
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
        payloads.extend(broker_multiday_payloads(ctx))
    if include_final:
        payloads.extend(final_watchlist_payloads(ctx))
        payloads.extend(lifecycle_payloads(ctx))
    return payloads

# FINAL_WATCHLIST_RUNTIME_BRIDGE_V2
_fw_original_builder = _builder


def _builder(ctx: RunnerContext) -> EnhancedDailyReportBuilder:
    builder = _fw_original_builder(ctx)
    cfg = ctx.scheduler_config.get("enhanced_reporting", {})
    builder.historical_dir = ctx.path("historical_dir", "data/output/historical/by_symbol")
    builder.chart_output_root = resolve(cfg.get("final_watchlist_chart_output_root", "output/final_watchlist"))
    return builder


_fw_original_artifact_payload = _artifact_payload


def _artifact_payload(artifact: DailyReportArtifact) -> ReportPayload:
    payload = _fw_original_artifact_payload(artifact)
    details = dict(artifact.validation_details or {})
    material = str(details.get("material_signature") or "").strip()
    if material:
        payload.material_signature = material
        payload.signal_version = material
    return payload


_fw_original_artifact_with_lineage = _artifact_with_lineage


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
