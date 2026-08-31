---
type: runbook
id: RUN-handle-a-withdrawn-record
status: current
date: 2026-08-31
related: [adr-0027-withdrawn-records-are-retained, adr-0037-events-are-the-source-of-truth, correct-a-record, record-format]
tags: [runbook, lifecycle, link-rot]
last_executed: 2026-08-31
---

# Runbook — handle a withdrawn record

**Goal:** record that an artifact has vanished upstream, while keeping the
record, the page and the URL.
**Governed by:** [[adr-0027-withdrawn-records-are-retained]].

---

## 0. The rule

**Withdrawn records are kept, never deleted.** Link rot is the failure mode a
catalogue exists to fight; silently dropping records makes it worse. Anyone who
cited `/record/<slug>/` must still land somewhere that explains what happened.

## 1. Recognise the case

| Signal | Fixture | This runbook applies? |
|---|---|---|
| DOI resolves to a Zenodo tombstone page | `zen-12` | **yes** |
| upstream API returns 404/410 for a record it previously returned | — | **yes** |
| GitHub repository deleted | — | **yes** |
| GitHub repository *archived* | `gh-05` | no — mark archived, keep listing it |
| GitHub repository renamed or transferred | `gh-07` | no — follow the redirect; the identity key must survive |
| a task page 404s | `iea-12` | no — existing records are retained, the *source* is marked unreachable |
| the record is noise and should not be listed | — | no — that is `suppressed`, see [[correct-a-record]] §3.5 |
| the whole source is behind an auth wall | `wdh-07` | no — the adapter disables itself; existing records untouched |

Only the first three are withdrawal. **A source being unreachable is never
withdrawal** — an adapter that cannot reach its API must not conclude that every
record it used to see has vanished. If you are writing adapter code, withdrawal
must come from a positive upstream signal (a tombstone, a 410, an explicit
withdrawn flag), never from absence in a listing you could not fetch.

## 2. Append the withdrawal event

```sh
uv run python - <<'PY'
from harvest.events import withdraw

withdraw(
    "10.5281/zenodo.1234566",             # the IDENTITY KEY, not the slug
    source_system="zenodo",
    note="DOI resolves to a Zenodo tombstone page; checked 2026-08-31",
)
PY
```

Then rebuild:

```sh
make materialize
make validate
```

**Verified** — the record is materialised, not deleted, and carries:

```json
{ "key": "lifecycle_state", "value": "withdrawn" },
{ "key": "withdrawn",       "value": "true" },
{ "key": "withdrawn_at",    "value": "2026-08-31T20:45:06Z" }
```

while `"state": "active"` — because CKAN's `state: deleted` means "hidden and
purgeable", which is exactly what ADR-0027 forbids ([[record-format]] §4.3).

## 3. What the site must do

- Render the **withdrawal banner** `role="status"` at the top of `<main>`
  (fixture `r-04`).
- **Keep the page and keep the URL.** No redirect, no 404, no removal from the
  sitemap.
- Never imply the artifact is downloadable.
- The record may legitimately still appear in search; it must be visibly marked
  when it does.

## 4. Reversal

If the artifact reappears, do **not** delete the withdrawal event. A later
`scraped` event whose `source.withdrawn` is `false` clears the flag —
`harvest.events.resolve` implements exactly that, so an ordinary re-harvest
undoes it. If you need to force it by hand, append a scrape rather than editing
the log.

## 5. What must never happen

```sh
rm records/doi-10-5281-zenodo-1234566.json     # NO
rm events/doi-10-5281-zenodo-1234566.jsonl     # NO — this is the source of truth
```

Deleting the record file achieves nothing: `make materialize` recreates it from
the events. Deleting the **event log** destroys the record permanently, and it
is the one action in this system that is genuinely irreversible. It also turns
the orphaned record file into the *only* case where `materialize_all(prune=True)`
deletes anything — you will see `pruning orphaned record with no events` in the
log, and by then it is too late.

`make clean` is safe: it removes `records/*.json`, `.pytest_cache`, `site/dist`
and `site/.astro`, and explicitly never touches `events/`.

## 6. Commit

```sh
git add events records
git commit -m "withdraw: 10.5281/zenodo.1234566 — tombstoned at Zenodo"
```

---

**Last executed:** 2026-08-31 — `harvest.events.withdraw` run under
`$HARVEST_ROOT` against a scratch repository; `make materialize` retained the
record with `lifecycle_state: withdrawn`, `withdrawn: true`, `withdrawn_at` set
and `state: active`; `validate-ckan-compat: OK`.
