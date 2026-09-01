---
type: adr
id: ADR-0039
title: Design system — DTCG tokens, Teresa's Green anchor, colour never fills a surface, AA as a build gate
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0028-provenance-is-displayed, adr-0036-component-architecture-and-the-gallery, adr-0032-site-framework-astro, run-the-a11y-gate, release-checklist]
tags: [design, accessibility, tokens]
---

# ADR-0039 — Design system

## Status

**Accepted, revision 3.** Revision 1 was agreed in turn 13; revision 2 in
turn 14 removed tinted surfaces entirely; revision 3 shipped with the site.

Revision 3 does not reopen anything decided here. It re-solves two *computed*
derivatives against the surfaces they actually land on, which is precisely what
this ADR says the palette is — "binary-searching OKLCH lightness for a target
WCAG ratio" — and it makes the gate stricter, not looser
(`design/design-system.md` §2.2.1):

| | Rev 2 | Rev 3 | Why |
|---|---|---|---|
| dark `action.primary` | `#558A6A` | **`#5B9070`** (hover `#6A9F7E`) | Rev 2 solved it against `surface.page` (4.64:1) but links live inside cards, where it measured **4.27:1** on `surface.raised`. Re-solved against `raised` |
| `component.panel.bg-light` | `neutral.50` | **`neutral.0`** | Status hues were solved against white and measured 4.37–4.40:1 on `neutral.50`. The hairline border and the 3px bar are what make a panel read as a panel; the tint was doing no work |

The generalised lesson is enforced rather than remembered: `design/gen.py` now
prints a **second** contrast table — every token against every surface it lands
on — and the committed palette ends `ALL PAIRS PASS`. There is no longer a
tolerated FAIL, so CI gate 4 treats any FAIL as fatal instead of downgrading it
to an annotation.

## Context

Turn 13:

> "Can you propose a design system (based on the design system theme standard)
> for this? I want it to look MUCH less naff than the very-old CKAN whilst being
> straightforward, professional and content-appropriate. Whoever chose the IEA
> Wind logo was obviously blind and pathologically lacking in design skill; it's
> a mid green that doesn't work anywhere but let's go with farrow and ball's
> teresa's green to start our prototyping. Why? I like it :)
>
> The entire app must be ARIA appropriate, so there should be some ally-checker
> in the loop, too. It'll need a dark and a light mode."

Turn 14, after seeing revision 1:

> "yeah, don't do green backgrounds, keep that to black/grey. … The highlighted
> bars along the left of panels in teh accent colour are lovely."

## Decision

**Tokens in W3C DTCG format** (`design/design-tokens.json`), anchored on Farrow
& Ball *Teresa's Green* No. 236 ≈ `#BDCCC2` = `oklch(0.831 0.021 156.7)`, with
**computed** accessible derivatives.

1. **Hue-locked ramp.** H = 156.7° throughout, chroma restrained to ≤ 0.055. The
   paint itself is 1.5:1 against white and unusable for text or buttons, so the
   *accessible* steps do the interactive work. Every derived value was found by
   binary-searching OKLCH lightness for a target WCAG ratio — light-mode action
   `#587D66` at 4.63:1 on white — not eyeballed. `design/gen.py` regenerates the
   palette and re-verifies every contrast pair; **run it after any colour change**
   (`make build-tokens`).
2. **Colour never fills a surface** (rev 2). Backgrounds are exclusively
   neutral, near-white through instrument-black. Semantic colour appears in
   exactly five places: **text, icons, outline badges, the focus ring, and a
   3px square-cornered left accent bar** down the edge of a panel. Status
   callouts, the withdrawn banner, the freshness warning and selected states are
   all neutral panels distinguished by their bar colour. Barred panels have
   square corners; plain cards keep 6px and take no bar.
3. **Near-achromatic neutrals** (C ≈ 0.004): light reads paper-white, dark reads
   instrument-black (`#121312` family) rather than green-black.
4. **Violet is reserved exclusively for machine inference** — see
   [[adr-0028-provenance-is-displayed]]. Provenance is `api` green, `pattern`
   blue, `llm` violet, always icon + text label + colour, **never colour alone**.
5. **Borders over shadows.** Cards are `surface.raised` plus a hairline border;
   the single shadow token exists for popovers only. This is most of what "less
   naff than old CKAN" means in practice.
6. **Type does the hierarchy**, and **metadata is typographically distinct**:
   DOIs, identifiers, version strings and source keys set in the mono face.
   **Inter (sans) + IBM Plex Mono (metadata), self-hosted as subset woff2**,
   committed to the repo — no CDN, no third-party request, a static asset that
   cannot rot. Headings are medium weight (not semibold) with slight negative
   tracking, so size carries the hierarchy; links underline on hover/focus only.
   *(Rev 3: anchor sans changed IBM Plex Sans → Inter after review; Plex Mono
   kept for metadata.)*
7. **Light and dark.** Tokens compile to CSS custom properties; `:root` carries
   light and `[data-theme="dark"]` overrides **the semantic tier only** —
   primitives are never redefined. Default follows `prefers-color-scheme`; a
   vanilla `<theme-toggle>` custom element cycles auto → light → dark, persists
   to `localStorage`, and a two-line inline `<head>` script applies the stored
   choice before first paint. `color-scheme: light dark` is declared. The toggle
   is a real `<button>` with a label and announced state.
8. **Every animation is removed under `prefers-reduced-motion` — no exceptions.**

### Accessibility is a gate, not advice

- **`pa11y-ci`** (axe-core runner) against the built `dist/` served locally, on a
  fixed URL list: home, search, one canonical record, one pathological record,
  and **`/dev/components`, which renders every component in every fixture state,
  so one URL audits the entire component inventory**.
- **Run the list twice, once per theme**, forced via a `?theme=` query parameter
  the toggle honours. Dark-mode contrast regressions are the most common kind
  and most pipelines never test them.
- Standard: WCAG 2.1 AA ruleset, with 2.2 criteria (focus appearance, target
  size ≥ 24px) treated as design requirements checked in review.
- **Automated checks catch roughly a third of real issues.** The release
  checklist therefore keeps **three manual passes**: a full keyboard-only walk,
  a screen-reader smoke test of search → result → record, and a 200% zoom /
  320px reflow check ([[release-checklist]]).

### Token discipline and build order

1. Tokens first — nothing else exists until they are stable.
2. Primitives with **all** their states — default, hover, active, focus-visible,
   disabled, loading. A primitive without its states is not done.
3. Composites — record card, search island, metadata `<dl>`, event history,
   pagination.
4. The living styleguide — `/dev/components` *is* this layer
   ([[adr-0036-component-architecture-and-the-gallery]]).

**Enforced, not aspirational: components consume tokens with zero hardcoded
values.** A CI step greps component styles for hex literals, raw `px` colours
and off-scale spacing and fails the build on any hit outside the generated
`tokens.css` (allow-listed: the font `@font-face` paths). It is a ten-line
script, and it is the difference between the system holding for years and
eroding one "quick fix" at a time.

## Consequences

**Good**

- Contrast is a computed property with a script that re-verifies it, so a token
  change that breaks AA fails CI before anyone ships it. The gallery's swatch
  section means the a11y gate audits the palette itself.
- Scarcity is what makes a desaturated celadon read as considered rather than
  washed-out. Rationing colour is the design, not a limitation of it.
- Self-hosted fonts and platform-standard theming mean the visual system has no
  third-party runtime dependency at all.

**Costs**

- The palette is not freely editable: any colour change requires re-running
  `design/gen.py` and re-reading its contrast table.
- Two deliberate oddities that look like mistakes and are not: **dark-mode
  buttons carry near-black labels**, because the accessible dark action colour
  (`#5B9070` as of revision 3; `#558A6A` when this was written) cannot reach
  4.5:1 under white text; and `border.subtle` is
  decorative only, while anything whose boundary *means* something (inputs) uses
  `border.input`, held to the 3:1 non-text rule.
- The token-grep CI step will occasionally block a legitimate one-off value. The
  correct response is a new token, not an allow-list entry.
- `pa11y-ci` is the heaviest dev dependency accepted
  ([[adr-0034-toolchain-pinning-and-no-auto-updates]]); the justification is
  that it is a gate.

## Source

`design/design-system.md` (all sections); `design/design-tokens.json`;
`design/gen.py`; `plans/02-static-plan.md` §8 (ADR-0039);
`transcript/conversation-record.md` turns 13–14.
