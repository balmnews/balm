# Balm — Developer & Agent Reference

> "The topical anti-inflammatory"

This document is the authoritative reference for any developer or AI agent maintaining or extending Balm. It is written to be self-sufficient: no external context should be required.

---

## Mission and Editorial Philosophy

The aggravation people feel from reading news is not accidental — it is the business model of ad-supported media. Outrage, fear, and tribal signaling drive clicks. Balm delivers the same factual information without the emotional manipulation.

**Balm's editorial character:** Calm, authoritative, considered. Never urgent, never alarming, never partisan.

Every editorial decision flows from a single question: *would a well-informed adult who doesn't follow news daily need to know this happened?* If yes, include it. If it only matters to people already following the story closely, or has no relevance beyond the immediate event, exclude it.

Balm is not a neutral aggregator — it is an editorial product with a point of view about *how* information should be delivered, not *which* information to deliver. The selection criteria are relevance and importance. The rewriting criteria are calm and accuracy.

---

## Architecture

Balm is a **fully static site**. There is no backend, no server, and no database. Everything is pre-generated HTML files hosted on GitHub Pages from the `/docs` folder.

### Pipeline runs

A Python script (`pipeline.py`) runs twice daily via GitHub Actions:

- **AM run**: 4am PT / 7am ET → outputs `YYYY-MM-DD-am.html`, `YYYY-MM-DD-am-sources.html`, `YYYY-MM-DD-am.json`, `YYYY-MM-DD-am.mp3`
- **PM run**: 2pm PT / 5pm ET → outputs `YYYY-MM-DD-pm.html`, `YYYY-MM-DD-pm-sources.html`, `YYYY-MM-DD-pm.json`, `YYYY-MM-DD-pm.mp3`

After each run, `index.html` is regenerated to point to the latest digest and refresh the archive navigation.

### Pipeline steps (V2)

1. **Fetch** — NewsAPI, The Guardian, NYT (via API keys) + Fox News, BBC, WSJ (public RSS via feedparser)
2. **Deduplicate** — Jaccard similarity on normalized title tokens, threshold ≥ 0.5
3. **Cluster** — TF-IDF cosine similarity on headline + description, Union-Find grouping, threshold ≥ 0.35; multi-source stories are clustered together and sent to Claude as a single unit
4. **Synthesize** — Claude processes each cluster, synthesizes multi-source stories into a single neutral account, returns 10–16 articles with `cluster_id` for source attribution
5. **Attach sources** — pipeline maps `cluster_id` back to input articles; each output article gains a `sources` array with outlet name, URL, and original headline
6. **Number** — sequential `ref` numbers assigned (1, 2, 3…) in category display order
7. **S&P 500** — non-blocking fetch from Yahoo Finance
8. **Metadata** — JSON saved alongside digest
9. **Audio** — ElevenLabs TTS from concatenated `full_summary` fields
10. **Render** — digest HTML, sources page HTML, index.html all written from Jinja2 templates
11. **Podcast RSS** — `podcast.xml` updated

### Sources page

Each digest has a companion sources page (`YYYY-MM-DD-am-sources.html`). It lists every story by reference number with the Balm headline, contributing outlets, and original article headlines linked to source URLs. The digest footer links to it; the sources page footer links back. The sources page uses the same visual template (masthead, typography, parchment) as the digest.

### File layout

```
/
├── pipeline.py              # Main pipeline script
├── templates/
│   ├── digest.html          # Jinja2 template for individual digest pages
│   ├── index.html           # Jinja2 template for the landing page
│   └── sources.html         # Jinja2 template for the companion sources page
├── docs/                    # All output goes here (GitHub Pages root)
│   ├── index.html           # Regenerated each run — always shows latest digest
│   ├── manifest.json        # PWA manifest
│   ├── service-worker.js    # PWA service worker
│   ├── podcast.xml          # Podcast RSS feed
│   ├── YYYY-MM-DD-am.html   # Individual digest files
│   ├── YYYY-MM-DD-pm.html
│   ├── YYYY-MM-DD-am-sources.html  # Companion sources pages
│   ├── YYYY-MM-DD-pm-sources.html
│   ├── YYYY-MM-DD-am.json   # Metadata for each digest
│   ├── YYYY-MM-DD-pm.json
│   ├── YYYY-MM-DD-am.mp3    # Audio digest
│   └── YYYY-MM-DD-pm.mp3
├── .github/
│   └── workflows/
│       └── balm.yml         # GitHub Actions CI/CD
├── manifest.json            # Source manifest (copied to docs/)
├── service-worker.js        # Source service worker (copied to docs/)
├── requirements.txt
├── README.md
└── CLAUDE.md                # This file
```

This structure supports years of accumulation. Digest files accumulate in `/docs` — flat, date-namespaced, no subdirectory restructuring needed.

---

## Editorial System Prompt

The following is the exact system prompt passed to Claude on every pipeline run. Do not modify it without considering the full downstream effects on content quality, legal compliance, and editorial consistency.

```
You are the editorial engine for Balm, a news digest with one guiding principle: inform without agitating.

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
Category display order: GEOPOLITICS, ECONOMY, DOMESTIC POLICY, SCIENCE & HEALTH, TECHNOLOGY, NATURAL EVENTS, SPORTS — then DIFFICULT NEWS last and collapsed.
```

---

## Environment Variables

All secrets are stored as GitHub repository Secrets and injected into the Actions workflow. For local development, export them in your shell or use a `.env` file (never commit it).

| Variable | Purpose | Where to obtain |
|---|---|---|
| `NEWS_API_KEY` | NewsAPI.org — fetches headlines across categories | newsapi.org → Register → API key |
| `GUARDIAN_API_KEY` | The Guardian Open Platform | open-platform.theguardian.com → Register |
| `NYT_API_KEY` | New York Times Developer API | developer.nytimes.com → Apps → + New App |
| `ANTHROPIC_API_KEY` | Claude API for editorial processing | console.anthropic.com → API Keys |
| `ELEVEN_LABS_API_KEY` | ElevenLabs text-to-speech for audio digest | elevenlabs.io → Profile → API Key |

---

## News Sources and Deduplication

Five source groups are fetched per run:

**API sources (require keys):**
1. **NewsAPI** — categories: world, business, technology, health, science, sports — 5 articles each (30 total)
2. **The Guardian** — sections: world, business, technology, science, sport — 5 articles each (25 total)
3. **New York Times** — sections: world, business, technology, health, science, sports — 5 articles each (30 total)

**RSS feeds (no API key required, parsed via feedparser):**
4. **Fox News** — `feeds.foxnews.com/foxnews/latest` and `/world` — up to 8 each
5. **BBC News** — `feeds.bbci.co.uk/news/rss.xml` and `/world/rss.xml` — up to 8 each
6. **WSJ Markets** — `feeds.content.dowjones.io/public/rss/mw_realtimeheadlines` — up to 8

Articles are deduplicated by normalized title similarity (Jaccard ≥ 0.5 triggers deduplication, keeping the first occurrence). After deduplication, articles are clustered by story: TF-IDF cosine similarity ≥ 0.35 groups articles covering the same event into a single cluster. Clusters — not individual articles — are sent to Claude for synthesis. Target: 35–55 unique articles → 25–40 clusters entering the Claude prompt.

### Multi-source synthesis

When a cluster contains articles from multiple outlets, Claude synthesizes them:
- Facts present in all sources are treated as high-confidence
- Facts present in only some sources are noted as uncertain ("accounts differ on…")
- Framing differences are stripped; only neutral factual content is retained
- The goal: a synthesis more complete and more neutral than any individual source

Each output article carries a `sources` array listing every contributing outlet, its URL, and the original headline. The sources page surfaces this attribution for readers who want to verify or dig deeper.

---

## Audio / Podcast

The `full_summary` field for all non-DIFFICULT articles is concatenated into a podcast script with intro and outro, then sent to ElevenLabs using voice **Rachel** (`voice_id: 21m00Tcm4TlvDq8ikWAM`).

- **AM intro**: "This is Balm for [Day, Month Date, Year]. Here are today's stories."
- **PM intro**: "This is Balm for [Day, Month Date, Year], afternoon edition. Here are today's stories."
- **AM outro**: "That's today's Balm digest. Return this evening for the day's second edition."
- **PM outro**: "That's the day's final Balm digest. We'll return tomorrow morning."

The podcast RSS feed (`docs/podcast.xml`) is updated after each run and is compatible with Apple Podcasts, Spotify, and standard podcast apps.

---

## Metadata Schema

Each run saves a `YYYY-MM-DD-am.json` / `YYYY-MM-DD-pm.json` alongside the HTML digest:

```json
{
  "date": "YYYY-MM-DD",
  "run": "am|pm",
  "timestamp": "ISO8601",
  "story_count": 0,
  "excluded_count": 0,
  "categories": [],
  "difficult_count": 0,
  "sp500_close": null
}
```

`sp500_close` is fetched from Yahoo Finance (unofficial endpoint) or Alpha Vantage free tier. Failure is non-blocking — the field stays null. This field exists to support a planned future feature: a timeline data overlay showing S&P 500 performance alongside editorial categories over time.

---

## Deployment

### First-time setup

1. Fork or create this repository on GitHub
2. Go to Settings → Pages → Source: Deploy from a branch → Branch: `main`, Folder: `/docs`
3. Add all API keys as repository secrets: Settings → Secrets and variables → Actions → New repository secret
4. Trigger a manual run: Actions → balm.yml → Run workflow
5. Site will be live at `https://[your-username].github.io/balm` (or your custom domain)

### Ongoing operation

GitHub Actions handles everything. The workflow runs at:
- `0 12 * * *` UTC → 4am PT / 7am ET (AM edition)
- `0 21 * * *` UTC → 2pm PT / 5pm ET (PM edition)

If a run fails, the site continues serving the last successful digest. No reader-facing breakage occurs.

### Local development

```bash
pip install -r requirements.txt
export NEWS_API_KEY=...
export GUARDIAN_API_KEY=...
export NYT_API_KEY=...
export ANTHROPIC_API_KEY=...
export ELEVEN_LABS_API_KEY=...
python pipeline.py --run am   # or --run pm
```

Output files are written to `docs/`. Open `docs/index.html` in a browser to preview.

---

## Visual Design System

### Color palette
- Background: `#f2ede4` (warm parchment)
- Body text: `#2a2520` (near-black ink)
- Secondary text: `#6a6058`
- Muted / metadata: `#9a9288`
- Rules and borders: `#c8c0b4`
- Accent (categories, links): `#6b82a8` (dusty slate blue)
- Difficult News label: `#5a4a3a`

### Typography
- **Caveat 700** — masthead logotype only
- **Source Serif 4 300 / 300 italic** — body text, tagline, metadata
- **Playfair Display 400 / 400 italic** — headlines, section headers

### Logo
The masthead "Balm" is rendered as inline SVG using Caveat 700, color `#6b82a8`, with a soft lotion-spread SVG filter: `feMorphology` dilate radius 1.5, `feGaussianBlur` stdDeviation 0.9, `feComposite` over. Capital B, lowercase alm.

---

## Future Features (planned, not built)

These features are intended but not yet implemented. Preserve the metadata schema fields that support them.

1. **Stock/economic data timeline overlay** — use `sp500_close` from metadata JSONs to render a historical chart alongside story categories. Requires a simple D3 or Chart.js frontend component and a data endpoint that aggregates all metadata JSONs.

2. **Marketing agent pipeline** — an automated agent that monitors digest publication and posts to social platforms (Threads, Bluesky, LinkedIn) with a Balm-voice excerpt and link.

3. **Native mobile app** — React Native or SwiftUI wrapper around the static site with push notifications for new digests. The PWA already provides a near-native experience; the native app adds notification capability.

4. **Multi-voice podcast** — a two-voice format using ElevenLabs where stories are read by alternating voices to improve listener engagement on longer runs.

5. **Subscriber newsletter via Beehiiv** — weekly digest format sent to email subscribers. Would reuse the `full_summary` content from the week's runs, formatted for email layout.

---

## Code Conventions

- Pipeline failures are non-fatal for individual steps. A failed audio generation should not prevent the HTML digest from publishing.
- All output goes to `docs/`. Nothing outside `docs/` is modified during a run except `docs/index.html` and `docs/podcast.xml`.
- The Jinja2 templates in `templates/` are the source of truth for HTML. Do not edit generated files in `docs/` directly.
- The editorial system prompt in `pipeline.py` must match the version in this CLAUDE.md exactly. If you update one, update both.
- The Claude model used is `claude-sonnet-4-6`. Update this constant in `pipeline.py` when upgrading.
