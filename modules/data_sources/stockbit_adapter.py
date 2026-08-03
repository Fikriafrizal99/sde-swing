from __future__ import annotations

"""Stockbit adapter — refactors existing broker raw parsing into canonical BrokerFlow.

This wraps the Stage-1 broker raw parser (``modules.broker_bridge.broker_raw``)
so its number parsing, exact market date, and Top-40 coverage are preserved
unchanged.  The adapter only re-shapes the normalized frame into canonical
``BrokerFlow`` and ``ForeignFlow`` records; it makes no trading decisions.

Duplicate and symbol-mismatch detection is delegated to the data quality
engine via the canonical dedup key; this adapter surfaces broker_type so the
foreign/domestic split (and double-count protection) works downstream.
"""

from pathlib import Path
from typing import Any

import pandas as pd

from modules.broker_bridge.broker_raw import (
    normalize_broker_raw_frame,
    read_normalized_broker_raw,
)
from modules.data_sources.base import Adapter
from modules.data_sources.canonical import (
    BrokerFlow,
    CanonicalRecord,
    ForeignFlow,
    compute_payload_hash,
    now_wib,
)


def _broker_type(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text == "ASING":
        return "ASING"
    if text in {"", "NAN", "NONE"}:
        return "UNKNOWN"
    return "DOMESTIC"


class StockbitAdapter(Adapter):
    source_name = "STOCKBIT"

    def to_canonical(
        self,
        record_type: str,
        raw: Any,
        *,
        market_date: str | None = None,
        **kwargs: Any,
    ) -> list[CanonicalRecord]:
        frame = self._as_frame(raw)
        if frame is None or frame.empty:
            return []
        if record_type == "BrokerFlow":
            return self._broker_flows(frame, market_date)
        if record_type == "ForeignFlow":
            return self._foreign_flows(frame, market_date)
        return []

    # -- helpers -----------------------------------------------------------
    def _as_frame(self, raw: Any) -> pd.DataFrame | None:
        if isinstance(raw, pd.DataFrame):
            # Normalize only if it is not already canonical broker-raw.
            if {"SYMBOL", "BROKER_CODE", "SIDE"}.issubset(raw.columns):
                return raw.copy()
            return normalize_broker_raw_frame(raw)
        if isinstance(raw, (str, Path)):
            return read_normalized_broker_raw(Path(raw))
        return None

    def _broker_flows(self, frame: pd.DataFrame, market_date: str | None) -> list[BrokerFlow]:
        now = now_wib()
        received = now.isoformat()
        records: list[BrokerFlow] = []
        for _, row in frame.iterrows():
            md = market_date or str(row.get("TO_DATE") or "")
            rec = BrokerFlow(
                symbol=str(row.get("SYMBOL", "")).strip().upper(),
                market_date=md[:10],
                event_timestamp=received,
                received_at=received,
                source="STOCKBIT",
                source_record_id=f"{row.get('SYMBOL','')}:{row.get('SIDE','')}:{row.get('BROKER_CODE','')}",
                raw_payload_hash=compute_payload_hash(row.to_dict()),
                broker_code=str(row.get("BROKER_CODE", "")).strip().upper(),
                broker_type=_broker_type(row.get("BROKER_TYPE")),
                side=str(row.get("SIDE", "")).strip().upper(),
                rank=_f(row.get("RANK")),
                net_value=_f(row.get("NET_VALUE")),
                net_lot=_f(row.get("NET_LOT")),
                gross_value=_f(row.get("GROSS_VALUE")),
                gross_lot=_f(row.get("GROSS_LOT")),
                frequency=_f(row.get("FREQUENCY")),
                avg_price=_f(row.get("AVG_PRICE")),
            )
            records.append(rec)
        return records

    def _foreign_flows(self, frame: pd.DataFrame, market_date: str | None) -> list[ForeignFlow]:
        """Aggregate foreign flow per symbol from the ASING broker rows.

        flow_origin is DERIVED_FROM_BROKER so the double-count guard downstream
        knows this must not be summed with an aggregate foreign feed.
        """
        now = now_wib()
        received = now.isoformat()
        work = frame.copy()
        work["__type"] = work["BROKER_TYPE"].map(_broker_type)
        work["__net"] = pd.to_numeric(work["NET_VALUE"], errors="coerce").fillna(0.0)
        work["Symbol"] = work["SYMBOL"].astype(str).str.strip().str.upper()
        records: list[ForeignFlow] = []
        for symbol, group in work.groupby("Symbol", sort=False):
            if not symbol:
                continue
            foreign = group[group["__type"].eq("ASING")]
            foreign_net = float(foreign["__net"].sum())
            foreign_gross = float(foreign["__net"].abs().sum())
            total_gross = float(group["__net"].abs().sum())
            md = market_date or str(group["TO_DATE"].iloc[0] if "TO_DATE" in group else "")
            records.append(ForeignFlow(
                symbol=symbol,
                market_date=md[:10],
                event_timestamp=received,
                received_at=received,
                source="STOCKBIT",
                source_record_id=f"FOREIGN:{symbol}",
                raw_payload_hash=compute_payload_hash({"symbol": symbol, "net": foreign_net}),
                foreign_net_value=foreign_net,
                foreign_gross_value=foreign_gross,
                foreign_net_pct=round(100.0 * foreign_net / foreign_gross, 4) if foreign_gross else 0.0,
                foreign_participation=round(foreign_gross / total_gross, 4) if total_gross else 0.0,
                flow_origin="DERIVED_FROM_BROKER",
            ))
        return records


def _f(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None