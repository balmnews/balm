# Balm — Developer & Agent Reference

> "Topical, anti-inflammatory news"

The tagline is a pun — *topical* meaning both current and applied to the surface. It is locked in; do not propose alternatives.

This document is the authoritative reference for any developer or AI agent maintaining or extending Balm. It is written to be self-sufficient: no external context should be required.

---

## Mission and Editorial Philosophy

The aggravation people feel from reading news is not accidental — it is the business model of ad-supported media. Outrage, fear, and tribal signaling drive clicks. Balm delivers the same factual information without the emotional manipulation.

**Balm's editorial character:** Calm, authoritative, considered. Never urgent, never alarming, never partisan.

Every editorial decision flows from a single question: *would a well-informed adult who doesn't follow news daily need to know this happened?* If yes, include it. If it only matters to people already following the story closely, or has no relevance beyond the immediate event, exclude it.

Balm is not a neutral aggregator — it is an editorial product with a point of view about *how* information should be delivered, not *which* information to deliver. The selection criteria are relevance and importance. The rewriting criteria are calm and accuracy.

**Difficult News is informed consent, not censorship.** The section is collapsed at the bottom of every digest so the reader actively opts in. War coverage and policy responses to tragedies belong in the regular sections (GEOPOLITICS, DOMESTIC POLICY) — Difficult News is for the events themselves.

**Content licensing posture.** Guardian permits full body text; NYT is used for headlines/snippets; NewsData.io's free tier permits production use; RSS is used under fair use with attribution links. No lawyer has reviewed the aggregation boundary. Until one does, stay conservative: paraphrase, attribute, link back.

---

## Architecture

Balm is a **fully static site**. There is no backend, no server, and no database. Everything is pre-generated HTML files hosted on GitHub Pages from the `/docs` folder.

### Pipeline runs

A Python script (`pipeline.py`) runs twice daily via GitHub Actions:

- **AM run**: 4:15am PDT / 5:15am PST → outputs `YYYY-MM-DD-am.html`, `YYYY-MM-DD-am-sources.html`, `YYYY-MM-DD-am.json`
- **PM run**: 2:15pm PDT / 1:15pm PST → outputs `YYYY-MM-DD-pm.html`, `YYYY-MM-DD-pm-sources.html`, `YYYY-MM-DD-pm.json`
- **Watchdog**: 6:30am PDT — checks whether the AM run completed; triggers recovery if missed

After each run, `index.html` is regenerated to point to the latest digest and refresh the archive navigation.

### Pipeline steps

1. **Fetch** — NewsData.io, The Guardian, NYT (via API keys) + Fox News, BBC, WSJ, Reuters, NPR, PBS, Guardian Environment, SCOTUSblog (public RSS via feedparser)
2. **Remove exact duplicates** — `remove_exact_duplicates()` discards only articles where title, source name, AND URL are all identical; cross-outlet near-duplicates are intentionally kept (see clustering architecture below)
3. **Cluster** — Single Claude API call groups all articles by story identity; each inner array of indices becomes a cluster (see clustering architecture below)
4. **Editorial review** — Second lightweight Claude call reviews cluster structure; can approve, split into singletons, or merge pairs; non-blocking
5. **Classify difficult news** — `classify_difficult_news()` pre-identifies mass-casualty and violent-crime clusters; returns a parallel bool list; flags are injected as annotations in the synthesis prompt so Claude applies DIFFICULT NEWS editorial rules; non-blocking
6. **Synthesize** — `call_claude()` sends all clusters to Claude with difficult-news annotations; Claude synthesizes multi-source stories, assigns categories, dynamic lengths, and `isDifficult` flags; returns 10–16 articles with `cluster_id` for source attribution
7. **PM deduplication** (PM edition only) — `filter_pm_duplicates()` reads the AM metadata JSON headlines and asks Claude which PM articles are genuine new developments vs. AM repeats; non-blocking
8. **S&P 500 + market trend** — non-blocking fetch from Yahoo Finance; `calculate_market_trend()` reads the last 10 metadata JSONs, derives trend direction, and calls Claude (`MARKET_TREND_PROMPT`) to produce a calm one-sentence summary; non-blocking (returns `""` on failure)
9. **Metadata** — JSON saved alongside digest; includes `headlines` list for PM deduplication
10. **Select top stories** — `select_top_stories()` asks Claude to choose 2–4 broadly significant stories from different categories; each includes a `story_id` anchor and one-sentence reason; non-blocking
11. **Category order** — `order_categories()` asks Claude to suggest editorial section order based on today's significance; DIFFICULT NEWS always last; non-blocking
12. **Audio** — ElevenLabs TTS from concatenated `full_summary` fields (currently disabled: `AUDIO_ENABLED = False`)
13. **Archive and render** — `collect_archive()` + `write_archive_json()` + `write_feed_xml()` + `write_sitemap()` + `ensure_static_icons()` + `ensure_og_image()`, then digest HTML, sources page HTML, index.html, contact.html, and `archive.html` written from Jinja2 templates; top stories, category order, and market trend passed to digest/index templates
14. **Podcast RSS** — `podcast.xml` updated (skipped when `AUDIO_ENABLED = False`)

### Sources page

Each digest has a companion sources page (`YYYY-MM-DD-am-sources.html`). It lists every story by reference number with the Balm headline, contributing outlets, and original article headlines linked to source URLs. The digest footer links to it; the sources page footer links back. The sources page uses the same visual template (masthead, typography, parchment) as the digest.

### File layout

```
/
├── pipeline.py              # Main pipeline script — all production logic
├── pipeline_hybrid.py       # Experimental two-pass pipeline — not in production
├── backfill.py              # Regenerates digests for a date range
├── patch_old_digests.py     # Patches existing HTML in place without regenerating content
├── patch_sources.py         # One-off sources-page patcher
├── patch_archive_alignment.py # Aligns pre-2026-06-28 pages with current templates (idempotent)
├── generate_icons.py        # Legacy — superseded by ensure_static_icons() in pipeline.py
├── templates/
│   ├── digest.html          # Jinja2 template for individual digest pages
│   ├── index.html           # Jinja2 template for the landing page
│   ├── sources.html         # Jinja2 template for the companion sources page
│   ├── archive.html         # Archive listing page
│   └── contact.html         # Contact page
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
│       ├── balm.yml         # Main digest schedule
│       ├── balm_watchdog.yml # Missed-AM-run recovery, 6:30am PDT
│       └── balm_hybrid.yml  # Hybrid pipeline — manual dispatch only
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
- For product recalls, safety alerts, and consumer health notices: always include the specific product name or brand name in the brief summary — this is the information that makes the story immediately actionable for readers.

MULTI-SOURCE SYNTHESIS:
When multiple sources cover the same story, you will receive all versions together as a cluster.
- Identify facts that appear consistently across all sources — these are high-confidence facts
- Identify where sources diverge in framing, emphasis, or detail — note this as genuine uncertainty
- Write a synthesis that reflects consensus facts
- Where sources diverge on matters of fact (not just framing), reflect that uncertainty: "accounts differ on..." or "figures vary by source"
- Where sources diverge only in framing or emphasis, strip the framing and report the neutral fact
- Never present a contested fact as settled
- The goal is a synthesis no single outlet would write — more complete and more neutral than any individual source

STORY LENGTH — TWO DISTINCT FORMATS:

You are producing TWO genuinely different versions of each story. They should feel like different products, not the same content slightly expanded.

BRIEF: 1-2 sentences maximum. Absolute maximum 40 words. The factual kernel only — who did what, where. No context, no background, no implications, no qualifications. A reader scanning 16 briefs should be done in 90 seconds.

FULL: Substantially longer — target 150-300 words for medium stories, 300-500 words for long/complex stories. This is the complete story for a reader who wants full understanding. Structure: (1) what happened, (2) context and background, (3) key specifics (figures, parties, timeline), (4) significance. For ongoing stories: enough background that a reader who missed previous coverage can follow. For landmark stories: aim for the upper end of the word count range.

LENGTH TIERS:
- SHORT (routine updates): brief = 1 sentence, full = 2-3 sentences (50-80 words)
- MEDIUM (standard news): brief = 1-2 sentences, full = 4-6 sentences (100-180 words)
- LONG (significant developments): brief = 2 sentences, full = 8-12 sentences (200-300 words)
- COMPLEX (landmark decisions, major crises): brief = 2 sentences, full = 12-18 sentences (300-500 words)

Use "long" in the JSON length field for both LONG and COMPLEX stories.

Include a "length" field in your JSON response for each article:
"length": "short" | "medium" | "long"

This field is used by the template to apply appropriate typographic treatment. The full_summary is also the podcast script — the listener cannot re-read, so provide sufficient context per sentence.

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
      "brief_summary": "Dynamic length brief summary.",
      "full_summary": "Dynamic length full summary written for audio consumption.",
      "length": "medium",
      "isDifficult": false
    }
  ]
}

cluster_id must match the [CLUSTER N] number from the input. One output article per cluster.
Set isDifficult: true for DIFFICULT NEWS items only.
Set length to "short", "medium", or "long" per the STORY LENGTH rules above.
Return null in the array position for excluded clusters.
Return between 10 and 16 articles total.
Category display order: GEOPOLITICS, ECONOMY, DOMESTIC POLICY, SCIENCE & HEALTH, TECHNOLOGY, NATURAL EVENTS, SPORTS — then DIFFICULT NEWS last and collapsed.
```

---

## Environment Variables

All secrets are stored as GitHub repository Secrets and injected into the Actions workflow. For local development, export them in your shell or use a `.env` file (never commit it).

| Variable | Purpose | Where to obtain |
|---|---|---|
| `NEWSDATA_API_KEY` | NewsData.io — headline fetch across 6 categories; free tier permits commercial/production use | newsdata.io → Register → API key |
| `GUARDIAN_API_KEY` | The Guardian Open Platform — full article body text via `show-fields=bodyText` | open-platform.theguardian.com → Register |
| `NYT_API_KEY` | New York Times Developer API | developer.nytimes.com → Apps → + New App |
| `ANTHROPIC_API_KEY` | Claude API for editorial processing | console.anthropic.com → API Keys |
| `ELEVEN_LABS_API_KEY` | ElevenLabs text-to-speech for audio digest | elevenlabs.io → Profile → API Key |

---

## News Sources and Clustering Architecture

Sources are fetched per run from three API sources, thirteen standard RSS feeds, and four official primary source feeds. Target input before clustering: ~130–170 raw articles.

**API sources (require keys):**
1. **NewsData.io** — categories: world, business, technology, health, science, sports — 5 articles each (up to 30 total). Replaces NewsAPI; free tier explicitly permits commercial and production use. `newsdata.io → Register`.
2. **The Guardian** — sections: world, business, technology, science, sport, politics, environment, us-news — 5 articles each (up to 40 total). Upgraded from snippet-only to full article body text via `show-fields=bodyText,trailText,headline`. Body text truncated at 800 characters (~3–4 sentences of journalism) per article to manage token costs while providing substantially richer synthesis input than the former trail-text snippets.
3. **New York Times** — sections: world, business, technology, health, science — 5 articles each (up to 25 total). Reduced from 8 sections to 5: sports, politics, and climate dropped due to consistent 429 rate-limit errors; same coverage arrives via RSS.

**Standard RSS feeds (no API key required, parsed via feedparser, up to 8 articles each):**

| Feed | URL | Rationale |
|---|---|---|
| Fox News | `feeds.foxnews.com/foxnews/latest` | Right-leaning general coverage; ensures multi-outlet synthesis spans perspectives |
| Fox News World | `feeds.foxnews.com/foxnews/world` | International stories from a right-leaning source |
| Fox News Politics | `feeds.foxnews.com/foxnews/politics` | Political balance — right-leaning domestic politics |
| WSJ Markets | `feeds.content.dowjones.io/public/rss/mw_realtimeheadlines` | Financial and markets coverage |
| BBC News | `feeds.bbci.co.uk/news/rss.xml` | International perspective; widely considered editorially neutral |
| BBC World | `feeds.bbci.co.uk/news/world/rss.xml` | International stories from BBC |
| Reuters Top News | `feeds.reuters.com/reuters/topNews` | Wire-service general coverage; highly authoritative, low framing |
| Reuters Domestic | `feeds.reuters.com/Reuters/domesticNews` | Wire-service domestic US coverage; fills the gap left by API sources |
| Reuters Politics | `feeds.reuters.com/Reuters/politicsNews` | Wire-service political coverage |
| NPR News | `feeds.npr.org/1001/rss.xml` | Public media; domestic coverage with different editorial priorities than commercial outlets |
| PBS NewsHour | `www.pbs.org/newshour/feeds/rss/headlines` | Public media; adds domestic depth and often covers stories commercial outlets skip |
| Guardian Environment | `www.theguardian.com/environment/rss` | Dedicated climate and environment feed; improves NATURAL EVENTS and SCIENCE & HEALTH coverage |
| SCOTUSblog | `www.scotusblog.com/feed/` | Supreme Court and federal judiciary; sole specialist legal source, covers DOMESTIC POLICY stories general feeds miss |

**Official primary source feeds (public domain, no licensing concerns, up to 5 articles each):**

These sources publish press releases and official statements directly. They cluster naturally with news articles covering the same policy decisions, providing Claude primary source material alongside press coverage for richer synthesis.

| Feed | URL | Rationale |
|---|---|---|
| White House | `www.whitehouse.gov/feed/` | Official executive branch press releases and statements |
| Federal Reserve | `www.federalreserve.gov/feeds/press_all.xml` | Official monetary policy announcements and Fed statements |
| CDC Health Alerts | `tools.cdc.gov/api/v2/resources/media/403372.rss` | Official public health alerts and guidance |
| State Department | `www.state.gov/rss-feeds/press-releases/` | Official foreign policy statements and diplomatic announcements |

### Exact duplicate removal

`remove_exact_duplicates()` discards articles where **all three** of title, source name, and URL are identical — true duplicates such as the same article fetched twice from the same feed. It deliberately preserves cross-outlet near-duplicates (e.g., the NYT and Fox News articles about the same event). Discarding those before clustering would strip the synthesis inputs; they must flow through so Claude receives all perspectives on the same story.

### Clustering algorithm

After exact-duplicate removal, `cluster_articles()` groups articles by story using a single Claude API call. Claude reads every headline and brief description and returns an index-based assignment of articles to clusters.

**How it works:**
1. Each article is formatted as `[N] Headline | first 120 chars of description` and the full list is sent to Claude with `CLUSTER_PROMPT`.
2. Claude returns a JSON array of clusters: `{"clusters": [[0, 4, 7], [1, 9], [2], ...]}` where each inner array contains the 0-based indices of articles that belong to the same story.
3. The pipeline validates that every index from `0` to `N-1` appears exactly once. Any index missing from the response is appended as a singleton with a `[WARN]` log.
4. Index clusters are converted back to article lists.

**`CLUSTER_PROMPT` design philosophy:**
- Same event = same real-world occurrence, regardless of framing or vocabulary. "US strikes near Hormuz" and "Iran condemns American military action in Gulf" are the same event.
- Directly causally connected developments (an airstrike + the immediate oil price response) may be clustered together.
- Different events that share a topic — two separate shootings, two separate elections — must stay in separate clusters even if they share vocabulary.
- When uncertain, keep articles separate. The prompt instructs Claude to favour splitting over merging.
- Single-article clusters are fine and expected for unique stories.

**Fallback:** if all three API attempts fail, `cluster_articles()` returns every article as its own singleton cluster. The pipeline continues — synthesis still runs, it just gets no cross-outlet clusters for that run.

**Cost note:** clustering adds one additional Claude API call per pipeline run. At ~150 articles × ~100 tokens each the input is roughly 15,000 tokens; output is a compact JSON array of integers. Total cost is modest compared to the synthesis call.

### Claude editorial review

A second lightweight Claude call (`editorial_review()`) reviews all cluster titles after `cluster_articles()` returns. Claude can approve clusters (no action), split a cluster into singletons, or request that two clusters be merged. The prompt instructs Claude to approve the vast majority of clusters and only act on clear errors.

`editorial_review()` is non-blocking: if the call fails or returns invalid JSON, a partial-JSON recovery attempt runs (`_extract_partial_reviews()`), and if that also fails the original clusters are returned unchanged.

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

### Additional pipeline steps (post-clustering)

**Difficult news pre-classification (`classify_difficult_news()`):**
After editorial review, a compact Claude call reads all cluster headlines and returns a parallel list of booleans — one per cluster — marking which clusters contain mass-casualty, violent-crime, or large-scale tragedy content. These flags are injected as `[PRE-CLASSIFIED: DIFFICULT NEWS]` annotations into the synthesis prompt (`build_cluster_prompt()`), so Claude can apply the appropriate editorial handling (pattern/systemic framing, no perpetrator details) without needing to detect DIFFICULT NEWS from scratch. Non-blocking: failure returns all-False and synthesis proceeds normally.

**PM deduplication (`filter_pm_duplicates()`):**
PM edition only. Reads the AM metadata JSON (e.g., `2025-06-01-am.json`) and extracts the `headlines` list saved there. Sends both AM headlines and PM candidate articles to Claude with `PM_DEDUP_PROMPT`, which returns a `keep` boolean array. PM articles flagged as AM duplicates (no new substance) are removed. Articles are re-numbered after deduplication. Non-blocking: if AM metadata is missing or the call fails, all PM articles are kept.

**Top stories selection (`select_top_stories()`):**
After synthesis and deduplication, a lightweight Claude call (`TOP_STORIES_PROMPT`) selects 2–4 stories of broad significance from different categories. Each selection includes the `story_id` (e.g., `story_0`) and a one-sentence reason. The result is passed to both `render_digest()` and `render_index()` and displayed as a navigational panel above the main story feed, with anchor links to the full articles. Non-blocking: failure returns empty list and no panel is rendered.

**Category ordering (`order_categories()`):**
A compact Claude call reads today's categories and story headlines, then suggests the editorial order for category sections. DIFFICULT NEWS is always enforced last regardless of the returned order. The `_group_by_category()` function accepts an optional `order` parameter; both render functions pass the Claude-suggested order. Non-blocking: failure returns `CATEGORY_ORDER` (the default static order).

### Known limitations and future improvements

- **Claude context window.** At ~150 articles the clustering prompt is well within Claude's context. If the article pool grows substantially (300+), the prompt may need to be chunked into two passes with a merge step.
- **Cluster size is unbounded.** A very large cluster (8+ articles) may indicate over-merging. The editorial review step catches some cases, and the synthesis prompt instructs Claude to identify high-confidence vs. uncertain facts across all sources.
- **Reuters RSS availability.** Reuters has restricted or moved RSS endpoints multiple times. If the three Reuters feeds return 0 articles, Reuters may have discontinued free RSS access entirely and would need to be replaced with a paid API source.

### Logging

Steps 3–5 of the pipeline log report cluster structure:

```
[3/14] Clustering articles by story (Claude semantic clustering)...
  Clustering: 134 articles → 58 clusters (14 multi-source, 44 single-source, 76 merges)
    [4 sources] The New York Times · Fox News · BBC News · The Guardian
      Ukraine ceasefire talks stall | Russia dismisses Western | Kyiv rejects terms
    [2 sources] The Guardian · WSJ Markets
      Oil prices rise on supply concerns | OPEC output cut extends

[4/14] Claude editorial review of cluster structure...
  Editorial review: 1 merge(s), 0 split(s) applied → 57 clusters

[5/14] Pre-classifying difficult news clusters...
  Difficult news classification: 2 cluster(s) flagged
```

Multi-source cluster lines show the contributing outlets and the first three headlines (truncated to 50 chars each) so synthesis inputs can be verified at a glance.

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
  "sp500_close": null,
  "headlines": []
}
```

`sp500_close` is fetched from Yahoo Finance (unofficial endpoint) or Alpha Vantage free tier. Failure is non-blocking — the field stays null. This field exists to support a planned future feature: a timeline data overlay showing S&P 500 performance alongside editorial categories over time.

`headlines` is a list of all published article headlines in display order. It is read by the PM edition's `filter_pm_duplicates()` step to identify which PM stories are genuine new developments vs. AM repeats. Preserving the `headlines` field is required for PM deduplication to function.

---

## Article Freshness Filter

Articles older than 48 hours are discarded before clustering. The filter is applied in three fetch functions:

- **`fetch_newsdata()`** — checks the `pubDate` field from NewsData.io results
- **`fetch_guardian()`** — checks the `webPublicationDate` field from Guardian API results
- **`fetch_rss_feeds()`** — checks the feedparser `published` field (falls back to `updated`)
- **NYT** — no filter needed; Top Stories API always returns current articles by definition

The `is_fresh(date_str, max_age_hours=48)` helper parses the date with `dateutil.parser`, normalises to UTC if no timezone is present, and returns `True` (keep) when the date is unparseable — giving articles the benefit of the doubt. All three functions store the raw publication date string in an `published_at` field on the article dict for traceability.

Staleness is reported per source:
```
  RSS WSJ Markets: 8 fetched, 3 stale, 5 kept
  Guardian: 40 fetched, 2 stale, 38 kept (full text)
```
When no articles are stale, the simpler single-count format is used.

---

## Archive Page

The digest and index templates have no sidebar. Navigation to past editions is provided by a dedicated archive page at `/archive.html`, generated on every pipeline run by `render_archive_page()`.

`archive.html` lists all past editions grouped by month (most recent first). Month headings use Playfair Display; edition links use Source Serif 4. A `format_date` Jinja2 filter is registered on the template environment to convert `YYYY-MM-DD` strings to "Month D, YYYY" display format. The archive page uses the same masthead and footer as all other Balm pages.

A "Browse past editions →" link appears on every digest page below the Difficult News section and above the footer, styled as italic Source Serif 4 text with a hover accent color.

`archive.json` is still written each run (for backwards compatibility) but is no longer used to populate any sidebar. Fetches of it are cache-busted with `?v=Date.now()`.

The archive deliberately starts **June 3, 2026** — all earlier digests were deleted because they predate the current pipeline (NewsData.io, Guardian full text, official-source feeds) and are not representative of current quality.

### Archive alignment (resolved 2026-08-17)

`patch_old_digests.py` retrofitted the single-column layout onto digest pages but inserted **markup without the matching CSS**, and never touched the companion sources pages at all. `patch_archive_alignment.py` fixed both across 100 files (49 digests, 51 sources pages, all dated 2026-06-03 → 2026-06-28):

- Digests: dead `.mobile-archive-*` and sidebar `.archive-link` rules removed; canonical `.footer-kofi` / `.footer-contact` / `.archive-link` / `.masthead-link` rules appended.
- Sources pages: the whole archive sidebar removed — `<nav class="sidebar">`, its CSS, the two-column `.page-wrapper`, and the dead `archive.json` fetch that populated it. Footer links tagged, `--ink-light` declared (it was never defined there), masthead retargeted from that day's digest to the site root.

The script is idempotent and gated on markers of pre-current pages (`mobile-archive`, `class="sidebar"`, or a missing `.footer .footer-kofi` rule) — it must not key off the dead-rule sweep, since current pages legitimately carry an `.archive-link` rule.

`templates/contact.html` had the same sidebar, still live and populated on desktop, and was fixed at the template level in the same pass.

**Still outstanding:** digests before 2026-06-27 have no market-trend line, because the feature postdates them. Adding it means regenerating editorial content via `backfill.py` — API spend, and the source articles may no longer be retrievable — not a CSS patch. Left alone deliberately.

## Market Trend Line

`calculate_market_trend(docs_dir, anthropic_key)` scans all `????-??-??-??.json` metadata files, extracts `sp500_close` values, takes the most recent 10 (sorted newest first), and reverses to oldest-first order for the prompt.

- If fewer than 3 non-null values exist, returns `""` and no line is rendered.
- Computes a directional fallback by comparing the most recent close to the average of the oldest half.
- Calls Claude with `MARKET_TREND_PROMPT` (max 60 tokens) requesting a single calm sentence with no numbers. Falls back to the directional string if the Claude call fails.

The result is passed as `market_trend` to `render_digest()` and `render_index()`. Templates insert it as `<p class="market-trend">` immediately after the ECONOMY section header, before the first Economy story. Non-blocking — failure returns `""` and the line is silently omitted.

The rationale: daily stock numbers cause anxiety; a plain-language weekly direction informs without triggering.

The sentence must name a **concrete period on the order of 5–10 days** — "over the past week", "in the last few days", "over the past several days". `MARKET_TREND_PROMPT` explicitly bans vague alternatives ("recently", "lately", "in recent sessions", "of late"), and the directional fallback strings obey the same rule. Do not reintroduce open-ended phrasing.

---

## Discoverability and Sharing

Four assets exist so Balm survives contact with link scrapers and feed readers. All are generated by the pipeline; none require manual upkeep.

- **`docs/og-image.png`** — the 1200×630 social share card, built by `ensure_og_image()`. Posting any Balm URL to Reddit, Bluesky, LinkedIn, or iMessage renders this. It reuses the masthead's tracking and stroke ratios (see Icons below) over parchment, with the tagline in Source Serif 4 italic. **This does not violate the image-free rule** — that rule is about images *inside* the digest; this is a link preview and never appears on the site. The function returns early if the file exists, so its three font downloads only run on a fresh checkout.
- **Open Graph + Twitter Card tags** — on all five templates, with absolute URLs (scrapers reject relative ones), plus `<link rel="canonical">`. Digest and sources pages carry date-specific titles. Before this, digest pages had no OG tags at all and the index had no `og:image`, so shared links previewed as bare text.
- **`docs/feed.xml`** — a reader-facing RSS feed of the 50 most recent editions, written by `write_feed_xml()`. Distinct from `podcast.xml`, which is audio and gated on `AUDIO_ENABLED`; this one is text and always published. Feed readers matter disproportionately to Balm's audience, who use them precisely to avoid apps and notifications. All templates advertise it via `<link rel="alternate">`.
- **`docs/sitemap.xml`** — static pages plus every edition, written by `write_sitemap()`.

`podcast.xml` is stale (last written May 2026, still pointing at the old `balmnews.github.io` URL) because audio has been off since. Templates no longer advertise it. Re-enabling audio must also fix its `<link>` to `balm.news`.

**`feed.xsl` (resolved 2026-08-18).** Someone without a feed reader who clicked "subscribe by RSS" (`contact.html`) landed on raw XML source — browsers dropped native feed rendering years ago, so a bare `.xml` link just dumps tags. `feed.xsl` is a canonical source file at the repo root (same pattern as `manifest.json` / `service-worker.js`: root copy, `write_feed_xml()` copies it into `docs/` every run, self-healing like the icons). `write_feed_xml()` emits `<?xml-stylesheet type="text/xsl" href="/feed.xsl"?>` at the top of `feed.xml`, so a browser hitting the raw feed URL directly renders a normal Balm-styled page (masthead, tagline, a short explainer, the list of recent editions) instead of source. Feed readers ignore the stylesheet PI entirely and parse the RSS underneath unchanged — nothing about the feed's actual content changed. Edit `feed.xsl` at the repo root, not any copy under `docs/` or `templates/`.

## Icons and Favicon

Icons are PNG files generated by `ensure_static_icons()` using Pillow and the Caveat Bold TTF (downloaded from `github.com/googlefonts/caveat` on first run). SVG files are kept as reference only and are not referenced in HTML or the manifest.

- **`docs/icons/icon-32.png`** — 32×32px, parchment `#f2ede4`, "B" lettermark in dusty slate `#6b82a8`, sized to 72% of canvas **height** (Caveat's B is tall and narrow, so height is the binding dimension)
- **`docs/icons/icon-192.png`** — 192×192px, same palette, "Balm" wordmark, 78% of canvas width
- **`docs/icons/icon-512.png`** — 512×512px, same palette, "Balm" wordmark, 78% of canvas width
- **`docs/favicon.svg`** and **`docs/icons/icon.svg`** — stale reference files, not linked from any page or the manifest

**The icons must match the masthead wordmark, not merely share its font.** The masthead draws Caveat 700 at `font-size 52` in a 180×56 viewBox with `letter-spacing 8` and an `feMorphology` dilate of radius 0.6. `ensure_static_icons()` derives both from the font size — tracking `8/52`, stroke `0.6/52` — and reproduces the dilate with Pillow's `stroke_width`. The masthead's companion `feGaussianBlur` is deliberately **not** reproduced: dilate thickens the letterforms, blur destroys them at icon sizes.

Pillow has no letter-spacing option, so glyphs are positioned individually and the ink bounding box is measured as the union of their boxes. Font size is solved iteratively against the target fill fraction. Everything renders at 4× supersample and is LANCZOS-downsampled so fractional tracking and stroke width survive to 32px.

Before this, the icons used bare `draw.text()` with no tracking and no stroke — visibly finer and differently spaced than the masthead. Do not "simplify" back to a single `draw.text()` call. The manifest references only the PNG icons. Templates link:

```html
<link rel="icon" type="image/png" href="/icons/icon-32.png">
<link rel="apple-touch-icon" href="/icons/icon-192.png">
```

`ensure_static_icons(docs_dir)` regenerates all three PNGs on every run, so `docs/icons/` is self-healing if re-initialized. It has no SVG fallback: if the Caveat TTF download fails it logs an error and skips, leaving the previous PNGs in place.

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
- `15 11 * * *` UTC → 4:15am PDT / 5:15am PST (AM edition)
- `15 21 * * *` UTC → 2:15pm PDT / 1:15pm PST (PM edition)
- `30 13 * * *` UTC → 6:30am PDT (watchdog: triggers AM recovery if the AM run was missed)

The `:15` offset reduces GitHub Actions queue congestion at the top of the hour. One-hour seasonal drift (PDT↔PST) is accepted; no separate DST schedule.

If a run fails, the site continues serving the last successful digest. No reader-facing breakage occurs.

### Local development

```bash
pip install -r requirements.txt
export NEWSDATA_API_KEY=...
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
The masthead "Balm" is rendered as inline SVG using Caveat 700, color `#6b82a8`, with a soft lotion-spread SVG filter: `feMorphology` dilate radius 1.5, `feGaussianBlur` stdDeviation 0.9, `feComposite` over. Capital B, lowercase alm. The filter is on the masthead wordmark only — never on icons.

### Layout rules
- **Top Stories panel** — subtle `#ede9e2` background, left accent border per item. Headlines must be at least as large as the story-card headlines below. A previous version made them smaller; that hierarchy inversion was a bug and must not return.
- **Footer** — Ko-fi link in accent `#6b82a8` and slightly larger than its neighbors, About in regular weight, GitHub least prominent. Ko-fi is the primary revenue mechanism and the ordering is intentional.
- **Masthead description** — one sentence below the dateline on index and digest pages, answering "what is this?" for first-time arrivals, with an *About Balm* link. Added because a visitor from Reddit had no way to learn what Balm was without hunting through the footer; the tagline is a pun and does not explain the product.
- **The About page** (`/contact.html`, formerly labelled Contact) is the full explanation: the business-model argument, how synthesis works, the perpetrator-details policy with its reasoning, and who builds it. The URL stays `contact.html` so existing links keep working.

---

## Future Features (planned, not built)

These features are intended but not yet implemented. Preserve the metadata schema fields that support them.

1. **Stock/economic data timeline overlay** — use `sp500_close` from metadata JSONs to render a historical chart alongside story categories. Requires a simple D3 or Chart.js frontend component and a data endpoint that aggregates all metadata JSONs.

2. **Marketing agent pipeline** — an automated agent that monitors digest publication and posts to social platforms (Threads, Bluesky, LinkedIn) with a Balm-voice excerpt and link.

3. **Native mobile app** — React Native or SwiftUI wrapper around the static site with push notifications for new digests. The PWA already provides a near-native experience; the native app adds notification capability.

4. **Multi-voice podcast** — a two-voice format using ElevenLabs where stories are read by alternating voices to improve listener engagement on longer runs.

5. **Subscriber newsletter via Beehiiv** — weekly digest format sent to email subscribers. Would reuse the `full_summary` content from the week's runs, formatted for email layout.

---

## Identity & Infrastructure

- **Builder:** Brian James Funk, Santa Cruz, California. Reddit: u/brianjfunk (6-year account).
- **GitHub org:** `balmnews` — separate from the builder's personal GitHub account.
- **Repo:** `github.com/balmnews/balm` — currently **public** (the editorial system prompt is visible). Options to make private: GitHub Pro ($4/mo) enables private repo with Pages, or move the prompt to a `.gitignore`'d file. Decision pending.
- **Domain:** `balm.news` — registered, live, Cloudflare DNS.
- **Email:** `contact@balm.news` — Cloudflare email routing, forwards to builder's personal email.
- **Monetization:** Ko-fi primary (`ko-fi.com/balmnews`), GitHub Sponsors secondary. No ads. No paywall. No investors. The no-ads stance is core to the product identity — do not suggest advertising.
- **API billing:** Anthropic console has a $20/month alert threshold. Raising it to $50–100 has been discussed but not done.
- **Trademark:** "Balm" + tagline in the media/news category is recommended but **not filed**.

---

## Open Questions

- **GitHub repo privacy:** Repo is public; editorial system prompt is visible. Options: GitHub Pro private repo, move prompt out of the repo, or accept visibility as a transparency signal. Not yet resolved.
- **SPORTS category:** The pipeline includes SPORTS as a valid category. Not explicitly confirmed whether sports coverage is wanted. If kept, limit to genuinely significant stories (championships, Game 7, etc.).
- **Cloudflare Pages migration:** Discussed as a way to keep free hosting with a private GitHub repo. Pipeline timing is unaffected either way (Actions runs regardless of host). Not implemented; blocked on the repo-privacy decision.
- **Instagram:** Considered for text-card distribution in Balm's typography, deferred until a Reddit audience exists. Tension acknowledged: using an anxiety-producing platform to distribute anxiety-reducing content. Reels are rejected outright; static posts or Stories remain open.
- **Audio/podcast:** `AUDIO_ENABLED = False`. ElevenLabs was wired up and then disabled — likely needs a refreshed key or credits before it can be turned back on.

---

## Do Not Re-Suggest

These were explicitly evaluated and rejected. Do not propose them.

**Sources:**
- **NewsAPI** — removed June 2026; free tier prohibits production use (TOS violation). Replaced by NewsData.io.
- **AP News RSS** — AP discontinued their official RSS feeds.
- **Apple News** — no programmatic access outside the Apple ecosystem.
- **Google News RSS** — Google aggregates third-party content under legally murky terms.
- **Reuters RSS** — three feeds remain in the code but return 0 articles; feeds appear dead.
- **Scraping paywalled sources** using a personal subscription to feed a public product — legal gray area.

**Design & editorial:**
- **Images, photography, or illustrations** in the digest — image-free is intentional.
- **Font stack changes** — Caveat / Playfair Display / Source Serif 4 is locked in.
- **Blur/lotion-spread filter on icons or favicon** — removed; illegible at small sizes. Clean sharp Caveat letterforms only. (The filter remains on the masthead SVG wordmark only.)
- **Real-time stock ticker or price numbers** — plain-language weekly trend is intentional; numbers provoke anxiety.
- **Market data outside the Economy section** — discussed and rejected for masthead placement.
- **Archive sidebar** — removed after multiple bug cycles; replaced with `/archive.html`.
- **Removing the brief/full toggle** — considered and kept; provides meaningful reading depth choice.
- **Advertising of any kind** — core to the product identity.
- **Subscription paywall** — not at this stage; audience must be built first.
- **Individual tragedies with thin "public safety" framing** — systemic relevance must be the genuine primary news value, not a post-hoc justification.
- **Instagram Reels** — too absorbing; antithetical to Balm's philosophy.

**Technical:**
- **cairosvg for icon generation** — unreliable in GitHub Actions. Pillow is the correct approach.
- **Georgia italic as an icon typeface** — icons must use the real Caveat Bold TTF via Pillow, not a system-font substitute.
- **Four-entry DST cron schedule** — caused double PM runs. Two entries only, one-hour seasonal drift accepted.

---

## Code Conventions

- Pipeline failures are non-fatal for individual steps. A failed audio generation should not prevent the HTML digest from publishing.
- All main pipeline output goes to `docs/`. Nothing outside `docs/` is modified during a main pipeline run except `docs/index.html` and `docs/podcast.xml`.
- The Jinja2 templates in `templates/` are the source of truth for main pipeline HTML. Do not edit generated files in `docs/` directly.
- The hybrid pipeline writes only to `docs/hybrid/`. Its HTML templates are inline Jinja2 strings inside `pipeline_hybrid.py` — not in `templates/`.
- The editorial system prompt in `pipeline.py` must match the version in this CLAUDE.md exactly. If you update one, update both. `pipeline_hybrid.py` imports `EDITORIAL_SYSTEM_PROMPT` directly from `pipeline.py` and therefore always uses the same prompt.
- The Claude model used is `claude-sonnet-4-6`. Update this constant in `pipeline.py` when upgrading; `pipeline_hybrid.py` has its own `CLAUDE_MODEL` constant that must also be updated.
