# Balm

**The topical anti-inflammatory.**

A daily news digest that delivers the same information as conventional media — without the inflammatory language, clickbait, or emotional manipulation. Fully static, AI-curated, published twice daily via GitHub Actions.

---

## Setup

### 1. Create a GitHub account

Go to [github.com](https://github.com) and create an account if you don't have one.

### 2. Create this repository

Fork this repository or create a new one and copy these files into it.

### 3. Enable GitHub Pages

1. Go to your repository → **Settings** → **Pages**
2. Under **Source**, select **Deploy from a branch**
3. Branch: `main` — Folder: `/docs`
4. Click **Save**

Your site will be live at `https://[your-username].github.io/balm`.

### 4. Obtain API keys

You need five API keys. All have free tiers sufficient to run Balm.

**NewsData.io** — [newsdata.io](https://newsdata.io)
- Register for a free account
- Free tier explicitly permits commercial and production use (unlike NewsAPI, which restricts free keys to non-production)
- Your API key is shown on the dashboard immediately after registration

**The Guardian** — [open-platform.theguardian.com](https://open-platform.theguardian.com)
- Click "Register developer key"
- Fill in the form — approval is instant
- Your key is emailed to you

**New York Times** — [developer.nytimes.com](https://developer.nytimes.com)
- Sign in with a NYT account (or create one free)
- Go to **Apps** → **+ New App**
- Enable **Top Stories API**
- Your key appears under the app after creation

**Anthropic (Claude)** — [console.anthropic.com](https://console.anthropic.com)
- Create an account and add a payment method
- Go to **API Keys** → **Create Key**
- Balm uses `claude-sonnet-4-20250514` — cost is a few cents per run

**ElevenLabs** — [elevenlabs.io](https://elevenlabs.io)
- Create a free account
- Go to your **Profile** → **API Key**
- Free tier (10,000 characters/month) covers roughly 2–3 digests; upgrade for daily use

### 5. Add secrets to GitHub

1. Go to your repository → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** for each key:

| Secret name | Value |
|---|---|
| `NEWSDATA_API_KEY` | Your NewsData.io key |
| `GUARDIAN_API_KEY` | Your Guardian key |
| `NYT_API_KEY` | Your NYT key |
| `ANTHROPIC_API_KEY` | Your Anthropic key |
| `ELEVEN_LABS_API_KEY` | Your ElevenLabs key |

If you previously had a `NEWS_API_KEY` secret, it can be deleted from your repository secrets — it is no longer used.

### 6. Set up Ko-fi (optional)

1. Create an account at [ko-fi.com](https://ko-fi.com)
2. Note your Ko-fi URL (e.g. `https://ko-fi.com/yourname`)
3. In `templates/digest.html` and `templates/index.html`, replace `https://ko-fi.com/balmnews` with your URL

### 7. Trigger your first run

1. Go to your repository → **Actions** → **Balm Digest**
2. Click **Run workflow** → select edition (AM or PM) → **Run workflow**
3. The workflow takes 1–2 minutes. When complete, your site is live.

### 8. Verify

Visit `https://[your-username].github.io/balm`. You should see today's digest.

---

## Automatic schedule

After setup, GitHub Actions runs the pipeline automatically:

| Time | Edition |
|---|---|
| 7am ET / 4am PT | AM |
| 5pm ET / 2pm PT | PM |

No further action required.

---

## Local development

```bash
git clone https://github.com/[your-username]/balm
cd balm
pip install -r requirements.txt

export NEWSDATA_API_KEY=...
export GUARDIAN_API_KEY=...
export NYT_API_KEY=...
export ANTHROPIC_API_KEY=...
export ELEVEN_LABS_API_KEY=...

python pipeline.py --run am
# Open docs/index.html in your browser
```

---

## Architecture

- **`pipeline.py`** — fetches news, calls Claude, generates HTML + audio
- **`templates/`** — Jinja2 HTML templates
- **`docs/`** — all output files (GitHub Pages root)
- **`.github/workflows/balm.yml`** — scheduled CI/CD

See [CLAUDE.md](CLAUDE.md) for the complete technical and editorial reference.

---

## Support

[Support Balm on Ko-fi](https://ko-fi.com/balmnews)

---

## License

The Balm software — pipeline, templates, and scripts — is released under the
[MIT License](LICENSE).

Published digest content under `docs/` is **not** covered by that license. Those
editions summarise reporting by third-party news organisations, which retain all
rights in their underlying work; Balm publishes them under fair use with
attribution and links back to every original source.
