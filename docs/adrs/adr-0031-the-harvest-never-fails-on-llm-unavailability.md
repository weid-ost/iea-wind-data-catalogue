---
type: adr
id: ADR-0031
title: LLM degradation — the harvest never fails because the LLM is unavailable
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0024-the-llm-boundary, adr-0025-the-extraction-cache-is-committed, adr-0030-llm-access-via-github-models, adr-0029-scheduling-and-the-heartbeat-commit, drain-the-pending-extraction-queue]
tags: [llm, degradation, operations]
---

# ADR-0031 — The harvest never fails because the LLM is unavailable

## Status

**Accepted.** `plans/02-static-plan.md` calls this "the part that actually
matters"; `CLAUDE.md` makes it an invariant.

## Context

The organisation may take a year to decide anything, and there is no budget
holder. Any design in which an expired key, exhausted credits, a provider
outage or a rate limit stops the catalogue is a design that will eventually stop
the catalogue — quietly, at the worst moment, with nobody watching.

Turn 5 asked how to plug the LLM in and whether that meant a billing account.
Turn 7 pushed further, and the resulting posture goes beyond graceful failure:
`plans/02-static-plan.md` §3.4 argues that the *stronger* fallback — no LLM in
CI at all — is worth building even if GitHub Models works, because it makes the
project immune to any future change in GitHub's free-tier terms. The delta is
roughly fifteen pages a week. That does not need to be automated.

## Decision

**Degradation is the default path, not the exception.**

1. **Tier 1 is fully deterministic and carries the majority of records.** Zenodo,
   DataCite, Crossref, GitHub and OSTI keep working regardless of any model
   ([[adr-0024-the-llm-boundary]]).
2. **Tier 3 reads the committed cache first.** On a cache hit, no call is made
   at all ([[adr-0025-the-extraction-cache-is-committed]]).
3. **On a cache miss with no working model**, `harvest.extract.extract` returns
   **`None`** — not an exception. `None` is not an error. The caller appends the
   page to `state/pending-extraction.json` via
   `harvest.extract.queue_pending` and moves on. **`extract()` must never raise
   for an LLM-side reason.** Fixture `x-07` exists to hold this line.
4. **The run succeeds.** The site renders normally; only new task-site records
   stop appearing.
5. **The backlog is visible.** `state/last-run.json` → `pending_extraction`
   carries the queue length, and the site shows it next to the freshness banner
   ([[adr-0029-scheduling-and-the-heartbeat-commit]]).
6. **The queue is drained by a human, whenever they care** — monthly, quarterly,
   never — with `make extract` on their own machine, using whatever key they
   personally have. The resulting cache entries are committed. See
   [[drain-the-pending-extraction-queue]].

The consequence is that **the LLM is a human-operated tool rather than a system
dependency.** Recommended posture: default to GitHub Models in CI, fall through
to the pending queue on any failure, rate limit or unavailability. Both paths
cost nothing and neither requires an account anyone has to administer.

The same rule generalises to the rest of the harvest, and `run_adapter`
implements it: an unreachable source, an auth wall, an upstream schema change or
a 500 becomes a line in the `SourceResult` and then in `state/last-run.json`,
and the other six sources finish (fixtures `wdh-07`, `iea-12`). **An exception
escaping `run_adapter` is a bug in `run_adapter`.**

## Consequences

**Good**

- **An unfunded year costs some new microsite records and nothing else.** That
  is what makes the billing dependency tolerable in an organisation that may
  take a year to decide anything.
- Tier 1 keeps the catalogue current and automatic throughout.
- It removes the temptation to fail loudly in CI, which in a dormant repository
  means a red badge nobody sees and a cron that eventually gets disabled.

**Costs**

- Silent partial coverage. The mitigation is that the backlog is a number on the
  homepage rather than a line in a log, and that
  `state/last-run.json` → `unreachable_sources` is rendered as an honest
  degradation notice.
- "Never fails" makes genuine bugs harder to notice, because everything is a
  report rather than an exit code. The countermeasure is that the run report is
  short and human-readable, and reading it is the monthly curator task.
- `validate-ckan-compat` is the one thing that *does* fail the run
  ([[adr-0021-canonical-record-is-a-ckan-package-dict]]). That asymmetry is
  deliberate: a malformed record breaks the promotion contract, whereas a
  missing record is merely a gap.

## Source

`plans/02-static-plan.md` §3.4 ("Degradation — the part that actually matters"),
§8 (ADR-0031); `harvest/extract.py`; `harvest/adapters/base.py::run_adapter`;
fixtures `x-07`, `wdh-07`; `transcript/conversation-record.md` turns 5, 7.
