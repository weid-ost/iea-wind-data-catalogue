---
type: reference
id: record-format
status: current
date: 2026-08-31
related: [architecture, adr-0021-canonical-record-is-a-ckan-package-dict, adr-0037-events-are-the-source-of-truth, adr-0038-source-metadata-is-never-updated-only-annotated]
tags: [schema, records, events]
---

# The record and event formats

Two shapes, strictly ordered. The event log is written; the record is
generated from it.

```
RawObservation  --map()-->  Event  --replay()-->  CKAN package dict
   adapter              events/<slug>.jsonl        records/<slug>.json
  (verbatim)            (SOURCE OF TRUTH)              (DERIVED)
```

Authority: `harvest/models.py`, `harvest/materialize.py`,
`harvest/identity.py` and `harvest/CONTRACT.md` §§4–8. This page annotates
them; it does not replace them.

Related: [[architecture]] · [[adr-0021-canonical-record-is-a-ckan-package-dict]] ·
[[adr-0037-events-are-the-source-of-truth]] ·
[[adr-0038-source-metadata-is-never-updated-only-annotated]] ·
[[correct-a-record]] · [[materialize-and-validate]]

---

## 1. Identity and slugs

### 1.1 The identity key

`harvest.identity.identity_key(...)` prefers, strictly in order:

| # | Rule | Example | `identity_kind()` |
|---|---|---|---|
| 1 | the lowercase-normalised **DOI** | `10.5281/zenodo.1234566` | `doi` |
| 2 | `source_system\|source_id` | `zenodo\|1234567`, `osti\|1854723` | `source` |
| 3 | `hash\|<16 hex>` of normalised title + first-author surname + year | `hash\|ab12cd34ef567890` | `fragile` |

DOI normalisation strips `https://doi.org/`, `http://dx.doi.org/`, `doi:`,
`info:doi/`, angle brackets, embedded whitespace and trailing prose
punctuation, and lowercases. `10.5281/ZENODO.123`,
`https://doi.org/10.5281/zenodo.123`, `doi:10.5281/zenodo.123.` and
`info:doi/10.5281/zenodo.123` therefore all yield one identity (fixture
`dc-05`).

Rule 3 is **fragile and documented as such**: a corrected upstream title
produces a different key and therefore a second record (fixture `x-06`). Use it
only when 1 and 2 are genuinely unavailable, and expect the reconciler to
propose a merge.

### 1.2 The slug

`harvest.identity.slug_for_identity(key)` renders the key once, and the result
is **the same string** in four places — that is the point:

- the CKAN `package.name`
- `records/<slug>.json`
- `events/<slug>.jsonl`
- the site URL `/record/<slug>/`

| identity key | slug |
|---|---|
| `10.5281/zenodo.1234566` | `doi-10-5281-zenodo-1234566` |
| `zenodo\|1234567` | `zenodo-1234567` |
| `github\|IEA-Task-43/digital-wra-data-standard` | `github-iea-task-43-digital-wra-data-standard` |
| `hash\|ab12cd34ef567890` | `hash-ab12cd34ef567890` |

Diacritics are **preserved in display and transliterated in slugs**, never
dropped: `Søren Ø. Müller` → `soren-o-muller` (fixture `zen-10`). Over-long
keys truncate to 91 characters plus `-<8 hex of the full key>`, keeping the
mapping injective inside CKAN's 100-character limit.

**The slug depends on the identity key and nothing else** — never on the title.
A retitled dataset keeps its citable URL.

### 1.3 Collisions

Two identities that would render to one slug (`zenodo|a.b` and `zenodo|a-b`)
are refused at `append_event` — the incumbent identity owns the file — and
caught again in `materialize_all` for hand-written logs, where they surface as
a `name` violation. `run_adapter` turns the refusal into one logged, skipped
record, not a failed run. If you hit this, your `source_id` needs
disambiguating; the slugifier does not need loosening.

---

## 2. The event log

`events/<slug>.jsonl`. One JSON object per line, append-only, ordered by *our*
observation time. Written **only** through `harvest.events.append_event` and
the convenience writers `record_scrape` / `annotate` / `withdraw` /
`raise_notice`.

Serialisation is `sort_keys=True, separators=(",", ":"), ensure_ascii=False`,
`exclude_none=True`, one line, `\n`-terminated.

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

**Append-on-change only.** A scrape whose source key matches the last
`scraped` event *for that same source system* writes nothing at all; the fact
that the run happened is recorded in `state/last-run.json`
([[adr-0026-change-detection-by-source-key]]). Growth stays proportional to
real change.

> ADR-0037 speaks of `events/<identity-key>.jsonl`. An identity key contains
> `/` and `|`, so the file *stem* is the slug and the unabbreviated
> `identity_key` is a field on every line. Same thing, spelled so it can exist
> on a filesystem.

### 2.1 A `scraped` line, annotated

Written as one line; shown pretty here. This is the shape
`fixtures/zenodo/zen-01-canonical.json` describes.

```jsonc
{
  "actor": "harvest/zenodo",              // who wrote it: harvest/<source>, curator:<name>, reconcile
  "event_type": "scraped",
  "identity_key": "10.5281/zenodo.1234566",  // the CONCEPT doi, never the version doi (zen-02)
  "local": {},                            // a scrape never sets local.*
  "observed_at": "2026-08-24T03:11:07Z",  // OUR clock, not the source's
  "provenance": {                         // per field, not per record
    "authors":       { "extraction_method": "api" },
    "doi":           { "extraction_method": "api" },
    "iea_task":      { "extraction_method": "pattern" },   // inferred from the community slug
    "license_id":    { "extraction_method": "pattern" },   // mapped through the licence table
    "notes":         { "extraction_method": "api" },
    "resource_kind": { "extraction_method": "api" },
    "title":         { "extraction_method": "api" }
  },
  "source": {                             // VERBATIM upstream, replaced wholesale on change
    "access_status": "open",
    "authors": [
      { "affiliation": "Technical University of Denmark",
        "name": "Müller, Søren Ø.", "orcid": "0000-0002-1825-0097" },
      { "affiliation": "National Renewable Energy Laboratory",
        "name": "Okafor, Chidi", "orcid": "0000-0001-5109-3700" }
    ],
    "doi": "10.5281/zenodo.1234566",
    "extra": {                            // anything the mapping has no field for; never rendered
      "zenodo_concept_recid": "1234566",
      "zenodo_record_id": 1234567,
      "zenodo_version_doi": "10.5281/zenodo.1234567"
    },
    "iea_task": ["task-43"],              // SET-VALUED: unions on collision
    "keywords": ["lidar", "wind energy", "Østerild", "remote sensing"],
    "license_id": "cc-by",                // mapped
    "license_raw": "cc-by-4.0",           // exactly what the source said
    "notes": "<p>Ten-minute statistics from a scanning lidar …</p>",  // sanitize_html'd
    "published_date": "2024-06-01",       // may be year-only; NEVER fabricate a month (cr-02)
    "publisher": "Zenodo",
    "related_identifiers": [
      { "identifier": "10.5281/zenodo.1234566",
        "identifier_type": "DOI", "relation": "IsVersionOf" }
    ],
    "resource_kind": "dataset",
    "resources": [                        // LINKS, never mirrors
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
  "source_key": "3",                      // the change token — ADR-0026
  "source_system": "zenodo"
}
```

### 2.2 An `annotated` line, exactly as written

```json
{"actor":"curator:tom","event_type":"annotated","identity_key":"10.5281/zenodo.1234566","local":{"curator_notes":[{"added_at":"2026-08-28T09:00:00Z","field":"license_id","note":"OST note: the licence stated at source appears incorrect; see the LICENCE file in the archive."}],"iea_task":["task-49"]},"note":"Task 49 attribution from the IDEA workshop list","observed_at":"2026-08-28T09:00:00Z","provenance":{},"source":{}}
```

Note what is *absent*: no `source` content, no `source_key`. An annotation can
only ever add to `local.*`.

---

## 3. The two namespaces

### 3.1 `source.*` — what upstream says

`harvest.models.SourceNamespace`. All fields optional, `extra="allow"`.
Verbatim in content; mapped only in field *names*.

| field | type | notes |
|---|---|---|
| `title` | `str` | verbatim, typos and all |
| `notes` | `str` | description; **run it through `sanitize_html` first** (fixture `zen-07`) |
| `doi` | `str` | normalised, and **resolved**, before it lands here |
| `url` | `str` | canonical landing page |
| `source_urls` | `list[str]` | **set-valued** |
| `authors` | `list[Author]` | `Author` needs only `name` (fixture `cr-06`) |
| `publisher` | `str` | |
| `published_date` | `str` | ISO 8601; may be year-only. Never fabricate a month |
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
declared in `sources.yaml` — **lower wins** for scalars, set-valued fields
union across all of them. Current register: DataCite 10, Crossref 20,
Zenodo 30, OSTI 40, WDH 50, GitHub 60, `ieawind` 90.

### 3.2 `local.*` — what we add

`harvest.models.LocalNamespace`, `extra="allow"`. Additive only.

| field | type | notes |
|---|---|---|
| `iea_task` | `list[str]` | **set-valued** — the one ADR-0038 names explicitly |
| `resource_kind` | `str` | where the source does not type it |
| `access_status` | `str` | |
| `curator_notes` | `list[dict]` | `{field?, note, added_at?}` — rendered beside the value |
| `links` | `list[dict]` | `{url, label}` |
| `source_urls` | `list[str]` | |
| `suppressed` | `bool` | noise; retained but not listed |
| `pinned` | `bool` | a pinned Tier-3 extraction |
| `pin_source_key` | `str` | the content hash the pin was made against |

**Latest local event wins, per field** — except set-valued fields, which union
across every annotation.

The set-valued fields are declared once, in
`harvest.models.SET_VALUED_FIELDS`: `iea_task`, `source_urls`, `keywords`,
`related_identifiers`, `curator_notes`, `links`.

### 3.3 Resolution, in one table

| situation | result |
|---|---|
| only `source` has the field | source value |
| only `local` has the field | local value |
| both, **scalar** | **source displaces local**; local retained in the log; a `displacement` notice appears in `resolved.notices` and the run report (`x-03`) |
| both, **set-valued** | **union** — a Zenodo community adding Task 43 never erases a hand-added Task 49 (`x-04`) |
| a `withdrawn` event exists | `withdrawn: true`, metadata retained, record still materialised (`zen-12`) |

`resolve()` raises implicit displacement notices itself, so the behaviour is
correct even if nobody remembered to append a `displacement_notice` event.

---

## 4. The record

A CKAN `package` dict, one JSON file per record, **directly POSTable to
`package_create` with no transformation**. That is the promotion contract
([[adr-0021-canonical-record-is-a-ckan-package-dict]]) and the CKAN-compat gate
enforces it on every run.

Written by `harvest.materialize.dump_record`:
`indent=2, sort_keys=True, ensure_ascii=False, separators=(",", ": ")`, one
trailing newline. **Byte-stable**: materialise twice, get identical bytes; a
run in which nothing changed produces no diff in `records/`, so the only churn
in a no-op heartbeat commit is `state/last-run.json`.

### 4.1 A full record, annotated

`records/doi-10-5281-zenodo-1234566.json`, produced by the scrape in §2.1 plus
the annotation in §2.2. This is real output — the procedure that generates it
is [[correct-a-record]].

```jsonc
{
  "extras": [                                   // ALL VALUES ARE STRINGS. Sorted by key.
    { "key": "access_status", "value": "open" },
    { "key": "authors", "value": "[{\"affiliation\":\"Technical University of Denmark\",…}]" },
    { "key": "curator_notes", "value": "[{\"added_at\":\"2026-08-28T09:00:00Z\",\"field\":\"license_id\",…}]" },
    { "key": "doi", "value": "10.5281/zenodo.1234566" },
    { "key": "first_seen", "value": "2026-08-24T03:11:07Z" },   // our clock, first event
    { "key": "identity_key", "value": "10.5281/zenodo.1234566" },
    { "key": "identity_kind", "value": "doi" },                 // doi | source | fragile
    { "key": "iea_task", "value": "[\"task-43\",\"task-49\"]" },  // UNION: 43 from Zenodo, 49 by hand
    { "key": "last_seen", "value": "2026-08-28T09:00:00Z" },
    { "key": "license_mapped", "value": "true" },               // false ⇒ flagged in the run report
    { "key": "license_raw", "value": "cc-by-4.0" },
    { "key": "lifecycle_state", "value": "active" },            // active | archived | withdrawn
    { "key": "provenance", "value": "{\"title\":{\"extraction_method\":\"api\",\"source_system\":\"zenodo\"},…}" },
    { "key": "published_date", "value": "2024-06-01" },
    { "key": "publisher", "value": "Zenodo" },
    { "key": "related_identifiers", "value": "[{\"identifier\":\"10.5281/zenodo.1234566\",…}]" },
    { "key": "resource_kind", "value": "dataset" },
    { "key": "source_id", "value": "1234567" },
    { "key": "source_key", "value": "3" },
    { "key": "source_system", "value": "zenodo" },              // the last system to scrape
    { "key": "source_systems", "value": "[\"zenodo\"]" },        // every system that has (x-01)
    { "key": "source_url", "value": "https://zenodo.org/records/1234567" },
    { "key": "source_urls", "value": "[\"https://zenodo.org/records/1234567\"]" },
    { "key": "withdrawn", "value": "false" }
  ],
  "groups": [ { "name": "task-43" }, { "name": "task-49" } ],   // must exist in groups.yaml
  "license_id": "cc-by",                                        // must exist in the licence register
  "name": "doi-10-5281-zenodo-1234566",                         // = the slug = the file stem = the URL
  "notes": "<p>Ten-minute statistics from a scanning lidar …</p>",
  "private": false,
  "resources": [
    { "format": "csv", "name": "osterild-lidar-2021.csv",
      "url": "https://zenodo.org/records/1234567/files/osterild-lidar-2021.csv" }
  ],
  "state": "active",                                            // see §4.3
  "tags": [ { "name": "lidar" }, { "name": "osterild" },
            { "name": "remote-sensing" }, { "name": "wind-energy" } ],
  "title": "Lidar measurements from the Østerild campaign, 2021",
  "url": "https://zenodo.org/records/1234567",
  "version": "2.0"
}
```

### 4.2 Extras encoding — the rules

- **Every `extras` value is a string.** CKAN accepts nothing else.
- Lists and objects are JSON **inside** that string, via
  `harvest.models.json_extra`:
  `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
- Booleans are the strings `"true"` / `"false"`.
- An empty value is **omitted entirely**, not written as `""`. On a record
  page, "absent" and "empty" say different things.
- `extras` is sorted by key; so are `tags` and `groups`. Determinism.

The full key list is `harvest.materialize.EXTRA_KEYS`, and every key in it is
documented in `schema/ckan-scheming.json`. A test enforces that those two
agree — **add a key to one, add it to the other.**

### 4.3 Why `state` is `"active"` on a withdrawn record

CKAN's `state` is its own row lifecycle: `deleted` means "hidden and
purgeable", which is exactly what
[[adr-0027-withdrawn-records-are-retained]] forbids. So `state` stays `active`
for every record the catalogue retains, and withdrawal lives in
`extras.lifecycle_state`, `extras.withdrawn` and `extras.withdrawn_at`. The
site renders the withdrawal banner from those; CKAN still ingests the record on
promotion day; the URL and the citation survive. Procedure:
[[handle-a-withdrawn-record]].

### 4.4 Provenance encoding — read this before rendering anything

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

- `extraction_method` is `api` | `pattern` | `llm`.
- `llm` **requires** `model` and `prompt_version` — `FieldProvenance` raises
  otherwise — and should carry `confidence`.
- Absent keys are omitted, so an ordinary field is exactly
  `{"extraction_method": "api"}`.
- `pinned: true` appears only on a pinned Tier-3 extraction.

Every field whose `extraction_method` is `llm` **must render with a visible
machine-inferred badge** ([[adr-0028-provenance-is-displayed]], fixture
`x-05`). The design system reserves violet for exactly this.

### 4.5 The CKAN-compat gate

`uv run python -m harvest validate` — exits non-zero and prints **every**
violation, not the first.

| field | rule |
|---|---|
| `name` | `^[a-z0-9_-]{2,100}$`, unique, matches the filename stem |
| `title` | non-empty |
| `notes` | a string when present |
| `tags[].name` | `^[A-Za-z0-9._-]{2,100}$`, no duplicates. Use `tagify()` |
| `license_id` | present in `harvest.licenses.LICENSE_REGISTER`, never empty |
| `extras[].value` | **string**; keys non-empty and unique |
| `resources[].url` | present |
| `owner_org` | exists in `organizations.yaml` when present |
| `groups[].name` | exists in `groups.yaml` |
| `state` | `active` \| `deleted` \| `draft` |

Licences map through `harvest.licenses.map_license(raw) -> (license_id, mapped)`.
`mapped is False` means the source said something the table did not recognise:
`license_id` becomes `notspecified` **and the run report flags it** (fixtures
`zen-08`, `dc-09`). An *absent* licence also yields `notspecified` but with
`mapped=True` — nothing went wrong, the source simply said nothing.
**Never infer an open licence.**

---

## 5. What the site is allowed to read

Astro is a renderer. It never writes into `records/`, and no framework-specific
field ever enters the record format
([[adr-0032-site-framework-astro]]).

| what | where | for |
|---|---|---|
| records | `records/*.json` via glob | list, record pages, JSON-LD, Pagefind index |
| freshness | `state/last-run.json` → `finished_at` | "last updated" banner; warning past 45 days (`r-08`) |
| backlog | `state/last-run.json` → `pending_extraction` | shown next to the freshness banner |
| unreachable sources | `state/last-run.json` → `unreachable_sources` | honest degradation notice |
| notices | `state/last-run.json` → `notices` | the curator's short monthly read |
| tasks | `groups.yaml` | task chips, task pages, facet labels |
| institutions | `organizations.yaml` | institution facet |
