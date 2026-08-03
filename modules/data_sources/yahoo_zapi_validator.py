from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from modules.data_sources.config import load_data_source_config
from modules.data_sources.zapi_idx_adapter import ZapiIdxAdapter, ZapiIdxClient


def _norm_symbol(value: str) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def _latest_yahoo_bar(folder: Path, symbol: str, market_date: str) -> dict[str, Any] | None:
    candidates = [folder / f"{symbol}.csv", folder / f"{symbol}.JK.csv"]
    for path in candidates:
        if not path.exists() or path.stat().st_size == 0:
            continue
        try:
            frame = pd.read_csv(path, low_memory=False)
        except Exception:
            continue
        date_col = next((c for c in frame.columns if str(c).strip().lower() in {"date", "datetime", "market_date"}), None)
        if not date_col:
            continue
        parsed = pd.to_datetime(frame[date_col], errors="coerce")
        rows = frame.loc[parsed.dt.date.astype(str).eq(market_date)]
        if rows.empty:
            continue
        row = rows.iloc[-1]
        def num(*names: str) -> float | None:
            for name in names:
                if name in row.index:
                    try:
                        value = row[name]
                        return None if pd.isna(value) else float(value)
                    except (TypeError, ValueError):
                        return None
            return None
        return {
            "path": str(path),
            "open": num("Open", "open"),
            "high": num("High", "high"),
            "low": num("Low", "low"),
            "close": num("Close", "close"),
            "volume": num("Volume", "volume"),
        }
    return None


def _pct_diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    base = max(abs(right), 1e-9)
    return abs(left - right) / base


def validate_yahoo_against_zapi(
    *,
    historical_dir: str | Path,
    symbols: list[str],
    market_date: str,
    output_dir: str | Path,
    config_path: str | Path = "config/data_sources.json",
    price_tolerance_pct: float = 0.005,
    volume_tolerance_pct: float = 0.20,
    max_symbols: int = 0,
) -> dict[str, Any]:
    """Validate Yahoo execution bars against ZAPI without replacing engine data.

    Yahoo remains the executable historical source. ZAPI is a secondary validator
    and enrichment source. Missing credentials or API errors never masquerade as
    successful validation and never stop the trading pipeline.
    """
    folder = Path(historical_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    normalized = list(dict.fromkeys(_norm_symbol(item) for item in symbols if _norm_symbol(item)))
    if max_symbols > 0:
        normalized = normalized[:max_symbols]

    cfg = load_data_source_config(config_path)
    source_cfg = cfg.sources.get("ZAPI_IDX")
    rows: list[dict[str, Any]] = []
    if source_cfg is None:
        summary = {
            "status": "NOT_CONFIGURED",
            "reason": "ZAPI_SOURCE_CONFIG_NOT_FOUND",
            "market_date": market_date,
            "symbols_requested": len(normalized),
            "validated": 0,
            "matched": 0,
            "conflicted": 0,
            "missing_yahoo": 0,
            "missing_zapi": len(normalized),
            "coverage_ratio": 0.0,
            "rows": rows,
        }
        _write_outputs(out, market_date, rows, summary)
        return summary

    client = ZapiIdxClient.from_config(source_cfg)
    adapter = ZapiIdxAdapter(client)
    configured = client.is_configured()

    for symbol in normalized:
        yahoo = _latest_yahoo_bar(folder, symbol, market_date)
        zapi_record = None
        error = ""
        if configured:
            try:
                raw = client.fetch_raw("DailyBar", symbol, market_date=market_date, date=market_date, length=10, start=0)
                mapped = adapter.to_canonical("DailyBar", raw, symbol=symbol, market_date=market_date)
                zapi_record = next((item for item in mapped if item.symbol == symbol and item.market_date == market_date), mapped[0] if mapped else None)
            except Exception as exc:  # validation is intentionally non-blocking
                error = f"{type(exc).__name__}: {exc}"

        close_diff = _pct_diff(yahoo.get("close") if yahoo else None, zapi_record.close if zapi_record else None)
        open_diff = _pct_diff(yahoo.get("open") if yahoo else None, zapi_record.open if zapi_record else None)
        high_diff = _pct_diff(yahoo.get("high") if yahoo else None, zapi_record.high if zapi_record else None)
        low_diff = _pct_diff(yahoo.get("low") if yahoo else None, zapi_record.low if zapi_record else None)
        volume_diff = _pct_diff(yahoo.get("volume") if yahoo else None, zapi_record.volume if zapi_record else None)
        price_diffs = [item for item in (open_diff, high_diff, low_diff, close_diff) if item is not None]
        price_match = bool(price_diffs) and max(price_diffs) <= price_tolerance_pct
        volume_match = volume_diff is None or volume_diff <= volume_tolerance_pct

        if yahoo is None:
            status = "MISSING_YAHOO"
        elif not configured:
            status = "VALID_WITHOUT_ZAPI_VALIDATION"
        elif zapi_record is None:
            status = "ZAPI_UNAVAILABLE"
        elif price_match and volume_match:
            status = "MATCH"
        elif price_match:
            status = "PRICE_MATCH_VOLUME_DIFFERENCE"
        else:
            status = "CONFLICT"

        rows.append({
            "market_date": market_date,
            "symbol": symbol,
            "status": status,
            "yahoo_source": "YAHOO",
            "zapi_source": "ZAPI_IDX" if zapi_record else "NOT_AVAILABLE",
            "yahoo_path": yahoo.get("path", "") if yahoo else "",
            "yahoo_open": yahoo.get("open") if yahoo else None,
            "yahoo_high": yahoo.get("high") if yahoo else None,
            "yahoo_low": yahoo.get("low") if yahoo else None,
            "yahoo_close": yahoo.get("close") if yahoo else None,
            "yahoo_volume": yahoo.get("volume") if yahoo else None,
            "zapi_open": zapi_record.open if zapi_record else None,
            "zapi_high": zapi_record.high if zapi_record else None,
            "zapi_low": zapi_record.low if zapi_record else None,
            "zapi_close": zapi_record.close if zapi_record else None,
            "zapi_volume": zapi_record.volume if zapi_record else None,
            "open_diff_pct": open_diff,
            "high_diff_pct": high_diff,
            "low_diff_pct": low_diff,
            "close_diff_pct": close_diff,
            "volume_diff_pct": volume_diff,
            "error": error,
        })

    validated = sum(row["status"] in {"MATCH", "PRICE_MATCH_VOLUME_DIFFERENCE", "CONFLICT"} for row in rows)
    matched = sum(row["status"] in {"MATCH", "PRICE_MATCH_VOLUME_DIFFERENCE"} for row in rows)
    conflicted = sum(row["status"] == "CONFLICT" for row in rows)
    summary = {
        "status": "SUCCESS" if configured and conflicted == 0 else "SUCCESS_WITH_WARNING" if configured else "NOT_CONFIGURED",
        "reason": "" if configured else "ZAPI_CREDENTIALS_NOT_CONFIGURED",
        "market_date": market_date,
        "symbols_requested": len(rows),
        "validated": validated,
        "matched": matched,
        "conflicted": conflicted,
        "missing_yahoo": sum(row["status"] == "MISSING_YAHOO" for row in rows),
        "missing_zapi": sum(row["status"] in {"ZAPI_UNAVAILABLE", "VALID_WITHOUT_ZAPI_VALIDATION"} for row in rows),
        "coverage_ratio": validated / len(rows) if rows else 0.0,
        "match_ratio": matched / validated if validated else 0.0,
        "price_tolerance_pct": price_tolerance_pct,
        "volume_tolerance_pct": volume_tolerance_pct,
        "execution_source": "YAHOO",
        "validation_source": "ZAPI_IDX" if configured else "NOT_CONFIGURED",
        "rows": rows,
    }
    _write_outputs(out, market_date, rows, summary)
    return summary


def _write_outputs(out: Path, market_date: str, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    csv_path = out / f"yahoo_zapi_reconciliation_{market_date}.csv"
    json_path = out / f"yahoo_zapi_reconciliation_{market_date}.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    payload = dict(summary)
    payload["csv_path"] = str(csv_path)
    payload["json_path"] = str(json_path)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
