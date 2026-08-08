#!/usr/bin/env python3
"""History-aware runtime for actual OPEN portfolio positions.

The stable Position Management decision rules remain in
``position_management_engine.py``.  This wrapper only enriches its broker input
with historical Broker Summary context (Current/3D/5D/7D/Since Entry), archives
the latest summary into the shared Swing DB, and persists an audit snapshot.

Discovery, Broker Fusion, Decision Engine, Exit Engine, and Final Watchlist are
not invoked or modified here.
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
        "effective_state": state,
        "effective_reason": "historical Broker Summary belum tersedia; memakai latest broker state",
        "3D": {"context": "UNAVAILABLE", "observation_count": 0},
        "5D": {"context": "UNAVAILABLE", "observation_count": 0},
        "7D": {"context": "UNAVAILABLE", "observation_count": 0},
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
            "persistence_pct": 0.0,
        },
        "broker_score_trend": "INSUFFICIENT_DATA",
        "flow_trend": "INSUFFICIENT_DATA",
    }


def _broker_reason(context: dict[str, Any]) -> str:
    since = context.get("since_entry") or {}
    d3 = context.get("3D") or {}
    d5 = context.get("5D") or {}
    d7 = context.get("7D") or {}
    parts = [
        f"Broker history: current={context.get('current_state', 'UNAVAILABLE')}",
        f"3D={d3.get('context', 'UNAVAILABLE')}",
        f"5D={d5.get('context', 'UNAVAILABLE')}",
        f"7D={d7.get('context', 'UNAVAILABLE')}",
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
            f"broker history query gagal ({type(exc).__name__}); memakai latest broker state"
        )

    # A standalone Position Management run can precede the normal DB archiver.
    # If no historical observation is available, the current CSV remains a
    # safe non-mutating fallback for the management layer only.
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
        decision["reason"] += (
            f" Broker Summary terakhir {broker_data_date}, bukan {analysis_date}."
        )

    since = broker_context.get("since_entry") or {}
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
        # broker_state is the effective state consumed by the stable management
        # rules.  Current and all historical contexts are also exposed below.
        "broker_state": str(broker_context.get("effective_state") or "UNAVAILABLE"),
        "broker_score": broker_context.get("current_score"),
        "broker_current_state": broker_context.get("current_state"),
        "broker_effective_state": broker_context.get("effective_state"),
        "broker_data_date": broker_data_date,
        "broker_observation_count": int(broker_context.get("observation_count") or 0),
        "broker_context_3d": (broker_context.get("3D") or {}).get("context"),
        "broker_context_5d": (broker_context.get("5D") or {}).get("context"),
        "broker_context_7d": (broker_context.get("7D") or {}).get("context"),
        "broker_context_since_entry": since.get("context"),
        "broker_net_flow_since_entry": since.get("net_flow"),
        "broker_avg_daily_net_flow": since.get("avg_net_flow"),
        "broker_buy_days": int(since.get("buy_days") or 0),
        "broker_sell_days": int(since.get("sell_days") or 0),
        "broker_accumulation_days": int(since.get("accumulation_days") or 0),
        "broker_distribution_days": int(since.get("distribution_days") or 0),
        "broker_persistence_pct": float(since.get("persistence_pct") or 0.0),
        "broker_score_avg": since.get("score_avg"),
        "broker_score_trend": broker_context.get("broker_score_trend"),
        "broker_flow_trend": broker_context.get("flow_trend"),
        "sector_state": sector_bucket,
        "sector_name": sector_name,
        "market_state": market,
        "management_action": decision["action"],
        "active_stop_loss": decision["active_stop_loss"],
        "extended_target": decision["extended_target"],
        "reason": decision["reason"].strip(),
        "data_quality_status": data_quality,
    }

    # Stable management persistence is preserved.  The additional broker
    # context is stored in its own append-by-date table for auditability.
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
        lines.extend([
            f"📌 <b>{html.escape(row['symbol'])}</b>",
            f"Buy / Current : {base.fmt_price(row.get('buy_price'))} / {base.fmt_price(row.get('current_price'))}",
            f"P/L           : {base.fmt_pct(row.get('pnl_pct'))}",
            "",
            "🎯 <b>INITIAL PLAN</b>",
            f"TP1 : {base.fmt_price(row.get('initial_tp1'))}{tp1_mark}",
            f"TP2 : {base.fmt_price(row.get('initial_tp2'))}{tp2_mark}",
            f"SL  : {base.fmt_price(row.get('initial_stop_loss'))}",
            "",
            "🏦 <b>BROKER POSITION CONTEXT</b>",
            f"Today       : {html.escape(str(row.get('broker_current_state') or '-'))}",
            f"3D          : {html.escape(str(row.get('broker_context_3d') or '-'))}",
            f"5D          : {html.escape(str(row.get('broker_context_5d') or '-'))}",
            f"7D          : {html.escape(str(row.get('broker_context_7d') or '-'))}",
            f"Since Entry : {html.escape(str(row.get('broker_context_since_entry') or '-'))}",
            f"Effective   : <b>{html.escape(str(row.get('broker_effective_state') or '-'))}</b>",
            f"Net Since   : {fmt_money(row.get('broker_net_flow_since_entry'))}",
            f"Buy/Sell Day: {row.get('broker_buy_days', 0)} / {row.get('broker_sell_days', 0)}",
            f"Persistence : {float(row.get('broker_persistence_pct') or 0.0):.0f}%",
            f"Flow Trend  : {html.escape(str(row.get('broker_flow_trend') or '-'))}",
            "",
            "📈 <b>CURRENT</b>",
            f"Technical : {html.escape(str(row.get('technical_state') or '-'))}",
            f"Sector    : {html.escape(str(row.get('sector_state') or '-'))}",
            f"Market    : {html.escape(str(row.get('market_state') or '-'))}",
            "",
            "🛡️ <b>MANAGEMENT</b>",
            f"Action          : <b>{html.escape(str(row.get('management_action') or '-'))}</b>",
            f"Active SL       : {base.fmt_price(row.get('active_stop_loss'))}",
            f"Extended Target : {base.fmt_price(row.get('extended_target'))}",
            f"Data            : {html.escape(str(row.get('data_quality_status') or '-'))}",
            "",
            "🧠 " + html.escape(str(row.get("reason") or "-")),
        ])
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
            # This is deliberately non-blocking.  The stable management engine
            # can still use the latest local CSV and the main SDE stays isolated.
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

    message = telegram_text(results, analysis_date)
    outputs = base.write_outputs(output_root, analysis_date, results, message)
    print(f"POSITION MANAGEMENT: {len(results)} OPEN position(s) analyzed")
    for row in results:
        print(
            f"  {row['symbol']}: {row['management_action']} | "
            f"P/L={base.fmt_pct(row['pnl_pct'])} | broker={row['broker_effective_state']} | "
            f"3D={row['broker_context_3d']} | 5D={row['broker_context_5d']} | "
            f"since={row['broker_context_since_entry']}"
        )
    print(f"Output: {outputs['json']}")

    if args.telegram and not args.no_telegram:
        payload = ReportPayload(
            report_type="POSITION_MANAGEMENT",
            filename="active_portfolio_management.txt",
            text=message,
            topic="position_management",
            signal_status="ACTIVE_PORTFOLIO",
            signal_version=analysis_date,
            input_paths=(str(db_path), str(historical_dir), str(broker_path)),
            source_of_truth=(
                "portfolio_positions",
                "shared_technical_features",
                "broker_summary_latest",
                "broker_summary_history",
            ),
            row_count=len(results),
        )
        delivery = deliver(ctx, [payload])
        failed = [item for item in delivery if item.get("status") == "FAILED"]
        if failed:
            print(f"Telegram delivery failed: {failed}", file=sys.stderr)
            return 50
        print("Telegram: processed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
