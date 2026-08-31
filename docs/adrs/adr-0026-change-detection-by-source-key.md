---
type: adr
id: ADR-0026
title: Change detection — a per-adapter record-level source key
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
supersedes: ["ADR-0026 (rev 1, turn 11: value-comparison event stream)"]
related: [adr-0037-events-are-the-source-of-truth, adr-0038-source-metadata-is-never-updated-only-annotated, adr-0025-the-extraction-cache-is-committed, add-a-source-adapter]
tags: [harvest, reconciliation, events]
---

# ADR-0026 — Change detection: a per-adapter record-level source key

## Status

**Accepted (revision 2).** Revision 1 — a value-comparison event stream, agreed
in turn 11 — was superseded in turn 12 by the author's better model.

## Context

Turn 11 proposed a history chain that compared *values* between scrapes. Turn 12
corrected it:

> "You still need a source key for every source - which for most sources will be
> a source_modified (or similar) timestamp; for github repos it'd probably be a
> tag or even a sha. So if the source key changes, you have to assume that the
> metadata has been updated."

That is right, and it is much cheaper. Field-level diffing across seven
heterogeneous sources requires normalisation-for-comparison, an "absence is not
a value" rule, and a philosophy of which timestamp to trust — for sources whose
timestamps are known to be untrustworthy (Crossref's `indexed` churns without
any content change at all).

## Decision

**Every adapter defines exactly one record-level change token — the source key —
whose semantics it owns.**

1. `run_adapter` compares the key to the last `scraped` event **for that same
   source system**. If it differs, upstream is assumed updated and its metadata
   is taken wholesale. **If it does not differ, the record is skipped and no
   event is written at all.** One comparison per record; no field diffing.
2. The design burden moves to *choosing a trustworthy key per source*, which is
   the right place for it, because trustworthiness is source-specific:

| Source | Key | Note |
|---|---|---|
| `zenodo` | record revision id (with the version DOI) | InvenioRDM bumps it on metadata edits — **verify the field name against a live payload** |
| `datacite` | `attributes.updated` | reflects client metadata pushes |
| `crossref` | `deposited` | **not** `indexed`, which churns without content change |
| `github` | default-branch SHA + latest release tag + `hash(description, topics, licence)` | no single trustworthy field exists |
| `osti` | metadata-updated field if provided, else fallback | |
| `wdh` | dataset-updated field if provided, else fallback | |
| `ieawind` | normalised main-content hash | **the same value as the LLM cache-key input** |

3. **Universal fallback: a hash of the normalised source payload.**
   `harvest.adapters.base.payload_hash` — sorted-key JSON → SHA-256 → 16 hex,
   deterministic across runs and interpreters. Key selection can therefore never
   block an adapter.
4. **Hash only the fields that mean something.** Including `pushed_at`, a star
   count or a `retrieved_at` timestamp makes every run a change event, which
   turns append-on-change into append-always and defeats the entire design.
5. `sources.yaml` carries a human-readable `source_key:` string per source
   describing the semantics; the adapter owns the implementation. The two must
   agree, and the class also declares `source_key_semantics` for the run report.

Note what the fallback quietly does: a content hash **is** value comparison,
done at record granularity where it is cheap. The two models from turns 11 and
12 are reunified rather than one discarded.

## Consequences

**Good**

- Growth of `events/` stays proportional to real change — roughly 3,000 events
  at seeding and a few hundred a year afterwards, against ~300 MB/year if every
  record were snapshotted every week.
- A no-op weekly run produces no diff in `events/` or `records/`; the only churn
  is `state/last-run.json`, which is exactly what the heartbeat needs
  ([[adr-0029-scheduling-and-the-heartbeat-commit]]).
- Adapters are simple: yield a token, and the shared runner does the rest, once,
  for everybody. The rule cannot be implemented inconsistently seven times.

**Costs**

- **A noisy key costs a redundant re-scrape and a no-op event.** It cannot
  clobber a human edit, because
  [[adr-0038-source-metadata-is-never-updated-only-annotated]] makes local
  annotation additive. This is why a slightly wrong key is a nuisance rather
  than a data-loss event.
- A *stale* key — one the source fails to bump on a real edit — means the
  catalogue silently shows old metadata. This is the failure mode to worry
  about, and it is why the Zenodo revision-id field name must be verified
  against a live payload rather than assumed.
- Change detection is per source system, so an artifact described by four
  systems has four independent keys and four independent replacement cycles.
  That is intentional: see the composition rules in [[record-format]] §3.1.

**Checkable.** `state/last-run.json` reports `seen`, `changed` and
`skipped_unchanged` per source. A second identical run must report
`changed: 0` for every source and leave `events/` untouched.

## Source

`plans/02-static-plan.md` §4.1, §8 (ADR-0026); `harvest/CONTRACT.md` §3;
`harvest/adapters/base.py`; `harvest/events.py` `has_changed`;
`transcript/conversation-record.md` turns 11–12.
