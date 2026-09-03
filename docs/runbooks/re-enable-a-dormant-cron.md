---
type: runbook
id: RUN-re-enable-a-dormant-cron
status: current
date: 2026-08-31
related: [adr-0029-scheduling-and-the-heartbeat-commit, adr-0022-hosting-and-automation, run-a-harvest-locally]
tags: [runbook, operations, dormancy]
last_executed: never
---

# Runbook — re-enable a dormant scheduled workflow

**Goal:** get the weekly harvest running again after GitHub has disabled it, and
make sure it does not happen for the same reason twice.
**Governed by:** [[adr-0029-scheduling-and-the-heartbeat-commit]].

---

## 0. The rule you are fighting

GitHub disables scheduled workflows after **60 days with no repository
activity**, and **only commits count** — tags, releases, issues and merged PRs
do not. The official wording covers public repositories; reports conflict on
private ones, so **assume it applies either way**.

GitHub warns by email before disabling, and re-enabling is one click. The worst
realistic outcome is a skipped cycle: the harvest is idempotent, so the next run
catches up completely.

## 1. Recognise it

| Signal | Where |
|---|---|
| "Scheduled workflow disabled due to repository inactivity" email | the repository owner's inbox |
| the workflow shows **"This workflow was disabled because…"** | Actions → the harvest workflow |
| the homepage freshness banner is in its **warning state** | the site — past 45 days since `finished_at` |
| `state/last-run.json` → `finished_at` is old | `uv run python -m harvest report` |

The banner is the one you will actually notice, which is why it exists: nobody
checks a CI dashboard for a dormant project.

## 2. Re-enable

1. GitHub → the repository → **Actions** → the harvest workflow.
2. **Enable workflow**.
3. Run it once by hand: **Run workflow** (`workflow_dispatch`, which exists
   alongside the cron for exactly this). Leave **skip_harvest** unticked: a
   skipped harvest makes no commit, so it is not a keepalive.
4. Confirm the run **committed** `state/last-run.json`. If it did not, the
   heartbeat is broken and re-enabling has bought you 60 days, not a fix — go
   to §4.

## 3. Diagnose why it went dormant

Ask, in order:

1. **Was the workflow failing, rather than not running?** Eight consecutive
   weekly failures reach 60 days. You would have had eight failure emails, so
   check whether they were going somewhere nobody reads — a personal mailbox
   that has been forwarded, or a filter.
2. **Did the heartbeat stop producing a diff?** `state/last-run.json` must be
   written on **every** run, including a total no-op and including a failed run.
   Verify locally:

   ```sh
   make harvest
   git status --short          # expect state/last-run.json, and normally nothing else
   ```

   If `state/last-run.json` is unchanged after a run, that is the bug.
3. **Did the workflow stop committing?** A run that writes the file but does not
   `git commit && git push` is not activity. Check the commit step's condition —
   a common mistake is `if: steps.harvest.outputs.changed == 'true'`, which
   skips exactly the no-op runs that the keepalive exists for. **Commit
   unconditionally.**
4. **Were the commits made by something that is not a commit?** Tags, releases
   and merged PRs do not reset the clock.

## 4. Requirements on the harvest workflow

`.github/workflows/catalogue.yml` implements all of this. These are the things
to verify after any change to it:

- **`schedule:` weekly** *and* **`workflow_dispatch:`**.
- **`runs-on: ubuntu-24.04`** — never `ubuntu-latest`
  ([[adr-0034-toolchain-pinning-and-no-auto-updates]]).
- `uv sync --frozen --dev`, then `uv run python -m harvest run`, with `--max-records N` when the dispatch input is set.
- **Commit `state/last-run.json` on every run, unconditionally**, including when
  a source failed and including when nothing changed. Inline `git` commands —
  **never a Marketplace keepalive action**, which would put a third party inside
  a workflow that has repository write permissions.
- **Build and deploy in the same workflow run as the harvest.** Pushes made with
  `GITHUB_TOKEN` deliberately do not trigger further workflows, so a separate
  workflow listening for the harvest commit **will never fire**. This one
  catches everybody once.
- Permissions scoped: `contents: write` on the harvest job, `models: read` where
  Tier-3 extraction runs, `pages: write` and `id-token: write` only on the
  deploy job.
- **Never run the harvest workflow on `pull_request`.**
- Set `permissions:` explicitly at the job level rather than relying on
  repository defaults.

## 5. Reduce the chance of a repeat

1. **Confirm the failure emails go somewhere a human reads** — a shared OST
   address or distribution list, never a personal mailbox. Bus factor.
2. **Consider a dead-man's switch.** A free monitor (healthchecks.io or similar)
   pinged at the end of each run emails you if the ping stops. **Prefer a
   monitor over an external cron trigger**: a monitor that dies costs you
   monitoring; an external trigger that dies costs you the catalogue, and adding
   a service to trigger the job reintroduces exactly the vendor sprawl this
   architecture removed.
3. **Leave the cadence weekly.** With the extraction cache, a week where nothing
   changed costs no model calls and about a minute of runner time, and it
   shortens the exposure window from 30 days to 7.

## 6. Catch up

Nothing special is required. The harvest is idempotent: the next run re-scrapes,
detects changed source keys, appends only what changed, and materialises. See
[[run-a-harvest-locally]].

---

**Last executed:** never — and it stays `never` until the workflow runs for real.
`.github/workflows/catalogue.yml` **does exist** (§4 describes it accurately);
what does not exist is a run of it. A workflow cannot be executed locally and
this one has deliberately not been pushed, so its own header says so too. The
local half of §3.2 was verified on 2026-08-31: `harvest run` writes
`state/last-run.json` unconditionally, and a no-op run leaves everything else
untouched. §2 — the "re-enable the dormant schedule" steps — is the part that
has never been exercised, because nothing has yet been dormant.
