from __future__ import annotations

"""Legacy broker multi-day output writer (archive/shadow only).

Writes the detail/summary/rotation/divergence/window-comparison CSVs plus a
manifest into ``data/output/broker_multiday/``. Each record carries provenance
and quality status for audit compatibility. It is not a production Final
Watchlist source and has no runtime delivery route.
"""

from pathlib import Path
from typing import Any

from swing_utils import iso_now, write_dict_rows_csv, write_json, PIPELINE_VERSION
from modules.data_sources.broker_multiday_engine import MultiDayContext
from modules.data_sources.broker_windows import WINDOWS

DEFAULT_OUTPUT_DIR = Path("data/output/broker_multiday")


def write_multiday_outputs(
    contexts: dict[str, MultiDayContext],
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    run_id: str = "",
    shadow_summary: dict[str, Any] | None = None,
    data_quality_status: str = "VALID",
    market_dates: list[str] | None = None,
    source_files: list[str] | None = None,
    minimum_sessions: int = 20,
) -> dict[str, Path]:
    out_dir = Path(output_dir)
    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []
    rotation_rows: list[dict[str, Any]] = []
    divergence_rows: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []

    first_metadata: dict[str, Any] = {}

    for symbol, ctx in sorted(contexts.items()):
        ctx_dict = ctx.to_context_dict()
        period_metadata = dict(ctx.period_metadata)
        if not first_metadata and period_metadata:
            first_metadata = period_metadata
        provenance = {
            "Symbol": symbol,
            "Market_Date": ctx.market_date,
            "Data_Quality_Status": data_quality_status,
            "Source": period_metadata.get("broker_period_source") or "STOCKBIT",
            "Primary_Window": period_metadata.get("broker_period_type") or ctx.primary_window,
            "Broker_Period_Type": period_metadata.get("broker_period_type", ""),
            "Broker_Period_Start": period_metadata.get("broker_period_start", ""),
            "Broker_Period_End": period_metadata.get("broker_period_end", ""),
            "Broker_Trading_Days": period_metadata.get("broker_trading_days", ""),
            "Broker_Session_Dates": period_metadata.get("broker_session_dates", []),
            "Broker_Snapshot_ID": period_metadata.get("broker_snapshot_id", ""),
            "Broker_Period_Source": period_metadata.get("broker_period_source", ""),
            "Broker_Coverage": period_metadata.get("broker_coverage", ""),
            "Broker_Period_Coverage": period_metadata.get("broker_period_coverage", ""),
            "Broker_Missing_Sessions": period_metadata.get("broker_missing_sessions", []),
            "Broker_Period_Complete": period_metadata.get("broker_period_complete", ""),
            "Broker_Session_Coverage": period_metadata.get("broker_session_coverage", ""),
            "Broker_Coverage_Text": period_metadata.get("broker_coverage_text", ""),
            "Broker_Coverage_Status": period_metadata.get("broker_coverage_status", ""),
            "Broker_Freshness_Status": period_metadata.get("broker_freshness_status", ""),
        }
        # Detail: one row per symbol with the full context bundle.
        detail_rows.append({**provenance, **ctx_dict})

        # Summary: compact per-symbol view.
        summary_rows.append({
            **provenance,
            "Context": ctx.broker_multiday_context,
            "Score": round(ctx.broker_multiday_score, 2),
            "Confidence": round(ctx.broker_multiday_confidence, 1),
            "Penalty": round(ctx.broker_multiday_penalty, 1),
            "Blocker": ctx.broker_multiday_blocker,
            "Alignment": ctx.alignment.alignment,
            "Broker_Period_Alignment": ctx.broker_period_alignment,
            **ctx.today_pulse,
        })

        # Rotation.
        rotation_rows.append({**provenance, **ctx.persistence})

        # Divergence.
        divergence_rows.append({**provenance, **ctx.divergence})

        # Window comparison: one row per (symbol, window).
        for window, wf in ctx.windows.items():
            cls = ctx.classifications.get(window)
            window_rows.append({
                **provenance,
                "Window": window,
                "Classification": cls.classification if cls else "INSUFFICIENT_DATA",
                **wf.to_dict(),
            })

    paths: dict[str, Path] = {}
    paths["detail"] = out_dir / "BROKER_MULTIDAY_DETAIL.csv"
    paths["summary"] = out_dir / "BROKER_MULTIDAY_SUMMARY.csv"
    paths["rotation"] = out_dir / "BROKER_ROTATION.csv"
    paths["divergence"] = out_dir / "BROKER_DIVERGENCE.csv"
    paths["window_comparison"] = out_dir / "BROKER_WINDOW_COMPARISON.csv"

    write_dict_rows_csv(detail_rows, paths["detail"])
    write_dict_rows_csv(summary_rows, paths["summary"])
    write_dict_rows_csv(rotation_rows, paths["rotation"])
    write_dict_rows_csv(divergence_rows, paths["divergence"])
    write_dict_rows_csv(window_rows, paths["window_comparison"])

    manifest = {
        "generated_at": iso_now(),
        "pipeline_version": PIPELINE_VERSION,
        "run_id": run_id,
        "data_quality_status": data_quality_status,
        "symbol_count": len(contexts),
        "market_dates": sorted({str(value) for value in (market_dates or []) if value}),
        "session_count": len(set(market_dates or [])),
        "minimum_sessions": int(minimum_sessions),
        "coverage_ratio": round(min(len(set(market_dates or [])) / max(int(minimum_sessions), 1), 1.0), 4),
        "date_range": {
            "start": min(market_dates) if market_dates else "",
            "end": max(market_dates) if market_dates else "",
        },
        "source_files": list(source_files or []),
        "windows": list(WINDOWS.keys()),
        "files": {name: str(path) for name, path in paths.items()},
        "shadow_summary": shadow_summary or {},
        "contract": "MULTI_DAY_ENGINE_PRODUCES_CONTEXT_ONLY_NO_BUY_WATCH_AVOID",
        "primary_context": first_metadata,
        "broker_period_type": first_metadata.get("broker_period_type", ""),
        "broker_period_start": first_metadata.get("broker_period_start", ""),
        "broker_period_end": first_metadata.get("broker_period_end", ""),
        "broker_trading_days": first_metadata.get("broker_trading_days", 0),
        "broker_session_dates": first_metadata.get("broker_session_dates", []),
        "broker_snapshot_id": first_metadata.get("broker_snapshot_id", ""),
        "broker_period_source": first_metadata.get("broker_period_source", ""),
        "broker_coverage": first_metadata.get("broker_coverage", 0.0),
        "broker_period_coverage": first_metadata.get("broker_period_coverage", ""),
        "broker_missing_sessions": first_metadata.get("broker_missing_sessions", []),
        "broker_period_complete": first_metadata.get("broker_period_complete", ""),
        "broker_freshness_status": first_metadata.get("broker_freshness_status", ""),
        "aggregate_snapshot": str(first_metadata.get("broker_period_type", "")).upper() != "1D",
        "daily_history_eligible": str(first_metadata.get("broker_period_type", "")).upper() == "1D",
    }
    manifest_path = out_dir / "BROKER_MULTIDAY_MANIFEST.json"
    write_json(manifest_path, manifest)
    paths["manifest"] = manifest_path
    return paths


def build_telegram_summary(ctx: MultiDayContext, *, use_emoji: bool = False) -> str:
    """Compact per-symbol broker summary for Telegram (never a long table)."""
    def _label(window: str) -> str:
        cls = ctx.classifications.get(window)
        return _humanize(cls.classification) if cls else "-"

    top_buyers = ctx.windows.get(ctx.primary_window)
    buyers = (
        ", ".join(str(value) for value in top_buyers.persistent_top_buyers[:3])
        if top_buyers
        else "-"
    )
    cost = None
    if top_buyers and top_buyers.weighted_broker_buy_cost:
        cost = top_buyers.weighted_broker_buy_cost

    lines = [
        f"Broker 1D : {_label('1D')}",
        f"Broker 5D : {_label('5D')}",
        f"Broker 10D: {_label('10D')}",
        f"Alignment : {_humanize(ctx.alignment.alignment)}",
        f"Top buyer : {buyers or '-'}",
    ]
    if cost is not None:
        lines.append(f"Cost {ctx.primary_window}   : Rp{cost:,.0f}".replace(",", "."))
    return "\n".join(lines)


def _humanize(label: str) -> str:
    return label.replace("_", " ").title()
