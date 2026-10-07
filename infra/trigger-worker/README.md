# Balm trigger Worker

A Cloudflare Worker that dispatches `balm.yml` on a cron, so editions no longer depend on GitHub's scheduler, which routinely fires hours late. The GitHub crons in `balm.yml` and the watchdog stay as backup. The `balm-edition` concurrency group in both workflows and the skip-if-exists guard in `pipeline.py` make a duplicate run exit through `[SKIP]`.

- Crons: `15 11 * * *` and `15 21 * * *` UTC (4:15am and 2:15pm PDT), matching `balm.yml`.
- Sends `POST /repos/balmnews/balm/actions/workflows/balm.yml/dispatches` with `{"ref":"main"}` and no inputs. `pipeline.py` decides the edition from Pacific time.
- Success is HTTP 204. Anything else is retried once after 30 seconds, then logged with `console.error` (visible in the Worker's logs).
- No URL route: `workers_dev = false` and the `fetch` handler returns 404.

## Token

A fine-grained personal access token. Resource owner `balmnews`, repository access `balmnews/balm` only, permission **Actions: read and write**, nothing else. When it expires the Worker fails (visible only in Worker logs) and editions fall back to GitHub cron, so set a reminder a week before expiry.

## Deploy

```bash
cd infra/trigger-worker
npx wrangler login
npx wrangler secret put GITHUB_TOKEN   # paste the token at the prompt, never anywhere else
npx wrangler deploy
```

Then confirm both Cron Triggers in the Cloudflare dashboard, trigger the scheduled handler once from there, and check the Actions tab for a `Balm Digest` run with event `workflow_dispatch`. If that edition already exists it exits through `[SKIP]`.
