---
type: adr
id: ADR-0024
title: The LLM boundary — Tier-3 HTML only; never where an API exists; identifiers always verified
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0025-the-extraction-cache-is-committed, adr-0028-provenance-is-displayed, adr-0030-llm-access-via-github-models, adr-0031-the-harvest-never-fails-on-llm-unavailability, adr-0035-no-vendor-sdk, drain-the-pending-extraction-queue]
tags: [llm, harvest, integrity]
---

# ADR-0024 — The LLM boundary

## Status

**Accepted.** `plans/02-static-plan.md` §2.3 states it as "the boundary matters
more than anything else in this document". Treat it that way.

## Context

Turn 3 asked for "(preferably) an AI based harvester". The honest answer is
*partly*. An LLM genuinely earns its place in four situations:

- **Heterogeneous HTML** — the task microsites, where the alternative is twenty
  bespoke parsers that each break silently. This is the real win and it is a big
  one.
- **Classification** — `resource_kind`, task attribution, subject tags against a
  controlled vocabulary.
- **Duplicate adjudication** — "are these the same artifact?" where there is no
  shared DOI, as a *proposer*, with the decision recorded.
- **Source discovery** — "does this task have a GitHub org, a Zenodo community,
  a data page?"

And it is actively harmful in two:

- **Anywhere a structured API exists.** Zenodo, DataCite, Crossref, GitHub and
  OSTI all return clean JSON. Sending that through a model adds cost, latency
  and hallucination risk for zero benefit.
- **Identifiers, dates, names, licences, URLs.** A hallucinated DOI is silent
  corruption: it looks exactly like a real one, and a catalogue whose entire
  product is *findability* is destroyed by pointing people at things that do not
  exist.

## Decision

**Tier 1 is deterministic, always. Tier 3 is the only tier allowed a model.**

`sources.yaml` declares the tier per source and the tiering is not advisory:
`datacite`, `crossref`, `zenodo`, `osti` and `github` are tier 1; `wdh` is
tier 2; `ieawind` is tier 3.

Five rules, all enforceable:

1. **Never where a structured API exists.** No model call may be made on a
   payload a tier-1 adapter produced.
2. **Identifiers never come from the model.** DOIs, GitHub URLs and ORCIDs are
   regexed out deterministically (`harvest.doi.extract_dois`) and passed to the
   model as *context*; the model is asked to **assign** identifiers to records,
   never to transcribe them.
3. **Resolve or drop.** Every extracted DOI is resolved against DataCite or
   Crossref (`harvest.doi.resolve_or_drop`) before the record is accepted. If it
   does not resolve, **the record is dropped and logged** — in
   `state/last-run.json` under `dropped_dois`, never silently discarded
   (fixture `iea-05`). Combined with rule 2, hallucinated identifiers become
   structurally impossible rather than merely unlikely.
4. **Structured output only.** JSON-schema-constrained responses, validated on
   receipt with pydantic. Not free-text parsing.
5. **Extraction, not generation.** Titles, DOIs, dates, resource kinds and task
   numbers are *copied* from the page; descriptions are taken verbatim from
   source abstracts rather than summarised. If the model's job is to locate and
   classify rather than to write, output is near-identical across models — which
   is what makes the two model lineages of
   [[adr-0030-llm-access-via-github-models]] an auditable footnote rather than a
   quality problem.

Three supporting requirements:

- **Feed clean text, not raw HTML.** `trafilatura` for main content, then
  `harvest.sanitize.html_to_text`. This cuts tokens by roughly an order of
  magnitude and *improves* accuracy — boilerplate nav and footers are the main
  source of spurious extractions (fixture `iea-10`). It is also the
  prompt-injection boundary: a harvested page is hostile input to a system that
  then writes records.
- **Provenance per field**, always — [[adr-0028-provenance-is-displayed]].
- **Cap calls per run.** `harvest.extract.MAX_EXTRACTIONS = 200`, so a task site
  redesign that invalidates three thousand cache entries drains over weeks
  rather than arriving as one surprise bill. The remaining backlog is reported
  in `state/last-run.json`.

Small model by default, escalating only when validation fails or confidence is
low. Temperature at minimum for stability — but determinism is not guaranteed
even so, which is why the *cache*, not the temperature, is what makes rebuilds
reproducible ([[adr-0025-the-extraction-cache-is-committed]]).

## Consequences

**Good**

- Hallucination becomes a caught error rather than silent corruption, and the
  rule that achieves it is cheap.
- The majority of records never touch a model at all, which is why
  [[adr-0031-the-harvest-never-fails-on-llm-unavailability]] is affordable.
- Being able to say "identifiers in this catalogue are resolved against a
  registry" is the difference between credible and dismissible in a
  FAIR-literate audience.

**Costs**

- Tier-3 coverage is limited by what survives resolve-or-drop. A publication
  listed as title + journal + year only (fixture `iea-07`) requires a Crossref
  title search and is accepted only on a high-confidence match, else flagged.
- Publication lists that live inside linked PDFs are **out of scope for v1** and
  the gap is recorded explicitly (fixture `iea-11`), rather than papered over.
- The boundary is the easiest thing in the system to erode. Any future change
  that sends tier-1 JSON through a model is relitigating this ADR and must say so.

**Estimated cost** (`plans/02-static-plan.md` §2.3): the first full pass over a
few thousand pages at ~4k tokens each on a small fast model lands in the **$5–20**
range; subsequent runs are pennies. See
[[adr-0030-llm-access-via-github-models]] for who pays it and how.

## Source

`plans/02-static-plan.md` §2.3, §3.4, §8 (ADR-0024); `harvest/CONTRACT.md` §10;
`harvest/extract.py` docstring; `transcript/conversation-record.md` turn 3.
