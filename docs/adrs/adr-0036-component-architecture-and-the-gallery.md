---
type: adr
id: ADR-0036
title: Component architecture — Astro components for content, custom elements only for interactivity, a gallery instead of Storybook
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0023-search-via-pagefind, adr-0032-site-framework-astro, adr-0039-design-system, run-the-a11y-gate, run-the-site-locally]
tags: [site, components, tooling]
---

# ADR-0036 — Component architecture, and a gallery instead of Storybook

## Status

**Accepted.** The author's instinct in turn 10 was web components plus
Storybook; this ADR takes half of it and pushes back on the other half, which
the author accepted.

## Context

Turn 10:

> "Next up is storybook and webcomponents. I don't want a vomit of html
> everywhere - encapsulated webcomponents that can be properly storybooked will
> be best practice here. The storybook itself will be the most vulnerable to
> dependency rot, but as it's an ancillary dev tool I'm less concerned about
> that. An alternative would be a rudimentary components gallery that we could
> develop against - your call."

The instinct is right — encapsulated, individually reviewable components beat a
sprawl of inline markup — but one obvious mechanism would quietly undermine the
biggest discovery win of going static.

## Decision

### Astro components for content; custom elements only for interactivity

**Do not build record pages out of custom elements.** A custom element only
populates when its class definition loads and `customElements.define()` runs, so
the built HTML is a shell and the content arrives via JS. That costs two things
this project has explicitly banked on: the zero-JS output property, and —
more importantly — reliable indexing by **Google Dataset Search**, which
[[adr-0023-search-via-pagefind]] identified as the single biggest discovery win.
Content that requires script execution to exist is the wrong trade for a
catalogue whose entire purpose is findability.

**Astro components (`.astro`) already give the encapsulation.** They are
composable, prop-driven, their styles are automatically scoped per component,
and they render at build time to plain HTML. That solves "no vomit of HTML
everywhere" without shipping a runtime.

**Reserve custom elements for genuine client-side interactivity** — realistically
the search/filter island, a copy-citation button, a filter chip bar, and the
`<theme-toggle>`. Write them **vanilla; skip Lit.** For two or three widgets,
`customElements.define()` with a `<template>` is enough, and vanilla custom
elements are the most rot-proof interactive technology available *because they
are a platform standard rather than a framework* — there is no version to
migrate, ever. Apply them as **progressive enhancement over content already in
the HTML**, so a JS failure degrades to a working page.

### A gallery, not Storybook

Three reasons, in order of weight:

1. **Astro components are not a first-class Storybook target.** Storybook's
   renderers cover React, Vue, Svelte, web components and plain HTML — `.astro`
   is not among them. Adopting Storybook would push you to write components *as*
   web components purely to satisfy the tool, which is exactly the mistake above.
   The tool would be driving the architecture.
2. **The component count does not justify it.** Record card, record detail,
   provenance badge, source badge, task chip, freshness banner, pagination,
   facet chip, search island — roughly ten. Storybook's value scales with
   component count and team size.
3. **It is the heaviest dependency tree in the repository.** Being a dev tool
   bounds the damage but does not eliminate it: a dev tool that will not install
   is a dev tool nobody uses.

**Instead: a `/dev/components` page in the site itself.** One Astro page
importing every component and rendering it against fixture data. Roughly a
hundred lines, zero new dependencies, builds with the same command as everything
else, reviewable in a browser exactly like Storybook.

It is also *better* in one specific way: the gallery renders **real records
pulled from `records/`, plus the deliberately pathological `fixtures/` set** —
missing DOI, 300-character title, no description, withdrawn record,
low-confidence LLM-extracted fields, a record belonging to five tasks at once.
That exercises the actual data shape, which is where the bugs will be. Storybook
stories use synthetic args.

Two build details, both mandatory: the page is **`data-pagefind-ignore`** so it
stays out of the search index, and **`noindex`** so it stays out of Google.

**Keep the discipline without the tool.** The valuable part of Storybook is the
constraint it imposes: components that are isolated, prop-driven and renderable
from fixtures alone. Hold that line and Storybook stays cheap to adopt later if
more than one front-end contributor ever appears.

## Consequences

**Good**

- Zero-JS content output is preserved, and with it Dataset Search indexing.
- The gallery doubles as the a11y gate's most efficient target: **one URL audits
  the entire component inventory** in both themes
  ([[run-the-a11y-gate]], [[adr-0039-design-system]] §7).
- The gallery is also the living styleguide layer of the design system's build
  order, so there is one artifact rather than two.

**Costs**

- No addon ecosystem, no interaction tests, no visual-regression tooling out of
  the box. Accepted at this scale.
- The gallery must be *maintained*: **every component appears in the gallery**
  is a working convention in `CLAUDE.md`, and a component that is not in it is
  not finished.
- Component isolation is a discipline rather than an enforced boundary, since
  nothing stops an `.astro` component reaching for global state.

## Source

`plans/02-static-plan.md` §3.7, §8 (ADR-0036); `design/design-system.md` §8;
`harvest/CONTRACT.md` §11; `transcript/conversation-record.md` turn 10.
