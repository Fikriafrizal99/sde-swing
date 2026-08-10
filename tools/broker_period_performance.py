#!/usr/bin/env python3
from __future__ import annotations

"""Additive Broker Period performance analytics.

The production signal_outcome_ledger is read-only from this module.  Broker
period metadata is stored in a separate table so existing performance history
and lifecycle rows are not rewritten or migrated.
"""

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/output/analytics/performance"
VALID_SIGNAL_QUALITY = {
    "VALID",
    "PARTIAL_COVERAGE",
    "SUCCESS_WITH_WARNING",
    "VALID_WITH_REFRESH_FALLBACK",
    "VALID_WITH_ZAPI_WARNING",
}
FINAL_OUTCOMES = {"WIN", "LOSS", "AMBIGUOUS"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_period_signal_context (
    context_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    signal_date TEXT,
    broker_period_type TEXT NOT NULL,
    broker_period_start TEXT NOT NULL,
    broker_period_end TEXT NOT NULL,
    broker_trading_days INTEGER NOT NULL,
    broker_snapshot_id TEXT NOT NULL,
    broker_score REAL,
    broker_confidence REAL,
    broker_confidence_bucket TEXT,
    broker_direction TEXT,
    freshness_status TEXT,
    primary_context INTEGER NOT NULL DEFAULT 1,
    scoring_adjustment_applied INTEGER NOT NULL DEFAULT 0,
    freshness_adjustment_applied INTEGER NOT NULL DEFAULT 0,
    persistence_adjustment_applied INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_broker_period_context_signal
    ON broker_period_signal_context(signal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_broker_period_context_run
    ON broker_period_signal_context(run_id);
CREATE INDEX IF NOT EXISTS idx_broker_period_context_period
    ON broker_period_signal_context(broker_period_type, broker_confidence_bucket);
"""


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return default if text.lower() in {"", "nan", "none", "null"} else text


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def bucket(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value < 60:
        return "<60%"
    if value < 75:
        return "60-74%"
    return ">=75%"


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"SDE_DB_NOT_FOUND:{db_path}")
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def decision_value(source_json: str, *keys: str) -> Any:
    try:
        source = json.loads(source_json or "{}")
    except Exception:
        return None
    decision = source.get("decision", {}) if isinstance(source, dict) else {}
    if not isinstance(decision, dict):
        return None
    normalized = {str(key).strip().lower(): value for key, value in decision.items()}
    for key in keys:
        value = normalized.get(key.strip().lower())
        if value is not None and norm(value):
            return value
    return None


def capture(db_path: Path, run_id: str, manifest_path: Path) -> int:
    if not manifest_path.exists():
        print(f"[BROKER PERIOD] snapshot manifest tidak ditemukan: {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = [
        "snapshot_id",
        "broker_period_type",
        "broker_period_start",
        "broker_period_end",
        "broker_trading_days",
    ]
    missing = [key for key in required if not norm(manifest.get(key))]
    if missing:
        print(f"[BROKER PERIOD] manifest incomplete: {', '.join(missing)}")
        return 2

    conn = connect(db_path)
    ledger_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_outcome_ledger'"
    ).fetchone()
    if not ledger_exists:
        conn.close()
        print("[BROKER PERIOD] signal_outcome_ledger belum tersedia; capture dilewati.")
        return 0

    rows = conn.execute(
        """
        SELECT signal_id, symbol, signal_date, broker_confidence,
               broker_confidence_bucket, broker_direction, source_json
        FROM signal_outcome_ledger
        WHERE latest_scan_run_id=? OR run_id=?
        """,
        (run_id, run_id),
    ).fetchall()
    inserted = 0
    for row in rows:
        broker_conf = as_float(row["broker_confidence"])
        broker_score = as_float(
            decision_value(
                row["source_json"],
                "Broker_Score_Final",
                "Broker_Confirmation_Score_Final",
                "Broker_Confirmation_Score",
                "Broker_Score",
            )
        )
        context_key = "|".join(
            [row["signal_id"], run_id, str(manifest["snapshot_id"])]
        )
        context_id = hashlib.sha256(context_key.encode("utf-8")).hexdigest()[:32]
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO broker_period_signal_context (
                context_id, signal_id, run_id, symbol, signal_date,
                broker_period_type, broker_period_start, broker_period_end,
                broker_trading_days, broker_snapshot_id, broker_score,
                broker_confidence, broker_confidence_bucket, broker_direction,
                freshness_status, primary_context, scoring_adjustment_applied,
                freshness_adjustment_applied, persistence_adjustment_applied,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context_id,
                row["signal_id"],
                run_id,
                row["symbol"],
                row["signal_date"],
                str(manifest["broker_period_type"]).upper(),
                str(manifest["broker_period_start"]),
                str(manifest["broker_period_end"]),
                int(manifest["broker_trading_days"]),
                str(manifest["snapshot_id"]),
                broker_score,
                broker_conf,
                norm(row["broker_confidence_bucket"], bucket(broker_conf)),
                norm(row["broker_direction"], "UNKNOWN"),
                norm(manifest.get("freshness_status"), "CURRENT"),
                1,
                int(bool(manifest.get("scoring_adjustment_applied", False))),
                int(bool(manifest.get("freshness_adjustment_applied", False))),
                int(bool(manifest.get("persistence_adjustment_applied", False))),
                now_text(),
            ),
        )
        inserted += max(cursor.rowcount, 0)
    conn.commit()
    conn.close()
    print(
        f"[BROKER PERIOD] captured={inserted} run_id={run_id} "
        f"period={manifest['broker_period_type']} snapshot={manifest['snapshot_id']}"
    )
    return 0


def load_analysis(conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    ledger = pd.read_sql_query("SELECT * FROM signal_outcome_ledger", conn)
    contexts = pd.read_sql_query(
        "SELECT * FROM broker_period_signal_context ORDER BY created_at, context_id",
        conn,
    )
    return ledger, contexts


def initial_contexts(contexts: pd.DataFrame) -> pd.DataFrame:
    if contexts.empty:
        return contexts
    ordered = contexts.sort_values(["created_at", "context_id"])
    return ordered.drop_duplicates("signal_id", keep="first")


def _valid_mask(frame: pd.DataFrame) -> pd.Series:
    quality = frame.get("data_quality_status", pd.Series("UNKNOWN", index=frame.index)).fillna("UNKNOWN").astype(str).str.upper()
    status = frame.get("current_status", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()
    outcome = frame.get("final_outcome", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()
    return quality.isin(VALID_SIGNAL_QUALITY) & ~status.eq("INVALIDATED_BEFORE_ENTRY") & ~outcome.eq("INVALIDATED")


def metrics(group: pd.DataFrame) -> dict[str, Any]:
    if group.empty:
        return {
            "Signals": 0,
            "Triggered": 0,
            "Trigger_Rate_Pct": None,
            "Closed": 0,
            "Win": 0,
            "Loss": 0,
            "Win_Rate_Pct": None,
            "Avg_Return_Pct": None,
            "Avg_MFE_Pct": None,
            "Avg_MAE_Pct": None,
        }
    valid = group[_valid_mask(group)].copy()
    triggered = valid[valid.get("entry_date", pd.Series("", index=valid.index)).fillna("").astype(str).str.strip().ne("")]
    final = valid.get("final_outcome", pd.Series("", index=valid.index)).fillna("").astype(str).str.upper()
    closed = valid[final.isin(FINAL_OUTCOMES)]
    wins = int(closed.get("final_outcome", pd.Series(dtype=str)).fillna("").astype(str).str.upper().eq("WIN").sum())
    losses = int(closed.get("final_outcome", pd.Series(dtype=str)).fillna("").astype(str).str.upper().eq("LOSS").sum())
    decided = wins + losses

    def mean_numeric(frame: pd.DataFrame, column: str) -> float | None:
        if column not in frame.columns or frame.empty:
            return None
        values = pd.to_numeric(frame[column], errors="coerce").dropna()
        return None if values.empty else float(values.mean())

    signals = int(len(valid))
    return {
        "Signals": signals,
        "Triggered": int(len(triggered)),
        "Trigger_Rate_Pct": None if signals == 0 else 100.0 * len(triggered) / signals,
        "Closed": int(len(closed)),
        "Win": wins,
        "Loss": losses,
        "Win_Rate_Pct": None if decided == 0 else 100.0 * wins / decided,
        "Avg_Return_Pct": mean_numeric(closed, "realized_return_pct"),
        "Avg_MFE_Pct": mean_numeric(triggered, "mfe_pct"),
        "Avg_MAE_Pct": mean_numeric(triggered, "mae_pct"),
    }


def build_reports(db_path: Path, output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    conn = connect(db_path)
    ledger, contexts = load_analysis(conn)
    conn.close()
    first = initial_contexts(contexts)
    if ledger.empty or first.empty:
        period = pd.DataFrame(columns=["Broker_Period", *metrics(pd.DataFrame()).keys()])
        cross = pd.DataFrame(columns=["Broker_Period", "Broker_Confidence_Bucket", *metrics(pd.DataFrame()).keys()])
        coverage = {"ledger_signals": int(len(ledger)), "mapped_signals": 0}
    else:
        merged = ledger.merge(
            first[[
                "signal_id",
                "broker_period_type",
                "broker_period_start",
                "broker_period_end",
                "broker_trading_days",
                "broker_snapshot_id",
                "broker_confidence_bucket",
            ]].rename(columns={"broker_confidence_bucket": "period_confidence_bucket"}),
            on="signal_id",
            how="inner",
        )
        period_rows = []
        for period_name, group in merged.groupby("broker_period_type", dropna=False):
            period_rows.append({"Broker_Period": str(period_name), **metrics(group)})
        period = pd.DataFrame(period_rows)
        cross_rows = []
        for (period_name, conf_bucket), group in merged.groupby(
            ["broker_period_type", "period_confidence_bucket"], dropna=False
        ):
            cross_rows.append({
                "Broker_Period": str(period_name),
                "Broker_Confidence_Bucket": norm(conf_bucket, "UNKNOWN"),
                **metrics(group),
            })
        cross = pd.DataFrame(cross_rows)
        coverage = {
            "ledger_signals": int(ledger["signal_id"].nunique()),
            "mapped_signals": int(merged["signal_id"].nunique()),
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    period.to_csv(output_dir / "BROKER_PERIOD_PERFORMANCE.csv", index=False, encoding="utf-8-sig")
    cross.to_csv(output_dir / "BROKER_CONFIDENCE_BY_PERIOD.csv", index=False, encoding="utf-8-sig")
    (output_dir / "BROKER_PERIOD_PERFORMANCE_META.json").write_text(
        json.dumps({**coverage, "generated_at": now_text()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return period, cross, coverage


def fmt(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def print_report(period: pd.DataFrame, cross: pd.DataFrame, coverage: dict[str, int]) -> None:
    print("\nBROKER PERIOD PERFORMANCE")
    print("=" * 88)
    print(
        f"Metadata coverage: {coverage.get('mapped_signals', 0)}/{coverage.get('ledger_signals', 0)} signal "
        "(signal lama sebelum fitur ini tetap utuh dan tidak dipaksa diberi horizon)."
    )
    if period.empty:
        print("Belum ada signal dengan Broker Period metadata.")
    else:
        columns = [
            "Broker_Period", "Signals", "Triggered", "Trigger_Rate_Pct", "Closed",
            "Win", "Loss", "Win_Rate_Pct", "Avg_Return_Pct", "Avg_MFE_Pct", "Avg_MAE_Pct",
        ]
        print(period[columns].to_string(index=False, formatters={col: fmt for col in columns}))

    print("\nBROKER CONFIDENCE x PERIOD")
    print("=" * 88)
    if cross.empty:
        print("Belum ada data.")
    else:
        columns = [
            "Broker_Period", "Broker_Confidence_Bucket", "Signals", "Triggered",
            "Trigger_Rate_Pct", "Closed", "Win", "Loss", "Win_Rate_Pct",
            "Avg_Return_Pct", "Avg_MFE_Pct", "Avg_MAE_Pct",
        ]
        print(cross[columns].to_string(index=False, formatters={col: fmt for col in columns}))


def main() -> int:
    parser = argparse.ArgumentParser(description="Broker Period performance extension")
    sub = parser.add_subparsers(dest="command", required=True)

    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    capture_parser.add_argument("--run-id", required=True)
    capture_parser.add_argument("--snapshot-manifest", type=Path, required=True)

    show_parser = sub.add_parser("show")
    show_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    show_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)

    args = parser.parse_args()
    if args.command == "capture":
        rc = capture(args.db, args.run_id, args.snapshot_manifest)
        if rc == 0:
            period, cross, coverage = build_reports(args.db, DEFAULT_OUTPUT)
            print_report(period, cross, coverage)
        return rc
    period, cross, coverage = build_reports(args.db, args.output_dir)
    print_report(period, cross, coverage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
