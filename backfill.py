#!/usr/bin/env python3
"""
Balm backfill script — regenerates digest HTML for past dates.
Usage: python backfill.py --start 2026-05-22 --end 2026-05-27

Note: News APIs return current articles, not historical ones.
Content will reflect today's news stamped with past dates.
Purpose: fix archive sidebar and apply current pipeline quality to old entries.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Import all pipeline functions directly — backfill runs the same steps as main()
from pipeline import (
    DOCS_DIR,
    fetch_newsapi,
    fetch_guardian,
    fetch_nyt,
    fetch_rss_feeds,
    remove_exact_duplicates,
    cluster_articles,
    editorial_review,
    classify_difficult_news,
    call_claude,
    attach_sources,
    sort_articles,
    number_articles,
    filter_pm_duplicates,
    fetch_sp500,
    save_metadata,
    select_top_stories,
    order_categories,
    collect_archive,
    write_archive_json,
    render_digest,
    render_sources,
    render_index,
    render_contact,
)


def run_one(date_str: str, run: str, api_keys: dict) -> bool:
    """Run the full pipeline for a single date/edition.

    Returns True on success, False on failure. Never raises — logs and continues.
    """
    print(f"\n[{date_str}] [{run.upper()}] — generating...")

    news_key = api_keys["news"]
    guardian_key = api_keys["guardian"]
    nyt_key = api_keys["nyt"]
    anthropic_key = api_keys["anthropic"]

    try:
        # ── Fetch ─────────────────────────────────────────────────────────
        raw_articles: list[dict] = []
        if news_key:
            raw_articles.extend(fetch_newsapi(news_key))
        else:
            print("  [WARN] NEWS_API_KEY not set — skipping NewsAPI")
        if guardian_key:
            raw_articles.extend(fetch_guardian(guardian_key))
        else:
            print("  [WARN] GUARDIAN_API_KEY not set — skipping Guardian")
        if nyt_key:
            raw_articles.extend(fetch_nyt(nyt_key))
        else:
            print("  [WARN] NYT_API_KEY not set — skipping NYT")
        raw_articles.extend(fetch_rss_feeds())

        # ── Deduplicate ────────────────────────────────────────────────────
        deduped = remove_exact_duplicates(raw_articles)
        if not deduped:
            print(f"  [ERROR] No articles fetched — skipping {date_str} {run.upper()}",
                  file=sys.stderr)
            return False

        # ── Cluster ────────────────────────────────────────────────────────
        clusters = cluster_articles(deduped, anthropic_key)
        clusters = editorial_review(clusters, anthropic_key)

        # ── Classify and synthesize ────────────────────────────────────────
        difficult_flags = classify_difficult_news(clusters, anthropic_key)
        articles = call_claude(clusters, anthropic_key, difficult_flags)
        attach_sources(articles, clusters)
        articles = sort_articles(articles)
        number_articles(articles)

        # ── PM deduplication ───────────────────────────────────────────────
        if run == "pm":
            am_path = DOCS_DIR / f"{date_str}-am.json"
            articles = filter_pm_duplicates(articles, anthropic_key, am_path)
            number_articles(articles)

        # ── S&P 500 ────────────────────────────────────────────────────────
        sp500 = fetch_sp500()

        # ── Metadata ───────────────────────────────────────────────────────
        metadata = save_metadata(date_str, run, articles, len(deduped), sp500, DOCS_DIR)

        # ── Top stories + category order ───────────────────────────────────
        top_stories = select_top_stories(articles, anthropic_key)
        category_order = order_categories(articles, anthropic_key)

        # ── Render ─────────────────────────────────────────────────────────
        archive = collect_archive(DOCS_DIR)
        render_digest(articles, date_str, run, metadata, archive, DOCS_DIR,
                      top_stories=top_stories, category_order=category_order)
        render_sources(articles, date_str, run, archive, DOCS_DIR)
        render_index(articles, date_str, run, metadata, archive, DOCS_DIR,
                     top_stories=top_stories, category_order=category_order)
        render_contact(archive, DOCS_DIR)

        print(f"[{date_str}] [{run.upper()}] — done ({len(articles)} stories)")
        return True

    except Exception as e:
        print(f"[{date_str}] [{run.upper()}] — FAILED: {e}", file=sys.stderr)
        return False


def date_range(start: datetime, end: datetime) -> list[datetime]:
    """Return list of dates from start to end inclusive."""
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Balm backfill — regenerate digest HTML for a date range.",
        epilog="Note: News APIs return current articles. Content reflects today's "
               "news stamped with past dates.",
    )
    parser.add_argument("--start", required=True, metavar="YYYY-MM-DD",
                        help="First date to generate (inclusive)")
    parser.add_argument("--end", required=True, metavar="YYYY-MM-DD",
                        help="Last date to generate (inclusive)")
    parser.add_argument("--editions", choices=["am", "pm", "both"], default="both",
                        help="Which editions to generate (default: both)")
    args = parser.parse_args()

    # Parse dates
    try:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError as e:
        print(f"[ERROR] Invalid date format: {e}", file=sys.stderr)
        sys.exit(1)
    if start > end:
        print("[ERROR] --start must be on or before --end", file=sys.stderr)
        sys.exit(1)

    # Read API keys
    api_keys = {
        "news": os.environ.get("NEWS_API_KEY", ""),
        "guardian": os.environ.get("GUARDIAN_API_KEY", ""),
        "nyt": os.environ.get("NYT_API_KEY", ""),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
    }
    if not api_keys["anthropic"]:
        print("[ERROR] ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    # Resolve editions list
    if args.editions == "both":
        editions = ["am", "pm"]
    else:
        editions = [args.editions]

    dates = date_range(start, end)
    runs = [(d.strftime("%Y-%m-%d"), ed) for d in dates for ed in editions]

    print(f"[START] Backfill: {len(runs)} run(s) from {args.start} to {args.end} "
          f"({args.editions} edition{'s' if args.editions == 'both' else ''})")
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    success = 0
    failure = 0

    for i, (date_str, run) in enumerate(runs):
        ok = run_one(date_str, run, api_keys)
        if ok:
            success += 1
        else:
            failure += 1

        # Delay between runs to avoid API rate limits — skip after the last run
        if i < len(runs) - 1:
            print("  Waiting 3s before next run...")
            time.sleep(3)

    # Final archive update covering all generated entries
    print("\n[FINAL] Updating archive index...")
    archive = collect_archive(DOCS_DIR)
    write_archive_json(archive, DOCS_DIR)

    print(f"\n[DONE] Backfill complete: {success} succeeded, {failure} failed")
    if failure:
        sys.exit(1)


if __name__ == "__main__":
    main()
