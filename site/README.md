# site/

The Astro renderer. **It renders; it does not own the data** (ADR-0032).
`records/*.json` are canonical CKAN package dicts produced by `harvest/`; this
directory reads them by glob and never writes to them.

Operating instructions live in the docs vault — [run the site
locally](../docs/runbooks/run-the-site-locally.md) and [run the a11y
gate](../docs/runbooks/run-the-a11y-gate.md). This file is the map.

## Commands

```sh
npm ci            # never `npm install` — the lockfile is the environment (ADR-0034)
npm run dev       # tokens + astro dev. No Pagefind index: search is inert here.
npm run build     # tokens + astro build + pagefind + token grep + CKAN-gate proof + URL and render gates
npm run preview   # serve dist/ exactly as Pages will, base path and all
npm run gates     # the four gate scripts + pa11y-ci, both themes
```

`make site` and `make gates` from the repository root wrap the same things.

## Layout

```
src/
├── ckan.mjs             the Zod gate — CKAN's rules, mirrored from harvest/ckan_compat.py
├── licenses.mjs         the licence register, mirrored from harvest/licenses.py
├── safety.mjs           escaping and the URL/tag allow-lists at the render boundary
├── schema-types.mjs     resource_kind -> schema.org type, and why it is not always Dataset
├── repo-root.mjs        finds the repo root from the working directory, not from import.meta
├── content.config.ts    the glob loader; its schema IS validate-ckan-compat
├── lib/                 reading a CKAN package, the registers, last-run.json, JSON-LD, facets
├── components/          Astro for content; three vanilla custom elements for interactivity
├── layouts/Base.astro   head, skip link, nav, theme pre-paint script
├── pages/               home, search, browse, record/[name], about, dev/components,
│                        catalog.jsonld, sitemap.xml
└── styles/              tokens.css (GENERATED), fonts.css, global.css
scripts/
├── check-tokens.mjs     fails the build on a hardcoded colour or px outside tokens.css
├── check-ckan-gate.mjs  proves the Zod gate refuses fixture x-08-ckan-invalid
├── check-urls.mjs       canonicals, the base path, and unique record slugs (name IS the URL)
├── check-render.mjs     render safety and the output's promises — see below
├── sync-fonts.mjs       design/fonts/*.woff2 -> public/fonts/
├── serve.mjs            twenty-line static server for dist/, honouring the base path
└── a11y.mjs             serves dist/ and runs pa11y-ci over .pa11yci, once per theme
```

## Deployment paths

The repository deploys to a GitHub Pages **project** site today, so `base`
defaults to `/iea-wind-data-catalogue` and `site` to
`https://weid-ost.github.io/iea-wind-data-catalogue/`. Both are environment
variables; a custom domain is:

```sh
SITE_BASE=/ SITE_URL=https://catalogue.example.org/ npm run build
```

Every internal link goes through `withBase()` in `src/lib/url.ts`, so that is
the only change needed.

## Render safety

Everything in `records/` is somebody else's text. `harvest/sanitize.py` and
`harvest/urls.py` clean it on the way in; `src/safety.mjs` refuses it again on
the way out, because the renderer is what turns a string into markup:

- **JSON-LD is serialised with `jsonForHtml`, never `JSON.stringify`.**
  `stringify` does not escape `<`, so a harvested title containing `</script>`
  closed the element and the rest of it became live HTML.
- **`set:html` is always wrapped in `safeHtml`** — the tag and attribute
  allow-list of `harvest.sanitize`, re-implemented so one adapter forgetting to
  sanitise cannot ship script.
- **Every href from data goes through `safeHref`**, because escaping an
  attribute does not disarm `javascript:`.

`scripts/check-render.mjs` feeds fixture `rep-09-hostile-markup` — whose title
really does contain `</script><img src=x onerror=…>` — through all three, then
re-reads `dist/` for what must never appear. `tests/test_site_render_safety.py`
asserts the allow-lists here still equal the Python ones.

## schema.org typing, and Google Dataset Search

ADR-0023 wants Dataset Search to index the catalogue. Records whose
`resource_kind` says they hold data (`dataset`, `model`) are therefore typed
`Dataset`, and `check-render.mjs` fails the build if one of them is not.
Records that are papers, reports or software keep their precise type rather
than claiming to be datasets — see the reasoning in `src/schema-types.mjs`.
They stay discoverable through the sitemap and `/catalog.jsonld`.

## Things that will bite you

- **`records/` empty?** The site falls back to `fixtures/rendering/` and says so
  on the homepage. One real record and the fixtures disappear.
- **Search does nothing under `npm run dev`.** Pagefind indexes `dist/`, so the
  index is a build artifact. Use `npm run build && npm run preview`.
- **`astro preview` is a daemon** in Astro 7 and survives the process that
  started it; `npm run preview` therefore uses `scripts/serve.mjs` instead, which
  a gate can start and stop.
- **Never hand-edit `src/styles/tokens.css`.** It is generated from
  `design/design-tokens.json` by `design/build-css.mjs`, and `check-tokens.mjs`
  is what stops the rest of the site growing hardcoded colours.
