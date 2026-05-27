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

### Pipeline steps

1. **Fetch** — NewsAPI, The Guardian, NYT (via API keys) + Fox News, BBC, WSJ (public RSS via feedparser)
2. **Remove exact duplicates** — `remove_exact_duplicates()` discards only articles where title, source name, AND URL are all identical; cross-outlet near-duplicates are intentionally kept (see clustering architecture below)
3. **Cluster** — Geographic gate + three-signal Union-Find grouping + post-clustering coherence validation (see clustering architecture below); multi-source stories are clustered together and sent to Claude as a single unit
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

## News Sources and Clustering Architecture

Sources are fetched per run from three API sources and twelve RSS feeds. Target input before clustering: ~120–160 raw articles.

**API sources (require keys):**
1. **NewsAPI** — categories: world, business, technology, health, science, sports, politics, national — 5 articles each (40 total). *politics* and *national* added to close the domestic-coverage gap present in the world/business-only set.
2. **The Guardian** — sections: world, business, technology, science, sport — 5 articles each (25 total).
3. **New York Times** — sections: world, business, technology, health, science, sports, politics, climate — 5 articles each (40 total). *politics* adds authoritative domestic political coverage; *climate* provides dedicated environmental reporting that rarely surfaces from the general sections.

**RSS feeds (no API key required, parsed via feedparser, up to 8 articles each):**

| Feed | URL | Rationale |
|---|---|---|
| Fox News | `feeds.foxnews.com/foxnews/latest` | Right-leaning general coverage; ensures multi-outlet synthesis spans perspectives |
| Fox News World | `feeds.foxnews.com/foxnews/world` | International stories from a right-leaning source |
| Fox News Politics | `feeds.foxnews.com/foxnews/politics` | Political balance — right-leaning domestic politics |
| WSJ Markets | `feeds.content.dowjones.io/public/rss/mw_realtimeheadlines` | Financial and markets coverage |
| BBC News | `feeds.bbci.co.uk/news/rss.xml` | International perspective; widely considered editorially neutral |
| BBC World | `feeds.bbci.co.uk/news/world/rss.xml` | International stories from BBC |
| Reuters Politics | `feeds.reuters.com/reuters/politicsNews` | Wire-service political coverage; highly authoritative, low framing |
| Reuters Domestic | `feeds.reuters.com/reuters/domesticNews` | Wire-service domestic US coverage; fills the gap left by API sources |
| NPR News | `feeds.npr.org/1001/rss.xml` | Public media; domestic coverage with different editorial priorities than commercial outlets |
| PBS NewsHour | `www.pbs.org/newshour/feeds/rss/headlines` | Public media; adds domestic depth and often covers stories commercial outlets skip |
| Guardian Environment | `www.theguardian.com/environment/rss` | Dedicated climate and environment feed; improves NATURAL EVENTS and SCIENCE & HEALTH coverage |
| SCOTUSblog | `www.scotusblog.com/feed/` | Supreme Court and federal judiciary; sole specialist legal source, covers DOMESTIC POLICY stories general feeds miss |

### Exact duplicate removal

`remove_exact_duplicates()` discards articles where **all three** of title, source name, and URL are identical — true duplicates such as the same article fetched twice from the same feed. It deliberately preserves cross-outlet near-duplicates (e.g., the NYT and Fox News articles about the same event). Discarding those before clustering would strip the synthesis inputs; they must flow through so Claude receives all perspectives on the same story.

### Clustering algorithm

After exact-duplicate removal, `cluster_articles()` groups articles by story using Union-Find. Grouping is transitive: if A merges with B and B merges with C, all three form one cluster. Before any pairwise merge is attempted, two blocking guards run in sequence (see below). If neither guard blocks, the effective similarity threshold is determined, and any one of three signals is sufficient to trigger a merge:

**Signal 1 — Named-entity boost** (evaluated first)
If two headlines share ≥ 2 capitalised non-initial words — the heuristic for proper nouns such as people, countries, and organisations — the articles auto-merge regardless of TF-IDF score. `_extract_named_entities(title)` extracts these by skipping the first word (always capitalised in a headline) and collecting every subsequent capitalised word. This catches pairs like "Iran strikes Israel in overnight attack" ↔ "US warns Iran over Israeli strikes" — low lexical overlap but clearly the same event.

**Signal 2 — Headline TF-IDF similarity** (threshold 0.22 or 0.45)
`_tfidf_vectors()` builds a smoothed IDF over the full corpus of article headlines, then computes per-article TF-IDF vectors. `_cosine()` measures cosine similarity between pairs. If the score ≥ effective threshold the articles merge. High-frequency terms are removed by `_CLUSTER_STOPWORDS` before vectorisation.

**Signal 3 — Description TF-IDF similarity** (same effective threshold, fallback only)
If both signals above fail but both articles have non-empty description fields, the same TF-IDF + cosine method is applied to the first 300 characters of each description. This catches cases where headlines are phrased very differently but descriptions of the same event share substantive vocabulary.

### Geographic coherence gate

Before any merge signal is evaluated, a geographic coherence check runs as a hard block. If both articles have detectable geographic entities and those entities are entirely disjoint, the articles describe events in different places and the merge is blocked unconditionally — TF-IDF and named-entity scores are not consulted.

`_extract_geo_entities(text)` searches the combined title + description text (lowercased) for:
- **Single-word geographic names** via set intersection with `_GEO_SINGLE` — a `frozenset` of ~200 country names, all 50 US states, and ~100 major world cities. Only unambiguous proper nouns are included to prevent false matches on common words.
- **Multi-word geographic phrases** via substring match against `_GEO_PHRASES` — a `frozenset` of ~35 phrases including "united states", "south korea", "hong kong", "saudi arabia", "new york", and all two-word US state names.

**Fallback behaviour:** if either article returns an empty set from `_extract_geo_entities` — no geographic signal detected — the gate is bypassed and the three merge signals run as normal. This preserves correct clustering for geography-free stories (technology, economics, domestic policy) that contain no country or city name.

**Example:** "China coal mine explosion kills 20 workers" → `{"china"}`. "Pakistan suicide bombing kills 15" → `{"pakistan"}`. Both articles have geographic signals; the signals are disjoint; merge is blocked before TF-IDF is consulted.

### Domestic political threshold

Domestic US political news poses a specific clustering problem: words like "senate", "congress", "administration", "bill", and "vote" appear in virtually every political story regardless of whether those stories are related. At the default threshold of 0.22, TF-IDF similarity alone can merge a Texas primary runoff result with a DOJ document-deletion story simply because both use standard political vocabulary. The geographic gate does not help because both articles are domestic — they share geographic entities rather than conflicting on them.

`_is_domestic_political(text)` identifies domestic political articles using two conditions:
1. Text contains at least one US state name (from `_GEO_US_STATES`) or the word "washington" (covers Washington DC).
2. Text contains no unambiguous foreign country name (from `_GEO_FOREIGN_COUNTRIES` or `_GEO_FOREIGN_PHRASES`). "Georgia" is intentionally excluded from the foreign-country set because it is also a US state; its presence cannot determine whether an article is domestic or foreign.

When both articles in a pair are classified as domestic political, the effective threshold for Signals 2 and 3 is raised from **0.22 to 0.45** (`DOMESTIC_POLITICAL_THRESHOLD`). Signal 1 (named-entity boost) is unaffected — if two domestic political articles share two named entities they are almost certainly about the same event and should merge. The elevated threshold blocks merges driven by shared generic political vocabulary while preserving merges grounded in shared specific names.

### Event-type coherence check

Even with the elevated domestic-political threshold, different event types within domestic politics share enough specific vocabulary to false-cluster. "DOJ January 6 records deletion" contains legal vocabulary; "Texas Senate primary runoff" contains election vocabulary. These are structurally different events that would never be synthesised into a coherent single story.

`_classify_event_type(text)` searches article text for substrings from `_EVENT_TYPE_KEYWORDS`, a dict of four event types:

| Type | Representative keywords |
|---|---|
| `election` | election, primary, runoff, ballot, candidate, senate race, voters, early voting |
| `legal` | doj, indictment, january 6, charges filed, grand jury, department of justice, attorney general |
| `legislative` | bill, legislation, amendment, passed the senate, passed the house, signed into law |
| `executive` | executive order, white house, president signed, cabinet, oval office |

The function returns `(event_type, confidence)` where confidence is the count of keyword matches for the winning type. Classification requires **≥ 2 keyword matches** and a **clear lead over the second type** to be considered high-confidence. If neither condition holds, the function returns `("ambiguous", 0)`.

In `cluster_articles()`, a merge is blocked before any similarity signal runs when **both** articles have a non-ambiguous, high-confidence classification **and those classifications differ**. An ambiguous classification on either article falls through to the standard TF-IDF logic, so articles that use mixed vocabulary are not penalised.

### Post-clustering coherence validation

After `cluster_articles()` returns, `_split_incoherent_clusters()` inspects every multi-article cluster as a backstop against false positives. It applies two independent tests; failing either causes the cluster to be split into singletons.

**Test 1 — Named-entity coherence** (existing):
1. Run `_extract_named_entities(title)` for every article in the cluster.
2. If all entity sets are empty, skip this test — we cannot determine incoherence from entities alone.
3. Otherwise, count how many articles each entity appears in.
4. If at least one entity appears in ≥ 2 articles, the cluster passes Test 1.
5. If no entity is shared by any two articles → split and log: `[SPLIT] No shared entity → N singletons: <title excerpts>`.

**Test 2 — Event-type diversity** (new):
1. Run `_classify_event_type()` on title + description for every article in the cluster.
2. Collect all non-ambiguous event-type labels assigned.
3. If ≤ 2 distinct types are present, the cluster passes Test 2.
4. If > 2 distinct types are present (e.g., election + legal + legislative in one cluster), the cluster is a false-positive transitivity merge → split and log: `[SPLIT] Event-type span (election, legal, legislative) → N singletons: <title excerpts>`.

### Interaction between layers

Four guards protect against false-positive merges, operating at two stages:

**During pairwise merge decisions (prevention):**
- The **geographic gate** blocks merges where both articles have conflicting geographic signals. Catches cross-country clustering (China mine + Pakistan bombing) but is blind to same-country stories.
- The **event-type conflict check** blocks merges where both articles have high-confidence classifications into different event types. Catches cross-type domestic political clustering (Texas primary + DOJ records) but only fires when both sides have ≥ 2 keyword matches.
- The **domestic political threshold** raises the TF-IDF bar from 0.22 to 0.45 for domestic-political pairs, filtering merges driven by shared generic political vocabulary rather than shared specific content.

**After all clusters are formed (post-hoc backstop):**
- The **named-entity coherence check** splits clusters where no named entity is shared by two or more articles. Catches cases where prevention guards missed a bad merge.
- The **event-type diversity check** splits clusters spanning more than two distinct event types — a pattern that indicates false transitivity merges (A~B and B~C where A and C are unrelated).

No single guard is sufficient. Each addresses a failure mode the others do not cover.

### Multi-source synthesis

When a cluster reaches Claude with multiple articles, the editorial system prompt instructs it to synthesise them as follows:

- **High-confidence facts**: claims that appear consistently across all sources are reported as settled facts.
- **Uncertain facts**: where sources diverge on a matter of fact (not just framing), the synthesis must reflect that uncertainty — "accounts differ on…" or "figures vary by source". A contested fact must never be presented as settled.
- **Framing divergence**: where sources disagree only in tone, emphasis, or loaded language, the framing is stripped and the neutral underlying fact is reported. Framing differences are invisible to the reader.
- **Goal**: produce a synthesis no single outlet would write — more complete because it draws on all sources, more neutral because the synthetic process eliminates outlet-specific framing.

Claude assigns each output article a `cluster_id` matching the `[CLUSTER N]` number in its input, which the pipeline uses for source attribution after the response is parsed.

### Source tracking

`attach_sources()` maps each Claude output article's `cluster_id` back to the original raw articles in the corresponding cluster. Each output article gains a `sources` array:

```python
[{"source": "Outlet Name", "url": "...", "original_headline": "..."}]
```

In the digest HTML, this array is rendered as a collapsible sources toggle beneath each article — a `<button aria-expanded="false">` that reveals a `<ul>` of outlet links on click, controlled by a CSS adjacent-sibling selector (`[aria-expanded="true"] + .sources-list`). The companion sources page (`YYYY-MM-DD-am-sources.html`) presents the full attribution list for every article in the digest, with outlet names linked to original articles and original headlines quoted.

### Known limitations and future improvements

- **Clustering is lexical, not semantic.** TF-IDF operates on exact token overlap. Two articles covering the same event using synonyms or entirely different vocabulary may fail to cluster even though they describe the same story. A future improvement would replace or supplement TF-IDF with embedding-based similarity (e.g., sentence-transformers or the Anthropic embeddings API) for semantic matching that is robust to paraphrase.
- **The geographic entity list is finite.** `_GEO_SINGLE` and `_GEO_PHRASES` cover the most newsworthy countries, states, and cities but will miss obscure place names, districts, regions, and newly prominent locations. A miss causes the geographic gate to be bypassed — falling back to TF-IDF — which is the correct graceful degradation. It does not cause an incorrect split.
- **Cluster size is unbounded.** A very large cluster (8+ articles) may indicate over-merging via Union-Find transitivity: A merges with B, B merges with C, producing a three-way cluster where A and C are unrelated. The coherence validation catches some of these cases, but a size threshold or additional validation may be warranted if large clusters become common.

### Logging

Step 3 of the pipeline log reports cluster distribution after both clustering and coherence validation complete:

```
[3/10] Clustering articles by story...
  [SPLIT] No shared entity → 2 singletons: China mine explosion kills 20 | Pakist...
  [SPLIT] Event-type span (election, legal) → 2 singletons: Texas runoff results | DOJ...
  87 articles → 54 clusters (12 multi-source, 42 single-source, 2 incoherent clusters split)
    Cluster  3 [4 sources]: The New York Times · Fox News · BBC News · The Guardian
    Cluster  7 [2 sources]: The Guardian · WSJ Markets
```

`[SPLIT]` lines are written to stderr so they appear in the GitHub Actions error stream, making false-positive merges easy to spot without noise in the normal stdout output. Each split line includes the reason (no shared entity, or the specific event-type labels that caused the span failure), the number of singletons produced, and the first 45 characters of each article title in the dissolved cluster. Multi-source cluster lines (printed to stdout) show contributing outlets in order of appearance so synthesis inputs can be verified at a glance.

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

## Hybrid Pipeline

`pipeline_hybrid.py` is an experimental two-pass editorial architecture that runs **alongside** the main pipeline without modifying any of its outputs. It exists as a comparison tool — a way to evaluate a top-down story-selection approach against the bottom-up clustering approach in `pipeline.py`.

### Architecture

The hybrid pipeline replaces clustering with two sequential Claude calls:

**Pass 1 — Story identification** (single lightweight call)
All article titles and descriptions are sent to Claude with a compact prompt (`PASS1_SYSTEM_PROMPT`). Claude surveys the full article pool and returns a JSON list of 10–16 newsworthy stories, each with:
- `headline` — brief factual label for the story
- `description` — one sentence on what happened and why it matters
- `key_terms` — 5–8 proper nouns and specific phrases that anchor the story
- `relevance_score` — 1–10 importance rating

Max tokens for Pass 1: **1 500**. This pass answers "what matters today?" — it does not write editorial copy.

**Pass 2 — Per-story synthesis** (one Claude call per story)
For each story identified in Pass 1, every raw article is scored against the story's `key_terms` and headline tokens. Scoring: key-term substring match = 2.0 points; headline token overlap = 0.3 × overlap count. Articles with score > 0 are eligible; articles with score == 0 (no keyword overlap) are excluded. The top N highest-scoring articles are sent to Claude with the standard `EDITORIAL_SYSTEM_PROMPT` for full neutral synthesis:
- Top 4 stories (by relevance_score rank): up to 8 source articles
- Remaining stories: up to 6 source articles

Each story is one independent Claude call. A failure or editorial exclusion on any story is non-blocking — the pipeline continues. Max tokens per story: **2 000**.

**Source attribution** is tracked per story: the scored articles sent to Claude in Pass 2 become the `sources` array on the synthesised article.

### Comparison with main pipeline

| Dimension | Main pipeline | Hybrid pipeline |
|---|---|---|
| Story selection | Bottom-up: TF-IDF clustering groups articles, Claude picks 10–16 | Top-down: Claude surveys all articles, picks stories first |
| Synthesis inputs | Articles that clustered together | Articles scored by keyword relevance to the identified story |
| Claude calls | 1 (all clusters at once) | 1 (Pass 1) + N (one per story, typically 10–16) |
| Audio | Yes (ElevenLabs) | No |
| Podcast RSS | Yes | No |
| Metadata JSON | Yes | No |
| Archive update | Yes (`archive.json`) | No |
| Output location | `docs/` | `docs/hybrid/` |

### Outputs

All output goes to `docs/hybrid/`. Nothing outside `docs/hybrid/` is modified.

```
docs/hybrid/
├── YYYY-MM-DD-am.html           # Digest page (same visual design as main)
├── YYYY-MM-DD-am-sources.html   # Source attribution page
└── index.html                   # Listing of all hybrid digests
```

The hybrid digest and sources pages use the same visual design as the main pipeline (same CSS, masthead, typography). Differences:
- A small "Hybrid" badge appears in the masthead dateline
- No audio player
- The archive sidebar dynamically loads `../archive.json` (main pipeline's archive) for navigation context
- The footer links to both the main site and the hybrid archive index

### Triggering

The hybrid pipeline **never runs on a schedule**. It is triggered only via `workflow_dispatch` in GitHub Actions:

```
Actions → Balm Hybrid Digest → Run workflow
```

The workflow (`balm_hybrid.yml`) accepts the same `run` (auto/am/pm) and `date` inputs as the main workflow. It does not require `ELEVEN_LABS_API_KEY`.

For local development:
```bash
export ANTHROPIC_API_KEY=...
export NEWS_API_KEY=...   # optional but recommended
export GUARDIAN_API_KEY=...
export NYT_API_KEY=...
python pipeline_hybrid.py --run am --date 2025-01-15
```

Output appears in `docs/hybrid/`. It does not affect the main site.

### Evaluating output

The hybrid pipeline is useful for comparing:
- **Story selection**: does top-down identification catch stories that bottom-up clustering misses, or vice versa?
- **Source diversity**: does keyword scoring bring in more relevant cross-outlet coverage than TF-IDF clustering?
- **Editorial quality**: does per-story synthesis (knowing the story before reading sources) produce better or worse briefs than cluster-first synthesis?

Neither approach is definitively better. The hybrid exists to make differences visible.

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
- All main pipeline output goes to `docs/`. Nothing outside `docs/` is modified during a main pipeline run except `docs/index.html` and `docs/podcast.xml`.
- The Jinja2 templates in `templates/` are the source of truth for main pipeline HTML. Do not edit generated files in `docs/` directly.
- The hybrid pipeline writes only to `docs/hybrid/`. Its HTML templates are inline Jinja2 strings inside `pipeline_hybrid.py` — not in `templates/`.
- The editorial system prompt in `pipeline.py` must match the version in this CLAUDE.md exactly. If you update one, update both. `pipeline_hybrid.py` imports `EDITORIAL_SYSTEM_PROMPT` directly from `pipeline.py` and therefore always uses the same prompt.
- The Claude model used is `claude-sonnet-4-6`. Update this constant in `pipeline.py` when upgrading; `pipeline_hybrid.py` has its own `CLAUDE_MODEL` constant that must also be updated.
