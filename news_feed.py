# FILE: news_feed.py
# Bloomberg Macro Terminal — RSS News Feed
# Real-time headlines from free RSS feeds.
# Fixed: date parsing and chronological sorting.

import time
import logging
import requests
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────
CACHE_TTL       = 120
MAX_ARTICLES    = 20
REQUEST_TIMEOUT = 10

# ── CACHE ─────────────────────────────────────────────────────
_cache = {"data": None, "ts": 0}

def _cache_valid():
    return (
        _cache["data"] is not None and
        (time.time() - _cache["ts"]) < CACHE_TTL
    )

def _set_cache(data):
    _cache["data"] = data
    _cache["ts"]   = time.time()

# ── RSS FEEDS ─────────────────────────────────────────────────
RSS_FEEDS = [
    {
        "url":    "https://feeds.reuters.com/reuters/businessNews",
        "source": "Reuters",
    },
    {
        "url":    "https://feeds.reuters.com/reuters/topNews",
        "source": "Reuters",
    },
    {
        "url":    "https://www.marketwatch.com/rss/economy",
        "source": "MarketWatch",
    },
    {
        "url":    "https://www.marketwatch.com/rss/marketpulse",
        "source": "MarketWatch",
    },
    {
        "url":    "https://www.marketwatch.com/rss/realtimeheadlines",
        "source": "MarketWatch",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSOpinion.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://feeds.a.dj.com/rss/RSSWSJD.xml",
        "source": "WSJ",
    },
    {
        "url":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
        "source": "CNBC",
    },
    {
        "url":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
        "source": "CNBC Economy",
    },
    {
        "url":    "https://www.ft.com/rss/home",
        "source": "Financial Times",
    },
    {
        "url":    "https://feeds.ap.org/rss/APFinance",
        "source": "AP Finance",
    },
    {
        "url":    "https://www.axios.com/feeds/feed.rss",
        "source": "Axios",
    },
    {
        "url":    "https://feeds.bloomberg.com/markets/news.rss",
        "source": "Bloomberg",
    },
    {
        "url":    "https://www.federalreserve.gov/feeds/press_all.xml",
        "source": "Federal Reserve",
    },
    {
        "url":    "https://www.bls.gov/feed/bls_latest.rss",
        "source": "BLS",
    },
    {
        "url":    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
        "source": "MarketWatch RT",
    },
]

# ── RELEVANCE KEYWORDS ────────────────────────────────────────
KEYWORD_SCORES = {
    "federal reserve":  10,
    "fed rate":         10,
    "fomc":             10,
    "stagflation":      10,
    "yield curve":       9,
    "recession":         9,
    "inflation":         8,
    "cpi":               8,
    "pce":               8,
    "powell":            8,
    "rate hike":         8,
    "rate cut":          8,
    "treasury":          7,
    "10-year":           7,
    "credit":            7,
    "tariff":            7,
    "trade":             6,
    "gdp":               6,
    "unemployment":      6,
    "jobs":              6,
    "nonfarm":           6,
    "payroll":           6,
    "debt":              5,
    "deficit":           5,
    "fiscal":            5,
    "bank":              5,
    "lending":           5,
    "mortgage":          5,
    "housing":           5,
    "economic":          4,
    "market":            3,
    "stocks":            2,
    "s&p":               2,
    "nasdaq":            2,
    "wall street":       2,
    "financial":         2,
}

NOISE_TERMS = [
    "cryptocurrency", "bitcoin", "crypto", "nft",
    "celebrity", "sports", "entertainment", "gaming",
    "cannabis", "obituary", "weather", "horoscope",
    "personal finance", "best credit cards", "mortgage rates today",
    "how to save", "retirement tips", "budget", "coupon",
    "best savings account", "cd rates", "insurance",
    "real estate agent", "home buying tips", "car insurance",
    "student loans", "personal loan", "dating", "relationship",
    "health tips", "diet", "exercise", "celebrity net worth",
    "richest", "wealthiest", "billionaire list",
]

MIN_SCORE = 6

# ── DATE PARSER ───────────────────────────────────────────────
def _parse_date(pub_date):
    """
    Robustly parse any RSS date format.
    Returns (iso_string, datetime_object) tuple.
    Falls back to now if unparseable.
    """
    now = datetime.now(timezone.utc)

    if not pub_date or not pub_date.strip():
        return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now

    pub_date = pub_date.strip()

    # Method 1 — email.utils handles all RFC 2822 formats
    # This covers 99% of RSS feeds correctly
    try:
        dt = parsedate_to_datetime(pub_date)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt
    except Exception:
        pass

    # Method 2 — ISO 8601 variants
    iso_formats = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in iso_formats:
        try:
            dt = datetime.strptime(pub_date, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), dt
        except ValueError:
            continue

    # Fallback — use now but log it
    log.warning(f"Could not parse date: '{pub_date}' — using now")
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now


def _fmt_rel_time(dt):
    """
    Format datetime as relative time string.
    Returns e.g. '5MIN AGO', '2HR AGO', 'JUST NOW'
    """
    if dt is None:
        return "--"
    now  = datetime.now(timezone.utc)
    diff = int((now - dt).total_seconds())

    if diff < 0:
        return "JUST NOW"
    if diff < 60:
        return "JUST NOW"
    if diff < 3600:
        mins = diff // 60
        return f"{mins}MIN AGO"
    if diff < 86400:
        hrs = diff // 3600
        return f"{hrs}HR AGO"

    days = diff // 86400
    return f"{days}D AGO"


# ── RSS PARSER ────────────────────────────────────────────────
def _fetch_rss(feed):
    """Fetch and parse a single RSS feed."""
    headers = {
        "User-Agent": "Mozilla/5.0 MacroTerminal/1.0",
        "Accept":     "application/rss+xml, application/xml, text/xml",
    }

    resp = requests.get(feed["url"], headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    root    = ET.fromstring(resp.content)
    channel = root.find("channel")
    if channel is None:
        channel = root

    articles = []
    for item in channel.findall("item"):
        title    = item.findtext("title", "").strip()
        url      = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        desc     = item.findtext("description", "").strip()

        # Some feeds use atom:updated or dc:date
        if not pub_date:
            for tag in [
                "{http://purl.org/dc/elements/1.1/}date",
                "{http://www.w3.org/2005/Atom}updated",
                "{http://www.w3.org/2005/Atom}published",
            ]:
                val = item.findtext(tag, "").strip()
                if val:
                    pub_date = val
                    break

        if not title or not url:
            continue

        iso_ts, dt_obj = _parse_date(pub_date)
        rel_time       = _fmt_rel_time(dt_obj)

        articles.append({
            "title":       title,
            "headline":    title,
            "description": desc[:200] if desc else "",
            "source":      feed["source"],
            "url":         url,
            "publishedAt": iso_ts,
            "timestamp":   iso_ts,
            "relTime":     rel_time,
            "dt":          dt_obj,  # keep for sorting, removed before response
        })

    return articles


# ── SCORING ───────────────────────────────────────────────────
def _score_article(article):
    title = (article.get("title") or "").lower()
    desc  = (article.get("description") or "").lower()
    text  = title + " " + desc
    score = 0

    for keyword, weight in KEYWORD_SCORES.items():
        if keyword in title:
            score += weight * 2
        elif keyword in desc:
            score += weight

    for noise in NOISE_TERMS:
        if noise in text:
            score -= 15

    return max(0, score)

def _is_valid(article):
    return (
        article.get("title") and
        article.get("url") and
        article.get("title") != "[Removed]"
    )

def _deduplicate(articles):
    seen   = set()
    unique = []
    for a in articles:
        key = a["title"][:60].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


# ── FALLBACK ──────────────────────────────────────────────────
def _fallback(error=""):
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = datetime.now(timezone.utc)
    return {
        "articles": [{
            "title":       "NEWS FEED TEMPORARILY UNAVAILABLE",
            "headline":    "NEWS FEED TEMPORARILY UNAVAILABLE",
            "description": "RSS feeds could not be reached. Will retry shortly.",
            "source":      "SYSTEM",
            "url":         "#",
            "publishedAt": ts,
            "timestamp":   ts,
            "relTime":     "JUST NOW",
            "score":       0,
        }],
        "count":     0,
        "timestamp": ts,
        "cached":    False,
        "error":     error,
        "status":    "fallback",
    }


# ── MAIN ENTRY POINT ──────────────────────────────────────────
def get_news():
    """
    Fetch real-time macro headlines from free RSS feeds.
    Sorted by recency. Relative timestamps accurate.
    """
    if _cache_valid():
        log.info("News: returning cached RSS data.")
        cached = dict(_cache["data"])
        cached["cached"] = True
        return cached

    log.info("News: fetching fresh RSS feeds...")
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw    = []
    errors = []

    for feed in RSS_FEEDS:
        try:
            articles = _fetch_rss(feed)
            raw.extend(articles)
            log.info(f"News: {len(articles)} from {feed['source']}")
        except Exception as e:
            log.warning(f"RSS failed [{feed['source']}]: {e}")
            errors.append(f"{feed['source']}: {str(e)[:60]}")

    if not raw:
        error_msg = "; ".join(errors) if errors else "All RSS feeds failed."
        log.error(f"News: complete failure — {error_msg}")
        result = _fallback(error_msg)
        _set_cache(result)
        return result

    # Filter valid articles
    valid = [a for a in raw if _is_valid(a)]

    # Score for relevance
    scored = [(a, _score_article(a)) for a in valid]
    filtered = [(a, s) for a, s in scored if s >= MIN_SCORE]

    # Sort by recency first — most recent at top
    filtered.sort(key=lambda x: x[0].get("dt") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Add score, remove dt object before sending to frontend
    cleaned = []
    for a, s in filtered:
        article = {k: v for k, v in a.items() if k != "dt"}
        article["score"] = s
        cleaned.append(article)

    unique = _deduplicate(cleaned)
    final  = unique[:MAX_ARTICLES]

    status = "ok" if not errors else "partial"

    result = {
        "articles":  final,
        "count":     len(final),
        "timestamp": ts,
        "cached":    False,
        "error":     "; ".join(errors) if errors else None,
        "status":    status,
    }

    _set_cache(result)
    log.info(f"News: {len(final)} articles, status={status}.")
    return result


# ── STANDALONE TEST ───────────────────────────────────────────
if __name__ == "__main__":
    import json
    result = get_news()
    # Remove dt objects for clean print
    print(json.dumps(
        {k: v for k, v in result.items()},
        indent=2,
        default=str
    ))
