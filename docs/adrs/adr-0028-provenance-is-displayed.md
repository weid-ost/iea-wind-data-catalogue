---
type: adr
id: ADR-0028
title: Provenance display — machine-inferred fields are visibly marked
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0024-the-llm-boundary, adr-0025-the-extraction-cache-is-committed, adr-0039-design-system, record-format, run-the-a11y-gate]
tags: [provenance, trust, site]
---

# ADR-0028 — Provenance display: machine-inferred fields are visibly marked

## Status

**Accepted.** Enforced by the data model, not by convention.

## Context

The audience is FAIR-literate — Task 43's remit is literally cataloguing, data
standards and open tooling. In that audience, being honest about which metadata
is inferred is the difference between a credible catalogue and one that gets
dismissed after someone spots a confidently wrong abstract. Once dismissed, it
does not come back.

The catalogue mixes three epistemic grades in the same record: a title read from
Zenodo's API, a licence mapped through a lookup table, and a resource kind a
model guessed from a task page. Presenting all three identically is a lie by
omission.

## Decision

**Every field carries its own provenance, and `llm` provenance renders visibly.**

1. `harvest.models.FieldProvenance` records `extraction_method` — `api` |
   `pattern` | `llm` — per field, plus `source_system`.
2. **`llm` requires `model` and `prompt_version`.** The model raises a
   `ValueError` if they are missing; this is not a lint, it is impossible to
   construct an unattributed LLM field. `confidence` is strongly expected too.
3. `pinned: true` appears on a field whose Tier-3 extraction a human has pinned
   ([[adr-0038-source-metadata-is-never-updated-only-annotated]] §4.3).
4. Provenance is carried into the record as `extras.provenance`, a JSON object
   string keyed by field name — [[record-format]] §4.4.
5. **The site must render a visible "machine-inferred" badge on every field
   whose `extraction_method` is `llm`** (fixture `x-05`). The design system
   reserves **violet exclusively for machine inference**, so "the model guessed
   this" is recognisable at a glance across the whole site
   ([[adr-0039-design-system]]).
6. Badges are **outline badges** — 1px coloured border, coloured text,
   transparent background — and always carry an icon **and** a text label:
   never colour alone. Icons are `aria-hidden`; the text is the accessible name.
7. Records surfaced by low-confidence extraction stay visible rather than being
   hidden; the badge is the mitigation, not suppression.

## Consequences

**Good**

- A reader can tell, field by field, what to trust, and the honest answer for
  most fields is "an API said so".
- It makes the LLM boundary auditable from the outside: if violet appears on a
  field of a tier-1 record, [[adr-0024-the-llm-boundary]] has been broken and it
  is visible on the page.
- It converts "we used AI" from a liability into a demonstration of care.

**Costs**

- Every renderer must decode `extras.provenance` and every component that
  displays a field value must be capable of carrying a badge — a real constraint
  on the component API, and the reason the provenance badge is one of the ~10
  components in the gallery.
- Violet is spent. It cannot be reused for anything else in the design system.
- The badge occupies layout in already dense metadata lists. The design system's
  answer is `<dl>` definition lists and typographic hierarchy rather than boxes.

**Checkable**, in two halves, because the claim has two halves.

- *The record side.* `fixtures/cross-cutting/x-05-low-confidence.json` holds
  fields the model extracted at 0.42 and 0.38 beside one pattern-extracted
  field, and `tests/test_crosscutting.py` asserts the badge lands on exactly the
  two `llm` fields, that neither is hidden, and that `iea_task` carries no badge
  at all.
- *The rendered side.* `/dev/components` draws
  `fixtures/rendering/rep-05-llm-inferred.json` — the same case as a record page
  — so the badge is audited by the a11y gate along with everything else
  ([[run-the-a11y-gate]]).

Until the reconciliation pass, this section named a fixture `x-05` that did not
exist in any directory (site-05, fixture-compliance-04). It does now, and
`tests/test_fixtures.py::TestTheCatalogueMatchesTheTree` fails if a named
fixture goes missing again.

## Source

`plans/02-static-plan.md` §2.3, §8 (ADR-0028); `design/design-system.md` §2.3;
`harvest/models.py::FieldProvenance`; `harvest/CONTRACT.md` §7;
`transcript/conversation-record.md` turns 3, 13.
