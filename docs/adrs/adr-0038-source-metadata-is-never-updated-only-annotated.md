---
type: adr
id: ADR-0038
title: Source metadata is never updated, only annotated
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0037-events-are-the-source-of-truth, adr-0026-change-detection-by-source-key, adr-0027-withdrawn-records-are-retained, adr-0028-provenance-is-displayed, correct-a-record, record-format]
tags: [data-model, curation, trust]
---

# ADR-0038 — Source metadata is never updated, only annotated

## Status

**Accepted.** The author's proposal in turn 12, endorsed with one refinement.

## Context

Turn 12, having just corrected the change-detection model:

> "Also, saying that, maybe we should disallow *update* of metadata entirely. So
> the original is always authoritative. We can *add* items of metadata, useful
> to our own catalogue - which then, if they appear when re-scraped get
> overwritten. What do you think?"

This is the right call, and it deletes most of what the reconciliation design
used to contain.

## Decision

**The original is always authoritative. The catalogue displays what the source
says, verbatim as of the last scrape. Local intervention is additive only.**

Three reasons it is right:

1. **It matches the catalogue's epistemic role.** This is an aggregator, not an
   authority. If a Zenodo record has a typo'd title, then "a typo'd title" is
   *what Zenodo says*, and the fix belongs at Zenodo, where the author can
   actually make it and where every other consumer benefits. A catalogue that
   edits upstream metadata forks the truth and starts a drift war it cannot win.
   Put a "report an issue at the source" link on every record so fixes flow to
   where they stick.
2. **It deletes a subsystem.** No precedence rules for human-versus-machine, no
   supersession semantics, no conflict lifecycle, no normalisation-for-comparison.
   For a project defined by handoff-to-a-stranger, removing a subsystem is worth
   more than any feature.
3. **It makes trust checkable.** Anyone can verify any displayed field against
   its source. There is never a "why does your catalogue disagree with Zenodo"
   conversation.

### The record is two namespaces

- **`source.*`** — verbatim upstream metadata. **Replaced wholesale** when the
  source key changes. Never locally editable. There is no field-level merge of
  successive scrapes: a field the source stops sending disappears from the
  record, because that is what the source now says.
- **`local.*`** — additions the sources do not provide: `iea_task` attribution,
  `resource_kind` where the source does not type it, curator notes,
  cross-record relationships, `suppressed` for noise, Tier-3 pins. **Latest
  local event wins within this namespace.**

Field lists are in [[record-format]] §3.

### The collision rule

| situation | result |
|---|---|
| only `source` has the field | source value |
| only `local` has the field | local value |
| both, **scalar** | **the source value displaces the local one**; the displaced value is retained in the event log; a `displacement` notice appears in `resolved.notices` and in the run report (fixture `x-03`) |
| both, **set-valued** | **union, never displace** — a Zenodo community adding Task 43 must not erase a hand-added Task 49 (fixture `x-04`) |

The set-valued/scalar split is the refinement added to the author's proposal,
and `iea_task` is the case that motivates it. `harvest.models.SET_VALUED_FIELDS`
declares the set once: `iea_task`, `source_urls`, `keywords`,
`related_identifiers`, `curator_notes`, `links`.

`resolve()` raises implicit displacement notices itself, so the behaviour is
correct even if nobody remembered to append a `displacement_notice` event.

### Two carve-outs, and only two

**1. Tier-3 records, where the "original" is our own inference.** An
LLM-extracted record has no authoritative structured source — the extraction *is*
the metadata. A human correcting it is not overriding an authority; they are
outranking a model's guess about our own output. Corrections there are
legitimate and are implemented as **pinned extractions**: the corrected object
replaces the cache entry and is marked `pinned`, with `pin_source_key` recording
the content hash it was made against. On a later scrape where the page's content
hash changes, **the pin holds and a `pin_notice` fires** — the page moved beneath
a human judgement, and a human decides (fixture `x-09`).

In practice this population is small: the resolve-or-drop rule means any Tier-3
record with a DOI takes its metadata from the DOI resolver, which then counts as
an authoritative source under the normal rule.

**2. Upstream that is known wrong and will not be fixed.** Display the wrong
value anyway — verbatim — with a visible **curator note** beside it: *"OST note:
the licence stated at source appears incorrect; see …"*. Both truths on the page
is better epistemics than a silent override, it is honest to a FAIR-literate
audience, and it keeps the pressure where it belongs: on the source (fixture
`x-10`).

## Consequences

**Good**

- **A noisy source key costs a redundant re-scrape and a no-op event — it can
  never clobber a human edit.** That is what makes
  [[adr-0026-change-detection-by-source-key]] affordable.
- The monthly human task shrinks to reading a short notice list rather than
  adjudicating conflicts.
- Every displayed value is checkable against its source, which is the strongest
  trust property the catalogue has.

**Costs**

- **A visibly wrong upstream value stays visibly wrong on the catalogue page.**
  That is intended, and it will occasionally look like a bug to a user who does
  not read the curator note. The note must therefore be rendered *beside* the
  value, not in a footer.
- Curators cannot fix typos, which is frustrating and correct.
- A scalar annotation can be silently superseded by a later scrape. It is not
  lost — it is in the log and it produces a notice — but the record page stops
  showing it, and the person who added it is not notified.
- The pin carve-out is a genuine exception to "the source wins", and every
  exception is a place the model can be misapplied. Pins are therefore explicit
  (`local.pinned`), auditable (`pin_source_key`), and noisy (`pin_notice`).

**Procedure.** [[correct-a-record]] works the full matrix with runnable
examples. Terminology note: the plan's `corrections/` directory was renamed
`annotations/` in turn 12, precisely because "correction" implies editing.

## Source

`plans/02-static-plan.md` §4.2, §4.3, §8 (ADR-0038); `harvest/models.py`
docstring; `harvest/events.py::resolve`; `harvest/CONTRACT.md` §5;
fixtures `x-02`, `x-03`, `x-04`, `x-09`, `x-10`;
`transcript/conversation-record.md` turn 12.
