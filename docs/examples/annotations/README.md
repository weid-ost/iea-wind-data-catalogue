---
type: reference
id: examples-annotations
status: current
date: 2026-09-01
related: [correct-a-record, adr-0038-source-metadata-is-never-updated-only-annotated]
tags: [annotations, examples]
---

# Worked annotation examples

Three templates, one per identity kind, covering every row of the matrix in
[[correct-a-record]] §3. Between them they exercise the whole of ADR-0038's
`local.*` namespace: a set-valued task attribution, a curator note on a
known-wrong upstream licence, a related link, the two scalar additions
(`resource_kind`, `access_status`), a pinned Tier-3 extraction, and suppression.

| File | Identity kind | Shows |
|---|---|---|
| `doi-10-5072-zenodo-1234566.yaml` | DOI | task attribution (union), curator note, related link |
| `github-iea-task-43-digital-wra-data-standard.yaml` | `github\|org/repo` | scalar additions and a Tier-3 pin |
| `hash-1f0e3dad99908345.yaml` | `hash\|…` (fragile) | suppression, which is never deletion |

## Why they are here and not in `annotations/`

They name identities nothing has harvested, so `annotations/` would report each
of them as `annotation_pending` on every run, forever — eight notices in
`state/last-run.json` that carried no information and drowned out the signal the
field exists to give (compliance-11). `annotations/` is now empty of samples, so
a non-zero pending count always means a real curator is waiting on a real
harvest.

## Using one

```sh
cp docs/examples/annotations/doi-10-5072-zenodo-1234566.yaml \
   annotations/<slug-of-a-real-record>.yaml
```

Then **change `identity_key`** to a key that exists — take it from
`records/<slug>.json` → `extras.identity_key` — and change the notes to say what
you actually did and why. `uv run python -m harvest annotations --dry-run` says
what would happen; `make materialize` applies it. The replay is idempotent:
running it a hundred times appends each annotation once.

An annotation for an identity that has never been harvested is **pending**, not
applied, and it stays pending until a harvest sees that identity. A file that
genuinely means to create an identity from nothing must say `allow_new: true`.
