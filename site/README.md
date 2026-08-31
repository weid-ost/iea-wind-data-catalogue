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
npm run build     # tokens + astro build + pagefind + token grep + CKAN-gate proof
npm run preview   # serve dist/ exactly as Pages will, base path and all
npm run gates     # token grep + CKAN-gate proof + pa11y-ci, both themes
```

`make site` and `make gates` from the repository root wrap the same things.

## Layout

```
src/
├── ckan.mjs             the Zod gate — CKAN's rules, mirrored from harvest/ckan_compat.py
├── licenses.mjs         the licence register, mirrored from harvest/licenses.py
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
├── sync-fonts.mjs       design/fonts/*.woff2 -> public/fonts/
├── serve.mjs            twenty-line static server for dist/, honouring the base path
└── a11y.mjs             serves dist/ and runs pa11y-ci over .pa11yci, once per theme
```

## Deployment paths

The repository deploys to a GitHub Pages **project** site today, so `base`
defaults to `/iea-wind-data-catalogue` and `site` to
`https://thclark.github.io/iea-wind-data-catalogue/`. Both are environment
variables; a custom domain is:

```sh
SITE_BASE=/ SITE_URL=https://catalogue.example.org/ npm run build
```

Every internal link goes through `withBase()` in `src/lib/url.ts`, so that is
the only change needed.

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
