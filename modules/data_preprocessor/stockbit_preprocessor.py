#!/usr/bin/env python3
"""Normalize Stockbit watchlist CSV into Decision-Engine-ready numeric data."""
from __future__ import annotations

import argparse
import csv
import math
import re
from pathlib import Path
from typing import Optional

MULTIPLIERS = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
MISSING = {"", "-", "—", "N/A", "NA", "NULL", "NONE"}


def clean_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def parse_number(value: object, *, compact_decimal_comma: bool = False) -> Optional[float]:
    """Parse Stockbit numbers, including accounting negatives and K/M/B/T suffixes.

    Examples:
      (705.59 B) -> -705590000000
      2,186.59 B -> 2186590000000
      65,12B     -> 65120000000 (when compact_decimal_comma=True)
      8,750      -> 8750
    """
    s = clean_text(value).replace("\u00a0", " ")
    if s.upper() in MISSING:
        return None

    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1].strip()
    elif s.startswith("-"):
        negative = True
        s = s[1:].strip()

    s = s.replace("Rp", "").replace("IDR", "").replace("%", "").strip()
    suffix = ""
    m = re.search(r"([KMBT])\s*$", s, flags=re.I)
    if m:
        suffix = m.group(1).upper()
        s = s[:m.start()].strip()

    s = re.sub(r"\s+", "", s)
    if not s:
        return None

    # Stockbit's compact columns may use Indonesian decimal comma: 65,12B or 2,41K.
    if compact_decimal_comma and suffix and "," in s and "." not in s:
        parts = s.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            s = parts[0] + "." + parts[1]
        else:
            s = s.replace(",", "")
    else:
        # Standard exported format uses comma as thousands separator.
        s = s.replace(",", "")

    try:
        number = float(s)
    except ValueError:
        return None

    number *= MULTIPLIERS.get(suffix, 1.0)
    return -number if negative else number


def fmt(value: Optional[float]) -> str:
    if value is None or math.isnan(value):
        return ""
    if float(value).is_integer():
        return str(int(value))
    return format(value, ".12g")


def normalize_symbol(value: object) -> str:
    lines = [x.strip() for x in clean_text(value).splitlines() if x.strip()]
    if not lines:
        return ""
    token = re.sub(r"\s+(BUY|SELL)$", "", lines[0], flags=re.I).strip()
    m = re.search(r"[A-Z0-9]{2,12}", token.upper())
    return m.group(0) if m else token.upper()


def preprocess(input_path: Path, output_path: Path) -> tuple[int, int]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        raise ValueError("CSV kosong")

    raw_headers = rows[0]
    # Preserve duplicate Stockbit headers by deterministic names.
    seen_headers: dict[str, int] = {}
    headers: list[str] = []
    for h in raw_headers:
        base = clean_text(h) or "Unnamed"
        seen_headers[base] = seen_headers.get(base, 0) + 1
        headers.append(base if seen_headers[base] == 1 else f"{base} {seen_headers[base]}")

    numeric_specs = {
        "Price": False, "Bid": False, "Ask": False,
        "Value": True, "Lot": True, "Freq": True,
        "Avg": False, "Prev": False, "Open": False, "High": False, "Low": False,
        "IEP": False, "IEV": False, "Volume": False, "Value 2": False,
        "Price MA 5": False, "Foreign Flow": False, "RSI (14)": False,
        "Volume MA 20": False, "VWAP": False, "Previous Price": False,
        "Market Cap": False, "1 Week Price Returns": False,
        "52 Week High": False, "52 Week Low": False,
        "1 Day Price Returns (%)": False, "Price Change": False,
        "Net Foreign Buy / Sell": False, "Bandar Accum/Dist": False,
    }

    derived_headers = ["Change Value", "Change Percent"]
    out_headers = headers + [f"{h} Numeric" for h in numeric_specs if h in headers] + derived_headers

    output_rows: list[list[str]] = []
    seen_symbols: set[str] = set()

    for raw in rows[1:]:
        raw += [""] * (len(headers) - len(raw))
        raw = raw[:len(headers)]
        row = dict(zip(headers, raw))
        symbol = normalize_symbol(row.get("Symbol", ""))
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        row["Symbol"] = symbol

        normalized_raw = [row[h].strip() for h in headers]
        numeric_values = []
        for h, decimal_comma in numeric_specs.items():
            if h in headers:
                numeric_values.append(fmt(parse_number(row.get(h), compact_decimal_comma=decimal_comma)))

        change_text = clean_text(row.get("Change (%)", ""))
        change_value = change_pct = None
        if change_text:
            lines = [x.strip() for x in change_text.splitlines() if x.strip()]
            if lines:
                change_value = parse_number(lines[0])
            pct_match = re.search(r"([+-]?\d[\d,.]*)\s*%", change_text)
            if pct_match:
                change_pct = parse_number(pct_match.group(1))

        output_rows.append(normalized_raw + numeric_values + [fmt(change_value), fmt(change_pct)])

    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(out_headers)
        writer.writerows(output_rows)

    return len(output_rows), len(out_headers)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize Stockbit watchlist CSV")
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()
    output = args.output or args.input_csv.with_name(args.input_csv.stem + "_normalized.csv")
    count, columns = preprocess(args.input_csv, output)
    print(f"OK: {count} symbols, {columns} columns -> {output}")


if __name__ == "__main__":
    main()
