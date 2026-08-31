---
type: runbook
id: RUN-run-the-a11y-gate
status: current
date: 2026-08-31
related: [adr-0039-design-system, adr-0036-component-architecture-and-the-gallery, adr-0023-search-via-pagefind, run-the-site-locally, release-checklist]
tags: [runbook, accessibility, gate]
last_executed: never
---

# Runbook — run the accessibility gate

**Goal:** prove the built site meets WCAG 2.2 AA, in **both** themes, before it
ships.
**Governed by:** [[adr-0039-design-system]] §7.

---

## 0. Gate, not advice

**The build fails on violations.** Accessibility here is a build gate in the
same sense that `validate-ckan-compat` is: not a report someone reads, a step
that stops the pipeline.

## 1. Run it

```sh
make gates
```

which is `make test`, `make validate`, `make build-tokens`, then
`cd site && npm run gates`.

To run only the accessibility part:

```sh
cd site
npm run build          # dist/ must exist and be current — pa11y runs against the built output
npm run gates          # token grep + x-08 gate proof + URL check + pa11y-ci, both themes
npm run a11y           # just the pa11y part
```

`npm run a11y` starts `scripts/serve.mjs` on port 4321, reads the path list from
`site/.pa11yci`, doubles it with `?theme=light` and `?theme=dark`, runs
`pa11y-ci`, and stops the server. It fails loudly if a path in `.pa11yci` is not
present in `dist/` — a record fixture that stops standing in for a real record
must be replaced in the list, not silently skipped.

## 2. What the gate must cover

**Tooling**

- **`pa11y-ci`**, the axe-core runner, against the built `dist/` **served
  locally**. Pinned like everything else
  ([[adr-0034-toolchain-pinning-and-no-auto-updates]]); it is the heaviest dev
  dependency accepted, and the justification is that it is a gate.
- Standard: the **WCAG 2.1 AA ruleset**. WCAG 2.2 criteria — focus appearance,
  target size ≥ 24px — are design requirements checked in review, because axe
  does not test them reliably.

**The URL list**

| URL | Why it is on the list |
|---|---|
| `/` | homepage, freshness banner in its warning state |
| `/search/` | the page a screen-reader user lives on |
| `/browse/` | the no-JavaScript path, and pagination |
| `/record/doi-10-5281-zenodo-1234566/` | `rep-01`, the canonical record |
| `/record/doi-10-5281-zenodo-7702209/` | `r-01`, the 300-character title |
| `/record/doi-10-5281-zenodo-3376526/` | `r-04`, withdrawn, and its banner |
| **`/dev/components`** | **renders every component in every fixture state, so one URL audits the entire component inventory** — the withdrawn banner, the LLM badge, the empty-search state, and the token swatch section |

The list lives in `site/.pa11yci` as site-relative paths; `scripts/a11y.mjs`
adds the base path and the theme parameter.

The gallery is the efficient part of this design: because it also renders every
token as a swatch, **the a11y gate is auditing the palette itself**, and a
future token change that breaks contrast fails CI before anyone ships it.

**Both themes**

**Run the whole list twice, once per theme**, forced via a `?theme=` query
parameter that the `<theme-toggle>` honours. Dark-mode contrast regressions are
the most common kind and most pipelines never test them.

**Token discipline, in the same step**

A ten-line grep over component styles that fails on hex literals, raw `px`
colours and off-scale spacing anywhere outside the generated `tokens.css`
(allow-listed: the font `@font-face` paths). **Components consume tokens with
zero hardcoded values.** This is the difference between the system holding for
years and eroding one "quick fix" at a time.

## 3. The specific things that fail

Drawn from `design/design-system.md` §6. If the gate is green but one of these
is wrong, the gate is under-specified, not the page correct.

**Global, every page**

- one `<main>`; `<nav>` labelled; **skip link as the first focusable element**;
  unique `<title>`; visible focus everywhere (`:focus-visible`, 2px ring + 2px
  offset); no keyboard traps; `lang="en"` on the document and `lang` attributes
  on non-English titles.

**Search — the Pagefind island**

- input is a labelled `role="combobox"` or a plain labelled input;
- result count announces via a **polite `aria-live` region** ("214 results for
  *lidar*");
- filters are real `<button>`s or checkboxes with `aria-pressed`/checked state —
  **never clickable `<div>`s**;
- results are a list;
- the zero-results state (fixture `r-07`) is **announced**, not merely rendered.

**Audit Pagefind's stock UI against this list before shipping it.** It is
decent, but the live result count and the filter semantics need verifying
([[adr-0023-search-via-pagefind]]).

**Record page**

- an `<h1>` title; metadata as `<dl>` definition lists; badges carry **text**,
  with `aria-hidden` icons; the withdrawn banner is `role="status"` at the top
  of `<main>`; the event-history list is a real list with meaningful text, not
  icon soup; "report an issue at source" is a link that says where it goes.

**Cards and lists**

- the card's **title** is the link, not the whole card wrapping nested
  interactive elements; task chips that filter are buttons, task chips that
  merely label are text.

**Motion**

- every animation removed under `prefers-reduced-motion`. **No exceptions.**

## 4. The three manual passes

**Automated checks catch roughly a third of real issues.** These are part of the
release, not optional extras ([[release-checklist]]):

1. **Keyboard-only walk** — unplug the mouse. Skip link first, tab through home
   → search → filter → result → record → theme toggle → back. No traps, focus
   always visible, focus order matching reading order.
2. **Screen-reader smoke test** — VoiceOver or NVDA, following search → result →
   record. Listen specifically for: the live result count, the zero-results
   announcement, the withdrawn banner, and whether provenance badges read as
   meaningful text rather than as colour.
3. **200% zoom / 320px reflow** — no horizontal scrolling, no clipped content,
   the 300-character title (`r-01`) still wrapping with dignity, task chips
   (`r-03`) overflowing gracefully.

Record the date each was last done in [[release-checklist]].

## 5. When it fails

- **Contrast** — do not hand-pick a colour. Change the token, re-run
  `make build-tokens` (`uv run python design/gen.py`), and read its two contrast
  tables. The second one, *"every colour against every surface it lands on"*, is
  the one that matters: it checks the **shipped** `design-tokens.json`, not the
  values the script derives, because a colour solved against white will happily
  be rendered on a card, a panel or a sunken block. It must end `ALL PAIRS PASS`.
- **A hardcoded value** — add a token, do not add an allow-list entry.
- **A missing state** — a primitive without default, hover, active,
  focus-visible and disabled is not done. Add it to the gallery, which is how
  the gate found it.
- **`aria-hidden` glyph flagged for contrast** — axe returns *incomplete* for an
  element whose only content is a symbol character ("contains only non-text
  characters"), and pa11y reports incomplete as an error. Decorative glyphs
  therefore go in `data-icon` and are drawn by the `.icon::before` rule; they
  never sit in the text layer.
- **"Failed to run" / `Protocol error (Target.closeTarget)`** — concurrency.
  `.pa11yci` pins `concurrency: 1` for this reason; a gate that silently skips a
  page is worse than a slow one.

## 6. What the first run found

Worth recording, because two of the four were defects in the design system
rather than in the markup:

1. **The dark action colour was solved against the wrong surface.** `#558A6A`
   measures 4.64:1 on `surface.page` but **4.27:1 on `surface.raised`** — and
   links live inside cards, in the header and in source badges. Re-solved
   against `raised`: `#5B9070` (4.62:1 there, 5.03:1 on page, 5.19:1 under the
   near-black button label).
2. **The panel background was darker than the surface the status hues were
   solved against.** Every status colour measured 4.37–4.40:1 on
   `{color.neutral.50}`. `component.panel.bg-light` is now `{color.neutral.0}`;
   the hairline border and the 3px bar are what make a panel read as a panel.
3. Duplicate `id`s on the gallery, which renders fourteen record bodies on one
   page — section heading ids are now scoped to the record slug.
4. Decorative glyphs in the text layer (see §5).

`design/gen.py` grew the shipped-token verification table so that (1) and (2)
fail in the generator, before pa11y ever runs.

---

**Last executed:** 2026-09-01 — `npm run gates` green: **14/14 URLs**
(7 pages × 2 themes), 0 errors. `design/gen.py` reports `ALL PAIRS PASS`.

Re-run after the gallery gained the **real** `<SearchIsland>` (it had been
hand-reproducing the island's empty state, which is precisely the drift the
gallery exists to catch): still 14/14, still 0 errors, and a headless check
confirms the gallery has no duplicate `id`s — the failure mode that §6.3 records
from the first run.

The `?theme=` parameter was verified to actually *do* something rather than
merely be accepted: light and dark render different computed backgrounds
(`rgb(252,254,253)` vs `rgb(18,19,18)`). A gate that runs the list twice against
an identical page is worse than not running it twice.

The three manual passes in §4 have **not** been done and are outstanding for
[[release-checklist]].
