#!/usr/bin/env python3
"""Offline validation for the current Telegram presentation contract.

This is deliberately a presentation-only release check.  It consumes the
artifacts produced by the isolated release fixture and renders through
``professional_ui`` directly, so release validation does not depend on the
deprecated ``telegram_bot.py``/``swing_report_builder.py`` compatibility chain.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.telegram.professional_ui import (
    UiConfig,
    format_closing_bell,
    format_data_warning,
    format_exit_alert,
    format_market_outlook,
    format_pipeline_status,
    format_position_evaluation,
    format_signal_detail,
    format_watchlist,
    rank_watchlist,
)


def _read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except (pd.errors.EmptyDataError, OSError, ValueError):
        return pd.DataFrame()


def _path(value: str) -> Path | None:
    text = str(value or "").strip()
    return Path(text) if text else None


def _write_preview(output: Path, name: str, text: str) -> dict[str, Any]:
    if not str(text or "").strip():
        raise RuntimeError(f"CURRENT_TELEGRAM_EMPTY:{name}")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{name}.txt"
    target.write_text(str(text).strip() + "\n", encoding="utf-8")
    return {"file": target.name, "chars": len(str(text))}


def build_presentations(args: argparse.Namespace) -> list[dict[str, Any]]:
    manifest = _read_json(_path(args.run_manifest))
    manifest.setdefault("Run_ID", args.run_id)
    decisions = _read_csv(_path(args.decisions))
    plans = _read_csv(_path(args.entry_plans))
    market_status = _read_json(_path(args.market_status))
    alerts = _read_csv(_path(args.exit_alerts))
    trade_date = str(manifest.get("Technical_Date") or args.trade_date or "")
    cfg = UiConfig(max_message_length=args.message_max_length)
    output = Path(args.preview_dir or args.reports_dir or "data/output/telegram_preview")
    output.mkdir(parents=True, exist_ok=True)

    ihsg = _read_csv(ROOT / "data/input/IHSG.csv")
    active = _read_csv(ROOT / "data/state/ACTIVE_TRADES.csv")
    rendered: list[dict[str, Any]] = []

    def render(name: str, builder: Callable[[], str]) -> None:
        try:
            rendered.append(_write_preview(output, name, builder()))
        except Exception as exc:
            raise RuntimeError(f"CURRENT_TELEGRAM_RENDER_FAILED:{name}:{exc}") from exc

    render(
        "01_pipeline_status",
        lambda: format_pipeline_status(manifest, decisions, alerts, config=cfg, entry_plans=plans),
    )
    render(
        "02_market_outlook",
        lambda: format_market_outlook(
            trade_date,
            market_status,
            {},
            "Broker fixture",
            "Gunakan area entry dan tunggu trigger valid.",
            ("Trend, momentum, dan volume selaras.",),
            ("False breakout dan harga terlalu jauh dari entry.",),
            config=cfg,
        ),
    )
    render(
        "03_watchlist",
        lambda: format_watchlist(trade_date, decisions, plans, config=cfg),
    )
    ranked = rank_watchlist(decisions, plans).head(cfg.max_watchlist_items)
    symbol_col = next((col for col in ("Symbol", "EMITEN", "Ticker") if col in ranked.columns), None)
    for index, (_, row) in enumerate(ranked.iterrows(), start=1):
        symbol = str(row.get(symbol_col, "UNKNOWN") if symbol_col else "UNKNOWN").strip().upper()
        plan = pd.Series(dtype=object)
        if not plans.empty:
            plan_symbol = next((col for col in ("Symbol", "EMITEN", "Ticker") if col in plans.columns), None)
            if plan_symbol:
                found = plans[plans[plan_symbol].astype(str).str.upper().str.replace(".JK", "", regex=False) == symbol.replace(".JK", "")]
                if not found.empty:
                    plan = found.iloc[0]
        render(
            f"04_signal_detail_{index:02d}_{symbol}",
            lambda row=row, plan=plan: format_signal_detail(trade_date, row, plan, config=cfg),
        )
    render(
        "05_closing_bell",
        lambda: format_closing_bell(trade_date, ihsg, market_status, {}, decisions, plans, config=cfg),
    )
    render(
        "06_position_evaluation",
        lambda: format_position_evaluation(trade_date, active, alerts, decisions, plans, config=cfg),
    )

    quality = str(manifest.get("Data_Quality_Status", "VALID"))
    warnings = manifest.get("Warnings") or []
    if quality.upper() != "VALID" or warnings or manifest.get("Fallback_Used") or manifest.get("Broker_Date_Override"):
        render(
            "07_data_warning",
            lambda: format_data_warning(
                run_id=manifest.get("Run_ID", args.run_id),
                expected_date=manifest.get("Latest_Expected_Trading_Date", trade_date),
                latest_valid_date=manifest.get("Historical_Latest_Valid_Date", trade_date),
                broker_date=manifest.get("Broker_Date", "DATA_NOT_AVAILABLE"),
                fallback_used=bool(manifest.get("Fallback_Used", False)),
                broker_override=bool(manifest.get("Broker_Date_Override", False)),
                data_status=quality,
                warnings=warnings,
                data_source=str(manifest.get("Data_Source", "")),
                config=cfg,
            ),
        )
    for index, (_, row) in enumerate(alerts.iterrows(), start=1):
        render(f"08_exit_alert_{index:02d}", lambda row=row: format_exit_alert(row, config=cfg))
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate current Telegram presentation without legacy bot delivery")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--run-manifest", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--entry-plans", default="")
    parser.add_argument("--market-status", default="")
    parser.add_argument("--exit-alerts", default="")
    parser.add_argument("--watchlist-outcomes", default="")
    parser.add_argument("--backtest-summary", default="")
    parser.add_argument("--reports-dir", default="")
    parser.add_argument("--preview-dir", default="")
    parser.add_argument("--message-max-length", type=int, default=3800)
    args = parser.parse_args()
    rendered = build_presentations(args)
    manifest_path = Path(args.preview_dir or args.reports_dir or "data/output/telegram_preview") / "CURRENT_PRESENTATION_MANIFEST.json"
    manifest_path.write_text(
        json.dumps({"run_id": args.run_id, "presentation_contract": "CURRENT_PROFESSIONAL_UI", "reports": rendered}, indent=2),
        encoding="utf-8",
    )
    print(f"CURRENT_TELEGRAM_PRESENTATION_VALID reports={len(rendered)} output={manifest_path.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
