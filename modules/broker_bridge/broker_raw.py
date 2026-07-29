from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from swing_utils import find_col, normalize_symbol


CANONICAL_COLUMNS = [
    "SYMBOL",
    "FROM_DATE",
    "TO_DATE",
    "SIDE",
    "RANK",
    "BROKER_CODE",
    "BROKER_TYPE",
    "NET_VALUE",
    "NET_LOT",
    "GROSS_VALUE",
    "GROSS_LOT",
    "FREQUENCY",
    "AVG_PRICE",
]

ALIASES: dict[str, tuple[str, ...]] = {
    "SYMBOL": ("SYMBOL", "EMITEN", "TICKER", "STOCK", "CODE"),
    "FROM_DATE": ("FROM_DATE", "START_DATE", "DATE_FROM", "TANGGAL_AWAL"),
    "TO_DATE": ("TO_DATE", "END_DATE", "DATE_TO", "DATE", "TANGGAL", "TANGGAL_AKHIR"),
    "SIDE": ("SIDE", "BUY_SELL", "TRANSACTION_SIDE", "TRADE_SIDE", "DIRECTION"),
    "RANK": ("RANK", "POSITION", "URUTAN", "NO", "NUMBER"),
    "BROKER_CODE": ("BROKER_CODE", "BROKER", "BROKER_ID", "BROKER_NAME", "CODE_BROKER"),
    "BROKER_TYPE": ("BROKER_TYPE", "INVESTOR_TYPE", "TYPE_BROKER", "CATEGORY"),
    "NET_VALUE": ("NET_VALUE", "VALUE", "AMOUNT", "NET_AMOUNT", "TRANSACTION_VALUE"),
    "NET_LOT": ("NET_LOT", "LOT", "NET_VOLUME_LOT", "VOLUME_LOT"),
    "GROSS_VALUE": ("GROSS_VALUE", "BUY_VALUE", "SELL_VALUE", "GROSS_AMOUNT"),
    "GROSS_LOT": ("GROSS_LOT", "BUY_LOT", "SELL_LOT", "TOTAL_LOT", "GROSS_VOLUME_LOT"),
    "FREQUENCY": ("FREQUENCY", "FREQ", "TRANSACTION_COUNT", "COUNT"),
    "AVG_PRICE": ("AVG_PRICE", "AVERAGE_PRICE", "AVG", "AVERAGE", "AVG_COST", "AVERAGE_COST"),
}


def _clean_numeric_value(value: Any) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if pd.notna(number) else None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "-"}:
        return None
    had_currency = bool(re.search(r"(?i)rp|idr", text))
    text = re.sub(r"(?i)rp|idr", "", text)
    text = re.sub(r"\s+", "", text)
    # Tampermonkey exports canonical numbers. These extra rules allow manual or
    # locale-formatted CSVs without changing the scoring data.
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        tail = text.rsplit(",", 1)[-1]
        text = text.replace(",", ".") if len(tail) <= 2 else text.replace(",", "")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif had_currency and "." in text and len(text.rsplit(".", 1)[-1]) == 3:
        text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


def _numeric_series(series: pd.Series) -> pd.Series:
    return series.map(_clean_numeric_value).astype(float)


def normalize_side(value: Any) -> str:
    text = str(value or "").strip().upper()
    buy_values = {"BUY", "B", "BUYER", "BELI", "PEMBELIAN", "ACCUMULATION", "AKUMULASI"}
    sell_values = {"SELL", "S", "SELLER", "JUAL", "PENJUALAN", "DISTRIBUTION", "DISTRIBUSI"}
    if text in buy_values or text.startswith("BUY"):
        return "BUY"
    if text in sell_values or text.startswith("SELL"):
        return "SELL"
    return text


def normalize_broker_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text or text == "NAN":
        return ""
    if re.fullmatch(r"[A-Z0-9]{1,8}", text):
        return text
    # Preserve unsafe/dynamic text so the Telegram layer can escape it instead
    # of silently truncating it. For ordinary decorated labels, take the code.
    if any(char in text for char in "<>&"):
        return text
    match = re.search(r"\b[A-Z0-9]{1,8}\b", text)
    return match.group(0) if match else text


def _derive_avg_price(frame: pd.DataFrame) -> pd.Series:
    avg = _numeric_series(frame["AVG_PRICE"])
    gross_value = _numeric_series(frame["GROSS_VALUE"]).abs()
    gross_lot = _numeric_series(frame["GROSS_LOT"]).abs()
    net_value = _numeric_series(frame["NET_VALUE"]).abs()
    net_lot = _numeric_series(frame["NET_LOT"]).abs()

    gross_derived = gross_value.div(gross_lot.mul(100).where(gross_lot > 0))
    net_derived = net_value.div(net_lot.mul(100).where(net_lot > 0))
    valid_avg = avg.where(avg > 0)
    return valid_avg.fillna(gross_derived.where(gross_derived > 0)).fillna(net_derived.where(net_derived > 0))


def normalize_broker_raw_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a canonical per-broker raw frame.

    This is presentation-only normalization. It never changes Broker Summary,
    Broker Score, Broker Fusion, or any trading decision.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    source = frame.copy()
    canonical = pd.DataFrame(index=source.index)
    for target, aliases in ALIASES.items():
        matched: list[str] = []
        for alias in aliases:
            col = find_col(source, alias)
            if col and col not in matched:
                matched.append(col)
        if not matched:
            canonical[target] = pd.NA
            continue
        combined = source[matched[0]].copy()
        for col in matched[1:]:
            combined = combined.combine_first(source[col])
        canonical[target] = combined

    canonical["SYMBOL"] = canonical["SYMBOL"].map(normalize_symbol)
    canonical["SIDE"] = canonical["SIDE"].map(normalize_side)
    canonical["BROKER_CODE"] = canonical["BROKER_CODE"].map(normalize_broker_code)
    canonical["BROKER_TYPE"] = canonical["BROKER_TYPE"].fillna("").astype(str).str.strip()

    for col in ("RANK", "NET_VALUE", "NET_LOT", "GROSS_VALUE", "GROSS_LOT", "FREQUENCY", "AVG_PRICE"):
        canonical[col] = _numeric_series(canonical[col])

    canonical["AVG_PRICE"] = _derive_avg_price(canonical)
    canonical["TO_DATE"] = pd.to_datetime(canonical["TO_DATE"], errors="coerce").dt.date.astype("string")
    canonical["FROM_DATE"] = pd.to_datetime(canonical["FROM_DATE"], errors="coerce").dt.date.astype("string")

    canonical = canonical[
        canonical["SYMBOL"].ne("")
        & canonical["BROKER_CODE"].ne("")
        & canonical["SIDE"].isin(["BUY", "SELL"])
    ].copy()
    if canonical.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    # Prefer valid rows when duplicate broker entries exist. This can happen
    # after repeated browser captures. We do not sum them because that would
    # double-count the same API snapshot.
    canonical["__has_avg"] = canonical["AVG_PRICE"].fillna(0).gt(0).astype(int)
    canonical["__abs_value"] = canonical["NET_VALUE"].fillna(canonical["GROSS_VALUE"]).abs().fillna(0)
    canonical["__rank_sort"] = canonical["RANK"].fillna(10_000)
    canonical = canonical.sort_values(
        ["SYMBOL", "SIDE", "BROKER_CODE", "__has_avg", "__abs_value", "__rank_sort"],
        ascending=[True, True, True, False, False, True],
    )
    canonical = canonical.drop_duplicates(["SYMBOL", "SIDE", "BROKER_CODE"], keep="first")

    # Recreate missing ranks per symbol and side from transaction value.
    canonical = canonical.sort_values(
        ["SYMBOL", "SIDE", "RANK", "__abs_value"],
        ascending=[True, True, True, False],
        na_position="last",
    )
    missing_rank = canonical["RANK"].isna() | canonical["RANK"].le(0)
    generated_rank = canonical.groupby(["SYMBOL", "SIDE"]).cumcount().add(1).astype(float)
    canonical.loc[missing_rank, "RANK"] = generated_rank[missing_rank]

    return canonical.drop(columns=["__has_avg", "__abs_value", "__rank_sort"], errors="ignore").reset_index(drop=True)


def read_normalized_broker_raw(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    try:
        source = pd.read_csv(path, low_memory=False, encoding="utf-8-sig")
    except UnicodeDecodeError:
        source = pd.read_csv(path, low_memory=False, encoding="latin-1")
    return normalize_broker_raw_frame(source)


def broker_raw_trade_date(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty or "TO_DATE" not in frame.columns:
        return ""
    dates = pd.to_datetime(frame["TO_DATE"], errors="coerce").dropna()
    return dates.max().date().isoformat() if not dates.empty else ""


def validate_broker_raw(path: Path, trade_date: str) -> tuple[bool, pd.DataFrame, str]:
    try:
        frame = read_normalized_broker_raw(path)
    except Exception as exc:
        return False, pd.DataFrame(columns=CANONICAL_COLUMNS), f"BROKER_RAW_PARSE_FAILED: {exc}"
    if frame.empty:
        return False, frame, "BROKER_RAW_EMPTY_OR_SCHEMA_INVALID"
    raw_date = broker_raw_trade_date(frame)
    if raw_date != trade_date:
        return False, frame, f"BROKER_RAW_DATE_MISMATCH: {raw_date or 'UNKNOWN'}"
    return True, frame, ""
