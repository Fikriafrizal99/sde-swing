#!/usr/bin/env python3
from __future__ import annotations

"""Read-only News Monitor for SDE Swing.

This module is intentionally isolated from Technical, Broker, Decision, and
Portfolio Management scoring/action logic. It only collects, filters, stores,
previews, and sends informational news digests.
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
DEFAULT_DOTENV = PROJECT_ROOT / ".env"
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
    "market", "stocks", "equities",
}
INDONESIA_KEYWORDS = {
    "indonesia", "ihsg", "idx", "rupiah", "bank indonesia", "bi rate",
    "ojk", "bursa efek indonesia", "bei", "ekonomi indonesia", "apbn",
    "pemerintah", "kementerian keuangan", "pasar saham",
}
SECTOR_KEYWORDS = {
    "energy", "energi", "bank", "banking", "perbankan", "technology",
    "teknologi", "property", "properti", "infrastructure", "infrastruktur",
    "consumer", "konsumer", "healthcare", "kesehatan", "mining", "tambang",
    "coal", "batubara", "nickel", "nikel", "gold", "emas", "oil", "minyak",
    "commodity", "komoditas",
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


def _load_dotenv(path: Path = DEFAULT_DOTENV) -> None:
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


def brave_api_key() -> str:
    _load_dotenv()
    env = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if env:
        return env
    local = load_json(DEFAULT_NEWS_LOCAL)
    return str(local.get("brave_search_api_key", "") or "").strip()


def telegram_config() -> dict[str, Any]:
    return load_json(DEFAULT_TELEGRAM)


def telegram_credentials() -> tuple[str, str]:
    _load_dotenv()
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
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
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
        if not canonical or canonical in seen_urls:
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


def _response_error(response: Any) -> str:
    try:
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception:
        return str(getattr(response, "text", "") or "").strip()[:1000]


def _brave_search(*, query: str, freshness: str, count: int, timeout: int) -> list[dict[str, Any]]:
    """Execute a minimal Brave News Search request.

    Locale parameters are intentionally omitted. Indonesia relevance is
    expressed in the query text, avoiding unsupported locale combinations that
    can return HTTP 422 even when the subscription key itself is valid.
    """
    if requests is None:
        raise RuntimeError("Dependency requests belum terpasang.")
    key = brave_api_key()
    if not key:
        raise RuntimeError("BRAVE_SEARCH_API_KEY belum dikonfigurasi.")

    params: dict[str, Any] = {
        "q": query,
        "freshness": freshness,
        "count": max(1, min(int(count), 50)),
        "safesearch": "moderate",
    }
    response = requests.get(
        BRAVE_NEWS_ENDPOINT,
        params=params,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": key,
        },
        timeout=max(5, int(timeout)),
    )
    if response.status_code == 429:
        raise RuntimeError("BRAVE_RATE_LIMIT")
    if response.status_code == 422:
        raise RuntimeError(f"BRAVE_REQUEST_INVALID: {_response_error(response)}")
    if not response.ok:
        raise RuntimeError(f"BRAVE_HTTP_{response.status_code}: {_response_error(response)}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("Brave response bukan JSON.") from exc
    results = payload.get("results", [])
    return [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []


def monitored_symbols(limit: int = 12) -> list[str]:
    symbols: list[str] = []
    if DEFAULT_DB.exists():
        try:
            conn = sqlite3.connect(DEFAULT_DB, timeout=5)
            rows = conn.execute(
                "SELECT DISTINCT UPPER(symbol) FROM portfolio_positions "
                "WHERE UPPER(current_status)='OPEN' ORDER BY updated_at DESC"
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
                symbol_col = next(
                    (col for col in frame.columns if str(col).strip().lower() in {"symbol", "ticker", "emiten"}),
                    None,
                )
                decision_col = next(
                    (col for col in frame.columns if str(col).strip().lower() in {"decision_v3", "decision", "final_decision"}),
                    None,
                )
                work = frame
                if decision_col:
                    mask = work[decision_col].astype(str).str.upper().str.contains("BUY|WATCH", regex=True, na=False)
                    work = work.loc[mask]
                if symbol_col:
                    symbols.extend(
                        work[symbol_col]
                        .astype(str)
                        .str.upper()
                        .str.replace(".JK", "", regex=False)
                        .str.strip()
                        .tolist()
                    )
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
    """Return batch queries; locality is encoded in query text, not country."""
    plans = [
        {
            "scope": "GLOBAL",
            "query": "Federal Reserve Wall Street China oil gold commodities geopolitics global markets stocks",
        },
        {
            "scope": "INDONESIA",
            "query": "IHSG rupiah Bank Indonesia OJK BEI ekonomi Indonesia pasar saham kebijakan pemerintah",
        },
        {
            "scope": "SECTOR",
            "query": "saham Indonesia sektor energi perbankan teknologi properti infrastruktur konsumer tambang komoditas",
        },
    ]
    if symbols:
        joined = " ".join(symbols[:12])
        plans.append(
            {
                "scope": "ISSUER",
                "query": f"{joined} saham emiten IDX corporate action earnings dividen kontrak akuisisi",
            }
        )
    return plans


def _resolve_freshness(session: str, config: dict[str, Any], current: datetime) -> str:
    if session == "morning":
        raw = str(config.get("morning_freshness", "pd") or "pd").strip()
    else:
        raw = str(config.get("post_market_freshness", "today") or "today").strip()

    if raw in {"pd", "pw", "pm", "py"}:
        return raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2}", raw):
        return raw
    if session == "post_market" and raw.lower() == "today":
        day = current.date().isoformat()
        return f"{day}to{day}"
    return "pd"


def _morning_items_for_date(day: str) -> list[NewsItem]:
    path = DEFAULT_OUTPUT / day / "morning_news.json"
    payload = load_json(path)
    rows = payload.get("items", []) if isinstance(payload.get("items", []), list) else []
    items: list[NewsItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            items.append(NewsItem(**row))
        except TypeError:
            continue
    return items


def _remove_morning_duplicates(items: list[NewsItem], day: str) -> list[NewsItem]:
    morning = _morning_items_for_date(day)
    if not morning:
        return items
    morning_urls = {item.url.split("#", 1)[0].rstrip("/") for item in morning}
    kept: list[NewsItem] = []
    for item in items:
        canonical = item.url.split("#", 1)[0].rstrip("/")
        if canonical in morning_urls:
            continue
        if any(_similar_title(item.headline, existing.headline) for existing in morning):
            continue
        kept.append(item)
    return kept


def _limit_items(items: list[NewsItem], maximum: int) -> list[NewsItem]:
    per_scope = {"GLOBAL": 3, "INDONESIA": 3, "SECTOR": 2, "ISSUER": 4}
    selected: list[NewsItem] = []
    for scope in ("GLOBAL", "INDONESIA", "SECTOR", "ISSUER"):
        group = [item for item in items if item.scope == scope]
        selected.extend(group[: per_scope[scope]])
    return selected[:maximum]


def collect_news(session: str, scheduler: dict[str, Any] | None = None) -> tuple[list[NewsItem], dict[str, Any]]:
    if session not in SESSION_REPORT_TYPE:
        raise ValueError(f"Unknown news session: {session}")

    scheduler = scheduler or load_json(DEFAULT_SCHEDULER)
    config = scheduler.get("news_monitor", {}) if isinstance(scheduler.get("news_monitor", {}), dict) else {}
    timeout = int(config.get("request_timeout_seconds", 20) or 20)
    count = int(config.get("results_per_query", 12) or 12)
    maximum = int(config.get("max_total_items", 10) or 10)
    current = now_wib()
    freshness = _resolve_freshness(session, config, current)
    symbols = monitored_symbols()
    plans = query_plan(symbols)

    raw_count = 0
    normalized: list[NewsItem] = []
    errors: list[str] = []
    for plan in plans:
        try:
            rows = _brave_search(
                query=plan["query"],
                freshness=freshness,
                count=count,
                timeout=timeout,
            )
        except Exception as exc:
            errors.append(f"{plan['scope']}: {exc}")
            continue
        raw_count += len(rows)
        for row in rows:
            item = normalize_result(row, scope=plan["scope"], symbols=symbols)
            if item is not None:
                normalized.append(item)

    deduped = dedupe_items(normalized)
    if session == "post_market":
        deduped = _remove_morning_duplicates(deduped, current.date().isoformat())
    selected = _limit_items(deduped, maximum)

    meta = {
        "session": session,
        "provider": "BRAVE_NEWS_SEARCH",
        "generated_at": current.isoformat(timespec="seconds"),
        "freshness": freshness,
        "monitored_symbols": symbols,
        "queries": len(plans),
        "raw_results": raw_count,
        "normalized_results": len(normalized),
        "deduplicated_results": len(deduped),
        "displayed_results": len(selected),
        "errors": errors,
        "decision_engine_write_access": False,
    }
    return selected, meta


def _item_time(item: NewsItem) -> str:
    if item.age:
        return item.age
    if item.published_at:
        return item.published_at
    return ""


def format_digest(session: str, items: list[NewsItem], generated_at: datetime | None = None) -> str:
    generated_at = generated_at or now_wib()
    if session == "morning":
        title = "📰 SDE SWING — MORNING NEWS"
        subtitle = "🌅 Overnight & Pre-Market Brief"
    else:
        title = "📰 SDE SWING — POST MARKET NEWS"
        subtitle = "🌆 Daily Market News Digest"

    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 {generated_at.strftime('%d %b %Y')} | {generated_at.strftime('%H:%M')} WIB",
        subtitle,
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    sections = [
        ("GLOBAL", "🌍 GLOBAL"),
        ("INDONESIA", "🇮🇩 INDONESIA"),
        ("SECTOR", "🏭 SECTOR"),
        ("ISSUER", "📌 EMITEN"),
    ]
    for scope, heading in sections:
        group = [item for item in items if item.scope == scope]
        if not group:
            continue
        lines.extend(["", heading, ""])
        current_symbol = ""
        for item in group:
            if scope == "ISSUER" and item.symbol and item.symbol != current_symbol:
                current_symbol = item.symbol
                lines.append(current_symbol)
            lines.append(f"• {item.headline}")
            time_text = _item_time(item)
            source_line = f"  {item.source}"
            if time_text:
                source_line += f" | {time_text}"
            lines.append(source_line)
            lines.append(f"  🔗 Baca: {item.url}")
            lines.append("")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        f"📊 {len(items)} berita relevan ditampilkan",
        "",
        "ℹ️ News hanya informasi dan tidak memengaruhi keputusan SDE.",
    ])
    return "\n".join(lines).strip() + "\n"


def _paths(session: str, day: str) -> tuple[Path, Path]:
    folder = DEFAULT_OUTPUT / day
    return folder / f"{session}_news.txt", folder / f"{session}_news.json"


def save_digest(session: str, items: list[NewsItem], meta: dict[str, Any]) -> tuple[Path, Path, str]:
    generated_at = now_wib()
    day = generated_at.date().isoformat()
    text_path, json_path = _paths(session, day)
    text = format_digest(session, items, generated_at)
    write_text_atomic(text_path, text)
    write_json_atomic(
        json_path,
        {
            "session": session,
            "generated_at": generated_at.isoformat(timespec="seconds"),
            "items": [asdict(item) for item in items],
            "meta": meta,
        },
    )
    return text_path, json_path, text


def _latest_text_path(session: str) -> Path | None:
    candidates = sorted(DEFAULT_OUTPUT.glob(f"*/{session}_news.txt"), reverse=True)
    return candidates[0] if candidates else None


def _existing_text(session: str) -> tuple[Path | None, str]:
    today_path, _ = _paths(session, now_wib().date().isoformat())
    path = today_path if today_path.exists() else _latest_text_path(session)
    if path is None or not path.exists():
        return None, ""
    try:
        return path, path.read_text(encoding="utf-8")
    except Exception:
        return path, ""


def _effective_news_topic(report_type: str) -> str:
    _load_dotenv()
    route = TelegramRouter(telegram_config(), os.environ).resolve(report_type, "news")
    thread = str(route.message_thread_id or "").strip()
    if thread.isdigit() and int(thread) > 0:
        return thread
    scheduler = load_json(DEFAULT_SCHEDULER)
    routing = scheduler.get("delivery", {}).get("topic_routing", {})
    for key in (report_type, "news"):
        value = str(routing.get(key, "") or "").strip() if isinstance(routing, dict) else ""
        if value.isdigit() and int(value) > 0:
            return value
    return ""


def _signature(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = block
    if current:
        parts.append(current)
    return parts


def send_existing(session: str, *, force: bool = False) -> int:
    if requests is None:
        print("[WARNING] News Telegram dilewati: dependency requests belum terpasang.")
        return 2
    path, text = _existing_text(session)
    if path is None or not text.strip():
        print(f"[WARNING] Belum ada output {session} news untuk dikirim.")
        return 2

    token, chat_id = telegram_credentials()
    if not token or not chat_id:
        print("[WARNING] News Telegram dilewati: token/chat_id belum dikonfigurasi.")
        return 2

    report_type = SESSION_REPORT_TYPE[session]
    thread_id = _effective_news_topic(report_type)
    if not thread_id:
        print("[WARNING] News Telegram dilewati: topic NEWS belum dikonfigurasi.")
        return 2

    state = load_json(DEFAULT_STATE)
    sig = _signature(text)
    prior = state.get(session, {}) if isinstance(state.get(session, {}), dict) else {}
    if not force and prior.get("signature") == sig:
        print(f"[SKIPPED] {session} news sudah pernah dikirim dengan isi yang sama.")
        return 0

    message_ids: list[Any] = []
    try:
        for part in _split_text(text):
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={
                    "chat_id": chat_id,
                    "message_thread_id": thread_id,
                    "text": part,
                    "disable_web_page_preview": "true",
                },
                timeout=30,
            )
            body = response.json()
            if not response.ok or not body.get("ok"):
                raise RuntimeError(str(body))
            message_ids.append(body.get("result", {}).get("message_id"))
    except Exception as exc:
        print(f"[WARNING] News Telegram gagal: {exc}")
        return 2

    state[session] = {
        "signature": sig,
        "sent_at": now_wib().isoformat(timespec="seconds"),
        "thread_id": thread_id,
        "message_ids": message_ids,
        "source_path": str(path),
    }
    write_json_atomic(DEFAULT_STATE, state)
    print(f"[OK] {session} news terkirim ke topic {thread_id}.")
    return 0


def run_session(session: str, *, send: bool = False) -> int:
    scheduler = load_json(DEFAULT_SCHEDULER)
    config = scheduler.get("news_monitor", {}) if isinstance(scheduler.get("news_monitor", {}), dict) else {}
    if config.get("enabled", True) is False:
        print("[SKIPPED] News Monitor disabled.")
        return 0

    try:
        items, meta = collect_news(session, scheduler)
        text_path, json_path, text = save_digest(session, items, meta)
    except Exception as exc:
        print(f"[WARNING] News Monitor gagal: {exc}")
        return 2

    print(text)
    print(f"[OK] Preview: {text_path}")
    print(f"[OK] Metadata: {json_path}")
    if meta.get("errors"):
        for error in meta["errors"]:
            print(f"[WARNING] {error}")

    if send:
        send_rc = send_existing(session)
        if send_rc != 0:
            return 2
    return 0


def preview_existing(session: str) -> int:
    path, text = _existing_text(session)
    if path is None or not text.strip():
        print(f"[WARNING] Belum ada preview {session} news.")
        return 2
    print(text)
    print(f"Preview source: {path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SDE Swing read-only News Monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run")
    run.add_argument("--session", choices=sorted(SESSION_REPORT_TYPE), required=True)
    run.add_argument("--send", action="store_true")

    preview = sub.add_parser("preview")
    preview.add_argument("--session", choices=sorted(SESSION_REPORT_TYPE), required=True)

    send = sub.add_parser("send")
    send.add_argument("--session", choices=sorted(SESSION_REPORT_TYPE), required=True)
    send.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "run":
        return run_session(args.session, send=args.send)
    if args.command == "preview":
        return preview_existing(args.session)
    if args.command == "send":
        return send_existing(args.session, force=args.force)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
