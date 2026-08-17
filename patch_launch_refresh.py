#!/usr/bin/env python3
"""
patch_launch_refresh.py — bring already-published pages up to the launch-ready
template, without regenerating any editorial content.

The pipeline only rewrites the current edition, so template changes reach new
pages immediately and old ones never. This applies the launch batch to every
previously published digest and sources page:

  1. --muted token   #9a9288 -> #726960   (2.63:1 -> 4.61:1 against parchment;
                     the old value failed WCAG AA and was invisible to readers
                     with ordinary age-related vision loss)
  2. .market-trend   var(--muted) 0.88rem -> var(--secondary) 0.92rem
  3. footer link     "Contact" -> "About"
  4. <head>          Open Graph + Twitter Card tags, canonical link, and RSS
                     autodiscovery, so shared archive links render a preview card
  5. masthead        one-line description with an About link (digest pages only)

The sources toggle keeps its hard-coded #9a9288: that subtlety is deliberate and
is set directly rather than through the token, so it is untouched here too.

Idempotent — pages that already carry the OG tags are skipped.

Usage:
    python patch_launch_refresh.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

DOCS_DIR = Path(__file__).parent / "docs"
BASE_URL = "https://balm.news"

OLD_MUTED, NEW_MUTED = "#9a9288", "#726960"

MASTHEAD_CSS = """
    .masthead-description {
      font-family: 'Source Serif 4', serif;
      font-weight: 300;
      font-size: 0.9rem;
      line-height: 1.6;
      color: var(--secondary);
      max-width: 34rem;
      margin: 0.9rem auto 0;
    }
"""

MASTHEAD_HTML = (
    '<p class="masthead-description">Balm rewrites the day\'s news to strip out '
    "inflammatory language, clickbait, and emotional framing — leaving just "
    'what happened. <a href="/contact.html">About Balm</a>.</p>'
)

DIGEST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)\.html$")
SOURCES_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)-sources\.html$")


def patch_css(soup: BeautifulSoup, is_digest: bool) -> bool:
    """Recolour the muted token and the market-trend line; add masthead CSS."""
    style = soup.find("style")
    if style is None or not style.string:
        return False
    css = style.string
    before = css

    css = css.replace(f"--muted: {OLD_MUTED};", f"--muted: {NEW_MUTED};", 1)
    # The trend line is editorial copy, not decoration, so it needs AA contrast.
    css = re.sub(
        r"(\.market-trend \{[^}]*?)font-size: 0\.88rem;(\s*)color: var\(--muted\);",
        r"\1font-size: 0.92rem;\2color: var(--secondary);",
        css,
    )
    if is_digest and ".masthead-description" not in css:
        css = re.sub(r"(\.masthead-dateline \{[^}]*\}\n)", r"\1" + MASTHEAD_CSS,
                     css, count=1)

    if css == before:
        return False
    style.string = css
    return True


def patch_footer_label(soup: BeautifulSoup) -> bool:
    """Relabel the footer Contact link as About (the page is now an About page)."""
    link = soup.find("a", class_="footer-contact")
    if link is None or link.get_text(strip=True) != "Contact":
        return False
    link.string = "About"
    return True


def add_masthead_description(soup: BeautifulSoup) -> bool:
    """Insert the one-line explainer after the dateline, on digest pages."""
    if soup.find(class_="masthead-description"):
        return False
    dateline = soup.find("p", class_="masthead-dateline")
    if dateline is None:
        return False
    dateline.insert_after(BeautifulSoup(MASTHEAD_HTML, "html.parser"))
    return True


def add_head_tags(soup: BeautifulSoup, url: str, title: str, description: str) -> bool:
    """Add OG/Twitter/canonical/RSS tags. Scrapers require absolute URLs."""
    head = soup.find("head")
    if head is None or head.find("meta", attrs={"property": "og:image"}):
        return False

    def meta(attr: str, key: str, value: str):
        tag = soup.new_tag("meta")
        tag[attr] = key
        tag["content"] = value
        return tag

    tags = [
        meta("property", "og:site_name", "Balm"),
        meta("property", "og:title", title),
        meta("property", "og:description", description),
        meta("property", "og:type", "article"),
        meta("property", "og:url", url),
        meta("property", "og:image", f"{BASE_URL}/og-image.png"),
        meta("property", "og:image:width", "1200"),
        meta("property", "og:image:height", "630"),
        meta("property", "og:image:alt", "Balm — topical, anti-inflammatory news"),
        meta("name", "twitter:card", "summary_large_image"),
        meta("name", "twitter:title", title),
        meta("name", "twitter:description", description),
        meta("name", "twitter:image", f"{BASE_URL}/og-image.png"),
    ]

    canonical = soup.new_tag("link", rel="canonical", href=url)
    feed = soup.new_tag("link", rel="alternate", type="application/rss+xml",
                        href="/feed.xml")
    feed["title"] = "Balm"

    anchor = head.find("meta", attrs={"name": "theme-color"}) or head.find("title")
    cursor = anchor
    for tag in [canonical] + tags + [feed]:
        cursor.insert_after(tag)
        cursor = tag
    return True


def patch_page(path: Path, dry_run: bool) -> str | None:
    m_digest, m_sources = DIGEST_RE.match(path.name), SOURCES_RE.match(path.name)
    if not (m_digest or m_sources):
        return None
    is_digest = bool(m_digest)
    date_str, run = (m_digest or m_sources).groups()

    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else "Balm"
    desc_tag = soup.find("meta", attrs={"name": "description"})
    description = (desc_tag.get("content") if desc_tag else
                   "The day's news, stripped of inflammatory language.")
    suffix = "" if is_digest else "-sources"
    url = f"{BASE_URL}/{date_str}-{run}{suffix}.html"

    done = []
    if patch_css(soup, is_digest):
        done.append("css")
    if patch_footer_label(soup):
        done.append("footer")
    if is_digest and add_masthead_description(soup):
        done.append("description")
    if add_head_tags(soup, url, title, description):
        done.append("head")

    if not done:
        return None
    if not dry_run:
        path.write_text(str(soup), encoding="utf-8")
    return ", ".join(done)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="patch at most N files (for testing)")
    args = ap.parse_args()

    if not DOCS_DIR.is_dir():
        print(f"[ERROR] {DOCS_DIR} not found", file=sys.stderr)
        return 1

    patched = skipped = 0
    for path in sorted(DOCS_DIR.iterdir()):
        if args.limit and patched >= args.limit:
            break
        result = patch_page(path, args.dry_run)
        if result is None:
            skipped += 1
        else:
            patched += 1
            if patched <= 5 or patched % 50 == 0:
                print(f"  [PATCH] {path.name}: {result}")

    verb = "would patch" if args.dry_run else "patched"
    print(f"\n{verb} {patched} file(s); {skipped} already current or not a page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
