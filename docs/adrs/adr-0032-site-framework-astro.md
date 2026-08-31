---
type: adr
id: ADR-0032
title: Site framework — Astro, thin, with records staying canonical JSON
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0021-canonical-record-is-a-ckan-package-dict, adr-0023-search-via-pagefind, adr-0034-toolchain-pinning-and-no-auto-updates, adr-0036-component-architecture-and-the-gallery, run-the-site-locally]
tags: [site, astro, longevity]
---

# ADR-0032 — Site framework: Astro, thin

## Status

**Accepted.**

## Context

Turn 8:

> "we need a static site generator right? would we use Astro, which I know a
> bit?"

Familiarity is a legitimate tiebreaker given the time box, but it is not the
only argument, and it should not be the deciding one for something a stranger
inherits.

## Decision

**Astro**, kept deliberately thin, with Pagefind run afterwards.

Three reasons on merits:

1. **It fits the record contract without distorting it.** Astro's content layer
   reads plain JSON off disk via a glob loader, so `records/*.json` stays the
   canonical CKAN-shaped artifact and Astro is a pure *renderer*.
   **Guard this boundary**: no frontmatter-flavoured schemas, no
   framework-specific fields, nothing that would stop the CKAN loader POSTing
   those files unmodified ([[adr-0021-canonical-record-is-a-ckan-package-dict]]).
2. **Zod content-collection schemas *are* the `validate-ckan-compat` gate.** The
   build fails on a malformed record, which is exactly the enforcement the
   promotion contract asks for, with no separate validator to write or run.
   Encode CKAN's slug rules, tag character rules and licence lookup there.
   Fixture `x-08-ckan-invalid` must fail the build.
3. **Zero JS by default.** The deployed output is plain HTML with one island for
   search. Which leads to the point that actually matters for a project that
   might sit dormant for years: **the built artifact outlives the build
   toolchain.** If Astro stops building cleanly in 2030 because Node moved on,
   the deployed site keeps serving and `records/*.json` is still the catalogue.
   Toolchain rot costs "can't rebuild until someone spends an afternoon on
   dependencies", not "the catalogue is gone".

**Longevity discipline, binding:**

- **Pin the runner image and the Node version explicitly** — `runs-on:
  ubuntu-24.04`, never `ubuntu-latest`; `actions/setup-node` with a fixed
  version, never `lts/*`. Floating references are how dormant repos break
  unattended.
- **Commit `package-lock.json`; build with `npm ci`.**
- **Keep the Astro layer thin.** Every integration is a future migration. Five
  page types need no UI framework, no CMS adapter and no image pipeline.
- **Do not auto-update dependencies** — see
  [[adr-0034-toolchain-pinning-and-no-auto-updates]].
- Keep workflow permissions scoped: `contents: write` only where the harvest
  commits, `pages: write` and `id-token: write` only on deploy. The residual
  risk is supply-chain compromise of a build-time dependency, because the build
  runs with a write-capable token; minimal dependencies plus `npm ci` from the
  lockfile is the mitigation.

Pagefind runs after `astro build`, indexing `dist/`
([[adr-0023-search-via-pagefind]]).

## Consequences

**Good**

- The record format is protected by architecture rather than by discipline: if
  Astro never writes to `records/`, it cannot corrupt it.
- Zero-JS output is what makes Google Dataset Search indexing work, which is the
  single biggest discovery win of going static.
- A rebuild failure is recoverable and bounded; a data loss would not be.

**Costs**

- An npm dependency tree in a repository that otherwise has four Python
  dependencies. Accepted explicitly, on the grounds above.
- Node is a second toolchain to install for local development. Note that
  choosing Go for the harvester would not have avoided this, since the repo
  already needs Node — see [[adr-0033-harvester-language-python]].
- Astro's conventions actively invite the thing this ADR forbids (schema-shaped
  content collections owning the data). Anyone adding a field must add it to
  `harvest.materialize.EXTRA_KEYS` and `schema/ckan-scheming.json`, not to a
  frontmatter schema.

**Alternatives considered**

- **Hugo** — maximum longevity: one pinned Go binary, no dependency tree, builds
  3,000 pages in about a second, still working untouched in a decade. Cost: Go
  templates, and emitting markdown-with-frontmatter from the harvest step. The
  author does not know Hugo (turn 9).
- **Jinja2 in the existing Python codebase** — one language, no JS toolchain,
  nothing to upgrade ever; cost is hand-rolling pagination and navigation.

Both defensible; neither worth trading against existing fluency and a hard time
box.

## Source

`plans/02-static-plan.md` §3.5, §8 (ADR-0032); `harvest/CONTRACT.md` §11;
`transcript/conversation-record.md` turn 8.
