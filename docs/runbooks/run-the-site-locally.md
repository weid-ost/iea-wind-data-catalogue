---
type: runbook
id: RUN-run-the-site-locally
status: current
date: 2026-08-31
related: [adr-0032-site-framework-astro, adr-0023-search-via-pagefind, adr-0036-component-architecture-and-the-gallery, adr-0034-toolchain-pinning-and-no-auto-updates, run-the-a11y-gate]
tags: [runbook, site, astro]
last_executed: 2026-09-01
---

# Runbook — run the site locally

**Goal:** build and preview the catalogue site, including the Pagefind index and
the `/dev/components` gallery.
**Governed by:** [[adr-0032-site-framework-astro]],
[[adr-0023-search-via-pagefind]],
[[adr-0036-component-architecture-and-the-gallery]].

---

## 0. Status

**Implemented.** `site/` exists and every command below was executed on
2026-09-01. Node is pinned to **22.21.1** (`site/.nvmrc`, and `engines` in
`site/package.json`).

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

`npm run build` runs, in order: the token compile
(`design/build-css.mjs` + the font sync), `astro build`, **Pagefind over
`dist/`**, the token-discipline grep, the proof that the Zod gate still
refuses fixture `x-08-ckan-invalid`, and the URL check
(`scripts/check-urls.mjs`). One command produces a complete, searchable,
checked site.

The URL check exists because the base path is the one thing on a project-site
deployment that nothing else notices when it is wrong: `Astro.site` already
contains `/iea-wind-data-catalogue`, so resolving a pathname against it with the
leading slash stripped emits the prefix **twice**. That shipped once — every
`<link rel="canonical">` pointed at a 404 — and neither the a11y gate, nor
Pagefind, nor the test suite caught it, because nothing on the site follows its
own canonical link. The check now asserts that no built URL repeats the base
segment and that every page's canonical is absolute and matches that page's own
path.

Output: `site/dist/`.

## 2. Develop

```sh
cd site
npm run dev        # Astro dev server, hot reload
npm run preview    # serve the built dist/ exactly as Pages will
```

Use `preview` whenever search matters: the Pagefind index is a **build**
artifact, so it does not exist under `dev`. The island degrades to the
server-rendered list there rather than erroring.

`npm run preview` is `scripts/serve.mjs`, a twenty-line static server, **not**
`astro preview` — Astro 7's preview is a daemon that outlives the process that
started it, which is the wrong lifecycle for the a11y gate to drive.

## 3. What to look at

| URL | Why |
|---|---|
| `/` | the freshness banner from `state/last-run.json` → `finished_at`; the pending backlog beside it |
| `/record/<slug>/` | a canonical record: provenance badges, task chips, source links, JSON-LD |
| `/search/` (or the home search island) | Pagefind, and its six filters |
| `/browse/` | the static, paginated, no-JavaScript path |
| `/dev/components` | **the gallery** — every component in every fixture state |
| `/catalog.jsonld`, `/sitemap.xml` | the machine-readable exports |

Note the base path: the site deploys to a GitHub Pages *project* site, so the
local URLs are `http://localhost:4321/iea-wind-data-catalogue/…`. A custom
domain is `SITE_BASE=/ SITE_URL=https://your.domain/ npm run build`.

Records are loaded by **glob from `../records/*.json`**, so if the site shows
nothing, run `make materialize` first ([[materialize-and-validate]]).

**Before the first harvest** `records/` is empty, and rather than build an empty
site the catalogue falls back to `fixtures/rendering/` and says so on the
homepage. One real record and the fixtures disappear.

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
`cd site && npm run gates`. `npm run gates` is the token-discipline grep, the
`x-08` gate proof, the URL check, and `pa11y-ci` over the URL list in both
themes ([[adr-0039-design-system]] §§7–8). It audits `dist/`, so **build
first**.

---

**Last executed:** 2026-09-01 — `make site` from a clean tree (`node_modules/`,
`dist/`, `.astro/` and the generated `tokens.css` all deleted first):
`npm ci` + `npm run build` green: **36 page(s) built**, Pagefind **indexed 30
pages** and **6 filters**. `npm run gates` green: 14/14 URLs, both themes,
0 errors.

Those numbers move with `records/`, so read them as a shape rather than a
constant: 30 indexed pages is one per record, 36 built pages is those plus the
home, search, browse, about and gallery pages. The sixth filter is
`institution`, which `src/lib/facets.ts` derives from `pkg.owner_org`; it was
dormant for a while because no record carried one, and the facet list drops any
facet with no values rather than rendering an empty control. All 30 records now
carry an `owner_org`, so it is back.

Verified in a headless browser against the built index, not merely built:
search returns hits and narrows, the facets are real checkboxes and filter,
filter state round-trips through the query string, a result links to a real
page, the zero-results state (`r-07`) is announced through the polite live
region, `?theme=` forces both themes and they render differently, the toggle
cycles auto → light → dark with `aria-pressed` and `localStorage`, the stored
choice is applied before first paint, and `prefers-reduced-motion` leaves zero
animated elements.

Two things this run changed. The canonical-URL doubling described in §1 was
found and fixed, and `scripts/check-urls.mjs` now guards it. And the gallery
had been hand-reproducing the search island's empty state instead of rendering
the island; it now renders the **real** `<SearchIsland>`, so `r-07` comes out of
the component's own template and cannot drift from it.

**Known limitation:** the copy-citation button's success path cannot be
exercised headlessly — Chrome denies `navigator.clipboard.writeText` outright
(`NotAllowedError`, even with permissions overridden and the page focused), so
what the smoke test verifies is the *fallback*: the button stays a real button
and announces "Could not copy — select the text and copy it manually" through
its live region. The success path is one of the manual passes.
