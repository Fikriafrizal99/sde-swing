from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from modules.market_data.market_outlook_regime import calculate_market_outlook_regime
from modules.telegram.post_market_ui import format_post_market

from .enhanced_runtime_bridge import post_market_payloads as _validated_post_market_payloads
from .runtime import RunnerContext, append_job_log, read_json, resolve


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
        else:
            return pd.DataFrame()
    return frame.reset_index(drop=True)


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(path)


def _rotation_context(ctx: RunnerContext) -> dict[str, Any]:
    regime_path = ctx.previews_root.parent / "market_regime" / ctx.trade_date.isoformat() / "market_outlook_regime.json"
    existing = read_json(regime_path)
    rotation: dict[str, Any] = {}
    if isinstance(existing.get("sector_rotation"), dict):
        rotation = dict(existing["sector_rotation"])
    raw_path = str(existing.get("sector_rotation_path") or "").strip()
    if not rotation and raw_path:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = resolve(candidate)
        payload = read_json(candidate)
        if isinstance(payload.get("sector_rotation"), dict):
            rotation = dict(payload["sector_rotation"])
        elif payload:
            rotation = payload
    return {
        "leading": rotation.get("leading", []),
        "rotating_in": rotation.get("improving", rotation.get("rotating_in", [])),
    }


def prepare_post_market_pulse(ctx: RunnerContext) -> dict[str, Any]:
    """Build a closing-session pulse for Post Market only.

    This function is presentation/runtime context. It never changes technical,
    broker, decision, entry, stop, target, or portfolio scoring.
    """
    ihsg_path = ctx.path("ihsg_csv", "data/input/IHSG.csv")
    technical_path = ctx.path("technical_output_dir", "data/output/technical") / "latest_technical_features.csv"
    warnings: list[str] = []

    should_refresh = not bool(ctx.preview_existing) and not bool(ctx.dry_run)
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

    regime = calculate_market_outlook_regime(
        ihsg_path=ihsg_path,
        technical_path=technical_path,
        as_of_date=ctx.trade_date,
    )
    technical = _read_latest_technical(technical_path, ctx.trade_date)
    breadth = _technical_breadth(technical)
    setups = _setup_distribution(technical)

    data_date = str(regime.get("data_date") or "")[:10]
    current_session = data_date == ctx.trade_date.isoformat()
    if not current_session:
        warnings.append(f"IHSG_SESSION_NOT_CURRENT:{data_date or 'MISSING'}")

    pulse = {
        "trade_date": ctx.trade_date.isoformat(),
        "market_regime": regime.get("market_regime") if current_session else "DATA_NOT_CURRENT",
        "execution_mode": regime.get("execution_mode") if current_session else "SELECTIVE",
        "ihsg_change": regime.get("ihsg_change_pct") if current_session else None,
        "ihsg_data_date": data_date,
        "ihsg_status": "CURRENT_SESSION" if current_session else "NOT_CURRENT_SESSION",
        "ihsg_trend": regime.get("trend") if current_session else "",
        "breadth": regime.get("breadth") if current_session else "",
        "breadth_above_sma20_pct": regime.get("breadth_above_sma20_pct"),
        **breadth,
        "setup_distribution": setups,
        **_rotation_context(ctx),
        "warnings": warnings,
        "source": "POST_MARKET_CLOSING_PULSE",
    }
    output = resolve("data/output/post_market") / ctx.trade_date.isoformat() / "market_pulse.json"
    _write_json(output, pulse)
    pulse["pulse_path"] = str(output)
    if warnings:
        append_job_log(ctx, "POST_MARKET_PULSE_WARNING", str(warnings))
    else:
        append_job_log(ctx, "POST_MARKET_PULSE_READY", str({
            "ihsg_data_date": data_date,
            "technical_rows": len(technical),
            "breadth": breadth,
        }))
    return pulse


def _render_data(ctx: RunnerContext, manifest: dict[str, Any], payload: Any, pulse: dict[str, Any]) -> dict[str, Any]:
    validated = dict(getattr(payload, "validation_details", {}) or {})
    requested = int(validated.get("symbols_requested", manifest.get("symbols_requested", 0)) or 0)
    loaded = int(validated.get("symbols_loaded", manifest.get("symbols_loaded", 0)) or 0)
    valid = int(validated.get("symbols_valid", manifest.get("symbols_valid", 0)) or 0)
    failed = int(validated.get("symbols_failed", manifest.get("symbols_failed", 0)) or 0)
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
    )
    candidate_count = int(manifest.get("Candidate_Count", manifest.get("candidate_count", 0)) or 0)
    broker_ready = bool(manifest.get("Broker_Navigator_Path") or manifest.get("broker_navigator_path"))

    return {
        **pulse,
        "trade_date": ctx.trade_date.isoformat(),
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
        "technical_status": "READY" if valid > 0 else "NOT READY",
        "candidate_status": "READY" if candidate_count > 0 else "EMPTY",
        "broker_status": "READY" if broker_ready else "WAITING",
        "historical_status": "VALID" if valid > 0 else "FAILED",
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
    pulse = prepare_post_market_pulse(ctx)
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
            "technical_bullish_count": pulse.get("technical_bullish_count"),
            "technical_neutral_count": pulse.get("technical_neutral_count"),
            "technical_bearish_count": pulse.get("technical_bearish_count"),
            "warnings": pulse.get("warnings", []),
        }
        setattr(payload, "validation_details", details)
    return payloads
