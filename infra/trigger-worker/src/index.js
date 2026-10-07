// Balm external trigger.
//
// GitHub's scheduled triggers routinely fire late, sometimes by hours. This
// Worker fires on Cloudflare's cron instead and dispatches balm.yml through
// the GitHub API. The GitHub crons and the watchdog remain as backup; the
// `balm-edition` concurrency group plus pipeline.py's skip-if-exists guard
// make any duplicate run exit cleanly.
//
// No inputs are sent: pipeline.py resolves the edition from Pacific time, and
// that must stay the only place it is decided.

const DISPATCH_URL =
  "https://api.github.com/repos/balmnews/balm/actions/workflows/balm.yml/dispatches";
const RETRY_DELAY_MS = 30_000;

async function dispatch(env) {
  return fetch(DISPATCH_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "balm-trigger-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
}

async function run(env, cron) {
  let res = await dispatch(env);
  if (res.status === 204) {
    console.log(`dispatched balm.yml (cron ${cron})`);
    return;
  }
  console.warn(`dispatch returned ${res.status}; retrying in 30s`);
  await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
  res = await dispatch(env);
  if (res.status === 204) {
    console.log(`dispatched balm.yml on retry (cron ${cron})`);
    return;
  }
  console.error(`dispatch failed after retry: ${res.status} ${await res.text()}`);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(run(env, event.cron));
  },

  // Not triggerable from a URL.
  async fetch() {
    return new Response("Not found", { status: 404 });
  },
};
