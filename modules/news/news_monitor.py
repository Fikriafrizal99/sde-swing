#!/usr/bin/env python3
from __future__ import annotations

"""Read-only news monitor for SDE Swing.

News is informational only. This module never imports or mutates the trading
Decision Engine, Broker Engine, Technical Engine, or Portfolio Management
scoring/action logic.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.telegram.router import TelegramRouter

WIB = ZoneInfo("Asia/Jakarta")
BRAVE_NEWS_ENDPOINT = "https://api.search.brave.com/res/v1/news/search"
DEFAULT_SCHEDULER = PROJECT_ROOT / "config/scheduler.json"
DEFAULT_TELEGRAM = PROJECT_ROOT / "config/telegram.json"
DEFAULT_NEWS_LOCAL = PROJECT_ROOT / "config/news.local.json"
DEFAULT_DB = PROJECT_ROOT / "data/database/sde_swing_history.db"
DEFAULT_DECISIONS = PROJECT_ROOT / "data/output/decision/FINAL_DECISION_V3.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data/output/news"
DEFAULT_STATE = PROJECT_ROOT / "data/state/news_delivery.json"

SESSION_REPORT_TYPE = {
    "morning": "morning_news",
    "post_market": "post_market_news",
}

SOURCE_PRIORITY = {
    "reuters.com": 5,
    "bloomberg.com": 5,
    "ft.com": 4,
    "wsj.com": 4,
    "cnbc.com": 4,
    "apnews.com": 4,
    "idx.co.id": 5,
    "ojk.go.id": 5,
    "bi.go.id": 5,
    "antaranews.com": 4,
    "cnbcindonesia.com": 4,
    "bisnis.com": 4,
    "kontan.co.id": 4,
    "investor.id": 3,
}

BLOCKED_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "x.com",
    "twitter.com",
    "medium.com",
    "blogspot.com",
}

GLOBAL_KEYWORDS = {
    "fed", "federal reserve", "wall street", "dow", "nasdaq", "s&p",
    "china", "beijing", "oil", "crude", "gold", "commodity", "commodities",
    "geopolit", "tariff", "inflation", "jobs", "yield", "dollar", "dxy",
}
INDONESIA_KEYWORDS = {
    "indonesia", "ihsg", "idx", "rupiah", "bank indonesia", "bi rate",
    "ojk", "bursa efek indonesia", "bei", "ekonomi indonesia", "apbn",
    "pemerintah", "kementerian keuangan", "sri mulyani", "purbaya",
}
SECTOR_KEYWORDS = {
    "energy", "energi", "bank", "banking", "perbankan", "technology",
    "teknologi", "property", "properti", "infrastructure", "infrastruktur",
    "consumer", "konsumer", "healthcare", "kesehatan", "mining", "tambang",
    "coal", "batubara", "nickel", "nikel", "gold", "emas", "oil", "minyak",
}


@dataclass(frozen=True)
class NewsItem:
    headline: str
    source: str
    url: str
    published_at: str
    age: str
    scope: str
    category: str
    symbol: str = ""
    score: float = 0.0


def now_wib() -> datetime:
    return datetime.now(tz=WIB)


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_runtime_environment() -> None:
    _load_dotenv(PROJECT_ROOT / ".env")


def brave_api_key() -> str:
    load_runtime_environment()
    env = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if env:
        return env
    local = load_json(DEFAULT_NEWS_LOCAL)
    return str(local.get("brave_search_api_key", "") or "").strip()


def telegram_config() -> dict[str, Any]:
    return load_json(DEFAULT_TELEGRAM)


def telegram_credentials() -> tuple[str, str]:
    load_runtime_environment()
    cfg = telegram_config().get("telegram", {})
    if not isinstance(cfg, dict):
        cfg = {}
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or str(cfg.get("bot_token", "") or "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip() or str(cfg.get("chat_id", "") or "").strip()
    return token, chat_id


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().split(":", 1)[0]
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _source_name(result: dict[str, Any]) -> str:
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    for key in ("long_name", "name"):
        value = str(profile.get(key, "") or "").strip()
        if value:
            return value
    domain = _domain(str(result.get("url", "") or ""))
    if not domain:
        return "Unknown Source"
    labels = domain.split(".")
    base = labels[-2] if len(labels) >= 2 else labels[0]
    return base.replace("-", " ").title()


def _source_score(url: str) -> int:
    domain = _domain(url)
    for known, score in SOURCE_PRIORITY.items():
        if domain == known or domain.endswith("." + known):
            return score
    return 1


def _blocked_source(url: str) -> bool:
    domain = _domain(url)
    return any(domain == bad or domain.endswith("." + bad) for bad in BLOCKED_DOMAINS)


def _normalize_title(value: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    return " ".join(part for part in text.split() if len(part) > 1)


def _title_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _normalize_title(value).split():
        if len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("es"):
            token = token[:-1]
        elif len(token) > 3 and token.endswith("s"):
            token = token[:-1]
        tokens.add(token)
    return tokens


def _similar_title(left: str, right: str) -> bool:
    a = _title_tokens(left)
    b = _title_tokens(right)
    if not a or not b:
        return False
    intersection = len(a & b)
    union = len(a | b)
    return union > 0 and (intersection / union) >= 0.68


def dedupe_items(items: Iterable[NewsItem]) -> list[NewsItem]:
    kept: list[NewsItem] = []
    seen_urls: set[str] = set()
    for item in sorted(items, key=lambda row: row.score, reverse=True):
        canonical = item.url.split("#", 1)[0].rstrip("/")
        if canonical in seen_urls:
            continue
        if any(_similar_title(item.headline, existing.headline) for existing in kept):
            continue
        seen_urls.add(canonical)
        kept.append(item)
    return kept


def _contains_any(text: str, keywords: set[str]) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in keywords)


def _extract_symbol(text: str, symbols: list[str]) -> str:
    upper = text.upper()
    for symbol in symbols:
        if re.search(rf"(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9])", upper):
            return symbol
    return ""


def normalize_result(result: dict[str, Any], *, scope: str, symbols: list[str]) -> NewsItem | None:
    headline = str(result.get("title", "") or "").strip()
    url = str(result.get("url", "") or "").strip()
    description = str(result.get("description", "") or "").strip()
    if not headline or not url or _blocked_source(url):
        return None

    combined = f"{headline} {description}"
    symbol = _extract_symbol(combined, symbols) if scope == "ISSUER" else ""
    if scope == "GLOBAL" and not _contains_any(combined, GLOBAL_KEYWORDS):
        return None
    if scope == "INDONESIA" and not _contains_any(combined, INDONESIA_KEYWORDS):
        return None
    if scope == "SECTOR" and not _contains_any(combined, SECTOR_KEYWORDS):
        return None
    if scope == "ISSUER" and symbols and not symbol:
        return None

    age = str(result.get("age", "") or "").strip()
    page_age = str(result.get("page_age", "") or result.get("published_at", "") or "").strip()
    score = float(_source_score(url))
    score += 1.0 if len(headline) >= 30 else 0.0
    if scope == "ISSUER" and symbol:
        score += 2.0

    return NewsItem(
        headline=headline,
        source=_source_name(result),
        url=url,
        published_at=page_age,
        age=age,
        scope=scope,
        category=scope,
        symbol=symbol,
        score=score,
    )


def _brave_search(*, query: str, country: str, search_lang: str, freshness: str, count: int, timeout: int) -> list[dict[str, Any]]:
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang.")
    key = brave_api_key()
    if not key:
        raise RuntimeError("BRAVE_SEARCH_API_KEY belum dikonfigurasi.")
    response = requests.get(
        BRAVE_NEWS_ENDPOINT,
        params={
            "q": query,
            "country": country,
            "search_lang": search_lang,
            "freshness": freshness,
            "count": max(1, min(int(count), 50)),
            "safesearch": "moderate",
            "spellcheck": "true",
            "operators": "true",
        },
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": key,
        },
        timeout=max(5, int(timeout)),
    )
    if response.status_code == 429:
        raise RuntimeError("BRAVE_RATE_LIMIT")
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", [])
    return [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []


def monitored_symbols(limit: int = 12) -> list[str]:
    symbols: list[str] = []
    if DEFAULT_DB.exists():
        try:
            conn = sqlite3.connect(DEFAULT_DB, timeout=5)
            rows = conn.execute(
                "SELECT DISTINCT UPPER(symbol) FROM portfolio_positions WHERE UPPER(current_status)='OPEN' ORDER BY updated_at DESC"
            ).fetchall()
            conn.close()
            symbols.extend(str(row[0]).replace(".JK", "").strip().upper() for row in rows if row and row[0])
        except Exception:
            pass
    if DEFAULT_DECISIONS.exists():
        try:
            import pandas as pd

            frame = pd.read_csv(DEFAULT_DECISIONS, low_memory=False)
            if not frame.empty:
                symbol_col = next((col for col in frame.columns if str(col).strip().lower() in {"symbol", "ticker", "emiten"}), None)
                decision_col = next((col for col in frame.columns if str(col).strip().lower() in {"decision_v3", "decision", "final_decision"}), None)
                work = frame
                if decision_col:
                    mask = work[decision_col].astype(str).str.upper().str.contains("BUY|WATCH", regex=True, na=False)
                    work = work.loc[mask]
                if symbol_col:
                    symbols.extend(work[symbol_col].astype(str).str.upper().str.replace(".JK", "", regex=False).str.strip().tolist())
        except Exception:
            pass
    unique: list[str] = []
    for symbol in symbols:
        if symbol and symbol not in unique and re.fullmatch(r"[A-Z0-9]{2,8}", symbol):
            unique.append(symbol)
        if len(unique) >= limit:
            break
    return unique


def query_plan(symbols: list[str]) -> list[dict[str, str]]:
    plans = [
        {
            "scope": "GLOBAL",
            "query": 'Federal Reserve OR Wall Street OR China OR oil OR gold OR commodities OR geopolitics markets',
            "country": "ALL",
            "search_lang": "en",
        },
        {
            "scope": "INDONESIA",
            "query": 'IHSG OR rupiah OR "Bank Indonesia" OR OJK OR BEI OR ekonomi Indonesia pasar saham',
            "country": "ID",
            "search_lang": "id",
        },
        {
            "scope": "SECTOR",
            "query": 'saham Indonesia sektor energi perbankan teknologi properti infrastruktur konsumer tambang komoditas',
            "country": "ID",
            "search_lang": "id",
        },
    ]
    if symbols:
        joined = " OR ".join(symbols[:12])
        plans.append(
            {
                "scope": "ISSUER",
                "query": f"({joined}) saham emiten IDX",
                "country": "ID",
                "search_lang": "id",
            }
        )
    return plans


def collect_news(session: str, scheduler: dict[str, Any] | None = None) -> tuple[list[NewsItem], dict[str, Any]]:
    scheduler = scheduler or load_json(DEFAULT_SCHEDULER)
    config = scheduler.get("news_monitor", {}) if isinstance(scheduler.get("news_monitor", {}), dict) else {}
    timeout = int(config.get("request_timeout_seconds", 20) or 20)
    count = int(config.get("results_per_query", 12) or 12)
    freshness = str(config.get(f"{session}_freshness", "pd") or "pd")
    if freshness.lower() == "today":
        today = now_wib().date().isoformat()
        freshness = f"{today}to{today}"
    max_total = int(config.get("max_total_items", 10) or 10)
    limits = {"GLOBAL": 3, "INDONESIA": 3, "SECTOR": 2, "ISSUER": 4}

    symbols = monitored_symbols()
    collected: list[NewsItem] = []
    request_errors: list[str] = []
    request_count = 0
    raw_count = 0
    for plan in query_plan(symbols):
        request_count += 1
        try:
            rows = _brave_search(
                query=plan["query"],
                country=plan["country"],
                search_lang=plan["search_lang"],
                freshness=freshness,
                count=count,
                timeout=timeout,
            )
        except Exception as exc:
            request_errors.append(f"{plan['scope']}:{type(exc).__name__}:{exc}")
            continue
        raw_count += len(rows)
        for row in rows:
            item = normalize_result(row, scope=plan["scope"], symbols=symbols)
            if item is not None:
                collected.append(item)

    deduped = dedupe_items(collected)
    selected: list[NewsItem] = []
    for scope in ("GLOBAL", "INDONESIA", "SECTOR", "ISSUER"):
        scoped = [item for item in deduped if item.scope == scope]
        selected.extend(scoped[: limits[scope]])
    selected = dedupe_items(selected)[:max_total]
    meta = {
        "provider": "BRAVE_NEWS_SEARCH",
        "session": session,
        "generated_at": now_wib().isoformat(timespec="seconds"),
        "monitored_symbols": symbols,
        "request_count": request_count,
        "raw_result_count": raw_count,
        "selected_count": len(selected),
        "request_errors": request_errors,
    }
    return selected, meta


def _display_time(item: NewsItem) -> str:
    if item.age:
        return item.age
    raw = item.published_at.strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=WIB)
        return parsed.astimezone(WIB).strftime("%H:%M WIB")
    except Exception:
        return raw[:32]


def format_digest(session: str, items: list[NewsItem], generated_at: datetime | None = None) -> str:
    generated_at = generated_at or now_wib()
    label = "MORNING NEWS" if session == "morning" else "POST MARKET NEWS"
    lines = [
        f"📰 SDE SWING — {label}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 {generated_at.strftime('%d %b %Y | %H:%M WIB')}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]
    sections = [
        ("GLOBAL", "🌍 GLOBAL"),
        ("INDONESIA", "🇮🇩 INDONESIA"),
        ("SECTOR", "🏭 SECTOR"),
    ]
    for scope, heading in sections:
        scoped = [item for item in items if item.scope == scope]
        if not scoped:
            continue
        lines.extend(["", heading])
        for item in scoped:
            stamp = _display_time(item)
            source_line = f"  {item.source}" + (f" | {stamp}" if stamp else "")
            lines.extend([f"• {item.headline}", source_line, f"  🔗 Baca: {item.url}"])

    issuer_items = [item for item in items if item.scope == "ISSUER"]
    if issuer_items:
        lines.extend(["", "📌 EMITEN"])
        by_symbol: dict[str, list[NewsItem]] = {}
        for item in issuer_items:
            by_symbol.setdefault(item.symbol or "LAINNYA", []).append(item)
        for symbol, rows in by_symbol.items():
            lines.extend(["", symbol])
            for item in rows:
                stamp = _display_time(item)
                source_line = f"  {item.source}" + (f" | {stamp}" if stamp else "")
                lines.extend([f"• {item.headline}", source_line, f"  🔗 Baca: {item.url}"])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 {len(items)} berita relevan ditampilkan",
        "",
        "ℹ️ News hanya informasi dan tidak memengaruhi keputusan SDE.",
    ])
    return "\n".join(lines).strip() + "\n"


def output_paths(session: str, date_text: str | None = None) -> tuple[Path, Path]:
    date_text = date_text or now_wib().date().isoformat()
    folder = DEFAULT_OUTPUT / date_text
    return folder / f"{session}_news.txt", folder / f"{session}_news.json"


def latest_output(session: str) -> Path | None:
    if not DEFAULT_OUTPUT.exists():
        return None
    candidates = sorted(DEFAULT_OUTPUT.glob(f"*/{session}_news.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def suppress_morning_duplicates(items: list[NewsItem], date_text: str | None = None) -> tuple[list[NewsItem], int]:
    date_text = date_text or now_wib().date().isoformat()
    morning_json = output_paths("morning", date_text)[1]
    payload = load_json(morning_json)
    prior_rows = payload.get("items", []) if isinstance(payload.get("items", []), list) else []
    prior: list[NewsItem] = []
    for row in prior_rows:
        if not isinstance(row, dict):
            continue
        try:
            prior.append(NewsItem(**{
                "headline": str(row.get("headline", "")),
                "source": str(row.get("source", "")),
                "url": str(row.get("url", "")),
                "published_at": str(row.get("published_at", "")),
                "age": str(row.get("age", "")),
                "scope": str(row.get("scope", "")),
                "category": str(row.get("category", "")),
                "symbol": str(row.get("symbol", "")),
                "score": float(row.get("score", 0.0) or 0.0),
            }))
        except Exception:
            continue
    if not prior:
        return items, 0
    prior_urls = {item.url.split("#", 1)[0].rstrip("/") for item in prior}
    kept: list[NewsItem] = []
    suppressed = 0
    for item in items:
        canonical = item.url.split("#", 1)[0].rstrip("/")
        duplicate = canonical in prior_urls or any(_similar_title(item.headline, old.headline) for old in prior)
        if duplicate:
            suppressed += 1
        else:
            kept.append(item)
    return kept, suppressed


def generate(session: str) -> tuple[Path | None, dict[str, Any]]:
    items, meta = collect_news(session)
    date_text = now_wib().date().isoformat()
    if session == "post_market":
        items, suppressed = suppress_morning_duplicates(items, date_text)
        meta["suppressed_morning_duplicates"] = suppressed
        meta["selected_count"] = len(items)
    text_path, json_path = output_paths(session, date_text)
    payload = {**meta, "items": [asdict(item) for item in items]}
    write_json_atomic(json_path, payload)
    if not items:
        if text_path.exists():
            text_path.unlink()
        return None, payload
    text = format_digest(session, items)
    write_text_atomic(text_path, text)
    return text_path, payload


def _route_thread(session: str) -> str:
    cfg = telegram_config()
    report_type = SESSION_REPORT_TYPE[session]
    route = TelegramRouter(cfg, os.environ).resolve(report_type, "news")
    if route.message_thread_id and str(route.message_thread_id).isdigit():
        return str(route.message_thread_id)
    scheduler = load_json(DEFAULT_SCHEDULER)
    routing = scheduler.get("delivery", {}).get("topic_routing", {})
    for key in (report_type, "news"):
        value = str(routing.get(key, "") or "").strip()
        if value.isdigit() and int(value) > 0:
            return value
    return ""


def _split_text(text: str, max_len: int = 3900) -> list[str]:
    if len(text) <= max_len:
        return [text]
    blocks = text.split("\n\n")
    parts: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = block
    if current:
        parts.append(current)
    final: list[str] = []
    for part in parts:
        while len(part) > max_len:
            final.append(part[:max_len])
            part = part[max_len:]
        if part:
            final.append(part)
    return final


def send_existing(session: str, *, date_text: str = "", force: bool = False) -> int:
    if requests is None:
        print("[WARNING] requests belum terpasang. News Telegram dilewati.")
        return 2
    path = output_paths(session, date_text)[0] if date_text else latest_output(session)
    if path is None or not path.exists():
        print(f"[WARNING] Output {session} news belum tersedia. Tidak ada yang dikirim.")
        return 2
    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        print("[WARNING] Telegram token/chat_id belum dikonfigurasi. News dilewati.")
        return 2
    thread_id = _route_thread(session)
    if not thread_id:
        print("[WARNING] Topic NEWS belum dikonfigurasi. News tidak dikirim ke main chat.")
        return 2

    text = path.read_text(encoding="utf-8")
    signature = hashlib.sha256(text.encode("utf-8")).hexdigest()
    state = load_json(DEFAULT_STATE)
    state_key = f"{session}:{path.parent.name}:{signature}"
    if state_key in state and not force:
        print("[SKIPPED] News yang sama sudah pernah dikirim. Gunakan Force Send untuk kirim ulang.")
        return 0

    message_ids: list[int] = []
    try:
        parts = _split_text(text)
        for index, part in enumerate(parts, start=1):
            prefix = f"Bagian {index}/{len(parts)}\n\n" if len(parts) > 1 else ""
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "message_thread_id": thread_id,
                    "text": prefix + part,
                    "disable_web_page_preview": "true",
                },
                timeout=30,
            )
            body = response.json() if response.content else {}
            if not response.ok or not body.get("ok"):
                raise RuntimeError(f"Telegram API gagal: {body or response.status_code}")
            message_ids.append(int(body.get("result", {}).get("message_id", 0) or 0))
    except Exception as exc:
        print(f"[WARNING] Delivery News gagal: {type(exc).__name__}: {exc}")
        return 2

    state[state_key] = {
        "sent_at": now_wib().isoformat(timespec="seconds"),
        "thread_id": thread_id,
        "message_ids": message_ids,
        "path": str(path),
        "force": bool(force),
    }
    write_json_atomic(DEFAULT_STATE, state)
    print(f"[OK] News terkirim ke topic {thread_id}: {path}")
    return 0


def preview_existing(session: str, date_text: str = "") -> int:
    path = output_paths(session, date_text)[0] if date_text else latest_output(session)
    if path is None or not path.exists():
        print(f"[WARNING] Preview {session} news belum tersedia.")
        return 2
    print(path.read_text(encoding="utf-8"))
    print(f"Preview: {path}")
    return 0


def test_brave() -> int:
    try:
        rows = _brave_search(
            query="IHSG Indonesia market",
            country="ID",
            search_lang="id",
            freshness="pd",
            count=1,
            timeout=15,
        )
        print(f"[OK] Brave News Search terhubung. Result={len(rows)}")
        return 0
    except Exception as exc:
        print(f"[FAILED] Brave News Search: {type(exc).__name__}: {exc}")
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SDE read-only News Monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--session", choices=sorted(SESSION_REPORT_TYPE), required=True)
    run.add_argument("--send", action="store_true")

    preview = sub.add_parser("preview")
    preview.add_argument("--session", choices=sorted(SESSION_REPORT_TYPE), required=True)
    preview.add_argument("--date", default="")

    send = sub.add_parser("send")
    send.add_argument("--session", choices=sorted(SESSION_REPORT_TYPE), required=True)
    send.add_argument("--date", default="")
    send.add_argument("--force", action="store_true")

    sub.add_parser("test-brave")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "test-brave":
        return test_brave()
    if args.command == "preview":
        return preview_existing(args.session, args.date)
    if args.command == "send":
        return send_existing(args.session, date_text=args.date, force=args.force)

    try:
        path, meta = generate(args.session)
    except Exception as exc:
        print(f"[WARNING] News generation gagal: {type(exc).__name__}: {exc}")
        return 2
    if path is None:
        errors = meta.get("request_errors", [])
        if errors:
            print("[WARNING] Tidak ada News yang lolos; sebagian/seluruh Brave request gagal.")
            for error in errors:
                print(f"  - {error}")
        else:
            print("[OK] Tidak ada berita relevan baru. Telegram News tidak dikirim.")
        return 0
    print(f"[OK] News generated: {path}")
    if args.send:
        return send_existing(args.session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
