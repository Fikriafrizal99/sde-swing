#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.ai_interpretation.watchlist import WatchlistAIResult, WatchlistAIService, build_watchlist_context
from modules.job_runner.delivery import deliver, telegram_route
from modules.job_runner.reports import ReportPayload
from modules.job_runner.runtime import RunnerContext, load_environment_file, read_json, resolve
from modules.telegram.watchlist_ai_ui import format_watchlist_ai_failure, format_watchlist_ai_interpretation
from swing_utils import PACKAGE_VERSION, make_run_id


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _load_context(args: argparse.Namespace) -> RunnerContext:
    load_environment_file(ROOT / ".env")
    config_path = resolve(args.config)
    scheduler_path = resolve(args.scheduler_config)
    config = read_json(config_path)
    scheduler = read_json(scheduler_path)
    ctx = RunnerContext(
        job="watchlist_ai",
        config_path=config_path,
        scheduler_config_path=scheduler_path,
        trade_date=date.fromisoformat(args.trade_date),
        run_id=make_run_id(prefix="SDE-WATCHLIST-AI"),
        dry_run=bool(args.dry_run),
        no_telegram=bool(args.no_telegram),
        force=bool(args.force),
    )
    ctx.config = config if isinstance(config, dict) else {}
    ctx.scheduler_config = scheduler if isinstance(scheduler, dict) else {}
    ctx.config_provenance = {
        "config_version": str(ctx.scheduler_config.get("config_version") or PACKAGE_VERSION),
    }
    return ctx


def _chart_for(symbol: str, scheduler: dict[str, Any]) -> Path | None:
    reporting = scheduler.get("enhanced_reporting", {}) if isinstance(scheduler, dict) else {}
    root = Path(str(reporting.get("final_watchlist_chart_output_root") or "output/final_watchlist"))
    if not root.is_absolute():
        root = ROOT / root
    path = root / f"{symbol.upper()}_setup.png"
    return path if path.exists() and path.is_file() else None


def _final_watchlist_path(trade_date: str, scheduler: dict[str, Any]) -> Path:
    reporting = scheduler.get("enhanced_reporting", {}) if isinstance(scheduler, dict) else {}
    output_root = Path(str(reporting.get("output_root") or "data/output"))
    if not output_root.is_absolute():
        output_root = ROOT / output_root
    return output_root / "final_watchlist" / f"sde-final-watchlist-{trade_date}.csv"


def _official_status_ok(trade_date: str) -> tuple[bool, str]:
    status_path = ROOT / "data/output/job_status/final_watchlist_latest.json"
    status = read_json(status_path)
    if not isinstance(status, dict) or not status:
        return False, "FINAL_WATCHLIST_STATUS_MISSING"
    if str(status.get("trade_date") or "")[:10] != trade_date:
        return False, "FINAL_WATCHLIST_STATUS_DATE_MISMATCH"
    state = str(status.get("status") or status.get("status_v1_7") or "").upper()
    if state not in {"SUCCESS", "SUCCESS_WITH_WARNING"}:
        return False, f"FINAL_WATCHLIST_NOT_SUCCESS:{state or 'UNKNOWN'}"
    return True, state


def _interpretation_payload(result: WatchlistAIResult) -> ReportPayload:
    text = format_watchlist_ai_interpretation({
        "symbol": result.symbol,
        "trade_date": result.trade_date,
        "decision": result.decision,
        "analysis": result.analysis,
        "conclusion": result.conclusion,
    })
    # Per-symbol report_type keeps delivery idempotency independent while the
    # `watchlist_ai` topic label keeps all messages in the dedicated AI route.
    return ReportPayload(
        report_type=f"watchlist_ai_interpretation_{result.symbol.lower()}",
        filename=f"watchlist_ai_{result.symbol.lower()}.txt",
        text=text,
        topic="watchlist_ai",
        symbol=result.symbol,
        signal_version=result.context_hash[:24],
    )


def _failure_payload(trade_date: str, symbols: list[str], reason: str = "") -> ReportPayload:
    return ReportPayload(
        report_type="watchlist_ai_status",
        filename="watchlist_ai_status.txt",
        text=format_watchlist_ai_failure(trade_date=trade_date, symbols=symbols, reason=reason),
        topic="watchlist_ai",
        signal_version=trade_date,
    )


def _deliver_ai(ctx: RunnerContext, payloads: list[ReportPayload]) -> list[dict[str, Any]]:
    """Deliver only when a dedicated AI topic is configured.

    Watchlist AI is not allowed to fall back into the Telegram main chat or any
    News/IDX/SDE report topic. `TELEGRAM_THREAD_AI_ID` is the preferred route.
    """
    if not payloads:
        return []
    configured: list[ReportPayload] = []
    for payload in payloads:
        route = telegram_route(ctx, payload)
        if str(route.get("message_thread_id") or "").strip():
            configured.append(payload)
    if not configured:
        return [{
            "status": "SKIPPED_AI_TOPIC_NOT_CONFIGURED",
            "category": "AI",
            "trade_date": ctx.trade_date.isoformat(),
            "report_count": len(payloads),
            "required_env": "TELEGRAM_THREAD_AI_ID",
        }]
    return deliver(ctx, configured)


def run(ctx: RunnerContext) -> dict[str, Any]:
    service = WatchlistAIService(ctx.scheduler_config)
    if not service.enabled:
        return {"status": "DISABLED", "payloads": [], "results": []}

    notify_failure = bool(service.config.get("notify_on_failure", True))
    ok, official_status = _official_status_ok(ctx.trade_date.isoformat())
    if not ok:
        payloads = []
        if notify_failure:
            payloads.append(_failure_payload(
                ctx.trade_date.isoformat(),
                [],
                "Interpretasi tidak dijalankan karena Final Watchlist resmi belum berstatus sukses untuk tanggal tersebut.",
            ))
        return {
            "status": "SKIPPED_OFFICIAL_NOT_READY",
            "reason": official_status,
            "payloads": payloads,
            "results": [],
        }

    csv_path = _final_watchlist_path(ctx.trade_date.isoformat(), ctx.scheduler_config)
    if not csv_path.exists() or not csv_path.is_file() or csv_path.stat().st_size <= 0:
        payloads = []
        if notify_failure:
            payloads.append(_failure_payload(
                ctx.trade_date.isoformat(),
                [],
                "Artifact Final Watchlist resmi tidak tersedia; jalur SDE resmi tidak diubah.",
            ))
        return {
            "status": "SOURCE_ARTIFACT_MISSING",
            "payloads": payloads,
            "results": [],
            "source": str(csv_path),
        }

    rows = _read_csv(csv_path)
    if not rows or service.max_symbols <= 0:
        return {
            "status": "NO_SYMBOLS",
            "official_status": official_status,
            "payloads": [],
            "results": [],
            "source": str(csv_path),
        }

    selected = rows[: service.max_symbols]
    results: list[WatchlistAIResult] = []
    payloads: list[ReportPayload] = []
    failed_symbols: list[str] = []

    for row in selected:
        symbol = str(row.get("symbol") or "").strip().upper().replace(".JK", "")
        chart = _chart_for(symbol, ctx.scheduler_config) if symbol else None
        context = build_watchlist_context(row, chart_path=chart)
        result = service.interpret(context)
        results.append(result)
        if result.success:
            payloads.append(_interpretation_payload(result))
        else:
            failed_symbols.append(result.symbol or symbol or "UNKNOWN")

    manifest = service.write_manifest(ctx.trade_date.isoformat(), results)
    if failed_symbols and notify_failure:
        payloads.append(_failure_payload(
            ctx.trade_date.isoformat(),
            failed_symbols,
            "Semua provider yang dikonfigurasi gagal atau responsnya ditolak validator untuk emiten tersebut.",
        ))

    status = (
        "SUCCESS" if results and all(item.success for item in results)
        else "PARTIAL" if any(item.success for item in results)
        else "ALL_PROVIDERS_FAILED"
    )
    return {
        "status": status,
        "official_status": official_status,
        "source": str(csv_path),
        "manifest": str(manifest),
        "results": results,
        "payloads": payloads,
        "failed_symbols": failed_symbols,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated Final Watchlist AI interpretation.")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--config", default="config/pipeline.json")
    parser.add_argument("--scheduler-config", default="config/scheduler.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--force", action="store_true")
    args, _ = parser.parse_known_args(argv)

    try:
        ctx = _load_context(args)
        outcome = run(ctx)
        payloads = list(outcome.get("payloads", []))
        delivery = _deliver_ai(ctx, payloads)
        print(json.dumps({
            "status": outcome.get("status"),
            "trade_date": args.trade_date,
            "failed_symbols": outcome.get("failed_symbols", []),
            "manifest": outcome.get("manifest", ""),
            "delivery": delivery,
        }, ensure_ascii=False, default=str), flush=True)
        # Watchlist AI is explicitly non-blocking. Provider failure is reported
        # through its own artifact/Telegram status and never changes SDE exit code.
        return 0
    except Exception as exc:
        # Best-effort information payload. Even if this secondary notification
        # fails, return success so the already-finished Final Watchlist remains
        # the authoritative job result.
        try:
            ctx = _load_context(args)
            payload = _failure_payload(
                args.trade_date,
                [],
                f"Subsystem Watchlist AI mengalami error operasional ({type(exc).__name__}); Final Watchlist resmi tetap tidak terpengaruh.",
            )
            _deliver_ai(ctx, [payload])
        except Exception:
            pass
        print(f"[WATCHLIST AI] non-blocking failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
