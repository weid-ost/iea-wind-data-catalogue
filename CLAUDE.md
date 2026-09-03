# CLAUDE.md — IEA Wind Data Catalogue

A static, self-maintaining catalogue of IEA Wind datasets, publications and software: harvested from where people already publish, asking nothing of anyone. GitHub Actions + GitHub Pages, no servers, no databases, ≈ $0/month.

## Committing

When making commits in git, NEVER attribute Claude (yourself) as a contributor. Reasons: 
1. A contributor is a human who takes responsibility for the code; LLMs (you) cannot do this. 
2. We (the Open-Source community) built the code that was used to train Claude (you), and never got any credit or compensation for that. Not attributing Anthropic/Claude to our outputs built with this tool is consistent with Anthropic's own practice.

## Authoritative documents — read before proposing changes

1. `docs/adrs/` — the twenty ADRs, 0020–0039. **Binding.** Don't relitigate one without saying you're doing so, in the commit message.
2. `docs/architecture.md` — the system end to end; `docs/record-format.md` — the shapes. Start from `docs/index.md`, the vault map.
3. `harvest/CONTRACT.md` — the interface document the code is written against. Where it and the vault disagree about the code, CONTRACT and then the code win.
4. `design/design-system.md` + `design/design-tokens.json` — visual system (DTCG). `design/gen.py` regenerates the palette and re-verifies WCAG contrast; run it after any colour change.
5. `data/fixtures/fixtures-catalogue.md` — the test/gallery fixture inventory. New behaviour ⇒ new fixture.
6. `docs/motivation.md` — why the catalogue exists and what it deliberately is not. `docs/decision-history.md` — every requirement verbatim, what it settled, and what was proposed and rejected. `docs/ckan-promotion-path.md` — the superseded CKAN design, retained solely as the promotion path.

## Non-negotiable invariants

- **Records are CKAN `package` dicts.** `data/records/*.json` must always pass the CKAN-compat validation gate (slugs, tags, licence ids, string extras). CKAN promotion must stay a one-day job.
- **`data/events/` is the source of truth; `data/records/` is derived** and regenerable by replay. Append-only, append-on-change, ordered by observation time.
- **Source metadata is never edited, only annotated** (ADR-0038). `source.*` verbatim from upstream; `local.*` additive (tasks, notes, links, suppression, Tier-3 pins). Scalar collisions: source displaces + notice. Set-valued (`iea_task`): union.
- **Change detection = per-adapter source key** (ADR-0026); fallback is a normalised payload hash.
- **No identifier a model produced is ever accepted.** Every DOI resolves against DataCite/Crossref or the record is dropped and logged.
- **The harvest never fails because the LLM is unavailable.** Tier 1 is deterministic; Tier-3 misses queue to `data/state/pending-extraction.json`.
- **LLM in CI = GitHub Models via `GITHUB_TOKEN` (`permissions: models: read`).** No vendor SDK — OpenAI-compatible HTTP only (ADR-0035). Extraction cache is committed; cache key = hash(content + prompt_version + model_id).
- **Every run commits** (`data/state/last-run.json` heartbeat) — this keeps the cron alive past GitHub's 60-day dormancy rule. Build+deploy live in the same workflow as the harvest.
- **Pinned everything, no auto-updates** (ADR-0034): `.python-version` + `uv.lock` (`uv sync --frozen`); pinned Node + `package-lock.json` (`npm ci`); pinned runner image (`ubuntu-24.04`, never `-latest`). Python direct deps capped at four: `httpx`, `trafilatura`, `pydantic`, `pyyaml`.
- **Astro renders; it doesn't own the data.** Records load via glob; no framework fields in the record format. Astro components for content; vanilla custom elements only for interactivity; `/dev/components` gallery (real records + pathological fixtures) instead of Storybook.
- **Design: colour never fills a surface.** Neutral backgrounds only; semantic colour appears as text, icons, outline badges, focus, and 3px square-cornered left accent bars. Components consume tokens with zero hardcoded values (CI grep enforces). WCAG 2.2 AA is a build gate: pa11y-ci over the gallery and key pages, both themes.
- **Withdrawn records are kept, never deleted.**

## Working conventions

- One adapter per source in `harvest/`; adapters own their source-key semantics and must degrade cleanly (fixture `wdh-07`).
- Robots.txt respected; descriptive User-Agent with contact address; conditional GETs; metadata and links only, never mirrored files.
- Every harvest change ships with its fixture; every component appears in the gallery.
- The human manages human interactions; keep advice to the technical.
