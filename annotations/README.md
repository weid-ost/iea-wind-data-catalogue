# `annotations/`

The human-readable record of **curatorial intent**: what a curator added to a
record, and why. One file per identity, named after the record's slug
(`annotations/doi-10-5072-zenodo-1234566.yaml`), so it sits beside the events it
produces and the record it affects.

`python -m harvest materialize` — and `python -m harvest run` — replay this
directory into `annotated` events **idempotently** before rebuilding
`records/`. Running it a hundred times appends each annotation once. You can
also run the replay alone:

```sh
uv run python -m harvest annotations --dry-run   # say what would happen
uv run python -m harvest annotations             # append the events
make materialize                                 # rebuild records/
```

## The one rule

**You may only add to `local.*`.** A file containing a `source:` key is refused
outright. Source metadata is never edited, only annotated
(ADR-0038); if the upstream title has a typo, that is what the source says, and
the fix belongs at the source. Attach a curator note instead — the wrong value
stays on the page, verbatim, with your note beside it.

The full matrix of what you are allowed to do, with runnable examples, is
`docs/runbooks/correct-a-record.md`.

## File format

```yaml
identity_key: "10.5072/zenodo.1234566"   # the IDENTITY KEY, not the slug
actor: "curator:tom"                     # default actor for every entry below
annotations:
  - local:
      iea_task: ["task-49"]
    note: "why you did this"
    observed_at: "2026-08-28T09:00:00Z"  # optional; omitted means 'now'
    actor: "curator:someone-else"        # optional per-entry override
```

| Key | Meaning |
|---|---|
| `identity_key` | required; the key, not the slug — `10.5281/zenodo.123`, `github|org/repo`, `hash|ab12…` |
| `actor` | who is speaking; `curator:<name>` by convention, `reconcile` for the deduplicator |
| `annotations[].local` | the `local.*` fields this entry sets; validated against `LocalNamespace` |
| `annotations[].note` | why. Part of the event's identity, so editing it appends a *new* event |
| `annotations[].observed_at` | optional ISO 8601 UTC; deliberately excluded from the idempotence fingerprint |
| `allow_new` | file- or entry-level; permit this annotation to create an identity nothing has harvested. Off by default |

## Waiting annotations are normal

An annotation for an identity with no events yet is **pending**, not applied:
applying it would materialise a record with no source. It applies itself on the
run that first harvests that identity, and until then it appears in
`state/last-run.json` → `notices` as `annotation_pending`.

That is a *signal*, so this directory deliberately holds no permanent residents:
a pending count above zero means a curator is waiting on a harvest, not that the
samples are still sitting here. **The worked examples live in
[`docs/examples/annotations/`](../docs/examples/annotations/)** — three
illustrative templates, one per identity kind, none of them replayed. Copy one
into this directory and point its `identity_key` at a record that exists.

## Collisions, in one line each

* `iea_task`, `curator_notes`, `links`, `source_urls` — **set-valued**: they
  union, so a Zenodo community adding Task 43 never erases a hand-added Task 49.
* `resource_kind`, `access_status`, `suppressed`, `pinned` — **scalar**: the
  latest local event wins, and if a source ever starts providing the field, the
  source displaces you and a `displacement` notice appears in the run report.
* Deletion is never an option. Suppression retains the record and its URL.
