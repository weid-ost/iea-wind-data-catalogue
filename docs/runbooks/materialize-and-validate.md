---
type: runbook
id: RUN-materialize-and-validate
status: current
date: 2026-08-31
related: [adr-0037-events-are-the-source-of-truth, adr-0021-canonical-record-is-a-ckan-package-dict, record-format, correct-a-record, promote-to-ckan]
tags: [runbook, records, gate]
last_executed: 2026-08-31
---

# Runbook — materialise and validate

**Goal:** rebuild `records/` from `events/` and prove it would be accepted by
CKAN.
**Prerequisites:** [[local-dev-setup]] done.
**Governed by:** [[adr-0037-events-are-the-source-of-truth]],
[[adr-0021-canonical-record-is-a-ckan-package-dict]].

---

## 1. The acceptance test for ADR-0037

`records/` is derived. Prove it, regularly:

```sh
rm -f records/*.json
make materialize
git status --short records/
```

**Expect no changes.** Materialisation is byte-stable — sorted keys, fixed
separators, two-space indent, one trailing newline — so a rebuild from the same
events produces identical bytes. If `git status` shows modifications, either an
event changed or byte-stability has been broken, and the second is a bug worth
stopping for.

Output looks like:

```
materialize: 1 record(s) (1 written, 0 unchanged, 0 pruned)
```

`written` counts files whose bytes changed; `unchanged` counts files that were
already correct and were therefore not rewritten.

## 2. Materialise

```sh
make materialize                                   # = uv run python -m harvest materialize
uv run python -m harvest materialize --no-prune    # keep record files with no backing events
```

**On pruning.** By default `materialize_all` removes `records/*.json` files that
have no backing events. That is the **only sanctioned deletion in the system**,
and it can only ever fire for an identity whose events were removed by hand.
Withdrawn identities keep their events and therefore keep their records
([[adr-0027-withdrawn-records-are-retained]]). Every prune is logged as a
warning. If you see one you did not expect, stop and work out which event log
went missing before you commit.

Materialisation also runs the gate at the end, so a bad record fails here as
well as in `validate`.

## 3. Validate

```sh
make validate            # = uv run python -m harvest validate
```

Success:

```
validate-ckan-compat: OK — 12 record(s)
```

Failure — **every** violation is printed, not just the first, and the exit code
is 1:

```
validate-ckan-compat: FAIL — 3 violation(s) across 12 record(s)
```

## 4. Fixing a violation

The gate checks exactly what CKAN's API would refuse. Full table in
[[record-format]] §4.5. The common ones, and what they actually mean:

| Violation | Cause | Fix |
|---|---|---|
| `name … is not a legal CKAN slug` | the slug was hand-written, or `slug_for_identity` was bypassed | derive it from the identity key; never from the title |
| `duplicate tag` / `is not a legal CKAN tag` | keywords passed through raw | run every keyword through `harvest.ckan_compat.tagify` |
| `license_id … is not in the licence register` | a raw SPDX or Zenodo id reached the record | map through `harvest.licenses.map_license`; unmappable ⇒ `notspecified`, flagged, **never guessed open** |
| `value must be a string` | a list or dict was put in `extras` | encode with `harvest.models.json_extra` |
| `'task-99' is not in groups.yaml` | a task attribution names a group that does not exist | add the group to `groups.yaml`, or add the string as an `aliases:` entry on the right group |
| `'foo' is not in organizations.yaml` | an `owner_org` that has no register entry | add the institution, or use `unattributed` |
| `slug collision: identities … both render to …` | two identity keys render to one slug | disambiguate the `source_id`; do not loosen the slugifier |

**The fix always goes upstream of `records/`.** Never edit a file in `records/`
— it is generated, and your edit is gone at the next materialisation. Fix the
adapter's `map()`, or append an annotation event
([[correct-a-record]]), then materialise again.

## 5. When you have changed the record shape

Adding a custom field is a three-file change, and a test enforces two of them:

1. add the key to `harvest.materialize.EXTRA_KEYS`,
2. document it in `schema/ckan-scheming.json`,
3. add or update a fixture that exercises it
   (`fixtures/fixtures-catalogue.md` is the inventory).

Then:

```sh
make test
make materialize
make validate
```

## 6. Working safely

Use a scratch root rather than the real `events/` while experimenting:

```sh
mkdir -p /tmp/drill/schema
cp sources.yaml groups.yaml organizations.yaml /tmp/drill/
cp schema/ckan-scheming.json /tmp/drill/schema/
HARVEST_ROOT=/tmp/drill uv run python -m harvest materialize
HARVEST_ROOT=/tmp/drill uv run python -m harvest validate
```

`$HARVEST_ROOT` overrides the repository root everywhere. It is how the test
suite keeps out of the real event log, and it should be how you do too.

## 7. Before promotion day

The gate is the promotion contract. If it is green, `records/*.json` can be
POSTed to CKAN `package_create` unmodified. Drill: [[promote-to-ckan]].

---

**Last executed:** 2026-08-31 — `uv run python -m harvest materialize` and
`uv run python -m harvest validate` both exit 0 on the foundation checkout
(`0 record(s)`), and the annotate → materialise → validate cycle in
[[correct-a-record]] produced a valid record under `$HARVEST_ROOT`.
