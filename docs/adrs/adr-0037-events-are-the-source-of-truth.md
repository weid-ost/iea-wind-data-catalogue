---
type: adr
id: ADR-0037
title: events/ is the source of truth; records/ is a derived materialised view
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0026-change-detection-by-source-key, adr-0038-source-metadata-is-never-updated-only-annotated, adr-0027-withdrawn-records-are-retained, adr-0021-canonical-record-is-a-ckan-package-dict, materialize-and-validate, record-format]
tags: [data-model, events, provenance]
---

# ADR-0037 — `events/` is the source of truth; `records/` is derived

## Status

**Accepted.** Proposed in turn 11, confirmed in turn 12.

## Context

Turn 11 asked for a history chain:

> "So basically you're saving an event stream of scrape / edit events in time
> order … We can always surface history as part of the actual UI later."

The immediate question is which artifact is authoritative. If `records/` is
authoritative and events are a log, then the log can drift from the record and
nobody notices. If events are authoritative and records are generated, the two
cannot disagree, because one is a pure function of the other.

## Decision

**`events/<slug>.jsonl` is the source of truth. `records/<slug>.json` is a
materialised view of it, and is regenerable by replay.**

1. **Append-only.** Events are written only through
   `harvest.events.append_event` and its convenience writers
   (`record_scrape`, `annotate`, `withdraw`, `raise_notice`). Nothing ever
   rewrites or reorders a line.
2. **Append-on-change only.** A scrape whose source key is unchanged writes
   nothing at all ([[adr-0026-change-detection-by-source-key]]); the fact that
   the run happened is recorded in `state/last-run.json`.
3. **Ordered by *our* observation time**, not by any source-provided timestamp.
   Source timestamps are unreliable across exactly these sources; the
   source-provided timestamp and the source key are carried as payload instead.
4. **`records/` is derived and disposable.** Delete it and
   `uv run python -m harvest materialize` rebuilds it byte-for-byte.
   `make clean` deletes `records/` and explicitly **never touches `events/`**.
5. **Materialisation is byte-stable**: sorted keys, fixed separators, two-space
   indent, one trailing newline. A run in which nothing changed produces no diff
   in `records/`.
6. **`resolve()` is the fold**: it turns an identity's event list into a
   `ResolvedRecord` (source, local, effective, provenance, notices), and
   `replay()` shapes that into the CKAN package dict. Both accept an in-memory
   event list, which is how the reconciliation tests avoid the filesystem
   entirely.

**Filenames.** This ADR is written as `events/<identity-key>.jsonl`. An identity
key contains `/` and `|`, so in practice the file *stem* is the slug and the
unabbreviated `identity_key` is a field on every line — the same thing, spelled
so it can exist on a filesystem. See [[record-format]] §1.2.

**What the log is for**, now that
[[adr-0038-source-metadata-is-never-updated-only-annotated]] has deleted most of
the reconciliation it used to carry, is three things: history and provenance for
the record page; the store of local annotations; and displacement and pin
notices for the run report.

## Consequences

**Good**

- The catalogue is reproducible: same events, same records, same bytes.
- Nothing is ever lost. A displaced local value, a superseded title, a
  withdrawn record's last-known metadata — all still in the log.
- The record page can show history later without any new storage, because the
  history is already the storage.
- Growth is proportional to real change, not to run frequency: roughly 3,000
  events at seeding and a few hundred a year afterwards, against ~300 MB/year if
  every record were snapshotted weekly.

**Costs**

- `events/` is the one directory that must never be lost, and it is the one
  directory with no automated backup beyond git. Treat a force-push over it as
  data loss.
- Hand-editing an event log is possible and occasionally necessary, and it is
  the only way to introduce a slug collision that `append_event` would have
  refused — which is why `materialize_all` re-checks for collisions.
- Two directories to reason about instead of one, and a standing temptation to
  "just fix the record". The answer is always: append an event, then
  materialise. See [[correct-a-record]].

**Checkable.** `rm -f records/*.json && make materialize && git diff --stat` must
report no changes. That is the acceptance test for this ADR and it is the first
step of [[materialize-and-validate]].

## Source

`plans/02-static-plan.md` §4.4, §8 (ADR-0037); `harvest/events.py`;
`harvest/materialize.py`; `harvest/CONTRACT.md` §§0, 6;
`transcript/conversation-record.md` turns 11–12.
