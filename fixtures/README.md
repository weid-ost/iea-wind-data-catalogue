# Fixture layout

The inventory of *what* fixtures exist is `fixtures-catalogue.md` — that file is
the specification and is not edited by the harvest tracks. This file describes
*where the bytes go*, which the tracks do add to.

The two are checked against each other:
`tests/test_fixtures.py::TestTheCatalogueMatchesTheTree` fails if a fixture has
no catalogue row, if a row has no fixture (unless the row says **realised as
`<id>`** and points at the artifact that carries the case), or if an id names
two rows. Add the row in the same commit as the fixture — the catalogue is the
specification, not a changelog.

```
fixtures/
├── fixtures-catalogue.md          # the inventory (specification; do not edit)
├── README.md                      # this file
├── <source>/
│   ├── raw/<id>.json              # the upstream payload, VERBATIM
│   └── <id>.json                  # the expected result
└── cross-cutting/
    └── <id>.json                  # records exercising reconciliation / the gate
```

`<source>` is the `sources.yaml` key: `zenodo`, `datacite`, `crossref`,
`github`, `osti`, `ieawind`, `wdh`, plus `cross-cutting` and `rendering` for
the fixtures that belong to no single adapter.

`<id>` is the catalogue id: `zen-01-canonical`, `iea-02-doi-punctuation`, and
so on. One id, two files, same stem.

## `raw/<id>.json` — the input

The upstream response captured verbatim, redacted only where it must be. Never
hand-written when a real payload can be captured: invented payloads test the
parser against your idea of the API rather than against the API.

### When you must invent one, two rules

1. **Say so, in the payload itself.** The word `INVENTED`, plus why capturing
   was impossible, in the expectation *and* in the raw file — `_comment` at the
   top of a JSON payload, an HTML comment at the top of a page. The disclosure
   may live in `case`, `note`, `invented` or `provenance_note`;
   `tests/test_fixtures.py::test_an_invented_payload_says_so` reads all four and
   then requires the marker in the raw file. (A fixture whose *expectation* is
   invented but whose payload is a genuine capture says `"raw_is_capture": true`
   — `zen-12`'s real 410 tombstone body is the only one.)
2. **Never wear a live identifier.** Synthetic DOIs go on the reserved DataCite
   test prefix **`10.5072`**, which does not resolve; synthetic Zenodo record
   URLs go to `sandbox.zenodo.org`. An invented record bound to a real DOI is
   not a fixture, it is a false statement about someone's work — the rendering
   fixtures once flagged a real, unretracted *Wind Energy* paper as retracted
   and hung a "withdrawn upstream" banner on a live Zenodo dataset
   (fixture-compliance-01). Do not use real people's names either.

Capture with the real client so the shape is honest:

```sh
curl -sS -H 'Accept: application/json' \
  -A 'iea-wind-data-catalogue/0.1 (+https://github.com/thclark/iea-wind-data-catalogue; tom@octue.com)' \
  https://sandbox.zenodo.org/api/records/1234567 \
  | python -m json.tool > fixtures/zenodo/raw/zen-01-canonical.json
```

## `<id>.json` — the expectation

One of five shapes, and the file says which in its `fixture_kind`:

* `"source_namespace"` — what `Adapter.map()` should produce: the
  `source` block, the `identity_key`, the `source_key`, and the per-field
  `provenance`. This is the normal shape for an adapter fixture.
* `"record"` — a whole CKAN package dict, for fixtures that exercise
  materialisation, the CKAN gate, or the gallery.
* `"page"` — a Tier-3 crawl expectation. `raw` is a captured `.html` page;
  the expectation is what happens *before* any record exists: the content
  hash, the reduced text, the DOI sweep, the classification, the notices
  (`iea-02` … `iea-12`).
* `"degradation"` — a source that disables itself. There is nothing upstream
  to capture, so `raw` is the live probe transcript that justifies the
  decision and the expectation is the `SourceResult` the run report shows
  (`wdh-07`).
* `"ui_state"` — not a record at all: a `state/*.json` payload the site renders
  (`r-07`, `r-08`, `r-09`). These live in `fixtures/rendering/ui/` so the
  generic record validation does not claim them.

```jsonc
{
  "fixture_id": "zen-01-canonical",
  "fixture_kind": "source_namespace",
  "case": "Published dataset, DOI, ORCID'd creators, licence, files",
  "identity_key": "10.5072/zenodo.1234567",
  "source_key": "3",
  "source": { "...": "the SourceNamespace fields" },
  "provenance": { "title": { "extraction_method": "api" } }
}
```

## `cross-cutting/` — where the input is an event stream, not a payload

A cross-cutting fixture belongs to no adapter, so there is no upstream payload
to capture. Its `raw/<id>.json` holds the **inputs to the pipeline** instead,
and `tests/test_crosscutting.py` replays them through the real code —
`append_event` → `apply_annotations` → `check_pins` → `materialize_all` →
`dedupe` / `linkcheck`:

```jsonc
{
  "note": "why this fixture exists and what it proves",
  "events": [ /* Event objects, appended in order */ ],

  // optional — the YAML a curator would have written in annotations/
  "annotations_yaml": { "identity_key": "...", "annotations": [ /* ... */ ] },
  "annotations_applied_before_event_index": 1,   // when the curator acted

  // optional — what the link checker sees, so it never touches the network
  "http_responses": { "https://example.org/x": 404 },

  // optional — a fixed timestamp for merge annotations, so replay is byte-stable
  "merge_observed_at": "2026-08-31T12:00:00Z",

  // what the fixture asserts, restated for a human reading the input file
  "expected_merges": [], "expected_proposals": [], "expected_notices": []
}
```

The expectation file is `"fixture_kind": "record"` and carries
`expected_records` (every materialised record, keyed by slug), plus
`expected_notices`, `expected_dedupe` / `expected_records_after_merge` for a
dedupe fixture, and `expected_link_check` for a link-rot one. `record` is the
first record by slug, so the generic checks in `tests/test_fixtures.py` still
apply.

## How tests use them

`tests/test_fixtures.py` walks the tree and checks every fixture is
well-formed, that every `source_namespace` fixture validates as a
`SourceNamespace`, and that every `record` fixture either passes the CKAN gate
or declares `"expect_violations": true`.

Adapter tests parametrize over their own directory:

```python
@pytest.mark.parametrize("fixture", load_fixtures("zenodo"), ids=lambda f: f["fixture_id"])
def test_map(fixture):
    raw = RawObservation(**raw_payload_for(fixture))
    mapped = ZenodoAdapter().map(raw)
    assert mapped.identity_key == fixture["identity_key"]
    assert mapped.source.model_dump(exclude_none=True) == fixture["source"]
```

`map()` is pure by contract — no network, no clock, no filesystem — which is
what makes this work offline.

**Every harvest change ships with its fixture.** New behaviour with no fixture
is not finished.
