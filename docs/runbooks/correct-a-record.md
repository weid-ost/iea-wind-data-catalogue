---
type: runbook
id: RUN-correct-a-record
status: current
date: 2026-08-31
related: [adr-0038-source-metadata-is-never-updated-only-annotated, adr-0037-events-are-the-source-of-truth, adr-0028-provenance-is-displayed, materialize-and-validate, handle-a-withdrawn-record, record-format]
tags: [runbook, curation, annotations]
last_executed: 2026-08-31
---

# Runbook — correct a record

**Goal:** change what the catalogue shows, without ever editing what a source
said.
**Prerequisites:** [[local-dev-setup]] done. Read
[[adr-0038-source-metadata-is-never-updated-only-annotated]] first.
**Governed by:** ADR-0038, [[adr-0037-events-are-the-source-of-truth]].

---

## 0. The rule, before anything else

**You cannot correct a source value. You can only add to `local.*`.**

If a Zenodo record has a typo'd title, then "a typo'd title" is *what Zenodo
says*, and the fix belongs at Zenodo. Report it there; the record page carries a
"report an issue at the source" link for exactly this. What you can do here is
add a **curator note** so both truths appear on the page.

Three things follow, and they are the whole runbook:

- **Never edit `records/*.json`.** It is generated. Your edit disappears at the
  next `make materialize`.
- **Never edit a line in `events/*.jsonl`.** It is append-only.
- **Append an annotation event, then materialise.** That is the only path.

## 1. The matrix — what you are allowed to do

| I want to… | Mechanism | Namespace | Collision behaviour |
|---|---|---|---|
| attribute a record to another task | `local.iea_task` | set-valued | **union** — a later Zenodo community adding Task 43 never erases your Task 49 |
| say the upstream licence is wrong | `local.curator_notes` | set-valued | union; the wrong upstream value is still displayed verbatim, note beside it |
| type a record the source did not type | `local.resource_kind` | scalar | **the source displaces you** if it ever starts providing it, with a notice |
| state availability the source did not | `local.access_status` | scalar | as above |
| add a related link | `local.links` | set-valued | union |
| add a discovery URL | `local.source_urls` | set-valued | union |
| hide a noise record from listings | `local.suppressed` | scalar | latest local event wins; the record is **retained** |
| hold a corrected Tier-3 extraction | `local.pinned` + `local.pin_source_key` | scalar | the pin holds; a `pin_notice` fires when the page moves |
| mark something gone upstream | a `withdrawn` **event**, not an annotation | — | [[handle-a-withdrawn-record]] |

Anything not in that table is either a source's job or a bug in an adapter's
`map()`.

## 2. Record the intent in `annotations/`

`annotations/` is the human-readable record of curatorial intent — what you did
and why — kept next to the events it produced. One file per identity, named
after the slug:

`annotations/doi-10-5281-zenodo-1234566.yaml`

```yaml
identity_key: "10.5281/zenodo.1234566"
actor: "curator:tom"
annotations:

  - local:
      iea_task: ["task-49"]
    note: "Task 49 attribution from the IDEA workshop participant list"

  - local:
      curator_notes:
        - field: license_id
          note: >-
            OST note: the licence stated at source appears incorrect; the
            archive's LICENCE file says CC-BY-NC-4.0.
    note: "known-wrong upstream licence; reported at source 2026-08-28"
```

Replaying this directory into `annotated` events is **implemented and
idempotent** — the same annotation is appended once however many times you run
it. `python -m harvest materialize` and `python -m harvest run` do the replay
before rebuilding `records/`, so the whole loop is one command:

```sh
uv run python -m harvest annotations --dry-run   # say what would be appended
make materialize                                 # replay, then rebuild records/
```

Three rules the replay enforces, so a typo fails here rather than three steps
downstream:

- a file containing a `source:` key is **refused** — source metadata is never
  edited, only annotated;
- the `local:` block is validated against `LocalNamespace`, so
  `iea_task: task-49` (a string where a list belongs) is an error;
- an `iea_task` that does not resolve through `harvest.config.canonical_group`
  to a group in `groups.yaml` is refused, because the CKAN gate would otherwise
  fail the whole run for one invented task name.

**An annotation for an identity nothing has harvested yet waits.** Applying it
would materialise a record whose title is its own identity key and whose every
other field is empty. It is reported as `annotation_pending` in
`state/last-run.json` → `notices`, and applies itself on the run that first sees
the record. A file that genuinely means to create an identity from nothing says
`allow_new: true`.

Idempotence is keyed on a fingerprint of `identity_key` + `actor` + `note` +
the `local` block — **not** on `observed_at`. So changing the `note` on an
existing entry appends a *new* event rather than editing the old one, which is
what an append-only log means.

The working examples in `annotations/` cover every kind in the matrix above; the
directory's `README.md` is the file-format reference.

## 3. Append the annotation

The working path is `harvest.events.annotate`. Each call appends exactly one
`annotated` event.

```sh
uv run python - <<'PY'
from harvest.events import annotate
from harvest.models import utcnow

KEY = "10.5281/zenodo.1234566"      # the IDENTITY KEY, not the slug

annotate(
    KEY,
    {"iea_task": ["task-49"]},
    actor="curator:tom",
    note="Task 49 attribution from the IDEA workshop participant list",
)
PY
```

Then rebuild and check:

```sh
make materialize
make validate
git diff records/
```

### 3.1 Add a task attribution (set-valued — unions)

```python
annotate(KEY, {"iea_task": ["task-49"]},
         actor="curator:tom",
         note="Task 49 attribution from the IDEA workshop participant list")
```

Result, with `task-43` already present from the Zenodo community:

```json
{ "key": "iea_task", "value": "[\"task-43\",\"task-49\"]" }
```

and `"groups": [{"name": "task-43"}, {"name": "task-49"}]`. **Verified.**

Task names must exist in `groups.yaml` or the CKAN gate fails. Renumbered tasks
(19 → 54, 34 → 59) resolve through `harvest.config.canonical_group`, so use the
canonical name or a declared alias — never a string you invented.

### 3.2 Curator note on a known-wrong upstream value (fixture `x-10`)

The upstream value stays on the page, verbatim. The note is rendered *beside*
it.

```python
annotate(
    KEY,
    {"curator_notes": [{
        "field": "license_id",
        "note": ("OST note: the licence stated at source appears incorrect; "
                 "the archive's LICENCE file says CC-BY-NC-4.0."),
        "added_at": utcnow(),
    }]},
    actor="curator:tom",
    note="known-wrong upstream licence; reported at source 2026-08-28",
)
```

`field` is optional — omit it for a record-level note. `curator_notes` is
set-valued, so successive notes accumulate rather than replacing each other.
**Verified**: lands in `extras.curator_notes` as a JSON string.

### 3.3 A scalar addition, and what displaces it

```python
annotate(KEY, {"resource_kind": "dataset"},
         actor="curator:tom", note="source does not type it")
```

If the source later starts providing `resource_kind`, **the source wins**, your
value stays in the log forever, and a notice appears. **Verified** — a scrape
with `resource_kind: "software"` after the annotation above yields:

```
effective resource_kind: software
local still: {'resource_kind': 'dataset'}
NOTICE: {'type': 'displacement', 'identity_key': 'zenodo|999',
         'field': 'resource_kind', 'displaced_local_value': 'dataset',
         'source_value': 'software', 'implicit': True}
```

The notice reaches `state/last-run.json` → `notices`, which is the short list a
curator reads monthly. **This is why a noisy source key is a nuisance rather
than a disaster**: it can never clobber a human edit, only outrank a human
scalar and say so.

### 3.4 A related link

```python
annotate(KEY, {"links": [{
    "url": "https://github.com/IEA-Task-43/digital-wra-data-standard",
    "label": "Reference implementation"}]},
    actor="curator:tom")
```

Lands in `extras.local_links`. **Verified.** (Note the extra key is
`local_links`, not `links` — it is namespaced so it can never be confused with
anything a source supplied.)

### 3.5 Suppress a noise record

Suppression **retains** the record and its URL; it removes it from listings.

```python
annotate(KEY, {"suppressed": True},
         actor="curator:tom", note="superseded by the concept-DOI record")
```

To reverse it, append the opposite — do not delete the event:

```python
annotate(KEY, {"suppressed": False},
         actor="curator:tom", note="un-suppress: it was not a duplicate")
```

**Verified**: after the second annotation the `suppressed` extra is omitted
entirely, because an empty value is never written as `""`.

Suppression is not deletion and never becomes deletion
([[adr-0027-withdrawn-records-are-retained]]).

### 3.6 Pin a Tier-3 extraction (fixture `x-09`)

The carve-out in ADR-0038 §4.3: for a Tier-3 record, the "original" is our own
inference, so a human correcting it is outranking a model's guess about our own
output rather than overriding an authority.

```python
annotate(
    KEY,
    {"pinned": True, "pin_source_key": "a1b2c3d4e5f60718"},
    actor="curator:tom",
    note="pinned: model classified this as a report; it is a dataset",
)
```

`pin_source_key` is **the content hash the pin was made against** — the same
value as the Tier-3 source key and the extraction cache-key input
([[adr-0025-the-extraction-cache-is-committed]]). Record it, always: it is what
lets the system detect that the page has moved beneath your judgement.

When the page's content hash later changes, **the pin holds and a `pin_notice`
fires**. A human then decides whether the pin is still right. **Verified**:
`pinned` lands as `extras.pinned = "true"`.

The corrected object should also replace the cache entry and be marked pinned.
Edit `cache/<key>.json` in place — it is committed on purpose, so this is a
reviewable diff — and set three fields:

```jsonc
{
  "fields":  { "…": "the corrected extraction" },
  "pinned":  true,
  "pin_source_key": "a1b2c3d4e5f60718",                    // the content hash
  "pin_url": "https://iea-wind.org/task43/t43-publications/" // the page it is for
}
```

`pin_url` is not decoration. A cache entry is keyed on content, so a redesigned
page mints a new key — and a pin found only by content would be reverted by the
first site refresh, silently. `harvest.extract.find_pin` looks a pin up by the
one handle the page keeps, and `harvest.extract.lookup_cache(..., url=…)`
serves it whatever the page says today. **Verified**: after such an edit the
`ieawind` adapter serves the pinned classification and raises one `pin_notice`
in `state/last-run.json` naming both hashes.

See [[drain-the-pending-extraction-queue]] §6.

### 3.7 Raise a notice by hand

Rarely needed, since `resolve()` raises implicit displacement notices itself.

```python
from harvest.events import raise_notice
raise_notice(KEY, "pin_notice",
             {"field": "resource_kind", "reason": "page content hash changed"},
             note="page redesigned 2026-09-14")
```

Only `displacement_notice` and `pin_notice` are legal event types here.

## 4. Verify, then commit

```sh
make materialize
make validate
uv run python -m harvest report | python3 -m json.tool | grep -A5 '"notices"'
git add annotations events records
git commit -m "annotate: Task 49 attribution and licence note on 10.5281/zenodo.1234566"
```

Rehearse anything you are unsure about under a scratch root first:

```sh
mkdir -p /tmp/drill/schema
cp sources.yaml groups.yaml organizations.yaml /tmp/drill/
cp schema/ckan-scheming.json /tmp/drill/schema/
HARVEST_ROOT=/tmp/drill uv run python - <<'PY'
...
PY
```

## 5. Things that look like corrections and are not

| Situation | Actually |
|---|---|
| the title is wrong | report it at the source; add a curator note if it matters |
| two records are the same artifact | a merge, owned by the reconciler — `python -m harvest dedupe` proposes it, `--apply` records it as two annotations. Fuzzy matches are **never** applied automatically; see §6 |
| a source page 404s | `python -m harvest linkcheck` reports it; the record is retained. A dead link is not a withdrawal |
| the DOI does not resolve | resolve-or-drop already dropped it; it is in `dropped_dois` |
| the record should not exist at all | `suppressed`, never deletion |
| the artifact is gone upstream | a `withdrawn` event — [[handle-a-withdrawn-record]] |
| the licence mapped to `notspecified` and should not have | fix `harvest/licenses.py`'s alias table, with a test; that is a code change, not an annotation |

## 6. Two records that are one artifact

Most of this solves itself: four sources describing one DOI derive one identity
key and compose into one record with four `source_url`s (fixture `x-01`). What
needs a decision is a pair whose identity **keys differ** — a Zenodo software
record and its GitHub repository, an OSTI deposit of an already-published
article, a preprint and its published version.

```sh
uv run python -m harvest dedupe            # propose; writes state/merge-proposals.json
uv run python -m harvest dedupe --apply    # record the automatic ones as annotations
make materialize
```

**Automatic** merges need an explicit join key — a shared DOI, a DOI badge
carried as a related identifier, or an `IsPreprintOf` relation. `--apply`
records each as two `annotated` events with `actor: reconcile`:

- the **primary** gains the other's `source_urls` and a `local.links` entry, so
  one record carries every way in;
- the **secondary** gains `local.suppressed: true` — **retained, citable, out of
  the listings.** A merge is a suppression, never a deletion (ADR-0027).

Re-running `--apply` changes nothing; the merge is already in the log. To
reverse one, append the opposite annotation — never delete the events.

**Fuzzy** matches (title + first-author surname + year, no shared DOI, fixture
`dc-08`) are **proposals only**. They appear in `state/merge-proposals.json` and
in the run report's `notices`; `--apply` will not touch them. A wrong automatic
merge hides a real record behind a suppression flag, which is worse than two
records a human can see. Confirm one by writing the merge annotation by hand;
reject one by leaving it alone.

## 7. Link rot

```sh
uv run python -m harvest linkcheck         # writes state/link-check.json
```

Every record's landing page, `source_urls`, curator links and resource URLs are
checked with the project's usual etiquette. Dead links are **reported, never
acted on**: the result goes to `state/link-check.json` and the run report's
`notices`, and no record is edited, withdrawn or deleted. Two reasons:

1. A 404 means the *page* moved, not that the artifact stopped existing.
   Withdrawal is an adapter's finding about the artifact
   ([[handle-a-withdrawn-record]]), never a link checker's inference from an
   HTTP status.
2. Records are byte-stable by contract. Writing HTTP status into a record would
   make one flaky 503 rewrite it, and a weekly run would churn `records/`
   forever for no change in what any source said.

`python -m harvest run --linkcheck` folds the same check into a run and puts the
notices in `state/last-run.json`. It is off by default: an unattended weekly job
should not add a few hundred requests to seven upstreams without being asked.

---

**Last executed:** 2026-08-31 — every snippet in §3 was run under
`$HARVEST_ROOT` against a scratch repository, materialised and validated
(`validate-ckan-compat: OK — 1 record(s)`). The displacement transcript in §3.3
is real output.
