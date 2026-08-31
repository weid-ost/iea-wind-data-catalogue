---
type: adr
id: ADR-0023
title: Search — Pagefind, built at build time, filtered client-side
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
supersedes: [ADR-0007]
related: [adr-0032-site-framework-astro, adr-0036-component-architecture-and-the-gallery, adr-0039-design-system, run-the-a11y-gate]
tags: [site, search, discovery]
---

# ADR-0023 — Search: Pagefind, built at build time, filtered client-side

## Status

**Accepted.** Supersedes ADR-0007 (Solr index persistence) — there is no Solr,
because there is no CKAN.

## Context

Turn 3 asked whether a static site could be "searchable and filterable just
like CKAN". At a few thousand public records the answer is *better than CKAN*:
CKAN's faceted search does a full page reload per facet click and a Solr
round-trip per query; a static site filters in the browser in single-digit
milliseconds with no network at all.

Turn 2 had already established the scale — "hundreds, or eventually low
thousands" — which is what removes every argument for a search server.

## Decision

**Pagefind**, run after `astro build` over `dist/`.

1. The index is a **build artifact**, regenerated from `records/` on every
   build. There is no index to keep in sync and no runtime to operate.
2. Pagefind chunks its index, so the browser downloads only the fragments a
   query needs and index size stops mattering.
3. **Filters are declared in the template**, not configured separately, via
   `data-pagefind-filter` attributes rendered onto record pages. The facet set
   is: **task, resource kind, year, licence, source system, institution**.
4. **`/dev/components` is excluded** with `data-pagefind-ignore` (and
   `noindex`) so the gallery never pollutes the index.
5. Pagefind's stock UI component is good enough to ship. A bespoke UI over its
   JS API is a later nicety, not a requirement.
6. The search island is one of the few places a **vanilla custom element** is
   allowed ([[adr-0036-component-architecture-and-the-gallery]]), applied as
   progressive enhancement so a JS failure degrades to a working page.

Two discovery properties follow from being static, and both are requirements
rather than side effects:

- **Stable, citable record URLs** at `/record/<slug>/`, derived deterministically
  from the identity key so they survive every rebuild — see [[record-format]] §1.2.
- **schema.org `Dataset` JSON-LD on every record page**, so **Google Dataset
  Search indexes the catalogue**. For a discovery product whose whole purpose is
  findability, being in the place researchers actually search from is worth more
  than any feature CKAN offers. This is also why record content must exist in
  the built HTML rather than being injected by script.

## Consequences

**Good**

- No server, no index synchronisation, no reindex runbook, no Solr schema
  migration on upgrade — three of CKAN's recurring operational costs deleted.
- Facets live next to the markup they describe, so adding one is a template
  change rather than a configuration change plus a redeploy.

**Costs**

- Search quality is lexical, not semantic. At this corpus size that is fine;
  if it stops being fine, the alternatives considered were MiniSearch and Orama
  (both load one index blob up front) and DuckDB-WASM over Parquet (genuinely
  excellent for complex filtering, but more machinery than a handover artifact
  should carry).
- **Pagefind's stock UI must be audited for accessibility before shipping.**
  The design system is explicit that the live result count and the filter
  semantics need verifying, and this is the page a screen-reader user lives on:
  labelled combobox or plain labelled input, polite `aria-live` result count,
  real `<button>`s or checkboxes with state (never clickable `<div>`s), results
  as a list, and an announced zero-results state (fixture `r-07`). See
  [[adr-0039-design-system]] §6 and [[run-the-a11y-gate]].

## Source

`plans/02-static-plan.md` §2.1, §3.5, §8 (ADR-0023); `design/design-system.md`
§6; `harvest/CONTRACT.md` §11; `transcript/conversation-record.md` turns 2–3.
