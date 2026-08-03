#!/usr/bin/env python3
"""Fuse technical candidates and Stockbit broker summary into FINAL_DECISION_V2.csv.

This module replaces the previously missing bridge between Candidate Selector and
Decision Engine V1.2. It accepts either an explicit broker summary CSV or a
folder containing Stockbit broker exports. When a folder is supplied, the newest
CSV with the required summary columns is selected automatically.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from swing_utils import file_sha256, make_run_id, write_json
from modules.broker_fusion.foreign_flow import attach_foreign_flow

REQUIRED_BROKER_COLUMNS = {
    "TOTAL_BUY",
    "TOTAL_SELL",
    "NET_FLOW",
    "BUYER_CONCENTRATION",
    "SELLER_CONCENTRATION",
}
SYMBOL_ALIASES = ("Symbol", "SYMBOL", "EMITEN", "Ticker", "TICKER", "Code")


def normalize_name(value: object) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return re.sub(r"_+", "_", text).strip("_")


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper().replace(".JK", "")
    first = text.splitlines()[0].split()[0] if text else ""
    match = re.search(r"[A-Z0-9]{2,12}", first)
    return match.group(0) if match else ""


def find_column(df: pd.DataFrame, aliases: Iterable[str]) -> str | None:
    mapping = {normalize_name(col): col for col in df.columns}
    for alias in aliases:
        key = normalize_name(alias)
        if key in mapping:
            return mapping[key]
    return None


def numeric(series: pd.Series | object, default: float = 0.0) -> pd.Series:
    if isinstance(series, pd.Series):
        cleaned = (
            series.astype(str)
            .str.replace("Rp", "", regex=False)
            .str.replace("IDR", "", regex=False)
            .str.replace("%", "", regex=False)
            .str.replace(",", "", regex=False)
            .str.strip()
        )
        return pd.to_numeric(cleaned, errors="coerce").fillna(default)
    return pd.Series(dtype=float)


def safe_ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a / b.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def newest_candidate_file(folder: Path) -> Path:
    priorities = (
        "technical_candidates_top40.csv",
        "technical_candidates_top50.csv",
        "technical_candidates_top30.csv",
        "technical_candidates_top20.csv",
        "technical_candidates_top10.csv",
        "broker_symbols.csv",
    )
    for name in priorities:
        path = folder / name
        if path.exists() and path.stat().st_size > 0:
            return path
    files = sorted(
        (p for p in folder.glob("technical_candidates_top*.csv") if p.stat().st_size > 0),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if files:
        return files[0]
    raise FileNotFoundError(f"File kandidat teknikal tidak ditemukan di: {folder}")


def is_broker_summary(path: Path) -> bool:
    try:
        columns = set(pd.read_csv(path, nrows=0).columns)
    except Exception:
        return False
    normalized = {str(c).strip().upper() for c in columns}
    has_symbol = bool(normalized.intersection({"EMITEN", "SYMBOL", "TICKER", "CODE"}))
    return has_symbol and REQUIRED_BROKER_COLUMNS.issubset(normalized)


def newest_broker_file(source: Path) -> Path:
    if source.is_file():
        if not is_broker_summary(source):
            raise ValueError(f"CSV bukan Broker Summary yang valid: {source}")
        return source
    if not source.is_dir():
        raise FileNotFoundError(f"Sumber broker tidak ditemukan: {source}")
    files = [p for p in source.rglob("*.csv") if p.stat().st_size > 0 and is_broker_summary(p)]
    if not files:
        raise FileNotFoundError(
            f"Broker Summary tidak ditemukan di {source}. "
            "File wajib memiliki EMITEN/SYMBOL, TOTAL_BUY, TOTAL_SELL, NET_FLOW, "
            "BUYER_CONCENTRATION, dan SELLER_CONCENTRATION."
        )
    return max(files, key=lambda p: p.stat().st_mtime)


def accumulation_points(value: object) -> int:
    text = str(value or "").strip().upper()
    if "BIG ACC" in text or "STRONG ACC" in text:
        return 10
    if "SMALL ACC" in text:
        return 5
    if text in {"ACC", "ACCUMULATION"} or ("ACC" in text and "DIST" not in text):
        return 8
    if "BIG DIST" in text or "STRONG DIST" in text:
        return -10
    if "SMALL DIST" in text:
        return -5
    if "DIST" in text:
        return -8
    return 0


def broker_score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Build transparent single-session broker direction and confidence metrics.

    The previous score could label a row STRONG ACCUMULATION even when net flow
    was materially negative because three pattern columns dominated the total.
    V1.6 keeps every component visible, measures agreement, and reduces
    confidence when flow, concentration, and Acc/Dist pattern disagree.
    """
    out = df.copy()
    for col in ["TOTAL_BUY", "TOTAL_SELL", "NET_FLOW", "TOTAL_VALUE", "TOTAL_VOLUME"]:
        if col not in out:
            out[col] = 0.0
        out[col] = numeric(out[col])
    for col in ["BUYER_CONCENTRATION", "SELLER_CONCENTRATION"]:
        if col not in out:
            out[col] = 0.0
        out[col] = numeric(out[col])
        out.loc[out[col].abs() > 1.5, col] = out.loc[out[col].abs() > 1.5, col] / 100.0

    gross = (out["TOTAL_BUY"].abs() + out["TOTAL_SELL"].abs()).replace(0, np.nan)
    total_value = out["TOTAL_VALUE"].abs().replace(0, np.nan)
    out["Net_Flow_Gross_Pct"] = (safe_ratio(out["NET_FLOW"], gross) * 100).round(4)
    out["Net_Flow_Value_Pct"] = (safe_ratio(out["NET_FLOW"], total_value) * 100).round(4)
    out["Concentration_Delta"] = (
        out["BUYER_CONCENTRATION"] - out["SELLER_CONCENTRATION"]
    ).round(4)

    signal_cols = ["BROKER_ACCDIST", "AVG_ACCDIST", "TOP3_ACCDIST"]
    for col in signal_cols:
        if col not in out:
            out[col] = ""

    pattern_points = pd.Series(0.0, index=out.index)
    for col in signal_cols:
        pattern_points += out[col].map(accumulation_points)

    nf = out["Net_Flow_Gross_Pct"]
    cd = out["Concentration_Delta"]
    flow_component = (nf / 5.0).clip(-1, 1) * 40.0
    concentration_component = (cd / 0.20).clip(-1, 1) * 25.0
    pattern_component = (pattern_points / 30.0).clip(-1, 1) * 35.0
    direction_score = (flow_component + concentration_component + pattern_component).clip(-100, 100)

    flow_sign = np.select([nf >= 0.5, nf <= -0.5], [1, -1], default=0)
    concentration_sign = np.select([cd >= 0.03, cd <= -0.03], [1, -1], default=0)
    pattern_sign = np.select([pattern_points >= 5, pattern_points <= -5], [1, -1], default=0)
    overall_sign = np.select([direction_score >= 15, direction_score <= -15], [1, -1], default=0)

    signs = np.vstack([flow_sign, concentration_sign, pattern_sign]).T
    nonzero = (signs != 0).sum(axis=1)
    agreement = np.zeros(len(out), dtype=float)
    divergence = np.zeros(len(out), dtype=bool)
    for idx, values in enumerate(signs):
        target = int(overall_sign[idx])
        nz = values[values != 0]
        agreement[idx] = float((nz == target).sum() / len(nz)) if len(nz) and target else 0.0
        divergence[idx] = bool((values > 0).any() and (values < 0).any())

    confidence = 30.0 + agreement * 35.0 + np.minimum(np.abs(direction_score), 100) * 0.30
    confidence -= np.where(nonzero < 2, 10.0, 0.0)
    confidence -= np.where(divergence, 20.0, 0.0)
    confidence = np.where(overall_sign == 0, np.minimum(confidence, 55.0), confidence)
    confidence = np.clip(confidence, 0, 100)

    out["Broker_Flow_Component"] = flow_component.round(2)
    out["Broker_Concentration_Component"] = concentration_component.round(2)
    out["Broker_Pattern_Component"] = pattern_component.round(2)
    out["Broker_Pattern_Points"] = pattern_points.round(2)
    out["Broker_Direction_Score"] = direction_score.round(2)
    out["Broker_Confidence"] = confidence.round(2)
    out["Broker_Divergence"] = divergence
    out["Broker_Direction"] = np.select(
        [overall_sign > 0, overall_sign < 0],
        ["ACCUMULATION", "DISTRIBUTION"],
        default="NEUTRAL",
    )
    out["Broker_Strength"] = np.select(
        [np.abs(direction_score) >= 60, np.abs(direction_score) >= 35],
        ["STRONG", "MODERATE"],
        default="WEAK",
    )
    out["Broker_Score"] = (50.0 + direction_score / 2.0).clip(0, 100).round(2)
    out["Broker_Confirmation"] = np.select(
        [
            (overall_sign > 0) & (np.abs(direction_score) >= 60) & (confidence >= 65),
            (overall_sign > 0) & (confidence >= 45),
            (overall_sign < 0) & (np.abs(direction_score) >= 60) & (confidence >= 65),
            (overall_sign < 0) & (confidence >= 45),
        ],
        ["STRONG ACCUMULATION", "ACCUMULATION", "STRONG DISTRIBUTION", "DISTRIBUTION"],
        default="NEUTRAL",
    )

    reasons: list[str] = []
    warnings: list[str] = []
    for _, row in out.iterrows():
        tags: list[str] = []
        warns: list[str] = []
        if row["Net_Flow_Gross_Pct"] >= 0.5:
            tags.append("net flow positif")
        elif row["Net_Flow_Gross_Pct"] <= -0.5:
            warns.append("net flow negatif")
        if row["Concentration_Delta"] >= 0.05:
            tags.append("buyer concentration dominan")
        elif row["Concentration_Delta"] <= -0.05:
            warns.append("seller concentration dominan")
        pattern_text = []
        for col in signal_cols:
            value = str(row.get(col, "")).strip()
            if value and value.lower() not in {"nan", "neutral"}:
                pattern_text.append(value)
        if pattern_text:
            tags.extend(dict.fromkeys(pattern_text))
        if bool(row["Broker_Divergence"]):
            warns.append("flow, concentration, dan pattern tidak searah")
        tags.append(f"confidence {row['Broker_Confidence']:.0f}%")
        reasons.append("; ".join(dict.fromkeys(tags)) or "broker signal netral")
        warnings.append("; ".join(dict.fromkeys(warns)))
    out["Broker_Reasons"] = reasons
    out["Broker_Warnings"] = warnings
    return out


def grade(score: float) -> str:
    if score >= 85:
        return "★★★★★"
    if score >= 75:
        return "★★★★"
    if score >= 65:
        return "★★★"
    if score >= 50:
        return "★★"
    return "★"


def scoring_facts(row: pd.Series) -> tuple[float, float, str, str]:
    """Return confirmation bonus/penalty facts without assigning a decision."""
    tech = float(row.get("Technical_Quality_Score", row.get("Technical_Score_Final", 0.0)) or 0.0)
    confidence = float(row.get("Broker_Confidence", 0.0) or 0.0)
    direction_score = float(row.get("Broker_Direction_Score", 0.0) or 0.0)
    direction = str(row.get("Broker_Direction", "NEUTRAL") or "NEUTRAL").upper()
    confirmation = str(row.get("Broker_Confirmation", "NO DATA") or "NO DATA").upper()
    divergence = bool(row.get("Broker_Divergence", False))
    available = bool(row.get("Broker_Data_Available", False))

    if confirmation == "STRONG DISTRIBUTION" or (direction == "DISTRIBUTION" and confidence >= 70 and direction_score <= -60):
        context, blocker = "STRONG_DISTRIBUTION", "STRONG_BROKER_DISTRIBUTION"
        bonus, penalty = 0.0, 20.0
    elif direction == "DISTRIBUTION" or confirmation == "DISTRIBUTION":
        context, blocker = "DISTRIBUTION", ""
        bonus, penalty = 0.0, 6.0
    elif divergence:
        context, blocker = "DIVERGENCE", ""
        bonus, penalty = 0.0, 3.0
    elif confirmation == "STRONG ACCUMULATION" or (direction == "ACCUMULATION" and confidence >= 65 and direction_score >= 60):
        context, blocker = "STRONG_ACCUMULATION", ""
        bonus, penalty = 4.0 if tech >= 70 else 3.0, 0.0
    elif direction == "ACCUMULATION" or confirmation == "ACCUMULATION":
        context, blocker = "ACCUMULATION", ""
        bonus, penalty = 2.0, 0.0
    else:
        context, blocker = "NEUTRAL", ""
        bonus, penalty = 0.0, 0.0
    if not available:
        penalty += 5.0
    return bonus, penalty, context, blocker

def fuse(
    technical_path: Path,
    broker_path: Path,
    output_path: Path,
    run_id: str = "",
    data_quality_status: str = "VALID",
    manifest_dir: Path | None = None,
    min_coverage: float = 0.80,
    expected_broker_date: str = "",
    allow_partial_broker: bool = False,
    allow_date_mismatch: bool = False,
    broker_raw_path: Path | None = None,
) -> pd.DataFrame:
    technical = pd.read_csv(technical_path, low_memory=False)
    if technical.empty:
        raise ValueError(f"File kandidat teknikal kosong: {technical_path}")
    technical_symbol = find_column(technical, SYMBOL_ALIASES)
    if technical_symbol is None:
        raise ValueError("Kolom Symbol/EMITEN/Ticker tidak ditemukan pada kandidat teknikal")
    technical["Symbol"] = technical[technical_symbol].map(normalize_symbol)
    technical = technical[technical["Symbol"].ne("")].drop_duplicates("Symbol", keep="first")

    tech_score_col = find_column(technical, ("Technical_Quality_Score", "Technical_Score_Final", "Technical_Score", "Technical_Score_V2", "Score"))
    if tech_score_col is None:
        raise ValueError("Kolom Technical_Score tidak ditemukan pada kandidat teknikal")
    technical["Technical_Score_Final"] = numeric(technical[tech_score_col])

    broker = pd.read_csv(broker_path, low_memory=False)
    broker_symbol = find_column(broker, SYMBOL_ALIASES)
    if broker_symbol is None:
        raise ValueError("Kolom EMITEN/SYMBOL/Ticker tidak ditemukan pada Broker Summary")
    broker["Symbol"] = broker[broker_symbol].map(normalize_symbol)
    raw_broker_symbols = broker["Symbol"].copy()
    duplicate_symbols = sorted(raw_broker_symbols[raw_broker_symbols.duplicated()].dropna().unique().tolist())
    broker = broker[broker["Symbol"].ne("")].drop_duplicates("Symbol", keep="last")

    expected_symbols = set(technical["Symbol"])
    broker_symbols = set(broker["Symbol"])
    matched_symbols = expected_symbols & broker_symbols
    coverage = len(matched_symbols) / max(1, len(expected_symbols))
    missing_symbols = sorted(expected_symbols - broker_symbols)
    unexpected_symbols = sorted(broker_symbols - expected_symbols)
    if coverage < min_coverage and not allow_partial_broker:
        raise RuntimeError(
            f"Broker coverage {len(matched_symbols)}/{len(expected_symbols)} ({coverage:.0%}) "
            f"di bawah minimum {min_coverage:.0%}. Missing: {', '.join(missing_symbols) or '-'}"
        )

    broker_date = ""
    broker_date_col = find_column(broker, ("TO_DATE", "Broker_Data_Date", "TO_DATE_BROKER"))
    if broker_date_col is not None:
        parsed_broker_dates = pd.to_datetime(broker[broker_date_col], errors="coerce").dropna()
        if not parsed_broker_dates.empty:
            broker_date = parsed_broker_dates.max().date().isoformat()
    if expected_broker_date and broker_date != expected_broker_date and not allow_date_mismatch:
        raise RuntimeError(
            f"Broker date mismatch: expected {expected_broker_date}, detected {broker_date or 'UNKNOWN'}"
        )

    effective_quality = str(data_quality_status or "VALID").upper()
    if coverage < 1.0 and effective_quality == "VALID":
        effective_quality = "PARTIAL_COVERAGE"
    if expected_broker_date and broker_date != expected_broker_date and allow_date_mismatch:
        effective_quality = "BROKER_DATE_OVERRIDE"

    broker = broker_score_frame(broker)
    broker = attach_foreign_flow(broker, broker_raw_path)

    duplicate_cols = [c for c in broker.columns if c in technical.columns and c != "Symbol"]
    broker = broker.rename(columns={c: f"{c}_BROKER" for c in duplicate_cols})
    # Restore scoring field names after collision-safe rename.
    rename_back = {}
    for col in [
        "Broker_Score",
        "Broker_Confirmation",
        "Broker_Reasons",
        "Broker_Warnings",
        "Net_Flow_Gross_Pct",
        "Net_Flow_Value_Pct",
        "Concentration_Delta",
        "Broker_Flow_Component", "Broker_Concentration_Component", "Broker_Pattern_Component",
        "Broker_Pattern_Points", "Broker_Direction_Score", "Broker_Confidence",
        "Broker_Divergence", "Broker_Direction", "Broker_Strength",
    ]:
        if f"{col}_BROKER" in broker.columns:
            rename_back[f"{col}_BROKER"] = col
    broker = broker.rename(columns=rename_back)

    merged = technical.merge(broker, how="left", on="Symbol", indicator=True)
    merged["Broker_Data_Available"] = merged["_merge"].eq("both")
    merged = merged.drop(columns=["_merge"])
    for col, default in {
        "Broker_Score": 0.0,
        "Broker_Confirmation": "NO DATA",
        "Broker_Direction": "NEUTRAL",
        "Broker_Strength": "WEAK",
        "Broker_Confidence": 0.0,
        "Broker_Divergence": False,
        "Broker_Direction_Score": 0.0,
        "Broker_Reasons": "broker data tidak tersedia",
        "Broker_Warnings": "symbol tidak ditemukan pada Broker Summary",
    }.items():
        if col not in merged:
            merged[col] = default
        merged[col] = merged[col].fillna(default)

    facts = merged.apply(scoring_facts, axis=1, result_type="expand")
    facts.columns = ["Synergy_Bonus", "Risk_Penalty", "Broker_Context", "Broker_Hard_Blocker"]
    merged = pd.concat([merged, facts], axis=1)
    merged["Broker_Confirmation_Score"] = (
        merged["Broker_Score"].astype(float) + merged["Synergy_Bonus"] - merged["Risk_Penalty"]
    ).clip(0, 100).round(2)
    merged["Technical_Grade"] = merged["Technical_Score_Final"].map(grade)
    merged["Broker_Grade"] = merged["Broker_Score"].map(grade)
    merged["Decision_Reasons"] = (
        merged.get("Candidate_Reason", pd.Series("", index=merged.index)).fillna("").astype(str).str.strip()
        + "; "
        + merged["Broker_Reasons"].fillna("").astype(str).str.strip()
    ).str.strip("; ")
    merged["Warnings"] = merged["Broker_Warnings"].fillna("")
    if run_id:
        merged["Run_ID"] = run_id
    merged["Data_Quality_Status"] = effective_quality

    merged = merged.sort_values(
        ["Technical_Score_Final", "Broker_Confirmation_Score"], ascending=[False, False]
    )
    merged.insert(0, "Rank", range(1, len(merged) + 1))

    lead = [
        "Rank", "Symbol", "Technical_Score_Final", "Technical_Grade", "Broker_Score",
        "Broker_Confirmation_Score", "Broker_Grade", "Broker_Context", "Broker_Hard_Blocker",
        "Broker_Confirmation", "Broker_Direction", "Broker_Strength", "Broker_Confidence",
        "Broker_Divergence", "Synergy_Bonus", "Risk_Penalty", "Decision_Reasons", "Warnings",
        "Broker_Data_Available",
    ]
    columns = lead + [c for c in merged.columns if c not in lead]
    merged = merged[columns]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(output_path, index=False, encoding="utf-8-sig")
    manifest = {
        "Run_ID": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "technical_source": str(technical_path.resolve()),
        "technical_source_hash": file_sha256(technical_path),
        "broker_source": str(broker_path.resolve()),
        "broker_source_hash": file_sha256(broker_path),
        "broker_raw_source": str(broker_raw_path.resolve()) if broker_raw_path and broker_raw_path.exists() else "",
        "broker_raw_source_hash": file_sha256(broker_raw_path),
        "foreign_flow_available": bool(broker_raw_path and broker_raw_path.exists()),
        "output": str(output_path.resolve()),
        "output_hash": file_sha256(output_path),
        "technical_rows": int(len(technical)),
        "broker_rows": int(len(broker)),
        "fused_rows": int(len(merged)),
        "broker_matched": int(merged["Broker_Data_Available"].sum()),
        "broker_expected": int(len(expected_symbols)),
        "broker_coverage": coverage,
        "broker_date": broker_date,
        "expected_broker_date": expected_broker_date,
        "missing_symbols": missing_symbols,
        "unexpected_symbols": unexpected_symbols,
        "duplicate_symbols": duplicate_symbols,
        "allow_partial_broker": allow_partial_broker,
        "allow_date_mismatch": allow_date_mismatch,
        "Data_Quality_Status": effective_quality,
    }
    write_json(output_path.with_suffix(".manifest.json"), manifest)
    if manifest_dir and run_id:
        write_json(manifest_dir / f"BROKER_FUSION_MANIFEST_{run_id}.json", manifest)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="Fuse technical candidates and Broker Summary")
    parser.add_argument("technical", type=Path, help="Candidate CSV or candidate output folder")
    parser.add_argument("broker", type=Path, help="Broker Summary CSV or broker input folder")
    parser.add_argument("--output", "-o", type=Path, default=Path("data/input/FINAL_DECISION_V2.csv"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--manifest-dir", type=Path)
    parser.add_argument("--data-quality-status", default="VALID")
    parser.add_argument("--min-coverage", type=float, default=0.80)
    parser.add_argument("--expected-broker-date", default="")
    parser.add_argument("--allow-partial-broker", action="store_true")
    parser.add_argument("--allow-date-mismatch", action="store_true")
    parser.add_argument("--broker-raw", type=Path, default=None, help="Stockbit BROKER_RAW CSV for foreign/domestic flow")
    args = parser.parse_args()
    args.run_id = args.run_id or make_run_id()

    technical_path = newest_candidate_file(args.technical) if args.technical.is_dir() else args.technical
    broker_path = newest_broker_file(args.broker)
    result = fuse(
        technical_path,
        broker_path,
        args.output,
        args.run_id,
        args.data_quality_status,
        args.manifest_dir,
        min_coverage=args.min_coverage,
        expected_broker_date=args.expected_broker_date,
        allow_partial_broker=args.allow_partial_broker,
        allow_date_mismatch=args.allow_date_mismatch,
        broker_raw_path=args.broker_raw,
    )
    print(f"TECHNICAL : {technical_path}")
    print(f"BROKER    : {broker_path}")
    print(f"MATCHED   : {int(result['Broker_Data_Available'].sum())}/{len(result)}")
    print(f"OUTPUT    : {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
