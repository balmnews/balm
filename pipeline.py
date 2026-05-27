#!/usr/bin/env python3
"""Balm pipeline — fetches news, clusters, synthesizes via Claude, generates digest HTML + audio."""

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import requests
from anthropic import Anthropic
from dateutil import tz
from feedgen.feed import FeedGenerator
from jinja2 import Environment, FileSystemLoader

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOCS_DIR = Path(__file__).parent / "docs"
TEMPLATES_DIR = Path(__file__).parent / "templates"
BASE_URL = "https://balmnews.github.io/balm"

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 8000

ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel

CLUSTER_SIMILARITY_THRESHOLD = 0.22
# Raised threshold applied when both articles are domestic US political stories.
# Political vocabulary (senate, congress, bill, vote, administration) is shared
# across many unrelated domestic stories, so a much higher bar is required before
# treating two domestic political articles as the same event.
DOMESTIC_POLITICAL_THRESHOLD = 0.45

CATEGORY_ORDER = [
    "GEOPOLITICS",
    "ECONOMY",
    "DOMESTIC POLICY",
    "SCIENCE & HEALTH",
    "TECHNOLOGY",
    "NATURAL EVENTS",
    "SPORTS",
    "DIFFICULT NEWS",
]

RSS_FEEDS = [
    # Existing feeds
    ("Fox News", "https://feeds.foxnews.com/foxnews/latest"),
    ("Fox News World", "https://feeds.foxnews.com/foxnews/world"),
    ("WSJ Markets", "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
    ("BBC News", "http://feeds.bbci.co.uk/news/rss.xml"),
    ("BBC World", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    # Political balance — right-leaning domestic politics perspective
    ("Fox News Politics", "https://feeds.foxnews.com/foxnews/politics"),
    # Wire service — authoritative domestic and political coverage
    ("Reuters Politics", "https://feeds.reuters.com/reuters/politicsNews"),
    ("Reuters Domestic", "https://feeds.reuters.com/reuters/domesticNews"),
    # Public media — domestic coverage with different editorial priorities
    ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
    ("PBS NewsHour", "https://www.pbs.org/newshour/feeds/rss/headlines"),
    # Climate and environment — dedicated coverage for NATURAL EVENTS / SCIENCE & HEALTH
    ("Guardian Environment", "https://www.theguardian.com/environment/rss"),
    # Legal and justice — Supreme Court and federal judiciary coverage
    ("SCOTUSblog", "https://www.scotusblog.com/feed/"),
]

EDITORIAL_SYSTEM_PROMPT = """You are the editorial engine for Balm, a news digest with one guiding principle: inform without agitating.

AUDIENCE: Primarily American readers. Cover international stories when they have direct or significant relevance to American readers — economic impact, military involvement, immigration implications, global health, major geopolitical shifts. Skip international stories that are purely regional with no US dimension.

REWRITING RULES:
- Strip all inflammatory, loaded, or emotionally charged language
- Remove editorializing, speculation, and opinion framing
- Rewrite headlines to be factual and descriptive — no clickbait, no fear language, no superlatives
- Use plain declarative sentences. Calm, authoritative tone.
- Never use: "shocking", "bombshell", "explosive", "crisis", "chaos", "slams", "blasts", "rips", "warns of disaster", "you won't believe", or any equivalent
- Write as Balm's own neutral voice — never attribute claims to a specific outlet in the text. All claims are attributed to original sources via the article link only, not in the text itself.
- Perpetrator details — names, photos, methods, manifestos — must be omitted from all violent events

MULTI-SOURCE SYNTHESIS:
When multiple sources cover the same story, you will receive all versions together as a cluster.
- Identify facts that appear consistently across all sources — these are high-confidence facts
- Identify where sources diverge in framing, emphasis, or detail — note this as genuine uncertainty
- Write a synthesis that reflects consensus facts
- Where sources diverge on matters of fact (not just framing), reflect that uncertainty: "accounts differ on..." or "figures vary by source"
- Where sources diverge only in framing or emphasis, strip the framing and report the neutral fact
- Never present a contested fact as settled
- The goal is a synthesis no single outlet would write — more complete and more neutral than any individual source

STORY LENGTH:
Each story requires TWO versions:
- brief_summary: 2-3 sentences. Factual kernel only.
- full_summary: 2-3 paragraphs, 10-30 sentences depending on story complexity. Enough context for a reader who wants full understanding. Written for audio — the listener cannot re-read, so provide sufficient context per sentence. This is also the podcast script.

CATEGORIZATION:
Assign each story exactly one of these categories:
GEOPOLITICS, ECONOMY, DOMESTIC POLICY, SCIENCE & HEALTH, TECHNOLOGY, NATURAL EVENTS, SPORTS, DIFFICULT NEWS

DIFFICULT NEWS: mass casualty events, violent crimes with broad relevance, large-scale tragedies.
- Frame only in terms of pattern, frequency, and systemic context — never as spectacle
- Omit all perpetrator identifying details
- Keep both summaries factual and restrained
- Only include if the event has implications beyond the immediate incident — policy relevance, pattern significance, or unusual scale

STORY IMPORTANCE HEURISTIC:
Ask of each story: would a well-informed adult who doesn't follow news daily need to know this happened? If yes, include. If it only matters to people already following the story closely, or has no relevance beyond the immediate event, exclude.

EXCLUDE entirely — return null for these:
- Celebrity gossip or entertainment news
- Political insults, feuds, or outrage without direct policy substance or legislative consequence
- Speculative threat pieces with no concrete news hook
- Individual tragedies with no broader policy or pattern relevance
- Polls and horse-race political coverage outside of election season
- Stories whose primary purpose is to provoke emotional reaction rather than convey information
- One-off crimes or accidents with no systemic relevance

LEGAL AND ETHICAL SAFEGUARDS:
- Attribution: all specific factual claims must be traceable to the source material provided. Never introduce details not present in the source.
- No new claims: do not add any specific fact about a named individual, company, or organization that is not explicitly in the source material. If the source is vague, your output must be equally vague.
- Private individuals: apply significantly more caution than with public figures. Omit identifying details wherever possible beyond what is necessary to understand the story.
- Allegations vs facts: "X is accused of" not "X did". "Authorities allege" not "the perpetrator". Allegations are not findings.
- Sensitive personal information: never include medical conditions, immigration status, sexual orientation, mental health history, financial situation, or religious beliefs unless the individual made this public themselves and it is directly relevant.
- Consistency check: every specific factual claim in your output must be traceable to the source material. If you cannot trace it, remove it.
- When in doubt, omit: a less detailed summary is always preferable to a legally or ethically uncertain one.

OUTPUT FORMAT — return ONLY valid JSON, no markdown, no preamble, no trailing text:
{
  "articles": [
    {
      "cluster_id": 1,
      "category": "CATEGORY",
      "headline": "Rewritten factual headline",
      "brief_summary": "2-3 sentence factual summary.",
      "full_summary": "2-3 paragraph full summary written for audio consumption.",
      "isDifficult": false
    }
  ]
}

cluster_id must match the [CLUSTER N] number from the input. One output article per cluster.
Set isDifficult: true for DIFFICULT NEWS items only.
Return null in the array position for excluded clusters.
Return between 10 and 16 articles total.
Category display order: GEOPOLITICS, ECONOMY, DOMESTIC POLICY, SCIENCE & HEALTH, TECHNOLOGY, NATURAL EVENTS, SPORTS — then DIFFICULT NEWS last and collapsed."""


# ---------------------------------------------------------------------------
# Article fetching — API sources
# ---------------------------------------------------------------------------

def fetch_newsapi(api_key: str) -> list[dict]:
    categories = ["world", "business", "technology", "health", "science", "sports",
                  "politics", "national"]
    articles = []
    for category in categories:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/top-headlines",
                params={"category": category, "pageSize": 5, "language": "en", "apiKey": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for a in data.get("articles", [])[:5]:
                if a.get("title") and a.get("url"):
                    articles.append({
                        "title": a["title"],
                        "description": a.get("description", ""),
                        "url": a["url"],
                        "source": a.get("source", {}).get("name", "NewsAPI"),
                    })
        except Exception as e:
            print(f"[WARN] NewsAPI {category}: {e}", file=sys.stderr)
    return articles


def fetch_guardian(api_key: str) -> list[dict]:
    sections = ["world", "business", "technology", "science", "sport"]
    articles = []
    for section in sections:
        try:
            resp = requests.get(
                "https://content.guardianapis.com/search",
                params={
                    "section": section,
                    "page-size": 5,
                    "show-fields": "trailText",
                    "api-key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for a in data.get("response", {}).get("results", [])[:5]:
                articles.append({
                    "title": a.get("webTitle", ""),
                    "description": a.get("fields", {}).get("trailText", ""),
                    "url": a.get("webUrl", ""),
                    "source": "The Guardian",
                })
        except Exception as e:
            print(f"[WARN] Guardian {section}: {e}", file=sys.stderr)
    return articles


def fetch_nyt(api_key: str) -> list[dict]:
    sections = ["world", "business", "technology", "health", "science", "sports",
                "politics", "climate"]
    articles = []
    for section in sections:
        try:
            resp = requests.get(
                f"https://api.nytimes.com/svc/topstories/v2/{section}.json",
                params={"api-key": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for a in data.get("results", [])[:5]:
                articles.append({
                    "title": a.get("title", ""),
                    "description": a.get("abstract", ""),
                    "url": a.get("url", ""),
                    "source": "The New York Times",
                })
        except Exception as e:
            print(f"[WARN] NYT {section}: {e}", file=sys.stderr)
    return articles


# ---------------------------------------------------------------------------
# Article fetching — RSS feeds
# ---------------------------------------------------------------------------

def fetch_rss_feeds() -> list[dict]:
    """Fetch articles from public RSS feeds. No API key required."""
    articles = []
    for source_name, feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            count = 0
            for entry in feed.entries:
                if count >= 8:
                    break
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or "").strip()
                summary = (entry.get("summary") or entry.get("description") or "").strip()
                if not title or not link:
                    continue
                # Strip HTML tags from summary
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s+", " ", summary).strip()
                articles.append({
                    "title": title,
                    "description": summary[:400],
                    "url": link,
                    "source": source_name,
                })
                count += 1
            print(f"  RSS {source_name}: {count} articles")
        except Exception as e:
            print(f"[WARN] RSS {source_name}: {e}", file=sys.stderr)
    return articles


# ---------------------------------------------------------------------------
# Exact-duplicate removal
# ---------------------------------------------------------------------------

def remove_exact_duplicates(articles: list[dict]) -> list[dict]:
    """Remove only true exact duplicates: same title AND same source AND same URL.

    Near-duplicates — the same story covered by different outlets — are
    intentionally kept so the clustering step can group them together and
    Claude can synthesize a multi-source account. Discarding cross-outlet
    near-duplicates here would strip the synthesis inputs before clustering
    ever runs.
    """
    seen: set[tuple] = set()
    unique = []
    for article in articles:
        key = (
            (article.get("title") or "").strip(),
            (article.get("source") or "").strip(),
            (article.get("url") or "").strip(),
        )
        if key not in seen:
            seen.add(key)
            unique.append(article)
    return unique


# ---------------------------------------------------------------------------
# Story clustering (TF-IDF cosine similarity, stdlib only)
# ---------------------------------------------------------------------------

_CLUSTER_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "are", "was", "were", "be", "been", "by", "as",
    "this", "that", "it", "its", "from", "has", "have", "had", "not",
    "after", "over", "more", "about", "up", "will", "said", "say", "says",
    "would", "could", "should", "also", "than", "when", "where", "who",
    "which", "what", "how", "can", "new", "one", "two", "three",
}

# ---------------------------------------------------------------------------
# Geographic entity reference lists — used for clustering coherence gate
# ---------------------------------------------------------------------------

# Unambiguous single-word geographic names: countries, US states, major cities.
# Kept to clearly distinct proper nouns so common words are never false-matched.
_GEO_SINGLE: frozenset[str] = frozenset({
    # Countries
    "afghanistan", "albania", "algeria", "angola", "argentina", "armenia",
    "australia", "austria", "azerbaijan", "bahrain", "bangladesh", "belarus",
    "belgium", "belize", "benin", "bhutan", "bolivia", "botswana", "brazil",
    "brunei", "bulgaria", "burkina", "burundi", "cambodia", "cameroon",
    "canada", "chile", "china", "colombia", "comoros", "congo", "croatia",
    "cuba", "cyprus", "czechia", "denmark", "djibouti", "ecuador", "egypt",
    "eritrea", "ethiopia", "fiji", "finland", "france", "gabon", "gambia",
    "georgia", "germany", "ghana", "greece", "grenada", "guatemala", "guinea",
    "guyana", "haiti", "honduras", "hungary", "india", "indonesia", "iran",
    "iraq", "ireland", "israel", "italy", "jamaica", "japan", "jordan",
    "kazakhstan", "kenya", "kiribati", "kosovo", "kuwait", "kyrgyzstan",
    "laos", "latvia", "lebanon", "lesotho", "liberia", "libya", "lithuania",
    "luxembourg", "madagascar", "malawi", "malaysia", "maldives", "mali",
    "malta", "mauritania", "mauritius", "mexico", "moldova", "monaco",
    "mongolia", "montenegro", "morocco", "mozambique", "myanmar", "namibia",
    "nauru", "nepal", "netherlands", "nicaragua", "niger", "nigeria",
    "norway", "oman", "pakistan", "palau", "palestine", "panama", "paraguay",
    "peru", "philippines", "poland", "portugal", "qatar", "romania", "russia",
    "rwanda", "samoa", "senegal", "serbia", "seychelles", "singapore",
    "slovakia", "slovenia", "somalia", "spain", "sudan", "suriname", "sweden",
    "switzerland", "syria", "taiwan", "tajikistan", "tanzania", "thailand",
    "togo", "tonga", "tunisia", "turkey", "turkmenistan", "tuvalu", "uganda",
    "ukraine", "uruguay", "uzbekistan", "vanuatu", "venezuela", "vietnam",
    "yemen", "zambia", "zimbabwe",
    # US states
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "ohio", "oklahoma",
    "oregon", "pennsylvania", "tennessee", "texas", "utah", "vermont",
    "virginia", "wisconsin", "wyoming",
    # Major world cities (unambiguous single-word names only)
    "kabul", "tirana", "algiers", "luanda", "yerevan", "canberra", "vienna",
    "baku", "manama", "dhaka", "minsk", "brussels", "brasilia", "sofia",
    "zagreb", "nicosia", "prague", "copenhagen", "cairo", "tallinn",
    "helsinki", "paris", "tbilisi", "berlin", "accra", "athens", "london",
    "moscow", "washington", "beijing", "shanghai", "mumbai", "delhi",
    "tokyo", "istanbul", "dubai", "singapore", "seoul", "toronto", "sydney",
    "madrid", "rome", "amsterdam", "stockholm", "oslo", "zurich", "warsaw",
    "budapest", "lisbon", "tehran", "baghdad", "riyadh", "jerusalem",
    "karachi", "lahore", "islamabad", "colombo", "bangkok", "jakarta",
    "manila", "yangon", "nairobi", "johannesburg", "lagos", "casablanca",
    "montreal", "melbourne", "geneva", "kyiv", "vilnius", "riga", "chisinau",
    "sarajevo", "pristina", "skopje", "belgrade", "bucharest", "ankara",
    "damascus", "amman", "beirut", "doha", "muscat", "sanaa", "mogadishu",
    "kampala", "lusaka", "harare", "khartoum", "freetown", "monrovia",
    "abuja", "dakar", "bamako", "niamey", "ndjamena", "bangui", "yaounde",
    "brazzaville", "maputo", "windhoek", "gaborone", "pretoria", "chicago",
    "houston", "phoenix", "philadelphia", "dallas", "miami", "atlanta",
    "seattle", "denver", "boston", "detroit", "minneapolis", "portland",
    "ottawa", "santiago", "bogota", "kinshasa", "havana",
})

# Multi-word geographic phrases — matched as substrings in lowercased text.
_GEO_PHRASES: frozenset[str] = frozenset({
    "united states", "united kingdom", "united arab emirates", "saudi arabia",
    "south africa", "south korea", "north korea", "costa rica", "new zealand",
    "el salvador", "sri lanka", "ivory coast", "burkina faso", "czech republic",
    "dominican republic", "central african republic", "papua new guinea",
    "equatorial guinea", "hong kong", "new york", "los angeles", "san francisco",
    "las vegas", "new delhi", "addis ababa", "dar es salaam", "cape town",
    "buenos aires", "rio de janeiro", "sao paulo", "mexico city",
    "kuala lumpur", "phnom penh", "ho chi minh", "rhode island",
    "west virginia", "new hampshire", "new jersey", "new mexico",
    "north carolina", "north dakota", "south carolina", "south dakota",
})

# ---------------------------------------------------------------------------
# Domestic political detection — used to apply stricter clustering threshold
# ---------------------------------------------------------------------------

# US states only.  Used to identify domestic political articles.
_GEO_US_STATES: frozenset[str] = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "hawaii", "idaho", "illinois",
    "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "ohio", "oklahoma",
    "oregon", "pennsylvania", "tennessee", "texas", "utah", "vermont",
    "virginia", "wisconsin", "wyoming",
})

# Foreign countries that are unambiguously NOT US states.
# "georgia" is intentionally excluded — it is both a US state and a country;
# the ambiguity means we cannot use it as a foreign-country signal.
_GEO_FOREIGN_COUNTRIES: frozenset[str] = frozenset({
    "afghanistan", "albania", "algeria", "angola", "argentina", "armenia",
    "australia", "austria", "azerbaijan", "bahrain", "bangladesh", "belarus",
    "belgium", "belize", "benin", "bhutan", "bolivia", "botswana", "brazil",
    "brunei", "bulgaria", "burkina", "burundi", "cambodia", "cameroon",
    "canada", "chile", "china", "colombia", "comoros", "congo", "croatia",
    "cuba", "cyprus", "czechia", "denmark", "djibouti", "ecuador", "egypt",
    "eritrea", "ethiopia", "fiji", "finland", "france", "gabon", "gambia",
    "germany", "ghana", "greece", "grenada", "guatemala", "guinea",
    "guyana", "haiti", "honduras", "hungary", "india", "indonesia", "iran",
    "iraq", "ireland", "israel", "italy", "jamaica", "japan", "jordan",
    "kazakhstan", "kenya", "kiribati", "kosovo", "kuwait", "kyrgyzstan",
    "laos", "latvia", "lebanon", "lesotho", "liberia", "libya", "lithuania",
    "luxembourg", "madagascar", "malawi", "malaysia", "maldives", "mali",
    "malta", "mauritania", "mauritius", "mexico", "moldova", "monaco",
    "mongolia", "montenegro", "morocco", "mozambique", "myanmar", "namibia",
    "nauru", "nepal", "netherlands", "nicaragua", "niger", "nigeria",
    "norway", "oman", "pakistan", "palau", "palestine", "panama", "paraguay",
    "peru", "philippines", "poland", "portugal", "qatar", "romania", "russia",
    "rwanda", "samoa", "senegal", "serbia", "seychelles", "singapore",
    "slovakia", "slovenia", "somalia", "spain", "sudan", "suriname", "sweden",
    "switzerland", "syria", "taiwan", "tajikistan", "tanzania", "thailand",
    "togo", "tonga", "tunisia", "turkey", "turkmenistan", "tuvalu", "uganda",
    "ukraine", "uruguay", "uzbekistan", "vanuatu", "venezuela", "vietnam",
    "yemen", "zambia", "zimbabwe",
})

# Multi-word phrases that unambiguously identify foreign stories.
# US state phrases ("new york", "new jersey", etc.) are intentionally excluded.
_GEO_FOREIGN_PHRASES: frozenset[str] = frozenset({
    "united kingdom", "united arab emirates", "saudi arabia", "south africa",
    "south korea", "north korea", "costa rica", "new zealand", "el salvador",
    "sri lanka", "ivory coast", "burkina faso", "czech republic",
    "dominican republic", "central african republic", "papua new guinea",
    "equatorial guinea", "hong kong", "new delhi", "addis ababa",
    "dar es salaam", "cape town", "buenos aires", "rio de janeiro",
    "sao paulo", "mexico city", "kuala lumpur", "phnom penh", "ho chi minh",
})

# ---------------------------------------------------------------------------
# Event-type classification — used to block merges between articles covering
# structurally different kinds of political events
# ---------------------------------------------------------------------------

# Each value is a tuple of lowercased substrings.  Multi-word phrases match
# naturally via substring search.  Keywords were chosen to be type-specific;
# ambiguous terms (senate, congress, vote, signed) are intentionally absent.
# Classification requires ≥ 2 keyword matches to be considered high-confidence.
_EVENT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "election": (
        "election", "primary", "runoff", "ballot", "candidate",
        "senate race", "voters", "voting rights", "campaign trail",
        "early voting", "runoff election", "general election",
    ),
    "legal": (
        "doj", "indictment", "january 6", "charges filed", "lawsuit",
        "trial", "verdict", "subpoena", "prosecution", "plea deal",
        "department of justice", "attorney general", "grand jury",
    ),
    "legislative": (
        "bill", "legislation", "amendment", "committee hearing",
        "filibuster", "reconciliation", "appropriations",
        "passed the senate", "passed the house", "signed into law",
    ),
    "executive": (
        "executive order", "white house", "president signed",
        "cabinet", "appointed", "administration announced", "oval office",
        "secretary of", "acting director",
    ),
}


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower())
            if t not in _CLUSTER_STOPWORDS and len(t) > 2]


def _tfidf_vectors(texts: list[str]) -> list[dict[str, float]]:
    tokenized = [_tokenize(t) for t in texts]
    N = len(texts)
    # Build IDF over corpus
    vocab: set[str] = set()
    for tokens in tokenized:
        vocab.update(tokens)
    idf: dict[str, float] = {}
    for term in vocab:
        df = sum(1 for tokens in tokenized if term in set(tokens))
        idf[term] = math.log((N + 1) / (df + 1))  # smoothed IDF
    # Build TF-IDF vector per document
    vectors: list[dict[str, float]] = []
    for tokens in tokenized:
        if not tokens:
            vectors.append({})
            continue
        tf = Counter(tokens)
        total = len(tokens)
        vec = {t: (c / total) * idf.get(t, 0.0) for t, c in tf.items()}
        vectors.append(vec)
    return vectors


def _cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
    if not v1 or not v2:
        return 0.0
    dot = sum(v1[t] * v2[t] for t in v1 if t in v2)
    mag1 = math.sqrt(sum(x * x for x in v1.values()))
    mag2 = math.sqrt(sum(x * x for x in v2.values()))
    if mag1 == 0.0 or mag2 == 0.0:
        return 0.0
    return dot / (mag1 * mag2)


def _extract_named_entities(title: str) -> set[str]:
    """Extract likely proper nouns: capitalised words that are not the first word.

    Simple heuristic — no NLP library required. Catches entity names like
    country names, people, organisations that anchor same-story matching.
    """
    words = title.split()
    entities: set[str] = set()
    for word in words[1:]:                       # skip the first word (always capitalised)
        clean = re.sub(r"[^a-zA-Z'-]", "", word)  # strip punctuation
        if clean and clean[0].isupper() and len(clean) > 1:
            entities.add(clean.lower())
    return entities


def _extract_geo_entities(text: str) -> set[str]:
    """Extract recognised country, state, and major city names from article text.

    Searches title + description text for single-word names (via token-set
    intersection with _GEO_SINGLE) and multi-word phrases (via substring
    match against _GEO_PHRASES). Case-insensitive.

    Returns an empty set when no geographic signal is detected — callers treat
    an empty set as "unknown geography" and fall back to TF-IDF logic rather
    than blocking a merge.
    """
    text_lower = text.lower()
    # Single-word pass: tokenise and intersect with reference set
    words = set(re.findall(r"\b[a-z]+\b", text_lower))
    found = words & _GEO_SINGLE
    # Multi-word pass: substring match
    for phrase in _GEO_PHRASES:
        if phrase in text_lower:
            found.add(phrase)
    return found


def _is_domestic_political(text: str) -> bool:
    """Return True if this article is clearly a domestic US political story.

    Heuristic: text contains at least one US state name or a Washington DC
    indicator ("washington"), AND contains no unambiguous foreign country name.
    When True, pairwise clustering applies DOMESTIC_POLITICAL_THRESHOLD (0.45)
    instead of the default (0.22), because political vocabulary is shared across
    many unrelated domestic stories and the standard threshold is too permissive.
    """
    text_lower = text.lower()
    words = set(re.findall(r"\b[a-z]+\b", text_lower))
    # Reject if any unambiguous foreign country name is present
    if words & _GEO_FOREIGN_COUNTRIES:
        return False
    for phrase in _GEO_FOREIGN_PHRASES:
        if phrase in text_lower:
            return False
    # Must have at least one US geographic anchor
    return bool(words & _GEO_US_STATES) or "washington" in words


def _classify_event_type(text: str) -> tuple[str, int]:
    """Classify article text into a broad political event type via keyword matching.

    Searches for substrings from _EVENT_TYPE_KEYWORDS in lowercased text and
    counts matches per type.  Returns (event_type, confidence) where confidence
    is the number of keyword matches for the winning type.

    Returns ("ambiguous", 0) when:
    - no type scores ≥ 2 (too few signals for high confidence), or
    - the top two types are tied (genuinely ambiguous article).

    Callers should treat "ambiguous" as "no classification" and fall back to
    existing TF-IDF logic rather than blocking a merge.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for etype, keywords in _EVENT_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[etype] = score

    if not scores:
        return ("ambiguous", 0)

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    top_type, top_score = ranked[0]

    # Require ≥ 2 matches and a clear lead over the second type
    if top_score < 2:
        return ("ambiguous", 0)
    if len(ranked) >= 2 and ranked[1][1] >= top_score:
        return ("ambiguous", 0)

    return (top_type, top_score)


def cluster_articles(articles: list[dict], threshold: float = CLUSTER_SIMILARITY_THRESHOLD) -> list[list[dict]]:
    """Group articles covering the same news event using layered merge guards.

    Guards fire in order; a block at any layer skips the merge entirely:

    0a. **Geographic coherence gate** — if both articles have detectable place
        names and share none, they are about different places: block.
    0b. **Event-type conflict check** — if both articles classify with high
        confidence (≥ 2 keyword matches) into different event types (election vs
        legal vs legislative vs executive), block regardless of text similarity.

    When neither guard blocks, the effective similarity threshold is determined:
    - Both articles are domestic US political → DOMESTIC_POLITICAL_THRESHOLD (0.45)
    - Otherwise → threshold (default 0.22)

    A merge then fires on ANY of:
    1. **Named-entity boost** — headlines share ≥ 2 capitalised non-initial words.
    2. **Headline TF-IDF similarity** ≥ effective threshold.
    3. **Description TF-IDF similarity** ≥ effective threshold (fallback).

    Uses Union-Find so grouping is transitive: if A~B and B~C, all three merge.
    """
    if not articles:
        return []

    n = len(articles)
    text = [a["title"] + " " + (a.get("description") or "") for a in articles]

    # Per-article signals (pre-computed for performance)
    named_entities = [_extract_named_entities(a["title"]) for a in articles]
    geo_entities   = [_extract_geo_entities(t) for t in text]
    is_domestic    = [_is_domestic_political(t) for t in text]
    event_types    = [_classify_event_type(t) for t in text]

    # Separate TF-IDF vectors for headlines and descriptions
    head_vectors = _tfidf_vectors([a["title"] for a in articles])
    desc_vectors = _tfidf_vectors([(a.get("description") or "")[:300] for a in articles])

    # Union-Find
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            # 0a. Geographic coherence gate
            if geo_entities[i] and geo_entities[j] and not (geo_entities[i] & geo_entities[j]):
                continue

            # 0b. Event-type conflict check — block when both articles have a
            #     high-confidence classification and those classifications differ.
            #     "ambiguous" is treated as "no classification" and does not block.
            type_i, conf_i = event_types[i]
            type_j, conf_j = event_types[j]
            if (type_i != "ambiguous" and type_j != "ambiguous"
                    and conf_i >= 2 and conf_j >= 2
                    and type_i != type_j):
                continue

            # Effective threshold: stricter for domestic-political pairs because
            # political vocabulary is shared across many unrelated stories.
            eff_threshold = (DOMESTIC_POLITICAL_THRESHOLD
                             if is_domestic[i] and is_domestic[j]
                             else threshold)

            # 1. Named-entity boost (threshold-independent)
            if len(named_entities[i] & named_entities[j]) >= 2:
                union(i, j)
                continue
            # 2. Headline similarity
            if _cosine(head_vectors[i], head_vectors[j]) >= eff_threshold:
                union(i, j)
                continue
            # 3. Description fallback
            if desc_vectors[i] and desc_vectors[j]:
                if _cosine(desc_vectors[i], desc_vectors[j]) >= eff_threshold:
                    union(i, j)

    clusters_map: dict[int, list[int]] = {}
    for i in range(n):
        clusters_map.setdefault(find(i), []).append(i)

    return [[articles[i] for i in idxs] for idxs in clusters_map.values()]


def _split_incoherent_clusters(clusters: list[list[dict]]) -> tuple[list[list[dict]], int]:
    """Post-clustering coherence check: split clusters that fail either of two tests.

    **Test 1 — Named-entity coherence** (existing):
    At least one named entity (capitalised non-initial word from the headline)
    must appear in ≥ 2 articles. Clusters where all entity sets are empty are
    exempt — we cannot determine incoherence from entities alone.

    **Test 2 — Event-type diversity** (new):
    Articles in the cluster must not span more than 2 distinct high-confidence
    event types. A cluster covering election + legal + legislative articles (3
    types) is a false-positive transitivity merge and is split into singletons.
    "ambiguous" classifications are excluded from the count.

    Failing either test causes the entire cluster to be split into singletons
    and logged to stderr as [SPLIT]. Returns (cleaned_clusters, n_splits).
    """
    result = []
    splits = 0

    for cluster in clusters:
        if len(cluster) <= 1:
            result.append(cluster)
            continue

        # --- Test 1: Named-entity coherence ---
        entity_sets = [_extract_named_entities(a["title"]) for a in cluster]
        if not all(not s for s in entity_sets):
            # At least one article has named entities — validate.
            entity_counts: Counter = Counter()
            for s in entity_sets:
                entity_counts.update(s)
            if not any(count >= 2 for count in entity_counts.values()):
                splits += 1
                headlines = " | ".join(a["title"][:45] for a in cluster)
                print(f"  [SPLIT] No shared entity → {len(cluster)} singletons: {headlines}",
                      file=sys.stderr)
                result.extend([[a] for a in cluster])
                continue

        # --- Test 2: Event-type diversity ---
        type_labels: set[str] = set()
        for a in cluster:
            etype, _ = _classify_event_type(
                a["title"] + " " + (a.get("description") or "")
            )
            if etype != "ambiguous":
                type_labels.add(etype)
        if len(type_labels) > 2:
            splits += 1
            headlines = " | ".join(a["title"][:45] for a in cluster)
            print(
                f"  [SPLIT] Event-type span ({', '.join(sorted(type_labels))}) → "
                f"{len(cluster)} singletons: {headlines}",
                file=sys.stderr,
            )
            result.extend([[a] for a in cluster])
            continue

        result.append(cluster)

    return result, splits


# ---------------------------------------------------------------------------
# Claude editorial processing
# ---------------------------------------------------------------------------

def build_cluster_prompt(clusters: list[list[dict]]) -> str:
    lines = [
        f"Process the following {len(clusters)} story clusters. "
        "Each cluster contains one or more news sources covering the same underlying event.\n"
    ]
    for ci, cluster in enumerate(clusters, 1):
        if len(cluster) == 1:
            a = cluster[0]
            lines.append(f"[CLUSTER {ci}] — 1 source")
            lines.append(f"  Source: {a['source']}")
            lines.append(f"  Title: {a['title']}")
            lines.append(f"  URL: {a['url']}")
            if a.get("description"):
                lines.append(f"  Description: {a['description'][:300]}")
        else:
            lines.append(f"[CLUSTER {ci}] — {len(cluster)} sources covering the same story")
            for si, a in enumerate(cluster, 1):
                lines.append(f"  — Source {si}: {a['source']}")
                lines.append(f"    Title: {a['title']}")
                lines.append(f"    URL: {a['url']}")
                if a.get("description"):
                    lines.append(f"    Description: {a['description'][:300]}")
        lines.append("")
    return "\n".join(lines)


def call_claude(clusters: list[list[dict]], anthropic_key: str) -> list[dict]:
    client = Anthropic(api_key=anthropic_key)
    user_prompt = build_cluster_prompt(clusters)

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=EDITORIAL_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            # Strip markdown code fences if present
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            processed = [a for a in data.get("articles", []) if a is not None]
            return processed
        except json.JSONDecodeError as e:
            print(f"[WARN] Claude returned invalid JSON (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt == 2:
                raise
            time.sleep(5)
        except Exception as e:
            print(f"[WARN] Claude API error (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt == 2:
                raise
            time.sleep(10)

    return []


def attach_sources(articles: list[dict], clusters: list[list[dict]]) -> None:
    """Attach source attribution to each Claude-processed article using cluster_id.

    Modifies articles in place. Each article gains a 'sources' field:
      [{"source": "Outlet Name", "url": "...", "original_headline": "..."}]
    """
    cluster_map = {i + 1: cluster for i, cluster in enumerate(clusters)}
    for article in articles:
        cid = article.get("cluster_id")
        cluster = cluster_map.get(cid, [])
        article["sources"] = [
            {
                "source": a["source"],
                "url": a["url"],
                "original_headline": a["title"],
            }
            for a in cluster
        ]
        # Convenience field for audio/RSS (primary source)
        if article["sources"]:
            article["primary_source"] = article["sources"][0]["source"]
        else:
            article["primary_source"] = "Unknown"


def sort_articles(articles: list[dict]) -> list[dict]:
    """Sort articles by canonical category order."""
    order = {cat: i for i, cat in enumerate(CATEGORY_ORDER)}
    return sorted(articles, key=lambda a: order.get(a.get("category", ""), 99))


def number_articles(articles: list[dict]) -> None:
    """Add sequential 'ref' field to articles in place (1-indexed)."""
    for i, article in enumerate(articles, 1):
        article["ref"] = i


# ---------------------------------------------------------------------------
# S&P 500 fetch
# ---------------------------------------------------------------------------

def fetch_sp500() -> float | None:
    """Attempt to fetch S&P 500 close from Yahoo Finance. Non-blocking."""
    try:
        resp = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC",
            params={"interval": "1d", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        close = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
        return round(float(close), 2)
    except Exception as e:
        print(f"[WARN] S&P 500 fetch failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def collect_archive(docs_dir: Path) -> list[dict]:
    """Return sorted list of all digest metadata for archive nav."""
    entries = []
    for f in sorted(docs_dir.glob("????-??-??-??.json"), reverse=True):
        try:
            meta = json.loads(f.read_text())
            run = meta.get("run", f.stem.split("-")[-1])
            date_str = meta.get("date", "-".join(f.stem.split("-")[:3]))
            entries.append({
                "date": date_str,
                "run": run,
                "label": f"{date_str} {'AM' if run == 'am' else 'PM'}",
                "file": f.stem + ".html",
            })
        except Exception:
            continue
    return entries


def group_archive_by_month(entries: list[dict]) -> list[dict]:
    """Group archive entries by YYYY-MM for the sidebar."""
    months: dict[str, list] = {}
    for e in entries:
        key = e["date"][:7]
        months.setdefault(key, []).append(e)
    result = []
    for key in sorted(months.keys(), reverse=True):
        year, month = key.split("-")
        month_name = datetime(int(year), int(month), 1).strftime("%B %Y")
        result.append({"month": month_name, "entries": months[key]})
    return result


def write_archive_json(archive: list[dict], docs_dir: Path) -> None:
    """Write docs/archive.json so all pages can load an always-current archive sidebar."""
    data = {"months": group_archive_by_month(archive)}
    out_path = docs_dir / "archive.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] archive.json updated ({len(archive)} entries)")


def _group_by_category(articles: list[dict]) -> list[dict]:
    grouped: dict[str, list] = {}
    for article in articles:
        cat = article.get("category", "UNCATEGORIZED")
        grouped.setdefault(cat, []).append(article)
    return [
        {"name": cat, "articles": grouped[cat]}
        for cat in CATEGORY_ORDER
        if cat in grouped
    ]


def render_digest(articles: list[dict], date_str: str, run: str, metadata: dict,
                  archive: list[dict], docs_dir: Path) -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("digest.html")

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")

    mp3_file = f"{date_str}-{run}.mp3"
    has_audio = (docs_dir / mp3_file).exists()
    sources_file = f"{date_str}-{run}-sources.html"

    html = template.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        categories=_group_by_category(articles),
        archive_months=group_archive_by_month(archive),
        metadata=metadata,
        has_audio=has_audio,
        mp3_file=mp3_file if has_audio else None,
        sp500=metadata.get("sp500_close"),
        base_url=BASE_URL,
        sources_file=sources_file,
    )

    out_path = docs_dir / f"{date_str}-{run}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] Digest HTML: {out_path}")
    return out_path


def render_sources(articles: list[dict], date_str: str, run: str,
                   archive: list[dict], docs_dir: Path) -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("sources.html")

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")
    digest_file = f"{date_str}-{run}.html"

    html = template.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        articles=articles,
        archive_months=group_archive_by_month(archive),
        digest_file=digest_file,
    )

    out_path = docs_dir / f"{date_str}-{run}-sources.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] Sources page: {out_path}")
    return out_path


def render_index(articles: list[dict], date_str: str, run: str, metadata: dict,
                 archive: list[dict], docs_dir: Path) -> None:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("index.html")

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")

    mp3_file = f"{date_str}-{run}.mp3"
    has_audio = (docs_dir / mp3_file).exists()
    sources_file = f"{date_str}-{run}-sources.html"

    html = template.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        categories=_group_by_category(articles),
        archive_months=group_archive_by_month(archive),
        metadata=metadata,
        has_audio=has_audio,
        mp3_file=mp3_file if has_audio else None,
        sp500=metadata.get("sp500_close"),
        base_url=BASE_URL,
        current_digest_file=f"{date_str}-{run}.html",
        sources_file=sources_file,
    )

    out_path = docs_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] index.html updated")


# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------

def build_audio_script(articles: list[dict], date_str: str, run: str) -> str:
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")

    if run == "am":
        intro = f"This is Balm for {date_display}. Here are today's stories."
        outro = "That's today's Balm digest. Return this evening for the day's second edition."
    else:
        intro = f"This is Balm for {date_display}, afternoon edition. Here are today's stories."
        outro = "That's the day's final Balm digest. We'll return tomorrow morning."

    parts = [intro, ""]

    for cat in CATEGORY_ORDER:
        if cat == "DIFFICULT NEWS":
            continue
        cat_articles = [a for a in articles if a.get("category") == cat]
        if cat_articles:
            parts.append(cat + ".")
            parts.append("")
            for article in cat_articles:
                parts.append(article.get("full_summary", ""))
                parts.append("")

    parts.append(outro)
    return "\n".join(parts)


def generate_audio(script: str, date_str: str, run: str, api_key: str, docs_dir: Path) -> Path | None:
    out_path = docs_dir / f"{date_str}-{run}.mp3"
    try:
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": script,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.6, "similarity_boost": 0.8},
            },
            timeout=120,
        )
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        print(f"[OK] Audio: {out_path}")
        return out_path
    except Exception as e:
        print(f"[WARN] ElevenLabs audio generation failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Podcast RSS feed
# ---------------------------------------------------------------------------

def update_podcast_feed(docs_dir: Path) -> None:
    fg = FeedGenerator()
    fg.load_extension("podcast")

    fg.id(f"{BASE_URL}/podcast.xml")
    fg.title("Balm")
    fg.author({"name": "Balm", "email": "balmnews@proton.me"})
    fg.link(href=BASE_URL, rel="alternate")
    fg.link(href=f"{BASE_URL}/podcast.xml", rel="self")
    fg.language("en-us")
    fg.description(
        "Topical, anti-inflammatory news — a daily digest stripped of inflammatory "
        "language and emotional manipulation."
    )
    fg.podcast.itunes_category("News")
    fg.podcast.itunes_explicit("no")
    fg.podcast.itunes_author("Balm")
    fg.podcast.itunes_summary(
        "Balm delivers the same factual information as conventional news without "
        "the emotional manipulation, clickbait, or inflammatory language."
    )

    mp3_files = sorted(docs_dir.glob("????-??-??-??.mp3"), reverse=True)
    for mp3 in mp3_files[:50]:
        stem = mp3.stem
        parts = stem.split("-")
        if len(parts) < 4:
            continue
        date_str = "-".join(parts[:3])
        run = parts[3]
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue

        run_label = "AM" if run == "am" else "PM"
        date_display = date_obj.strftime("%B %-d, %Y")
        size = mp3.stat().st_size

        fe = fg.add_entry()
        fe.id(f"{BASE_URL}/{mp3.name}")
        fe.title(f"Balm — {date_display} {run_label}")
        fe.description(f"Balm news digest for {date_display}, {run_label} edition.")
        fe.enclosure(f"{BASE_URL}/{mp3.name}", str(size), "audio/mpeg")
        pub_dt = datetime.combine(date_obj, datetime.min.time()).replace(tzinfo=timezone.utc)
        fe.published(pub_dt)
        fe.podcast.itunes_duration("00:00")

    feed_path = docs_dir / "podcast.xml"
    fg.rss_str(pretty=True)
    fg.rss_file(str(feed_path))
    print(f"[OK] Podcast RSS: {feed_path}")


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def save_metadata(date_str: str, run: str, articles: list[dict], raw_count: int,
                  sp500: float | None, docs_dir: Path) -> dict:
    categories_present = list({a.get("category") for a in articles})
    difficult = [a for a in articles if a.get("isDifficult")]

    metadata = {
        "date": date_str,
        "run": run,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "story_count": len(articles),
        "excluded_count": raw_count - len(articles),
        "categories": categories_present,
        "difficult_count": len(difficult),
        "sp500_close": sp500,
    }

    out_path = docs_dir / f"{date_str}-{run}.json"
    out_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"[OK] Metadata: {out_path}")
    return metadata


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Balm pipeline")
    parser.add_argument("--run", choices=["am", "pm"], default=None,
                        help="Which edition to generate (default: auto-detect from current time)")
    parser.add_argument("--date", default=None,
                        help="Override date (YYYY-MM-DD). Default: today in PT.")
    args = parser.parse_args()

    # Resolve run and date
    pt_tz = tz.gettz("America/Los_Angeles")
    now_pt = datetime.now(pt_tz)
    date_str = args.date or now_pt.strftime("%Y-%m-%d")
    run = args.run or ("am" if now_pt.hour < 13 else "pm")

    print(f"[START] Balm pipeline — {date_str} {run.upper()}")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Read API keys
    news_api_key    = os.environ.get("NEWS_API_KEY", "")
    guardian_api_key = os.environ.get("GUARDIAN_API_KEY", "")
    nyt_api_key     = os.environ.get("NYT_API_KEY", "")
    anthropic_key   = os.environ.get("ANTHROPIC_API_KEY", "")
    elevenlabs_key  = os.environ.get("ELEVEN_LABS_API_KEY", "")

    # ── Step 1: Fetch articles ────────────────────────────────────────────
    print("\n[1/10] Fetching articles from all sources...")
    raw_articles: list[dict] = []

    if news_api_key:
        fetched = fetch_newsapi(news_api_key)
        print(f"  NewsAPI: {len(fetched)} articles")
        raw_articles.extend(fetched)
    else:
        print("  [WARN] NEWS_API_KEY not set — skipping NewsAPI")

    if guardian_api_key:
        fetched = fetch_guardian(guardian_api_key)
        print(f"  Guardian: {len(fetched)} articles")
        raw_articles.extend(fetched)
    else:
        print("  [WARN] GUARDIAN_API_KEY not set — skipping Guardian")

    if nyt_api_key:
        fetched = fetch_nyt(nyt_api_key)
        print(f"  NYT: {len(fetched)} articles")
        raw_articles.extend(fetched)
    else:
        print("  [WARN] NYT_API_KEY not set — skipping NYT")

    rss_fetched = fetch_rss_feeds()
    raw_articles.extend(rss_fetched)
    print(f"  RSS feeds total: {len(rss_fetched)} articles")
    print(f"  Total raw: {len(raw_articles)}")

    # ── Step 2: Remove exact duplicates ──────────────────────────────────
    print("\n[2/10] Removing exact duplicates (same title + source + URL)...")
    deduped_articles = remove_exact_duplicates(raw_articles)
    removed = len(raw_articles) - len(deduped_articles)
    print(f"  {len(raw_articles)} raw → {len(deduped_articles)} articles "
          f"({removed} exact duplicates removed)")

    if not deduped_articles:
        print("[ERROR] No articles fetched. Check API keys.", file=sys.stderr)
        sys.exit(1)

    # ── Step 3: Cluster ───────────────────────────────────────────────────
    print("\n[3/10] Clustering articles by story...")
    clusters = cluster_articles(deduped_articles)
    clusters, n_splits = _split_incoherent_clusters(clusters)
    multi = sum(1 for c in clusters if len(c) > 1)
    single = len(clusters) - multi
    print(f"  {len(deduped_articles)} articles → {len(clusters)} clusters "
          f"({multi} multi-source, {single} single-source"
          + (f", {n_splits} incoherent cluster{'s' if n_splits != 1 else ''} split" if n_splits else "")
          + ")")
    # Log each multi-source cluster so it's easy to verify synthesis inputs
    for i, cluster in enumerate(clusters, 1):
        if len(cluster) > 1:
            outlets = " · ".join(a["source"] for a in cluster)
            print(f"    Cluster {i:2d} [{len(cluster)} sources]: {outlets}")

    if not anthropic_key:
        print("[ERROR] ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    # ── Step 4: Claude editorial processing ──────────────────────────────
    print("\n[4/10] Sending clusters to Claude for synthesis and editorial processing...")
    processed_articles = call_claude(clusters, anthropic_key)
    attach_sources(processed_articles, clusters)
    processed_articles = sort_articles(processed_articles)
    number_articles(processed_articles)
    print(f"  Claude returned {len(processed_articles)} articles after filtering")

    # ── Step 5: S&P 500 ──────────────────────────────────────────────────
    print("\n[5/10] Fetching S&P 500 close...")
    sp500 = fetch_sp500()
    print(f"  S&P 500: {sp500 if sp500 else 'unavailable (non-blocking)'}")

    # ── Step 6: Save metadata ─────────────────────────────────────────────
    print("\n[6/10] Saving metadata...")
    metadata = save_metadata(date_str, run, processed_articles, len(deduped_articles), sp500, DOCS_DIR)

    # ── Step 7: Generate audio ────────────────────────────────────────────
    print("\n[7/10] Generating audio...")
    mp3_path = None
    if elevenlabs_key:
        script = build_audio_script(processed_articles, date_str, run)
        mp3_path = generate_audio(script, date_str, run, elevenlabs_key, DOCS_DIR)
    else:
        print("  [WARN] ELEVEN_LABS_API_KEY not set — skipping audio")

    # ── Step 8: Collect archive ───────────────────────────────────────────
    print("\n[8/10] Building archive index...")
    archive = collect_archive(DOCS_DIR)
    write_archive_json(archive, DOCS_DIR)

    # ── Step 9: Render output files ───────────────────────────────────────
    print("\n[9/10] Rendering output files...")
    render_digest(processed_articles, date_str, run, metadata, archive, DOCS_DIR)
    render_sources(processed_articles, date_str, run, archive, DOCS_DIR)
    render_index(processed_articles, date_str, run, metadata, archive, DOCS_DIR)

    # ── Step 10: Podcast RSS ──────────────────────────────────────────────
    print("\n[10/10] Updating podcast RSS feed...")
    try:
        update_podcast_feed(DOCS_DIR)
    except Exception as e:
        print(f"  [WARN] Podcast feed update failed: {e}", file=sys.stderr)

    print(f"\n[DONE] Balm {date_str} {run.upper()} complete.")
    print(f"  Stories published : {len(processed_articles)}")
    print(f"  Clusters processed: {len(clusters)} ({multi} multi-source)")
    print(f"  Audio             : {'yes' if mp3_path else 'no'}")
    print(f"  S&P 500           : {sp500 if sp500 else 'unavailable'}")


if __name__ == "__main__":
    main()
