#!/usr/bin/env python3
"""
patch_archive_alignment.py — bring older published pages in line with the
current templates, without regenerating any editorial content.

Background
----------
`patch_old_digests.py` retrofitted the single-column layout onto digest pages,
but it inserted markup without the matching CSS and never touched the companion
sources pages at all. The result, on pages published before 2026-06-27 PM:

  Digest pages (49)
    - `.footer-kofi` / `.footer-contact` / `.archive-link` markup is present but
      unstyled, so the footer hierarchy is inverted (Ko-fi renders as muted grey
      body text) and "Browse past editions" inherits the old *sidebar*
      `.archive-link` rule — left-aligned, roman, 13.5px.
    - Dead `.mobile-archive-*` rules remain for elements already removed.

  Sources pages (51)
    - Still carry the whole archive sidebar: `<nav class="sidebar">`, the
      two-column `.page-wrapper`, and all its CSS.
    - Masthead is not wrapped in the home link.
    - Footer links carry no `footer-kofi` / `footer-contact` classes.

This script fixes both, idempotently. Editorial content is never touched.

Usage
-----
    python patch_archive_alignment.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

DOCS_DIR = Path(__file__).parent / "docs"

# Rules whose elements no longer exist. Any CSS rule whose selector matches one
# of these is dropped, at the top level and inside @media blocks alike.
DEAD_SELECTOR_PATTERNS = (
    ".sidebar",
    ".mobile-archive",
    ".archive-month",
    ".archive-link",  # the old sidebar rule; the canonical one is re-added below
)

# Canonical rules, copied verbatim from templates/digest.html and
# templates/sources.html. Appended to the end of the stylesheet so they win on
# cascade order regardless of what survives above.
CANONICAL_CSS = """
    /* --- patched to current template --- */
    .masthead-link {
      text-decoration: none;
      display: inline-block;
    }

    .archive-link-section {
      padding: 1.5rem 0;
      border-top: 1px solid var(--rule);
      text-align: center;
    }

    .archive-link {
      font-family: 'Source Serif 4', serif;
      font-size: 0.85rem;
      font-style: italic;
      color: var(--muted);
      text-decoration: none;
      letter-spacing: 0.04em;
      border-bottom: 1px solid var(--rule);
      transition: color 0.15s, border-color 0.15s;
    }

    .archive-link:hover {
      color: var(--accent);
      border-color: var(--accent);
      text-decoration: none;
    }

    .footer .footer-kofi {
      font-size: 0.92rem;
      color: var(--accent);
      font-weight: 400;
      text-decoration: none;
      border-bottom: 1px solid var(--accent);
      opacity: 0.85;
      transition: opacity 0.15s;
    }

    .footer .footer-kofi:hover {
      opacity: 1;
      text-decoration: none;
    }

    .footer .footer-contact {
      font-size: 0.88rem;
      color: var(--ink-light);
      text-decoration: none;
      border-bottom: 1px solid var(--rule);
      transition: color 0.15s;
    }

    .footer .footer-contact:hover {
      color: var(--accent);
      text-decoration: none;
    }
"""

# Single-column layout for sources pages, matching templates/sources.html.
SOURCES_LAYOUT_CSS = """
    .page-wrapper {
      max-width: 860px;
      margin: 0 auto;
      padding: 0 1.25rem;
    }

    .main-content {
      padding: 0 0 4rem;
    }

    @media (max-width: 768px) {
      .main-content { padding: 0 0 3rem; }
    }
"""

PATCH_MARKER = "/* --- patched to current template --- */"

# Older sources pages never declared --ink-light, so `.footer-contact` would
# silently fall back to inherited colour. Declared only where it is missing.
INK_LIGHT_CSS = """
    :root { --ink-light: #5a5048; }
"""


# ---------------------------------------------------------------------------
# CSS surgery
# ---------------------------------------------------------------------------

def _split_rules(css: str):
    """Split a stylesheet into top-level (selector, full_text) chunks.

    Walks the text tracking brace depth so that @media blocks come back as a
    single chunk with their inner rules intact. Anything that is not a rule
    (comments, stray whitespace) is yielded with a selector of None.
    """
    chunks = []
    depth = 0
    start = 0
    sel_end = None
    for i, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                sel_end = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                selector = css[start:sel_end].strip()
                chunks.append((selector, css[start:i + 1]))
                start = i + 1
                sel_end = None
    tail = css[start:]
    if tail.strip():
        chunks.append((None, tail))
    return chunks


_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _selector_is_dead(selector: str) -> bool:
    """True if every comma-separated part of `selector` targets removed markup.

    A rule is only dropped when the whole thing is dead — a selector list that
    still styles something live is left alone.

    Comments are stripped before splitting: a chunk carries any comment that
    precedes it, and prose commas ("/* CSS rotation, no substitution */") would
    otherwise split the selector and hide the real target.
    """
    selector = _COMMENT_RE.sub("", selector)
    parts = [p.strip() for p in selector.split(",") if p.strip()]
    if not parts:
        return False
    for part in parts:
        # `.archive-link-section` is live markup; don't let the `.archive-link`
        # prefix match drop it.
        if ".archive-link-section" in part:
            return False
        if not any(pat in part for pat in DEAD_SELECTOR_PATTERNS):
            return False
    return True


def strip_dead_css(css: str) -> tuple[str, int]:
    """Remove rules for markup that no longer exists, including inside @media."""
    out = []
    removed = 0
    for selector, text in _split_rules(css):
        if selector is None:
            out.append(text)
            continue
        if selector.startswith("@media"):
            head, _, body = text.partition("{")
            inner = body.rsplit("}", 1)[0]
            cleaned, n = strip_dead_css(inner)
            removed += n
            if cleaned.strip():
                out.append(f"{head}{{{cleaned.rstrip()}\n    }}")
            else:
                removed += 1  # the @media block itself is now empty
            continue
        if _selector_is_dead(selector):
            removed += 1
            continue
        out.append(text)
    return "".join(out), removed


def patch_stylesheet(soup: BeautifulSoup, extra_css: str = "") -> int:
    """Strip dead rules from the page's <style> and append canonical ones."""
    style = soup.find("style")
    if style is None:
        return 0
    css = style.string or ""
    if PATCH_MARKER in css:
        return 0  # already patched
    cleaned, removed = strip_dead_css(css)
    prelude = "" if "--ink-light" in css else INK_LIGHT_CSS
    style.string = cleaned.rstrip() + "\n" + extra_css + prelude + CANONICAL_CSS
    return removed


# ---------------------------------------------------------------------------
# Markup surgery (sources pages)
# ---------------------------------------------------------------------------

def remove_sidebar(soup: BeautifulSoup) -> bool:
    nav = soup.find("nav", class_="sidebar")
    if nav is None:
        return False
    nav.decompose()
    return True


def wrap_masthead_link(soup: BeautifulSoup) -> str | None:
    """Point the masthead at the site root, matching the current templates.

    Older sources pages already wrap the wordmark in a link, but it targets that
    day's digest and carries an inline style instead of the `masthead-link`
    class — so the masthead means "back to this digest" there and "home"
    everywhere else. Normalise it rather than leaving the behaviour split.
    """
    logo = soup.find(class_="masthead-logo")
    if logo is None:
        return None
    link = logo.find("a")
    if link is not None:
        if link.get("href") == "https://balm.news" and "masthead-link" in (link.get("class") or []):
            return None
        link["href"] = "https://balm.news"
        link["class"] = ["masthead-link"]
        del link["style"]
        return "retargeted"
    svg = logo.find("svg")
    if svg is None:
        return None
    link = soup.new_tag("a", href="https://balm.news")
    link["class"] = "masthead-link"
    svg.wrap(link)
    return "added"


def remove_sidebar_script(soup: BeautifulSoup) -> bool:
    """Drop the dead archive.json fetch that used to populate the sidebar.

    `patch_old_digests.py` removed this from digest pages but not from sources
    pages, so 51 of them still request archive.json on every load and write into
    a `nav.sidebar` that no longer exists. Guarded, so harmless — but it is a
    wasted round trip and the current template has no such script.
    """
    removed = False
    for script in soup.find_all("script"):
        text = script.string or ""
        if "nav.sidebar" in text and "archive.json" in text:
            script.decompose()
            removed = True
    return removed


def tag_footer_links(soup: BeautifulSoup) -> int:
    """Add footer-kofi / footer-contact classes to the relevant footer links."""
    footer = soup.find("footer", class_="footer")
    if footer is None:
        return 0
    changed = 0
    for a in footer.find_all("a", href=True):
        existing = a.get("class") or []
        if "ko-fi.com" in a["href"] and "footer-kofi" not in existing:
            a["class"] = existing + ["footer-kofi"]
            changed += 1
        elif a["href"].endswith("/contact.html") and "footer-contact" not in existing:
            a["class"] = existing + ["footer-contact"]
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Per-file drivers
# ---------------------------------------------------------------------------

def needs_patch(html: str) -> bool:
    """True only for pages predating the current template.

    Current pages legitimately carry an `.archive-link` rule, so the dead-rule
    sweep cannot be the trigger — it would match everything. The reliable
    markers are removed markup that is still present (`.sidebar`,
    `.mobile-archive-*`) or the canonical footer rule being absent.
    """
    if PATCH_MARKER in html:
        return False
    return (
        "mobile-archive" in html
        or 'class="sidebar"' in html
        or ".footer .footer-kofi" not in html
    )


def patch_digest(path: Path, dry_run: bool) -> str | None:
    html = path.read_text(encoding="utf-8")
    if not needs_patch(html):
        return None
    soup = BeautifulSoup(html, "html.parser")
    removed = patch_stylesheet(soup)
    linked = wrap_masthead_link(soup)
    tagged = tag_footer_links(soup)
    if not (removed or linked or tagged):
        return None
    if not dry_run:
        path.write_text(str(soup), encoding="utf-8")
    return (f"{removed} dead rule(s) removed, masthead_link={linked or 'ok'}, "
            f"{tagged} footer link(s) tagged, CSS appended")


def patch_sources(path: Path, dry_run: bool) -> str | None:
    html = path.read_text(encoding="utf-8")
    if not needs_patch(html):
        return None
    soup = BeautifulSoup(html, "html.parser")
    had_sidebar = remove_sidebar(soup)
    had_script = remove_sidebar_script(soup)
    # Only sources pages that carried the sidebar need the layout override.
    removed = patch_stylesheet(soup, SOURCES_LAYOUT_CSS if had_sidebar else "")
    linked = wrap_masthead_link(soup)
    tagged = tag_footer_links(soup)
    if not (had_sidebar or had_script or removed or linked or tagged):
        return None
    if not dry_run:
        path.write_text(str(soup), encoding="utf-8")
    return (f"sidebar={'removed' if had_sidebar else 'absent'}, "
            f"script={'removed' if had_script else 'absent'}, "
            f"{removed} dead rule(s), masthead_link={linked or 'ok'}, "
            f"{tagged} footer link(s) tagged")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing files")
    args = ap.parse_args()

    if not DOCS_DIR.is_dir():
        print(f"[ERROR] {DOCS_DIR} not found", file=sys.stderr)
        return 1

    digest_re = re.compile(r"^\d{4}-\d{2}-\d{2}-(am|pm)\.html$")
    sources_re = re.compile(r"^\d{4}-\d{2}-\d{2}-(am|pm)-sources\.html$")

    patched = skipped = 0
    for path in sorted(DOCS_DIR.iterdir()):
        if digest_re.match(path.name):
            result = patch_digest(path, args.dry_run)
        elif sources_re.match(path.name):
            result = patch_sources(path, args.dry_run)
        else:
            continue
        if result is None:
            skipped += 1
        else:
            patched += 1
            print(f"  [PATCH] {path.name}: {result}")

    verb = "would patch" if args.dry_run else "patched"
    print(f"\n{verb} {patched} file(s); {skipped} already current.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
