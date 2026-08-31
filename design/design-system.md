# IEA Wind Catalogue — Design System

**Status:** proposed · **Tokens:** `design-tokens.json` (W3C Design Tokens / DTCG format)
**Anchor:** Farrow & Ball *Teresa's Green* No. 236 ≈ `#BDCCC2` — a screen approximation of a physical paint; treated as canonical for this system, `oklch(0.831 0.021 156.7)`.

---

## 1. Character

Quiet, papery, institutional-but-current. The references are a well-set journal and a field notebook, not a SaaS dashboard. Concretely:

- **Borders over shadows.** Cards are `surface.raised` + a hairline border. The single shadow token exists for popovers only. This is most of what "less naff than old CKAN" means in practice — CKAN's vintage look is mostly gradient buttons, boxed shadows and cramped default type.
- **Colour never fills a surface.** Backgrounds are exclusively neutral, near-white through instrument-black. Semantic colour appears in exactly five places: text, icons, outline badges, the focus ring, and the **left accent bar** — a 3px, square-cornered bar down the left edge of a panel. Status callouts, the withdrawn banner, the freshness warning and selected states are all neutral panels distinguished by their bar colour. Scarcity is what makes a desaturated celadon read as considered rather than washed-out.
- **Barred panels have square corners.** A single-sided bar against a rounded panel mismatches at the joins, and radius-0 callouts are the crisper, instrument-panel look anyway. Plain cards keep 6px and take no bar.
- **Type does the hierarchy.** Size, weight and space — not rules, boxes, or colour blocks.
- **Metadata is typographically distinct.** DOIs, identifiers, version strings and source keys set in the mono face. A catalogue is mostly metadata; giving it its own voice is the content-appropriate move.

## 2. Colour

### 2.1 How Teresa's Green becomes a system

The paint itself (`green.300`, kept verbatim) is far too light for interactive use — 1.5:1 against white, unusable for text or buttons. So it anchors a **hue-locked ramp** (H = 156.7° throughout, chroma restrained to ≤ 0.055) and the *accessible* steps do the interactive work. Every "solved" value below was computed, not eyeballed, by binary-searching OKLCH lightness for the target WCAG ratio.

| Step | Hex | Role |
|---|---|---|
| green.50–100 | `#F2F9F4` `#E7F2EA` | chart fills and documentation swatches only — never page or panel backgrounds |
| **green.300** | **`#BDCCC2`** | **the paint** — hero band, empty states, decorative accents |
| green.500 | `#7A9C86` | large graphics, charts |
| green.700-ish (solved) | `#587D66` | **light-mode action & links — 4.63:1 on white** |
| green.900 | `#21372A` | deep chart fills |

Neutrals are near-achromatic (C ≈ 0.004): light mode reads paper-white, dark mode reads instrument-black (`#121312` family) rather than green-black. The hue-lock is still present but sits below the threshold of "tinted" — cohesion without a cast. (Rev 2: chroma was halved after review precisely so dark surfaces read black/grey.)

### 2.2 Verified contrast (WCAG 2.2 AA)

| Pair | Light | Dark |
|---|---|---|
| Body text / page | 17.6:1 | 15.2:1 |
| Secondary text / deepest surface it sits on | 4.61:1 | 4.60:1 |
| Action colour / surface | 4.63:1 | 4.64:1 |
| Label on action (white ⁄ near-black) | 4.63:1 | 4.78:1 |
| Focus ring / surface (3:1 non-text) | 3.13:1 | ✓ |
| Input border / surface (3:1 non-text) | 3.35:1 | 7.94:1 |
| Status text (info · warning · danger · violet) / surface | all ≥ 4.63:1 | all ≥ 4.60:1 |

Two deliberate details: **dark-mode buttons carry near-black labels**, because the accessible dark action colour (`#558A6A`) can't reach 4.5:1 under white text — solving for both constraints simultaneously is what fixed the button colour. And `border.subtle` is decorative only; anything whose boundary *means* something (inputs) uses `border.input`, which is held to the 3:1 non-text rule.

### 2.3 Semantic colour beyond status

The catalogue's own vocabulary gets colour semantics — expressed only as **outline badges** (1px coloured border + coloured text, transparent background) and **accent bars**, always icon + text label + colour, never colour alone, never a tinted fill:

- **Provenance** — `api` (green), `pattern` (blue), `llm` (violet). Violet is reserved exclusively for machine inference so "the model guessed this" is recognisable at a glance across the whole site (fixture `x-05`).
- **Availability** — open (green), restricted (amber), embargoed (neutral).
- **Lifecycle** — withdrawn renders as a danger-tinted banner across the record (fixture `r-04`); archived is neutral.

## 3. Typography

**IBM Plex Sans + IBM Plex Mono**, self-hosted as subset woff2 (two files, committed to the repo — a static asset that cannot rot, no CDN, no third-party request). Plex is institutional without being dull, has the broad Latin coverage the author names need (`Søren`, `Müller` — fixture `r-05`), and the sans/mono pair is designed together. Fallback stacks are metric-compatible; `font-display: swap`.

Scale: 12 / 14 / 16 / 18 / 20 / 24 / 30 / 36 px expressed in rem; body line-height 1.55; prose measure 68ch. Record titles at `h2`/semibold — big enough for scanning a results list, restrained enough that `r-01`'s 300-character title wraps with dignity.

## 4. Space, shape, elevation, motion

4px base scale (1–16). Radius 4px controls, 6px cards, pill badges. One shadow token (popovers). Motion 120/200ms, one easing, and **every animation is removed under `prefers-reduced-motion` — no exceptions**.

## 5. Theming mechanism

- Tokens compile to CSS custom properties. `:root` carries light; `[data-theme="dark"]` overrides **the semantic tier only** — primitives are never redefined.
- Default follows `prefers-color-scheme`; a `<theme-toggle>` **vanilla custom element** (§3.7's rules apply — progressive enhancement, no framework) cycles auto → light → dark, persists to `localStorage`, and a two-line inline `<head>` script applies the stored choice before first paint to prevent flash.
- `color-scheme: light dark` is declared so native controls, scrollbars and form widgets follow.
- The toggle is a real control: `<button>`, visible label or `aria-label`, state announced via `aria-pressed`/text, fully keyboard operable.

## 6. ARIA requirements by surface

**Global** — every page: one `<main>`, `<nav>` labelled, skip link as first focusable element, unique `<title>`, visible focus everywhere (`:focus-visible`, 2px ring + 2px offset), no keyboard traps, `lang="en"` on the document and `lang` attributes on non-English titles.

**Search (the Pagefind island)** — the input is a labelled `role="combobox"` or plain labelled input; the result count announces via a polite `aria-live` region ("214 results for *lidar*"); filters are real `<button>`s or checkboxes with `aria-pressed`/checked state, never clickable `<div>`s; results are a list; zero-results state (`r-07`) is announced, not just rendered. **Audit Pagefind's stock UI against this list before shipping it** — it's decent but the live result count and filter semantics need verifying, and this is the page a screen-reader user lives on.

**Record page** — an `<h1>` title; metadata as `<dl>` definition lists (the semantically correct structure for label/value metadata, and screen readers navigate them well); badges carry text, with `aria-hidden` icons; the withdrawn banner is `role="status"` at the top of `<main>`; the event-history list is a real list with meaningful text, not icon soup; "report an issue at source" is a link that says where it goes.

**Cards/lists** — the card's title is the link, not the whole card as a click target wrapping nested interactive elements; task chips that filter are buttons, task chips that merely label are text.

## 7. The checker in the loop

**Gate, not advice: the build fails on violations.**

- **`pa11y-ci`** (axe-core runner) against the built `dist/` served locally, on a fixed URL list: home, search, one canonical record, one pathological record (`r-01`/`r-04` fixtures), and — the efficient part — **`/dev/components`, which renders every component in every fixture state, so one URL audits the entire component inventory** including the withdrawn banner, the LLM badge, and the empty-search state.
- **Run the list twice, once per theme**, forced via a `?theme=` query parameter the toggle honours — dark-mode contrast regressions are the most common kind and most pipelines never test them.
- Standard: WCAG 2.1 AA ruleset, treating 2.2 criteria (focus appearance, target size ≥ 24px) as design requirements checked in review.
- Pinned like everything else (§3.5 discipline); it's a dev-only dependency and the heaviest one we accept — the justification is that it's a *gate*.
- **Automated checks catch roughly a third of real issues.** The release checklist keeps three manual passes: a full keyboard-only walk, a screen-reader smoke test (VoiceOver or NVDA) of search → result → record, and a 200% zoom / 320px reflow check.

## 8. Token discipline and build order

Adopted verbatim from the colour-lab chat's closing note, because it's the part that makes a system rather than a pile of pretty pieces:

1. **Tokens first** — this file, compiled to CSS custom properties. Nothing else exists until this is stable.
2. **Primitives with all their states** — button, input, badge, panel, link: default, hover, active, focus-visible, disabled, and (where applicable) loading. A primitive without its states isn't done.
3. **Composites** — record card, search island, metadata `<dl>`, event history, pagination.
4. **The living styleguide** — which we already have: `/dev/components` *is* this layer, rendering every primitive state and every composite against the fixture set.

**Enforced, not aspirational: components consume tokens with zero hardcoded values.** A CI step greps component styles for hex literals, raw `px` colours and off-scale spacing and fails the build on any hit outside the generated `tokens.css` (allow-listed: the font `@font-face` paths). It's a ten-line script, and it's the difference between the system holding for years and eroding one "quick fix" at a time.

## 9. Files and wiring

```
design/
├── design-tokens.json      # DTCG source of truth (this proposal)
├── build-css.mjs           # tokens → custom properties (≈40 lines; no Style Dictionary
│                           #   unless multi-platform output is ever actually needed)
└── fonts/                  # Plex Sans + Mono, subset woff2
site/src/styles/tokens.css  # generated — marked as such, never hand-edited
```

The gallery gains a **swatch section** rendering every token from the JSON — which means the a11y gate that audits the gallery is also auditing the palette itself, and a future token change that breaks contrast fails CI before anyone ships it.

---

*ADR-0039 records: DTCG token format; Teresa's Green №236 as anchor with computed accessible derivatives; hue-locked ramp; **colour never fills surfaces — accent bars, outline badges, text, icons and focus only**; near-achromatic neutrals; square-cornered barred panels; token-only components enforced by CI grep; AA as a hard gate via pa11y-ci over the gallery in both themes; Plex self-hosted (the referenced chat's fonts deliberately not carried over); violet reserved for machine inference.*
