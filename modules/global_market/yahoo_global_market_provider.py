from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None


@dataclass
class YahooFetchResult:
    symbol: str
    data: pd.DataFrame
    status: str
    error: str = ""
    retry_count: int = 0


def _flatten_single(raw: pd.DataFrame) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.copy()
        raw.columns = [str(col[-1] if len(col) > 1 else col[0]) for col in raw.columns]
    return raw


def _extract_symbol(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    if not isinstance(raw.columns, pd.MultiIndex):
        return raw.copy()
    level0 = [str(x) for x in raw.columns.get_level_values(0)]
    level1 = [str(x) for x in raw.columns.get_level_values(1)]
    if symbol in level0:
        return raw[symbol].copy()
    if symbol in level1:
        return raw.xs(symbol, level=1, axis=1).copy()
    return _flatten_single(raw)


def normalize_history(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Previous_Close", "Volume"])
    df = _flatten_single(raw).copy().reset_index()
    rename: dict[Any, str] = {}
    for col in df.columns:
        key = str(col).strip().lower().replace("_", " ")
        if key in {"date", "datetime", "timestamp", "index"}:
            rename[col] = "Date"
        elif key == "open":
            rename[col] = "Open"
        elif key == "high":
            rename[col] = "High"
        elif key == "low":
            rename[col] = "Low"
        elif key in {"close", "last price"}:
            rename[col] = "Close"
        elif key in {"previous close", "prev close"}:
            rename[col] = "Previous_Close"
        elif key == "volume":
            rename[col] = "Volume"
    df = df.rename(columns=rename)
    if "Date" not in df.columns and len(df.columns):
        df = df.rename(columns={df.columns[0]: "Date"})
    for col in ["Open", "High", "Low", "Close", "Previous_Close", "Volume"]:
        if col not in df.columns:
            df[col] = pd.NA
    if "Close" not in df.columns:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Previous_Close", "Volume"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True).dt.tz_convert(None)
    for col in ["Open", "High", "Low", "Close", "Previous_Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date")
    df["Yahoo_Symbol"] = symbol
    return df[["Date", "Open", "High", "Low", "Close", "Previous_Close", "Volume", "Yahoo_Symbol"]]


class YahooGlobalMarketProvider:
    def __init__(self, yf_module: Any | None = None):
        self.yf = yf_module if yf_module is not None else yf

    def download_batch(
        self,
        symbols: list[str],
        period: str,
        interval: str,
        timeout: int,
        threads: bool = True,
    ) -> dict[str, YahooFetchResult]:
        if not symbols:
            return {}
        if self.yf is None:
            message = "Dependency yfinance belum terpasang. Jalankan: pip install -r requirements.txt"
            return {symbol: YahooFetchResult(symbol, pd.DataFrame(), "FETCH_FAILED", message) for symbol in symbols}
        try:
            raw = self.yf.download(
                tickers=symbols if len(symbols) > 1 else symbols[0],
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=threads,
                timeout=timeout,
            )
        except TypeError:
            raw = self.yf.download(
                symbols if len(symbols) > 1 else symbols[0],
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=threads,
                timeout=timeout,
            )
        except Exception as exc:
            return {symbol: YahooFetchResult(symbol, pd.DataFrame(), "FETCH_FAILED", str(exc)) for symbol in symbols}
        results: dict[str, YahooFetchResult] = {}
        for symbol in symbols:
            frame = normalize_history(_extract_symbol(raw, symbol), symbol)
            status = "SUCCESS" if not frame.empty else "FETCH_FAILED"
            error = "" if status == "SUCCESS" else "Yahoo mengembalikan data kosong"
            results[symbol] = YahooFetchResult(symbol, frame, status, error)
        return results

