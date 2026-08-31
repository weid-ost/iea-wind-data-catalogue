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

The first coherent harvest ran on 2026-09-01 and is committed: **30 records**
from six sources, five each, under the deliberate five-record cap. The seventh,
Wind Data Hub, disabled itself behind its authentication wall and said so — the
site shows that as a degradation notice rather than pretending. Seven
iea-wind.org pages that needed a model to classify are sitting in
`state/pending-extraction.json`, because the model was unavailable and the rule
is that the harvest does not fail when that happens.

**There is a deliberate five-record cap per source.** `harvest.DEFAULT_LIMIT`
is 5 and `sources.yaml` sets `max_records: 5` everywhere, so a stray run cannot
hammer an upstream API or commit a catalogue nobody has looked at. Raise it
consciously, per run: `make harvest LIMIT=50`.

## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/) — it fetches the pinned CPython, so
you do not need Python 3.12 installed.

```sh
make sync        # install the pinned environment (uv sync --frozen --dev)
make test        # 1447 passed, 101 skipped
make harvest     # harvest every enabled source (LIMIT=5) → events → records → validate → report
make materialize # replay annotations/ + events/ into records/ (derived; delete it freely)
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
| `events/` | **the source of truth.** Append-only JSONL, one file per identity |
| `records/` | derived CKAN package dicts. Regenerable — `make materialize` rebuilds them byte-for-byte |
| `annotations/` | the human-readable record of curatorial intent |
| `cache/` | committed LLM extraction cache, content-hash keyed |
| `state/last-run.json` | the run report, and the cron heartbeat — written on every run |
| `site/` | the Astro renderer and Pagefind index build |
| `design/` | DTCG design tokens, the palette derivation script, the design system |
| `fixtures/` | test and gallery fixtures; `fixtures-catalogue.md` is the specification |
| `docs/` | the documentation vault — ADRs, runbooks, architecture |
| `plans/` | the two plan documents |

## Documentation

Start at **[`docs/index.md`](docs/index.md)** — the vault map.

- [`docs/architecture.md`](docs/architecture.md) — the system end to end, and the binding invariants
- [`docs/record-format.md`](docs/record-format.md) — the record and event schemas
- [`docs/adrs/`](docs/adrs/) — twenty ADRs, 0020–0039. Do not relitigate one without saying so
- [`docs/runbooks/`](docs/runbooks/) — thirteen procedures with exact commands

Underneath the vault:

- [`plans/02-static-plan.md`](plans/02-static-plan.md) — the authoritative architecture, with the ADR register (§8) and decisions log (§9)
- [`plans/01-ckan-plan.md`](plans/01-ckan-plan.md) — the original CKAN/GCP plan, retained as the documented *promotion path*, not the current design
- [`harvest/CONTRACT.md`](harvest/CONTRACT.md) — the interface document the code is written against
- [`design/design-system.md`](design/design-system.md) — the visual system and the accessibility gate
- [`transcript/conversation-record.md`](transcript/conversation-record.md) — why everything is the way it is

## Two things to know before changing anything

1. **`events/` is the truth; `records/` is derived.** Never edit a record file —
   append an event and re-materialise. See
   [`docs/runbooks/correct-a-record.md`](docs/runbooks/correct-a-record.md).
2. **Source metadata is never edited, only annotated.** The catalogue reports
   what a source says, verbatim; local additions live in a separate namespace.
   See [ADR-0038](docs/adrs/adr-0038-source-metadata-is-never-updated-only-annotated.md).

## Licence

MIT for the code. Harvested metadata belongs to its sources; the catalogue holds
metadata and links only and never mirrors a file.
