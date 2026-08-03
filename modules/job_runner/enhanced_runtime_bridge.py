from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from modules.job_runner.enhanced_daily_reports import DailyReportArtifact, EnhancedDailyReportBuilder
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import RunnerContext, read_json, resolve


def _norm(value: Any) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "")).strip("_")


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


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _records(path: Path) -> list[dict[str, Any]]:
    frame = _read_csv(path)
    return frame.to_dict(orient="records") if not frame.empty else []


def _latest_file(patterns: Iterable[str]) -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(resolve(".").glob(pattern))
    candidates = [path for path in candidates if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _coverage(valid: int, requested: int) -> float:
    return round((valid / requested * 100.0), 1) if requested > 0 else 0.0


def _provider(ctx: RunnerContext, record_type: str, fallback: str) -> tuple[str, str, float]:
    try:
        metadata = ctx.source_manager.provider_metadata(record_type=record_type)
    except Exception:
        metadata = {}
    provider = str(metadata.get("provider_status") or metadata.get("primary_provider") or fallback)
    mode = str(metadata.get("data_source_mode") or "NOT_CONFIGURED")
    coverage = _float(metadata.get("source_coverage_ratio"), 0.0)
    if 0 <= coverage <= 1:
        coverage *= 100
    return provider, mode, coverage


def _artifact_payload(artifact: DailyReportArtifact) -> ReportPayload:
    filename = f"{artifact.report_type}.txt"
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
    return payload


def _builder(ctx: RunnerContext) -> EnhancedDailyReportBuilder:
    cfg = ctx.scheduler_config.get("enhanced_reporting", {})
    return EnhancedDailyReportBuilder(
        output_root=resolve(cfg.get("output_root", "data/output")),
        max_watchlist_messages=int(cfg.get("max_watchlist_messages", 5)),
    )


def market_outlook_payloads(
    ctx: RunnerContext,
    global_snapshot: dict[str, Any],
    market_status: dict[str, Any],
) -> list[ReportPayload]:
    provider, mode, source_coverage = _provider(ctx, "MarketIndex", str(global_snapshot.get("provider") or "ZAPI IDX"))
    coverage = _float(global_snapshot.get("coverage_ratio"), source_coverage)
    if 0 <= coverage <= 1:
        coverage *= 100
    regime = str(market_status.get("market_regime") or "UNKNOWN").replace("_", " ").upper()
    execution_mode = "SELECTIVE"
    if "STRONG BULL" in regime:
        execution_mode = "SELECTIVE AGGRESSIVE"
    elif "BEAR" in regime:
        execution_mode = "DEFENSIVE"

    rotation_path = _latest_file([
        "data/output/**/sector_rotation*.json",
        "data/output/**/latest_sector_rotation.json",
    ])
    rotation = read_json(rotation_path) if rotation_path else {}
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "market_regime": regime,
        "execution_mode": execution_mode,
        "ihsg_change": market_status.get("change_pct", market_status.get("ihsg_change_pct", 0)),
        "ihsg_trend": market_status.get("trend", market_status.get("trend_state", "UNKNOWN")),
        "ihsg_momentum": market_status.get("momentum", market_status.get("momentum_state", "UNKNOWN")),
        "market_breadth": market_status.get("breadth", market_status.get("breadth_state", "UNKNOWN")),
        "rotating_in": rotation.get("rotating_in", []),
        "leading": rotation.get("leading", []),
        "weakening": rotation.get("weakening", []),
        "rotating_out": rotation.get("rotating_out", []),
        "focus_tomorrow": rotation.get("focus_tomorrow", "Prioritaskan sektor kuat dengan setup valid dan broker flow mendukung."),
        "avoid_guidance": rotation.get("avoid_guidance", "Hindari mengejar harga dan sektor yang momentumnya melemah."),
        "provider": provider,
        "source_mode": mode,
        "coverage": coverage,
    }
    return [_artifact_payload(_builder(ctx).build_market_outlook(data))]


def post_market_payloads(ctx: RunnerContext, manifest: dict[str, Any]) -> list[ReportPayload]:
    requested = int(manifest.get("symbols_requested", manifest.get("symbols_loaded", 0)) or 0)
    loaded = int(manifest.get("symbols_loaded", 0) or 0)
    valid = int(manifest.get("symbols_valid", loaded) or 0)
    failed = int(manifest.get("symbols_failed", max(0, loaded - valid)) or 0)
    skipped = int(manifest.get("symbols_skipped", max(0, requested - loaded)) or 0)
    provider = str(manifest.get("provider_status") or manifest.get("Data_Source") or "NOT_CONFIGURED")
    mode = str(manifest.get("data_source_mode") or manifest.get("Data_Quality_Status") or "NOT_CONFIGURED")
    coverage = _coverage(valid, requested)
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
        "candidate_status": "READY" if int(manifest.get("Candidate_Count", manifest.get("candidate_count", 0)) or 0) > 0 else "EMPTY",
        "broker_status": "READY" if manifest.get("Broker_Navigator_Path") else "WAITING",
        "data_note": f"{failed} saham gagal validasi dan {skipped} saham dilewati.",
        "next_process": "Data siap digunakan untuk Final Watchlist." if valid > 0 else "Periksa sumber data sebelum melanjutkan.",
        "provider": provider,
        "source_mode": mode,
    }
    return [_artifact_payload(_builder(ctx).build_post_market(data))]


def _broker_summary_rows(ctx: RunnerContext) -> list[dict[str, Any]]:
    source = ctx.path("broker_summary_latest", "data/input/broker/BROKER_SUMMARY_LATEST.csv")
    if not source.exists():
        source = ctx.path("broker_raw_latest", "data/input/broker/BROKER_RAW_LATEST.csv")
    rows: list[dict[str, Any]] = []
    for raw in _records(source):
        net = _float(_value(raw, "NET_FLOW", "Net Flow", "TOTAL_BUY"), 0) - _float(_value(raw, "TOTAL_SELL"), 0)
        state = str(_value(raw, "Broker_Confirmation", "broker_state", "Broker_Direction_Final", default="NO_DATA")).upper()
        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "symbol": str(_value(raw, "Symbol", "EMITEN", "Ticker")).upper(),
            "broker_state": state,
            "broker_score": _value(raw, "Broker_Score", "Broker_Confidence_Final", "Broker_Confidence", default=0),
            "net_flow": _value(raw, "NET_FLOW", "Net Flow", default=net),
            "buy_ratio": _value(raw, "BUY_RATIO", "Buy Ratio", default=""),
            "sell_ratio": _value(raw, "SELL_RATIO", "Sell Ratio", default=""),
            "top_buyer": _value(raw, "TOP_BUYER_1", "TOP_BUYER", default=""),
            "top_seller": _value(raw, "TOP_SELLER_1", "TOP_SELLER", default=""),
            "broker_1d": _value(raw, "broker_1d", "state_1d", default=state),
            "broker_3d": _value(raw, "broker_3d", "state_3d", default="NO_DATA"),
            "broker_5d": _value(raw, "broker_5d", "state_5d", default="NO_DATA"),
            "data_status": "VALID",
            "source": "STOCKBIT_FILE",
        })
    return [row for row in rows if row["symbol"]]


def broker_summary_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    rows = _broker_summary_rows(ctx)
    counts = {
        "accumulation_count": sum("ACC" in str(row["broker_state"]).upper() for row in rows),
        "distribution_count": sum("DIST" in str(row["broker_state"]).upper() for row in rows),
        "no_data_count": sum(str(row["broker_state"]).upper() in {"", "NO_DATA", "NEUTRAL"} for row in rows),
    }
    counts["neutral_count"] = max(0, len(rows) - counts["accumulation_count"] - counts["distribution_count"] - counts["no_data_count"])
    valid = len(rows) - counts["no_data_count"]
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "process_status": "SUCCESS" if valid > 0 else "NOT_CONFIGURED",
        **counts,
        "rows": rows,
        "top_accumulation": sorted([r for r in rows if "ACC" in str(r["broker_state"]).upper()], key=lambda r: -_float(r["broker_score"]))[:3],
        "top_distribution": sorted([r for r in rows if "DIST" in str(r["broker_state"]).upper()], key=lambda r: _float(r["broker_score"]))[:3],
        "provider": "STOCKBIT FILE",
        "source_mode": "FILE",
        "coverage": _coverage(valid, len(rows)),
    }
    summary, csv_artifact = _builder(ctx).build_broker_summary(data)
    return [_artifact_payload(summary), _artifact_payload(csv_artifact)]


def _multiday_rows(ctx: RunnerContext) -> list[dict[str, Any]]:
    path = _latest_file([
        "data/output/**/broker*multi*day*.csv",
        "data/output/**/BROKER_MULTI_DAY*.csv",
        "data/output/**/broker_multiday*.csv",
    ])
    if path is None:
        return []
    rows: list[dict[str, Any]] = []
    for raw in _records(path):
        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "symbol": str(_value(raw, "symbol", "emiten", "ticker")).upper(),
            "state_1d": _value(raw, "state_1d", "broker_1d", default="NO_DATA"),
            "state_3d": _value(raw, "state_3d", "broker_3d", default="NO_DATA"),
            "state_5d": _value(raw, "state_5d", "broker_5d", default="NO_DATA"),
            "state_10d": _value(raw, "state_10d", "broker_10d", default="NO_DATA"),
            "state_20d": _value(raw, "state_20d", "broker_20d", default="NO_DATA"),
            "overall_state": _value(raw, "overall_state", "broker_state", default="NO_DATA"),
            "broker_score": _value(raw, "broker_score", "score", default=0),
            "coverage_days": _value(raw, "coverage_days", default=0),
            "missing_days": _value(raw, "missing_days", default=0),
            "data_status": _value(raw, "data_status", default="VALID"),
            "source": _value(raw, "source", default="STOCKBIT_FILE"),
        })
    return [row for row in rows if row["symbol"]]


def broker_multiday_payloads(ctx: RunnerContext) -> list[ReportPayload]:
    rows = _multiday_rows(ctx)
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "process_status": "SUCCESS" if rows else "NOT_CONFIGURED",
        "rows": rows,
        "top_accumulation": [row for row in rows if "ACC" in str(row["overall_state"]).upper()][:3],
        "top_distribution": [row for row in rows if "DIST" in str(row["overall_state"]).upper()][:3],
        "provider": "STOCKBIT FILE",
        "source_mode": "FILE",
        "coverage": _coverage(sum(str(row["data_status"]).upper() == "VALID" for row in rows), len(rows)),
    }
    summary, csv_artifact = _builder(ctx).build_broker_multiday(data)
    return [_artifact_payload(summary), _artifact_payload(csv_artifact)]


def _entry_plan_map() -> dict[str, dict[str, Any]]:
    path = resolve("data/output/exit/ENTRY_PLANS.csv")
    result: dict[str, dict[str, Any]] = {}
    for row in _records(path):
        symbol = str(_value(row, "Symbol", "EMITEN", "Ticker")).upper()
        if symbol:
            result[symbol] = row
    return result


def final_watchlist_payloads(ctx: RunnerContext, manifest: dict[str, Any] | None = None) -> list[ReportPayload]:
    decisions_path = resolve("data/output/decision/FINAL_DECISION_V3.csv")
    plans = _entry_plan_map()
    provider, mode, coverage = _provider(ctx, "DailyBar", "ZAPI IDX")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(_records(decisions_path), start=1):
        symbol = str(_value(raw, "Symbol", "EMITEN", "Ticker")).upper()
        if not symbol:
            continue
        plan = plans.get(symbol, {})
        rows.append({
            "trade_date": ctx.trade_date.isoformat(),
            "rank": _value(raw, "Rank_V3", "Rank", default=index),
            "symbol": symbol,
            "decision": _value(raw, "Decision_Status_Final", "Decision_V3", "Decision", default="WAIT"),
            "confidence": _value(raw, "Confidence", "Final_Score_V3", "Final_Score", default=0),
            "setup": _value(raw, "Setup_Type", "Setup_Label", default="DEVELOPING"),
            "entry_low": _value(plan, "Entry_Low", "Entry_Zone_Low", "Entry_Min", default=_value(raw, "Entry_Low", default="")),
            "entry_high": _value(plan, "Entry_High", "Entry_Zone_High", "Entry_Max", default=_value(raw, "Entry_High", default="")),
            "stop_loss": _value(plan, "Stop_Loss", "Stop", default=_value(raw, "Stop_Loss", default="")),
            "target_1": _value(plan, "Target_1", "TP1", default=_value(raw, "Target_1", "TP1", default="")),
            "target_2": _value(plan, "Target_2", "TP2", default=_value(raw, "Target_2", "TP2", default="")),
            "risk_reward": _value(plan, "Risk_Reward", "RR", default=_value(raw, "Risk_Reward", default="")),
            "technical_score": _value(raw, "Technical_Score", default=0),
            "technical_state": _value(raw, "Technical_State", "Technical_Confirmation", default="UNKNOWN"),
            "broker_score": _value(raw, "Broker_Score", "Broker_Confidence_Final", default=0),
            "broker_state": _value(raw, "Broker_Confirmation", "Broker_Direction_Final", default="NO_DATA"),
            "sector_state": _value(raw, "Sector_State", "Sector_Rotation_State", default="NO_DATA"),
            "market_regime": _value(raw, "Market_Regime", default="UNKNOWN"),
            "main_reason": _value(raw, "Main_Reason", "Decision_Reason", "Reason", default=""),
            "main_risk": _value(raw, "Main_Risk", "Risk_Note", default=""),
            "data_status": _value(raw, "Data_Status", default="VALID"),
            "source": _value(raw, "Source", default=provider),
            "provider": provider,
            "source_mode": mode,
            "coverage": coverage,
        })
    data = {
        "trade_date": ctx.trade_date.isoformat(),
        "rows": rows,
        "provider": provider,
        "source_mode": mode,
        "coverage": coverage,
    }
    return [_artifact_payload(artifact) for artifact in _builder(ctx).build_final_watchlist(data)]


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
    return payloads
