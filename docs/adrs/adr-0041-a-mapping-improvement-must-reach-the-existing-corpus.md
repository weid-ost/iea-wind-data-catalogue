---
type: adr
id: ADR-0041
title: A mapping improvement must reach the records already harvested
status: accepted
date: 2026-09-08
deciders: [project author (OST)]
related: [adr-0026-change-detection-by-source-key, adr-0037-events-are-the-source-of-truth, adr-0038-source-metadata-is-never-updated-only-annotated, adr-0040-vocabulary-is-defined-once-and-shown-at-two-levels, adr-0042-the-concept-doi-is-the-record, run-a-harvest-locally]
tags: [harvest, adapters, change-detection, backfill]
---

# ADR-0041 — A mapping improvement must reach the records already harvested

## Status

**Accepted.**

## Context

ADR-0026 makes change detection a comparison of source keys: if the upstream
record has not changed, the adapter writes no event. That is right, and it is
what keeps a weekly cron from rewriting the whole corpus every Sunday.

It has a consequence nobody had stated. **An adapter mapping change only ever
applies to records harvested after it ships.** Improve `map()` to preserve a
field it used to discard, and every record already in the catalogue keeps the
old, poorer mapping for as long as its upstream sits still — which, for a
finished conference presentation from 2023, is forever. Re-running the harvest
does not help: the adapter fetches, computes the same key, and skips.

This surfaced twice at once, both from the same set of reported issues:

1. **[[adr-0040-vocabulary-is-defined-once-and-shown-at-two-levels]] needed
   Zenodo's resource subtype**, which the Zenodo adapter mapped to
   `resource_kind` and then threw away. Zenodo is the only source in the
   catalogue that publishes a type *and* a subtype, so it is the most
   informative vocabulary there is — and none of it was stored.
2. **DataCite does not carry access conditions for Zenodo deposits.** Checked
   live: DataCite's record for `10.5281/zenodo.21619015` has the CC-BY
   `rightsList` and no access term at all, while Zenodo's own API says
   `access_right: open`. The DataCite adapter refuses — correctly — to infer
   access from a licence, so 92 of the catalogue's 151 "Access unknown" records
   were openly-available Zenodo deposits that the community sweep had never
   seen, because they sit outside the nine configured communities.

## Decision

### 1. The adapter's mapping version is part of its change token

Each adapter may declare a `MAPPING_VERSION`, folded into the source key:

```
"<revision>@<version DOI>~m2"
```

Bump it when `map()` starts preserving something it did not preserve before, and
the next run re-scrapes every record of that source **exactly once**, then
resumes ordinary change detection. Zenodo is at `2`: preserve
`metadata.resource_type` as `extra.zenodo_resource_type`.

This is the smallest possible answer. It costs one sweep per mapping change, it
needs no new state, it is visible in the event log, and it makes the cost of a
mapping change explicit at the point where somebody decides to make one.

### 2. Adapters are told where the event log is, and never assume

`Adapter.events_dir` is set by `run_adapter()` before `harvest()` runs. It is
`None` for a directly-constructed adapter — a `map()` unit test, a fixture
replay — and an adapter **must** treat `None` as "I have not been told what the
catalogue holds" and skip any backfill entirely, rather than reaching for the
default directory and reading the live repository from inside a test. That is
not hypothetical: the first implementation did exactly that, and four unit tests
started making network-shaped calls against real data.

### 3. Zenodo backfills the records another source found first

A second phase of `ZenodoAdapter.harvest()`, after the community sweep. It reads
the event log for identities whose DOI matches `10.5281/zenodo.<n>` and which no
Zenodo scrape has ever touched, and fetches each one. The numeric suffix of a
Zenodo DOI *is* the record id, so it costs one GET and no search. The queue
empties as it is worked and never re-fetches, because a scrape puts each
identity out of scope.

This closes issue #3 at its root — Zenodo states the access conditions, so the
catalogue goes and asks Zenodo — rather than by inferring open access from an
open licence. That inference was considered and rejected: `AvailabilityBadge`
is explicit that implying a download which is actually gated is the one thing a
catalogue must not get wrong, and a CC-BY licence on the metadata is not a
statement about the files.

**"Unknown" keeps its name.** It would have been easy to relabel it "Access not
stated" and sound more precise. This is a prototype, other gaps in the same area
are entirely plausible, and a word that sounds settled would hide them. The
definition in `vocabulary.yaml` says what it means and says the bucket should
keep shrinking.

### 4. A backfill enriches the identity the catalogue already holds

`RawObservation.identity_override`. Zenodo's identity is the **concept** DOI
(`zen-02`), but DataCite indexes **version** DOIs, so the catalogue is usually
already listing the artifact under a version DOI. Re-keying the enrichment to the
concept would mint a *second* record for one artifact instead of enriching the
one that exists — verified on a sample: nine of twelve backfill candidates had a
concept DOI that differed from the identity held.

So the backfill carries the identity it read from the log, and `map()` honours
it. This is the same move `recheck_withdrawn()` already makes: the caller
supplies the identity it holds, because an adapter must not re-key a record it
was asked about. `map()` stays pure.

Collapsing a version DOI onto its concept is a **merge**, not a re-key, and
merges belong to the reconciler — [[adr-0042-the-concept-doi-is-the-record]].

## Consequences

**Good**

- A mapping fix can be shipped and actually take effect, which was not
  previously true.
- The cost is bounded and visible: one re-scrape per record per bump.
- 92 records moved from "Access unknown" to a stated status without a single
  inference; open access went from 177 records to 258.
- `events_dir` being explicit removed a whole class of test that quietly read
  the live repository.

**Costs**

- A bump re-writes an event for every record of that source, so the heartbeat
  commit after one is large. That is honest — the mapping really did change for
  all of them — but it is a diff somebody has to review.
- Forgetting to bump is silent: the improvement ships and reaches nothing. The
  only defence is that this ADR exists and the constant sits at the top of the
  adapter with a changelog comment.
- The backfill is Zenodo-only. The same gap may exist for other DOI registrars,
  and nothing here generalises it; a second one would be a second adapter
  method, deliberately, rather than a framework built for one case.
- The backfill shares `max_records` with the community sweep, so under the CI
  record cap it drains slowly. It drains, and it is idempotent, so this is a
  schedule question rather than a correctness one.

## Source

`harvest/adapters/zenodo.py` (`MAPPING_VERSION`, `zenodo_dois_to_backfill`);
`harvest/adapters/base.py` (`Adapter.events_dir`); `harvest/models.py`
(`RawObservation.identity_override`); fixture `zen-13`;
[GitHub issue #3](https://github.com/weid-ost/iea-wind-data-catalogue/issues).
