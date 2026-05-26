#!/usr/bin/env python3
"""One-time migration: replace <p class="article-source"> with sources-toggle in stale docs/."""
from pathlib import Path
from bs4 import BeautifulSoup
import re

SOURCES_TOGGLE_CSS = """
    /* Sources inline toggle */
    .sources-toggle { margin-top: 0.5rem; }
    .sources-toggle-btn {
      background: none; border: none; padding: 0; cursor: pointer;
      font-family: 'Source Serif 4', serif; font-size: 0.75rem; color: #9a9288;
      display: flex; align-items: center; gap: 0.3rem; letter-spacing: 0.04em;
    }
    .sources-toggle-btn:hover { color: #6a6058; }
    .sources-caret { display: inline-block; transition: transform 0.2s ease; }
    .sources-toggle-btn[aria-expanded="true"] .sources-caret { transform: rotate(90deg); }
    .sources-list {
      list-style: none; padding: 0; margin: 0;
      max-height: 0; overflow: hidden; transition: max-height 0.25s ease;
    }
    .sources-toggle-btn[aria-expanded="true"] + .sources-list { max-height: 20rem; }
    .sources-list li { padding: 0.2rem 0 0.2rem 0.8rem; }
    .sources-list a {
      font-size: 0.75rem; color: #9a9288;
      text-decoration: none; border-bottom: 1px solid #c8c0b4;
    }
    .sources-list a:hover { color: #6b82a8; }
"""

SOURCES_TOGGLE_JS = """
  // Sources toggle — delegated listener
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('.sources-toggle-btn');
    if (!btn) return;
    var expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!expanded));
  });
"""


def build_toggle(links):
    items = ""
    for a in links:
        href = a.get("href", "#")
        text = a.get_text(strip=True)
        items += f'<li><a href="{href}" target="_blank" rel="noopener">{text}</a></li>'
    return (
        '<div class="sources-toggle">'
        '<button class="sources-toggle-btn" aria-expanded="false">'
        '<span class="sources-caret">›</span> Sources'
        '</button>'
        '<ul class="sources-list">' + items + '</ul>'
        '</div>'
    )


docs = Path(__file__).parent / "docs"
patched = []

for html_path in sorted(docs.glob("*.html")):
    text = html_path.read_text(encoding="utf-8")
    if 'class="article-source"' not in text:
        continue

    soup = BeautifulSoup(text, "html.parser")
    changed = False

    for p in soup.find_all("p", class_="article-source"):
        links = p.find_all("a")
        if not links:
            p.decompose()
            changed = True
            continue
        toggle_tag = BeautifulSoup(build_toggle(links), "html.parser").div
        p.replace_with(toggle_tag)
        changed = True

    if not changed:
        continue

    # Strip old .article-source CSS, append new sources-toggle CSS
    style = soup.find("style")
    if style:
        css = style.string or ""
        css = re.sub(r"\n?\s*\.article-source\s*\{[^}]*\}", "", css)
        style.clear()
        style.append(css + SOURCES_TOGGLE_CSS)

    # Append sources-toggle JS to existing <script> if not already present
    for script in soup.find_all("script"):
        if (script.string
                and "toggleSummary" in script.string
                and "sources-toggle-btn" not in script.string):
            script.string = script.string + SOURCES_TOGGLE_JS
            break

    html_path.write_text(str(soup), encoding="utf-8")
    patched.append(html_path.name)

for name in patched:
    print(f"Patched: {name}")
print(f"\nTotal: {len(patched)} files patched")
