#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf


def normalize_download(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    df = df.reset_index()
    rename = {}
    for col in df.columns:
        key = str(col).strip().lower().replace("_", " ")
        if key in {"date", "datetime"}:
            rename[col] = "Date"
        elif key == "open": rename[col] = "Open"
        elif key == "high": rename[col] = "High"
        elif key == "low": rename[col] = "Low"
        elif key == "close": rename[col] = "Close"
        elif key in {"adj close", "adjclose"}: rename[col] = "Adj Close"
        elif key == "volume": rename[col] = "Volume"
    df = df.rename(columns=rename)
    if "Date" not in df.columns or "Close" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.tz_localize(None)
    for c in ["Open", "High", "Low", "Close", "Adj Close", "Volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]
    if "Volume" not in df.columns:
        df["Volume"] = 0
    keep = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
    return df[keep].dropna(subset=["Date", "Close"]).sort_values("Date")


def read_existing(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, low_memory=False)
        mapping = {str(c).strip().lower(): c for c in df.columns}
        date_col = mapping.get("date")
        close_col = mapping.get("close")
        if not date_col or not close_col:
            return pd.DataFrame()
        out = pd.DataFrame({
            "Date": pd.to_datetime(df[date_col], errors="coerce"),
            "Open": pd.to_numeric(df[mapping.get("open", close_col)], errors="coerce"),
            "High": pd.to_numeric(df[mapping.get("high", close_col)], errors="coerce"),
            "Low": pd.to_numeric(df[mapping.get("low", close_col)], errors="coerce"),
            "Close": pd.to_numeric(df[close_col], errors="coerce"),
            "Adj Close": pd.to_numeric(df[mapping.get("adj close", close_col)], errors="coerce"),
            "Volume": pd.to_numeric(df[mapping.get("volume", close_col)], errors="coerce") if mapping.get("volume") else 0,
        })
        return out.dropna(subset=["Date", "Close"]).sort_values("Date")
    except Exception:
        return pd.DataFrame()


def main() -> int:
    p = argparse.ArgumentParser(description="Update IHSG history from Yahoo Finance")
    p.add_argument("--output", required=True)
    p.add_argument("--ticker", default="^JKSE")
    p.add_argument("--period", default="2y")
    p.add_argument("--overlap-days", type=int, default=10)
    args = p.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = read_existing(output)
    before = len(existing)
    last_local = existing["Date"].max() if not existing.empty else None

    kwargs = {"interval": "1d", "auto_adjust": False, "progress": False, "threads": False, "timeout": 15}
    if last_local is not None:
        start = (last_local.date() - timedelta(days=args.overlap_days)).isoformat()
        end = (date.today() + timedelta(days=1)).isoformat()
        print(f"[IHSG] Download {args.ticker} start={start} end={end}", flush=True)
        raw = yf.download(args.ticker, start=start, end=end, **kwargs)
    else:
        print(f"[IHSG] Download {args.ticker} period={args.period}", flush=True)
        raw = yf.download(args.ticker, period=args.period, **kwargs)

    fresh = normalize_download(raw)
    if fresh.empty:
        if existing.empty:
            raise RuntimeError("Yahoo returned no IHSG data and no existing file is available")
        print(f"[IHSG] Yahoo returned no new rows; keeping existing file ({before} rows)")
        return 0

    combined = pd.concat([existing, fresh], ignore_index=True) if not existing.empty else fresh
    combined["Date"] = pd.to_datetime(combined["Date"], errors="coerce")
    combined = combined.dropna(subset=["Date", "Close"]).sort_values("Date")
    combined = combined.drop_duplicates(subset=["Date"], keep="last")
    combined["Date"] = combined["Date"].dt.strftime("%Y-%m-%d")
    combined.to_csv(output, index=False, encoding="utf-8-sig")
    after = len(combined)
    print(f"[IHSG] before={before} after={after} added={max(0, after-before)} last={combined['Date'].iloc[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
