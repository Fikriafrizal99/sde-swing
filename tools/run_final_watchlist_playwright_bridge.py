#!/usr/bin/env python3
from __future__ import annotations

"""Narrow Playwright producer hook for Final Watchlist broker-period runs.

This bridge intentionally leaves the Final Watchlist orchestration, broker
fusion, scoring, decision, entry, stop-loss, and target engines untouched.
It only replaces the broker-export wait boundary when the existing optional
Playwright collector is enabled, and enforces that a real 1D TODAY PULSE is
present before any broker/Final Watchlist stage is allowed to run.
"""

import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.broker_bridge import broker_period_context as period_context


_ORIGINAL_WAIT_FOR_MATCHING_EXPORT = period_context.wait_for_matching_export


def _collector_module():
    # Import lazily so Playwright remains an optional dependency while the
    # existing OFF/manual workflow keeps working without browser packages.
    from modules.portfolio import stockbit_playwright_collector as collector

    return collector


def _playwright_enabled() -> bool:
    collector = _collector_module()
    return bool(collector.load_state(collector.DEFAULT_STATE).enabled)


def _browser_timeout_ms() -> int:
    raw = str(os.environ.get("SDE_PLAYWRIGHT_TIMEOUT_MS", "30000")).strip()
    try:
        value = int(raw)
    except ValueError:
        value = 30_000
    return min(max(value, 5_000), 120_000)


def _normalized_symbols(values: Iterable[str]) -> list[str]:
    collector = _collector_module()
    normalized = [collector.normalize_symbol(value) for value in values]
    return list(dict.fromkeys(value for value in normalized if value))


def _publish_combined_artifacts(
    downloads: Path,
    *,
    spec: Any,
    collections: list[Any],
) -> tuple[Path, Path]:
    collector = _collector_module()
    import pandas as pd

    summary = pd.DataFrame(
        [dict(item.summary) for item in collections],
        columns=list(collector.PORTFOLIO_COLUMNS),
    ).loc[:, list(collector.SUMMARY_COLUMNS)]
    raw = pd.DataFrame(
        [dict(row) for item in collections for row in item.raw],
        columns=list(collector.RAW_COLUMNS),
    )

    downloads.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    identity = (
        f"PLAYWRIGHT_{str(spec.period_type).upper()}_"
        f"{str(spec.period_start).replace('-', '')}_"
        f"{str(spec.period_end).replace('-', '')}_{stamp}_{uuid.uuid4().hex[:8]}"
    )
    summary_path = downloads / f"BROKER_SUMMARY_COMBINED_{identity}.csv"
    raw_path = downloads / f"BROKER_RAW_COMBINED_{identity}.csv"
    summary_tmp = summary_path.with_name(f".{summary_path.name}.tmp")
    raw_tmp = raw_path.with_name(f".{raw_path.name}.tmp")

    published_summary = False
    try:
        summary.to_csv(summary_tmp, index=False, encoding="utf-8-sig")
        raw.to_csv(raw_tmp, index=False, encoding="utf-8-sig")
        os.replace(summary_tmp, summary_path)
        published_summary = True
        os.replace(raw_tmp, raw_path)
    except BaseException:
        if published_summary and summary_path.exists():
            try:
                summary_path.unlink()
            except OSError:
                pass
        raise
    finally:
        for temp in (summary_tmp, raw_tmp):
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    return summary_path, raw_path


def _collect_with_playwright(
    downloads: Path,
    expected_symbols: Iterable[str],
    spec: Any,
    *,
    min_coverage: float,
) -> tuple[Path, dict[str, Any]]:
    collector = _collector_module()
    state = collector.load_state(collector.DEFAULT_STATE)
    if not state.enabled:
        raise RuntimeError("PLAYWRIGHT_COLLECTOR_NOT_ENABLED")

    symbols = _normalized_symbols(expected_symbols)
    if not symbols:
        raise RuntimeError("BROKER_PLAYWRIGHT_EXPECTED_SYMBOLS_EMPTY")

    label = "TODAY PULSE" if str(spec.period_type).upper() == "1D" else "PRIMARY FALLBACK"
    print(
        f"[PLAYWRIGHT] Auto collect {label}: {spec.period_type} "
        f"{spec.period_start}..{spec.period_end} | {len(symbols)} symbols",
        flush=True,
    )

    session = None
    collections: list[Any] = []
    try:
        session = collector.StockbitPlaywrightSession(
            collector.DEFAULT_PROFILE,
            state.headless,
            _browser_timeout_ms(),
            collector.DEFAULT_LOG_DIR,
        )
        for symbol in symbols:
            task = collector.BrokerTask(
                symbol=symbol,
                from_date=str(spec.period_start),
                to_date=str(spec.period_end),
                task_key=(
                    f"FINAL_WATCHLIST|{str(spec.period_type).upper()}|"
                    f"{spec.period_start}|{spec.period_end}|{symbol}"
                ),
                source="FINAL_WATCHLIST",
            )
            try:
                collected = session.collect(task)
            except Exception as exc:
                reason = getattr(exc, "code", type(exc).__name__)
                try:
                    session.capture_failure(task, str(reason))
                except Exception:
                    pass
                raise
            collections.append(collected)
            print(f"[PLAYWRIGHT] {symbol} | OK", flush=True)
    except Exception as exc:
        code = str(getattr(exc, "code", type(exc).__name__)).strip().upper()
        detail = str(getattr(exc, "detail", "")).strip()
        suffix = f":{detail}" if detail else ""
        raise RuntimeError(f"BROKER_PLAYWRIGHT_FAILED:{code}{suffix}") from exc
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    summary_path, _raw_path = _publish_combined_artifacts(
        downloads,
        spec=spec,
        collections=collections,
    )
    valid, message, info = period_context.inspect_summary_export(
        summary_path,
        symbols,
        min_coverage,
        spec,
    )
    if not valid:
        raw_path = period_context.raw_companion(summary_path)
        try:
            summary_path.unlink()
        except OSError:
            pass
        if raw_path is not None:
            try:
                raw_path.unlink()
            except OSError:
                pass
        raise RuntimeError(f"BROKER_PLAYWRIGHT_EXPORT_INVALID:{message}")

    print(f"[PLAYWRIGHT] Broker export siap: {summary_path.name}", flush=True)
    return summary_path, info


def playwright_wait_for_matching_export(
    downloads: Path,
    expected_symbols: Iterable[str],
    min_coverage: float,
    spec: Any,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> tuple[Path, dict[str, Any]]:
    """Use Playwright when ON; preserve the existing manual wait when OFF."""
    if _playwright_enabled():
        return _collect_with_playwright(
            downloads,
            expected_symbols,
            spec,
            min_coverage=min_coverage,
        )

    try:
        return _ORIGINAL_WAIT_FOR_MATCHING_EXPORT(
            downloads,
            expected_symbols,
            min_coverage,
            spec,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
        )
    except TimeoutError as exc:
        # A real 1D capture is the mandatory TODAY PULSE boundary. Raising a
        # non-TimeoutError here prevents the legacy multi-day fallback branch
        # from silently continuing without the current-session pulse.
        if str(spec.period_type).upper() == "1D":
            raise RuntimeError(f"BROKER_TODAY_PULSE_REQUIRED:{spec.period_end}") from exc
        raise


def require_today_pulse(payload: dict[str, Any]) -> dict[str, Any]:
    """Fail closed unless sidecar metadata proves a real current-session pulse."""
    primary_end = str(payload.get("broker_period_end", ""))[:10]
    pulse_date = str(payload.get("today_pulse_date", ""))[:10]
    pulse_source = str(payload.get("today_pulse_source", "")).strip().upper()
    available = bool(payload.get("today_pulse_available"))

    if not available:
        raise RuntimeError(f"BROKER_TODAY_PULSE_REQUIRED:{primary_end or 'UNKNOWN'}")
    if not primary_end or pulse_date != primary_end:
        raise RuntimeError(
            f"BROKER_TODAY_PULSE_DATE_MISMATCH:{pulse_date or 'MISSING'}!={primary_end or 'UNKNOWN'}"
        )
    if pulse_source in {"", "STOCKBIT_AGGREGATE_EXPORT", "AGGREGATE", "BROKER_AGGREGATE"}:
        raise RuntimeError(f"BROKER_TODAY_PULSE_SOURCE_INVALID:{pulse_source or 'MISSING'}")
    return payload


def main() -> int:
    # Patch before importing the target runner so its direct function import
    # receives this narrow producer boundary instead of the original waiter.
    period_context.wait_for_matching_export = playwright_wait_for_matching_export

    from tools import run_final_watchlist_broker_period as target

    original_sidecar = target.active_sidecar_payload

    def strict_sidecar(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return require_today_pulse(original_sidecar(*args, **kwargs))

    target.active_sidecar_payload = strict_sidecar
    return int(target.main())


if __name__ == "__main__":
    raise SystemExit(main())
