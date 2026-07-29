from __future__ import annotations

from pathlib import Path
from typing import Any

from swing_utils import read_json


ROOT = Path(__file__).resolve().parents[2]


def resolve(path: str | Path) -> Path:
    current = Path(path)
    return current if current.is_absolute() else ROOT / current


def load_registry(path: str | Path = "config/global_market.json") -> dict[str, Any]:
    payload = read_json(resolve(path))
    if not payload:
        return {"provider": "YAHOO", "enabled": False, "instruments": []}
    provider = str(payload.get("provider", "")).upper()
    if provider != "YAHOO":
        raise ValueError("Global market provider selain YAHOO tidak diizinkan")
    if str(payload.get("source_mode", "LIVE")).upper() != "LIVE":
        raise ValueError("Scheduled global market hanya boleh memakai source_mode LIVE")
    return payload


def enabled_instruments(registry: dict[str, Any]) -> list[dict[str, Any]]:
    if not bool(registry.get("enabled", True)):
        return []
    instruments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in registry.get("instruments", []):
        item = dict(raw)
        if not bool(item.get("enabled", True)):
            continue
        key = str(item.get("key", "")).strip().lower()
        symbol = str(item.get("symbol", "")).strip()
        name = str(item.get("name", "")).strip()
        category = str(item.get("category", "")).strip().upper()
        if not key or not symbol or not name or not category:
            raise ValueError(f"Global market instrument tidak lengkap: {raw}")
        if key in seen:
            raise ValueError(f"Duplikat global market key: {key}")
        seen.add(key)
        item["key"] = key
        item["symbol"] = symbol
        item["name"] = name
        item["category"] = category
        instruments.append(item)
    return instruments

