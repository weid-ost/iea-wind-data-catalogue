---
type: runbook
id: RUN-run-the-site-locally
status: current
date: 2026-08-31
related: [adr-0032-site-framework-astro, adr-0023-search-via-pagefind, adr-0036-component-architecture-and-the-gallery, adr-0034-toolchain-pinning-and-no-auto-updates, run-the-a11y-gate]
tags: [runbook, site, astro]
last_executed: never
---

# Runbook — run the site locally

**Goal:** build and preview the catalogue site, including the Pagefind index and
the `/dev/components` gallery.
**Governed by:** [[adr-0032-site-framework-astro]],
[[adr-0023-search-via-pagefind]],
[[adr-0036-component-architecture-and-the-gallery]].

---

## 0. Status

> **`SPEC — not yet implemented`.** `site/` does not exist in this checkout. The
> commands below are the interface the site track must provide; they are taken
> from the `Makefile` and `harvest/CONTRACT.md` §§11–12 and are binding on that
> track.

## 1. Build

```sh
make site
```

which is exactly:

```sh
cd site && npm ci && npm run build
```

`npm ci`, never `npm install` — the build installs from the committed
`package-lock.json` and nothing else
([[adr-0034-toolchain-pinning-and-no-auto-updates]]). The Node version is
pinned; check `site/.nvmrc` or the `engines` field and match it.

`npm run build` must run `astro build` **and then** Pagefind over `dist/`, so
one command produces a complete, searchable site.

Output: `site/dist/`.

## 2. Develop

```sh
cd site
npm run dev        # Astro dev server, hot reload
npm run preview    # serve the built dist/ exactly as Pages will
```

Use `preview` whenever search matters: the Pagefind index is a **build**
artifact, so it does not exist under `dev`.

## 3. What to look at

| URL | Why |
|---|---|
| `/` | the freshness banner from `state/last-run.json` → `finished_at`; the pending backlog beside it |
| `/record/<slug>/` | a canonical record: provenance badges, task chips, source links, JSON-LD |
| `/search/` (or the home search island) | Pagefind, and its filters |
| `/dev/components` | **the gallery** — every component in every fixture state |

Records are loaded by **glob from `../records/*.json`**, so if the site shows
nothing, run `make materialize` first ([[materialize-and-validate]]).

## 4. Requirements the site build must satisfy

These are not preferences; they are the contract between the site and the rest
of the system.

**The record boundary**

- Records load via `glob({ pattern: '*.json', base: '../records' })` in
  `site/src/content.config.ts`.
- **The Zod collection schema *is* the `validate-ckan-compat` gate**: slug
  rules, tag charset, licence register. A malformed record **fails the build**.
  Fixture `x-08-ckan-invalid` exists to prove it does.
- Astro never writes into `records/`, and **no framework-specific field ever
  enters the record format**.

**Per-record rendering**

- `extras.provenance` → a **violet machine-inferred badge** on every `llm` field
  ([[adr-0028-provenance-is-displayed]], fixture `x-05`).
- `extras.lifecycle_state == "withdrawn"` → the withdrawal banner,
  `role="status"`, at the top of `<main>`. **Keep the page and the URL.**
- `extras.curator_notes` → rendered *beside* the value they annotate, with the
  upstream value still shown verbatim. Both truths on the page (`x-10`).
- `extras.access_status` → availability badge. Never imply a download that
  requires an account or is embargoed.
- `extras.source_urls` → "view at source" links, one per contributing system.
- `extras.iea_task` → task chips, display names resolved from `groups.yaml`.
- Record URLs are `/record/<slug>/` where `<slug>` is `package.name` — stable
  across a retitle.
- schema.org `Dataset` JSON-LD on every record page, present in the **built
  HTML**, not injected by script.

**Search**

- Pagefind runs after `astro build`, over `dist/`.
- Filters come from `data-pagefind-filter` attributes on the record page:
  **task, resource kind, year, licence, source system, institution**.

**The gallery**

- `/dev/components` imports every component and renders it against **real
  records from `records/` plus the pathological `fixtures/` set**.
- It is `data-pagefind-ignore` **and** `noindex`.
- Every component appears in it. A component that does not is not finished.

**Zero JS by default**

- Content is plain HTML. Vanilla custom elements only for genuine interactivity
  — the search island, `<theme-toggle>`, a copy-citation button — applied as
  progressive enhancement over content already in the HTML.

**Theming**

- Tokens compile to `site/src/styles/tokens.css`, generated and never
  hand-edited.
- A `?theme=light` / `?theme=dark` query parameter must force the theme, because
  the a11y gate runs the URL list twice ([[run-the-a11y-gate]]).

## 5. Gates before you push

```sh
make gates
```

which runs `make test`, `make validate`, `make build-tokens`, and then
`cd site && npm run gates`. `npm run gates` must include, at minimum, the
token-discipline grep and `pa11y-ci` in both themes
([[adr-0039-design-system]] §§7–8).

---

**Last executed:** never — `site/` does not exist yet. `make site` and
`make gates` are declared in the `Makefile` and will fail until the site track
lands.
