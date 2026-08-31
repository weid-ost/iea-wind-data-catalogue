---
type: adr
id: ADR-0025
title: The extraction cache is committed to the repository
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0024-the-llm-boundary, adr-0026-change-detection-by-source-key, adr-0030-llm-access-via-github-models, adr-0031-the-harvest-never-fails-on-llm-unavailability, drain-the-pending-extraction-queue]
tags: [llm, cache, reproducibility]
---

# ADR-0025 — The extraction cache is committed to the repository

## Status

**Accepted.**

## Context

"Rebuild from the repository" and "AI harvester" are in direct conflict unless
something resolves them. A model is not a pure function: same page, same prompt,
different day, possibly different output. If a rebuild re-infers, then
`records/` is not reproducible from `events/`, the byte-stability that makes a
no-op run produce no diff is lost, and the heartbeat commit becomes noise.

There is also a cost dimension. Most pages do not change between weekly runs, so
re-inferring them is money and rate limit spent on nothing.

## Decision

**Cache every extraction, key it on content, and commit it.**

1. The cache key is `sha256(content + prompt_version + model_id)`, implemented
   in `harvest.extract.cache_key` — which is *not* a stub, precisely because the
   Tier-3 source key derives from the same normalised content and the adapters
   and the reconciler must agree on it.
2. Entries live at `cache/<key>.json`, are byte-stable JSON, and are
   **committed to the repository**.
3. **A rebuild replays the committed cache rather than re-inferring**, and
   therefore produces byte-identical output.
4. `prompt_version` (`harvest.extract.PROMPT_VERSION`, currently `"v1"`) is part
   of the key. **Bump it on any prompt change** — that invalidates the cache
   deliberately and visibly, which is the correct behaviour and the reason it is
   in the key.
5. `model_id` is in the key too, so a change of model is a visible, auditable
   cache-lineage split rather than a silent quality change.
6. Cache hit and miss counts, and the resulting hit rate, are reported in
   `state/last-run.json` under `cache`.

Note the second job the content hash does: for Tier-3 pages it **is** the source
key ([[adr-0026-change-detection-by-source-key]]). One hash, two purposes, no
possibility of the two disagreeing.

## Consequences

**Good**

- Monthly and weekly runs are nearly free: most pages do not change, so most
  runs invoke the model almost not at all.
- Reproducibility is restored, which is what the whole
  events-are-truth/records-are-derived model depends on
  ([[adr-0037-events-are-the-source-of-truth]]).
- The expensive first pass can be run **on a laptop with someone's own key** and
  the resulting cache committed, so CI only ever handles the delta. That is what
  turns "we need a funded API account" into "someone spends about $20 once".

**Costs**

- The repository grows by one small JSON file per extracted page. At a few
  thousand pages this is trivial, and it is the good kind of growth: auditable,
  diffable, and visible in a public repo, which is part of what makes an
  AI-assisted catalogue trustworthy.
- A prompt change invalidates everything at once. That is deliberate, but it
  means a prompt change is a **budgeted operation**, not a casual one: the
  `MAX_EXTRACTIONS = 200` per-run cap exists exactly so the resulting backlog
  drains over weeks. Announce it in the commit message and watch
  `pending_extraction` in the run report.
- Cache entries are inputs to the record format, so a cache entry that is wrong
  is wrong until someone pins over it — see
  [[adr-0038-source-metadata-is-never-updated-only-annotated]] §4.3 and
  [[correct-a-record]].

**Ownership.** `read_cache`, `write_cache` and the drain path are stubs owned by
the extraction track. Procedure: [[drain-the-pending-extraction-queue]].

## Source

`plans/02-static-plan.md` §2.3, §3.4, §8 (ADR-0025); `harvest/extract.py`;
`harvest/CONTRACT.md` §10; `transcript/conversation-record.md` turn 3.
