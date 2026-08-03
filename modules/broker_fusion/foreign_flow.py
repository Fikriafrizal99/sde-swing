#!/usr/bin/env python3
"""Independent foreign/domestic flow telemetry from Stockbit broker raw rows.

The aggregate Broker Summary already mixes foreign and domestic brokers.  This
module exposes both dimensions separately so the decision engine can avoid
counting the same aggregate net flow twice.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd


def _symbol(value: object) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def aggregate_foreign_flow(source: Path | str | pd.DataFrame | None) -> pd.DataFrame:
    columns = [
        "Symbol", "Foreign_Net_Value", "Foreign_Gross_Value", "Foreign_Net_Pct",
        "Foreign_Score", "Foreign_Confidence", "Foreign_Direction",
        "Domestic_Net_Value", "Domestic_Gross_Value", "Domestic_Net_Pct",
        "Domestic_Flow_Score", "Foreign_Participation",
    ]
    if source is None:
        return pd.DataFrame(columns=columns)
    if isinstance(source, pd.DataFrame):
        raw = source.copy()
    else:
        path = Path(source)
        if not path.exists() or path.stat().st_size == 0:
            return pd.DataFrame(columns=columns)
        raw = pd.read_csv(path, low_memory=False)
    required = {"SYMBOL", "BROKER_TYPE", "NET_VALUE"}
    if not required.issubset(set(raw.columns)):
        return pd.DataFrame(columns=columns)

    raw["Symbol"] = raw["SYMBOL"].map(_symbol)
    raw["Broker_Type_Normalized"] = raw["BROKER_TYPE"].astype(str).str.upper().str.strip()
    raw["NET_VALUE"] = pd.to_numeric(raw["NET_VALUE"], errors="coerce").fillna(0.0)
    raw["ABS_NET_VALUE"] = raw["NET_VALUE"].abs()
    raw = raw[raw["Symbol"].ne("")]

    records: list[dict[str, object]] = []
    for symbol, group in raw.groupby("Symbol", sort=False):
        foreign = group[group["Broker_Type_Normalized"].eq("ASING")]
        domestic = group[~group["Broker_Type_Normalized"].eq("ASING")]
        foreign_net = float(foreign["NET_VALUE"].sum())
        foreign_gross = float(foreign["ABS_NET_VALUE"].sum())
        domestic_net = float(domestic["NET_VALUE"].sum())
        domestic_gross = float(domestic["ABS_NET_VALUE"].sum())
        total_gross = foreign_gross + domestic_gross
        foreign_pct = 100.0 * foreign_net / foreign_gross if foreign_gross else 0.0
        domestic_pct = 100.0 * domestic_net / domestic_gross if domestic_gross else 0.0
        participation = foreign_gross / total_gross if total_gross else 0.0
        foreign_score = float(np.clip(50.0 + 3.0 * foreign_pct, 0.0, 100.0))
        domestic_score = float(np.clip(50.0 + 3.0 * domestic_pct, 0.0, 100.0))
        confidence = float(np.clip(25.0 + 50.0 * participation + 1.25 * min(abs(foreign_pct), 20.0), 0.0, 100.0))
        direction = "POSITIVE" if foreign_pct >= 1.0 else "NEGATIVE" if foreign_pct <= -1.0 else "NEUTRAL"
        records.append({
            "Symbol": symbol,
            "Foreign_Net_Value": foreign_net,
            "Foreign_Gross_Value": foreign_gross,
            "Foreign_Net_Pct": round(foreign_pct, 4),
            "Foreign_Score": round(foreign_score, 2),
            "Foreign_Confidence": round(confidence, 2),
            "Foreign_Direction": direction,
            "Domestic_Net_Value": domestic_net,
            "Domestic_Gross_Value": domestic_gross,
            "Domestic_Net_Pct": round(domestic_pct, 4),
            "Domestic_Flow_Score": round(domestic_score, 2),
            "Foreign_Participation": round(participation, 4),
        })
    return pd.DataFrame(records, columns=columns)


def attach_foreign_flow(frame: pd.DataFrame, source: Path | str | pd.DataFrame | None) -> pd.DataFrame:
    out = frame.copy()
    foreign = aggregate_foreign_flow(source)
    if not foreign.empty:
        out = out.merge(foreign, on="Symbol", how="left")
    defaults = {
        "Foreign_Net_Value": 0.0,
        "Foreign_Gross_Value": 0.0,
        "Foreign_Net_Pct": 0.0,
        "Foreign_Score": 50.0,
        "Foreign_Confidence": 0.0,
        "Foreign_Direction": "NO DATA",
        "Domestic_Net_Value": 0.0,
        "Domestic_Gross_Value": 0.0,
        "Domestic_Net_Pct": 0.0,
        "Domestic_Flow_Score": 50.0,
        "Foreign_Participation": 0.0,
    }
    for column, default in defaults.items():
        if column not in out.columns:
            out[column] = default
        out[column] = out[column].fillna(default)
    return out
