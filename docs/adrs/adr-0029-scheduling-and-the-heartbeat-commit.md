---
type: adr
id: ADR-0029
title: Scheduling — weekly cron, workflow_dispatch, and a heartbeat commit every run
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0022-hosting-and-automation, adr-0026-change-detection-by-source-key, re-enable-a-dormant-cron, architecture]
tags: [scheduling, operations, dormancy]
---

# ADR-0029 — Scheduling, and the 60-day dormancy trap

## Status

**Accepted.**

## Context

Turn 4, immediately after accepting the GitHub-only architecture:

> "No GCP is very good: less billing and admin by far… but how would you
> schedule the rebuild? Github actions schedular stops working if a repo is
> dormant…"

Correct, and it is the sharpest edge of [[adr-0022-hosting-and-automation]].
GitHub disables scheduled workflows after **60 days with no repository
activity**, and **only commits count** — tags, releases, issues and merged PRs
do not. The official wording covers public repositories; reports conflict on
private ones, so assume it applies either way.

For a project that may sit unattended inside a slow federation, "the cron
quietly stopped eleven months ago" is the realistic death, not a crash.

## Decision

**Weekly cron plus `workflow_dispatch`, and every run commits.**

Four measures, in the order they matter:

1. **Always commit, even on a no-op run.** `state/last-run.json` is written
   **every time** — timestamp, per-source counts, displacement and pin notices,
   cache hit rate, pending backlog — so a run where nothing upstream changed
   still produces a diff. This is the whole fix and it costs three lines.
   `harvest.runreport.RunReport.write` is called unconditionally in
   `cmd_run`, *including when a source failed*, and its module docstring says
   so: "write it last, write it always, write it even when the harvest failed."
   **Implemented inline, never with a Marketplace keepalive action** — a
   third-party action running in a workflow with repo write permissions is a
   supply-chain risk not worth taking for something this trivial.
2. **Run weekly, not monthly.** With the extraction cache, a week where nothing
   changed costs no LLM calls and about a minute of runner time. It shortens the
   exposure window from 30 days to 7: you would need eight consecutive failures
   to reach dormancy, by which time you would have had eight failure emails.
3. **Surface staleness on the site, not in the Actions tab.** The homepage
   renders "last updated: *date*" from `last-run.json` → `finished_at`, styled
   as a **warning past 45 days** (fixture `r-08`). Nobody checks a CI dashboard
   for a dormant project; a stale banner on the front page is seen by whoever
   next visits, including you. It doubles as honest provenance for users.
4. **Optional dead-man's switch.** A free monitor (healthchecks.io or similar)
   pinged at the end of each run will email if the ping stops. **Prefer a
   monitor over an external cron trigger**: a monitor that dies costs you
   monitoring, whereas an external trigger that dies costs you the catalogue,
   and adding a service to trigger the job would reintroduce exactly the vendor
   sprawl this architecture removed.

Two mechanical requirements that follow:

- **`workflow_dispatch` alongside the cron**, so an on-demand run is a button.
- **Build and deploy must happen in the same workflow run as the harvest.**
  Pushes made with `GITHUB_TOKEN` deliberately do not trigger further workflows,
  so a separate workflow listening for the harvest commit will never fire. This
  one catches everybody once.

## Consequences

**Good**

- The keepalive is a by-product of doing the actual work, not a hack bolted on.
- Byte-stable materialisation ([[adr-0026-change-detection-by-source-key]],
  [[record-format]] §4) means a no-op run's diff is exactly one file, so the
  commit log stays readable rather than becoming noise.
- The worst realistic outcome is a skipped cycle: GitHub warns by email before
  disabling, re-enabling is one click, and the harvest is idempotent so the next
  run catches up completely.

**Costs**

- A commit per week forever. Small, and the alternative is worse.
- `state/last-run.json` is in the repository and changes constantly, so it is a
  perpetual source of merge conflicts on long-lived branches. Resolve by taking
  the newer file and re-running; it is generated.
- The freshness banner is a *user-visible* consequence of an operational
  failure. That is the point, but it means an unattended catalogue looks
  unattended, which is honest and occasionally embarrassing.

**Procedure.** [[re-enable-a-dormant-cron]].

## Source

`plans/02-static-plan.md` §3.3, §8 (ADR-0029); `harvest/runreport.py`;
`harvest/cli.py::cmd_run`; fixture `r-08`;
`transcript/conversation-record.md` turn 4.
