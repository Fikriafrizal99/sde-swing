from __future__ import annotations

"""Strict source validation for report payloads.

Reports are presentation-only.  This module keeps the boundary explicit: a
report may only be built after its engine-owned source files and required
fields have been validated.  Missing data is an error at this boundary, not a
formatter fallback such as ``UNKNOWN`` or ``DATA_NOT_AVAILABLE``.
"""

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, TYPE_CHECKING

import pandas as pd

from swing_utils import find_col

if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    from .runtime import RunnerContext


MISSING_MARKERS = {
    "",
    "nan",
    "none",
    "null",
    "n/a",
    "na",
    "not available",
    "not_available",
    "data_not_available",
    "not configured",
    "not_configured",
    "unknown",
}


class ReportSourceValidationError(ValueError):
    """Raised when a report source cannot satisfy its published contract."""

    def __init__(
        self,
        report_type: str,
        errors: Iterable[str],
        *,
        input_paths: Iterable[str | Path] = (),
        source_of_truth: Iterable[str | Path] = (),
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.report_type = str(report_type)
        self.errors = [str(error) for error in errors if str(error).strip()]
        self.input_paths = [str(path) for path in input_paths]
        self.source_of_truth = [str(path) for path in source_of_truth]
        self.details = dict(details or {})
        message = f"{self.report_type} source validation failed: " + "; ".join(self.errors or ["VALIDATION_ERROR"])
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_type": self.report_type,
            "status": "ERROR",
            "errors": list(self.errors),
            "input_paths": list(self.input_paths),
            "source_of_truth": list(self.source_of_truth),
            "details": dict(self.details),
        }


def is_missing(value: Any) -> bool:
    """Return True only for values that cannot represent a source fact."""

    if value is None:
        return True
    if value is pd.NA:
        return True
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return not math.isfinite(float(value))
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    try:
        if bool(pd.isna(value)):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in MISSING_MARKERS


def require_file(path: str | Path, label: str) -> Path:
    resolved = Path(path)
    if not resolved.exists():
        raise ReportSourceValidationError(label, [f"INPUT_FILE_NOT_FOUND:{resolved}"], input_paths=[resolved])
    if resolved.is_file() and resolved.stat().st_size <= 0:
        raise ReportSourceValidationError(label, [f"INPUT_FILE_EMPTY:{resolved}"], input_paths=[resolved])
    return resolved


def read_required_json(path: str | Path, label: str) -> dict[str, Any]:
    resolved = require_file(path, label)
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReportSourceValidationError(label, [f"INPUT_JSON_INVALID:{type(exc).__name__}:{exc}"], input_paths=[resolved]) from exc
    if not isinstance(payload, dict) or not payload:
        raise ReportSourceValidationError(label, ["INPUT_JSON_OBJECT_EMPTY"], input_paths=[resolved])
    return payload


def read_required_csv(path: str | Path, label: str) -> pd.DataFrame:
    resolved = require_file(path, label)
    try:
        frame = pd.read_csv(resolved, low_memory=False)
    except Exception as exc:
        raise ReportSourceValidationError(label, [f"INPUT_CSV_INVALID:{type(exc).__name__}:{exc}"], input_paths=[resolved]) from exc
    if frame.empty:
        raise ReportSourceValidationError(label, ["INPUT_CSV_NO_ROWS"], input_paths=[resolved])
    return frame


def require_mapping_fields(
    payload: Mapping[str, Any],
    fields: Mapping[str, Iterable[str]],
    label: str,
    *,
    input_paths: Iterable[str | Path] = (),
    source_of_truth: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Resolve required aliases and reject missing source values."""

    errors: list[str] = []
    values: dict[str, Any] = {}
    for canonical, aliases in fields.items():
        found = next((payload.get(alias) for alias in aliases if alias in payload), None)
        if is_missing(found):
            errors.append(f"FIELD_EMPTY:{canonical}")
        else:
            values[canonical] = found
    if errors:
        raise ReportSourceValidationError(
            label,
            errors,
            input_paths=input_paths,
            source_of_truth=source_of_truth,
        )
    return values


def require_columns(
    frame: pd.DataFrame,
    fields: Mapping[str, Iterable[str]],
    label: str,
    *,
    input_paths: Iterable[str | Path] = (),
    source_of_truth: Iterable[str | Path] = (),
    require_values_for: Iterable[str] = (),
) -> dict[str, str]:
    """Resolve aliases in a DataFrame and reject missing columns/values."""

    errors: list[str] = []
    resolved: dict[str, str] = {}
    value_fields = set(require_values_for)
    for canonical, aliases in fields.items():
        column = find_col(frame, *tuple(aliases))
        if column is None:
            errors.append(f"COLUMN_MISSING:{canonical}")
            continue
        resolved[canonical] = column
        if canonical in value_fields:
            values = frame[column]
            if values.map(is_missing).all():
                errors.append(f"COLUMN_VALUES_EMPTY:{canonical}")
    if errors:
        raise ReportSourceValidationError(
            label,
            errors,
            input_paths=input_paths,
            source_of_truth=source_of_truth,
            details={"columns": resolved, "rows": int(len(frame))},
        )
    return resolved


def _normalise_coverage(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    normalised = number * 100.0 if number <= 1 else number
    return normalised if normalised <= 100.0 else None


def validate_market_outlook_sources(
    global_snapshot: Mapping[str, Any],
    market_status: Mapping[str, Any],
    *,
    rotation: Mapping[str, Any] | None = None,
    input_paths: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Validate the exact engine fields consumed by Market Outlook."""

    sentiment = global_snapshot.get("global_sentiment") if isinstance(global_snapshot.get("global_sentiment"), Mapping) else {}
    source_meta = global_snapshot.get("source_metadata") if isinstance(global_snapshot.get("source_metadata"), Mapping) else {}
    sector = market_status.get("sector_rotation")
    if not isinstance(sector, Mapping):
        sector = rotation if isinstance(rotation, Mapping) else None
    if isinstance(sector, Mapping) and isinstance(sector.get("sector_rotation"), Mapping):
        sector = sector.get("sector_rotation")
    errors: list[str] = []
    field_values = {
        "market_regime": next((market_status.get(key) for key in ("market_regime", "regime") if not is_missing(market_status.get(key))), None),
        "ihsg_change": next((market_status.get(key) for key in ("ihsg_change_pct", "change_pct", "ihsg_change") if not is_missing(market_status.get(key))), None),
        "ihsg_trend": next((market_status.get(key) for key in ("trend", "ihsg_trend") if not is_missing(market_status.get(key))), None),
        "ihsg_momentum": next((market_status.get(key) for key in ("momentum", "ihsg_momentum") if not is_missing(market_status.get(key))), None),
        "breadth": next((market_status.get(key) for key in ("breadth", "market_breadth") if not is_missing(market_status.get(key))), None),
        "execution_mode": market_status.get("execution_mode"),
        "provider": global_snapshot.get("provider") or market_status.get("provider") or source_meta.get("provider"),
        "source_mode": global_snapshot.get("source_mode") or market_status.get("source_mode") or source_meta.get("data_source_mode"),
        "coverage": global_snapshot.get("coverage_ratio", sentiment.get("coverage_ratio")),
    }
    for name, value in field_values.items():
        if name == "coverage":
            coverage = _normalise_coverage(value)
            if coverage is None or coverage <= 0:
                errors.append("FIELD_EMPTY:coverage")
        elif is_missing(value):
            errors.append(f"FIELD_EMPTY:{name}")
    if market_status.get("is_stale") is True:
        errors.append("MARKET_DATA_STALE")
    # The canonical artifact uses the four requested buckets.  Legacy aliases
    # are accepted only as an input compatibility shim and are normalized
    # before the report builder consumes them.
    if isinstance(sector, Mapping):
        sector = dict(sector)
        sector.setdefault("improving", sector.get("rotating_in"))
        sector.setdefault("lagging", sector.get("rotating_out"))
    rotation_keys = ("leading", "improving", "weakening", "lagging")
    if not isinstance(sector, Mapping):
        errors.append("FIELD_EMPTY:sector_rotation")
    else:
        for key in rotation_keys:
            if key not in sector or sector.get(key) is None:
                errors.append(f"FIELD_MISSING:sector_rotation.{key}")
        rotation_status = str(sector.get("status", "VALID")).upper()
        rotation_coverage = _normalise_coverage(sector.get("coverage", 1.0))
        if rotation_status in {"INSUFFICIENT_DATA", "NOT_CONFIGURED", "UNAVAILABLE"}:
            errors.append(f"SECTOR_ROTATION_STATUS:{rotation_status}")
        if rotation_coverage is None or rotation_coverage <= 0:
            errors.append("FIELD_EMPTY:sector_rotation.coverage")
    if errors:
        raise ReportSourceValidationError(
            "market_outlook",
            errors,
            input_paths=input_paths,
            source_of_truth=input_paths,
            details={"rows": len(global_snapshot.get("instruments", []) or [])},
        )
    return {
        **field_values,
        "coverage": _normalise_coverage(field_values["coverage"]),
        "sector_rotation": dict(sector),
        "global_sentiment": dict(sentiment),
    }


def validate_post_market_sources(
    manifest: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    input_paths: Iterable[str | Path] = (),
) -> dict[str, Any]:
    snapshot_id = snapshot.get("snapshot_id")
    manifest_id = manifest.get("Snapshot_ID") or manifest.get("snapshot_id")
    snapshot_date = snapshot.get("trade_date")
    manifest_date = manifest.get("Technical_Date") or manifest.get("trade_date")
    source_meta = snapshot.get("source_metadata") if isinstance(snapshot.get("source_metadata"), Mapping) else {}
    reconciliation = snapshot.get("reconciliation") if isinstance(snapshot.get("reconciliation"), Mapping) else {}
    errors: list[str] = []
    if is_missing(snapshot_id):
        errors.append("FIELD_EMPTY:snapshot_id")
    if is_missing(manifest_id):
        errors.append("FIELD_EMPTY:manifest_snapshot_id")
    elif not is_missing(snapshot_id) and str(snapshot_id) != str(manifest_id):
        errors.append(f"SNAPSHOT_ID_MISMATCH:{snapshot_id}!={manifest_id}")
    if is_missing(snapshot_date):
        errors.append("FIELD_EMPTY:trade_date")
    if is_missing(manifest_date):
        errors.append("FIELD_EMPTY:manifest_trade_date")
    elif not is_missing(snapshot_date) and str(snapshot_date) != str(manifest_date):
        errors.append(f"SNAPSHOT_DATE_MISMATCH:{snapshot_date}!={manifest_date}")
    required = {
        "snapshot_id": snapshot_id,
        "trade_date": snapshot_date,
        "provider": source_meta.get("provider") or snapshot.get("source_provider"),
        "source_mode": source_meta.get("data_source_mode"),
        "coverage": source_meta.get("source_coverage_ratio", snapshot.get("source_coverage_ratio")),
    }
    for name, value in required.items():
        if name == "coverage":
            coverage = _normalise_coverage(value)
            if coverage is None or coverage <= 0:
                errors.append("FIELD_EMPTY:coverage")
        elif is_missing(value):
            errors.append(f"FIELD_EMPTY:{name}")
    count_fields = {
        "symbols_requested": snapshot.get("symbols_requested", manifest.get("symbols_requested")),
        "symbols_loaded": snapshot.get("symbols_loaded", manifest.get("symbols_loaded")),
        "symbols_valid": snapshot.get("symbols_valid", manifest.get("symbols_valid")),
        "symbols_failed": snapshot.get("symbols_failed", manifest.get("symbols_failed")),
        "symbols_skipped": snapshot.get("symbols_skipped", manifest.get("symbols_skipped")),
    }
    for name, value in count_fields.items():
        if is_missing(value):
            errors.append(f"FIELD_EMPTY:{name}")
    for name, value in {
        "symbols_requested": manifest.get("symbols_requested"),
        "symbols_loaded": manifest.get("symbols_loaded"),
        "symbols_valid": manifest.get("symbols_valid"),
        "symbols_failed": manifest.get("symbols_failed"),
        "symbols_skipped": manifest.get("symbols_skipped"),
    }.items():
        if value is not None and not is_missing(count_fields[name]):
            try:
                count_mismatch = int(value) != int(count_fields[name])
            except (TypeError, ValueError):
                count_mismatch = str(value) != str(count_fields[name])
            if count_mismatch:
                errors.append(f"SNAPSHOT_COUNT_MISMATCH:{name}:{count_fields[name]}!={value}")
    if "data_source_mode" in manifest and not is_missing(manifest.get("data_source_mode")):
        if str(manifest.get("data_source_mode")) != str(required["source_mode"]):
            errors.append(f"SOURCE_MODE_MISMATCH:{required['source_mode']}!={manifest.get('data_source_mode')}")
    if "source_coverage_ratio" in manifest and not is_missing(manifest.get("source_coverage_ratio")):
        manifest_coverage = _normalise_coverage(manifest.get("source_coverage_ratio"))
        snapshot_coverage = _normalise_coverage(required["coverage"])
        if manifest_coverage is None or snapshot_coverage is None or abs(manifest_coverage - snapshot_coverage) > 0.01:
            errors.append(f"SOURCE_COVERAGE_MISMATCH:{snapshot_coverage}!={manifest_coverage}")
    output_paths = snapshot.get("output_paths") if isinstance(snapshot.get("output_paths"), Mapping) else {}
    for key in ("technical_features", "technical_candidates"):
        raw = output_paths.get(key)
        if is_missing(raw):
            errors.append(f"OUTPUT_PATH_MISSING:{key}")
        elif not Path(str(raw)).exists():
            errors.append(f"OUTPUT_FILE_NOT_FOUND:{key}:{raw}")
    if errors:
        raise ReportSourceValidationError("post_market", errors, input_paths=input_paths, source_of_truth=input_paths)
    return {
        **required,
        "coverage": _normalise_coverage(required["coverage"]),
        "output_paths": dict(output_paths),
        "symbols_requested": int(count_fields["symbols_requested"] or 0),
        "symbols_loaded": int(count_fields["symbols_loaded"] or 0),
        "symbols_valid": int(count_fields["symbols_valid"] or 0),
        "symbols_failed": int(count_fields["symbols_failed"] or 0),
        "symbols_skipped": int(count_fields["symbols_skipped"] or 0),
        "reconciliation": dict(reconciliation),
        "zapi_status": reconciliation.get("status") or source_meta.get("zapi_status") or "ZAPI_LINEAGE_MISSING",
        "zapi_coverage": _normalise_coverage(
            reconciliation.get("coverage_ratio", source_meta.get("zapi_coverage_ratio"))
        ) or 0.0,
        "degraded_reason": reconciliation.get("reason") or source_meta.get("degraded_reason") or "",
    }


def validate_broker_summary_source(frame: pd.DataFrame, path: str | Path) -> dict[str, Any]:
    columns = require_columns(
        frame,
        {
            "symbol": ("Symbol", "EMITEN", "Ticker"),
            "broker_state": ("Broker_Confirmation", "Broker_Direction_Final", "Broker_Direction", "Broker_State"),
            "broker_score": ("Broker_Score", "Broker_Confidence_Final", "Broker_Confidence"),
            "net_flow": ("NET_FLOW", "Net_Flow", "Net Flow"),
        },
        "broker_summary",
        input_paths=[path],
        source_of_truth=[path],
        require_values_for=("symbol", "broker_state", "broker_score", "net_flow"),
    )
    errors = [
        f"FIELD_VALUE_EMPTY:{canonical}:rows={','.join(str(index) for index in frame.index[frame[column].map(is_missing)])}"
        for canonical, column in columns.items()
        if frame[column].map(is_missing).any()
    ]
    if errors:
        raise ReportSourceValidationError(
            "broker_summary",
            errors,
            input_paths=[path],
            source_of_truth=[path],
        )
    return {"columns": columns, "rows": int(len(frame))}


def validate_broker_multiday_source(frame: pd.DataFrame, path: str | Path) -> dict[str, Any]:
    columns = require_columns(
        frame,
        {
            "symbol": ("Symbol", "EMITEN", "Ticker"),
            "context": ("Context", "Broker_MultiDay_Context", "Overall_State"),
            "score": ("Score", "Broker_MultiDay_Score"),
            "confidence": ("Confidence", "Broker_MultiDay_Confidence"),
            "blocker": ("Blocker", "Broker_MultiDay_Blocker"),
        },
        "broker_multi_day",
        input_paths=[path],
        source_of_truth=[path],
        require_values_for=("symbol", "context", "score", "confidence", "blocker"),
    )
    errors = [
        f"FIELD_VALUE_EMPTY:{canonical}:rows={','.join(str(index) for index in frame.index[frame[column].map(is_missing)])}"
        for canonical, column in columns.items()
        if frame[column].map(is_missing).any()
    ]
    if errors:
        raise ReportSourceValidationError(
            "broker_multi_day",
            errors,
            input_paths=[path],
            source_of_truth=[path],
        )
    quality_column = find_col(frame, "Data_Quality_Status", "data_status")
    if quality_column is not None:
        quality = frame[quality_column].astype(str).str.strip().str.upper()
        if (~quality.eq("VALID")).any():
            raise ReportSourceValidationError(
                "broker_multi_day",
                ["DATA_QUALITY_NOT_VALID"],
                input_paths=[path],
                source_of_truth=[path],
                details={"quality_counts": quality.value_counts().to_dict()},
            )
    context_fields = {
        "context_1d": ("Broker_Context_1D", "state_1d"),
        "context_3d": ("Broker_Context_3D", "state_3d"),
        "context_5d": ("Broker_Context_5D", "state_5d"),
        "context_10d": ("Broker_Context_10D", "state_10d"),
        "context_20d": ("Broker_Context_20D", "state_20d"),
    }
    context_errors: list[str] = []
    for canonical, aliases in context_fields.items():
        column = find_col(frame, *aliases)
        if column is not None and frame[column].map(is_missing).any():
            rows = ",".join(str(index) for index in frame.index[frame[column].map(is_missing)])
            context_errors.append(f"FIELD_VALUE_EMPTY:{canonical}:rows={rows}")
    if context_errors:
        raise ReportSourceValidationError(
            "broker_multi_day",
            context_errors,
            input_paths=[path],
            source_of_truth=[path],
        )
    return {"columns": columns, "rows": int(len(frame))}


def validate_final_watchlist_sources(
    decisions: pd.DataFrame,
    entry_plans: pd.DataFrame,
    *,
    decision_path: str | Path,
    entry_plan_path: str | Path,
) -> dict[str, Any]:
    decision_columns = require_columns(
        decisions,
        {
            "symbol": ("Symbol", "EMITEN", "Ticker"),
            "decision": ("Decision_Status_Final", "Decision_V3", "Decision"),
        },
        "final_watchlist_decision",
        input_paths=[decision_path],
        source_of_truth=[decision_path],
        require_values_for=("symbol", "decision"),
    )
    plan_columns = require_columns(
        entry_plans,
        {
            "symbol": ("Symbol", "EMITEN", "Ticker"),
            "entry_low": ("Entry_Zone_Low", "Entry_Low", "Entry_Min"),
            "entry_high": ("Entry_Zone_High", "Entry_High", "Entry_Max"),
            "stop_loss": ("Initial_Stop", "Stop_Loss", "Stop"),
            "target_1": ("Target_1", "TP1"),
            "target_2": ("Target_2", "TP2"),
            "risk_reward": ("RR_To_Resistance", "RR_To_Minor_Resistance", "Risk_Reward", "RR"),
        },
        "final_watchlist_entry_plans",
        input_paths=[entry_plan_path],
        source_of_truth=[entry_plan_path],
        require_values_for=("symbol",),
    )
    decision_errors = [
        f"FIELD_VALUE_EMPTY:{canonical}:rows={','.join(str(index) for index in decisions.index[decisions[column].map(is_missing)])}"
        for canonical, column in decision_columns.items()
        if decisions[column].map(is_missing).any()
    ]
    plan_symbol = plan_columns["symbol"]
    if entry_plans[plan_symbol].map(is_missing).any():
        decision_errors.append(
            f"FIELD_VALUE_EMPTY:entry_plan.symbol:rows={','.join(str(index) for index in entry_plans.index[entry_plans[plan_symbol].map(is_missing)])}"
        )
    if decision_errors:
        raise ReportSourceValidationError(
            "final_watchlist",
            decision_errors,
            input_paths=[decision_path, entry_plan_path],
            source_of_truth=[decision_path, entry_plan_path],
        )
    actionable = decisions[decisions[decision_columns["decision"]].astype(str).str.upper().str.replace("_", " ").isin({"BUY", "BUY READY", "BUY CONFIRMED", "BUY CANDIDATE", "BUY ON TRIGGER"})]
    if not actionable.empty:
        decision_symbols = set(actionable[decision_columns["symbol"]].astype(str).str.upper().str.replace(".JK", "", regex=False))
        plan_symbols = set(entry_plans[plan_columns["symbol"]].astype(str).str.upper().str.replace(".JK", "", regex=False))
        missing = sorted(decision_symbols - plan_symbols)
        if missing:
            raise ReportSourceValidationError(
                "final_watchlist",
                [f"ENTRY_PLAN_MISSING_FOR_SYMBOLS:{','.join(missing)}"],
                input_paths=[decision_path, entry_plan_path],
                source_of_truth=[decision_path, entry_plan_path],
            )
        plan_errors: list[str] = []
        for symbol in sorted(decision_symbols & plan_symbols):
            rows = entry_plans[entry_plans[plan_columns["symbol"]].astype(str).str.upper().str.replace(".JK", "", regex=False).eq(symbol)]
            if rows.empty:
                continue
            row = rows.iloc[0]
            for canonical in ("entry_low", "entry_high", "stop_loss", "target_1", "target_2", "risk_reward"):
                if is_missing(row.get(plan_columns[canonical])):
                    plan_errors.append(f"ENTRY_PLAN_FIELD_EMPTY:{symbol}:{canonical}")
        if plan_errors:
            raise ReportSourceValidationError(
                "final_watchlist",
                plan_errors,
                input_paths=[decision_path, entry_plan_path],
                source_of_truth=[decision_path, entry_plan_path],
            )
    return {
        "decision_columns": decision_columns,
        "plan_columns": plan_columns,
        "decision_rows": int(len(decisions)),
        "entry_plan_rows": int(len(entry_plans)),
        "actionable_rows": int(len(actionable)),
    }


def report_audit_path(ctx: "RunnerContext") -> Path:
    root = ctx.path("reports_root", "data/output/reports") / "audit"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{ctx.trade_date.isoformat()}.jsonl"


def append_report_audit(
    ctx: "RunnerContext",
    report_type: str,
    status: str,
    *,
    input_paths: Iterable[str | Path] = (),
    source_of_truth: Iterable[str | Path] = (),
    rows: int | None = None,
    details: Mapping[str, Any] | None = None,
    output_paths: Iterable[str | Path] = (),
    errors: Iterable[str] = (),
) -> Path:
    """Append one machine-readable source-lineage event for a report."""

    event = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_id": ctx.run_id,
        "job": ctx.job,
        "trade_date": ctx.trade_date.isoformat(),
        "report_type": report_type,
        "status": str(status).upper(),
        "input_paths": [str(path) for path in input_paths],
        "source_of_truth": [str(path) for path in source_of_truth],
        "rows": rows,
        "details": dict(details or {}),
        "output_paths": [str(path) for path in output_paths],
        "errors": [str(error) for error in errors if str(error).strip()],
    }
    path = report_audit_path(ctx)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    return path


def record_validation_error(ctx: "RunnerContext", error: ReportSourceValidationError) -> Path:
    return append_report_audit(
        ctx,
        error.report_type,
        "ERROR",
        input_paths=error.input_paths,
        source_of_truth=error.source_of_truth,
        details=error.details,
        errors=error.errors,
    )
