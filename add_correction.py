#!/usr/bin/env python3
"""
add_correction.py — append a visible correction to an already-published story.

Balm publishes corrections rather than editing silently. An unannounced edit to
a story someone may have read is worse than the original error: it removes the
reader's ability to know the record changed.

What this does, for one story on one published page:
  1. optionally removes an incorrect sentence from the brief and full summaries
  2. inserts a visible .correction note into that story
  3. injects the .correction CSS into the page if it isn't already there
  4. records the correction in the edition's metadata JSON

Idempotent: re-running with the same --id and --note changes nothing.

Usage:
  python add_correction.py --file docs/2026-08-26-am.html \
      --id story_7 \
      --remove "She is survived by her husband Carl Dean." \
      --note "An earlier version said ..." \
      [--date 2026-08-27] [--dry-run]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date as _date
from pathlib import Path

CSS = """
    /* Corrections — appended to a story after publication. Deliberately visible
       rather than silent: an unannounced edit is worse than the original error. */
    .correction {
      margin-top: 0.9rem;
      padding: 0.7rem 0.9rem;
      border-left: 3px solid var(--accent);
      background: #ede9e2;
      font-family: 'Source Serif 4', serif;
      font-size: 0.88rem;
      line-height: 1.65;
      color: var(--secondary);
    }

    .correction-label {
      font-family: 'Playfair Display', serif;
      font-size: 0.68rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--accent);
      margin-right: 0.5rem;
    }
"""
CSS_MARKER = ".correction-label {"


def ensure_css(page: str) -> str:
    if CSS_MARKER in page:
        return page
    anchor = "    /* Sources inline toggle */"
    if anchor in page:
        return page.replace(anchor, CSS.strip("\n") + "\n\n" + anchor, 1)
    return page.replace("  </style>", CSS + "  </style>", 1)


def find_article(page: str, story_id: str) -> tuple[int, int]:
    """Return the (start, end) span of the article div with this id."""
    m = re.search(rf'<div class="article[^"]*"[^>]*id="{re.escape(story_id)}"', page)
    if not m:
        sys.exit(f"[ERROR] story id {story_id!r} not found")
    start = m.start()
    nxt = re.search(r'<div class="article[^"]*"[^>]*id="story_', page[m.end():])
    end = m.end() + nxt.start() if nxt else page.find("</section>", start)
    return start, (end if end > start else len(page))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True)
    ap.add_argument("--id", required=True, help="story_id anchor, e.g. story_7")
    ap.add_argument("--note", required=True, help="the correction text readers see")
    ap.add_argument("--remove", default=None,
                    help="exact sentence to strike from the summaries")
    ap.add_argument("--date", default=None, help="correction date (default: today)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    path = Path(a.file)
    page = path.read_text(encoding="utf-8")
    when = a.date or _date.today().isoformat()

    if a.note in page:
        print("[SKIP] this correction is already on the page")
        return 0

    start, end = find_article(page, a.id)
    article = page[start:end]

    removed = 0
    if a.remove:
        for variant in (a.remove, html.escape(a.remove), a.remove.replace("'", "&#39;")):
            if variant in article:
                # Drop the sentence and any doubled space it leaves behind.
                article = article.replace(" " + variant, "").replace(variant, "")
                removed += 1
                break
        if not removed:
            sys.exit(f"[ERROR] sentence to remove not found in {a.id}")

    pretty = _date.fromisoformat(when).strftime("%-d %B %Y")
    note = (f'\n              <div class="correction">'
            f'<span class="correction-label">Correction</span>'
            f'{html.escape(pretty)} — {html.escape(a.note)}</div>')

    # Place the note after the summary block, before the sources toggle.
    anchor = '<div class="sources-toggle">'
    if anchor in article:
        article = article.replace(anchor, note.strip() + "\n              " + anchor, 1)
    else:
        article = article.rstrip() + note + "\n"

    page = page[:start] + article + page[end:]
    page = ensure_css(page)

    meta_path = path.with_suffix(".json")
    meta = None
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.setdefault("corrections", []).append(
            {"story_id": a.id, "date": when, "note": a.note,
             "removed": a.remove or None})

    if a.dry_run:
        print(f"[DRY RUN] would correct {a.id} in {path.name}"
              f"{' (1 sentence removed)' if removed else ''}")
        return 0

    path.write_text(page, encoding="utf-8")
    if meta is not None:
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] corrected {a.id} in {path.name}"
          f"{' — 1 sentence removed' if removed else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
