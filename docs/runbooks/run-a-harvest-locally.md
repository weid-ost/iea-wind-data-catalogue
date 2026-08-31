---
type: runbook
id: RUN-run-a-harvest-locally
status: current
date: 2026-08-31
related: [adr-0026-change-detection-by-source-key, adr-0031-the-harvest-never-fails-on-llm-unavailability, adr-0029-scheduling-and-the-heartbeat-commit, materialize-and-validate, add-a-source-adapter]
tags: [runbook, harvest]
last_executed: 2026-08-31
---

# Runbook — run a harvest locally

**Goal:** run the pipeline end to end against the real sources, understand what
it did, and know how to lift the prototype cap when you mean to.
**Prerequisites:** [[local-dev-setup]] done.
**Governed by:** [[adr-0026-change-detection-by-source-key]],
[[adr-0031-the-harvest-never-fails-on-llm-unavailability]].

---

## 1. The one-liner

```sh
make harvest
```

which is exactly:

```sh
uv run python -m harvest run --limit 5
```

That performs the whole pipeline in one pass: harvest every enabled source →
change detection → append events on change only → replay into `records/` →
validate → write `state/last-run.json`.

**Exit codes.** `0` means the run completed and the CKAN gate passed. `1` means
the gate failed, and every violation is printed to stderr. An unreachable source
does **not** fail the run — it is reported. That is deliberate
([[adr-0031-the-harvest-never-fails-on-llm-unavailability]]).

## 2. The five-record cap

`harvest.DEFAULT_LIMIT == 5`, and `sources.yaml` sets `max_records: 5` for all
seven sources. `run_adapter` takes `min(cli_limit, source.max_records)`, and
`_iter_limited` truncates and logs a warning if an adapter yields more anyway —
so forgetting the cap is a warning, not three thousand records.

**This is a deliberate prototype cap, not a performance choice.** It stops a
stray run from hammering an upstream API, blowing a rate limit, or committing a
catalogue nobody has looked at.

### Lifting it — consciously, and per run

```sh
uv run python -m harvest run --limit 50      # one run, one source's worth of care
make harvest LIMIT=50                        # identical
```

The `LIMIT` variable is declared at the top of the `Makefile` for exactly this.

**Do not raise `max_records` in `sources.yaml` as part of an adapter change.**
That changes the cap for everybody, permanently, in a file that is canonical
data. Raising the real cap is a separate, deliberate commit with a reason in its
message — and when you do it, raise one source at a time and watch the run
report.

Remember that `--limit` and `max_records` interact by `min()`: with
`max_records: 5` still in `sources.yaml`, `--limit 50` will still yield five.
To genuinely harvest more, both must move.

## 3. Narrowing a run

```sh
uv run python -m harvest run --source zenodo                  # one source
uv run python -m harvest run --source zenodo --source github  # repeatable
uv run python -m harvest run --dry-run                        # harvest and report, append NO events
uv run python -m harvest run --no-materialize                 # skip the replay into records/
uv run python -m harvest run -v                               # debug logging
```

`--dry-run` is the safe first move against a source you have just changed: it
exercises `harvest()` and `map()`, counts what *would* change, and writes
nothing to `events/`. It still writes `state/last-run.json`, because that file is
written on every run without exception.

## 4. Read the report

```sh
uv run python -m harvest report
```

Prints `state/last-run.json`. What to look at, in order:

| Field | Means |
|---|---|
| `ok` | did the CKAN gate pass |
| `sources.<name>.implemented` | `false` = the adapter is still a stub. **All seven are now built, so this should never be `false`** — if it is, an adapter module has regressed to carrying its `_TODO` marker |
| `sources.<name>.reachable` | `false` = network, auth wall, robots, or an exception; see `errors` |
| `sources.<name>.seen` / `changed` / `skipped_unchanged` | change detection working as designed |
| `unreachable_sources` | rendered on the site as an honest degradation notice |
| `notices` | displacement and pin notices — **this is the monthly curator read** |
| `dropped_dois` | DOIs that failed resolve-or-drop. Never silent |
| `unmapped_licenses` | the source said something the licence table did not recognise |
| `cache.hit_rate` | Tier-3 cache effectiveness |
| `pending_extraction` | the Tier-3 backlog — see [[drain-the-pending-extraction-queue]] |
| `validation_violations` | why `ok` is false |

All seven adapters are built, so a healthy run reports `"implemented": true`
for all seven. `wdh` is the one source expected to report `reachable: false`
routinely: its listing endpoint is behind an authentication wall and the
adapter disables itself rather than guessing (fixture `wdh-07`). That is
correct degradation, not a failure — `ok` stays `true`.

## 5. Prove change detection works

Run twice in a row. The second run must report `changed: 0` for every source and
leave `events/` byte-identical:

```sh
make harvest
git status --short events/     # expect: nothing
make harvest
git status --short events/     # expect: still nothing new
git status --short             # expect: only state/last-run.json
```

**Only `state/last-run.json` changes on a no-op run.** That single-file diff is
the cron heartbeat ([[adr-0029-scheduling-and-the-heartbeat-commit]]). If
`records/` also churns on a no-op run, materialisation has stopped being
byte-stable and that is a bug.

## 6. Etiquette — non-negotiable while harvesting

`harvest.http.HarvestClient` implements all of this for you; use it rather than
raw `httpx`:

- descriptive **User-Agent with a contact address**:
  `iea-wind-data-catalogue/0.1 (+https://github.com/thclark/iea-wind-data-catalogue; tom@octue.com)`
- **`robots.txt` honoured per host**
- **throttled** to 5 requests/second
- **conditional GETs** — `If-None-Match` / `If-Modified-Since` from
  `client.fetch_state`
- **never raises** on a transport error; returns a `FetchResult` with `error` set

And the rule that overrides convenience: **metadata and links only. The
catalogue never mirrors a file.** The Wind Data Hub in particular is a *federal*
system — do not hammer it.

## 7. When something goes wrong

| Symptom | Cause | Do |
|---|---|---|
| `not implemented: … is a stub` | that adapter has lost its implementation | a regression: every adapter is built. Check the module still lacks its `_TODO` marker |
| `reachable: false` with an HTTP status | upstream down, rate limited, or robots-blocked | re-run later; the harvest is idempotent |
| `reachable: false` with `SourceUnreachable: listing endpoint requires a token` | the WDH auth wall (fixture `wdh-07`) | correct degradation; nothing to fix |
| `map failed for <system>:<id>` | one record's payload broke `map()` | only that record is lost; capture it as a fixture and fix `map()` |
| `slug collision: …` | two identities render to one slug | the `source_id` needs disambiguating, not the slugifier loosening |
| `validate-ckan-compat: FAIL` | a record would be refused by CKAN | [[materialize-and-validate]] §4 |

Nothing in this table should stop the run. If an exception escapes
`run_adapter`, that is a bug in `run_adapter`, not in your adapter.

## 8. Committing a harvest

```sh
git add events records state cache
git commit -m "harvest: <what changed>"
```

`events/` and `state/last-run.json` are always part of the commit. `records/` is
derived but **is** committed, because it is what the site globs and what CKAN
would receive on promotion day.

---

**Last executed:** 2026-08-31 — `uv run python -m harvest run --limit 5
--dry-run` completed with exit 0, all seven sources reporting
`implemented: false`, `state/last-run.json` written.
