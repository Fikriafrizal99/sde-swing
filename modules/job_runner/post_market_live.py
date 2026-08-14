from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from swing_utils import file_sha256, write_json as _durable_write_json

from modules.market_data.market_outlook_regime import calculate_market_outlook_regime
from modules.telegram.post_market_ui import format_post_market

from .enhanced_runtime_bridge import validated_post_market_payloads as _validated_post_market_payloads
from .runtime import RunnerContext, append_job_log, now_wib, read_json, resolve


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    mapping = {_norm(column): str(column) for column in frame.columns}
    for alias in aliases:
        found = mapping.get(_norm(alias))
        if found is not None:
            return found
    return None


def _read_latest_technical(path: Path, trade_date: date) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 0:
        return pd.DataFrame()
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()
    if frame.empty:
        return frame

    date_column = _column(
        frame,
        "Date",
        "Latest Valid Candle Date",
        "Technical Data Date",
        "Technical_Date",
        "trade_date",
    )
    if date_column is not None:
        parsed = pd.to_datetime(frame[date_column], errors="coerce")
        eligible = parsed.notna() & (parsed.dt.date <= trade_date)
        if eligible.any():
            latest = parsed.loc[eligible].max().date()
            frame = frame.loc[parsed.dt.date == latest].copy()
            frame.attrs["resolved_data_date"] = latest.isoformat()
        else:
            return pd.DataFrame()
    result = frame.reset_index(drop=True)
    result.attrs["resolved_data_date"] = str(frame.attrs.get("resolved_data_date") or "")[:10]
    return result


def _read_csv_optional(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _artifact_trade_date(frame: pd.DataFrame, *aliases: str) -> str:
    """Return one unambiguous artifact date, or empty when the rows disagree."""
    if frame.empty:
        return ""
    column = _column(frame, *aliases)
    if column is None:
        return ""
    parsed = pd.to_datetime(frame[column], errors="coerce").dropna()
    if parsed.empty:
        return ""
    dates = {timestamp.date().isoformat() for timestamp in parsed}
    return next(iter(dates)) if len(dates) == 1 else ""


def _broker_presentation_context(
    ctx: RunnerContext,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify an existing broker summary for presentation, without producing it."""
    manifest = manifest or {}
    explicit_path = str(
        manifest.get("Broker_Summary_Path")
        or manifest.get("broker_summary_path")
        or ""
    ).strip()
    if explicit_path:
        candidate = Path(explicit_path)
        broker_path = candidate if candidate.is_absolute() else resolve(candidate)
    else:
        broker_path = ctx.path(
            "broker_summary_latest",
            "data/input/broker/BROKER_SUMMARY_LATEST.csv",
        )

    result: dict[str, Any] = {
        "broker_status": "NOT_READY",
        "broker_artifact_available": False,
        "broker_data_verified": False,
        "broker_data_current": False,
        "broker_data_date": "",
        "broker_artifact_path": str(broker_path),
        "broker_upstream_status": "UNAVAILABLE",
        "broker_readiness_reason": "FILE_NOT_FOUND",
    }
    try:
        artifact_exists = broker_path.exists()
        artifact_size = broker_path.stat().st_size if artifact_exists else 0
    except OSError as exc:
        result.update({
            "broker_upstream_status": "ARTIFACT_ACCESS_FAILED",
            "broker_readiness_reason": f"ARTIFACT_ACCESS_FAILED:{type(exc).__name__}",
        })
        return result
    if not artifact_exists or artifact_size <= 0:
        return result

    result["broker_artifact_available"] = True
    try:
        frame = pd.read_csv(broker_path, low_memory=False)
    except Exception as exc:
        result.update({
            "broker_upstream_status": "PARSE_FAILED",
            "broker_readiness_reason": f"PARSE_FAILED:{type(exc).__name__}",
        })
        return result
    if frame.empty:
        result.update({
            "broker_upstream_status": "EMPTY_DATA",
            "broker_readiness_reason": "EMPTY_DATA",
        })
        return result

    symbol_col = _column(frame, "EMITEN", "Symbol", "Ticker")
    date_col = _column(frame, "TO_DATE", "TO_DATE_BROKER", "Broker_Data_Date", "Trade_Date")
    buy_col = _column(frame, "TOTAL_BUY", "Total_Buy")
    sell_col = _column(frame, "TOTAL_SELL", "Total_Sell")
    missing = [
        label for label, column in (
            ("SYMBOL", symbol_col),
            ("DATE", date_col),
            ("TOTAL_BUY", buy_col),
            ("TOTAL_SELL", sell_col),
        ) if column is None
    ]
    if missing:
        result.update({
            "broker_upstream_status": "SCHEMA_INVALID",
            "broker_readiness_reason": f"SCHEMA_INVALID:{','.join(missing)}",
        })
        return result
    assert symbol_col and date_col and buy_col and sell_col

    symbols = frame[symbol_col].dropna().astype(str).str.strip()
    parsed_dates = pd.to_datetime(frame[date_col], errors="coerce")
    buy_values = pd.to_numeric(frame[buy_col], errors="coerce")
    sell_values = pd.to_numeric(frame[sell_col], errors="coerce")
    if symbols.empty or (symbols == "").any() or parsed_dates.isna().any():
        result.update({
            "broker_upstream_status": "CONTENT_INVALID",
            "broker_readiness_reason": "CONTENT_INVALID:SYMBOL_OR_DATE",
        })
        return result
    if buy_values.isna().any() or sell_values.isna().any():
        result.update({
            "broker_upstream_status": "INVALID_NUMERIC_DATA",
            "broker_readiness_reason": "INVALID_NUMERIC_DATA",
        })
        return result

    csv_dates = {value.date().isoformat() for value in parsed_dates}
    if len(csv_dates) != 1:
        result.update({
            "broker_upstream_status": "DATE_CONFLICT",
            "broker_readiness_reason": "DATE_CONFLICT",
        })
        return result
    csv_date = next(iter(csv_dates))

    sidecar_path = broker_path.with_suffix(".manifest.json")
    sidecar_exists = sidecar_path.exists()
    sidecar = read_json(sidecar_path)
    if sidecar_exists and not sidecar:
        result.update({
            "broker_upstream_status": "LINEAGE_INVALID",
            "broker_readiness_reason": "LINEAGE_INVALID:SIDECAR_UNREADABLE",
        })
        return result
    status_keys = (
        "status", "Status", "Data_Quality_Status", "data_quality_status",
        "freshness_status", "broker_freshness_status",
    )
    statuses = [
        str(sidecar.get(key)).strip().upper().replace("_", " ")
        for key in status_keys
        if str(sidecar.get(key) or "").strip()
    ]
    status_text = " | ".join(dict.fromkeys(statuses)) or "ARTIFACT_VALIDATED"
    result["broker_upstream_status"] = status_text

    failure_tokens = (
        "FAILED", "ERROR", "INVALID", "FILE NOT FOUND", "EMPTY DATA",
        "PARSE FAILED", "SCHEMA INVALID", "BLOCKED", "UNAVAILABLE",
        "NOT AVAILABLE",
    )
    if any(token in status for status in statuses for token in failure_tokens):
        result["broker_readiness_reason"] = f"UPSTREAM_FAILED:{status_text}"
        return result

    manifest_date_keys = (
        "broker_date", "to_date", "broker_period_end", "trade_date", "Trade_Date",
    )
    manifest_dates: set[str] = set()
    for key in manifest_date_keys:
        raw = sidecar.get(key)
        if raw in (None, ""):
            continue
        parsed = pd.to_datetime(raw, errors="coerce")
        if pd.isna(parsed):
            result.update({
                "broker_upstream_status": "LINEAGE_INVALID",
                "broker_readiness_reason": f"LINEAGE_INVALID:{key}",
            })
            return result
        manifest_dates.add(parsed.date().isoformat())
    if len(manifest_dates) > 1:
        result.update({
            "broker_upstream_status": "LINEAGE_DATE_CONFLICT",
            "broker_readiness_reason": "LINEAGE_DATE_CONFLICT",
        })
        return result
    manifest_date = next(iter(manifest_dates)) if manifest_dates else ""
    if manifest_date and manifest_date != csv_date:
        result.update({
            "broker_upstream_status": "LINEAGE_DATE_CONFLICT",
            "broker_readiness_reason": "CSV_MANIFEST_DATE_MISMATCH",
        })
        return result

    expected_hash = str(
        sidecar.get("summary_hash")
        or sidecar.get("summary_source_hash")
        or sidecar.get("source_hash")
        or ""
    ).strip().lower()
    try:
        actual_hash = file_sha256(broker_path)
    except OSError as exc:
        result.update({
            "broker_upstream_status": "HASH_READ_FAILED",
            "broker_readiness_reason": f"HASH_READ_FAILED:{type(exc).__name__}",
        })
        return result
    if expected_hash and expected_hash != actual_hash:
        result.update({
            "broker_upstream_status": "HASH_MISMATCH",
            "broker_readiness_reason": "HASH_MISMATCH",
        })
        return result

    data_date = manifest_date or csv_date
    result["broker_data_date"] = data_date
    stale_tokens = ("STALE", "NOT CURRENT", "DATE MISMATCH")
    stale_status = any(token in status for status in statuses for token in stale_tokens)
    if stale_status or data_date != ctx.trade_date.isoformat():
        result.update({
            "broker_status": "WAITING",
            "broker_readiness_reason": "BROKER_DATA_NOT_CURRENT",
        })
        return result

    result.update({
        "broker_status": "READY",
        "broker_data_verified": True,
        "broker_data_current": True,
        "broker_readiness_reason": "CURRENT_USABLE_BROKER_ARTIFACT",
    })
    return result


def _trend_bucket(value: Any) -> str:
    text = str(value or "").strip().upper().replace("_", " ")
    if not text or text in {"NAN", "NONE", "NULL"}:
        return ""
    bullish_tokens = ("STRONG BULL", "BULLISH", "UPTREND", "UP TREND")
    bearish_tokens = ("STRONG BEAR", "BEARISH", "DOWNTREND", "DOWN TREND")
    if any(token in text for token in bullish_tokens):
        return "BULLISH"
    if any(token in text for token in bearish_tokens):
        return "BEARISH"
    if any(token in text for token in ("SIDEWAYS", "NEUTRAL", "MIXED", "RANGE")):
        return "NEUTRAL"
    return ""


def _technical_breadth(frame: pd.DataFrame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "technical_bullish_count": 0,
        "technical_neutral_count": 0,
        "technical_bearish_count": 0,
        "breadth_classified_count": 0,
    }
    if frame.empty:
        return result

    trend_column = _column(
        frame,
        "Technical Regime",
        "Technical_Regime",
        "Trend",
        "Trend State",
        "Trend_State",
        "Technical Trend",
        "Technical_Trend",
    )
    close_column = _column(frame, "Close", "Last Price", "Last_Price", "Current Price", "Current_Price")
    ma20_column = _column(frame, "SMA20", "SMA 20", "SMA_20", "MA20", "MA 20", "MA_20")
    ma50_column = _column(frame, "SMA50", "SMA 50", "SMA_50", "MA50", "MA 50", "MA_50")

    bullish = neutral = bearish = 0
    for _, row in frame.iterrows():
        bucket = _trend_bucket(row.get(trend_column)) if trend_column else ""
        if not bucket and close_column and ma20_column:
            try:
                close = float(row.get(close_column))
                ma20 = float(row.get(ma20_column))
                ma50_raw = row.get(ma50_column) if ma50_column else None
                ma50 = float(ma50_raw) if ma50_raw is not None and not pd.isna(ma50_raw) else None
                if pd.isna(close) or pd.isna(ma20):
                    bucket = "NEUTRAL"
                elif close > ma20 and (ma50 is None or ma20 >= ma50):
                    bucket = "BULLISH"
                elif close < ma20 and (ma50 is None or ma20 <= ma50):
                    bucket = "BEARISH"
                else:
                    bucket = "NEUTRAL"
            except Exception:
                bucket = "NEUTRAL"
        if not bucket:
            bucket = "NEUTRAL"

        if bucket == "BULLISH":
            bullish += 1
        elif bucket == "BEARISH":
            bearish += 1
        else:
            neutral += 1

    result.update({
        "technical_bullish_count": bullish,
        "technical_neutral_count": neutral,
        "technical_bearish_count": bearish,
        "breadth_classified_count": bullish + neutral + bearish,
    })
    return result


def _setup_distribution(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {}
    setup_column = _column(
        frame,
        "Setup Type",
        "Setup_Type",
        "Setup",
        "Technical Setup",
        "Technical_Setup",
    )
    if setup_column is None:
        return {}
    values = (
        frame[setup_column]
        .dropna()
        .astype(str)
        .str.strip()
        .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
        .dropna()
    )
    if values.empty:
        return {}
    counts = values.str.upper().str.replace("_", " ", regex=False).value_counts()
    return {str(label): int(count) for label, count in counts.head(5).items() if int(count) > 0}


def _candidate_health(frame: pd.DataFrame) -> dict[str, Any]:
    """Build Post Market health facts from the existing candidate ranking.

    This function deliberately does not call Final Watchlist or create a new
    score.  It only ranks already-produced valid candidate/setup rows for a
    compact screening-health preview and excludes AVOID rows.
    """
    result: dict[str, Any] = {
        "candidate_funnel": {
            "technical_rows": int(len(frame)),
            "candidate_rows": 0,
            "pass_rows": 0,
            "avoid_rows": 0,
            "ready_rows": 0,
            "developing_rows": 0,
        },
        "dominant_filter_reason": "NOT_AVAILABLE",
        "screening_result": "NOT_AVAILABLE",
        "top_screening_watchlist": [],
    }
    if frame.empty:
        return result

    def col(*aliases: str) -> str | None:
        return _column(frame, *aliases)

    symbol_col = col("Symbol", "EMITEN", "Ticker")
    status_col = col("Candidate_Status", "Candidate Status", "Screening_Status", "Screening Status")
    decision_col = col("Decision", "Decision_V3", "Final Decision")
    reason_col = col(
        "Filter_Reason", "Filter Reason", "Rejection_Reason", "Rejection Reason",
        "Candidate_Reason", "Candidate Reason", "Failure_Reason", "Failure Reason",
    )
    quality_col = col("Data_Quality_Status", "Data Quality Status", "Technical_Quality_Check", "Quality")
    setup_col = col("Setup_Type", "Setup Type", "Setup_Label", "Setup Label", "Technical Setup")
    score_col = col("Technical_Score_Final", "Technical_Score", "Technical Score", "Score")
    quality_score_col = col("Technical_Quality_Score", "Technical Quality Score", "Quality Score")
    readiness_col = col("Entry_Readiness_PreScore", "Entry_Readiness_Score", "Entry Readiness")
    readiness_class_col = col("Entry_Readiness_Class", "Entry Readiness Class")
    price_col = col("Close", "Current Price", "Current_Price", "Last Price", "Reference_Close")
    entry_low_col = col("Entry_Low", "Entry Zone Low", "Entry_Zone_Low")
    entry_high_col = col("Entry_High", "Entry Zone High", "Entry_Zone_High")

    status = frame[status_col].astype(str).str.upper().str.strip() if status_col else pd.Series("", index=frame.index)
    decision = frame[decision_col].astype(str).str.upper().str.strip() if decision_col else pd.Series("", index=frame.index)
    reason_values = frame[reason_col].astype(str).str.upper().str.strip() if reason_col else pd.Series("", index=frame.index)
    avoid_mask = (
        status.str.contains("AVOID", na=False)
        | decision.str.contains("AVOID", na=False)
        | reason_values.str.contains("AVOID", na=False)
    )
    if status_col:
        pass_mask = status.isin({"PASS", "BUY", "BUY CANDIDATE", "WATCH", "WATCH HIGH", "READY", "READY_ZONE"})
        # Some historical candidate files use an empty status but do contain a
        # valid decision. Keep those rows eligible unless they are AVOID.
        pass_mask = pass_mask | (status.eq("") & decision.ne(""))
    elif decision_col:
        pass_mask = decision.ne("")
    else:
        pass_mask = pd.Series(True, index=frame.index)
    pass_mask = pass_mask & ~avoid_mask
    valid_symbol = (
        frame[symbol_col].map(
            lambda value: value is not None and not pd.isna(value) and str(value).strip() != ""
        )
        if symbol_col
        else pd.Series(False, index=frame.index)
    )
    pass_mask = pass_mask & valid_symbol
    if quality_col:
        quality_status = frame[quality_col].astype(str).str.upper().str.strip()
        invalid_quality = quality_status.str.contains("INVALID|STALE|FAILED|ERROR|NOT VALID", regex=True, na=False)
        pass_mask = pass_mask & ~invalid_quality

    result["candidate_funnel"].update({
        "candidate_rows": int(len(frame)),
        "pass_rows": int(pass_mask.sum()),
        "avoid_rows": int(avoid_mask.sum()),
    })
    if readiness_class_col:
        readiness_class = frame[readiness_class_col].astype(str).str.upper()
        result["candidate_funnel"]["ready_rows"] = int((pass_mask & readiness_class.isin({"READY_ZONE", "READY"})).sum())
        result["candidate_funnel"]["developing_rows"] = int((pass_mask & readiness_class.eq("DEVELOPING")).sum())
    elif readiness_col:
        readiness = pd.to_numeric(frame[readiness_col], errors="coerce")
        result["candidate_funnel"]["ready_rows"] = int((pass_mask & readiness.ge(75)).sum())
        result["candidate_funnel"]["developing_rows"] = int((pass_mask & readiness.between(60, 74.999)).sum())

    rejected = frame.loc[~pass_mask]
    if reason_col and not rejected.empty:
        reasons = (
            rejected[reason_col]
            .dropna()
            .astype(str)
            .str.strip()
            .replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
            .dropna()
        )
        if not reasons.empty:
            result["dominant_filter_reason"] = str(reasons.str.replace("_", " ", regex=False).value_counts().index[0]).upper()

    result["screening_result"] = (
        "READY" if int(pass_mask.sum()) > 0 else "NO_VALID_CANDIDATE"
    )
    work = frame.loc[pass_mask].copy()
    if work.empty:
        return result

    sort_columns: list[str] = []
    for source_column in (quality_score_col, score_col, readiness_col):
        if source_column and source_column not in sort_columns:
            sort_columns.append(source_column)
            work[f"__sort_{source_column}"] = pd.to_numeric(work[source_column], errors="coerce")
    if sort_columns:
        work = work.sort_values(
            [f"__sort_{column}" for column in sort_columns],
            ascending=[False] * len(sort_columns),
            na_position="last",
        )

    def text_value(row: pd.Series, source_column: str | None) -> str:
        if not source_column:
            return ""
        value = row.get(source_column)
        if value is None or pd.isna(value) or str(value).strip().lower() in {"", "nan", "none"}:
            return ""
        return str(value).strip()

    def number_value(row: pd.Series, source_column: str | None) -> float | None:
        if not source_column:
            return None
        try:
            value = float(row.get(source_column))
            return value if pd.notna(value) else None
        except (TypeError, ValueError):
            return None

    top: list[dict[str, Any]] = []
    for _, row in work.head(5).iterrows():
        symbol = text_value(row, symbol_col).upper()
        if not symbol:
            continue
        entry = {
            "symbol": symbol,
            "setup": text_value(row, setup_col).replace("_", " ").upper(),
            "decision": text_value(row, decision_col).replace("_", " ").upper(),
            "candidate_status": text_value(row, status_col).replace("_", " ").upper(),
            "score": number_value(row, score_col),
            "quality_score": number_value(row, quality_score_col),
            "entry_readiness": number_value(row, readiness_col),
            "entry_readiness_class": text_value(row, readiness_class_col).replace("_", " ").upper(),
            "price": number_value(row, price_col),
            "entry_low": number_value(row, entry_low_col),
            "entry_high": number_value(row, entry_high_col),
            "data_quality": text_value(row, quality_col).replace("_", " ").upper(),
        }
        top.append(entry)
    result["top_screening_watchlist"] = top
    return result


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _durable_write_json(path, payload)


def _rotation_context(ctx: RunnerContext) -> dict[str, Any]:
    regime_path = ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
    existing = read_json(regime_path)
    rotation: dict[str, Any] = {}
    raw_path = str(existing.get("sector_rotation_path") or "").strip()
    candidates: list[Path] = []
    if raw_path:
        candidate = Path(raw_path)
        candidates.append(candidate if candidate.is_absolute() else resolve(candidate))
    configured = str(ctx.path("sector_rotation_output", "data/output/market/SECTOR_ROTATION.json"))
    if configured:
        candidates.append(Path(configured))
    for candidate in candidates:
        payload = read_json(candidate)
        if isinstance(payload.get("sector_rotation"), dict):
            rotation = dict(payload["sector_rotation"])
        elif payload:
            rotation = dict(payload)
        if rotation:
            break
    if not rotation and isinstance(existing.get("sector_rotation"), dict):
        rotation = dict(existing["sector_rotation"])

    rotation_date = str(rotation.get("trade_date") or existing.get("sector_rotation_trade_date") or "")[:10]
    rotation_status = str(rotation.get("status") or existing.get("sector_rotation_status") or "UNAVAILABLE").upper()
    current = rotation_date == ctx.trade_date.isoformat() and rotation_status in {"VALID", "CURRENT", "READY"}
    return {
        "leading": rotation.get("leading", []) if current else [],
        "rotating_in": rotation.get("improving", rotation.get("rotating_in", [])) if current else [],
        "weakening": rotation.get("weakening", rotation.get("rotating_out", [])) if current else [],
        "rotating_out": rotation.get("rotating_out", rotation.get("weakening", [])) if current else [],
        "lagging": rotation.get("lagging", []) if current else [],
        "sector_rotation_trade_date": rotation_date,
        "sector_rotation_status": rotation_status if current else "NOT_CURRENT",
        "sector_rotation_current": current,
    }


def prepare_post_market_pulse(
    ctx: RunnerContext,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a closing-session pulse for Post Market only.

    This function is presentation/runtime context. It never changes technical,
    broker, decision, entry, stop, target, or portfolio scoring.
    """
    ihsg_path = ctx.path("ihsg_csv", "data/input/IHSG.csv")
    manifest = manifest or {}
    snapshot_path = str(manifest.get("Snapshot_Manifest") or manifest.get("snapshot_manifest") or "")
    snapshot = read_json(Path(snapshot_path)) if snapshot_path else {}
    output_paths = snapshot.get("output_paths", {}) if isinstance(snapshot.get("output_paths"), dict) else {}
    technical_path = Path(str(
        output_paths.get("technical_features")
        or ctx.path("technical_output_dir", "data/output/technical") / "latest_technical_features.csv"
    ))
    candidate_path = Path(str(
        output_paths.get("technical_candidates")
        or ctx.path("candidate_output_dir", "data/output/candidates") / "technical_ranking_full.csv"
    ))
    warnings: list[str] = []

    should_refresh = (
        not bool(ctx.preview_existing)
        and not bool(ctx.dry_run)
        and not bool(getattr(ctx, "delivery_only", False))
    )
    if should_refresh:
        updater = ctx.path("ihsg_updater", "modules/market_data/update_ihsg.py")
        command = [
            sys.executable,
            "-u",
            str(updater),
            "--output",
            str(ihsg_path),
            "--period",
            str(ctx.config.get("download", {}).get("period", "2y")),
        ]
        try:
            completed = subprocess.run(command, cwd=Path.cwd(), check=False)
            if completed.returncode != 0:
                warnings.append(f"IHSG_REFRESH_EXIT_{completed.returncode}")
        except Exception as exc:
            warnings.append(f"IHSG_REFRESH_FAILED:{type(exc).__name__}:{exc}")

    try:
        regime = calculate_market_outlook_regime(
            ihsg_path=ihsg_path,
            technical_path=technical_path,
            as_of_date=ctx.trade_date,
        )
    except Exception as exc:
        regime = {
            "market_regime": "UNKNOWN",
            "data_date": None,
            "warnings": [f"IHSG_CONTEXT_FAILED:{type(exc).__name__}:{exc}"],
        }
        warnings.append(f"IHSG_CONTEXT_FAILED:{type(exc).__name__}:{exc}")
    technical = _read_latest_technical(technical_path, ctx.trade_date)
    candidates = _read_csv_optional(candidate_path)
    technical_data_date = str(technical.attrs.get("resolved_data_date") or "")[:10]
    technical_current = technical_data_date == ctx.trade_date.isoformat()
    # Breadth is a current-date fact only when the technical universe itself
    # resolves to today's session.  Do not present yesterday's breadth as a
    # current market pulse merely because it is the latest available file.
    breadth = _technical_breadth(technical) if technical_current else {
        "technical_bullish_count": 0,
        "technical_neutral_count": 0,
        "technical_bearish_count": 0,
        "breadth_classified_count": 0,
    }
    # Setup_Type is produced by Candidate Selector, not Technical Feature Engine.
    # It is presentation-eligible only when the ranking artifact itself carries
    # the current session date.  Never infer a setup distribution from stale or
    # raw technical rows.
    candidate_data_date = _artifact_trade_date(
        candidates,
        "Technical_Data_Date",
        "Candidate_Data_Date",
        "Candidate_Trade_Date",
        "Trade_Date",
        "Date",
    )
    candidate_current = candidate_data_date == ctx.trade_date.isoformat()
    setups = _setup_distribution(candidates) if technical_current and candidate_current else {}
    candidate_health = _candidate_health(candidates) if technical_current and candidate_current else {
        "candidate_funnel": {
            "technical_rows": 0,
            "candidate_rows": 0,
            "pass_rows": 0,
            "avoid_rows": 0,
            "ready_rows": 0,
            "developing_rows": 0,
        },
        "dominant_filter_reason": "NOT_CURRENT",
        "screening_result": "NOT_CURRENT",
        "top_screening_watchlist": [],
    }

    data_date = str(regime.get("data_date") or "")[:10]
    current_session = data_date == ctx.trade_date.isoformat()
    for regime_warning in regime.get("warnings", []) or []:
        rendered_warning = str(regime_warning)
        if rendered_warning and rendered_warning not in warnings:
            warnings.append(rendered_warning)
    if not current_session:
        warnings.append(f"IHSG_SESSION_NOT_CURRENT:{data_date or 'MISSING'}")

    calculated_at = now_wib().isoformat(timespec="seconds")
    pulse = {
        "trade_date": ctx.trade_date.isoformat(),
        "calculated_at": calculated_at,
        "market_regime": regime.get("market_regime") if current_session else "DATA_NOT_CURRENT",
        "execution_mode": regime.get("execution_mode") if current_session else "SELECTIVE",
        "ihsg_change": regime.get("ihsg_change_pct") if current_session else None,
        "ihsg_data_date": data_date,
        "ihsg_status": "CURRENT_SESSION" if current_session else "NOT_CURRENT_SESSION",
        "ihsg_trend": regime.get("trend") if current_session else "",
        "breadth": regime.get("breadth") if current_session else "",
        "breadth_above_sma20_pct": regime.get("breadth_above_sma20_pct") if technical_current else None,
        "breadth_above_sma50_pct": regime.get("breadth_above_sma50_pct") if technical_current else None,
        "breadth_macd_bullish_pct": regime.get("breadth_macd_bullish_pct") if technical_current else None,
        "breadth_status": "CURRENT" if technical_current else "NOT_CURRENT",
        "ihsg_close": regime.get("ihsg_close") if current_session else None,
        **breadth,
        "setup_distribution": setups,
        **candidate_health,
        "technical_data_date": technical_data_date,
        "candidate_data_date": candidate_data_date,
        "candidate_ranking_current": candidate_current,
        **_rotation_context(ctx),
        "warnings": warnings,
        "source": "POST_MARKET_CLOSING_PULSE",
    }
    output = ctx.path("post_market_output_dir", "data/output/post_market") / ctx.trade_date.isoformat() / "market_pulse.json"
    _write_json(output, pulse)
    pulse["pulse_path"] = str(output)
    if warnings:
        append_job_log(ctx, "POST_MARKET_PULSE_WARNING", str(warnings))
    else:
        append_job_log(ctx, "POST_MARKET_PULSE_READY", str({
            "ihsg_data_date": data_date,
            "technical_rows": len(technical),
            "candidate_rows": len(candidates),
            "breadth": breadth,
        }))
    return pulse


def _render_data(ctx: RunnerContext, manifest: dict[str, Any], payload: Any, pulse: dict[str, Any]) -> dict[str, Any]:
    validated = dict(getattr(payload, "validation_details", {}) or {})
    requested = int(validated.get("symbols_requested", manifest.get("symbols_requested", 0)) or 0)
    loaded = int(validated.get("symbols_loaded", manifest.get("symbols_loaded", 0)) or 0)
    valid = int(validated.get("symbols_valid", manifest.get("symbols_valid", 0)) or 0)
    skipped = int(validated.get("symbols_skipped", manifest.get("symbols_skipped", 0)) or 0)
    not_loaded = max(0, requested - loaded)
    invalid = max(0, loaded - valid)
    coverage = float(validated.get("coverage", manifest.get("source_coverage_ratio", 0.0)) or 0.0)
    if 0 <= coverage <= 1:
        coverage *= 100.0

    finished_at = (
        manifest.get("Finished_At")
        or manifest.get("finished_at")
        or manifest.get("completed_at")
        or pulse.get("calculated_at")
        or now_wib().isoformat(timespec="seconds")
    )
    broker_context = _broker_presentation_context(ctx, manifest)
    technical_current = str(pulse.get("technical_data_date") or "") == ctx.trade_date.isoformat()
    candidate_current = bool(pulse.get("candidate_ranking_current")) and str(
        pulse.get("candidate_data_date") or ""
    ) == ctx.trade_date.isoformat()
    if candidate_current:
        screening_result = str(pulse.get("screening_result") or "NOT_AVAILABLE")
    else:
        screening_result = "NOT_CURRENT"

    return {
        **pulse,
        "trade_date": ctx.trade_date.isoformat(),
        "post_market_report_version": "CURRENT_V2",
        "finished_at": finished_at,
        "run_id": manifest.get("Run_ID") or ctx.run_id,
        "process_status": "SUCCESS" if coverage >= 90 else "PARTIAL" if valid > 0 else "FAILED",
        "symbols_requested": requested,
        "symbols_loaded": loaded,
        "symbols_valid": valid,
        "symbols_not_loaded": not_loaded,
        "symbols_invalid": invalid,
        "symbols_skipped": skipped,
        "coverage": coverage,
        "data_impact": "TIDAK MATERIAL" if coverage >= 90 else "MATERIAL",
        "technical_status": "READY" if valid > 0 and technical_current else "NOT CURRENT",
        "candidate_status": screening_result if candidate_current else "NOT CURRENT",
        **broker_context,
        "historical_status": "VALID" if valid > 0 else "FAILED",
        "pipeline_status": "READY_FOR_FINAL_WATCHLIST" if valid > 0 else "BLOCKED_DATA",
        "source_status": {
            "IHSG": pulse.get("ihsg_status", "NOT_AVAILABLE"),
            "TECHNICAL": "CURRENT" if str(pulse.get("technical_data_date") or "") == ctx.trade_date.isoformat() else "NOT_CURRENT",
            "BREADTH": pulse.get("breadth_status", "NOT_AVAILABLE"),
            "CANDIDATE_RANKING": "AVAILABLE" if int((pulse.get("candidate_funnel") or {}).get("candidate_rows", 0) or 0) > 0 else "NOT_AVAILABLE",
            "BROKER": broker_context["broker_status"],
        },
        "data_note": " • ".join(
            text for text in (
                f"{not_loaded} saham tidak dimuat" if not_loaded else "",
                f"{invalid} saham gagal validasi" if invalid else "",
                f"{skipped} saham dilewati" if skipped else "",
            ) if text
        ),
    }


def post_market_live_payloads(ctx: RunnerContext, manifest: dict[str, Any]) -> list[Any]:
    """Validate with the existing bridge, then render with fresh closing facts."""
    payloads = _validated_post_market_payloads(ctx, manifest)
    pulse = prepare_post_market_pulse(ctx, manifest)
    for payload in payloads:
        if str(getattr(payload, "report_type", "")).lower() != "post_market":
            continue
        data = _render_data(ctx, manifest, payload, pulse)
        payload.text = format_post_market(data)
        inputs = list(getattr(payload, "input_paths", ()) or ())
        if pulse.get("pulse_path"):
            inputs.append(str(pulse["pulse_path"]))
        setattr(payload, "input_paths", tuple(dict.fromkeys(inputs)))
        details = dict(getattr(payload, "validation_details", {}) or {})
        details["post_market_pulse"] = {
            "ihsg_status": pulse.get("ihsg_status"),
            "ihsg_data_date": pulse.get("ihsg_data_date"),
            "technical_data_date": pulse.get("technical_data_date"),
            "candidate_funnel": pulse.get("candidate_funnel", {}),
            "dominant_filter_reason": pulse.get("dominant_filter_reason"),
            "screening_result": pulse.get("screening_result"),
            "technical_bullish_count": pulse.get("technical_bullish_count"),
            "technical_neutral_count": pulse.get("technical_neutral_count"),
            "technical_bearish_count": pulse.get("technical_bearish_count"),
            "warnings": pulse.get("warnings", []),
        }
        setattr(payload, "validation_details", details)
    return payloads
