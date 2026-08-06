from __future__ import annotations

"""Apply non-scoring exchange flags to decision artifacts.

The decision engine remains responsible for technical/broker scoring.  This
post-processing step only enforces explicit exchange vetoes and carries UMA /
RELISTING risk flags into the canonical decision CSV and reports.
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd


def load_exchange_status(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    rows = payload.get("symbols", {}) if isinstance(payload, dict) else {}
    return rows if isinstance(rows, dict) else {}


def _canonical(value: Any) -> str:
    return str(value or "").strip().upper().replace(".JK", "")


def _decision_columns(frame: pd.DataFrame) -> list[str]:
    return [
        column for column in ("Decision_Status_Final", "Decision_Status", "Decision_V3", "Decision")
        if column in frame.columns
    ]


def _downgrade_uma(value: Any) -> str:
    text = str(value or "").strip().upper().replace("_", " ")
    if "BUY READY" in text or text in {"BUY", "STRONG BUY", "BUY CONFIRMED"}:
        return "BUY CANDIDATE"
    return str(value)


def apply_exchange_status_to_decisions(
    decision_path: str | Path,
    enrichment_path: str | Path | None,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Attach flags and enforce SUSPENDED/RELISTING-history vetoes."""
    source = Path(decision_path)
    if not source.exists() or source.stat().st_size == 0:
        return {"status": "NO_DECISION_FILE", "suspended_count": 0, "uma_count": 0, "relisting_blocked_count": 0}
    frame = pd.read_csv(source, low_memory=False)
    states = load_exchange_status(enrichment_path)
    if frame.empty or not states:
        return {"status": "NO_EXCHANGE_STATUS", "suspended_count": 0, "uma_count": 0, "relisting_blocked_count": 0}
    symbol_col = next((column for column in ("Symbol", "SYMBOL", "Ticker", "EMITEN") if column in frame.columns), None)
    if symbol_col is None:
        return {"status": "SYMBOL_COLUMN_MISSING", "suspended_count": 0, "uma_count": 0, "relisting_blocked_count": 0}

    for column, default in (
        ("Exchange_Status", "NORMAL"),
        ("Risk_Flags", ""),
        ("Exchange_Veto", ""),
        ("Veto", ""),
        ("Veto_Reason", ""),
        ("Exchange_History_Candles", 0),
    ):
        if column not in frame.columns:
            frame[column] = default

    suspended_count = uma_count = relisting_blocked_count = 0
    columns = _decision_columns(frame)
    normalized_states = {
        _canonical(symbol): state
        for symbol, state in states.items()
        if _canonical(symbol) and isinstance(state, dict)
    }
    for index, value in frame[symbol_col].items():
        state = normalized_states.get(_canonical(value), {}) or {}
        if not state:
            continue
        status = str(state.get("status") or "NORMAL").upper()
        raw_flags = state.get("risk_flags") or []
        if isinstance(raw_flags, str):
            raw_flags = [item.strip() for item in raw_flags.split(",") if item.strip()]
        flags = sorted(set(str(item).upper() for item in raw_flags))
        veto = str(state.get("veto") or "").upper()
        if status == "SUSPENDED":
            suspended_count += 1
            veto = "SUSPENDED"
            frame.at[index, "Veto_Reason"] = "SUSPENDED"
            for column in columns:
                frame.at[index, column] = "BLOCKED"
        elif "RELISTING" in flags and veto == "RELISTING_HISTORY_INSUFFICIENT":
            relisting_blocked_count += 1
            frame.at[index, "Veto_Reason"] = "RELISTING_HISTORY_INSUFFICIENT"
            for column in columns:
                frame.at[index, column] = "BLOCKED"
        elif "UMA" in flags:
            uma_count += 1
            for column in columns:
                frame.at[index, column] = _downgrade_uma(frame.at[index, column])
        frame.at[index, "Exchange_Status"] = status
        frame.at[index, "Risk_Flags"] = ",".join(flags)
        frame.at[index, "Exchange_Veto"] = veto
        frame.at[index, "Veto"] = veto
        try:
            history_candles = int(state.get("history_candle_count") or 0)
        except (TypeError, ValueError):
            history_candles = 0
        frame.at[index, "Exchange_History_Candles"] = history_candles

    target = Path(output_path) if output_path else source
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False, encoding="utf-8-sig")
    return {
        "status": "APPLIED",
        "path": str(target),
        "suspended_count": suspended_count,
        "uma_count": uma_count,
        "relisting_blocked_count": relisting_blocked_count,
    }


__all__ = ["apply_exchange_status_to_decisions", "load_exchange_status"]
