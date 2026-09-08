# IEA Wind Data Catalogue

A static, self-maintaining catalogue of IEA Wind datasets, publications and
software. Nobody who has already published a fully-described dataset to Zenodo
will log into a second system to describe it again — so this catalogue asks
nothing of anyone. It watches the places where IEA Wind people already publish
and reflects what it finds. A scheduled job harvests the sources, records what
it sees as an append-only event log, materialises that log into CKAN-shaped JSON
records, validates them against what CKAN's API would accept, and renders them
as a static site with a build-time search index. GitHub Actions + GitHub Pages,
no servers, no databases, no billing account, ≈ $0/month.

## Status

**Prototype, and now a working one.** Everything is built: the `harvest`
package, the event log, the materialiser, the CKAN-compat gate, the registers,
**all seven adapters** (Zenodo, DataCite, Crossref, GitHub, OSTI, iea-wind.org,
Wind Data Hub), the Tier-3 extraction layer with its committed cache, the
reconciliation layer, the Astro site with its design system and accessibility
gate, and the CI workflows.
**NEXT STEPS** TC to look at docs/harvest-anomalies.md and address the decisions there using `claude --resume bbdb6a80-6832-47fe-9c9e-ad91c2805634`


## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/) — it fetches the pinned CPython, so
you do not need Python 3.12 installed.

```sh
make sync        # install the pinned environment (uv sync --frozen --dev)
make test        # 2080 passed, 476 skipped
make harvest     # harvest every enabled source (MAX_RECORDS=N to override the cap) → events → records → validate → report
make materialize # replay data/annotations/ + data/events/ into data/records/ (derived; delete it freely)
make validate    # the CKAN-compat gate
make site        # build the static site + Pagefind index
make gates       # everything CI enforces: tests, CKAN gate, palette, tokens, a11y
make             # list every command
```

The underlying CLI is `uv run python -m harvest
{run,materialize,validate,annotations,dedupe,linkcheck,extract,report,sources}`.

## Repo layout

| Path | What |
|---|---|
| `sources.yaml` | the source register — the only configuration that matters |
| `organizations.yaml` / `groups.yaml` | CKAN-shaped institutions and IEA Wind Tasks; canonical data, not config |
| `schema/ckan-scheming.json` | the written definition of the custom fields; CKAN needs it on promotion day |
| `harvest/` | adapters, event log, materialiser, CKAN gate. **Read `harvest/CONTRACT.md` first** |
| `data/events/` | **the source of truth.** Append-only JSONL, one file per identity |
| `data/records/` | derived CKAN package dicts. Regenerable — `make materialize` rebuilds them byte-for-byte |
| `data/annotations/` | the human-readable record of curatorial intent. Deliberately empty of samples — the worked templates are in `docs/examples/annotations/`, so a pending annotation here always means a curator is waiting on a harvest |
| `data/cache/` | committed LLM extraction cache, content-hash keyed |
| `data/state/last-run.json` | the run report, and the cron heartbeat — written on every run |
| `site/` | the Astro renderer and Pagefind index build |
| `design/` | DTCG design tokens, the palette derivation script, the design system |
| `data/fixtures/` | test and gallery fixtures; `fixtures-catalogue.md` is the specification |
| `data/README.md` | what the `data/` subtree is and the two rules that govern it |
| `docs/` | the documentation vault — **the authority** for every decision |

## Documentation

Start at **[`docs/index.md`](docs/index.md)** — the vault map.

- [`docs/architecture.md`](docs/architecture.md) — the system end to end, and the binding invariants
- [`docs/record-format.md`](docs/record-format.md) — the record and event schemas
- [`docs/adrs/`](docs/adrs/) — twenty-four ADRs, 0020–0043. **The authority.** Do not relitigate one without saying so
- [`docs/runbooks/`](docs/runbooks/) — thirteen procedures with exact commands

Background:

- [`docs/motivation.md`](docs/motivation.md) — why this exists, what it deliberately is not, and what it costs
- [`docs/decision-history.md`](docs/decision-history.md) — every requirement verbatim, what it settled, and what was proposed and rejected
- [`docs/ckan-promotion-path.md`](docs/ckan-promotion-path.md) — the CKAN/GCP architecture that was planned first, retained solely as the promotion path

Alongside:

- [`harvest/CONTRACT.md`](harvest/CONTRACT.md) — the interface document the code is written against
- [`design/design-system.md`](design/design-system.md) — the visual system and the accessibility gate

## Two things to know before changing anything

1. **`data/events/` is the truth; `data/records/` is derived.** Never edit a record file —
   append an event and re-materialise. See
   [`docs/runbooks/correct-a-record.md`](docs/runbooks/correct-a-record.md).
2. **Source metadata is never edited, only annotated.** The catalogue reports
   what a source says, verbatim; local additions live in a separate namespace.
   See [ADR-0038](docs/adrs/adr-0038-source-metadata-is-never-updated-only-annotated.md).

## Licence

MIT for the code. Harvested metadata belongs to its sources; the catalogue holds
metadata and links only and never mirrors a file.
