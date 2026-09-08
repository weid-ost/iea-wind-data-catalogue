---
type: adr
id: ADR-0040
title: The catalogue's vocabulary is defined once, and shown at two levels
status: accepted
date: 2026-09-08
deciders: [project author (OST)]
related: [adr-0021-canonical-record-is-a-ckan-package-dict, adr-0023-search-via-pagefind, adr-0028-provenance-is-displayed, adr-0038-source-metadata-is-never-updated-only-annotated, adr-0041-a-mapping-improvement-must-reach-the-existing-corpus, adr-0042-the-concept-doi-is-the-record, record-format, correct-a-record, adr-0043-what-belongs-in-the-catalogue]
tags: [vocabulary, classification, record-format, site, definitions]
---

# ADR-0040 — The catalogue's vocabulary is defined once, and shown at two levels

## Status

**Accepted.**

## Context

A week after the prototype went live an IEA Wind board member filed three
issues, and all three were the same complaint in different clothes: *the words
on a record do not mean anything precise, and where they do, the catalogue is
not using the precise ones.*

> **#1 Clarify "Resource kind" definitions.** "Differentiate between 'software'
> and 'research software'… we thought we could use the definition of 'research
> software' from [FAIR4RS]. What is meant by a 'publication'? Can we use
> 'Journal article', 'Conference paper', etc.? What is meant by 'report'? Can
> you use 'Recommended Practice', 'Best Practice' and 'Technical Report', which
> are official IEA Wind publication types? How is 'other' defined, everything
> that's not one of the other things? Maybe 'presentation' could be added?"

> **#2 Clarify "Source".** "When I look through the records with source
> iea-wind.org, most of them actually have somewhere else as the source, usually
> Zenodo or OSTI."

> **#3 Clarify "Access unknown".** "Some publications have been classified as
> 'Access unknown' although they are freely available on Zenodo."

The data bore every point out. `other` held 62 records that were almost entirely
kickoff meetings, seminar series, minutes and posters. `software` held 58 that
mixed `windIO`, `OpenOA` and the FAD-Toolset with `api-evangelist/sunedison` and
`nuclear_vs_wind_solar_cleanliness`. And the same IEA Wind publication type was
filed three different ways: "IEA Wind TCP Recommended practice 13" as a
`report`, "Recommended Practice: Ontology creation…" as a `publication`, and
"Ontologies for wind energy domain experts – Recommended Practice" as `other`.

**The specific value was never lost — only unused.** Every adapter already
preserves the upstream type verbatim in `source.extra`: DataCite's
`types.resourceType` says "Project deliverable", "Conference Paper", "Technical
Report"; Crossref's `type` says `journal-article`; OSTI's `product_type` says
"Technical Report". The adapters flattened all of it into six values at map
time and the richer string sat unread in the event log.

Two facts shaped what could be done about it:

1. **Re-harvesting would not have helped.** Change detection is by source key
   (ADR-0026): an unchanged upstream record emits no event, so no adapter
   mapping change ever reaches records already harvested. That problem is real
   and is answered separately, in [[adr-0041-a-mapping-improvement-must-reach-the-existing-corpus]].
2. **`resource_kind` is a CKAN field.** Its six values are fixed by the
   promotion contract (ADR-0021) and by every `/catalogue?kind=` URL already
   published.

## Decision

### 1. `vocabulary.yaml` — the terms are defined once, at the repository root

A new register beside `sources.yaml`, `groups.yaml` and `organizations.yaml`. It
holds every controlled value the catalogue shows a reader, each with a
**label**, a **definition**, and — where the term is not ours to define — the
**authority** that defines it.

Three things read it and nothing else defines a term:

| Reader | Uses it for |
|---|---|
| `harvest/resource_types.py` | which values are legal, and the derivation |
| `site/src/lib/vocabulary.ts` | the label and the tooltip on every chip |
| `site/src/pages/about.astro` | the **Definitions** section, rendered from the file |

The tooltip on a chip and the paragraph on the About page are now *the same
sentence from the same file*. They used to be two hardcoded copies, which is
exactly how a chip and the page explaining it drift apart. `check_vocabulary()`
fails the test suite if the code can produce a value the register does not
define: **a term that cannot be defined has no business on a chip.**

Where a definition is quoted, it is quoted:

- **Research software** — FAIR4RS: *"source code files, algorithms, scripts,
  computational workflows and executables that were created during the research
  process or for a research purpose"*, as against *"software in research"* for
  components not created with research intent. This is the definition the issue
  asked for.
- **Recommended Practice / Best Practice / Expert Group Report** — IEA Wind's
  own wording from iea-wind.org, verbatim.

### 2. Two levels: `resource_kind` stays coarse, `resource_type` is specific

`resource_kind` is unchanged: six values, the wide net, the CKAN field, the
existing facet. A new **derived** extra, `resource_type`, carries the specific
value — `journal-article`, `recommended-practice`, `research-software`,
`presentation`. Twenty-five values, each with a declared parent kind.

Both are filterable, as separate Pagefind facets, because "show me the reports"
and "show me the Recommended Practices" are both things a reader asks. On a
record page the pair renders together — **Report › IEA Wind Recommended
Practice** — so a reader can widen from the specific value to its parent in one
click. On a card only the specific chip appears: it implies its parent, its
tooltip names it, and two classification chips per card is noise.

`software` splits into `research-software` and `other-software` **under the same
kind**, so "all software" still finds both and either can be asked for alone —
which is what the request asked for, and the same shape the publication types
use.

### 3. It is derived at materialise time, not at harvest time

`harvest/resource_types.py` is a pure function over the resolved record. It runs
in `build_extras()` alongside `map_license()` and `infer_owner_org()`, which
already work exactly this way. **Deleting `data/records/` and running
`make materialize` reclassifies the whole catalogue offline**, with no network
and no new events — which is the only reason this could be applied to a corpus
whose sources have not changed.

Order of evidence, most specific first:

1. **An IEA Wind publication type in the title.** No upstream vocabulary can
   express one; they are IEA Wind's own types and the title is the only place
   they appear. Matched conservatively — the phrase must sit in *type position*
   (IEA Wind adjacency plus a number, separator or "on"; or opening the title;
   or closing it after a separator). "Definition of Best Practice for Testing
   Icephobic Surfaces" is a journal paper *about* best practice and must not
   match, and does not.
2. **The source's own vocabulary**, most informative first: Zenodo (the only one
   with a *subtype*), then OSTI, Crossref, DataCite. This is a different
   question from precedence (ADR-0026): precedence settles whose *value* wins,
   this settles whose *vocabulary* is most specific.
3. **Research-software provenance** for anything already known to be software.
4. **The generic child of the recorded kind**, so every record with a kind gets
   a type and "unspecified" is a stated fact rather than a gap.

**Where the specific value implies a different parent, the derived parent
wins.** A "Project deliverable" the adapter flattened to `publication` is grey
literature, and `report` is where a reader looking for grey literature goes. The
specific signal is better evidence than the flattening that discarded it.

### 4. Research software is decided on evidence, not judgement

FAIR4RS draws the line at *intent*, and intent is in no payload. Three signals
travel with a record and a reader can check all three: **it has a DOI**
(somebody deposited it as a citable research output), **it is attributed to an
IEA Wind Task**, or **it came from a research repository** (Zenodo, OSTI,
DataCite) rather than a bare code host. Everything else is `other-software`.

That is what separates `windIO` and `OpenOA` from the wind-adjacent company
repositories a GitHub topic sweep also finds. It is a rule about evidence, not a
verdict on quality, and the About page says so in those words.

### 5. `source.extra` merges across systems, key by key

The enabling change, and a bug in its own right. `extra` is a **bag of fields**,
not one value, and each source fills it with keys only that source has. Composing
several systems by wholesale replacement meant the highest-precedence system's
bag *deleted* every other system's — so a record scraped by both DataCite
(precedence 10) and Zenodo (30) kept `datacite_types` and silently lost
`zenodo_resource_type`, the single most specific signal in the catalogue.

`extra` is now merged key by key, with precedence deciding only where two systems
use the *same* key. Everything else in `_compose_source` is unchanged: scalars
still go to the highest-precedence system, set-valued fields still union. Fixture
`x-11`.

### 6. "Source" and "Published at" are different questions, and both are answered

Issue #2 is not a data bug; it is a labelling one. `source_system` answers *which
of our seven adapters read this record* — genuine and useful provenance. A reader
looking at "Source: iea-wind.org" on a Zenodo deposit is asking a different
question: *where is the thing?*

The record page now answers both, in that order. **Published at** lists every
distinct host derived from the record's own URLs, DOI first, as outbound links —
that is the link to follow for the files. **Harvested from** keeps the source
badges and the report-an-issue links, under a sentence saying plainly that it
means where the description came from and not where the artifact is published.
Both terms are defined in `vocabulary.yaml` and appear in the Definitions
section.

## Consequences

**Good**

- `other → other` fell from 54 records to 3; the rest resolved to presentations,
  posters and meeting records. `report` gained Technical Report, Recommended
  Practice, Best Practice and Project deliverable as filterable values.
- Every term a reader meets has a definition, in the same words, in three
  places, enforced by a test.
- IEA Wind's own publication types appear in a catalogue of IEA Wind's own
  output, which is the least it could do.
- Adding a value is a one-file change: `vocabulary.yaml` gains an entry and the
  label, the tooltip, the About page and the gallery all pick it up.

**Costs**

- Two classification fields where there was one. `resource_kind` is now derived
  rather than copied, so a record's kind can change without its source changing
  — which is the point, but it means the field is no longer a verbatim echo of
  what one adapter said. The event log still holds what each adapter said.
- The IEA-title matcher is deliberately conservative and therefore misses real
  cases: "IEA Wind Task 32: best practices for the certification of…" stays
  generic. A curator can annotate one (ADR-0038); a looser matcher would
  relabel other people's journal papers, which is worse.
- Twenty-five values is a vocabulary somebody has to maintain. The gate that
  every value carries a definition is what stops it rotting into a second
  "Other".
- The research-software rule is evidence, not truth. A genuinely research-born
  repository with no DOI, no Task attribution and no deposit reads as
  `other-software` until one of those appears. `CITATION.cff` is the natural
  fourth signal, and the GitHub adapter does not preserve it yet.

## Source

`vocabulary.yaml`; `harvest/resource_types.py`; `site/src/lib/vocabulary.ts`;
`schema/ckan-scheming.json`; fixtures `x-11`, `zen-13`;
[GitHub issues #1, #2 and #3](https://github.com/weid-ost/iea-wind-data-catalogue/issues),
filed by an IEA Wind board member on 2026-09-08.
