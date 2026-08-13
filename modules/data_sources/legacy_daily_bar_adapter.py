from __future__ import annotations

"""Legacy historical-provider adapter for the canonical DailyBar boundary.

The legacy Yahoo/historical downloader remains responsible only for acquisition.
Before Technical Feature Engine consumption, every historical row is mapped to a
canonical ``DailyBar`` and routed through ``DataSourceManager``.  The adapter
then materializes a run-scoped CSV view containing only canonical, accepted
records.  OHLCV values are copied from canonical fields without recalculation.
"""

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from modules.data_sources.canonical import DailyBar, compute_payload_hash, now_wib

CANONICAL_DAILY_HISTORY_CONTRACT = "SDE_CANONICAL_DAILY_HISTORY_V1"
LEGACY_PROVIDER_NAME = "HISTORICAL_PROVIDER"


def _clean_symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os_getpid()}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
    tmp.replace(path)


def os_getpid() -> int:
    # Kept behind a tiny helper so tests can monkeypatch deterministic temp names.
    import os
    return os.getpid()


def _source_path(input_dir: Path, symbol: str) -> Path | None:
    for candidate in (input_dir / f"{symbol}.csv", input_dir / f"{symbol}.JK.csv"):
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _column(frame: pd.DataFrame, *aliases: str) -> str | None:
    normalized = {
        str(name).strip().lower().replace("_", " ").replace("-", " "): str(name)
        for name in frame.columns
    }
    for alias in aliases:
        key = alias.strip().lower().replace("_", " ").replace("-", " ")
        if key in normalized:
            return normalized[key]
    return None


def _number(row: pd.Series, column: str | None) -> float | None:
    if not column:
        return None
    try:
        value = row.get(column)
        return None if pd.isna(value) else float(value)
    except (TypeError, ValueError):
        return None


def _daily_bar(
    *,
    symbol: str,
    market_date: str,
    row: pd.Series,
    source_path: Path,
    row_number: int,
    columns: dict[str, str | None],
) -> DailyBar:
    received = now_wib().isoformat()
    record = DailyBar(
        symbol=symbol,
        market_date=market_date,
        event_timestamp=f"{market_date}T16:15:00+07:00",
        received_at=received,
        source=LEGACY_PROVIDER_NAME,
        source_record_id=f"FILE:{source_path.name}:{market_date}:{row_number}",
        raw_payload_hash=compute_payload_hash(row.to_dict()),
        open=_number(row, columns["open"]),
        high=_number(row, columns["high"]),
        low=_number(row, columns["low"]),
        close=_number(row, columns["close"]),
        volume=_number(row, columns["volume"]),
        value=_number(row, columns["value"]),
        adjusted_close=_number(row, columns["adjusted_close"]),
        is_closed=True,
    )
    for field_name in ("open", "high", "low", "close", "volume", "adjusted_close"):
        value = getattr(record, field_name)
        if value is not None:
            record.set_provenance(field_name, LEGACY_PROVIDER_NAME, value)
    return record


def _canonical_row(record: DailyBar) -> dict[str, Any]:
    return {
        "Symbol": record.symbol,
        "Ticker": f"{record.symbol}.JK",
        "Date": record.market_date,
        "Open": record.open,
        "High": record.high,
        "Low": record.low,
        "Close": record.close,
        "Adj Close": record.adjusted_close,
        "Volume": record.volume,
    }


def materialize_legacy_daily_history(
    manager,
    *,
    input_dir: str | Path,
    symbols: list[str],
    expected_market_date: str,
    run_id: str,
    manifest_dir: str | Path,
) -> tuple[Path, dict[str, Any]]:
    """Materialize a run-scoped canonical DailyBar view through DataSourceManager.

    ``manager`` is intentionally an object argument rather than a second router.
    Production passes ``DataSourceManager`` itself, so the manager remains the
    sole routing/quality boundary and this adapter only maps legacy file rows.
    """
    source_root = Path(input_dir)
    manifest_root = Path(manifest_dir)
    expected = str(expected_market_date or "").strip()[:10]
    if not expected:
        raise RuntimeError("CANONICAL_EXPECTED_MARKET_DATE_MISSING")

    selected = []
    seen_symbols: set[str] = set()
    for raw_symbol in symbols:
        symbol = _clean_symbol(raw_symbol)
        if symbol and symbol not in seen_symbols:
            selected.append(symbol)
            seen_symbols.add(symbol)
    if not selected:
        raise RuntimeError("CANONICAL_SYMBOL_UNIVERSE_EMPTY")

    canonical_root = source_root.parent / "canonical_runs" / run_id
    if canonical_root.exists():
        shutil.rmtree(canonical_root)
    canonical_root.mkdir(parents=True, exist_ok=True)

    accepted_symbols: list[str] = []
    rejected_symbols: dict[str, str] = {}
    symbol_lineage: list[dict[str, Any]] = []
    total_rows_seen = 0
    total_rows_accepted = 0
    total_rows_rejected = 0
    total_rows_after_expected = 0
    total_duplicate_dates = 0

    for symbol in selected:
        source = _source_path(source_root, symbol)
        if source is None:
            rejected_symbols[symbol] = "SOURCE_FILE_MISSING"
            continue

        try:
            frame = pd.read_csv(source, low_memory=False)
        except Exception as exc:
            rejected_symbols[symbol] = f"SOURCE_FILE_READ_FAILED:{type(exc).__name__}"
            continue
        if frame.empty:
            rejected_symbols[symbol] = "SOURCE_FILE_EMPTY"
            continue

        date_col = _column(frame, "Date", "Datetime", "Market Date")
        columns = {
            "open": _column(frame, "Open"),
            "high": _column(frame, "High"),
            "low": _column(frame, "Low"),
            "close": _column(frame, "Close"),
            "volume": _column(frame, "Volume"),
            "value": _column(frame, "Value", "Traded Value"),
            "adjusted_close": _column(frame, "Adj Close", "Adjusted Close"),
        }
        if not date_col:
            rejected_symbols[symbol] = "DATE_COLUMN_MISSING"
            continue
        if any(columns[name] is None for name in ("open", "high", "low", "close", "volume")):
            rejected_symbols[symbol] = "REQUIRED_OHLCV_COLUMN_MISSING"
            continue

        working = frame.copy()
        working["__market_date"] = pd.to_datetime(working[date_col], errors="coerce").dt.date.astype(str)
        invalid_date_mask = working["__market_date"].isin({"NaT", "None", ""})
        invalid_date_count = int(invalid_date_mask.sum())
        working = working.loc[~invalid_date_mask].copy()
        total_rows_seen += int(len(frame))
        total_rows_rejected += invalid_date_count

        after_expected = working["__market_date"] > expected
        total_rows_after_expected += int(after_expected.sum())
        working = working.loc[~after_expected].copy()
        if working.empty:
            rejected_symbols[symbol] = "NO_ROWS_ON_OR_BEFORE_EXPECTED_DATE"
            continue

        working["__order"] = range(len(working))
        working = working.sort_values(["__market_date", "__order"])
        duplicate_mask = working.duplicated("__market_date", keep="last")
        duplicate_count = int(duplicate_mask.sum())
        total_duplicate_dates += duplicate_count
        total_rows_rejected += duplicate_count
        working = working.loc[~duplicate_mask].copy()

        canonical_rows: list[dict[str, Any]] = []
        quality_warnings = 0
        hard_rejections = 0
        for row_number, (_, row) in enumerate(working.iterrows(), start=1):
            market_date = str(row["__market_date"])
            record = _daily_bar(
                symbol=symbol,
                market_date=market_date,
                row=row,
                source_path=source,
                row_number=row_number,
                columns=columns,
            )
            routed = manager.route(
                "DailyBar",
                symbol,
                market_date,
                candidates={LEGACY_PROVIDER_NAME: record},
                expected_market_date=market_date,
            )
            if routed.record is None or routed.quality is None or not routed.quality.accepted:
                hard_rejections += 1
                total_rows_rejected += 1
                continue
            if routed.quality.findings:
                quality_warnings += 1
            canonical_rows.append(_canonical_row(routed.record))

        if not canonical_rows:
            rejected_symbols[symbol] = "NO_CANONICAL_ROWS_ACCEPTED"
            symbol_lineage.append({
                "symbol": symbol,
                "source": str(source.resolve()),
                "source_sha256": _file_sha256(source),
                "canonical": "",
                "canonical_sha256": "",
                "rows_accepted": 0,
                "hard_rejections": hard_rejections,
                "quality_warning_rows": quality_warnings,
                "duplicate_dates_dropped": duplicate_count,
                "status": "REJECTED",
            })
            continue

        latest = max(str(item["Date"]) for item in canonical_rows)
        if latest != expected:
            rejected_symbols[symbol] = f"EXPECTED_DATE_NOT_CANONICAL:{latest}!={expected}"
            total_rows_rejected += len(canonical_rows)
            symbol_lineage.append({
                "symbol": symbol,
                "source": str(source.resolve()),
                "source_sha256": _file_sha256(source),
                "canonical": "",
                "canonical_sha256": "",
                "latest_canonical_date": latest,
                "rows_accepted": 0,
                "hard_rejections": hard_rejections,
                "quality_warning_rows": quality_warnings,
                "duplicate_dates_dropped": duplicate_count,
                "status": "REJECTED_EXPECTED_DATE",
            })
            continue

        output = canonical_root / f"{symbol}.csv"
        pd.DataFrame(canonical_rows).to_csv(output, index=False)
        accepted_symbols.append(symbol)
        total_rows_accepted += len(canonical_rows)
        symbol_lineage.append({
            "symbol": symbol,
            "source": str(source.resolve()),
            "source_sha256": _file_sha256(source),
            "canonical": str(output.resolve()),
            "canonical_sha256": _file_sha256(output),
            "latest_canonical_date": latest,
            "rows_accepted": len(canonical_rows),
            "hard_rejections": hard_rejections,
            "quality_warning_rows": quality_warnings,
            "duplicate_dates_dropped": duplicate_count,
            "status": "CANONICAL_ACCEPTED",
        })

    if not accepted_symbols:
        raise RuntimeError("CANONICAL_DAILY_HISTORY_EMPTY")

    audit = {
        "Contract": CANONICAL_DAILY_HISTORY_CONTRACT,
        "Run_ID": run_id,
        "Boundary": "DataSourceManager.route",
        "Legacy_Adapter": "LegacyHistoricalProviderAdapter",
        "Legacy_Provider": LEGACY_PROVIDER_NAME,
        "Expected_Closed_Date": expected,
        "Source_Input_Dir": str(source_root.resolve()),
        "Canonical_Input_Dir": str(canonical_root.resolve()),
        "Selected_Symbol_Count": len(selected),
        "Canonical_Symbol_Count": len(accepted_symbols),
        "Rejected_Symbol_Count": len(rejected_symbols),
        "Canonical_Coverage_Ratio": len(accepted_symbols) / len(selected),
        "Accepted_Symbols": accepted_symbols,
        "Rejected_Symbols": rejected_symbols,
        "Rows_Seen": total_rows_seen,
        "Rows_Accepted": total_rows_accepted,
        "Rows_Rejected": total_rows_rejected,
        "Rows_After_Expected_Date_Blocked": total_rows_after_expected,
        "Duplicate_Dates_Dropped": total_duplicate_dates,
        "Provider_Metadata": manager.provider_metadata(
            record_type="DailyBar",
            providers_attempted=[LEGACY_PROVIDER_NAME],
            coverage_ratio=len(accepted_symbols) / len(selected),
        ),
        "Lineage": symbol_lineage,
    }
    audit_path = manifest_root / f"CANONICAL_DAILY_HISTORY_{run_id}.json"
    _atomic_json(audit_path, audit)
    audit["Manifest_Path"] = str(audit_path.resolve())
    return canonical_root, audit
