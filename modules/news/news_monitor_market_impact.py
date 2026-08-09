#!/usr/bin/env python3
from __future__ import annotations

"""Strict market-impact presentation/filter layer for SDE Swing News Monitor.

The stable base News Monitor remains untouched and usable as a fallback. This
module broadens event coverage while keeping Telegram selective. It never writes
to Technical, Broker, Decision, or Portfolio Management engine state.
"""

import html
import re
import sys
from datetime import datetime, timedelta
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
    "abc.net.au": "ABC News",
}

# Tier A/B can enter normal market reports. Unknown specialist sources are
# conditional and only survive for material Indonesia/issuer events.
SOURCE_TIER_A = {
    "reuters.com", "bloomberg.com", "cnbc.com", "cnbcindonesia.com",
    "ft.com", "wsj.com", "apnews.com", "idx.co.id", "ojk.go.id", "bi.go.id",
    "antaranews.com", "bisnis.com", "kontan.co.id",
}
SOURCE_TIER_B = {
    "investor.id", "economictimes.indiatimes.com", "abc.net.au",
    "liputan6.com", "timesofindia.indiatimes.com",
}
SOURCE_REJECT = {
    "seekingalpha.com", "cryptobriefing.com", "nzcity.co.nz",
    "cointelegraph.com", "coingape.com",
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
    "msci", "ftse", "rebalancing", "passive flow", "foreign flow",
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
    "below forecast", "katalis", "sentiment", "sentimen", "wins", "won",
    "menang", "ditunjuk", "ditetapkan", "effective", "berlaku",
}

INDONESIA_MARKET_ANCHORS = {
    "indonesia", "ihsg", "idx", "bei", "bursa efek indonesia", "pasar saham",
    "pasar modal", "saham indonesia", "rupiah", "bank indonesia", "bi rate",
    "suku bunga", "inflasi", "obligasi", "yield", "ojk", "foreign flow",
    "asing", "jakarta", "kementerian", "pemerintah indonesia",
}

INDEX_REBALANCING_TERMS = {
    "msci", "ftse", "ftse russell", "lq45", "idx30", "idx80",
    "index review", "index rebalance", "index rebalancing", "rebalancing",
    "rebalance", "inclusion", "exclusion", "index constituent",
    "constituent change", "constituent changes", "index weight",
    "weight change", "effective date", "free float", "free-float",
}
CORPORATE_ACTION_TERMS = {
    "rights issue", "right issue", "private placement", "buyback",
    "share buyback", "stock split", "reverse split", "reverse stock",
    "tender offer", "mandatory tender offer", "special dividend",
    "dividen khusus", "dividend", "dividen", "bonus shares", "saham bonus",
}
EARNINGS_GUIDANCE_TERMS = {
    "earnings", "financial results", "laporan keuangan", "net profit",
    "laba bersih", "laba", "rugi", "loss", "revenue", "pendapatan",
    "margin", "ebitda", "guidance", "outlook", "target laba",
    "target pendapatan", "earnings surprise", "profit warning",
}
CONTRACT_PROJECT_TERMS = {
    "contract", "kontrak", "tender", "wins contract", "won contract",
    "menang tender", "proyek", "project", "order book", "orderbook",
    "backlog", "ekspansi kapasitas", "capacity expansion",
    "new plant", "pabrik baru", "smelter", "commercial operation",
}
MNA_OWNERSHIP_TERMS = {
    "acquisition", "akuisisi", "merger", "divestment", "divestasi",
    "strategic investor", "investor strategis", "controlling shareholder",
    "pemegang saham pengendali", "change of control", "perubahan pengendali",
    "takeover", "pengambilalihan", "stake sale", "jual saham",
}
REGULATION_POLICY_TERMS = {
    "royalty", "royalti", "dmo", "dhe", "pajak", "tax", "tariff", "tarif",
    "subsidi", "subsidy", "regulation", "regulasi", "aturan ojk", "aturan bei",
    "aturan bi", "bank indonesia regulation", "export ban", "larangan ekspor",
    "export quota", "kuota ekspor", "import quota", "kuota impor",
    "minimum free float", "free float rule", "kebijakan pemerintah",
}
EXCHANGE_EVENT_TERMS = {
    "suspension", "suspensi", "suspend", "unsuspend", "unsuspension",
    "dibuka kembali", "uma", "unusual market activity", "forced delisting",
    "delisting", "relisting", "relist", "free float", "free-float",
    "trading halt", "penghentian sementara",
}
COMMODITY_CATALYST_TERMS = {
    "coal", "batubara", "nickel", "nikel", "gold", "emas", "cpo",
    "palm oil", "minyak sawit", "crude oil", "oil", "minyak",
    "copper", "tembaga", "tin", "timah", "lng", "gas alam",
    "commodity price", "harga komoditas",
}
FUNDING_DEBT_TERMS = {
    "bond", "bonds", "obligasi", "sukuk", "refinancing", "refinance",
    "refinancing debt", "debt refinancing", "gagal bayar", "default",
    "covenant", "credit rating", "rating upgrade", "rating downgrade",
    "downgrade rating", "upgrade rating", "maturity", "jatuh tempo utang",
    "debt restructuring", "restrukturisasi utang",
}
OPERATIONAL_EVENT_TERMS = {
    "fire", "kebakaran", "explosion", "ledakan", "mine shutdown",
    "tambang berhenti", "shutdown", "production halt", "stop production",
    "gangguan produksi", "force majeure", "operational disruption",
    "gangguan operasional", "permit revoked", "izin dicabut",
    "permit extended", "izin diperpanjang", "license revoked",
    "accident", "kecelakaan", "flood", "banjir",
}
FOREIGN_PASSIVE_FLOW_TERMS = {
    "passive fund", "passive flow", "passive inflow", "passive outflow",
    "foreign flow", "foreign inflow", "foreign outflow", "foreign ownership",
    "kepemilikan asing", "net foreign buy", "net foreign sell",
    "block trade", "crossing", "index fund", "etf flow",
}
MANAGEMENT_DISCLOSURE_TERMS = {
    "ceo", "chief executive", "president director", "direktur utama",
    "director", "direktur", "commissioner", "komisaris", "management change",
    "pergantian manajemen", "resignation", "mengundurkan diri",
    "material transaction", "transaksi material", "affiliate transaction",
    "transaksi afiliasi", "related party transaction",
}
MACRO_INDONESIA_TERMS = {
    "bi rate", "bank indonesia", "rupiah", "inflasi", "inflation indonesia",
    "trade balance", "neraca perdagangan", "gdp indonesia", "pdb indonesia",
    "bond yield indonesia", "yield obligasi", "sbn", "foreign flow ihsg",
    "foreign flow", "cadangan devisa", "current account", "neraca berjalan",
    "consumer confidence indonesia", "pmi indonesia",
}
GLOBAL_CATALYST_TERMS = {
    "federal reserve", "fed", "us cpi", "cpi", "payroll", "nonfarm payroll",
    "jobs report", "treasury yield", "treasury yields", "dollar index", "dxy",
    "china stimulus", "china economy", "beijing stimulus", "tariff",
    "tariffs", "trade war", "geopolit", "sanction", "sanksi",
    "oil", "crude", "gold", "commodity", "commodities", "wall street",
    "nasdaq", "s&p", "dow",
}

EVENT_TERMS: dict[str, set[str]] = {
    "INDEX_REBALANCING": INDEX_REBALANCING_TERMS,
    "CORPORATE_ACTION": CORPORATE_ACTION_TERMS,
    "EARNINGS_GUIDANCE": EARNINGS_GUIDANCE_TERMS,
    "CONTRACT_PROJECT": CONTRACT_PROJECT_TERMS,
    "MNA_OWNERSHIP": MNA_OWNERSHIP_TERMS,
    "REGULATION_POLICY": REGULATION_POLICY_TERMS,
    "EXCHANGE_EVENT": EXCHANGE_EVENT_TERMS,
    "COMMODITY_CATALYST": COMMODITY_CATALYST_TERMS,
    "FUNDING_DEBT": FUNDING_DEBT_TERMS,
    "OPERATIONAL_EVENT": OPERATIONAL_EVENT_TERMS,
    "FOREIGN_PASSIVE_FLOW": FOREIGN_PASSIVE_FLOW_TERMS,
    "MANAGEMENT_DISCLOSURE": MANAGEMENT_DISCLOSURE_TERMS,
    "MACRO_INDONESIA": MACRO_INDONESIA_TERMS,
    "GLOBAL_CATALYST": GLOBAL_CATALYST_TERMS,
}

CATEGORY_PRIORITY = {
    "INDEX_REBALANCING": 2.8,
    "EXCHANGE_EVENT": 2.7,
    "FUNDING_DEBT": 2.5,
    "OPERATIONAL_EVENT": 2.5,
    "CORPORATE_ACTION": 2.4,
    "EARNINGS_GUIDANCE": 2.4,
    "CONTRACT_PROJECT": 2.4,
    "MNA_OWNERSHIP": 2.4,
    "REGULATION_POLICY": 2.3,
    "FOREIGN_PASSIVE_FLOW": 2.2,
    "COMMODITY_CATALYST": 2.0,
    "MACRO_INDONESIA": 2.0,
    "MANAGEMENT_DISCLOSURE": 1.8,
    "GLOBAL_CATALYST": 1.6,
}

DISPLAY_SCOPES = ("GLOBAL", "INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK", "ISSUER")
SCOPE_ALLOWED_CATEGORIES = {
    "GLOBAL": {"GLOBAL_CATALYST"},
    "INDONESIA": {"MACRO_INDONESIA", "REGULATION_POLICY"},
    "INDEX": {"INDEX_REBALANCING", "FOREIGN_PASSIVE_FLOW"},
    "SECTOR": {"COMMODITY_CATALYST"},
    "CORPORATE": {
        "CORPORATE_ACTION", "EARNINGS_GUIDANCE", "CONTRACT_PROJECT",
        "MNA_OWNERSHIP", "MANAGEMENT_DISCLOSURE",
    },
    "RISK": {"EXCHANGE_EVENT", "FUNDING_DEBT", "OPERATIONAL_EVENT"},
    "ISSUER": set(EVENT_TERMS),
}

# Long lookbacks are used only to find material events that can remain relevant
# for a swing horizon. Telegram output is still capped at 10 and sent-history
# suppression prevents old items from repeating.
SCOPE_LOOKBACK_DAYS = {
    "GLOBAL": 1,
    "INDONESIA": 1,
    "INDEX": 7,
    "SECTOR": 1,
    "CORPORATE": 5,
    "RISK": 3,
    "ISSUER": 3,
}

TITLE_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "at", "as",
    "is", "are", "was", "were", "after", "before", "with", "from", "by", "over",
    "this", "that", "these", "those", "market", "markets", "news", "update",
    "indonesia", "indonesian", "saham", "stock", "stocks", "idx",
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


def _domain_matches(domain: str, candidates: set[str]) -> bool:
    return any(domain == item or domain.endswith("." + item) for item in candidates)


def _source_tier(url: str) -> str:
    domain = base._domain(url)
    if not domain or _domain_matches(domain, SOURCE_REJECT):
        return "REJECT"
    if _domain_matches(domain, SOURCE_TIER_A):
        return "A"
    if _domain_matches(domain, SOURCE_TIER_B):
        return "B"
    return "C"


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


def _detect_event_category(text: str, *, allowed: set[str] | None = None) -> tuple[str, int]:
    candidates = allowed if allowed is not None else set(EVENT_TERMS)
    scored: list[tuple[int, float, str]] = []
    for category in candidates:
        hits = _hit_count(text, EVENT_TERMS[category])
        if hits:
            scored.append((hits, CATEGORY_PRIORITY.get(category, 0.0), category))
    if not scored:
        return "", 0
    scored.sort(reverse=True)
    hits, _, category = scored[0]
    return category, hits


def _indonesia_relevant(text: str, symbol: str = "") -> bool:
    return bool(symbol) or _contains(text, INDONESIA_MARKET_ANCHORS)


def _source_allowed(
    *,
    url: str,
    scope: str,
    category_hits: int,
    indonesia_relevant: bool,
    symbol: str,
) -> bool:
    tier = _source_tier(url)
    if tier == "REJECT":
        return False
    if tier in {"A", "B"}:
        return True
    # Unknown/specialist sources are not accepted for broad macro/commodity
    # coverage. For material local/issuer events, require stronger evidence.
    return (
        scope in {"INDEX", "CORPORATE", "RISK", "ISSUER"}
        and indonesia_relevant
        and category_hits >= 2
        and (bool(symbol) or scope != "ISSUER")
    )


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

    scope = str(scope or "").upper()
    if scope not in DISPLAY_SCOPES:
        return None

    combined = f"{headline} {description}"
    symbol = base._extract_symbol(combined, symbols)
    impact_hits = _hit_count(combined, MARKET_IMPACT_TERMS)
    movement_hits = _hit_count(combined, MOVEMENT_EVENT_TERMS)
    source_score = base._source_score(url)
    indonesia_relevant = _indonesia_relevant(combined, symbol)

    category, category_hits = _detect_event_category(
        combined,
        allowed=SCOPE_ALLOWED_CATEGORIES.get(scope),
    )
    if not category:
        return None
    if not _source_allowed(
        url=url,
        scope=scope,
        category_hits=category_hits,
        indonesia_relevant=indonesia_relevant,
        symbol=symbol,
    ):
        return None

    if scope == "GLOBAL":
        if impact_hits < 1:
            return None
        if _contains(combined, POLITICAL_TERMS):
            pathway_terms = (
                MARKET_IMPACT_TERMS
                | COMMODITY_CATALYST_TERMS
                | {"tariff", "tariffs", "trade war", "yield", "dollar", "stocks", "equities"}
            )
            if _hit_count(combined, pathway_terms) < 2:
                return None

    elif scope in {"INDONESIA", "INDEX", "SECTOR", "CORPORATE", "RISK"}:
        if not indonesia_relevant:
            return None
        if scope == "SECTOR" and movement_hits < 1 and impact_hits < 1 and category_hits < 2:
            return None

    elif scope == "ISSUER":
        if symbols and not symbol:
            return None
        if not symbol:
            return None

    tier_bonus = {"A": 1.5, "B": 0.6, "C": 0.0}.get(_source_tier(url), 0.0)
    score = float(source_score) + tier_bonus
    score += CATEGORY_PRIORITY.get(category, 0.0)
    score += min(3.0, float(impact_hits) * 0.30)
    score += min(2.0, float(movement_hits) * 0.35)
    score += min(1.5, float(category_hits) * 0.30)
    score += 1.0 if len(headline) >= 30 else 0.0
    if symbol:
        score += 2.0
    if scope == "INDEX":
        score += 0.5

    age = str(result.get("age", "") or "").strip()
    page_age = str(result.get("page_age", "") or result.get("published_at", "") or "").strip()

    return base.NewsItem(
        headline=headline,
        source=_source_label(result),
        url=url,
        published_at=page_age,
        age=age,
        scope=scope,
        category=category,
        symbol=symbol,
        score=score,
    )


def market_query_plan(symbols: list[str]) -> list[dict[str, str]]:
    plans = [
        {
            "scope": "GLOBAL",
            "query": (
                "Federal Reserve Fed US CPI jobs payroll Treasury yields dollar DXY "
                "China stimulus tariffs trade war oil gold commodities Wall Street stocks markets"
            ),
        },
        {
            "scope": "INDONESIA",
            "query": (
                "Indonesia IHSG rupiah Bank Indonesia BI Rate inflation trade balance GDP "
                "SBN bond yield OJK government policy regulation DMO DHE royalty tax subsidy foreign flow"
            ),
        },
        {
            "scope": "INDEX",
            "query": (
                "MSCI Indonesia FTSE Russell LQ45 IDX30 IDX80 index review rebalancing "
                "inclusion exclusion index weight free float passive fund foreign flow IDX stocks"
            ),
        },
        {
            "scope": "SECTOR",
            "query": (
                "Indonesia stocks coal nickel gold CPO crude oil copper tin commodity prices "
                "production export policy mining energy palm oil IDX"
            ),
        },
        {
            "scope": "CORPORATE",
            "query": (
                "IDX Indonesia rights issue private placement buyback stock split dividend earnings "
                "profit revenue guidance contract tender project order book acquisition merger "
                "strategic investor controlling shareholder director material transaction"
            ),
        },
        {
            "scope": "RISK",
            "query": (
                "IDX Indonesia suspension UMA delisting relisting bond refinancing default covenant "
                "rating downgrade fire mine shutdown force majeure production halt permit revoked"
            ),
        },
    ]
    if symbols:
        joined = " ".join(symbols[:12])
        plans.append(
            {
                "scope": "ISSUER",
                "query": (
                    f"{joined} IDX MSCI FTSE earnings laba guidance dividend rights issue buyback "
                    "private placement contract tender project acquisition merger investor shareholder "
                    "suspension UMA bond default rating fire shutdown force majeure commodity "
                    "foreign flow management director material transaction"
                ),
            }
        )
    return plans


def _freshness_for_scope(
    session: str,
    scope: str,
    config: dict[str, Any],
    current: datetime,
) -> str:
    days = SCOPE_LOOKBACK_DAYS.get(scope, 1)
    if days <= 1:
        return base._resolve_freshness(session, config, current)
    start = (current.date() - timedelta(days=days - 1)).isoformat()
    end = current.date().isoformat()
    return f"{start}to{end}"


def _canonical_url(url: str) -> str:
    return str(url or "").split("#", 1)[0].split("?", 1)[0].rstrip("/")


def _title_tokens(title: str) -> set[str]:
    text = re.sub(r"\s+-\s+[^-]{2,40}$", " ", str(title or "").lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return {
        token
        for token in text.split()
        if len(token) > 2 and token not in TITLE_STOPWORDS
    }


def _title_similarity(left: str, right: str) -> float:
    a = _title_tokens(left)
    b = _title_tokens(right)
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / max(len(a | b), 1)
    containment = len(a & b) / max(min(len(a), len(b)), 1)
    return max(jaccard, containment * 0.85)


def _same_story(left: base.NewsItem, right: base.NewsItem) -> bool:
    if _canonical_url(left.url) == _canonical_url(right.url):
        return True
    similarity = _title_similarity(left.headline, right.headline)
    same_category = left.category == right.category
    same_symbol = bool(left.symbol and right.symbol and left.symbol == right.symbol)
    return similarity >= 0.62 or (similarity >= 0.50 and (same_category or same_symbol))


def _dedupe_market_items(items: list[base.NewsItem]) -> list[base.NewsItem]:
    kept: list[base.NewsItem] = []
    for item in sorted(items, key=lambda row: row.score, reverse=True):
        if any(_same_story(item, existing) for existing in kept):
            continue
        kept.append(item)
    return kept


def _seen_rows() -> list[dict[str, Any]]:
    state = base.load_json(base.DEFAULT_STATE)
    rows = state.get("news_seen_items", []) if isinstance(state, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _already_sent(item: base.NewsItem, seen: list[dict[str, Any]]) -> bool:
    canonical = _canonical_url(item.url)
    for row in seen:
        if canonical and canonical == _canonical_url(str(row.get("url") or "")):
            return True
        prior_title = str(row.get("headline") or "")
        if not prior_title:
            continue
        same_category = str(row.get("category") or "") == str(item.category or "")
        same_symbol = bool(
            item.symbol
            and str(row.get("symbol") or "").upper() == str(item.symbol).upper()
        )
        similarity = _title_similarity(item.headline, prior_title)
        if similarity >= 0.65 or (similarity >= 0.52 and (same_category or same_symbol)):
            return True
    return False


def _limit_market_items(items: list[base.NewsItem], maximum: int) -> list[base.NewsItem]:
    # User-facing hard cap: broad search coverage must never turn Telegram into
    # a noisy feed.
    maximum = min(max(int(maximum or 1), 1), 10)
    caps = {
        "ISSUER": 4,
        "INDEX": 3,
        "INDONESIA": 2,
        "GLOBAL": 2,
        "CORPORATE": 3,
        "RISK": 2,
        "SECTOR": 2,
    }
    priority = ("ISSUER", "INDEX", "RISK", "CORPORATE", "INDONESIA", "SECTOR", "GLOBAL")
    grouped = {
        scope: sorted(
            [item for item in items if item.scope == scope],
            key=lambda item: item.score,
            reverse=True,
        )
        for scope in DISPLAY_SCOPES
    }

    selected: list[base.NewsItem] = []
    selected_urls: set[str] = set()
    scope_counts = {scope: 0 for scope in DISPLAY_SCOPES}

    # First pass preserves event breadth, but only after quality filtering.
    for scope in priority:
        if len(selected) >= maximum:
            break
        if grouped.get(scope):
            item = grouped[scope][0]
            selected.append(item)
            selected_urls.add(_canonical_url(item.url))
            scope_counts[scope] += 1

    # Fill remaining slots by relevance score while respecting per-scope caps.
    for item in sorted(items, key=lambda row: row.score, reverse=True):
        if len(selected) >= maximum:
            break
        canonical = _canonical_url(item.url)
        if canonical in selected_urls:
            continue
        if item.scope not in caps or scope_counts[item.scope] >= caps[item.scope]:
            continue
        selected.append(item)
        selected_urls.add(canonical)
        scope_counts[item.scope] += 1

    return sorted(selected, key=lambda item: item.score, reverse=True)


def collect_market_news(
    session: str,
    scheduler: dict[str, Any] | None = None,
) -> tuple[list[base.NewsItem], dict[str, Any]]:
    if session not in base.SESSION_REPORT_TYPE:
        raise ValueError(f"Unknown news session: {session}")

    scheduler = scheduler or base.load_json(base.DEFAULT_SCHEDULER)
    config = (
        scheduler.get("news_monitor", {})
        if isinstance(scheduler.get("news_monitor", {}), dict)
        else {}
    )
    timeout = int(config.get("request_timeout_seconds", 20) or 20)
    count = int(config.get("results_per_query", 12) or 12)
    maximum = min(int(config.get("max_total_items", 10) or 10), 10)
    current = base.now_wib()
    symbols = base.monitored_symbols()
    plans = market_query_plan(symbols)

    raw_count = 0
    normalized: list[base.NewsItem] = []
    errors: list[str] = []
    freshness_by_scope: dict[str, str] = {}

    for plan in plans:
        scope = plan["scope"]
        freshness = _freshness_for_scope(session, scope, config, current)
        freshness_by_scope[scope] = freshness
        try:
            rows = base._brave_search(
                query=plan["query"],
                freshness=freshness,
                count=count,
                timeout=timeout,
            )
        except Exception as exc:
            errors.append(f"{scope}: {exc}")
            continue
        raw_count += len(rows)
        for row in rows:
            item = strict_normalize_result(row, scope=scope, symbols=symbols)
            if item is not None:
                normalized.append(item)

    deduped = _dedupe_market_items(normalized)
    seen = _seen_rows()
    unseen = [item for item in deduped if not _already_sent(item, seen)]

    # Keep the existing morning/post-market separation too. Sent-history is
    # stricter, while this covers preview-only morning runs on the same day.
    if session == "post_market":
        unseen = base._remove_morning_duplicates(unseen, current.date().isoformat())

    selected = _limit_market_items(unseen, maximum)
    meta = {
        "session": session,
        "provider": "BRAVE_NEWS_SEARCH",
        "generated_at": current.isoformat(timespec="seconds"),
        "freshness_by_scope": freshness_by_scope,
        "monitored_symbols": symbols,
        "queries": len(plans),
        "raw_results": raw_count,
        "normalized_results": len(normalized),
        "deduplicated_results": len(deduped),
        "already_sent_suppressed": max(0, len(deduped) - len(unseen)),
        "displayed_results": len(selected),
        "max_total_items": maximum,
        "errors": errors,
        "decision_engine_write_access": False,
    }
    return selected, meta


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


def _category_label(category: str) -> str:
    labels = {
        "INDEX_REBALANCING": "Index / Rebalancing",
        "CORPORATE_ACTION": "Corporate Action",
        "EARNINGS_GUIDANCE": "Earnings / Guidance",
        "CONTRACT_PROJECT": "Contract / Project",
        "MNA_OWNERSHIP": "M&A / Ownership",
        "REGULATION_POLICY": "Regulation / Policy",
        "EXCHANGE_EVENT": "Exchange Event",
        "COMMODITY_CATALYST": "Commodity Catalyst",
        "FUNDING_DEBT": "Funding / Debt",
        "OPERATIONAL_EVENT": "Operational Event",
        "FOREIGN_PASSIVE_FLOW": "Foreign / Passive Flow",
        "MANAGEMENT_DISCLOSURE": "Management / Disclosure",
        "MACRO_INDONESIA": "Macro Indonesia",
        "GLOBAL_CATALYST": "Global Catalyst",
    }
    return labels.get(
        str(category or "").upper(),
        str(category or "").replace("_", " ").title(),
    )


def format_market_digest(
    session: str,
    items: list[base.NewsItem],
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or base.now_wib()
    title = (
        "📰 <b>SDE SWING — MORNING NEWS</b>"
        if session == "morning"
        else "📰 <b>SDE SWING — POST MARKET NEWS</b>"
    )
    lines = [
        title,
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 {generated_at.strftime('%d %b %Y')} | {generated_at.strftime('%H:%M')} WIB",
        _subtitle(session, generated_at),
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    sections = [
        ("GLOBAL", "🌍 <b>GLOBAL &amp; MACRO</b>"),
        ("INDONESIA", "🇮🇩 <b>INDONESIA MARKET</b>"),
        ("INDEX", "📊 <b>INDEX &amp; REBALANCING</b>"),
        ("SECTOR", "🏭 <b>SECTOR &amp; COMMODITY</b>"),
        ("CORPORATE", "🏢 <b>CORPORATE EVENTS</b>"),
        ("RISK", "⚠️ <b>RISK &amp; EXCHANGE</b>"),
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
            lines.append(f"🏷 {_esc(_category_label(item.category))}")
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


def _remember_sent_rows(
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    sent_at: str,
) -> None:
    existing = state.get("news_seen_items", [])
    seen = [row for row in existing if isinstance(row, dict)] if isinstance(existing, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = {
            "url": _canonical_url(str(row.get("url") or "")),
            "headline": str(row.get("headline") or ""),
            "category": str(row.get("category") or ""),
            "symbol": str(row.get("symbol") or ""),
            "sent_at": sent_at,
        }
        if not candidate["url"] and not candidate["headline"]:
            continue
        seen = [
            old
            for old in seen
            if not (
                candidate["url"]
                and candidate["url"] == _canonical_url(str(old.get("url") or ""))
            )
        ]
        seen.append(candidate)
    state["news_seen_items"] = seen[-300:]


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

    sent_at = base.now_wib().isoformat(timespec="seconds")
    state[session] = {
        "signature": sig,
        "sent_at": sent_at,
        "thread_id": thread_id,
        "message_ids": message_ids,
        "source_path": str(path),
    }
    if isinstance(rows, list):
        _remember_sent_rows(state, rows, sent_at=sent_at)
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
        items, meta = collect_market_news(session, scheduler)
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
    base._limit_items = _limit_market_items
    base.collect_news = collect_market_news
    base.format_digest = format_market_digest
    base.send_existing = send_market_existing
    base.run_session = run_market_session
    base.preview_existing = preview_market_existing


def main() -> int:
    install_overrides()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
