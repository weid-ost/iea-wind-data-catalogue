---
type: adr
id: ADR-0042
title: The concept DOI is the record; version DOIs merge into it and are retained
status: accepted
date: 2026-09-08
deciders: [project author (OST)]
related: [adr-0026-change-detection-by-source-key, adr-0027-withdrawn-records-are-retained, adr-0037-events-are-the-source-of-truth, adr-0038-source-metadata-is-never-updated-only-annotated, adr-0041-a-mapping-improvement-must-reach-the-existing-corpus, correct-a-record, handle-a-withdrawn-record]
tags: [identity, dedupe, doi, site]
---

# ADR-0042 — The concept DOI is the record; version DOIs merge into it

## Status

**Accepted.**

## Context

Reported from use, alongside the three classification issues:

> "If you look at the records for 'Perspectives on Wind Lidar Digitalisation' as
> an example, we find that a record can have multiple DOIs… there's one DOI that
> Zenodo gives for latest, and other DOIs for different versions/revisions. We
> want to deduplicate that (but keeping a record of the deduplication so we can
> always show it later if we need to)."

Exactly right about the cause. Zenodo mints a DOI for every deposited version
**and** a *concept* DOI that always resolves to the latest. The Zenodo adapter
already catalogues the concept (`zen-02`). But **DataCite's index carries the
version DOIs**, so an artifact that Zenodo listed under its concept and DataCite
found under a version arrived as two identity keys, and both were listed: one
title, two DOIs, two cards. There were 24 such pairs in a 330-record catalogue.

Two things were already true, and neither was being used:

1. **The reconciler had found every pair** — as *fuzzy* proposals scoring 1.00
   on title, first author and year, which it correctly refuses to auto-apply
   (`dc-08`). But this is not a guess: DataCite **states** `IsVersionOf`. The
   evidence was sitting in `related_identifiers` and no rule looked at it.
2. **The merge machinery already did what was asked.** `harvest/dedupe.py`
   expresses a merge as two `annotated` events — the primary gains the
   secondary's URLs and a cross-link, the secondary gains `local.suppressed` —
   and its own docstring promises the secondary stays "retained, citable, and
   out of the listings". That is precisely "deduplicate but keep a record of it
   so we can show it later", and it was already reversible and auditable.

What was missing was the join rule, and the half of the promise the *site* owed:
`catalogue()` returned every record, suppressed or not, so nothing was ever
actually out of the listings.

## Decision

### 1. `IsVersionOf` / `HasVersion` is an automatic join, concept primary

`VERSION_OF_RELATIONS` and `HAS_VERSION_RELATIONS` join a concept/version pair
at confidence 0.99, kind `version-pair`, from either direction. **The concept
DOI is always the primary**, because it is the identifier that resolves to the
latest version and therefore the one worth citing; listing a version DOI instead
pins the catalogue to a snapshot that the next release silently ages.

It is automatic — not a proposal — because the relation is *stated by DataCite*,
not inferred from a title. That is the same standard `x-20` (a resolved DOI
badge) and `x-22` (a declared `IsPreprintOf`) already meet. Fixture `x-12`.

Applying it collapsed 24 duplicate pairs and dropped the fuzzy proposal queue
from 24 entries to 5 — and the five that remain are genuinely different works
that a human should look at, which is what that queue is for.

### 2. Merged away is not deleted, and the site now honours the difference

`catalogue()` returns the records the catalogue *lists* and excludes suppressed
ones. A new `allEntries()` returns everything and is what *builds* pages, so the
merged-away record keeps:

- its page,
- its URL,
- its citation,
- a link to the record it was merged into, and a link back from that record,
- the full evidence in the event log, permanently and reversibly.

Somebody may already have cited the version DOI, and a catalogue that breaks
citations has failed at its one job (ADR-0027). Only the listing loses it. A
`MergedBanner` on the page explains what happened and where the artifact is
listed, so a visitor who arrives at the old URL is never confused about why the
page looks orphaned.

The sitemap and the DCAT catalogue use `catalogue()` — a duplicate should not be
advertised to search engines — while the page generator uses `allEntries()`.

## Consequences

**Good**

- One artifact, one card. The reported symptom is gone, and the 24 pairs behind
  it with it.
- Nothing was deleted and nothing became uncitable, which is the constraint the
  request itself put on the fix.
- The fuzzy queue is now short enough to actually be read by a human once a
  month, which is what makes it useful.
- The reconciler's promise and the site's behaviour finally agree.

**Costs**

- The listed record count drops visibly — 344 records, 291 listed — and anyone
  comparing to last week's number needs this ADR to explain it.
- The catalogue still holds identities keyed on version DOIs. Suppressing them
  is the honest fix given an append-only log where identity keys are file names
  and citable URLs (ADR-0037); re-keying them would break exactly the citations
  this ADR exists to preserve. The consequence is that `data/records/` contains
  more records than the site lists, and every consumer has to respect
  `suppressed`.
- `IsVersionOf` occasionally points at something that is *not* a Zenodo concept.
  The join is symmetric and evidence-based rather than Zenodo-specific, which is
  deliberate — but if a source ever uses the relation loosely, this becomes a
  wrong automatic merge. A wrong merge is reversible by appending the opposite
  annotation, and shows up as a suppressed record with a visible banner rather
  than as a silent disappearance.

## Source

`harvest/dedupe.py` (`VERSION_OF_RELATIONS`); `site/src/lib/catalogue.ts`
(`allEntries` vs `catalogue`); `site/src/components/MergedBanner.astro`;
fixture `x-12`; reported alongside
[GitHub issues #1–#3](https://github.com/weid-ost/iea-wind-data-catalogue/issues).
