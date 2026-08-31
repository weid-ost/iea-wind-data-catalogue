---
type: adr
id: ADR-0027
title: Withdrawn records are retained, never deleted
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0021-canonical-record-is-a-ckan-package-dict, adr-0037-events-are-the-source-of-truth, handle-a-withdrawn-record, record-format]
tags: [lifecycle, link-rot, records]
---

# ADR-0027 — Withdrawn records are retained, never deleted

## Status

**Accepted.** Non-negotiable invariant in `CLAUDE.md`.

## Context

Artifacts disappear upstream. A Zenodo record is withdrawn and its DOI resolves
to a tombstone page (fixture `zen-12`). A task microsite for a completed task
404s or redirects (fixture `iea-12`). A GitHub repository is archived, renamed
or transferred (fixtures `gh-05`, `gh-07`).

The tempting behaviour — drop the record because the thing is gone — makes the
catalogue an active participant in link rot, which is the exact failure mode a
catalogue exists to fight. Anyone who cited `/record/<slug>/` now has a dead
link, and the one place that could have explained what happened has removed the
explanation.

## Decision

**When a record vanishes upstream, do not delete it.**

1. Append a `withdrawn` event to the identity's log
   (`harvest.events.withdraw`). The event log is append-only, so the
   metadata as last observed is preserved forever.
2. The record continues to be **materialised**, with:
   - `extras.lifecycle_state = "withdrawn"`,
   - `extras.withdrawn = "true"`,
   - `extras.withdrawn_at = <the observation timestamp>`.
3. **CKAN's `state` stays `"active"`.** CKAN's `state: deleted` means "hidden
   and purgeable", which is precisely what this ADR forbids. Withdrawal is a
   catalogue concept and lives in the extras
   ([[adr-0021-canonical-record-is-a-ckan-package-dict]],
   [[record-format]] §4.3).
4. **The page and the URL survive**, with a withdrawal banner rendered
   `role="status"` at the top of `<main>` (fixture `r-04`).
5. If the source starts reporting the record as present again, a later `scraped`
   event with `source.withdrawn: false` clears the flag — `harvest.events.resolve`
   implements exactly that.

The **only** sanctioned deletion in the whole system is
`materialize_all(prune=True)` removing a `records/*.json` file that has no
backing events at all, which can only ever fire for an identity whose events
were removed by hand. Withdrawn identities keep their events and therefore keep
their records.

## Consequences

**Good**

- Citations survive. The catalogue is a stable place to point at, which is most
  of what makes it worth building.
- "This existed and is now withdrawn, as of this date" is genuinely useful
  metadata, and it is only available because the record was kept.
- Retention is free: the event log already holds the last-observed metadata, so
  the record needs no special storage.

**Costs**

- The record count only ever grows, and some of it is tombstones. Mitigated by
  `extras.suppressed` for noise records (retained but not listed) and by the
  lifecycle facet.
- Anyone reading `records/` must handle withdrawn records: never imply a
  download, never present a withdrawn record as current, and never let one lead
  a search result list unremarked.
- `state: "active"` on a withdrawn record surprises people who know CKAN. It is
  documented in three places for that reason: here,
  `harvest/models.py::CkanPackage`, and [[record-format]] §4.3.

**Procedure.** [[handle-a-withdrawn-record]].

## Source

`plans/02-static-plan.md` §4.4, §8 (ADR-0027); `CLAUDE.md` invariants;
`harvest/models.py`; `harvest/events.py`; fixtures `zen-12`, `r-04`, `gh-05`,
`iea-12`; `transcript/conversation-record.md` turns 11–12.
