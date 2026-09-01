# Rendering fixtures

The gallery's data, and the site's fallback content before the first harvest.
The inventory these implement is `fixtures/fixtures-catalogue.md` §"Rendering-only
fixtures" (r-01 … r-08); that file is the specification and is not edited here.

```
fixtures/rendering/
├── <id>.json           a CKAN package inside the standard fixture wrapper
├── raw/<id>.json       the upstream-shaped payload it corresponds to
└── ui/<id>.json        fixtures that are NOT records (r-07, r-08)
```

## `<id>.json` — records

`fixture_kind: "record"`, so `tests/test_fixtures.py` validates every one of
them against the CKAN-compat gate, and `site/src/content.config.ts` validates
them again through the same Zod schema the real records go through. A rendering
fixture that CKAN would refuse is a broken fixture, not an interesting one.

Two families:

- **`r-NN-…`** — the pathological cases from the catalogue: a 300-character
  title, a record with nothing but a title and a DOI, five tasks at once, a
  withdrawn record, mixed scripts, and the single search result.
- **`rep-NN-…`** — representative, *ordinary* records, one per source system and
  one per interesting-but-not-broken state (LLM-inferred fields, a retraction, a
  curator note, an embargo, restricted access). They exist so the site builds
  and looks like itself before `records/` is populated, and so the gallery shows
  what normal looks like next to what awkward looks like.

Some carry an `events` array: a hand-written event log for the record-history
component, used only when `events/<slug>.jsonl` does not yet exist.

## `raw/<id>.json` — read this before using them

**These payloads are hand-built.** There is no upstream artifact with a
fabricated 300-character title, so there is nothing to capture. They are shaped
like the API responses they imitate and they are consistent with the expectation
beside them, but they are *not* verbatim captures and they are **not** a
reference for what a real Zenodo, OSTI or Crossref response looks like — the
adapter tracks capture their own, per `fixtures/README.md`.

## `ui/` — fixtures that are not records

`r-07-empty-search` is a search that returns nothing, `r-08-stale-banner` is a
`state/last-run.json` older than 45 days, and `r-09-dead-link` is a
`state/link-check.json` in which one record's source link no longer responds.
None is a CKAN package, so they live one directory down, where the generic
fixture test — which globs `fixtures/rendering/*.json` and validates each as a
package — does not claim them. They declare `fixture_kind: "ui_state"` and are
read by `site/src/lib/state.ts` (`uiFixture`).
