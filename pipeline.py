#!/usr/bin/env python3
"""Balm pipeline — fetches news, rewrites via Claude, generates digest HTML + audio."""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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
BASE_URL = "https://brianfunk.github.io/balm"  # update after deploy

CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS = 8000

ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Rachel

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

EDITORIAL_SYSTEM_PROMPT = """You are the editorial engine for Balm, a news digest with one guiding principle: inform without agitating.

AUDIENCE: Primarily American readers. Cover international stories when they have direct or significant relevance to American readers — economic impact, military involvement, immigration implications, global health, major geopolitical shifts. Skip international stories that are purely regional with no US dimension.

REWRITING RULES:
- Strip all inflammatory, loaded, or emotionally charged language
- Remove editorializing, speculation, and opinion framing
- Rewrite headlines to be factual and descriptive — no clickbait, no fear language, no superlatives
- Use plain declarative sentences. Calm, authoritative tone.
- Never use: "shocking", "bombshell", "explosive", "crisis", "chaos", "slams", "blasts", "rips", "warns of disaster", "you won't believe", or any equivalent
- Write as Balm's own neutral voice — never attribute claims to a specific outlet in the text (e.g. never write "according to the New York Times"). All claims are attributed to original sources via the article link only, not in the text itself.
- Perpetrator details — names, photos, methods, manifestos — must be omitted from all violent events

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
      "category": "CATEGORY",
      "headline": "Rewritten factual headline",
      "brief_summary": "2-3 sentence factual summary.",
      "full_summary": "2-3 paragraph full summary written for audio consumption.",
      "source": "Original source name",
      "url": "original article url",
      "isDifficult": false
    }
  ]
}

Set isDifficult: true for DIFFICULT NEWS items only.
Return null in the array position for excluded stories.
Return between 10 and 16 articles total.
Category display order: GEOPOLITICS, ECONOMY, DOMESTIC POLICY, SCIENCE & HEALTH, TECHNOLOGY, NATURAL EVENTS, SPORTS — then DIFFICULT NEWS last and collapsed."""


# ---------------------------------------------------------------------------
# Article fetching
# ---------------------------------------------------------------------------

def fetch_newsapi(api_key: str) -> list[dict]:
    categories = ["world", "business", "technology", "health", "science", "sports"]
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
    sections = ["world", "business", "technology", "health", "science", "sports"]
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


def normalize_title(title: str) -> set[str]:
    """Return lowercase token set for similarity comparison."""
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    stopwords = {"a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
                 "of", "with", "is", "are", "was", "were", "be", "been", "by", "as"}
    return {t for t in tokens if t not in stopwords and len(t) > 2}


def deduplicate(articles: list[dict]) -> list[dict]:
    """Remove near-duplicate articles by title Jaccard similarity >= 0.5."""
    seen: list[set] = []
    unique = []
    for article in articles:
        tokens = normalize_title(article["title"])
        if not tokens:
            continue
        duplicate = False
        for seen_tokens in seen:
            intersection = tokens & seen_tokens
            union = tokens | seen_tokens
            if union and len(intersection) / len(union) >= 0.5:
                duplicate = True
                break
        if not duplicate:
            seen.append(tokens)
            unique.append(article)
    return unique


# ---------------------------------------------------------------------------
# Claude editorial processing
# ---------------------------------------------------------------------------

def build_user_prompt(articles: list[dict]) -> str:
    lines = ["Here are the articles to process:\n"]
    for i, a in enumerate(articles, 1):
        lines.append(f"[{i}] Title: {a['title']}")
        lines.append(f"    Source: {a['source']}")
        lines.append(f"    URL: {a['url']}")
        if a.get("description"):
            lines.append(f"    Description: {a['description']}")
        lines.append("")
    return "\n".join(lines)


def call_claude(articles: list[dict], anthropic_key: str) -> list[dict]:
    client = Anthropic(api_key=anthropic_key)
    user_prompt = build_user_prompt(articles)

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


def sort_articles(articles: list[dict]) -> list[dict]:
    """Sort articles by canonical category order."""
    order = {cat: i for i, cat in enumerate(CATEGORY_ORDER)}
    return sorted(articles, key=lambda a: order.get(a.get("category", ""), 99))


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
        key = e["date"][:7]  # YYYY-MM
        months.setdefault(key, []).append(e)
    result = []
    for key in sorted(months.keys(), reverse=True):
        year, month = key.split("-")
        month_name = datetime(int(year), int(month), 1).strftime("%B %Y")
        result.append({"month": month_name, "entries": months[key]})
    return result


def render_digest(articles: list[dict], date_str: str, run: str, metadata: dict,
                  archive: list[dict], docs_dir: Path) -> Path:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("digest.html")

    pt_tz = tz.gettz("America/Los_Angeles")
    now_pt = datetime.now(pt_tz)
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")

    # Group articles by category
    grouped: dict[str, list] = {}
    for article in articles:
        cat = article.get("category", "UNCATEGORIZED")
        grouped.setdefault(cat, []).append(article)

    categories_ordered = []
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            categories_ordered.append({"name": cat, "articles": grouped[cat]})

    mp3_file = f"{date_str}-{run}.mp3"
    mp3_path = docs_dir / mp3_file
    has_audio = mp3_path.exists()

    html = template.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        categories=categories_ordered,
        archive_months=group_archive_by_month(archive),
        metadata=metadata,
        has_audio=has_audio,
        mp3_file=mp3_file if has_audio else None,
        sp500=metadata.get("sp500_close"),
        base_url=BASE_URL,
    )

    out_path = docs_dir / f"{date_str}-{run}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"[OK] Digest HTML: {out_path}")
    return out_path


def render_index(articles: list[dict], date_str: str, run: str, metadata: dict,
                 archive: list[dict], docs_dir: Path) -> None:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("index.html")

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    date_display = date_obj.strftime("%A, %B %-d, %Y")

    grouped: dict[str, list] = {}
    for article in articles:
        cat = article.get("category", "UNCATEGORIZED")
        grouped.setdefault(cat, []).append(article)

    categories_ordered = []
    for cat in CATEGORY_ORDER:
        if cat in grouped:
            categories_ordered.append({"name": cat, "articles": grouped[cat]})

    mp3_file = f"{date_str}-{run}.mp3"
    mp3_path = docs_dir / mp3_file
    has_audio = mp3_path.exists()

    html = template.render(
        date_display=date_display,
        date_str=date_str,
        run=run,
        run_label="AM" if run == "am" else "PM",
        categories=categories_ordered,
        archive_months=group_archive_by_month(archive),
        metadata=metadata,
        has_audio=has_audio,
        mp3_file=mp3_file if has_audio else None,
        sp500=metadata.get("sp500_close"),
        base_url=BASE_URL,
        current_digest_file=f"{date_str}-{run}.html",
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
        "The topical anti-inflammatory — a daily news digest stripped of inflammatory "
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
    for mp3 in mp3_files[:50]:  # cap at 50 episodes
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
        fe.podcast.itunes_duration("00:00")  # ElevenLabs doesn't return duration easily

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
    if args.date:
        date_str = args.date
    else:
        date_str = now_pt.strftime("%Y-%m-%d")

    if args.run:
        run = args.run
    else:
        run = "am" if now_pt.hour < 13 else "pm"

    print(f"[START] Balm pipeline — {date_str} {run.upper()}")

    # Ensure output directory exists
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Read API keys
    news_api_key = os.environ.get("NEWS_API_KEY", "")
    guardian_api_key = os.environ.get("GUARDIAN_API_KEY", "")
    nyt_api_key = os.environ.get("NYT_API_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    elevenlabs_key = os.environ.get("ELEVEN_LABS_API_KEY", "")

    # --- Step 1: Fetch ---
    print("[1/9] Fetching articles...")
    raw_articles: list[dict] = []

    if news_api_key:
        fetched = fetch_newsapi(news_api_key)
        print(f"  NewsAPI: {len(fetched)} articles")
        raw_articles.extend(fetched)
    else:
        print("[WARN] NEWS_API_KEY not set — skipping NewsAPI")

    if guardian_api_key:
        fetched = fetch_guardian(guardian_api_key)
        print(f"  Guardian: {len(fetched)} articles")
        raw_articles.extend(fetched)
    else:
        print("[WARN] GUARDIAN_API_KEY not set — skipping Guardian")

    if nyt_api_key:
        fetched = fetch_nyt(nyt_api_key)
        print(f"  NYT: {len(fetched)} articles")
        raw_articles.extend(fetched)
    else:
        print("[WARN] NYT_API_KEY not set — skipping NYT")

    # --- Step 2: Deduplicate ---
    print("[2/9] Deduplicating...")
    unique_articles = deduplicate(raw_articles)
    print(f"  {len(raw_articles)} raw → {len(unique_articles)} unique")

    if not unique_articles:
        print("[ERROR] No articles fetched. Check API keys.", file=sys.stderr)
        sys.exit(1)

    # --- Step 3: Claude editorial processing ---
    print("[3/9] Sending to Claude for editorial processing...")
    if not anthropic_key:
        print("[ERROR] ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    processed_articles = call_claude(unique_articles, anthropic_key)
    processed_articles = sort_articles(processed_articles)
    print(f"  Claude returned {len(processed_articles)} articles after filtering")

    # --- Step 4: S&P 500 ---
    print("[4/9] Fetching S&P 500 close...")
    sp500 = fetch_sp500()
    if sp500:
        print(f"  S&P 500: {sp500}")
    else:
        print("  S&P 500: unavailable (non-blocking)")

    # --- Step 5: Save metadata ---
    print("[5/9] Saving metadata...")
    metadata = save_metadata(date_str, run, processed_articles, len(unique_articles), sp500, DOCS_DIR)

    # --- Step 6: Generate audio ---
    print("[6/9] Generating audio...")
    mp3_path = None
    if elevenlabs_key:
        script = build_audio_script(processed_articles, date_str, run)
        mp3_path = generate_audio(script, date_str, run, elevenlabs_key, DOCS_DIR)
    else:
        print("[WARN] ELEVEN_LABS_API_KEY not set — skipping audio")

    # --- Step 7: Collect archive ---
    print("[7/9] Building archive index...")
    archive = collect_archive(DOCS_DIR)

    # --- Step 8: Render HTML ---
    print("[8/9] Rendering HTML...")
    render_digest(processed_articles, date_str, run, metadata, archive, DOCS_DIR)
    render_index(processed_articles, date_str, run, metadata, archive, DOCS_DIR)

    # --- Step 9: Update podcast feed ---
    print("[9/9] Updating podcast RSS feed...")
    try:
        update_podcast_feed(DOCS_DIR)
    except Exception as e:
        print(f"[WARN] Podcast feed update failed: {e}", file=sys.stderr)

    print(f"\n[DONE] Balm {date_str} {run.upper()} complete.")
    print(f"  Stories published: {len(processed_articles)}")
    print(f"  Audio: {'yes' if mp3_path else 'no'}")
    print(f"  S&P 500: {sp500 if sp500 else 'unavailable'}")


if __name__ == "__main__":
    main()
