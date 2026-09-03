---
type: runbook
id: RUN-add-a-source-adapter
status: current
date: 2026-08-31
related: [adr-0026-change-detection-by-source-key, adr-0024-the-llm-boundary, adr-0031-the-harvest-never-fails-on-llm-unavailability, run-a-harvest-locally, record-format]
tags: [runbook, harvest, adapters]
last_executed: 2026-08-31
---

# Runbook — add a source adapter

**Goal:** a new source harvested, mapped, fixtured and reported, without
breaking the other six.
**Prerequisites:** [[local-dev-setup]] done; read `harvest/CONTRACT.md` §§2–4
first — it is the interface document and it wins over this page.
**Governed by:** [[adr-0026-change-detection-by-source-key]],
[[adr-0024-the-llm-boundary]],
[[adr-0031-the-harvest-never-fails-on-llm-unavailability]].

---

## 1. Decide four things before writing code

1. **The source name.** One string that is simultaneously the module name
   (`harvest/adapters/<name>.py`), the class's `source_name`, the key under
   `sources:` in `sources.yaml`, and the `source_system` on every event. Those
   four must agree.
2. **The tier.** 1 = structured API (never a model), 2 = structured-ish, 3 =
   heterogeneous HTML (the only tier allowed a model). **If a structured API
   exists, the tier is 1** — see [[adr-0024-the-llm-boundary]].
3. **The source key.** One record-level change token whose semantics you own.
   Prefer a revision id or a metadata-updated field; verify it against a live
   payload rather than assuming. If nothing trustworthy exists, use
   `harvest.adapters.base.payload_hash` over **only the fields that mean
   something** — including `pushed_at`, a star count or a `retrieved_at`
   timestamp turns append-on-change into append-always and defeats the design.
4. **The identity rule.** DOI if there is one; else `source_system|source_id`;
   else the fragile title-hash, used knowingly. See [[record-format]] §1.

## 2. Register the source

Add a block to `sources.yaml`:

```yaml
  mysource:
    enabled: true
    tier: 1
    precedence: 45          # lower wins for scalars when several systems describe one identity
    max_records: 5          # the prototype cap. Leave it at five.
    source_key: "revision_id — bumped on metadata edit; verified 2026-08-31"
    api: "https://example.org/api/records"
    # anything else reaches the adapter as SourceConfig.options
```

`precedence` places the source in the composition order: DataCite 10, Crossref
20, Zenodo 30, OSTI 40, WDH 50, GitHub 60, `ieawind` 90. Lower wins for scalars;
set-valued fields union across all of them regardless.

## 3. Write the adapter

`harvest/adapters/mysource.py`:

```python
from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, SourceUnreachable, register
from harvest.http import HarvestClient
from harvest.identity import identity_key
from harvest.models import FieldProvenance, MappedObservation, RawObservation, SourceNamespace
from harvest.sanitize import sanitize_html
from harvest.licenses import map_license
from harvest.doi import normalise_doi


@register                                     # adds it to the registry
class MySourceAdapter(Adapter):
    source_name = "mysource"                  # == module name == sources.yaml key
    tier = 1
    source_key_semantics = "revision_id, bumped on metadata edit"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        with HarvestClient() as client:
            result = client.get(self.config.get("api"))
            if not result.ok:
                raise SourceUnreachable(result.error or f"HTTP {result.status_code}")
            for item in result.json()["records"][:limit]:
                yield RawObservation(
                    source_system=self.source_name,
                    source_id=str(item["id"]),
                    source_key=str(item["revision_id"]),
                    url=item.get("links", {}).get("html"),
                    payload=item,                      # VERBATIM. No cleaning.
                )

    def map(self, raw: RawObservation) -> MappedObservation:
        metadata = raw.payload["metadata"]
        license_id, _ = map_license(metadata.get("license"))
        return MappedObservation(
            identity_key=identity_key(doi=metadata.get("doi"),
                                      source_system=self.source_name,
                                      source_id=raw.source_id),
            source_system=self.source_name,
            source_id=raw.source_id,
            source_key=raw.source_key,
            source=SourceNamespace(
                title=metadata["title"],
                notes=sanitize_html(metadata.get("description")),
                doi=normalise_doi(metadata.get("doi")),
                url=raw.url,
                source_urls=[raw.url] if raw.url else [],
                license_raw=metadata.get("license"),
                license_id=license_id,
            ),
            provenance={
                "title": FieldProvenance(extraction_method="api"),
                "license_id": FieldProvenance(extraction_method="pattern"),
            },
        )
```

### `harvest(limit)` — talk to the source

- Yields at most `limit` observations. `_iter_limited` truncates and warns if
  you forget, but forgetting is still a bug.
- `payload` is the upstream response **verbatim**. No cleaning, no
  interpretation, no mapping — what you yield here is what
  `fixtures/<source>/raw/<id>.json` holds.
- Use `harvest.http.HarvestClient`. It sends the project User-Agent, honours
  `robots.txt` per host, throttles to 5 req/s, sends conditional-GET headers,
  and **never raises** on a transport error. Network etiquette is not yours to
  reinvent.
- **Metadata and links only. Never mirror a file.**
- If you open your own client, implement `close()` — `run_adapter` always calls
  it, including on failure.

### `map(raw)` — interpret one payload

**Pure.** No network, no clock, no filesystem, no globals. This is what the
fixture tests call, and purity is the only reason they run offline. If you need
"now", it is already on `raw.fetched_at`.

`map()` must **not**:

- write an event — `run_adapter` does that, once, for everyone;
- check whether something changed — `run_adapter` does that too;
- resolve a DOI over the network — resolve in `harvest()`, or hand candidates to
  the reconciler;
- invent a value the source did not state. **An absent licence is
  `notspecified` + `license_mapped: false`, never a guess. Never infer an open
  licence.**

### Failing

```python
raise SourceUnreachable("listing endpoint requires a token (Spike 4)")
```

`run_adapter` catches `SourceUnreachable` (→ `reachable: false`),
`NotImplementedError` (→ `implemented: false`) and **every other exception**
(→ `reachable: false` with the exception text), records it in the
`SourceResult`, and lets the other six sources finish. A `map()` that throws on
one record loses that record, not the source.

## 4. Ship the fixtures — this is not optional

**Every harvest change ships with its fixture. New behaviour without one is not
finished.**

`fixtures/README.md` describes the layout; `fixtures/fixtures-catalogue.md` is
the inventory and **is a specification you do not edit**. Capture the real
payload rather than inventing one:

```sh
mkdir -p fixtures/mysource/raw
curl -sS -H 'Accept: application/json' \
  -A 'iea-wind-data-catalogue/0.1 (+https://github.com/weid-ost/iea-wind-data-catalogue; tom@octue.com)' \
  https://example.org/api/records/123 \
  | python -m json.tool > fixtures/mysource/raw/mys-01-canonical.json
```

Then write the expectation, `fixtures/mysource/mys-01-canonical.json`:

```jsonc
{
  "fixture_id": "mys-01-canonical",
  "fixture_kind": "source_namespace",
  "case": "Published dataset, DOI, licence, files",
  "identity_key": "10.5072/zenodo.1234567",
  "source_id": "123",
  "source_key": "3",
  "raw": "raw/mys-01-canonical.json",
  "source": { "…": "the SourceNamespace fields map() should produce" },
  "provenance": { "title": { "extraction_method": "api" } }
}
```

`tests/test_fixtures.py` already walks the whole tree and validates every
fixture generically, so a malformed one fails immediately. Add your own
parametrised test:

```python
from conftest import load_fixtures   # tests/conftest.py; pytest puts tests/ on sys.path


@pytest.mark.parametrize("fixture", load_fixtures("mysource"), ids=lambda f: f["fixture_id"])
def test_map(fixture):
    raw = RawObservation(
        source_system="mysource", source_id=fixture["source_id"],
        source_key=fixture["source_key"],
        payload=json.loads((FIXTURES / "mysource" / fixture["raw"]).read_text()),
    )
    mapped = MySourceAdapter().map(raw)
    assert mapped.identity_key == fixture["identity_key"]
    assert mapped.source.model_dump(exclude_none=True) == fixture["source"]
```

Cover the canonical case **and** the edge cases the catalogue names for your
source — for an existing source, the rows in `fixtures/fixtures-catalogue.md`
are the required set, not a menu. The catalogue is checked in both directions
(`tests/test_fixtures.py::TestTheCatalogueMatchesTheTree`), so a new fixture
without a row fails the suite just as a row without a fixture does. **Add the
row in the same commit as the fixture.**

If your fixture is invented rather than captured, say so in the expectation
*and* in the raw payload — the word `INVENTED`, plus why — and put its
identifiers on the reserved DataCite test prefix `10.5072`, which does not
resolve. `tests/test_fixtures.py` enforces the first; the reason for the second
is that a fixture bound to a live DOI makes a claim about somebody's real work.

## 5. Verify

```sh
make test
uv run python -m harvest sources                             # your adapter appears
uv run python -m harvest run --source mysource --dry-run     # exercises harvest() and map(), writes no events
uv run python -m harvest report                              # seen / changed / errors for your source
uv run python -m harvest run --source mysource               # for real
make materialize && make validate
```

Then run it **twice** and confirm the second run reports `changed: 0` and leaves
`events/` untouched. If it does not, your source key includes something that
churns — go back to §1.3.

## 6. Checklist

- [ ] `sources.yaml` entry, with a `source_key:` string that describes the real
      semantics and a `precedence:`
- [ ] `harvest/adapters/<name>.py`, `@register`, `source_name` matching in four
      places
- [ ] `harvest()` honours `limit`, uses `HarvestClient`, yields verbatim
      payloads, raises `SourceUnreachable` on failure
- [ ] `map()` is pure — no network, no clock, no filesystem
- [ ] identity rule documented in the class docstring
- [ ] fixtures for the canonical case and every edge case listed for the source
- [ ] a parametrised test over your fixture directory
- [ ] two consecutive runs ⇒ `changed: 0` on the second
- [ ] `make validate` green
- [ ] `max_records` still 5 in `sources.yaml`

---

**Last executed:** 2026-08-31 — verified against the foundation: the registry,
`sources` listing, `--dry-run` path, `SourceUnreachable` / `NotImplementedError`
handling and the fixture loader all behave as described. No new adapter shipped
by this runbook's author.
