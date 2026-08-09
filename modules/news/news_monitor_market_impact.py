#!/usr/bin/env python3
from __future__ import annotations

"""Strict market-impact presentation/filter layer for SDE Swing News Monitor.

The stable base News Monitor remains untouched and usable as a fallback. This
module only narrows relevance and improves Telegram presentation. It never
writes to Technical, Broker, Decision, or Portfolio Management engine state.
"""

import html
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from modules.news import news_monitor as base


SOURCE_LABELS = {
    "economictimes.indiatimes.com": "The Economic Times",
    "timesofindia.indiatimes.com": "Times of India",
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "cnbc.com": "CNBC",
    "cnbcindonesia.com": "CNBC Indonesia",
    "bisnis.com": "Bisnis.com",
    "kontan.co.id": "Kontan",
    "investor.id": "Investor Daily",
    "liputan6.com": "Liputan6",
    "antaranews.com": "ANTARA",
    "idx.co.id": "IDX",
    "ojk.go.id": "OJK",
    "bi.go.id": "Bank Indonesia",
    "ft.com": "Financial Times",
    "wsj.com": "The Wall Street Journal",
    "apnews.com": "AP",
}

NOISE_PHRASES = {
    "sejarah", "history of", "historical overview", "era kolonial", "berusia",
    "apa itu", "what is", "mengenal", "panduan", "guide to", "how to",
    "cara memilih", "tips memilih", "tips investasi", "profil perusahaan",
    "company profile", "biografi", "biography", "explainer", "edukasi",
    "pelajari", "kamus", "glossary", "tutorial", "sepak bola", "football",
    "celebrity", "selebriti", "film terbaru", "hasil pertandingan",
}

POLITICAL_TERMS = {
    "trump", "senate", "president", "election", "pemilu", "parliament",
    "military", "war", "perang", "russia", "ukraine", "gaza", "iran",
    "geopolit", "sanction", "sanksi",
}

MARKET_IMPACT_TERMS = {
    "stock", "stocks", "equity", "equities", "index", "indices", "ihsg",
    "rupiah", "dollar", "usd", "dxy", "yuan", "currency", "forex",
    "bond", "bonds", "treasury", "yield", "yields", "rate", "rates",
    "suku bunga", "inflation", "inflasi", "cpi", "gdp", "pdb", "jobs",
    "payroll", "oil", "crude", "minyak", "gold", "emas", "coal", "batubara",
    "nickel", "nikel", "commodity", "commodities", "komoditas", "earnings",
    "laba", "profit", "revenue", "pendapatan", "dividend", "dividen",
    "tariff", "tariffs", "bea masuk", "trade war", "credit rating",
    "rating downgrade", "rating upgrade", "liquidity", "likuiditas",
}

MOVEMENT_EVENT_TERMS = {
    "rise", "rises", "rose", "rising", "gain", "gains", "jump", "jumps",
    "surge", "surges", "rally", "rallies", "record high", "fall", "falls",
    "fell", "drop", "drops", "plunge", "plunges", "slump", "selloff",
    "sell-off", "volatile", "volatility", "naik", "menguat", "terbang",
    "melonjak", "menguat tajam", "turun", "melemah", "anjlok", "merosot",
    "terkoreksi", "rebound", "cut", "cuts", "hike", "hikes", "hold rates",
    "holds rates", "announce", "announces", "announced", "rilis", "merilis",
    "lapor", "melaporkan", "approve", "approves", "disetujui", "resmi",
    "unexpected", "surprise", "higher than", "lower than", "above forecast",
    "below forecast", "katalis", "sentiment", "sentimen",
}

INDONESIA_MARKET_ANCHORS = {
    "ihsg", "idx", "bei", "bursa efek indonesia", "pasar saham", "pasar modal",
    "saham indonesia", "rupiah", "bank indonesia", "bi rate", "suku bunga",
    "inflasi", "obligasi", "yield", "ojk", "foreign flow", "asing",
}

ISSUER_IMPACT_TERMS = {
    "earnings", "financial results", "laporan keuangan", "laba", "rugi",
    "profit", "revenue", "pendapatan", "dividend", "dividen", "rights issue",
    "right issue", "buyback", "akuisisi", "acquisition", "merger", "divestasi",
    "divestment", "kontrak", "contract", "proyek", "project", "tender",
    "produksi", "production", "penjualan", "sales", "operasional", "operational",
    "ekspansi", "expansion", "capex", "utang", "debt", "default", "rating",
    "suspensi", "suspension", "uma", "management change", "direktur", "director",
    "komisaris", "commissioner", "regulatory", "regulasi", "izin", "license",
    "ipo", "private placement", "stock split", "reverse stock", "material transaction",
}


def _contains(text: str, terms: set[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _hit_count(text: str, terms: set[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _is_noise(headline: str, description: str) -> bool:
    combined = f"{headline} {description}".lower()
    return any(phrase in combined for phrase in NOISE_PHRASES)


def _source_label(result: dict[str, Any]) -> str:
    url = str(result.get("url", "") or "")
    domain = base._domain(url)
    for known in sorted(SOURCE_LABELS, key=len, reverse=True):
        if domain == known or domain.endswith("." + known):
            return SOURCE_LABELS[known]

    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    generic = {"market", "news", "business", "finance", "saham", "money"}
    for key in ("long_name", "name"):
        value = str(profile.get(key, "") or "").strip()
        if value and value.lower() not in generic:
            return value

    if domain:
        labels = domain.split(".")
        base_name = labels[-2] if len(labels) >= 2 else labels[0]
        return base_name.replace("-", " ").title()
    return "Unknown Source"


def strict_normalize_result(
    result: dict[str, Any], *, scope: str, symbols: list[str]
) -> base.NewsItem | None:
    headline = str(result.get("title", "") or "").strip()
    url = str(result.get("url", "") or "").strip()
    description = str(result.get("description", "") or "").strip()
    if not headline or not url or base._blocked_source(url):
        return None
    if _is_noise(headline, description):
        return None

    combined = f"{headline} {description}"
    symbol = base._extract_symbol(combined, symbols) if scope == "ISSUER" else ""
    impact_hits = _hit_count(combined, MARKET_IMPACT_TERMS)
    event_hits = _hit_count(combined, MOVEMENT_EVENT_TERMS)
    source_score = base._source_score(url)

    if scope == "GLOBAL":
        if not base._contains_any(combined, base.GLOBAL_KEYWORDS):
            return None
        if _contains(combined, POLITICAL_TERMS) and impact_hits < 2:
            return None
        if impact_hits < 1:
            return None
        if source_score <= 1 and (impact_hits + event_hits) < 3:
            return None

    elif scope == "INDONESIA":
        if not _contains(combined, INDONESIA_MARKET_ANCHORS):
            return None
        if impact_hits < 1 and event_hits < 1:
            return None

    elif scope == "SECTOR":
        if not base._contains_any(combined, base.SECTOR_KEYWORDS):
            return None
        if impact_hits < 1 or event_hits < 1:
            return None

    elif scope == "ISSUER":
        if symbols and not symbol:
            return None
        if not _contains(combined, ISSUER_IMPACT_TERMS):
            return None
    else:
        return None

    age = str(result.get("age", "") or "").strip()
    page_age = str(result.get("page_age", "") or result.get("published_at", "") or "").strip()
    score = float(source_score)
    score += min(3.0, float(impact_hits) * 0.35)
    score += min(2.0, float(event_hits) * 0.40)
    score += 1.0 if len(headline) >= 30 else 0.0
    if scope == "ISSUER" and symbol:
        score += 2.0

    return base.NewsItem(
        headline=headline,
        source=_source_label(result),
        url=url,
        published_at=page_age,
        age=age,
        scope=scope,
        category=scope,
        symbol=symbol,
        score=score,
    )


def market_query_plan(symbols: list[str]) -> list[dict[str, str]]:
    plans = [
        {
            "scope": "GLOBAL",
            "query": "Federal Reserve Wall Street Treasury yields dollar oil gold China economy inflation tariffs stocks markets",
        },
        {
            "scope": "INDONESIA",
            "query": "IHSG rupiah Bank Indonesia suku bunga inflasi OJK BEI saham obligasi pasar modal Indonesia",
        },
        {
            "scope": "SECTOR",
            "query": "saham Indonesia energi bank tambang batubara nikel minyak emas teknologi properti infrastruktur komoditas",
        },
    ]
    if symbols:
        joined = " ".join(symbols[:12])
        plans.append(
            {
                "scope": "ISSUER",
                "query": f"{joined} IDX earnings laba dividen rights issue buyback akuisisi kontrak proyek produksi corporate action",
            }
        )
    return plans


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _subtitle(session: str, generated_at: datetime) -> str:
    if session == "morning":
        if generated_at.weekday() >= 5 or generated_at.hour >= 10:
            return "🧪 <b>Manual / Off-Hours News Run</b>"
        return "🌅 <b>Overnight &amp; Pre-Market Brief</b>"
    if generated_at.hour < 15:
        return "🧪 <b>Manual / Off-Hours News Run</b>"
    return "🌆 <b>Daily Market News Digest</b>"


def format_market_digest(
    session: str, items: list[base.NewsItem], generated_at: datetime | None = None
) -> str:
    generated_at = generated_at or base.now_wib()
    if session == "morning":
        title = "📰 <b>SDE SWING — MORNING NEWS</b>"
    else:
        title = "📰 <b>SDE SWING — POST MARKET NEWS</b>"

    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 {generated_at.strftime('%d %b %Y')} | {generated_at.strftime('%H:%M')} WIB",
        _subtitle(session, generated_at),
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    sections = [
        ("GLOBAL", "🌍 <b>GLOBAL MARKET</b>"),
        ("INDONESIA", "🇮🇩 <b>INDONESIA MARKET</b>"),
        ("SECTOR", "🏭 <b>SECTOR WATCH</b>"),
        ("ISSUER", "📌 <b>EMITEN TERPANTAU</b>"),
    ]

    for scope, heading in sections:
        group = [item for item in items if item.scope == scope]
        if not group:
            continue
        lines.extend(["", heading, ""])
        for item in group:
            headline = item.headline
            if scope == "ISSUER" and item.symbol:
                prefix = f"{item.symbol} — "
                if not headline.upper().startswith(item.symbol.upper()):
                    headline = prefix + headline
            lines.append(f"◆ <b>{_esc(headline)}</b>")
            lines.append("")
            time_text = base._item_time(item)
            source_line = _esc(item.source)
            if time_text:
                source_line += f" | {_esc(time_text)}"
            lines.append(source_line)
            lines.append(f"🔗 Baca: {_esc(item.url)}")
            lines.append("")

    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━━━",
            f"📊 <b>{len(items)} berita berdampak terhadap market</b>",
            "",
            "ℹ️ News hanya informasi dan tidak memengaruhi keputusan SDE.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"</?b>", "", text))


def send_market_existing(session: str, *, force: bool = False) -> int:
    if base.requests is None:
        print("[WARNING] News Telegram dilewati: dependency requests belum terpasang.")
        return 2
    path, text = base._existing_text(session)
    if path is None or not text.strip():
        print(f"[WARNING] Belum ada output {session} news untuk dikirim.")
        return 2

    json_path = path.with_suffix(".json")
    payload = base.load_json(json_path)
    rows = payload.get("items") if isinstance(payload, dict) else None
    if isinstance(rows, list) and len(rows) == 0:
        print(f"[SKIPPED] Tidak ada berita berdampak untuk {session}; Telegram tidak dikirim.")
        return 0

    token, chat_id = base.telegram_credentials()
    if not token or not chat_id:
        print("[WARNING] News Telegram dilewati: token/chat_id belum dikonfigurasi.")
        return 2

    report_type = base.SESSION_REPORT_TYPE[session]
    thread_id = base._effective_news_topic(report_type)
    if not thread_id:
        print("[WARNING] News Telegram dilewati: topic NEWS belum dikonfigurasi.")
        return 2

    state = base.load_json(base.DEFAULT_STATE)
    sig = base._signature(text)
    prior = state.get(session, {}) if isinstance(state.get(session, {}), dict) else {}
    if not force and prior.get("signature") == sig:
        print(f"[SKIPPED] {session} news sudah pernah dikirim dengan isi yang sama.")
        return 0

    use_html = "<b>" in text
    message_ids: list[Any] = []
    try:
        for part in base._split_text(text):
            data: dict[str, Any] = {
                "chat_id": chat_id,
                "message_thread_id": thread_id,
                "text": part,
                "disable_web_page_preview": "true",
            }
            if use_html:
                data["parse_mode"] = "HTML"
            response = base.requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=data,
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
        "sent_at": base.now_wib().isoformat(timespec="seconds"),
        "thread_id": thread_id,
        "message_ids": message_ids,
        "source_path": str(path),
    }
    base.write_json_atomic(base.DEFAULT_STATE, state)
    print(f"[OK] {session} news terkirim ke topic {thread_id}.")
    return 0


def run_market_session(session: str, *, send: bool = False) -> int:
    scheduler = base.load_json(base.DEFAULT_SCHEDULER)
    config = scheduler.get("news_monitor", {}) if isinstance(scheduler.get("news_monitor", {}), dict) else {}
    if config.get("enabled", True) is False:
        print("[SKIPPED] News Monitor disabled.")
        return 0

    try:
        items, meta = base.collect_news(session, scheduler)
        text_path, json_path, text = base.save_digest(session, items, meta)
    except Exception as exc:
        print(f"[WARNING] News Monitor gagal: {exc}")
        return 2

    print(_strip_html(text))
    print(f"[OK] Preview: {text_path}")
    print(f"[OK] Metadata: {json_path}")
    if meta.get("errors"):
        for error in meta["errors"]:
            print(f"[WARNING] {error}")

    if send:
        send_rc = send_market_existing(session)
        if send_rc != 0:
            return 2
    return 0


def preview_market_existing(session: str) -> int:
    path, text = base._existing_text(session)
    if path is None or not text.strip():
        print(f"[WARNING] Belum ada preview {session} news.")
        return 2
    print(_strip_html(text))
    print(f"Preview source: {path}")
    return 0


def install_overrides() -> None:
    base._source_name = _source_label
    base.normalize_result = strict_normalize_result
    base.query_plan = market_query_plan
    base.format_digest = format_market_digest
    base.send_existing = send_market_existing
    base.run_session = run_market_session
    base.preview_existing = preview_market_existing


def main() -> int:
    install_overrides()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
