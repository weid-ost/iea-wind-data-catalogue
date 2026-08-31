# harvest/CONTRACT.md

**The interface document.** If you are building an adapter, the Tier-3
extraction layer, the reconciler or the Astro site, this file plus
`plans/02-static-plan.md` is everything you need. You will not be able to ask
anyone a question, so where this document is ambiguous, the code it describes
is authoritative — and if you find an ambiguity, fix it here in the same PR.

Everything described here exists and is tested. `uv run pytest` is green
before you start.

---

## 0. Ten-second orientation

```
sources.yaml ──► Adapter.harvest()  ──►  RawObservation
                        │
                 Adapter.map()      ──►  MappedObservation (identity + source.* + provenance)
                        │
                 run_adapter()      ──►  events/<slug>.jsonl        ← SOURCE OF TRUTH
                        │                (append-only, append-on-change)
                 resolve() / replay()
                        │
                 materialize_all()  ──►  records/<slug>.json        ← DERIVED, regenerable
                        │                (CKAN package dicts, byte-stable)
                 validate_records() ──►  the CKAN-compat gate
                        │
                 RunReport.write()  ──►  state/last-run.json        ← WRITTEN EVERY RUN
                        │
                     Astro          ──►  the site
```

Six rules that override anything you might otherwise reason your way into:

1. **`events/` is the truth; `records/` is derived.** Delete `records/` and
   `make materialize` rebuilds it byte-for-byte.
2. **Source metadata is never edited, only annotated** (ADR-0038).
3. **Unchanged source key ⇒ no event at all** (ADR-0026).
4. **No identifier a model produced is ever accepted unresolved.**
5. **Nothing fails the run.** Not an unreachable source, not a missing LLM,
   not a schema change upstream. It degrades and reports.
6. **The limit is five.**

---

## 1. The five-record cap

`harvest.DEFAULT_LIMIT == 5`.

Every `Adapter.harvest(limit)` yields **at most `limit`** observations.
`sources.yaml` sets `max_records: 5` for all seven sources, and
`run_adapter` takes `min(cli_limit, source.max_records)`. `_iter_limited`
truncates and logs a warning if an adapter yields more anyway, so forgetting is
a warning rather than three thousand records.

This is a deliberate prototype cap, not a performance choice. It stops a stray
run from hammering an upstream API, blowing a rate limit, or committing a
catalogue nobody has looked at. Raise it consciously and per-run:

```sh
uv run python -m harvest run --limit 50            # one run
make harvest LIMIT=50                              # same thing
```

Do **not** raise `max_records` in `sources.yaml` as part of an adapter PR.

---

## 2. The adapter interface

```python
from typing import Iterable
from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register, payload_hash
from harvest.models import MappedObservation, RawObservation, SourceNamespace, FieldProvenance

@register                                   # ← adds it to the registry
class ZenodoAdapter(Adapter):
    source_name = "zenodo"                  # ← == module name == sources.yaml key
    tier = 1                                # 1 API · 2 structured-ish · 3 HTML+LLM
    source_key_semantics = "InvenioRDM record revision id + version DOI"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]: ...
    def map(self, raw: RawObservation) -> MappedObservation: ...
```

You get `self.config` (a `SourceConfig` built from that source's
`sources.yaml` entry) and `self.client` (whatever the caller injected, or
`None`). You may open your own client in `harvest()`; implement `close()` if
you do — `run_adapter` always calls it, including on failure.

### `harvest(limit)` — talk to the source

Yields `RawObservation`s. **Verbatim.** No cleaning, no interpretation, no
mapping. What you yield here is what `fixtures/<source>/raw/<id>.json` holds.

```python
class RawObservation(BaseModel):
    source_system: str      # == self.source_name
    source_id: str          # the upstream's own id, as a string
    source_key: str         # the change token — see §3
    fetched_at: str         # ISO 8601 UTC, defaults to now
    url: str | None         # landing page, if cheap to know
    payload: dict           # the upstream response, VERBATIM
```

Network etiquette is not yours to reinvent — use `harvest.http`:

```python
from harvest.http import HarvestClient        # robots-aware, throttled, conditional GET
with HarvestClient() as client:
    result = client.get("https://zenodo.org/api/records?communities=iea_wind_task_43")
    if not result.ok:
        raise SourceUnreachable(result.error or f"HTTP {result.status_code}")
```

`HarvestClient` sends the project User-Agent
(`iea-wind-data-catalogue/0.1 (+https://github.com/thclark/iea-wind-data-catalogue; tom@octue.com)`),
honours `robots.txt` per host, throttles to 5 req/s, sends `If-None-Match` /
`If-Modified-Since` from `client.fetch_state`, and **never raises** on a
transport error — it returns a `FetchResult` with `error` set.

**Metadata and links only. The catalogue never mirrors a file.** If you are
writing bytes from a resource URL to disk, stop.

### `map(raw)` — interpret one payload

**Pure.** No network, no clock, no filesystem, no globals. This is what the
fixture tests call, and purity is the only reason they run offline. If you
need "now", it is already on `raw.fetched_at`.

```python
def map(self, raw: RawObservation) -> MappedObservation:
    metadata = raw.payload["metadata"]
    return MappedObservation(
        identity_key=identity_key(doi=raw.payload["conceptdoi"]),
        source_system="zenodo",
        source_id=raw.source_id,
        source_key=raw.source_key,
        source=SourceNamespace(
            title=metadata["title"],
            notes=sanitize_html(metadata.get("description")),
            doi=normalise_doi(raw.payload["conceptdoi"]),
            url=raw.payload["links"]["self_html"],
            source_urls=[raw.payload["links"]["self_html"]],
            license_raw=(metadata.get("license") or {}).get("id"),
            license_id=map_license((metadata.get("license") or {}).get("id"))[0],
            ...
        ),
        provenance={
            "title": FieldProvenance(extraction_method="api"),
            "license_id": FieldProvenance(extraction_method="pattern"),
        },
    )
```

### What `map()` must *not* do

* Write an event. `run_adapter` does that, once, for everyone.
* Check whether something changed. `run_adapter` does that too.
* Resolve a DOI over the network. Purity. Resolve in `harvest()`, or hand
  candidates to the reconciler.
* Invent a value the source did not state. An absent licence is
  `notspecified` + `license_mapped: false`, never a guess.

### Failing

```python
from harvest.adapters.base import SourceUnreachable
raise SourceUnreachable("listing endpoint requires a token (Spike 4)")
```

`run_adapter` catches `SourceUnreachable` (→ `reachable: false`),
`NotImplementedError` (→ `implemented: false`) and **every other exception**
(→ `reachable: false` with the exception text), records it in the
`SourceResult`, and lets the other six sources finish. An exception escaping
`run_adapter` is a bug in `run_adapter`, not in your adapter — but a `map()`
that throws on one record only loses that record, not the source.

### Registering

1. Create `harvest/adapters/<name>.py` — the file name is the source name.
2. Decorate the class with `@register`.
3. Ensure `<name>` is a key under `sources:` in `sources.yaml`.

That is all. `load_adapters()` imports `harvest.adapters.<name>` for every key
in `sources.yaml`; `get_adapter(name)` returns the class. A module that fails
to import is logged and skipped, so a broken adapter cannot take down the run.

---

## 3. The source key (ADR-0026)

One record-level change token per source, whose semantics the adapter owns.
`run_adapter` compares it to the last `scraped` event **for that same source
system** and, if unchanged, **skips the record and writes no event**.

| Source | Key | Note |
|---|---|---|
| `zenodo` | record revision id (with the version DOI) | InvenioRDM bumps it on metadata edits — **verify the field name against a live payload** |
| `datacite` | `attributes.updated` | reflects client metadata pushes |
| `crossref` | `deposited` | **not** `indexed`, which churns without content change |
| `github` | default-branch SHA + latest release tag + `hash(description, topics, licence)` | no single trustworthy field exists |
| `osti` | metadata-updated field if provided, else fallback | |
| `wdh` | dataset-updated field if provided, else fallback | |
| `ieawind` | normalised main-content hash | **the same value as the LLM cache key input** |

Universal fallback:

```python
from harvest.adapters.base import payload_hash
source_key = payload_hash({"description": repo["description"], "topics": repo["topics"]})
```

`payload_hash` is sorted-key JSON → SHA-256 → 16 hex. Deterministic across
runs and interpreters. **Hash only the fields that mean something.** Including
`pushed_at`, a star count or a `retrieved_at` timestamp makes every run a
change event, which turns append-on-change into append-always and defeats the
entire design.

A noisy key costs a redundant re-scrape and a no-op event. It cannot clobber a
human edit, because of §5.

---

## 4. Identity and slugs

```python
from harvest.identity import identity_key, slug_for_identity, slugify
```

`identity_key(...)` prefers, strictly in order:

1. **The DOI**, lowercase-normalised. `10.5281/ZENODO.123`,
   `https://doi.org/10.5281/zenodo.123`, `doi:10.5281/zenodo.123.` and
   `info:doi/10.5281/zenodo.123` all yield `10.5281/zenodo.123` (fixture
   `dc-05`). Normalisation strips `https://doi.org/`, `http://dx.doi.org/`,
   `doi:`, `info:doi/`, angle brackets, embedded whitespace and trailing
   prose punctuation, and lowercases.
2. **`source_system|source_id`**, e.g. `zenodo|1234567`, `osti|1854723`.
3. **`hash|<16 hex>`** of normalised title + first-author surname + year.
   **Fragile and documented as such**: a corrected upstream title produces a
   different key and therefore a second record (fixture `x-06`). Use only when
   1 and 2 are genuinely unavailable, and expect the reconciler to merge.

`identity_kind(key)` → `"doi"` | `"source"` | `"fragile"`.

### Slugs

`slug_for_identity(key)` renders the key filesystem- and CKAN-safe. It is
**the same string** for all four of these, which is the point:

* the CKAN `package.name`
* `records/<slug>.json`
* `events/<slug>.jsonl`
* the site URL `/record/<slug>/`

| identity key | slug |
|---|---|
| `10.5281/zenodo.1234566` | `doi-10-5281-zenodo-1234566` |
| `zenodo|1234567` | `zenodo-1234567` |
| `github|IEA-Task-43/digital-wra-data-standard` | `github-iea-task-43-digital-wra-data-standard` |
| `hash|ab12cd34ef567890` | `hash-ab12cd34ef567890` |

Over-long keys truncate to 91 characters plus `-<8 hex of the full key>`, so
the mapping stays injective within CKAN's 100-character limit.

**The slug depends on the identity key and nothing else** — never on the
title. A retitled dataset keeps its citable URL. This is why
`plans/02-static-plan.md` §2.1 can promise stable record URLs.

**Collisions.** The slug is short and lossy; the identity key is not. Two
identities that would render to one slug (`zenodo|a.b` and `zenodo|a-b`) are
refused at `append_event` — the incumbent identity owns the file — and caught
again in `materialize_all` for hand-written logs. `run_adapter` turns the
refusal into one logged, skipped record, not a failed run. If you hit this,
your `source_id` needs disambiguating, not the slugifier loosening.

> **Note for anyone reading ADR-0037 literally.** It says
> `events/<identity-key>.jsonl`. An identity key contains `/` and `|`, so the
> file *stem* is the slug and the unabbreviated `identity_key` is a field on
> every line. Same thing, spelled so it can exist on a filesystem.

`slugify(text)` is the general transliterating slugifier — `Søren Ø. Müller` →
`soren-o-muller`, `Ægir & Þór` → `aegir-and-thor` (fixture `zen-10`).
Diacritics are **preserved in display and transliterated in slugs**, never
dropped.

---

## 5. The two namespaces, and the rules (ADR-0038)

### `source.*` — what upstream says

`SourceNamespace`, all fields optional, `extra="allow"`. Verbatim in content,
mapped only in field *names*.

| field | type | notes |
|---|---|---|
| `title` | `str` | verbatim, typos and all |
| `notes` | `str` | description; **run it through `sanitize_html` first** |
| `doi` | `str` | normalised, and **resolved**, before it lands here |
| `url` | `str` | canonical landing page |
| `source_urls` | `list[str]` | **set-valued** |
| `authors` | `list[Author]` | `Author` needs only `name` (fixture `cr-06`) |
| `publisher` | `str` | |
| `published_date` | `str` | ISO 8601; may be year-only. **Never fabricate a month** (`cr-02`) |
| `version` | `str` | |
| `license_raw` | `str` | exactly what the source said |
| `license_id` | `str` | mapped through `harvest.licenses.map_license` |
| `resource_kind` | `str` | `dataset` `publication` `software` `report` `model` `other` |
| `access_status` | `str` | `open` `restricted` `embargoed` `registration-required` `metadata-only` `unknown` |
| `embargo_date` | `str` | |
| `container` | `str` | journal / series / community title |
| `keywords` | `list[str]` | **set-valued**; become CKAN tags via `tagify` |
| `resources` | `list[dict]` | CKAN resource dicts. **Links, never mirrors** |
| `related_identifiers` | `list[dict]` | **set-valued**; `{relation, identifier, identifier_type}` |
| `iea_task` | `list[str]` | **set-valued**; group names, when the source states one |
| `withdrawn` | `bool` | |
| `extra` | `dict` | anything else, verbatim; never rendered unless a curator opts in |

**Replaced wholesale** on a source-key change. There is no field-level merge of
successive scrapes: a field the source stops sending disappears from the
record, because that is what the source now says.

When several systems describe one identity (fixture `x-01`), each system's
block is replaced independently and the blocks compose by the `precedence`
declared in `sources.yaml` — lower wins for scalars, set-valued fields union
across all of them. DataCite (10) outranks Crossref (20) outranks Zenodo (30)
… outranks `ieawind` (90).

### `local.*` — what we add

`LocalNamespace`, `extra="allow"`. Additive only.

| field | type | notes |
|---|---|---|
| `iea_task` | `list[str]` | **set-valued** — the one ADR-0038 names explicitly |
| `resource_kind` | `str` | where the source does not type it |
| `access_status` | `str` | |
| `curator_notes` | `list[dict]` | `{field?, note, added_at?}` — rendered beside the value |
| `links` | `list[dict]` | `{url, label}` |
| `source_urls` | `list[str]` | |
| `suppressed` | `bool` | noise; retained but not listed |
| `pinned` | `bool` | a pinned Tier-3 extraction (§4.3 of the plan) |
| `pin_source_key` | `str` | the content hash the pin was made against |

**Latest local event wins, per field** — except set-valued fields, which union
across every annotation.

### Resolution, in one table

| situation | result |
|---|---|
| only `source` has the field | source value |
| only `local` has the field | local value |
| both, **scalar** | **source displaces local**; local retained in the log; a `displacement` notice appears in `resolved.notices` and the run report (`x-03`) |
| both, **set-valued** | **union** — a Zenodo community adding Task 43 never erases a hand-added Task 49 (`x-04`) |
| a `withdrawn` event exists | `withdrawn: true`, metadata retained, record still materialised (`zen-12`, ADR-0027) |

`resolve()` raises implicit displacement notices itself, so the behaviour is
correct even if nobody remembered to append a `displacement_notice` event.

---

## 6. The event log

`events/<slug>.jsonl`, one JSON object per line, append-only, ordered by *our*
observation time. Written **only** through `harvest.events.append_event` and
the convenience writers `record_scrape` / `annotate` / `withdraw` /
`raise_notice`.

```python
class Event(BaseModel):
    observed_at: str          # ISO 8601 UTC, second precision, "Z"
    event_type: str           # scraped | annotated | withdrawn
                              # | displacement_notice | pin_notice
    identity_key: str
    source_key: str | None
    source_system: str | None
    source_id: str | None
    source: dict              # the SourceNamespace, JSON-dumped
    local: dict               # the LocalNamespace fields this event sets
    provenance: dict[str, FieldProvenance]
    notice: dict | None       # displacement / pin payload
    actor: str | None         # "harvest/zenodo" | "curator:tom" | "reconcile"
    note: str | None
```

Serialisation is `sort_keys=True, separators=(",", ":"), ensure_ascii=False`,
`exclude_none=True`, one line, `\n`-terminated.

### A full `scraped` line

Written as one line; shown pretty here. This is real output from
`fixtures/zenodo/zen-01-canonical.json`.

```json
{
  "actor": "harvest/zenodo",
  "event_type": "scraped",
  "identity_key": "10.5281/zenodo.1234566",
  "local": {},
  "observed_at": "2026-08-24T03:11:07Z",
  "provenance": {
    "authors":       { "extraction_method": "api" },
    "doi":           { "extraction_method": "api" },
    "iea_task":      { "extraction_method": "pattern" },
    "license_id":    { "extraction_method": "pattern" },
    "notes":         { "extraction_method": "api" },
    "resource_kind": { "extraction_method": "api" },
    "title":         { "extraction_method": "api" }
  },
  "source": {
    "access_status": "open",
    "authors": [
      { "affiliation": "Technical University of Denmark",
        "name": "Müller, Søren Ø.", "orcid": "0000-0002-1825-0097" },
      { "affiliation": "National Renewable Energy Laboratory",
        "name": "Okafor, Chidi", "orcid": "0000-0001-5109-3700" }
    ],
    "doi": "10.5281/zenodo.1234566",
    "extra": {
      "zenodo_concept_recid": "1234566",
      "zenodo_record_id": 1234567,
      "zenodo_version_doi": "10.5281/zenodo.1234567"
    },
    "iea_task": ["task-43"],
    "keywords": ["lidar", "wind energy", "Østerild", "remote sensing"],
    "license_id": "cc-by",
    "license_raw": "cc-by-4.0",
    "notes": "<p>Ten-minute statistics from a scanning lidar deployed at the Østerild National Test Centre during 2021.</p>",
    "published_date": "2024-06-01",
    "publisher": "Zenodo",
    "related_identifiers": [
      { "identifier": "10.5281/zenodo.1234566",
        "identifier_type": "DOI", "relation": "IsVersionOf" }
    ],
    "resource_kind": "dataset",
    "resources": [
      { "format": "csv", "name": "osterild-lidar-2021.csv",
        "url": "https://zenodo.org/records/1234567/files/osterild-lidar-2021.csv" }
    ],
    "source_urls": ["https://zenodo.org/records/1234567"],
    "title": "Lidar measurements from the Østerild campaign, 2021",
    "url": "https://zenodo.org/records/1234567",
    "version": "2.0",
    "withdrawn": false
  },
  "source_id": "1234567",
  "source_key": "3",
  "source_system": "zenodo"
}
```

### A full `annotated` line, exactly as written

```json
{"actor":"curator:tom","event_type":"annotated","identity_key":"10.5281/zenodo.1234566","local":{"curator_notes":[{"added_at":"2026-08-28T09:00:00Z","field":"license_id","note":"OST note: the licence stated at source appears incorrect; see the LICENCE file in the archive."}],"iea_task":["task-49"]},"note":"Task 49 attribution from the IDEA workshop list","observed_at":"2026-08-28T09:00:00Z","provenance":{},"source":{}}
```

### Reading it

```python
from harvest.events import read_events, resolve, replay, has_changed, last_source_key

has_changed(key, "zenodo", "rev-4")   # False ⇒ skip, write NOTHING
resolve(key)                          # -> ResolvedRecord (source, local, effective, notices)
replay(key)                           # -> the CKAN package dict
```

`resolve()` also accepts an in-memory `events=[...]` list, which is how the
reconciliation tests avoid the filesystem entirely.

---

## 7. The record

A CKAN `package` dict, one JSON file per record, **directly POSTable to
`package_create` with no transformation**. That is the promotion contract
(ADR-0021) and the CKAN-compat gate enforces it on every run.

Written by `harvest.materialize.dump_record`:
`indent=2, sort_keys=True, ensure_ascii=False, separators=(",", ": ")`, one
trailing newline. **Byte-stable**: materialise twice, get identical bytes; a
run in which nothing changed produces no diff in `records/`.

### A full record

`records/doi-10-5281-zenodo-1234566.json`, from the scrape and annotation above:

```json
{
  "extras": [
    { "key": "access_status", "value": "open" },
    { "key": "authors", "value": "[{\"affiliation\":\"Technical University of Denmark\",\"name\":\"Müller, Søren Ø.\",\"orcid\":\"0000-0002-1825-0097\"},{\"affiliation\":\"National Renewable Energy Laboratory\",\"name\":\"Okafor, Chidi\",\"orcid\":\"0000-0001-5109-3700\"}]" },
    { "key": "curator_notes", "value": "[{\"added_at\":\"2026-08-28T09:00:00Z\",\"field\":\"license_id\",\"note\":\"OST note: the licence stated at source appears incorrect; see the LICENCE file in the archive.\"}]" },
    { "key": "doi", "value": "10.5281/zenodo.1234566" },
    { "key": "first_seen", "value": "2026-08-24T03:11:07Z" },
    { "key": "identity_key", "value": "10.5281/zenodo.1234566" },
    { "key": "identity_kind", "value": "doi" },
    { "key": "iea_task", "value": "[\"task-43\",\"task-49\"]" },
    { "key": "last_seen", "value": "2026-08-28T09:00:00Z" },
    { "key": "license_mapped", "value": "true" },
    { "key": "license_raw", "value": "cc-by-4.0" },
    { "key": "lifecycle_state", "value": "active" },
    { "key": "provenance", "value": "{\"authors\":{\"extraction_method\":\"api\",\"source_system\":\"zenodo\"},\"doi\":{\"extraction_method\":\"api\",\"source_system\":\"zenodo\"},\"iea_task\":{\"extraction_method\":\"pattern\",\"source_system\":\"zenodo\"},\"license_id\":{\"extraction_method\":\"pattern\",\"source_system\":\"zenodo\"},\"notes\":{\"extraction_method\":\"api\",\"source_system\":\"zenodo\"},\"resource_kind\":{\"extraction_method\":\"api\",\"source_system\":\"zenodo\"},\"title\":{\"extraction_method\":\"api\",\"source_system\":\"zenodo\"}}" },
    { "key": "published_date", "value": "2024-06-01" },
    { "key": "publisher", "value": "Zenodo" },
    { "key": "related_identifiers", "value": "[{\"identifier\":\"10.5281/zenodo.1234566\",\"identifier_type\":\"DOI\",\"relation\":\"IsVersionOf\"}]" },
    { "key": "resource_kind", "value": "dataset" },
    { "key": "source_id", "value": "1234567" },
    { "key": "source_key", "value": "3" },
    { "key": "source_system", "value": "zenodo" },
    { "key": "source_systems", "value": "[\"zenodo\"]" },
    { "key": "source_url", "value": "https://zenodo.org/records/1234567" },
    { "key": "source_urls", "value": "[\"https://zenodo.org/records/1234567\"]" },
    { "key": "withdrawn", "value": "false" }
  ],
  "groups": [ { "name": "task-43" }, { "name": "task-49" } ],
  "license_id": "cc-by",
  "name": "doi-10-5281-zenodo-1234566",
  "notes": "<p>Ten-minute statistics from a scanning lidar deployed at the Østerild National Test Centre during 2021.</p>",
  "private": false,
  "resources": [
    { "format": "csv", "name": "osterild-lidar-2021.csv",
      "url": "https://zenodo.org/records/1234567/files/osterild-lidar-2021.csv" }
  ],
  "state": "active",
  "tags": [
    { "name": "lidar" }, { "name": "osterild" },
    { "name": "remote-sensing" }, { "name": "wind-energy" }
  ],
  "title": "Lidar measurements from the Østerild campaign, 2021",
  "url": "https://zenodo.org/records/1234567",
  "version": "2.0"
}
```

### Why `state` is `"active"` on a withdrawn record

CKAN's `state` is its own row lifecycle: `deleted` means "hidden and
purgeable", which is exactly what ADR-0027 forbids. So `state` stays `active`
for every record the catalogue retains, and withdrawal lives in
`extras.lifecycle_state` (`active` | `archived` | `withdrawn`),
`extras.withdrawn` and `extras.withdrawn_at`. The site renders the withdrawal
banner from those; CKAN still ingests the record on promotion day; the URL and
the citation survive.

### Extras encoding — the rules

* **Every `extras` value is a string.** CKAN accepts nothing else.
* Lists and objects are JSON **inside** that string:
  `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
* Booleans are the strings `"true"` / `"false"`.
* An empty value is **omitted entirely**, not written as `""`. On a record
  page, "absent" and "empty" say different things.
* `extras` is sorted by key; so are `tags` and `groups`. Determinism.

The full key list is `harvest.materialize.EXTRA_KEYS`, and every key in it is
documented in `schema/ckan-scheming.json` — a test enforces that those two
agree. Add a key to one, add it to the other.

### Provenance encoding — read this before rendering anything

`extras.provenance` is a JSON object string, `{field: FieldProvenance}`:

```jsonc
{
  "title":         { "extraction_method": "api",     "source_system": "zenodo" },
  "doi":           { "extraction_method": "pattern", "source_system": "ieawind" },
  "resource_kind": { "extraction_method": "llm",     "source_system": "ieawind",
                     "model": "openai/gpt-4o-mini",
                     "prompt_version": "v1",
                     "confidence": 0.72 }
}
```

* `extraction_method` is `api` | `pattern` | `llm`.
* `llm` **requires** `model` and `prompt_version` — the model raises otherwise
  — and should carry `confidence`.
* Absent keys are omitted, so an ordinary field is exactly
  `{"extraction_method": "api"}`.
* `pinned: true` appears only on a pinned Tier-3 extraction.

Every field whose `extraction_method` is `llm` **must render with a visible
machine-inferred badge** (ADR-0028, fixture `x-05`). The design system reserves
violet for exactly this. Being honest about which metadata is inferred is the
difference between a credible catalogue and one that gets dismissed the first
time someone spots a confidently wrong abstract.

---

## 8. The CKAN-compat gate

`uv run python -m harvest validate` — exits non-zero and prints **every**
violation, not the first.

| field | rule |
|---|---|
| `name` | `^[a-z0-9_-]{2,100}$`, unique, matches the filename stem |
| `title` | non-empty |
| `tags[].name` | `^[A-Za-z0-9._-]{2,100}$`, no duplicates. Use `tagify()` |
| `license_id` | present in `harvest.licenses.LICENSE_REGISTER` |
| `extras[].value` | **string**; keys non-empty and unique |
| `resources[].url` | present |
| `owner_org` | exists in `organizations.yaml` |
| `groups[].name` | exists in `groups.yaml` |
| `state` | `active` \| `deleted` \| `draft` |

Licences map through `harvest.licenses.map_license(raw) -> (license_id, mapped)`.
`mapped is False` means the source said something the table did not recognise:
`license_id` becomes `notspecified` **and the run report flags it** (fixtures
`zen-08`, `dc-09`). An *absent* licence also yields `notspecified` but with
`mapped=True` — nothing went wrong, the source simply said nothing. **Never
infer an open licence.**

---

## 9. Fixtures

Layout and capture instructions: `fixtures/README.md`. Inventory:
`fixtures/fixtures-catalogue.md` (specification — do not edit).

```
fixtures/<source>/raw/<id>.json     the upstream payload, VERBATIM
fixtures/<source>/<id>.json         the expectation
```

The expectation declares `"fixture_kind": "source_namespace"` (what `map()`
should produce: `identity_key`, `source_key`, `source`, `provenance`) or
`"record"` (a whole CKAN package). Reference implementations:
`fixtures/zenodo/zen-01-canonical.json` and
`fixtures/cross-cutting/x-08-ckan-invalid.json`.

`tests/test_fixtures.py` already walks the whole tree and validates every
fixture generically, so a malformed fixture fails immediately. Parametrize your
adapter test over your directory:

```python
@pytest.mark.parametrize("fixture", load_fixtures("zenodo"), ids=lambda f: f["fixture_id"])
def test_map(fixture):
    raw = RawObservation(
        source_system="zenodo", source_id=fixture["source_id"],
        source_key=fixture["source_key"],
        payload=json.loads((FIXTURES / "zenodo" / fixture["raw"]).read_text()),
    )
    mapped = ZenodoAdapter().map(raw)
    assert mapped.identity_key == fixture["identity_key"]
    assert mapped.source.model_dump(exclude_none=True) == fixture["source"]
```

**Every harvest change ships with its fixture.** New behaviour without one is
not finished.

---

## 10. Tier 3 and the LLM boundary

`harvest/extract.py` documents the interface precisely; the implementation is
Track H's. The boundary, restated because it is the easiest thing to erode:

* **Never where a structured API exists.** Zenodo, DataCite, Crossref, GitHub
  and OSTI return clean JSON. Tier 1 is deterministic, always.
* **Identifiers never come from the model.** Regex them out with
  `harvest.doi.extract_dois`, pass the list in as context, ask the model to
  *assign* them, then `resolve_or_drop` every one. Combined, hallucinated
  identifiers become structurally impossible rather than merely unlikely.
* **Extraction, not generation.** Copy titles and abstracts; never summarise.
* **Sanitise before the model sees anything** — `sanitize.html_to_text` after
  `trafilatura`. Prompt injection through a harvested page is a live attack
  surface for a system that then writes records.
* **The run never fails on LLM unavailability** (ADR-0031). `extract()`
  returns `None`; the page goes on `state/pending-extraction.json`; the run
  succeeds (fixture `x-07`). Somebody runs `make extract` later, or never.
* Cache key is `sha256(content + prompt_version + model_id)`, entries live in
  `cache/` and are **committed** — a rebuild replays the cache instead of
  re-inferring, which is the only reason a reproducible rebuild and an AI
  harvester can coexist.

---

## 11. What the site reads

Astro is a **renderer**. It never writes into `records/`, and no
framework-specific field ever enters the record format (ADR-0032). The record
you glob is the record CKAN will receive.

```js
// site/src/content.config.ts
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';

const records = defineCollection({
  loader: glob({ pattern: '*.json', base: '../records' }),
  // The Zod schema here IS the validate-ckan-compat gate, per plan §2.2:
  // slug rules, tag charset, licence register. Fail the build on a bad record.
});
```

| what | where | for |
|---|---|---|
| records | `records/*.json` via glob | list, record pages, JSON-LD, Pagefind index |
| freshness | `state/last-run.json` → `finished_at` | "last updated" banner; **warning state past 45 days** (fixture `r-08`) |
| backlog | `state/last-run.json` → `pending_extraction` | shown next to the freshness banner |
| unreachable sources | `state/last-run.json` → `unreachable_sources` | honest degradation notice |
| notices | `state/last-run.json` → `notices` | the curator's short monthly read |
| tasks | `groups.yaml` | task chips, task pages, facet labels |
| institutions | `organizations.yaml` | institution facet |

Per-record rendering notes:

* `extras.provenance` → **violet machine-inferred badge** on every `llm` field.
* `extras.lifecycle_state == "withdrawn"` → the withdrawal banner,
  `role="status"`, at the top of `<main>`. **Keep the page and the URL.**
* `extras.curator_notes` → rendered *beside* the value it annotates, with the
  upstream value still shown verbatim. Both truths on the page (`x-10`).
* `extras.access_status` → availability badge. Never imply a download that
  requires an account or is embargoed.
* `extras.source_urls` → "view at source" links, one per contributing system.
* `extras.iea_task` → task chips; resolve display names from `groups.yaml`.
* Pagefind filters come from `data-pagefind-filter` attributes on the record
  page: task, resource kind, year, licence, source system, institution.
* `/dev/components` is `data-pagefind-ignore` and `noindex`.

---

## 12. Commands

```sh
uv sync --frozen --dev                       # the pinned environment (ADR-0034)
uv run pytest                                # tests
uv run python -m harvest run --limit 5       # harvest → events → records → validate → report
uv run python -m harvest run --source zenodo # one source
uv run python -m harvest materialize         # replay events/ into records/
uv run python -m harvest validate            # the CKAN gate alone
uv run python -m harvest extract             # drain the Tier-3 pending queue
uv run python -m harvest report              # print state/last-run.json
uv run python -m harvest sources             # what is configured, and its adapter
```

`make harvest | materialize | validate | test | extract | build-tokens | site | gates`
wrap the same things.

`$HARVEST_ROOT` overrides the repository root everywhere, which is how the
tests keep out of the real `events/`.

---

## 13. Dependency discipline

Direct runtime dependencies are capped at **four**: `httpx`, `trafilatura`,
`pydantic`, `pyyaml`. Dev adds `pytest`. All pinned exactly; `.python-version`
pins CPython 3.12.8; `uv.lock` is committed; CI runs `uv sync --frozen`.

**No fifth dependency.** Not `beautifulsoup4` (the sanitiser is stdlib
`html.parser`), not `requests`, not `python-dateutil`, and above all **no LLM
SDK** — GitHub Models is OpenAI-compatible, so inference is a POST (ADR-0035).
If you are certain you need a fifth, that is an ADR, not a commit.

---

## 14. Track ownership

| Track | Owns | Stub |
|---|---|---|
| A | Zenodo | `harvest/adapters/zenodo.py` |
| B | DataCite | `harvest/adapters/datacite.py` |
| C | Crossref | `harvest/adapters/crossref.py` |
| D | GitHub | `harvest/adapters/github.py` |
| E | OSTI | `harvest/adapters/osti.py` |
| F | iea-wind.org (Tier 3) | `harvest/adapters/ieawind.py` |
| G | Wind Data Hub | `harvest/adapters/wdh.py` |
| H | LLM extraction, cache, pending queue | `harvest/extract.py` |
| I | Reconciliation, merges, notices, link checking | new module; uses `events.resolve` |
| J | The Astro site | `site/` |

Each stub's docstring names its source key, its identity rule, its fixtures and
the traps specific to it. Read yours first.

**Shared files.** `harvest/models.py`, `identity.py`, `doi.py`, `licenses.py`,
`events.py`, `materialize.py`, `ckan_compat.py`, `sanitize.py`, `http.py` and
this document are the foundation. If your track needs a change to one of them,
make it additive and ship it with a test — another nine tracks are reading the
same file at the same time.
