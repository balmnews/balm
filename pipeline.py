#!/usr/bin/env python3
"""Balm pipeline — fetches news, clusters, synthesizes via Claude, generates digest HTML + audio."""

import argparse
import json
import os
import re
import sys
import time
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
BASE_URL = "https://balm.news"

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 8000

ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel
AUDIO_ENABLED = False  # Set to True when ElevenLabs API key is refreshed and audio pipeline is ready for production


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

# Each entry: (source_name, feed_url, max_articles)
# Standard feeds: max 8 articles. Official primary sources: max 5.
RSS_FEEDS = [
    # Media feeds — up to 8 articles each
    ("Fox News",         "https://feeds.foxnews.com/foxnews/latest",                            8),
    ("Fox News World",   "https://feeds.foxnews.com/foxnews/world",                             8),
    ("Fox News Politics","https://feeds.foxnews.com/foxnews/politics",                          8),
    ("WSJ Markets",      "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",   8),
    ("BBC News",         "http://feeds.bbci.co.uk/news/rss.xml",                                8),
    ("BBC World",        "http://feeds.bbci.co.uk/news/world/rss.xml",                          8),
    # Wire service — NOTE: Reuters has been known to restrict or move RSS endpoints;
    # if these return 0 articles, Reuters may have discontinued free RSS access.
    ("Reuters Top News", "https://feeds.reuters.com/reuters/topNews",                           8),
    ("Reuters Domestic", "https://feeds.reuters.com/Reuters/domesticNews",                      8),
    ("Reuters Politics", "https://feeds.reuters.com/Reuters/politicsNews",                      8),
    # Public media
    ("NPR News",         "https://feeds.npr.org/1001/rss.xml",                                  8),
    ("PBS NewsHour",     "https://www.pbs.org/newshour/feeds/rss/headlines",                    8),
    # Topical specialist feeds
    ("Guardian Environment", "https://www.theguardian.com/environment/rss",                     8),
    ("SCOTUSblog",       "https://www.scotusblog.com/feed/",                                     8),
    # Official primary sources — public domain, no licensing concerns.
    # Capped at 5: these publish less frequently; quality over quantity.
    # They naturally cluster with news articles on the same policy decisions,
    # giving Claude primary source material alongside press coverage.
    ("White House",      "https://www.whitehouse.gov/feed/",                                    5),
    ("Federal Reserve",  "https://www.federalreserve.gov/feeds/press_all.xml",                  5),
    ("CDC Health Alerts","https://tools.cdc.gov/api/v2/resources/media/403372.rss",             5),
    ("State Department", "https://www.state.gov/rss-feeds/press-releases/",                     5),
]

# Names of official primary source feeds — used for distinguished logging
_OFFICIAL_RSS_SOURCES = {"White House", "Federal Reserve", "CDC Health Alerts", "State Department"}

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
- For product recalls, safety alerts, and consumer health notices: always include the specific product name or brand name in the brief summary — this is the information that makes the story immediately actionable for readers.

MULTI-SOURCE SYNTHESIS:
When multiple sources cover the same story, you will receive all versions together as a cluster.
- Identify facts that appear consistently across all sources — these are high-confidence facts
- Identify where sources diverge in framing, emphasis, or detail — note this as genuine uncertainty
- Write a synthesis that reflects consensus facts
- Where sources diverge on matters of fact (not just framing), reflect that uncertainty: "accounts differ on..." or "figures vary by source"
- Where sources diverge only in framing or emphasis, strip the framing and report the neutral fact
- Never present a contested fact as settled
- The goal is a synthesis no single outlet would write — more complete and more neutral than any individual source

STORY LENGTH — TWO DISTINCT FORMATS:

You are producing TWO genuinely different versions of each story. They should feel like different products, not the same content slightly expanded.

BRIEF: 1-2 sentences maximum. Absolute maximum 40 words. The factual kernel only — who did what, where. No context, no background, no implications, no qualifications. A reader scanning 16 briefs should be done in 90 seconds.

Examples of correct brief length:
- "The Federal Reserve indicated a September interest rate cut is possible, citing reduced inflation pressure from the labor market."
- "Israeli forces captured Beaufort Castle in southern Lebanon and expanded evacuation orders as the ground operation extended beyond the Litani River."

FULL: Substantially longer — target 150-300 words for medium stories, 300-500 words for long/complex stories. This is the complete story for a reader who wants full understanding. Structure it as:
1. What happened — expand the brief with key details
2. Context and background — what does a reader need to know to understand why this matters
3. Key specifics — figures, named parties, timeline, disputed elements
4. Significance — implications for readers, what happens next

For ongoing stories: include enough background that a reader who missed previous coverage can follow without confusion.
For complex or landmark stories (Supreme Court rulings, major geopolitical developments, economic policy shifts): aim for the upper end of the word count range. These stories deserve space.

LENGTH TIERS:
- SHORT (routine updates): brief = 1 sentence, full = 2-3 sentences (50-80 words)
- MEDIUM (standard news): brief = 1-2 sentences, full = 4-6 sentences (100-180 words)
- LONG (significant developments): brief = 2 sentences, full = 8-12 sentences (200-300 words)
- COMPLEX (landmark decisions, major crises): brief = 2 sentences, full = 12-18 sentences (300-500 words)

The brief/full toggle exists because readers have genuinely different needs at different moments. A reader with 3 minutes reads all the briefs. A reader with 30 minutes reads full summaries on the stories they care about. Honor that difference — make the full version worth the click.

Include a "length" field in your JSON response for each article:
"length": "short" | "medium" | "long"

Use "long" for both LONG and COMPLEX stories. The full_summary is also the podcast script — the listener cannot re-read, so provide sufficient context per sentence.

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
      "brief_summary": "Dynamic length brief summary.",
      "full_summary": "Dynamic length full summary written for audio consumption.",
      "length": "medium",
      "isDifficult": false
    }
  ]
}

cluster_id must match the [CLUSTER N] number from the input. One output article per cluster.
Set isDifficult: true for DIFFICULT NEWS items only.
Set length to "short", "medium", or "long" per the STORY LENGTH rules above.
Return null in the array position for excluded clusters.
Return between 10 and 16 articles total.
Category display order: GEOPOLITICS, ECONOMY, DOMESTIC POLICY, SCIENCE & HEALTH, TECHNOLOGY, NATURAL EVENTS, SPORTS — then DIFFICULT NEWS last and collapsed."""


DIFFICULT_NEWS_PROMPT = """You are a pre-classifier for a news digest. Your sole task is to identify
which story clusters contain tragic, violent, or mass-casualty content that requires special editorial
handling (the DIFFICULT NEWS category).

DIFFICULT NEWS includes:
- Mass casualty events (shootings, bombings, natural disasters with significant death tolls)
- Violent crimes with broad relevance (not isolated incidents — events with policy, pattern, or scale significance)
- Large-scale tragedies affecting communities

NOT difficult news:
- Standard geopolitical conflicts reported factually (war updates, diplomatic disputes)
- Economic hardship stories
- Health crises covered as policy or science
- Stories about violence that are primarily about policy or legal outcomes

For each cluster, output true if it is DIFFICULT NEWS, false otherwise.

Return ONLY valid JSON — an array of booleans, one per cluster, in the same order:
{"difficult": [false, true, false, false, true]}"""


TOP_STORIES_PROMPT = """You are the editorial director for Balm, a calm news digest. Your task is to
identify 2-4 stories from today's digest that are the most broadly significant — stories that a
well-informed adult would most want to know about first.

Selection criteria:
- Broad significance: affects many people or has major implications
- Timeliness: genuinely new development, not a continuation of weeks-old news
- Variety: select from different categories when possible — avoid clustering top stories in one area
- Calm importance: not sensational or alarming, but genuinely consequential

Return ONLY valid JSON:
{
  "top_stories": [
    {"story_id": "story_0", "reason": "One brief sentence on why this story is broadly significant."},
    {"story_id": "story_2", "reason": "One brief sentence on why this story is broadly significant."}
  ]
}

story_id must exactly match a story_id from the input list. Select 2-4 stories."""


PM_DEDUP_PROMPT = """You are an editorial assistant for Balm, a twice-daily news digest. The AM edition
has already been published. You are reviewing the PM edition's candidate stories.

Your task: for each PM story, determine whether it is a genuine new development since the AM edition,
or whether it is essentially the same story already covered in the AM edition with no substantial new information.

Rules:
- If the PM story has significant new facts, developments, or a materially different angle → KEEP
- If the PM story covers the same event as an AM story with only minor updates or rewording → EXCLUDE
- Breaking news that emerged after the AM edition → always KEEP
- Routine follow-ups with no new substance → EXCLUDE

Return ONLY valid JSON — an array of booleans, one per PM story, in the same order as input.
true = keep this story, false = exclude as AM duplicate:
{"keep": [true, false, true, true, false]}"""


CATEGORY_ORDER_PROMPT = """You are the layout editor for Balm, a calm news digest. Based on today's
stories, suggest the order in which categories should appear in the digest.

RULES:
- DIFFICULT NEWS must always be last (it appears as a collapsed section)
- GEOPOLITICS, ECONOMY, and DOMESTIC POLICY should generally appear early when significant
- Categories with no stories in today's digest will be omitted automatically — order only present categories
- The order should reflect today's editorial weight: the category with the most significant stories first

Return ONLY valid JSON:
{"order": ["GEOPOLITICS", "ECONOMY", "DOMESTIC POLICY", "SCIENCE & HEALTH", "TECHNOLOGY", "NATURAL EVENTS", "SPORTS", "DIFFICULT NEWS"]}

Adjust the order (except DIFFICULT NEWS must be last) to reflect today's editorial significance."""

MARKET_TREND_PROMPT = """You are writing one calm, factual sentence about recent stock market performance for Balm, a neutral news digest.

You will receive recent S&P 500 closing values in chronological order (oldest first).

Write exactly one sentence characterizing the trend over this period.

RULES:
- No specific numbers or percentages
- No alarm language — avoid "plunged", "surged", "crashed", "soared"
- Calm directional language only: "trended upward", "declined modestly", "remained relatively flat", "been mixed with no clear direction", "recovered after earlier losses"
- If the trend is genuinely ambiguous or volatile, say so calmly: "Markets have been volatile with no clear trend over the past week"
- Write in past tense, and name a CONCRETE time period on the order of 5 to 10 days — for example "over the past week", "in the last few days", "over the past several days", "for the past week and a half"
- Never use vague time references. Do not write "recently", "lately", "in recent sessions", "of late", or any phrase that leaves the period unspecified
- The sentence will appear below the Economy section heading in a news digest
- Maximum 20 words

Examples of correct output:
"Markets have trended modestly higher over the past week."
"Stocks have declined gradually over the past several days."
"Markets have been relatively flat with no clear direction this week."
"Stocks have recovered over the last few days after declining earlier."

Return ONLY the sentence. No preamble, no quotation marks."""


# ---------------------------------------------------------------------------
# Article fetching — API sources
# ---------------------------------------------------------------------------

# NewsData.io returns `source_name` title-cased from its internal slug, so
# acronym outlets arrive as "Wsj", "Npr", "Bbc". These are reader-facing in the
# sources list under every story, so map the known ones back to real casing.
# Anything unlisted passes through unchanged.
_SOURCE_DISPLAY_NAMES = {
    "wsj":            "WSJ",
    "npr":            "NPR",
    "bbc":            "BBC",
    "bbc news":       "BBC News",
    "cnn":            "CNN",
    "cnbc":           "CNBC",
    "pbs":            "PBS",
    "pbs newshour":   "PBS NewsHour",
    "abc news":       "ABC News",
    "cbs news":       "CBS News",
    "nbc news":       "NBC News",
    "upi":            "UPI",
    "nyt":            "The New York Times",
    "ap":             "The Associated Press",
    "usa today":      "USA Today",
    "us news":        "U.S. News & World Report",
    "espn":           "ESPN",
    "nypost":         "New York Post",
    "scotusblog":     "SCOTUSblog",
}


def normalize_source_name(name: str) -> str:
    """Return an outlet's display name with correct casing."""
    if not name:
        return name
    return _SOURCE_DISPLAY_NAMES.get(name.strip().lower(), name.strip())


def is_fresh(date_str: str, max_age_hours: int = 48) -> bool:
    """Return True if the article date is within max_age_hours of now.

    Returns True (keep) when date_str is absent or unparseable — we cannot
    determine staleness so the article gets the benefit of the doubt.
    """
    if not date_str:
        return True
    try:
        from dateutil import parser as _dp
        from datetime import timezone as _tz
        pub_date = _dp.parse(date_str)
        if pub_date.tzinfo is None:
            pub_date = pub_date.replace(tzinfo=_tz.utc)
        age_hours = (datetime.now(_tz.utc) - pub_date).total_seconds() / 3600
        return age_hours <= max_age_hours
    except Exception:
        return True  # Can't parse date — keep it


def fetch_newsdata(api_key: str) -> list[dict]:
    """Fetch top headlines from NewsData.io.

    Free tier explicitly permits commercial and production use — unlike
    NewsAPI's Developer plan which restricts to non-production only.
    Covers the same broad category set as the former NewsAPI integration.
    """
    categories = ["world", "business", "technology", "health", "science", "sports"]
    articles = []
    total_stale = 0
    for category in categories:
        try:
            resp = requests.get(
                "https://newsdata.io/api/1/news",
                params={
                    "apikey": api_key,
                    "category": category,
                    "language": "en",
                    "country": "us",
                    "size": 5,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("results", []):
                if not item.get("title") or not item.get("description"):
                    continue
                pub_str = item.get("pubDate", "")
                if not is_fresh(pub_str):
                    total_stale += 1
                    continue
                articles.append({
                    "title": item.get("title", ""),
                    "description": (item.get("description") or item.get("content", ""))[:300],
                    "url": item.get("link", ""),
                    "source": normalize_source_name(item.get("source_name", "NewsData")),
                    "published_at": pub_str,
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"  [WARN] NewsData {category}: {e}", file=sys.stderr)
    kept = len(articles)
    if total_stale:
        print(f"  NewsData.io: {kept + total_stale} fetched, {total_stale} stale, {kept} kept")
    else:
        print(f"  NewsData.io: {kept} articles")
    return articles


def fetch_guardian(api_key: str) -> list[dict]:
    """Fetch articles from The Guardian with full article body text.

    Uses show-fields=bodyText,trailText,headline to retrieve full article
    content. Body truncated at 800 chars — roughly 3-4 sentences of actual
    journalism, substantially more context than the former snippet-only fetch.
    The Guardian explicitly permits full content retrieval on the free tier.
    """
    sections = ["world", "business", "technology", "science", "sport",
                "politics", "environment", "us-news"]
    articles = []
    total_stale = 0
    for section in sections:
        try:
            resp = requests.get(
                "https://content.guardianapis.com/search",
                params={
                    "section": section,
                    "page-size": 5,
                    "show-fields": "bodyText,trailText,headline",
                    "order-by": "newest",
                    "lang": "en",
                    "api-key": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            for a in data.get("response", {}).get("results", [])[:5]:
                pub_str = a.get("webPublicationDate", "")
                if not is_fresh(pub_str):
                    total_stale += 1
                    continue
                fields = a.get("fields", {})
                # Prefer full body text; fall back to trail text snippet
                body = fields.get("bodyText", "") or fields.get("trailText", "")
                title = fields.get("headline", "") or a.get("webTitle", "")
                if not title:
                    continue
                articles.append({
                    "title": title,
                    "description": body[:800].strip(),
                    "url": a.get("webUrl", ""),
                    "source": "The Guardian",
                    "published_at": pub_str,
                })
            time.sleep(0.3)
        except Exception as e:
            print(f"  [WARN] Guardian {section}: {e}", file=sys.stderr)
    kept = len(articles)
    if total_stale:
        print(f"  Guardian: {kept + total_stale} fetched, {total_stale} stale, {kept} kept (full text)")
    else:
        print(f"  Guardian: {kept} articles (full text)")
    return articles


def fetch_nyt(api_key: str) -> list[dict]:
    # Five core sections only. Sports, politics, and climate were dropped:
    # they hit 429 errors consistently across every run (rate limit), and
    # the same coverage arrives via RSS feeds (Fox News Politics, Reuters
    # Politics, Guardian Environment) that don't require API quotas.
    sections = ["world", "business", "technology", "health", "science"]
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
        # NYT free tier: 10 requests/minute. 6.0s between sections keeps the
        # combined request rate safely under the limit (5 sections × 6.0s = 30s).
        time.sleep(6.0)
    return articles


# ---------------------------------------------------------------------------
# Article fetching — RSS feeds
# ---------------------------------------------------------------------------

def fetch_rss_feeds() -> list[dict]:
    """Fetch articles from public RSS feeds. No API key required."""
    articles = []
    official_count = 0
    for source_name, feed_url, max_articles in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            kept = 0
            stale = 0
            for entry in feed.entries:
                if kept >= max_articles:
                    break
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or "").strip()
                if not title or not link:
                    continue
                # Use the richer published date if available; fall back to updated
                pub_str = entry.get("published", "") or entry.get("updated", "")
                if not is_fresh(pub_str):
                    stale += 1
                    print(
                        f"  [WARN] Stale RSS ({source_name}): {title[:70]}"
                        f" (published {pub_str})",
                        file=sys.stderr,
                    )
                    continue
                summary = (entry.get("summary") or entry.get("description") or "").strip()
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s+", " ", summary).strip()
                articles.append({
                    "title": title,
                    "description": summary[:400],
                    "url": link,
                    "source": source_name,
                    "published_at": pub_str,
                })
                kept += 1
            if source_name in _OFFICIAL_RSS_SOURCES:
                official_count += kept
            else:
                if stale:
                    print(f"  RSS {source_name}: {kept + stale} fetched, "
                          f"{stale} stale, {kept} kept")
                else:
                    print(f"  RSS {source_name}: {kept} articles")
        except Exception as e:
            print(f"  [WARN] RSS {source_name}: {e}", file=sys.stderr)
    if official_count:
        print(f"  RSS Official Sources: {official_count} articles "
              f"(White House, Fed, CDC, State Dept)")
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
# Voyage AI embedding-based clustering
# ---------------------------------------------------------------------------

CLUSTER_PROMPT = """You are an editorial clustering engine for a news digest.

You will receive a numbered list of article headlines and brief descriptions.
Your job is to group them by underlying news event — articles that are reporting
on the same real-world event should be in the same cluster.

CLUSTERING RULES:
- Same event = same real-world occurrence, regardless of framing or vocabulary
- "US strikes near Hormuz" and "Iran condemns American military action in Gulf"
  are the same event — cluster them together
- "Fox News: violent protesters clash with ICE" and "CNN: agents use force on
  demonstrators" are the same event — cluster them together
- Different aspects of a continuing story (Iran strikes + oil price response)
  CAN be clustered together if they are directly causally connected
- Different events that share a topic (two separate shootings, two separate
  elections) must be in separate clusters even if they share vocabulary
- When uncertain, keep articles in separate clusters — do not over-merge
- Every article must appear in exactly one cluster
- Single-article clusters are fine and expected for unique stories

Return ONLY valid JSON, no preamble, no markdown, no explanation:
{
  "clusters": [
    [0, 4, 7, 12],
    [1, 9],
    [2],
    [3, 6, 11]
  ]
}

Each inner array is a cluster containing the index numbers of articles
that belong together. Every index from 0 to N-1 must appear exactly once."""

# Maximum articles per single clustering call. Lists larger than this are
# split into two batches to keep index counts manageable for the model.
CLUSTER_BATCH_SIZE = 80

CROSS_BATCH_MERGE_PROMPT = """You are reviewing story clusters assembled for a news digest.
Two batches of articles were clustered independently. You will see one representative headline
per cluster from each batch (labeled A0, A1, A2... and B0, B1, B2...).

Your task: identify cross-batch pairs that clearly cover the same underlying real-world event
and should be merged into a single cluster.

Rules:
- Only merge when two clusters are obviously the same event, not just the same topic
- When uncertain, do not merge — keep clusters separate
- Each cluster index may appear in at most one merge pair

Return ONLY valid JSON, no preamble, no markdown:
{
  "merges": [[0, 2], [3, 0]]
}

Each inner pair is [A_cluster_index, B_cluster_index]. If no merges are needed: {"merges": []}"""


def _cluster_single_batch(articles: list[dict], anthropic_key: str,
                           label: str = "") -> list[list[dict]]:
    """Cluster one batch of articles (must be <= CLUSTER_BATCH_SIZE).

    Returns a list of clusters (each cluster is a list of article dicts).
    Falls back to singletons on API failure.
    """
    if not articles:
        return []
    if len(articles) == 1:
        return [articles]

    tag = f"[{label}] " if label else ""
    client = Anthropic(api_key=anthropic_key)

    # Build compact article list: headline + first 120 chars of description
    article_lines = []
    for i, a in enumerate(articles):
        desc = (a.get("description") or "").strip()[:120]
        if desc:
            article_lines.append(f"[{i}] {a['title']} | {desc}")
        else:
            article_lines.append(f"[{i}] {a['title']}")

    base_body = (
        f"Group these {len(articles)} articles into clusters by news event:\n\n"
        + "\n".join(article_lines)
    )

    raw_clusters: list[list[int]] | None = None

    for attempt in range(3):
        # On retries, prepend an explicit index-range reminder to the prompt.
        # This directly addresses the 'Invalid index' failure mode where Claude
        # generates out-of-range indices on large lists.
        if attempt > 0:
            range_note = (
                f"IMPORTANT: Article indices are 0 through {len(articles) - 1}. "
                f"Every index in your response must be within this range.\n\n"
            )
            prompt = range_note + base_body
        else:
            prompt = base_body

        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=4000,
                system=CLUSTER_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
            # Slice to the outermost JSON object so trailing prose after the
            # closing brace doesn't cause 'Extra data' parse errors.
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                text = text[start:end + 1]
            data = json.loads(text)
            raw_clusters = data.get("clusters", [])

            # Validate — every index 0..N-1 must appear exactly once
            seen: set[int] = set()
            for cluster in raw_clusters:
                for idx in cluster:
                    if idx in seen or not (0 <= idx < len(articles)):
                        raise ValueError(
                            f"Invalid index {idx} "
                            f"(valid range: 0–{len(articles) - 1})"
                        )
                    seen.add(idx)

            missing = set(range(len(articles))) - seen
            if missing:
                for idx in sorted(missing):
                    raw_clusters.append([idx])
                print(
                    f"  [WARN] {tag}Clustering: {len(missing)} article(s) appended as singletons",
                    file=sys.stderr,
                )
            break

        except Exception as e:
            print(
                f"  [WARN] {tag}Claude clustering error (attempt {attempt + 1}): {e}",
                file=sys.stderr,
            )
            if attempt == 2:
                print(
                    f"  [WARN] {tag}Claude clustering failed — falling back to singletons",
                    file=sys.stderr,
                )
                return [[a] for a in articles]

    return [[articles[i] for i in cluster] for cluster in raw_clusters]  # type: ignore[index]


def _cross_batch_merge(
    clusters_a: list[list[dict]],
    clusters_b: list[list[dict]],
    anthropic_key: str,
) -> list[list[dict]]:
    """Ask Claude if any A-batch cluster should merge with a B-batch cluster.

    Uses one representative headline per cluster. Non-blocking — on failure,
    returns clusters_a + clusters_b with no merges applied.
    """
    if not clusters_a or not clusters_b:
        return clusters_a + clusters_b

    lines = ["Batch A clusters:"]
    for i, c in enumerate(clusters_a):
        lines.append(f"  [A{i}] {c[0]['title']}")
    lines.append("\nBatch B clusters:")
    for i, c in enumerate(clusters_b):
        lines.append(f"  [B{i}] {c[0]['title']}")

    client = Anthropic(api_key=anthropic_key)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=CROSS_BATCH_MERGE_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        # Slice to the outermost JSON object so trailing prose after the
        # closing brace doesn't cause 'Extra data' parse errors.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            raw = raw[start:end + 1]
        merges: list[list[int]] = json.loads(raw).get("merges", [])
    except Exception as e:
        print(
            f"  [WARN] Cross-batch merge check failed ({e}) — skipping",
            file=sys.stderr,
        )
        return clusters_a + clusters_b

    if not merges:
        print("  Cross-batch merge: no cross-batch merges identified")
        return clusters_a + clusters_b

    # Apply merges — each cluster index may only appear once
    consumed_a: set[int] = set()
    consumed_b: set[int] = set()
    combined: list[list[dict]] = []
    merge_count = 0

    for pair in merges:
        if len(pair) != 2:
            continue
        ai, bi = int(pair[0]), int(pair[1])
        if (
            not (0 <= ai < len(clusters_a))
            or not (0 <= bi < len(clusters_b))
            or ai in consumed_a
            or bi in consumed_b
        ):
            continue
        combined.append(clusters_a[ai] + clusters_b[bi])
        consumed_a.add(ai)
        consumed_b.add(bi)
        merge_count += 1

    # Append remaining unmerged clusters from each batch
    for i, c in enumerate(clusters_a):
        if i not in consumed_a:
            combined.append(c)
    for i, c in enumerate(clusters_b):
        if i not in consumed_b:
            combined.append(c)

    print(f"  Cross-batch merge: {merge_count} cross-batch merge(s) applied")
    return combined


def cluster_articles(articles: list[dict], anthropic_key: str) -> list[list[dict]]:
    """Cluster articles by story identity using Claude's semantic understanding.

    For article pools larger than CLUSTER_BATCH_SIZE, splits into two roughly
    equal batches, clusters each independently, then runs a cross-batch merge
    check. This keeps each Claude call under the threshold where index tracking
    is reliable.

    Falls back to single-article clusters if all API calls fail.
    """
    if not articles:
        return []
    if len(articles) == 1:
        return [articles]

    if len(articles) <= CLUSTER_BATCH_SIZE:
        clusters = _cluster_single_batch(articles, anthropic_key)
    else:
        mid = len(articles) // 2
        batch_a = articles[:mid]
        batch_b = articles[mid:]
        print(
            f"  Large article pool ({len(articles)} articles): "
            f"splitting into two batches ({len(batch_a)} + {len(batch_b)})"
        )
        clusters_a = _cluster_single_batch(batch_a, anthropic_key, label="Batch A")
        clusters_b = _cluster_single_batch(batch_b, anthropic_key, label="Batch B")
        clusters = _cross_batch_merge(clusters_a, clusters_b, anthropic_key)

    # Log cluster distribution
    multi = [c for c in clusters if len(c) > 1]
    single = [c for c in clusters if len(c) == 1]
    total_merges = len(articles) - len(clusters)
    print(
        f"  Clustering: {len(articles)} articles → {len(clusters)} clusters "
        f"({len(multi)} multi-source, {len(single)} single-source, "
        f"{total_merges} merges)"
    )
    for c in sorted(multi, key=len, reverse=True):
        outlets = " · ".join(dict.fromkeys(a["source"] for a in c))
        headlines = " | ".join(a["title"][:50] for a in c[:3])
        print(f"    [{len(c)} sources] {outlets}")
        print(f"      {headlines}")

    return clusters



def _extract_partial_reviews(raw: str) -> list[dict]:
    """Extract complete review objects from a potentially truncated JSON response.

    Walks the raw string character by character, tracking brace depth to identify
    complete top-level JSON objects inside the "reviews" array. Returns every
    object that parses cleanly and contains a "cluster_id" field.
    """
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw).strip()
    m = re.search(r'"reviews"\s*:\s*\[', raw)
    if not m:
        return []
    pos = m.end()
    complete: list[dict] = []
    depth = 0
    obj_start: int | None = None
    in_string = False
    i = pos
    while i < len(raw):
        ch = raw[i]
        if in_string:
            if ch == "\\":
                i += 2
                continue
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        obj = json.loads(raw[obj_start: i + 1])
                        if isinstance(obj, dict) and "cluster_id" in obj:
                            complete.append(obj)
                    except json.JSONDecodeError:
                        pass
                    obj_start = None
            elif ch == "]" and depth == 0:
                break
        i += 1
    return complete


EDITORIAL_REVIEW_PROMPT = """You are reviewing story clusters assembled for the Balm news digest.
Each cluster contains one or more news articles that the system believes cover the same story.

Your task: review each cluster and decide whether it is correctly grouped.

For each cluster, respond with one of:
  "approve" — the cluster is coherent; articles cover the same underlying story
  "split"   — the cluster contains articles about different stories; split into singletons
  "merge: X,Y" — clusters X and Y (by cluster number) should be merged into one

Rules:
- Approve the vast majority of clusters. Only split or merge when the error is clear.
- Split only when articles in the cluster are clearly about different events.
- Merge only when two separate clusters are obviously about the same story.
- Do not merge clusters that cover related-but-distinct events.
- You may approve, split, or merge — but do not invent new clusters.

Output ONLY valid JSON, no markdown, no preamble:
{
  "reviews": [
    {"cluster_id": 1, "action": "approve"},
    {"cluster_id": 2, "action": "split"},
    {"cluster_id": 3, "action": "merge: 3,7"}
  ]
}
"""


def editorial_review(
    clusters: list[list[dict]],
    anthropic_key: str,
) -> list[list[dict]]:
    """Ask Claude to review cluster structure and apply approved splits/merges.

    This is a lightweight single call (low token budget) that acts as a
    final sanity check on the cluster groupings before synthesis. It catches
    edge cases the embedding + backstop pipeline missed.

    Failures are non-blocking: if the call fails or returns invalid JSON,
    the original clusters are returned unchanged.
    """
    if not clusters:
        return clusters

    # Build a compact summary of each cluster for review
    lines = [f"Review these {len(clusters)} story clusters:\n"]
    for ci, cluster in enumerate(clusters, 1):
        titles = "; ".join(a["title"] for a in cluster)
        lines.append(f"[CLUSTER {ci}] ({len(cluster)} article{'s' if len(cluster) != 1 else ''}): {titles}")

    prompt = "\n".join(lines)

    client = Anthropic(api_key=anthropic_key)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            system=EDITORIAL_REVIEW_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        try:
            data = json.loads(raw)
            reviews = data.get("reviews", [])
        except json.JSONDecodeError:
            # Response may be truncated — extract complete review objects character by character
            reviews = _extract_partial_reviews(raw)
            if reviews:
                print(f"  [FALLBACK] Extracted {len(reviews)} complete review objects "
                      f"from truncated editorial response", file=sys.stderr)
            else:
                print(f"  [WARN] Editorial review JSON unparseable; using original clusters.",
                      file=sys.stderr)
                return clusters
    except Exception as e:
        print(f"  [WARN] Editorial review failed ({e}); using original clusters.", file=sys.stderr)
        return clusters

    # Index clusters for mutation (1-based cluster_id → 0-based index)
    result: list[list[dict] | None] = [list(c) for c in clusters]
    merges_applied = 0
    splits_applied = 0

    # First pass: merges
    for review in reviews:
        action = review.get("action", "approve")
        if not action.startswith("merge:"):
            continue
        try:
            ids_str = action.split(":", 1)[1]
            ids = [int(x.strip()) - 1 for x in ids_str.split(",")]
            # Validate all ids
            if not all(0 <= i < len(result) for i in ids):
                continue
            # Merge all into the first
            primary = ids[0]
            for other in ids[1:]:
                if result[other] is not None and result[primary] is not None:
                    result[primary].extend(result[other])
                    result[other] = None
            merges_applied += 1
        except (ValueError, IndexError):
            continue

    # Second pass: splits
    for review in reviews:
        action = review.get("action", "approve")
        if action != "split":
            continue
        cid = review.get("cluster_id", 0) - 1
        if 0 <= cid < len(result) and result[cid] is not None and len(result[cid]) > 1:
            articles = result[cid]
            result[cid] = None
            for a in articles:
                result.append([a])
            splits_applied += 1

    final = [c for c in result if c is not None]

    if merges_applied or splits_applied:
        print(f"  Editorial review: {merges_applied} merge(s), {splits_applied} split(s) applied"
              f" → {len(final)} clusters")
    else:
        print(f"  Editorial review: all {len(clusters)} clusters approved")

    return final


# ---------------------------------------------------------------------------
# Claude editorial processing
# ---------------------------------------------------------------------------

def build_cluster_prompt(clusters: list[list[dict]],
                         difficult_flags: list[bool] | None = None) -> str:
    lines = [
        f"Process the following {len(clusters)} story clusters. "
        "Each cluster contains one or more news sources covering the same underlying event.\n"
    ]
    for ci, cluster in enumerate(clusters, 1):
        is_difficult = difficult_flags[ci - 1] if difficult_flags else False
        difficult_tag = " [PRE-CLASSIFIED: DIFFICULT NEWS]" if is_difficult else ""
        if len(cluster) == 1:
            a = cluster[0]
            lines.append(f"[CLUSTER {ci}] — 1 source{difficult_tag}")
            lines.append(f"  Source: {a['source']}")
            lines.append(f"  Title: {a['title']}")
            lines.append(f"  URL: {a['url']}")
            if a.get("description"):
                lines.append(f"  Description: {a['description'][:300]}")
        else:
            lines.append(f"[CLUSTER {ci}] — {len(cluster)} sources covering the same story{difficult_tag}")
            for si, a in enumerate(cluster, 1):
                lines.append(f"  — Source {si}: {a['source']}")
                lines.append(f"    Title: {a['title']}")
                lines.append(f"    URL: {a['url']}")
                if a.get("description"):
                    lines.append(f"    Description: {a['description'][:300]}")
        lines.append("")
    return "\n".join(lines)


def call_claude(clusters: list[list[dict]], anthropic_key: str,
                difficult_flags: list[bool] | None = None) -> list[dict]:
    client = Anthropic(api_key=anthropic_key)
    user_prompt = build_cluster_prompt(clusters, difficult_flags)

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
    """Add sequential 'ref' and stable 'story_id' fields to articles in place."""
    for i, article in enumerate(articles, 1):
        article["ref"] = i
        article["story_id"] = f"story_{i - 1}"  # 0-indexed anchor target


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


def calculate_market_trend(docs_dir: Path, anthropic_key: str) -> str:
    """Return a one-sentence S&P 500 trend description for the Economy section.

    Reads sp500_close values from the last 10 metadata JSONs, calls Claude
    with MARKET_TREND_PROMPT, and returns the sentence. Falls back to a
    simple directional string if fewer than 3 values exist or Claude fails.
    Non-blocking — returns "" on any unexpected error.
    """
    try:
        entries: list[tuple[str, float]] = []
        for f in sorted(docs_dir.glob("????-??-??-??.json"), reverse=True):
            try:
                meta = json.loads(f.read_text())
                close = meta.get("sp500_close")
                date = meta.get("date", f.stem[:10])
                if close is not None:
                    entries.append((date, float(close)))
            except Exception:
                continue
            if len(entries) >= 10:
                break

        if len(entries) < 3:
            return ""

        # Chronological order (oldest first) for the prompt
        prices = [v for _, v in reversed(entries)]

        # Simple directional fallback: most recent vs. average of older half
        midpoint = len(prices) // 2
        avg_older = sum(prices[:midpoint]) / midpoint
        recent = prices[-1]
        if recent > avg_older * 1.005:
            fallback = "Markets have trended modestly higher over the past week."
        elif recent < avg_older * 0.995:
            fallback = "Stocks have declined gradually over the past several days."
        else:
            fallback = "Markets have been relatively flat with no clear direction this week."

        try:
            client = Anthropic(api_key=anthropic_key)
            prices_str = ", ".join(str(p) for p in prices)
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=60,
                system=MARKET_TREND_PROMPT,
                messages=[{"role": "user", "content": f"S&P 500 closing prices, oldest first: {prices_str}"}],
            )
            sentence = msg.content[0].text.strip().strip('"').strip("'")
            if sentence:
                return sentence
        except Exception as e:
            print(f"  [WARN] Market trend Claude call failed: {e}", file=sys.stderr)

        return fallback

    except Exception as e:
        print(f"  [WARN] calculate_market_trend failed: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

def collect_archive(docs_dir: Path) -> list[dict]:
    """Return sorted list of all published digests for archive nav.

    Primary scan: metadata JSON files (YYYY-MM-DD-{am,pm}.json).
    Because the current run's JSON is written at step 9 — before this
    function is called in step 13 — the current digest is always included.

    Fallback scan: digest HTML files (YYYY-MM-DD-{am,pm}.html) for any
    digest whose metadata JSON is absent (e.g. a partial run that wrote
    HTML before a JSON write failure). HTML files are the actual published
    artifact, so the archive must reflect them.
    """
    entries: list[dict] = []
    seen_stems: set[str] = set()

    # Primary: JSON files carry rich metadata (run, date)
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
            seen_stems.add(f.stem)
        except Exception:
            continue

    # Fallback: HTML files for any digest that has no metadata JSON
    for f in sorted(docs_dir.glob("????-??-??-??.html"), reverse=True):
        if f.stem in seen_stems:
            continue  # already captured via JSON
        parts = f.stem.split("-")
        if len(parts) != 4 or parts[3] not in ("am", "pm"):
            continue
        date_str = "-".join(parts[:3])
        run = parts[3]
        entries.append({
            "date": date_str,
            "run": run,
            "label": f"{date_str} {'AM' if run == 'am' else 'PM'}",
            "file": f.name,
        })

    entries.sort(key=lambda e: (e["date"], e["run"]), reverse=True)
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


def _group_by_category(articles: list[dict],
                        order: list[str] | None = None) -> list[dict]:
    """Group articles by category, using dynamic order if provided."""
    grouped: dict[str, list] = {}
    for article in articles:
        cat = article.get("category", "UNCATEGORIZED")
        grouped.setdefault(cat, []).append(article)
    effective_order = order if order else CATEGORY_ORDER
    # Always ensure DIFFICULT NEWS is last if present
    if "DIFFICULT NEWS" in effective_order:
        effective_order = [c for c in effective_order if c != "DIFFICULT NEWS"] + ["DIFFICULT NEWS"]
    return [
        {"name": cat, "articles": grouped[cat]}
        for cat in effective_order
        if cat in grouped
    ]


def ensure_og_image(docs_dir: Path) -> None:
    """Generate docs/og-image.png, the 1200x630 social share card.

    Posting a Balm link to Reddit, Bluesky, LinkedIn or iMessage renders this
    card. It is a static asset — the design never changes — so this returns
    immediately once the file exists, and the two font downloads only ever run
    on a fresh checkout. Non-blocking: on any failure the card is skipped and
    the pages simply fall back to a text-only preview.

    Note this does not conflict with the image-free digest rule: the card is a
    link preview, never rendered on the site itself. It reproduces the masthead
    wordmark using the same tracking and stroke ratios as ensure_static_icons().
    """
    out_path = docs_dir / "og-image.png"
    if out_path.exists():
        return

    import tempfile
    import os as _os

    _PARCHMENT, _SLATE = "#f2ede4", "#6b82a8"
    _SECONDARY, _RULE, _MUTED = "#6a6058", "#c8c0b4", "#9a9288"
    _FONTS = {
        "caveat": "https://github.com/googlefonts/caveat/raw/main/fonts/ttf/Caveat-Bold.ttf",
        "serif":  "https://github.com/google/fonts/raw/main/ofl/sourceserif4/SourceSerif4%5Bopsz,wght%5D.ttf",
        "italic": "https://github.com/google/fonts/raw/main/ofl/sourceserif4/SourceSerif4-Italic%5Bopsz,wght%5D.ttf",
    }
    _LS_RATIO, _STROKE_RATIO, _SS = 8 / 52, 0.6 / 52, 2
    W, H = 1200, 630

    paths: dict[str, str] = {}
    try:
        from PIL import Image, ImageDraw, ImageFont

        for key, url in _FONTS.items():
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
                tmp.write(resp.content)
                paths[key] = tmp.name

        def ink_bbox(font, text, ls):
            pen = 0.0
            l = t = r = b = None
            for ch in text:
                cl, ct, cr, cb = font.getbbox(ch)
                cl, cr = cl + pen, cr + pen
                l = cl if l is None else min(l, cl)
                t = ct if t is None else min(t, ct)
                r = cr if r is None else max(r, cr)
                b = cb if b is None else max(b, cb)
                pen += font.getlength(ch) + ls
            return l, t, r, b

        def draw_tracked(draw, xy, text, font, ls, fill, stroke=0):
            x, y = xy
            for ch in text:
                draw.text((x, y), ch, font=font, fill=fill,
                          stroke_width=stroke, stroke_fill=fill if stroke else None)
                x += font.getlength(ch) + ls

        img = Image.new("RGB", (W * _SS, H * _SS), _PARCHMENT)
        draw = ImageDraw.Draw(img)

        # Wordmark, solved to 46% of card width.
        target = W * _SS * 0.46
        fs = target * 0.5
        for _ in range(40):
            f = ImageFont.truetype(paths["caveat"], max(1, int(round(fs))))
            l, t, r, b = ink_bbox(f, "Balm", fs * _LS_RATIO)
            if abs((r - l) - target) < 1:
                break
            fs *= target / (r - l)
        fs = max(1, int(round(fs)))
        wf = ImageFont.truetype(paths["caveat"], fs)
        ls, stroke = fs * _LS_RATIO, int(round(fs * _STROKE_RATIO))
        l, t, r, b = ink_bbox(wf, "Balm", ls)
        wy = H * _SS * 0.30 - t
        draw_tracked(draw, ((W * _SS - (r - l)) / 2 - l, wy), "Balm", wf, ls, _SLATE, stroke)
        word_bottom = wy + b

        # Tagline, in the site's italic serif.
        tf = ImageFont.truetype(paths["italic"], int(38 * _SS))
        tag, tls = "Topical, anti-inflammatory news", 1.2 * _SS
        tl, tt, tr, tb = ink_bbox(tf, tag, tls)
        ty = word_bottom + 46 * _SS
        draw_tracked(draw, ((W * _SS - (tr - tl)) / 2 - tl, ty), tag, tf, tls, _SECONDARY)

        ry = ty + (tb - tt) + 52 * _SS
        draw.line([(W * _SS * 0.34, ry), (W * _SS * 0.66, ry)], fill=_RULE, width=2 * _SS)

        sf = ImageFont.truetype(paths["serif"], int(27 * _SS))
        sub, sls = "A calm news digest, twice daily", 1.0 * _SS
        sl, st, sr, sb = ink_bbox(sf, sub, sls)
        draw_tracked(draw, ((W * _SS - (sr - sl)) / 2 - sl, ry + 44 * _SS - st),
                     sub, sf, sls, _MUTED)

        img.resize((W, H), Image.LANCZOS).save(str(out_path), "PNG")
        print(f"[OK] og-image.png ({W}x{H})")

    except Exception as exc:
        print(f"[WARN] ensure_og_image: skipped ({exc})", file=sys.stderr)
    finally:
        for p in paths.values():
            try:
                _os.unlink(p)
            except Exception:
                pass


def write_feed_xml(archive: list[dict], docs_dir: Path) -> None:
    """Write docs/feed.xml — a reader-facing RSS feed of recent editions.

    Distinct from podcast.xml, which is an audio feed and is gated on
    AUDIO_ENABLED. This one is text and always published: feed readers are how
    a meaningful share of Balm's audience prefers to read, precisely because it
    avoids apps and notifications.

    Also copies feed.xsl into docs/ and links it via an xml-stylesheet PI, so
    a browser hitting the raw feed URL directly renders a normal-looking page
    instead of dumping XML source. Feed readers ignore the stylesheet.
    """
    from xml.sax.saxutils import escape

    items = []
    for entry in archive[:50]:
        date_str, run = entry["date"], entry["run"]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        # RSS wants RFC 822; approximate publish times to the run windows.
        hour = "12:00:00" if run == "am" else "21:00:00"
        pub = f"{dt.strftime('%a, %d %b %Y')} {hour} GMT"
        label = f"{dt.strftime('%B %-d, %Y')} — {run.upper()} Edition"
        link = f"{BASE_URL}/{date_str}-{run}.html"
        items.append(
            "    <item>\n"
            f"      <title>{escape(label)}</title>\n"
            f"      <link>{escape(link)}</link>\n"
            f"      <guid isPermaLink=\"true\">{escape(link)}</guid>\n"
            f"      <pubDate>{pub}</pubDate>\n"
            "      <description>Balm digest — the day's news, stripped of "
            "inflammatory language and emotional framing.</description>\n"
            "    </item>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<?xml-stylesheet type="text/xsl" href="/feed.xsl"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>Balm</title>\n"
        f"    <link>{BASE_URL}/</link>\n"
        "    <description>Topical, anti-inflammatory news. A calm news digest, "
        "published twice daily.</description>\n"
        "    <language>en-us</language>\n"
        f'    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )
    (docs_dir / "feed.xml").write_text(xml, encoding="utf-8")
    print(f"[OK] feed.xml updated ({len(items)} editions)")

    # feed.xsl makes the raw feed URL render as a normal page for anyone who
    # clicks it directly instead of pointing a feed reader at it. Self-heals
    # docs/ from the root copy on every run, same pattern as the PWA icons.
    xsl_src = Path(__file__).parent / "feed.xsl"
    if xsl_src.exists():
        (docs_dir / "feed.xsl").write_text(
            xsl_src.read_text(encoding="utf-8"), encoding="utf-8"
        )


def write_sitemap(archive: list[dict], docs_dir: Path) -> None:
    """Write docs/sitemap.xml covering the static pages and every edition."""
    from xml.sax.saxutils import escape

    urls = [(f"{BASE_URL}/", "daily"), (f"{BASE_URL}/archive.html", "daily"),
            (f"{BASE_URL}/contact.html", "monthly")]
    urls += [(f"{BASE_URL}/{e['date']}-{e['run']}.html", "never") for e in archive]

    body = "\n".join(
        f"  <url><loc>{escape(u)}</loc><changefreq>{f}</changefreq></url>"
        for u, f in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n"
    )
    (docs_dir / "sitemap.xml").write_text(xml, encoding="utf-8")
    print(f"[OK] sitemap.xml updated ({len(urls)} URLs)")


def ensure_static_icons(docs_dir: Path) -> None:
    """Generate PNG icon files for docs/ that match the masthead SVG wordmark.

    The icons reproduce the masthead's letterforms rather than merely reusing
    its font. The masthead (templates/*.html) draws Caveat 700 at font-size 52
    inside a 180×56 viewBox with letter-spacing 8 and an feMorphology dilate of
    radius 0.6, so both tracking and stroke weight are scaled off the font size
    using the ratios below. The dilate is reproduced with Pillow's stroke_width,
    which thickens the letterforms without softening them — the masthead's
    accompanying feGaussianBlur is deliberately NOT reproduced, since blur is
    illegible at icon sizes.

    Rendering is supersampled 4× and LANCZOS-downsampled so the fractional
    tracking and stroke width survive down to 32px.

    Always regenerates all three icons on every call. No SVG fallback — if the
    font download fails, icon generation is skipped and an error is printed.
    """
    import tempfile
    import os as _os

    _PARCHMENT = "#f2ede4"
    _SLATE     = "#6b82a8"
    _CAVEAT_TTF_URL = (
        "https://github.com/googlefonts/caveat/raw/main/fonts/ttf/Caveat-Bold.ttf"
    )

    # Masthead geometry, expressed as fractions of the font size.
    _LETTER_SPACING_RATIO = 8 / 52
    _STROKE_RATIO         = 0.6 / 52
    _SUPERSAMPLE          = 4

    icons_dir = docs_dir / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)

    # Download Caveat Bold TTF.
    try:
        resp = requests.get(_CAVEAT_TTF_URL, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[ERROR] ensure_static_icons: font download failed ({exc}); skipping icon generation")
        return

    ttf_path = None
    try:
        from PIL import Image, ImageDraw, ImageFont

        with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
            tmp.write(resp.content)
            ttf_path = tmp.name

        def _ink_bbox(font, text, letter_spacing):
            """Bounding box of `text` laid out char-by-char with extra tracking.

            Pillow has no letter-spacing option, so glyphs are positioned
            manually and the union of their boxes is measured. Returns
            (left, top, right, bottom) relative to the draw origin.
            """
            pen = 0.0
            left = top = right = bottom = None
            for ch in text:
                cl, ct, cr, cb = font.getbbox(ch)
                cl, cr = cl + pen, cr + pen
                left   = cl if left   is None else min(left, cl)
                top    = ct if top    is None else min(top, ct)
                right  = cr if right  is None else max(right, cr)
                bottom = cb if bottom is None else max(bottom, cb)
                pen += font.getlength(ch) + letter_spacing
            return left, top, right, bottom

        # (canvas_size, text, fit_axis, fraction_of_canvas, output_path)
        # "B" alone is height-bound (Caveat's B is tall and narrow); the "Balm"
        # wordmark is width-bound.
        specs = [
            (32,  "B",    "height", 0.72, icons_dir / "icon-32.png"),
            (192, "Balm", "width",  0.78, icons_dir / "icon-192.png"),
            (512, "Balm", "width",  0.78, icons_dir / "icon-512.png"),
        ]

        for size, text, axis, fraction, out_path in specs:
            canvas = size * _SUPERSAMPLE
            target = canvas * fraction

            # Iterate to the font size whose ink hits `target` on `axis`.
            # Ink extent is very nearly linear in font size, so this converges
            # in a handful of passes; the cap is only a safety net.
            font_size = canvas * 0.5
            for _ in range(40):
                font = ImageFont.truetype(ttf_path, max(1, int(round(font_size))))
                l, t, r, b = _ink_bbox(font, text, font_size * _LETTER_SPACING_RATIO)
                extent = (r - l) if axis == "width" else (b - t)
                if abs(extent - target) < 0.5:
                    break
                font_size *= target / extent

            font_size      = max(1, int(round(font_size)))
            font           = ImageFont.truetype(ttf_path, font_size)
            letter_spacing = font_size * _LETTER_SPACING_RATIO
            stroke         = int(round(font_size * _STROKE_RATIO))
            l, t, r, b     = _ink_bbox(font, text, letter_spacing)

            img  = Image.new("RGB", (canvas, canvas), _PARCHMENT)
            draw = ImageDraw.Draw(img)

            # Offset the pen so the ink bounding box lands centred on canvas.
            pen = (canvas - (r - l)) / 2 - l
            y   = (canvas - (b - t)) / 2 - t
            for ch in text:
                draw.text((pen, y), ch, font=font, fill=_SLATE,
                          stroke_width=stroke, stroke_fill=_SLATE)
                pen += font.getlength(ch) + letter_spacing

            img.resize((size, size), Image.LANCZOS).save(str(out_path), "PNG")

            print(
                f"[OK] {out_path.name} ({size}×{size}): font={font_size}pt "
                f"tracking={letter_spacing:.1f} stroke={stroke} "
                f"ink={r - l:.0f}×{b - t:.0f} of {canvas} ({axis} {fraction:.0%})"
            )

    except Exception as exc:
        print(f"[ERROR] ensure_static_icons: PNG generation failed ({exc})")
    finally:
        if ttf_path:
            try:
                _os.unlink(ttf_path)
            except Exception:
                pass


def render_digest(articles: list[dict], date_str: str, run: str, metadata: dict,
                  archive: list[dict], docs_dir: Path,
                  top_stories: list[dict] | None = None,
                  category_order: list[str] | None = None,
                  market_trend: str = "") -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("digest.html")

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")

    mp3_file = f"{date_str}-{run}.mp3"
    has_audio = (docs_dir / mp3_file).exists()
    sources_file = f"{date_str}-{run}-sources.html"

    # Build a lookup so templates can retrieve article objects by story_id
    article_by_id = {a["story_id"]: a for a in articles if "story_id" in a}

    html = template.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        categories=_group_by_category(articles, category_order),
        archive_months=group_archive_by_month(archive),
        metadata=metadata,
        has_audio=has_audio,
        mp3_file=mp3_file if has_audio else None,
        sp500=metadata.get("sp500_close"),
        base_url=BASE_URL,
        sources_file=sources_file,
        top_stories=top_stories or [],
        article_by_id=article_by_id,
        market_trend=market_trend,
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
                 archive: list[dict], docs_dir: Path,
                 top_stories: list[dict] | None = None,
                 category_order: list[str] | None = None,
                 market_trend: str = "") -> None:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("index.html")

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")

    mp3_file = f"{date_str}-{run}.mp3"
    has_audio = (docs_dir / mp3_file).exists()
    sources_file = f"{date_str}-{run}-sources.html"

    # Build a lookup so templates can retrieve article objects by story_id
    article_by_id = {a["story_id"]: a for a in articles if "story_id" in a}

    html = template.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        categories=_group_by_category(articles, category_order),
        archive_months=group_archive_by_month(archive),
        metadata=metadata,
        has_audio=has_audio,
        mp3_file=mp3_file if has_audio else None,
        sp500=metadata.get("sp500_close"),
        base_url=BASE_URL,
        current_digest_file=f"{date_str}-{run}.html",
        sources_file=sources_file,
        top_stories=top_stories or [],
        article_by_id=article_by_id,
        market_trend=market_trend,
    )

    out_path = docs_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] index.html updated")


def render_contact(archive: list[dict], docs_dir: Path) -> None:
    """Render docs/contact.html from templates/contact.html."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("contact.html")
    html = template.render(
        archive_months=group_archive_by_month(archive),
    )
    out_path = docs_dir / "contact.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] contact.html updated")


def render_archive_page(archive: list[dict], docs_dir: Path) -> None:
    """Render docs/archive.html — a standalone listing of all past editions."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    def _format_date(date_str: str) -> str:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%B %-d, %Y")
        except Exception:
            return date_str

    env.filters["format_date"] = _format_date
    template = env.get_template("archive.html")
    html = template.render(archive_months=group_archive_by_month(archive))
    out_path = docs_dir / "archive.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] archive.html updated")


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
    # Validate the API key and account status before attempting TTS generation.
    # A 401 from /v1/user means the key is invalid; a 200 with character_count
    # near the limit means the account has insufficient credits.
    try:
        check = requests.get(
            "https://api.elevenlabs.io/v1/user",
            headers={"xi-api-key": api_key},
            timeout=10,
        )
        if check.status_code == 401:
            print(
                "  [WARN] ElevenLabs API key invalid or insufficient credits"
                " — skipping audio generation",
                file=sys.stderr,
            )
            return None
        check.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"  [WARN] ElevenLabs key validation failed ({e}) — skipping audio generation",
              file=sys.stderr)
        return None

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
        print(f"  [WARN] ElevenLabs audio generation failed: {e}", file=sys.stderr)
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
# New editorial pipeline steps
# ---------------------------------------------------------------------------

def classify_difficult_news(clusters: list[list[dict]], anthropic_key: str) -> list[bool]:
    """Pre-classify clusters as DIFFICULT NEWS before synthesis.

    Returns a parallel bool list: True means the cluster should be treated as
    DIFFICULT NEWS. Synthesis receives this flag as an annotation on the cluster
    so Claude can apply appropriate editorial handling.

    Non-blocking: on failure returns all-False (no pre-classification).
    """
    if not clusters:
        return []

    lines = [f"Classify these {len(clusters)} story clusters:\n"]
    for ci, cluster in enumerate(clusters, 1):
        titles = " | ".join(a["title"][:80] for a in cluster[:3])
        lines.append(f"[{ci}] {titles}")

    client = Anthropic(api_key=anthropic_key)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=DIFFICULT_NEWS_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        data = json.loads(raw)
        flags = data.get("difficult", [])
        if len(flags) != len(clusters):
            print(f"  [WARN] Difficult news classification: got {len(flags)} flags "
                  f"for {len(clusters)} clusters — padding with False", file=sys.stderr)
            flags = flags[:len(clusters)] + [False] * (len(clusters) - len(flags))
        difficult_count = sum(1 for f in flags if f)
        print(f"  Difficult news classification: {difficult_count} cluster(s) flagged")
        return [bool(f) for f in flags]
    except Exception as e:
        print(f"  [WARN] Difficult news classification failed ({e}) — skipping pre-classification",
              file=sys.stderr)
        return [False] * len(clusters)


def select_top_stories(articles: list[dict], anthropic_key: str) -> list[dict]:
    """Ask Claude to select 2-4 broadly significant stories as top stories.

    Each top story includes a story_id (matching an article's story_id field)
    and a one-sentence reason. Non-blocking: returns empty list on failure.
    """
    if not articles:
        return []

    lines = [f"Select 2-4 top stories from this digest of {len(articles)} articles:\n"]
    for a in articles:
        if a.get("isDifficult"):
            continue  # Difficult news is never a top story
        sid = a.get("story_id", "")
        cat = a.get("category", "")
        headline = a.get("headline", "")
        lines.append(f"{sid} [{cat}] {headline}")

    client = Anthropic(api_key=anthropic_key)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            system=TOP_STORIES_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        data = json.loads(raw)
        top = data.get("top_stories", [])
        # Validate story_ids
        valid_ids = {a.get("story_id") for a in articles}
        top = [t for t in top if t.get("story_id") in valid_ids]
        top = top[:4]  # Hard cap
        print(f"  Top stories selected: {len(top)}")
        return top
    except Exception as e:
        print(f"  [WARN] Top stories selection failed ({e}) — skipping", file=sys.stderr)
        return []


def filter_pm_duplicates(articles: list[dict], anthropic_key: str,
                         am_metadata_path: Path) -> list[dict]:
    """Filter PM articles that are duplicates of AM edition stories.

    Reads AM edition headlines from the metadata JSON, then asks Claude to
    determine which PM articles are genuine new developments vs. repeats.
    Non-blocking: if AM metadata is missing or the call fails, returns all articles.
    """
    if not am_metadata_path.exists():
        print("  No AM metadata found — skipping PM deduplication")
        return articles

    try:
        am_meta = json.loads(am_metadata_path.read_text())
        am_headlines = am_meta.get("headlines", [])
    except Exception as e:
        print(f"  [WARN] Could not read AM metadata ({e}) — skipping PM deduplication",
              file=sys.stderr)
        return articles

    if not am_headlines:
        print("  AM metadata has no headlines — skipping PM deduplication")
        return articles

    lines = ["AM EDITION HEADLINES:"]
    for h in am_headlines:
        lines.append(f"  - {h}")
    lines.append(f"\nPM CANDIDATE STORIES ({len(articles)} total):")
    for i, a in enumerate(articles):
        lines.append(f"[{i}] {a.get('headline', '')} [{a.get('category', '')}]")

    client = Anthropic(api_key=anthropic_key)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            system=PM_DEDUP_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        data = json.loads(raw)
        keep_flags = data.get("keep", [])
        if len(keep_flags) != len(articles):
            print(f"  [WARN] PM dedup: got {len(keep_flags)} flags for {len(articles)} articles "
                  f"— keeping all", file=sys.stderr)
            return articles
        kept = [a for a, k in zip(articles, keep_flags) if k]
        excluded = len(articles) - len(kept)
        if excluded:
            print(f"  PM deduplication: {excluded} article(s) removed as AM duplicates")
        else:
            print(f"  PM deduplication: all {len(articles)} articles are new developments")
        return kept
    except Exception as e:
        print(f"  [WARN] PM deduplication failed ({e}) — keeping all articles", file=sys.stderr)
        return articles


def order_categories(articles: list[dict], anthropic_key: str) -> list[str]:
    """Ask Claude to suggest today's editorial category order.

    Returns the ordered list of category names. DIFFICULT NEWS is always last.
    Non-blocking: returns CATEGORY_ORDER on failure.
    """
    if not articles:
        return CATEGORY_ORDER

    present_cats = list(dict.fromkeys(
        a.get("category", "") for a in articles if a.get("category")
    ))

    lines = [f"Today's digest has {len(articles)} stories across these categories:"]
    for cat in present_cats:
        cat_articles = [a for a in articles if a.get("category") == cat]
        headlines = "; ".join(a.get("headline", "")[:60] for a in cat_articles[:2])
        lines.append(f"  {cat} ({len(cat_articles)} stories): {headlines}")

    client = Anthropic(api_key=anthropic_key)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=CATEGORY_ORDER_PROMPT,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
        data = json.loads(raw)
        order = data.get("order", [])
        # Validate: must contain all present categories
        valid_cats = set(CATEGORY_ORDER)
        order = [c for c in order if c in valid_cats]
        # Ensure DIFFICULT NEWS is last
        if "DIFFICULT NEWS" in order:
            order = [c for c in order if c != "DIFFICULT NEWS"] + ["DIFFICULT NEWS"]
        # Add any present category not returned by Claude (safety net)
        for cat in CATEGORY_ORDER:
            if cat not in order and cat in present_cats:
                if cat == "DIFFICULT NEWS":
                    order.append(cat)
                else:
                    order.insert(-1 if "DIFFICULT NEWS" in order else len(order), cat)
        print(f"  Category order: {' → '.join(order)}")
        return order
    except Exception as e:
        print(f"  [WARN] Category ordering failed ({e}) — using default order", file=sys.stderr)
        return CATEGORY_ORDER


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
        "headlines": [a.get("headline", "") for a in articles],
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
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an edition that already exists (see guard below)")
    args = parser.parse_args()

    # Resolve run and date. Pacific is the ONLY clock that decides either one —
    # Balm's editions are named for the Pacific day they cover. CI runs in UTC,
    # so anything that re-derives the edition from a UTC clock disagrees with
    # this for runs between 00:00-07:00 UTC (5pm-midnight PT). That disagreement
    # destroyed two published editions: a PM run delayed past midnight UTC was
    # classified "am" from the UTC hour and overwrote that morning's digest.
    pt_tz = tz.gettz("America/Los_Angeles")
    now_pt = datetime.now(pt_tz)
    date_str = args.date or now_pt.strftime("%Y-%m-%d")
    run = args.run or ("am" if now_pt.hour < 13 else "pm")

    print(f"[START] Balm pipeline — {date_str} {run.upper()}")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Publish the resolved values so CI labels the commit with the edition that
    # was actually written, instead of re-deriving it from its own clock.
    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"resolved_date={date_str}\n")
            fh.write(f"resolved_run={run}\n")

    # Never silently replace a published edition. A correct run always writes a
    # file that does not exist yet.
    #
    # This is not an error condition. GitHub routinely fires a straggler hours
    # after a scheduled slot — on 2026-08-28 and 2026-08-29 late runs resolved
    # to the previous evening's PM edition, which was already published. Under
    # the old UTC-hour logic those same runs would have been labelled "am" and
    # overwritten that morning's digest. Skipping is the whole point, so it
    # exits successfully: failing here would put a red X on correct behaviour
    # twice a week and train us to ignore genuine failures.
    #
    # Deliberate regeneration passes --force.
    existing = DOCS_DIR / f"{date_str}-{run}.html"
    if existing.exists() and not args.force:
        msg = (f"{existing.name} already exists — skipping to avoid overwriting a "
               f"published edition. Pass --force to regenerate deliberately.")
        print(f"[SKIP] {msg}")
        if os.environ.get("GITHUB_ACTIONS"):
            # Surfaced in the run summary without failing the job.
            print(f"::warning title=Edition already published::{msg}")
        sys.exit(0)

    # Read API keys
    newsdata_api_key  = os.environ.get("NEWSDATA_API_KEY", "")
    guardian_api_key  = os.environ.get("GUARDIAN_API_KEY", "")
    nyt_api_key       = os.environ.get("NYT_API_KEY", "")
    anthropic_key     = os.environ.get("ANTHROPIC_API_KEY", "")
    elevenlabs_key    = os.environ.get("ELEVEN_LABS_API_KEY", "")

    # Fail fast on required keys
    if not anthropic_key:
        print("[ERROR] ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)
    if not newsdata_api_key:
        print("[ERROR] NEWSDATA_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    # ── Step 1: Fetch articles ────────────────────────────────────────────
    print("\n[1/14] Fetching articles from all sources...")
    raw_articles: list[dict] = []

    raw_articles.extend(fetch_newsdata(newsdata_api_key))

    if guardian_api_key:
        raw_articles.extend(fetch_guardian(guardian_api_key))
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
    print("\n[2/14] Removing exact duplicates (same title + source + URL)...")
    deduped_articles = remove_exact_duplicates(raw_articles)
    removed = len(raw_articles) - len(deduped_articles)
    print(f"  {len(raw_articles)} raw → {len(deduped_articles)} articles "
          f"({removed} exact duplicates removed)")

    if not deduped_articles:
        print("[ERROR] No articles fetched. Check API keys.", file=sys.stderr)
        sys.exit(1)

    # ── Step 3: Cluster ───────────────────────────────────────────────────
    print("\n[3/14] Clustering articles by story (Claude semantic clustering)...")
    clusters = cluster_articles(deduped_articles, anthropic_key)
    multi = [c for c in clusters if len(c) > 1]

    # ── Step 4: Editorial review ──────────────────────────────────────────
    print("\n[4/14] Claude editorial review of cluster structure...")
    clusters = editorial_review(clusters, anthropic_key)
    multi = [c for c in clusters if len(c) > 1]

    # ── Step 5: Classify difficult news ──────────────────────────────────
    print("\n[5/14] Pre-classifying difficult news clusters...")
    difficult_flags = classify_difficult_news(clusters, anthropic_key)

    # ── Step 6: Claude editorial processing ──────────────────────────────
    print("\n[6/14] Sending clusters to Claude for synthesis and editorial processing...")
    processed_articles = call_claude(clusters, anthropic_key, difficult_flags)
    attach_sources(processed_articles, clusters)
    processed_articles = sort_articles(processed_articles)
    number_articles(processed_articles)
    print(f"  Claude returned {len(processed_articles)} articles after filtering")

    # ── Step 7: PM deduplication (PM edition only) ────────────────────────
    print("\n[7/14] PM deduplication check...")
    if run == "pm":
        am_metadata_path = DOCS_DIR / f"{date_str}-am.json"
        processed_articles = filter_pm_duplicates(processed_articles, anthropic_key, am_metadata_path)
        # Re-number after deduplication
        number_articles(processed_articles)
    else:
        print("  AM edition — skipping PM deduplication")

    # ── Step 8: S&P 500 ──────────────────────────────────────────────────
    print("\n[8/14] Fetching S&P 500 close...")
    sp500 = fetch_sp500()
    print(f"  S&P 500: {sp500 if sp500 else 'unavailable (non-blocking)'}")

    print("  Calculating market trend line...")
    market_trend = calculate_market_trend(DOCS_DIR, anthropic_key)
    if market_trend:
        print(f"  Market trend: {market_trend}")
    else:
        print("  Market trend: unavailable (non-blocking)")

    # ── Step 9: Save metadata ─────────────────────────────────────────────
    print("\n[9/14] Saving metadata...")
    metadata = save_metadata(date_str, run, processed_articles, len(deduped_articles), sp500, DOCS_DIR)

    # ── Step 10: Select top stories ───────────────────────────────────────
    print("\n[10/14] Selecting top stories...")
    top_stories = select_top_stories(processed_articles, anthropic_key)

    # ── Step 11: Determine category order ────────────────────────────────
    print("\n[11/14] Determining editorial category order...")
    category_order = order_categories(processed_articles, anthropic_key)

    # ── Step 12: Generate audio ───────────────────────────────────────────
    print("\n[12/14] Generating audio...")
    mp3_path = None
    if AUDIO_ENABLED:
        if elevenlabs_key:
            script = build_audio_script(processed_articles, date_str, run)
            mp3_path = generate_audio(script, date_str, run, elevenlabs_key, DOCS_DIR)
        else:
            print("  [WARN] ELEVEN_LABS_API_KEY not set — skipping audio")
    else:
        print("  Audio disabled (AUDIO_ENABLED = False)")

    # ── Step 13: Collect archive and render output ────────────────────────
    print("\n[13/14] Building archive index and rendering output files...")
    archive = collect_archive(DOCS_DIR)
    write_archive_json(archive, DOCS_DIR)
    write_feed_xml(archive, DOCS_DIR)
    write_sitemap(archive, DOCS_DIR)
    ensure_static_icons(DOCS_DIR)
    ensure_og_image(DOCS_DIR)
    render_digest(processed_articles, date_str, run, metadata, archive, DOCS_DIR,
                  top_stories=top_stories, category_order=category_order, market_trend=market_trend)
    render_sources(processed_articles, date_str, run, archive, DOCS_DIR)
    render_index(processed_articles, date_str, run, metadata, archive, DOCS_DIR,
                 top_stories=top_stories, category_order=category_order, market_trend=market_trend)
    render_contact(archive, DOCS_DIR)
    render_archive_page(archive, DOCS_DIR)

    # ── Step 14: Podcast RSS ──────────────────────────────────────────────
    print("\n[14/14] Updating podcast RSS feed...")
    if AUDIO_ENABLED:
        try:
            update_podcast_feed(DOCS_DIR)
        except Exception as e:
            print(f"  [WARN] Podcast feed update failed: {e}", file=sys.stderr)
    else:
        print("  Podcast RSS update skipped (AUDIO_ENABLED = False)")

    print(f"\n[DONE] Balm {date_str} {run.upper()} complete.")
    print(f"  Stories published : {len(processed_articles)}")
    print(f"  Clusters processed: {len(clusters)} ({len(multi)} multi-source)")
    print(f"  Top stories       : {len(top_stories)}")
    print(f"  Category order    : {' → '.join(category_order[:4])}{'...' if len(category_order) > 4 else ''}")
    print(f"  Audio             : {'yes' if mp3_path else 'disabled' if not AUDIO_ENABLED else 'no'}")
    print(f"  S&P 500           : {sp500 if sp500 else 'unavailable'}")


if __name__ == "__main__":
    main()
