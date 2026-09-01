---
type: runbook
id: RUN-local-dev-setup
status: current
date: 2026-08-31
related: [adr-0033-harvester-language-python, adr-0034-toolchain-pinning-and-no-auto-updates, run-a-harvest-locally, run-the-site-locally]
tags: [runbook, setup]
last_executed: 2026-08-31
---

# Runbook — local development setup

**Goal:** a clean clone to a green test suite in under five minutes.
**Prerequisites:** `git`, `uv`, and (for the site only) Node.
**Governed by:** [[adr-0033-harvester-language-python]],
[[adr-0034-toolchain-pinning-and-no-auto-updates]].

---

## 1. Install `uv`

`uv` fetches a standalone CPython build, so you do **not** need Python 3.12.8
installed already. That is the whole point of pinning the interpreter.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh    # macOS / Linux
```

Verify:

```sh
uv --version
```

## 2. Clone and install the pinned environment

```sh
git clone https://github.com/thclark/iea-wind-data-catalogue.git
cd iea-wind-data-catalogue
make sync
```

`make sync` runs `uv sync --frozen --dev`. **`--frozen` is deliberate**: a stale
`uv.lock` fails loudly instead of being silently rewritten. If it fails, the
lockfile and `pyproject.toml` genuinely disagree and that is a change someone
must make on purpose — do not "fix" it by dropping `--frozen`.

This creates `.venv/` with CPython 3.12.8 and exactly five packages' worth of
direct dependencies: `httpx`, `trafilatura`, `pydantic`, `pyyaml`, plus `pytest`
for development.

## 3. Verify

```sh
make test
```

Expect a green run — `2080 passed, 476 skipped` at the time of writing. (The
skips are the fixture-kind parametrisations stepping over fixtures of the other
kinds; nothing is being avoided.)

Then confirm the CLI is wired up:

```sh
uv run python -m harvest --version
uv run python -m harvest sources
```

`sources` prints one line per configured source. On a foundation-only checkout:

```
crossref   tier 1  enabled  max 5   harvest.adapters.crossref.CrossrefAdapter
datacite   tier 1  enabled  max 5   harvest.adapters.datacite.DataCiteAdapter
github     tier 1  enabled  max 5   harvest.adapters.github.GitHubAdapter
ieawind    tier 3  enabled  max 5   harvest.adapters.ieawind.IeaWindAdapter
osti       tier 1  enabled  max 5   harvest.adapters.osti.OstiAdapter
wdh        tier 2  enabled  max 5   harvest.adapters.wdh.WindDataHubAdapter
zenodo     tier 1  enabled  max 5   harvest.adapters.zenodo.ZenodoAdapter
```

Seven sources, `max 5` on every one — that is the prototype cap
([[run-a-harvest-locally]] §2).

Finally, confirm the gate runs:

```sh
uv run python -m harvest validate
# validate-ckan-compat: OK — 30 record(s)
```

Thirty on a fresh clone, because `records/` is committed: it is *derived* from
`events/` — delete it and `make materialize` rebuilds it byte-for-byte — but the
2026-09-01 harvest is in the tree, so a clone has a catalogue in it and the site
builds without harvesting anything. On a foundation-only checkout, before any
adapter had run, the same command printed `OK — 0 record(s)`, and that was
correct too.

## 4. Everything you can run

```sh
make            # the help target — lists every command with its description
```

| Command | Does |
|---|---|
| `make sync` | install the pinned environment (`uv sync --frozen --dev`) |
| `make harvest` | harvest every enabled source (`LIMIT=5`) and materialise |
| `make materialize` | replay `events/` into `records/` |
| `make validate` | the CKAN-compat gate alone |
| `make test` | `uv run pytest` |
| `make extract` | drain `state/pending-extraction.json` |
| `make build-tokens` | regenerate the palette and re-verify WCAG contrast |
| `make site` | `cd site && npm ci && npm run build` |
| `make gates` | everything CI enforces: tests, CKAN compat, tokens, a11y |
| `make clean` | remove derived artifacts — **never touches `events/`** |

The underlying CLI, if you prefer it:

```sh
uv run python -m harvest run [--source X] [--limit N] [--dry-run] [--no-materialize]
uv run python -m harvest materialize [--no-prune]
uv run python -m harvest validate
uv run python -m harvest extract [--limit N]
uv run python -m harvest report
uv run python -m harvest sources
```

Global flags: `--root PATH` (repository root), `-v` / `--verbose` (debug logging).

## 5. Node, for the site only

Skip this unless you are working on `site/`. See [[run-the-site-locally]] for the
pinned version and the commands.

## 6. Useful environment variables

| Variable | Effect |
|---|---|
| `HARVEST_ROOT` | overrides the repository root everywhere — how the tests keep out of the real `events/` |
| `HARVEST_LLM_ENDPOINT` | Tier-3 inference endpoint (default: GitHub Models) |
| `HARVEST_LLM_TOKEN` | your personal key, for a local backfill or queue drain |
| `HARVEST_LLM_MODEL` | model id, e.g. `openai/gpt-4o-mini` |

`HARVEST_ROOT` is the safest way to experiment. Point it at a scratch directory
containing copies of `sources.yaml`, `groups.yaml`, `organizations.yaml` and
`schema/` and you can append events, materialise and validate without touching
the real repository:

```sh
mkdir -p /tmp/drill/schema
cp sources.yaml groups.yaml organizations.yaml /tmp/drill/
cp schema/ckan-scheming.json /tmp/drill/schema/
HARVEST_ROOT=/tmp/drill uv run python -m harvest materialize
```

## 7. Before you commit

```sh
make test
make validate
```

And read [[release-checklist]] if you are about to ship rather than iterate.

---

**Last executed:** 2026-08-31 — clean install, `make test` green,
`harvest sources` and `harvest validate` as shown above.
