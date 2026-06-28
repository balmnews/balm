#!/usr/bin/env python3
"""
patch_old_digests.py — backfill the current template design changes to existing
digest HTML files without regenerating their editorial content.

Changes applied:
  1. Remove <nav class="sidebar"> element
  2. Remove mobile-archive-toggle and mobile-archive-panel elements
  3. Remove archive.json fetch JS and toggleMobileArchive() function
  4. Fix .page-wrapper CSS to single-column layout (max-width: 860px)
  5. Wrap masthead SVG wordmark in <a href='https://balm.news' class='masthead-link'>
  6. Insert archive-link-section div before the footer
  7. Add footer-kofi / footer-contact classes to the relevant footer links

Usage:
  python patch_old_digests.py

Adjust CUTOFF_DATE to change which files are processed. Files dated on or after
CUTOFF_DATE are skipped. Set to a future date to process all existing digests.
"""

import re
import sys
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

DOCS_DIR = Path(__file__).parent / "docs"

# Files from this date onwards are skipped.
# Change to date(2099, 1, 1) to patch every existing digest.
CUTOFF_DATE = date(2099, 1, 1)


# ---------------------------------------------------------------------------
# CSS patch: replace the old flex-based .page-wrapper with single-column
# ---------------------------------------------------------------------------

_PAGE_WRAPPER_PATTERN = re.compile(
    r'(\.page-wrapper\s*\{)[^}]+(})',
    re.DOTALL,
)
_PAGE_WRAPPER_REPLACEMENT = (
    r'\1'
    '\n      max-width: 860px;\n      margin: 0 auto;\n      padding: 0 1.25rem;\n    '
    r'\2'
)


def _patch_css(style_text: str) -> str:
    # count=1: only replace the top-level .page-wrapper block, not the media query override
    return _PAGE_WRAPPER_PATTERN.sub(_PAGE_WRAPPER_REPLACEMENT, style_text, count=1)


# ---------------------------------------------------------------------------
# JS patch: remove toggleMobileArchive() and the archive.json fetch IIFE
# ---------------------------------------------------------------------------

_TOGGLE_MOBILE_RE = re.compile(
    r'\n\s*function toggleMobileArchive\(\)\s*\{[^}]*\}',
    re.DOTALL,
)

# Matches optional comment lines then the IIFE starting with (function()
_ARCHIVE_IIFE_RE = re.compile(
    r'\n[ \t]*(?:// [^\n]*\n[ \t]*)*\(function\(\)\s*\{.*?\}\)\(\);',
    re.DOTALL,
)


def _patch_script(src: str) -> str:
    src = _TOGGLE_MOBILE_RE.sub('', src)
    src = _ARCHIVE_IIFE_RE.sub('', src)
    return src


# ---------------------------------------------------------------------------
# Per-file patch
# ---------------------------------------------------------------------------

def patch_file(path: Path) -> str:
    """
    Apply all patches to one digest file.
    Returns 'patched' if anything changed, 'skipped' if already up to date.
    """
    original_html = path.read_text(encoding='utf-8')
    soup = BeautifulSoup(original_html, 'html.parser')
    changed = False

    # 1 — Remove sidebar <nav>
    sidebar = soup.find('nav', class_='sidebar')
    if sidebar:
        sidebar.decompose()
        changed = True

    # 2 — Remove mobile archive toggle and panel
    for cls in ('mobile-archive-toggle', 'mobile-archive-panel'):
        for el in soup.find_all(class_=cls):
            el.decompose()
            changed = True

    # 3 — Remove archive.json JS / toggleMobileArchive from script tags
    for script in soup.find_all('script'):
        src = script.string
        if not src:
            continue
        if 'archive.json' not in src and 'toggleMobileArchive' not in src:
            continue
        patched_src = _patch_script(src)
        if patched_src != src:
            script.string = patched_src
            changed = True

    # 4 — Patch .page-wrapper CSS
    for style_tag in soup.find_all('style'):
        raw = style_tag.string or ''
        if not raw:
            continue
        patched = _patch_css(raw)
        if patched != raw:
            style_tag.string = patched
            changed = True

    # 5 — Wrap masthead SVG in home link (if not already wrapped)
    masthead_logo = soup.find('div', class_='masthead-logo')
    if masthead_logo:
        svg = masthead_logo.find('svg')
        if svg and svg.parent.name != 'a':
            link = soup.new_tag('a', href='https://balm.news')
            link['class'] = ['masthead-link']
            svg.wrap(link)
            changed = True

    # 6 — Insert archive-link-section before footer (if not already present)
    if not soup.find('div', class_='archive-link-section'):
        footer = soup.find('footer', class_='footer')
        if footer:
            archive_html = BeautifulSoup(
                '\n    <div class="archive-link-section">'
                '<a href="/archive.html" class="archive-link">Browse past editions &#8594;</a>'
                '</div>\n    ',
                'html.parser',
            )
            footer.insert_before(archive_html)
            changed = True

    # 7 — Add footer-kofi / footer-contact classes
    footer = soup.find('footer', class_='footer')
    if footer:
        for a_tag in footer.find_all('a'):
            href = a_tag.get('href', '')
            classes = a_tag.get('class') or []

            if 'ko-fi.com' in href and 'footer-kofi' not in classes:
                a_tag['class'] = classes + ['footer-kofi']
                changed = True

            elif href in ('/contact.html', 'contact.html') and 'footer-contact' not in classes:
                a_tag['class'] = classes + ['footer-contact']
                changed = True

    if not changed:
        return 'skipped'

    path.write_text(str(soup), encoding='utf-8')
    return 'patched'


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    candidates: list[tuple[date, Path]] = []

    for f in sorted(DOCS_DIR.glob('????-??-??-??.html')):
        parts = f.stem.split('-')
        if len(parts) != 4 or parts[3] not in ('am', 'pm'):
            continue
        try:
            file_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue
        if file_date >= CUTOFF_DATE:
            continue
        candidates.append((file_date, f))

    if not candidates:
        print(f"No digest files found before {CUTOFF_DATE}. Nothing to patch.")
        return

    print(f"Patching {len(candidates)} files dated before {CUTOFF_DATE} ...\n")

    patched = skipped = errors = 0
    for _, path in candidates:
        label = path.stem
        try:
            result = patch_file(path)
            status = 'patched' if result == 'patched' else 'skipped (already current)'
            print(f"  {label:<22}  {status}")
            if result == 'patched':
                patched += 1
            else:
                skipped += 1
        except Exception as exc:
            print(f"  {label:<22}  ERROR: {exc}", file=sys.stderr)
            errors += 1

    print(f"\n{'─' * 40}")
    print(f"  Patched : {patched}")
    print(f"  Skipped : {skipped}")
    print(f"  Errors  : {errors}")


if __name__ == '__main__':
    main()
