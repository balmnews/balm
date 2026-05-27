#!/usr/bin/env python3
"""Balm hybrid pipeline — two-pass top-down editorial synthesis.

ARCHITECTURE
============
This is an experimental parallel pipeline alongside the main pipeline.py.
It implements a different story-selection approach and does not modify any
main-pipeline outputs (archive.json, podcast.xml, docs/*.html).

  Pass 1 — Story identification (single lightweight Claude call)
    All article titles and descriptions are sent to Claude with a compact
    prompt.  Claude returns a ranked list of 10–16 newsworthy stories, each
    with a headline, description, key terms, and relevance score.
    Token budget: ~1 500.  This pass answers "what matters today?" — it does
    NOT write editorial copy.

  Pass 2 — Per-story synthesis (one Claude call per story)
    For each story from Pass 1, every raw article is keyword-scored against
    the story's headline, description, and key terms.  The top 5–8 highest-
    scoring articles are sent to Claude with the standard EDITORIAL_SYSTEM_PROMPT
    used by the main pipeline for full neutral synthesis.
    Each story is one independent Claude call; failures are non-blocking.

OUTPUTS (all under docs/hybrid/):
  YYYY-MM-DD-{am,pm}.html           Digest page (same visual design as main)
  YYYY-MM-DD-{am,pm}-sources.html   Source attribution page
  index.html                         Listing of all hybrid digests

NOT produced: audio, podcast.xml, archive.json updates, metadata JSON.
NOT modified: any file outside docs/hybrid/.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure pipeline.py is importable regardless of working directory
sys.path.insert(0, str(Path(__file__).parent))

from anthropic import Anthropic
from dateutil import tz
from jinja2 import BaseLoader, Environment

# ---------------------------------------------------------------------------
# Import shared functions and constants from the main pipeline.
# pipeline.py has an `if __name__ == "__main__": main()` guard so importing
# it does not execute the pipeline.
# ---------------------------------------------------------------------------
from pipeline import (
    CATEGORY_ORDER,
    EDITORIAL_SYSTEM_PROMPT,
    _group_by_category,
    _tokenize,
    collect_archive,
    DOCS_DIR as MAIN_DOCS_DIR,
    fetch_guardian,
    fetch_newsapi,
    fetch_nyt,
    fetch_rss_feeds,
    group_archive_by_month,
    number_articles,
    remove_exact_duplicates,
    sort_articles,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HYBRID_DOCS_DIR = Path(__file__).parent / "docs" / "hybrid"
BASE_URL = "https://balmnews.github.io/balm"
CLAUDE_MODEL = "claude-sonnet-4-6"

# NOTE: The hybrid pipeline uses keyword-based article scoring (see pass2_synthesize_story)
# rather than the embedding-based clustering in pipeline.py. A future upgrade could replace
# the keyword scorer with cluster_articles() from pipeline.py for semantic source retrieval.
# That would require VOYAGE_API_KEY and the voyageai package (already in requirements.txt).

# Pass 1: story identification — needs enough room for 10-16 detailed story objects
PASS1_MAX_TOKENS = 4000

# Pass 2: one story per call; full_summary needs adequate room
PASS2_MAX_TOKENS = 2000

# Articles sent to Claude per story in Pass 2
TOP_N_DEFAULT = 6   # standard stories
TOP_N_MAX = 8       # top-4 highest-relevance stories

# Minimum score to include an article in Pass 2 (score > 0 means at least
# one key-term matched; score == 0 means completely unrelated)
SCORE_THRESHOLD = 0.0


# ---------------------------------------------------------------------------
# Pass 1 — Story identification system prompt
# ---------------------------------------------------------------------------

PASS1_SYSTEM_PROMPT = """You are the editorial director for Balm, a calm news digest that informs without agitating.

Your task is to survey today's article pool and identify the most newsworthy stories for a well-informed American adult reader. You are NOT writing editorial copy yet — only identifying which stories matter and providing the key terms needed to find relevant source articles for each.

AUDIENCE: Primarily American readers. Prioritise stories with direct US relevance or significant global implications (economic impact, military involvement, immigration, global health, major geopolitical shifts).

IDENTIFY AS NEWSWORTHY: stories a well-informed adult who doesn't follow news daily would need to know about.

DO NOT IDENTIFY — skip these entirely:
- Celebrity gossip or entertainment
- Political insults or feuds without direct policy substance or legislative consequence
- Speculative pieces with no concrete news hook
- Individual tragedies with no broader policy or pattern relevance
- Polls and horse-race political coverage outside election season

DEDUPLICATION: Multiple articles may cover the same story. Identify each STORY once, not each article separately.

OUTPUT FORMAT — return ONLY valid JSON, no markdown, no preamble, no trailing text:
{
  "stories": [
    {
      "story_id": 1,
      "headline": "Short factual label for the story",
      "description": "One sentence: what happened and why it matters to American readers",
      "key_terms": ["specific", "proper", "nouns", "and", "phrases"],
      "relevance_score": 8
    }
  ]
}

story_id: Sequential integers starting from 1.
headline: A brief factual label — enough to identify the story, not a Balm rewrite.
description: One sentence explaining what happened and its significance.
key_terms: 5–8 specific keywords or phrases that distinguish this story from others.
  Include proper nouns — names, places, organisations — that anchor the story.
  Avoid generic political vocabulary like "senate", "congress", "bill", "vote".
relevance_score: 1–10 importance (10 = essential reading for any American today).

Return 10–16 stories ordered from highest to lowest relevance_score."""


# ---------------------------------------------------------------------------
# Article scoring for Pass 2
# ---------------------------------------------------------------------------

def score_article_for_story(article: dict, story: dict) -> float:
    """Score an article's relevance to a story using key-term and token overlap.

    Key-term matches (proper nouns and specific phrases from Pass 1) carry
    higher weight than generic token overlap.  This prefers articles that share
    the story's specific anchoring entities over articles that merely use
    similar political vocabulary.
    """
    article_text = (article["title"] + " " + (article.get("description") or "")).lower()
    score = 0.0

    # Key-term substring match — high weight (proper nouns are high signal)
    for term in story.get("key_terms", []):
        if term.lower() in article_text:
            score += 2.0

    # Headline + description token overlap — lower weight
    query_text = story.get("headline", "") + " " + story.get("description", "")
    query_tokens = set(_tokenize(query_text))
    article_tokens = set(_tokenize(article_text))
    if query_tokens:
        overlap = len(query_tokens & article_tokens)
        score += overlap * 0.3

    return score


def select_articles_for_story(
    articles: list[dict], story: dict, story_rank: int
) -> list[dict]:
    """Return the most relevant articles for a story.

    Top-ranked stories (rank 1–4 by relevance_score) receive up to TOP_N_MAX
    source articles; all others receive up to TOP_N_DEFAULT.  Articles with
    score == 0 (no keyword overlap at all) are excluded.

    Returns articles sorted by descending relevance score.
    """
    top_n = TOP_N_MAX if story_rank <= 4 else TOP_N_DEFAULT

    scored = []
    for article in articles:
        s = score_article_for_story(article, story)
        if s > SCORE_THRESHOLD:
            scored.append((s, article))

    scored.sort(key=lambda x: -x[0])
    return [article for _, article in scored[:top_n]]


# ---------------------------------------------------------------------------
# Pass 1 — Partial JSON extraction fallback
# ---------------------------------------------------------------------------

def _extract_partial_stories(raw: str) -> list[dict]:
    """Extract any complete story objects from a truncated JSON response.

    When Claude's response is cut off mid-object (token budget exceeded), the
    outer JSON is unparseable but earlier story objects are intact.  This
    function walks the raw text character-by-character, tracking brace/bracket
    depth to find every complete story object and returns them.

    Handles nested arrays (e.g. key_terms) and escaped characters in strings.
    Returns an empty list when no complete story object is found.
    """
    # Strip markdown fences in case the caller passed the original response
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw).strip()

    m = re.search(r'"stories"\s*:\s*\[', raw)
    if not m:
        return []

    pos = m.end()          # character index just after the '[' of the stories array
    complete: list[dict] = []
    depth = 0              # 0 = between story objects in the array; 1+ = inside an object
    obj_start: int | None = None
    in_string = False
    i = pos

    while i < len(raw):
        ch = raw[i]

        if in_string:
            if ch == '\\':
                i += 2     # skip the escaped character entirely
                continue
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '{':
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == '[':
                depth += 1     # nested array (e.g. key_terms) inside a story object
            elif ch == '}':
                depth -= 1
                if depth == 0 and obj_start is not None:
                    try:
                        obj = json.loads(raw[obj_start: i + 1])
                        if isinstance(obj, dict) and "story_id" in obj:
                            complete.append(obj)
                    except json.JSONDecodeError:
                        pass
                    obj_start = None
            elif ch == ']':
                if depth == 0:
                    break      # reached the closing bracket of the stories array
                depth -= 1

        i += 1

    return complete


# ---------------------------------------------------------------------------
# Pass 1 — Identify stories
# ---------------------------------------------------------------------------

def pass1_identify_stories(articles: list[dict], anthropic_key: str) -> list[dict]:
    """Send all article titles/descriptions to Claude to identify top stories.

    Returns a list of story dicts (story_id, headline, description, key_terms,
    relevance_score).  Returns an empty list on failure — the pipeline cannot
    continue without Pass 1 output.
    """
    client = Anthropic(api_key=anthropic_key)

    # Compact article listing: numbered, source, title, brief description
    lines = [
        f"Survey this pool of {len(articles)} news articles and identify the top stories.\n"
    ]
    for i, a in enumerate(articles, 1):
        desc = (a.get("description") or "")[:120].replace("\n", " ").strip()
        lines.append(f"{i}. [{a['source']}] {a['title']}")
        if desc:
            lines.append(f"   {desc}")
    user_prompt = "\n".join(lines)

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=PASS1_MAX_TOKENS,
                system=PASS1_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            stories = data.get("stories", [])
            print(f"  Pass 1: {len(stories)} stories identified")
            for s in stories:
                score = s.get("relevance_score", "?")
                score_str = f"{score:>2}" if isinstance(score, int) else f" {score}"
                print(f"    [{score_str}] {s.get('headline', '?')}")
            return stories
        except json.JSONDecodeError as e:
            print(f"[WARN] Pass 1 JSON error (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt == 2:
                # Final fallback: extract whatever complete story objects exist
                # before the truncation point rather than discarding the run.
                partial = _extract_partial_stories(raw)
                if partial:
                    print(f"  [FALLBACK] Extracted {len(partial)} complete stories "
                          f"from truncated response", file=sys.stderr)
                    for s in partial:
                        score = s.get("relevance_score", "?")
                        score_str = f"{score:>2}" if isinstance(score, int) else f" {score}"
                        print(f"    [{score_str}] {s.get('headline', '?')}")
                    return partial
                return []
            time.sleep(5)
        except Exception as e:
            print(f"[WARN] Pass 1 API error (attempt {attempt + 1}): {e}", file=sys.stderr)
            if attempt == 2:
                return []
            time.sleep(10)

    return []


# ---------------------------------------------------------------------------
# Pass 2 — Synthesize individual stories
# ---------------------------------------------------------------------------

def pass2_synthesize_story(
    story: dict, source_articles: list[dict], anthropic_key: str
) -> dict | None:
    """Send a story's source articles to Claude for full editorial synthesis.

    Uses the same EDITORIAL_SYSTEM_PROMPT as the main pipeline.  The user
    message overrides the default 10–16 article count since each Pass 2 call
    covers exactly one story.

    Returns the synthesized article dict, or None if Claude excludes the story
    or the call fails.
    """
    client = Anthropic(api_key=anthropic_key)

    story_id = story["story_id"]
    n = len(source_articles)

    # Build cluster prompt for this single story
    lines = [
        "Synthesise this single news story cluster. "
        "Return exactly ONE article in the JSON 'articles' array "
        "(single-story call — ignore the default 10–16 count instruction).\n"
    ]
    if n == 1:
        a = source_articles[0]
        lines.append(f"[CLUSTER {story_id}] — 1 source")
        lines.append(f"  Source: {a['source']}")
        lines.append(f"  Title: {a['title']}")
        lines.append(f"  URL: {a['url']}")
        if a.get("description"):
            lines.append(f"  Description: {a['description'][:300]}")
    else:
        lines.append(f"[CLUSTER {story_id}] — {n} sources covering the same story")
        for si, a in enumerate(source_articles, 1):
            lines.append(f"  — Source {si}: {a['source']}")
            lines.append(f"    Title: {a['title']}")
            lines.append(f"    URL: {a['url']}")
            if a.get("description"):
                lines.append(f"    Description: {a['description'][:300]}")

    user_prompt = "\n".join(lines)

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=PASS2_MAX_TOKENS,
                system=EDITORIAL_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            data = json.loads(raw)
            articles = [a for a in data.get("articles", []) if a is not None]
            return articles[0] if articles else None
        except json.JSONDecodeError as e:
            print(
                f"  [WARN] Pass 2 story {story_id} JSON error (attempt {attempt + 1}): {e}",
                file=sys.stderr,
            )
            if attempt == 2:
                return None
            time.sleep(5)
        except Exception as e:
            print(
                f"  [WARN] Pass 2 story {story_id} API error (attempt {attempt + 1}): {e}",
                file=sys.stderr,
            )
            if attempt == 2:
                return None
            time.sleep(10)

    return None


# ---------------------------------------------------------------------------
# Source attribution
# ---------------------------------------------------------------------------

def attach_hybrid_sources(article: dict, source_articles: list[dict]) -> None:
    """Attach source attribution to a synthesised article in place."""
    article["sources"] = [
        {
            "source": a["source"],
            "url": a["url"],
            "original_headline": a["title"],
        }
        for a in source_articles
    ]
    article["primary_source"] = (
        article["sources"][0]["source"] if article["sources"] else "Unknown"
    )


# ---------------------------------------------------------------------------
# Hybrid archive listing
# ---------------------------------------------------------------------------

def collect_hybrid_archive() -> list[dict]:
    """Return hybrid digest entries sorted newest-first."""
    entries = []
    if not HYBRID_DOCS_DIR.exists():
        return entries
    for f in sorted(HYBRID_DOCS_DIR.glob("????-??-??-??.html"), reverse=True):
        parts = f.stem.split("-")
        if len(parts) < 4:
            continue
        date_str = "-".join(parts[:3])
        run = parts[3]
        if run not in ("am", "pm"):
            continue
        entries.append({
            "date": date_str,
            "run": run,
            "label": f"{date_str} {'AM' if run == 'am' else 'PM'}",
            "file": f.name,
            "sources_file": f.stem + "-sources.html",
        })
    return entries


# ---------------------------------------------------------------------------
# HTML templates — inline Jinja2 strings
#
# Same visual design as the main pipeline templates.  Differences:
#   - archive.json fetched from ../archive.json (parent directory)
#   - No audio player
#   - "Hybrid" badge in masthead dateline
#   - All archive sidebar links prefixed with ../ to reach docs/
# ---------------------------------------------------------------------------

_HYBRID_DIGEST_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Balm Hybrid — {{ date_display }}{% if run_label %} {{ run_label }}{% endif %}</title>
  <meta name="description" content="Balm hybrid digest for {{ date_display }} — topical, anti-inflammatory news.">
  <meta name="theme-color" content="#6b82a8">
  <link rel="manifest" href="/balm/manifest.json">
  <link rel="apple-touch-icon" href="/balm/icons/icon-192.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Caveat:wght@700&family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Source+Serif+4:ital,wght@0,300;1,300&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    :root{--bg:#f2ede4;--ink:#2a2520;--secondary:#6a6058;--muted:#9a9288;--rule:#c8c0b4;--accent:#6b82a8;--difficult:#5a4a3a}
    html{font-size:18px;scroll-behavior:smooth}
    body{background:var(--bg);color:var(--ink);font-family:'Source Serif 4',Georgia,serif;font-weight:300;line-height:1.7;min-height:100vh}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    .page-wrapper{display:flex;max-width:1100px;margin:0 auto;padding:0 1rem}
    .sidebar{width:220px;flex-shrink:0;padding:2rem 1.5rem 2rem 0;border-right:1px solid var(--rule);min-height:100vh}
    .main-content{flex:1;max-width:860px;padding:0 2rem 4rem}
    @media(max-width:768px){.page-wrapper{display:block;padding:0}.sidebar{display:none}.main-content{padding:0 1.25rem 3rem}}
    .masthead{text-align:center;padding:2.5rem 0 1.5rem;border-bottom:3px double var(--rule);margin-bottom:1.5rem}
    .masthead-logo svg{display:block;margin:0 auto .5rem}
    .masthead-tagline{font-family:'Source Serif 4',serif;font-weight:300;font-style:italic;font-size:.85rem;color:var(--secondary);letter-spacing:.08em;margin-bottom:.4rem}
    .masthead-dateline{font-family:'Source Serif 4',serif;font-weight:300;font-size:.7rem;color:var(--muted);letter-spacing:.18em;text-transform:uppercase}
    .hybrid-badge{display:inline-block;background:var(--accent);color:#fff;font-size:.6rem;letter-spacing:.12em;text-transform:uppercase;padding:.1rem .4rem;border-radius:2px;margin-left:.4rem;vertical-align:middle}
    .section-header{font-family:'Playfair Display',serif;font-size:.7rem;font-weight:400;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);border-top:1px solid var(--rule);padding-top:1.25rem;margin:2rem 0 1.25rem}
    .article{padding-bottom:1.5rem;margin-bottom:1.5rem;border-bottom:1px solid var(--rule)}
    .article:last-child{border-bottom:none}
    .article-headline{font-family:'Playfair Display',serif;font-size:1.2rem;font-weight:600;line-height:1.3;margin-bottom:.6rem;color:var(--ink)}
    .summary-toggle{display:flex;gap:0;margin-bottom:.75rem;font-size:.68rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);user-select:none}
    .toggle-btn{cursor:pointer;padding:.15rem .5rem;border:1px solid var(--rule);background:none;font-family:'Source Serif 4',serif;font-size:.68rem;font-weight:300;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);transition:background .15s,color .15s}
    .toggle-btn:first-child{border-radius:3px 0 0 3px}
    .toggle-btn:last-child{border-radius:0 3px 3px 0;border-left:none}
    .toggle-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
    .summary-text{font-size:1rem;line-height:1.75;color:var(--ink)}
    .summary-brief{display:block}
    .summary-full{display:none}
    .summary-text.full-visible .summary-brief{display:none}
    .summary-text.full-visible .summary-full{display:block}
    .sources-toggle{margin-top:.5rem}
    .sources-toggle-btn{background:none;border:none;padding:0;cursor:pointer;font-family:'Source Serif 4',serif;font-size:.75rem;color:#9a9288;display:flex;align-items:center;gap:.3rem;letter-spacing:.04em}
    .sources-toggle-btn:hover{color:#6a6058}
    .sources-caret{display:inline-block;transition:transform .2s ease;font-style:normal}
    .sources-toggle-btn[aria-expanded="true"] .sources-caret{transform:rotate(90deg)}
    .sources-list{list-style:none;padding:0;margin:0;max-height:0;overflow:hidden;transition:max-height .25s ease}
    .sources-toggle-btn[aria-expanded="true"]+.sources-list{max-height:20rem}
    .sources-list li{padding:.2rem 0 .2rem .8rem}
    .sources-list a{font-size:.75rem;color:#9a9288;text-decoration:none;border-bottom:1px solid #c8c0b4;transition:color .15s}
    .sources-list a:hover{color:#6b82a8}
    .difficult-section{border-top:1px solid var(--rule);margin-top:2rem;padding-top:1.25rem}
    .difficult-toggle{display:flex;align-items:center;gap:.5rem;cursor:pointer;list-style:none}
    .difficult-toggle::-webkit-details-marker{display:none}
    .difficult-label{font-family:'Playfair Display',serif;font-size:.7rem;font-weight:400;letter-spacing:.22em;text-transform:uppercase;color:var(--difficult)}
    .difficult-caret{font-size:.7rem;color:var(--difficult);transition:transform .2s}
    details[open] .difficult-caret{transform:rotate(90deg)}
    .difficult-subtext{font-size:.8rem;font-style:italic;color:var(--muted);margin-top:.3rem}
    details[open] .difficult-subtext{display:none}
    .difficult-content{margin-top:1.25rem}
    .difficult-content .article-headline{color:var(--difficult)}
    .sidebar-title{font-family:'Playfair Display',serif;font-size:.65rem;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:1rem;padding-top:2rem}
    .archive-month{margin-bottom:1rem}
    .archive-month-label{font-size:.68rem;font-weight:300;letter-spacing:.08em;color:var(--secondary);text-transform:uppercase;margin-bottom:.3rem}
    .archive-link{display:block;font-size:.75rem;color:var(--secondary);padding:.1rem 0;line-height:1.6}
    .archive-link:hover{color:var(--accent);text-decoration:none}
    .footer{border-top:1px solid var(--rule);margin-top:3rem;padding:2rem 0;font-size:.78rem;color:var(--muted);line-height:1.7;text-align:center}
    .footer a{color:var(--muted);text-decoration:underline}
    .footer a:hover{color:var(--secondary)}
  </style>
</head>
<body>
<div class="page-wrapper">
  <nav class="sidebar" aria-label="Archive">
    <div class="sidebar-title">Archive</div>
    {% for month in archive_months %}
    <div class="archive-month">
      <div class="archive-month-label">{{ month.month }}</div>
      {% for entry in month.entries %}
      <a class="archive-link" href="../{{ entry.file }}">{{ entry.date[5:] }} {{ entry.run | upper }}</a>
      {% endfor %}
    </div>
    {% endfor %}
  </nav>
  <div class="main-content">
    <header class="masthead">
      <div class="masthead-logo">
        <svg width="180" height="56" viewBox="0 0 180 56" xmlns="http://www.w3.org/2000/svg">
          <defs>
            <filter id="balm-spread" x="-3%" y="-3%" width="106%" height="106%">
              <feMorphology in="SourceGraphic" operator="dilate" radius="0.6" result="dilated"/>
              <feGaussianBlur in="dilated" stdDeviation="0.3" result="blurred"/>
              <feComposite in="SourceGraphic" in2="blurred" operator="over"/>
            </filter>
          </defs>
          <text x="90" y="44" font-family="Caveat, cursive" font-size="52" font-weight="700"
                fill="#6b82a8" text-anchor="middle" letter-spacing="8" filter="url(#balm-spread)">Balm</text>
        </svg>
      </div>
      <p class="masthead-tagline">Topical, anti-inflammatory news</p>
      <p class="masthead-dateline">
        {{ date_display }}{% if run_label %} &middot; {{ run_label }} Edition{% endif %}
        <span class="hybrid-badge">Hybrid</span>
      </p>
    </header>

    {% for section in categories %}
      {% if section.name == 'DIFFICULT NEWS' %}
        <div class="difficult-section">
          <details>
            <summary class="difficult-toggle">
              <span class="difficult-caret">&#9658;</span>
              <span class="difficult-label">Difficult News</span>
            </summary>
            <p class="difficult-subtext">Stories of tragedy and violence — expand only if you choose to</p>
            <div class="difficult-content">
              {% for article in section.articles %}
              <div class="article">
                <h2 class="article-headline">{{ article.headline }}</h2>
                <div class="summary-toggle">
                  <button class="toggle-btn active" onclick="toggleSummary(this,'brief')">Brief</button>
                  <button class="toggle-btn" onclick="toggleSummary(this,'full')">Full</button>
                </div>
                <div class="summary-text">
                  <span class="summary-brief"><span>{{ article.brief_summary }}</span></span>
                  <span class="summary-full"><span>{{ article.full_summary }}</span></span>
                </div>
                {% if article.sources %}
                <div class="sources-toggle">
                  <button class="sources-toggle-btn" aria-expanded="false">
                    <span class="sources-caret">&rsaquo;</span> Sources
                  </button>
                  <ul class="sources-list">
                    {% for src in article.sources %}
                    <li><a href="{{ src.url }}" target="_blank" rel="noopener">{{ src.source }}</a></li>
                    {% endfor %}
                  </ul>
                </div>
                {% endif %}
              </div>
              {% endfor %}
            </div>
          </details>
        </div>
      {% else %}
        <div class="section-header">{{ section.name }}</div>
        {% for article in section.articles %}
        <div class="article">
          <h2 class="article-headline">{{ article.headline }}</h2>
          <div class="summary-toggle">
            <button class="toggle-btn active" onclick="toggleSummary(this,'brief')">Brief</button>
            <button class="toggle-btn" onclick="toggleSummary(this,'full')">Full</button>
          </div>
          <div class="summary-text">
            <span class="summary-brief"><span>{{ article.brief_summary }}</span></span>
            <span class="summary-full"><span>{{ article.full_summary }}</span></span>
          </div>
          {% if article.sources %}
          <div class="sources-toggle">
            <button class="sources-toggle-btn" aria-expanded="false">
              <span class="sources-caret">&rsaquo;</span> Sources
            </button>
            <ul class="sources-list">
              {% for src in article.sources %}
              <li><a href="{{ src.url }}" target="_blank" rel="noopener">{{ src.source }}</a></li>
              {% endfor %}
            </ul>
          </div>
          {% endif %}
        </div>
        {% endfor %}
      {% endif %}
    {% endfor %}

    <footer class="footer">
      <p>Balm surfaces factual information stripped of inflammatory language, clickbait, and emotional manipulation.
      Perpetrator details are withheld from violent events by editorial policy.</p>
      <p style="margin-top:.75rem"><a href="{{ sources_file }}">Sources for this digest &rarr;</a></p>
      <p style="margin-top:.75rem">
        <a href="../index.html">&larr; Main Balm digest</a>
        &nbsp;&middot;&nbsp;
        <a href="index.html">Hybrid archive</a>
      </p>
    </footer>
  </div>
</div>
<script>
  function toggleSummary(btn, mode) {
    const g = btn.closest('.summary-toggle');
    const s = g.nextElementSibling;
    g.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    s.classList.toggle('full-visible', mode === 'full');
  }
  document.addEventListener('click', function(e) {
    const btn = e.target.closest('.sources-toggle-btn');
    if (!btn) return;
    btn.setAttribute('aria-expanded', String(btn.getAttribute('aria-expanded') !== 'true'));
  });
  // Dynamic archive — loads main pipeline archive for sidebar navigation
  (function() {
    fetch('../archive.json')
      .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function(data) {
        var html = data.months.map(function(m) {
          var links = m.entries.map(function(e) {
            return '<a class="archive-link" href="../' + e.file + '">'
              + e.date.slice(5) + ' ' + e.run.toUpperCase() + '</a>';
          }).join('');
          return '<div class="archive-month"><div class="archive-month-label">'
            + m.month + '</div>' + links + '</div>';
        }).join('');
        var nav = document.querySelector('nav.sidebar');
        if (nav) nav.innerHTML = '<div class="sidebar-title">Archive</div>' + html;
      })
      .catch(function() {});
  })();
</script>
</body>
</html>"""


_HYBRID_SOURCES_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Balm Hybrid Sources — {{ date_display }}{% if run_label %} {{ run_label }}{% endif %}</title>
  <meta name="description" content="Source attribution for Balm hybrid digest of {{ date_display }}.">
  <meta name="theme-color" content="#6b82a8">
  <link rel="manifest" href="/balm/manifest.json">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Caveat:wght@700&family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Source+Serif+4:ital,wght@0,300;1,300&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    :root{--bg:#f2ede4;--ink:#2a2520;--secondary:#6a6058;--muted:#9a9288;--rule:#c8c0b4;--accent:#6b82a8;--difficult:#5a4a3a}
    html{font-size:18px;scroll-behavior:smooth}
    body{background:var(--bg);color:var(--ink);font-family:'Source Serif 4',Georgia,serif;font-weight:300;line-height:1.7;min-height:100vh}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    .page-wrapper{display:flex;max-width:1100px;margin:0 auto;padding:0 1rem}
    .sidebar{width:220px;flex-shrink:0;padding:2rem 1.5rem 2rem 0;border-right:1px solid var(--rule);min-height:100vh}
    .main-content{flex:1;max-width:860px;padding:0 2rem 4rem}
    @media(max-width:768px){.page-wrapper{display:block;padding:0}.sidebar{display:none}.main-content{padding:0 1.25rem 3rem}}
    .masthead{text-align:center;padding:2.5rem 0 1.5rem;border-bottom:3px double var(--rule);margin-bottom:1.5rem}
    .masthead-logo svg{display:block;margin:0 auto .5rem}
    .masthead-tagline{font-family:'Source Serif 4',serif;font-weight:300;font-style:italic;font-size:.85rem;color:var(--secondary);letter-spacing:.08em;margin-bottom:.4rem}
    .masthead-dateline{font-family:'Source Serif 4',serif;font-weight:300;font-size:.7rem;color:var(--muted);letter-spacing:.18em;text-transform:uppercase}
    .hybrid-badge{display:inline-block;background:var(--accent);color:#fff;font-size:.6rem;letter-spacing:.12em;text-transform:uppercase;padding:.1rem .4rem;border-radius:2px;margin-left:.4rem;vertical-align:middle}
    .section-header{font-family:'Playfair Display',serif;font-size:.7rem;font-weight:400;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);border-top:1px solid var(--rule);padding-top:1.25rem;margin:2rem 0 1.5rem}
    .source-entry{display:flex;gap:1.25rem;padding-bottom:1.5rem;margin-bottom:1.5rem;border-bottom:1px solid var(--rule)}
    .source-entry:last-child{border-bottom:none}
    .source-num{font-family:'Source Serif 4',serif;font-size:.72rem;font-weight:300;color:var(--muted);min-width:1.5rem;padding-top:.2rem;text-align:right;flex-shrink:0}
    .source-body{flex:1}
    .source-balm-headline{font-family:'Playfair Display',serif;font-size:1rem;font-weight:600;line-height:1.35;color:var(--ink);margin-bottom:.6rem}
    .source-list{list-style:none;padding:0;margin:0}
    .source-list li{font-size:.85rem;line-height:1.6;color:var(--secondary);padding:.15rem 0}
    .source-list li::before{content:"· ";color:var(--muted)}
    .source-outlet{font-style:normal;color:var(--accent)}
    .source-original-headline{font-style:italic;color:var(--secondary)}
    .sidebar-title{font-family:'Playfair Display',serif;font-size:.65rem;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:1rem;padding-top:2rem}
    .archive-month{margin-bottom:1rem}
    .archive-month-label{font-size:.68rem;font-weight:300;letter-spacing:.08em;color:var(--secondary);text-transform:uppercase;margin-bottom:.3rem}
    .archive-link{display:block;font-size:.75rem;color:var(--secondary);padding:.1rem 0;line-height:1.6}
    .archive-link:hover{color:var(--accent);text-decoration:none}
    .footer{border-top:1px solid var(--rule);margin-top:3rem;padding:2rem 0;font-size:.78rem;color:var(--muted);line-height:1.7;text-align:center}
    .footer a{color:var(--muted);text-decoration:underline}
    .footer a:hover{color:var(--secondary)}
  </style>
</head>
<body>
<div class="page-wrapper">
  <nav class="sidebar" aria-label="Archive">
    <div class="sidebar-title">Archive</div>
    {% for month in archive_months %}
    <div class="archive-month">
      <div class="archive-month-label">{{ month.month }}</div>
      {% for entry in month.entries %}
      <a class="archive-link" href="../{{ entry.file }}">{{ entry.date[5:] }} {{ entry.run | upper }}</a>
      {% endfor %}
    </div>
    {% endfor %}
  </nav>
  <div class="main-content">
    <header class="masthead">
      <div class="masthead-logo">
        <a href="{{ digest_file }}" style="text-decoration:none">
          <svg width="180" height="56" viewBox="0 0 180 56" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <filter id="balm-spread" x="-3%" y="-3%" width="106%" height="106%">
                <feMorphology in="SourceGraphic" operator="dilate" radius="0.6" result="dilated"/>
                <feGaussianBlur in="dilated" stdDeviation="0.3" result="blurred"/>
                <feComposite in="SourceGraphic" in2="blurred" operator="over"/>
              </filter>
            </defs>
            <text x="90" y="44" font-family="Caveat, cursive" font-size="52" font-weight="700"
                  fill="#6b82a8" text-anchor="middle" letter-spacing="8" filter="url(#balm-spread)">Balm</text>
          </svg>
        </a>
      </div>
      <p class="masthead-tagline">Topical, anti-inflammatory news</p>
      <p class="masthead-dateline">
        {{ date_display }}{% if run_label %} &middot; {{ run_label }} Edition{% endif %}
        &middot; Sources <span class="hybrid-badge">Hybrid</span>
      </p>
    </header>

    <div class="section-header">Sources for this digest</div>

    {% for article in articles %}
    <div class="source-entry">
      <div class="source-num">{{ article.ref }}</div>
      <div class="source-body">
        <div class="source-balm-headline">{{ article.headline }}</div>
        <ul class="source-list">
          {% for src in article.sources %}
          <li>
            <a class="source-outlet" href="{{ src.url }}" target="_blank" rel="noopener noreferrer">{{ src.source }}</a>
            {% if src.original_headline %}
            &mdash; <span class="source-original-headline">&ldquo;{{ src.original_headline }}&rdquo;</span>
            {% endif %}
          </li>
          {% else %}
          <li><span style="color:var(--muted);font-style:italic">Source unavailable</span></li>
          {% endfor %}
        </ul>
      </div>
    </div>
    {% endfor %}

    <footer class="footer">
      <p><a href="{{ digest_file }}">&larr; Return to digest</a></p>
      <p style="margin-top:.75rem"><a href="../index.html">&larr; Main Balm digest</a></p>
    </footer>
  </div>
</div>
<script>
  (function() {
    fetch('../archive.json')
      .then(function(r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function(data) {
        var html = data.months.map(function(m) {
          var links = m.entries.map(function(e) {
            return '<a class="archive-link" href="../' + e.file + '">'
              + e.date.slice(5) + ' ' + e.run.toUpperCase() + '</a>';
          }).join('');
          return '<div class="archive-month"><div class="archive-month-label">'
            + m.month + '</div>' + links + '</div>';
        }).join('');
        var nav = document.querySelector('nav.sidebar');
        if (nav) nav.innerHTML = '<div class="sidebar-title">Archive</div>' + html;
      })
      .catch(function() {});
  })();
</script>
</body>
</html>"""


_HYBRID_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Balm Hybrid — Archive</title>
  <meta name="description" content="Experimental hybrid pipeline digests for Balm.">
  <meta name="theme-color" content="#6b82a8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Caveat:wght@700&family=Playfair+Display:ital,wght@0,400;0,600;1,400&family=Source+Serif+4:ital,wght@0,300;1,300&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    :root{--bg:#f2ede4;--ink:#2a2520;--secondary:#6a6058;--muted:#9a9288;--rule:#c8c0b4;--accent:#6b82a8}
    html{font-size:18px}
    body{background:var(--bg);color:var(--ink);font-family:'Source Serif 4',Georgia,serif;font-weight:300;line-height:1.7;min-height:100vh}
    a{color:var(--accent);text-decoration:none}
    a:hover{text-decoration:underline}
    .wrapper{max-width:700px;margin:0 auto;padding:0 1.5rem 4rem}
    .masthead{text-align:center;padding:2.5rem 0 1.5rem;border-bottom:3px double var(--rule);margin-bottom:2rem}
    .masthead-logo svg{display:block;margin:0 auto .5rem}
    .masthead-tagline{font-family:'Source Serif 4',serif;font-weight:300;font-style:italic;font-size:.85rem;color:var(--secondary);letter-spacing:.08em;margin-bottom:.4rem}
    .masthead-dateline{font-family:'Source Serif 4',serif;font-weight:300;font-size:.7rem;color:var(--muted);letter-spacing:.18em;text-transform:uppercase}
    .hybrid-badge{display:inline-block;background:var(--accent);color:#fff;font-size:.6rem;letter-spacing:.12em;text-transform:uppercase;padding:.1rem .4rem;border-radius:2px;margin-left:.4rem;vertical-align:middle}
    .section-header{font-family:'Playfair Display',serif;font-size:.7rem;font-weight:400;letter-spacing:.22em;text-transform:uppercase;color:var(--accent);border-top:1px solid var(--rule);padding-top:1.25rem;margin:2rem 0 1.25rem}
    .digest-entry{padding:.6rem 0;border-bottom:1px solid var(--rule);display:flex;justify-content:space-between;align-items:baseline;gap:1rem}
    .digest-entry:last-child{border-bottom:none}
    .digest-label{font-family:'Playfair Display',serif;font-size:1rem;color:var(--ink)}
    .digest-links{font-size:.78rem;color:var(--muted);white-space:nowrap}
    .digest-links a{color:var(--accent)}
    .empty{font-style:italic;color:var(--muted);font-size:.9rem}
    .notice{font-size:.85rem;color:var(--secondary);line-height:1.6;margin-bottom:1.5rem;padding:.75rem 1rem;border-left:3px solid var(--rule)}
    .footer{border-top:1px solid var(--rule);margin-top:3rem;padding:2rem 0;font-size:.78rem;color:var(--muted);line-height:1.7;text-align:center}
    .footer a{color:var(--muted);text-decoration:underline}
  </style>
</head>
<body>
<div class="wrapper">
  <header class="masthead">
    <div class="masthead-logo">
      <svg width="180" height="56" viewBox="0 0 180 56" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <filter id="balm-spread" x="-3%" y="-3%" width="106%" height="106%">
            <feMorphology in="SourceGraphic" operator="dilate" radius="0.6" result="dilated"/>
            <feGaussianBlur in="dilated" stdDeviation="0.3" result="blurred"/>
            <feComposite in="SourceGraphic" in2="blurred" operator="over"/>
          </filter>
        </defs>
        <text x="90" y="44" font-family="Caveat, cursive" font-size="52" font-weight="700"
              fill="#6b82a8" text-anchor="middle" letter-spacing="8" filter="url(#balm-spread)">Balm</text>
      </svg>
    </div>
    <p class="masthead-tagline">Topical, anti-inflammatory news</p>
    <p class="masthead-dateline">Hybrid Pipeline Archive <span class="hybrid-badge">Hybrid</span></p>
  </header>

  <p class="notice">
    These digests are generated by an experimental two-pass pipeline and are not the primary Balm output.
    <a href="../index.html">Return to main Balm digest.</a>
  </p>

  <div class="section-header">Hybrid digests</div>

  {% if entries %}
    {% for entry in entries %}
    <div class="digest-entry">
      <span class="digest-label">{{ entry.date }} {{ entry.run | upper }}</span>
      <span class="digest-links">
        <a href="{{ entry.file }}">Digest</a>
        &nbsp;&middot;&nbsp;
        <a href="{{ entry.sources_file }}">Sources</a>
      </span>
    </div>
    {% endfor %}
  {% else %}
    <p class="empty">No hybrid digests have been generated yet.</p>
  {% endif %}

  <footer class="footer">
    <p><a href="../index.html">&larr; Main Balm digest</a></p>
  </footer>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _jinja_env() -> Environment:
    return Environment(loader=BaseLoader())


def render_hybrid_digest(
    articles: list[dict], date_str: str, run: str, archive_months: list[dict]
) -> Path:
    env = _jinja_env()
    tmpl = env.from_string(_HYBRID_DIGEST_TEMPLATE)

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")
    sources_file = f"{date_str}-{run}-sources.html"

    html = tmpl.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        categories=_group_by_category(articles),
        archive_months=archive_months,
        sources_file=sources_file,
    )
    out_path = HYBRID_DOCS_DIR / f"{date_str}-{run}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] Hybrid digest: {out_path}")
    return out_path


def render_hybrid_sources(
    articles: list[dict], date_str: str, run: str, archive_months: list[dict]
) -> Path:
    env = _jinja_env()
    tmpl = env.from_string(_HYBRID_SOURCES_TEMPLATE)

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")
    digest_file = f"{date_str}-{run}.html"

    html = tmpl.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        articles=articles,
        archive_months=archive_months,
        digest_file=digest_file,
    )
    out_path = HYBRID_DOCS_DIR / f"{date_str}-{run}-sources.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] Hybrid sources: {out_path}")
    return out_path


def render_hybrid_index() -> Path:
    env = _jinja_env()
    tmpl = env.from_string(_HYBRID_INDEX_TEMPLATE)

    entries = collect_hybrid_archive()
    html = tmpl.render(entries=entries)
    out_path = HYBRID_DOCS_DIR / "index.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] Hybrid index: {out_path} ({len(entries)} entries)")
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Balm hybrid pipeline (two-pass)")
    parser.add_argument("--run", choices=["am", "pm"], default=None,
                        help="Edition to generate (default: auto-detect from current time)")
    parser.add_argument("--date", default=None,
                        help="Override date (YYYY-MM-DD). Default: today in PT.")
    args = parser.parse_args()

    pt_tz = tz.gettz("America/Los_Angeles")
    now_pt = datetime.now(pt_tz)
    date_str = args.date or now_pt.strftime("%Y-%m-%d")
    run = args.run or ("am" if now_pt.hour < 13 else "pm")

    print(f"[START] Balm hybrid pipeline — {date_str} {run.upper()}")
    HYBRID_DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # API keys
    news_api_key     = os.environ.get("NEWS_API_KEY", "")
    guardian_api_key = os.environ.get("GUARDIAN_API_KEY", "")
    nyt_api_key      = os.environ.get("NYT_API_KEY", "")
    anthropic_key    = os.environ.get("ANTHROPIC_API_KEY", "")

    if not anthropic_key:
        print("[ERROR] ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    # ── Step 1: Fetch ─────────────────────────────────────────────────────
    print("\n[1/7] Fetching articles from all sources...")
    raw: list[dict] = []

    if news_api_key:
        fetched = fetch_newsapi(news_api_key)
        print(f"  NewsAPI: {len(fetched)} articles")
        raw.extend(fetched)
    else:
        print("  [WARN] NEWS_API_KEY not set — skipping NewsAPI", file=sys.stderr)

    if guardian_api_key:
        fetched = fetch_guardian(guardian_api_key)
        print(f"  Guardian: {len(fetched)} articles")
        raw.extend(fetched)
    else:
        print("  [WARN] GUARDIAN_API_KEY not set — skipping Guardian", file=sys.stderr)

    if nyt_api_key:
        fetched = fetch_nyt(nyt_api_key)
        print(f"  NYT: {len(fetched)} articles")
        raw.extend(fetched)
    else:
        print("  [WARN] NYT_API_KEY not set — skipping NYT", file=sys.stderr)

    rss = fetch_rss_feeds()
    raw.extend(rss)
    print(f"  RSS feeds total: {len(rss)} articles")
    print(f"  Total raw: {len(raw)}")

    # ── Step 2: Exact-duplicate removal ───────────────────────────────────
    print("\n[2/7] Removing exact duplicates (same title + source + URL)...")
    articles = remove_exact_duplicates(raw)
    removed = len(raw) - len(articles)
    print(f"  {len(raw)} raw → {len(articles)} articles ({removed} exact duplicates removed)")

    if not articles:
        print("[ERROR] No articles after deduplication. Check API keys.", file=sys.stderr)
        sys.exit(1)

    # ── Step 3: Pass 1 — Story identification ─────────────────────────────
    print(f"\n[3/7] Pass 1 — Story identification ({len(articles)} articles → Claude)...")
    stories = pass1_identify_stories(articles, anthropic_key)

    if not stories:
        print("[ERROR] Pass 1 returned no stories. Cannot continue.", file=sys.stderr)
        sys.exit(1)

    # ── Step 4: Pass 2 — Per-story synthesis ──────────────────────────────
    print(f"\n[4/7] Pass 2 — Per-story synthesis ({len(stories)} stories × 1 Claude call each)...")
    processed: list[dict] = []
    pass2_calls = 0
    pass2_excluded = 0

    for rank, story in enumerate(stories, 1):
        story_id = story.get("story_id", rank)
        headline = story.get("headline", f"Story {story_id}")

        # Select most relevant source articles for this story
        selected = select_articles_for_story(articles, story, rank)

        if not selected:
            print(f"  Story {story_id:2d} \"{headline[:50]}\": no matching articles — skipped")
            continue

        outlets = ", ".join(dict.fromkeys(a["source"] for a in selected))
        print(f"  Story {story_id:2d} \"{headline[:50]}\": {len(selected)} sources ({outlets})")

        result = pass2_synthesize_story(story, selected, anthropic_key)
        pass2_calls += 1

        if result is None:
            print(f"    → [EXCLUDED] by editorial filter")
            pass2_excluded += 1
            continue

        attach_hybrid_sources(result, selected)
        category = result.get("category", "?")
        out_headline = result.get("headline", "?")[:60]
        print(f"    → [{category}] {out_headline}")
        processed.append(result)

    print(f"\n  Pass 2 summary: {pass2_calls} calls, "
          f"{len(processed)} included, {pass2_excluded} excluded by editorial filter")

    if not processed:
        print("[ERROR] No articles survived editorial filtering.", file=sys.stderr)
        sys.exit(1)

    # ── Step 5: Sort and number ───────────────────────────────────────────
    print(f"\n[5/7] Sorting and numbering {len(processed)} articles...")
    processed = sort_articles(processed)
    number_articles(processed)

    # ── Step 6: Load archive for sidebar (read-only, main pipeline's archive)
    print("\n[6/7] Loading main archive for sidebar navigation...")
    main_archive = collect_archive(MAIN_DOCS_DIR)
    archive_months = group_archive_by_month(main_archive)
    print(f"  {len(main_archive)} main digests found")

    # ── Step 7: Render ────────────────────────────────────────────────────
    print("\n[7/7] Rendering output files...")
    render_hybrid_digest(processed, date_str, run, archive_months)
    render_hybrid_sources(processed, date_str, run, archive_months)
    render_hybrid_index()

    print(f"\n[DONE] Balm hybrid {date_str} {run.upper()} complete.")
    print(f"  Stories published   : {len(processed)}")
    print(f"  Pass 1 stories      : {len(stories)}")
    print(f"  Pass 2 API calls    : {pass2_calls} (+ 1 for Pass 1 = {pass2_calls + 1} total)")
    print(f"  Editorial exclusions: {pass2_excluded}")


if __name__ == "__main__":
    main()
