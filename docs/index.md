---
type: moc
id: index
status: current
date: 2026-09-03
related: [motivation, architecture, record-format, decision-history, ckan-promotion-path]
tags: [vault, map]
---

# IEA Wind Data Catalogue — documentation vault

This is an Obsidian-style vault: plain Markdown, double-bracket wikilink
backlinks in both directions, no plugins required. It documents a static, self-maintaining
catalogue of IEA Wind datasets, publications and software, built from GitHub
Actions and GitHub Pages with no servers, no databases and no billing account.

The vault has three jobs, in order of importance to a stranger picking this up:

1. **Stop you relitigating settled decisions.** That is `docs/adrs/`.
2. **Let you actually do things.** That is `docs/runbooks/`.
3. **Explain the shapes.** That is [[architecture]] and [[record-format]].

The ADRs and these pages are **authoritative**. There is no separate plan
document and no conversation log behind them: the reasoning was folded in here,
and [[decision-history]] carries the requirements record — verbatim — that the
decisions answer.

---

## Reading order

If you have twenty minutes:

1. [[motivation]] — why the catalogue exists and what it deliberately is not.
2. [[architecture]] — the whole system on one page, with the binding invariants.
3. [[record-format]] — what a record and an event actually look like.
4. [[local-dev-setup]] then [[run-a-harvest-locally]] — get it running.

If you are about to change something:

5. The relevant ADR in [[#Decision register]] below. **Do not change behaviour
   an ADR fixes without saying, in the commit message, that you are
   relitigating it.**
6. `harvest/CONTRACT.md` — the interface document the code is written against.
   Where this vault and `CONTRACT.md` disagree about the code, `CONTRACT.md`
   and then the code win; fix the vault in the same change.
7. `data/fixtures/fixtures-catalogue.md` — new behaviour requires a new fixture.

If you want to know *why*:

8. [[decision-history]] — every requirement verbatim, in order, with what it
   settled and which ADR owns it. Each ADR cites its turn there. It also records
   what was **proposed and rejected**, including two reconciliation models that
   were accepted and then superseded.
9. [[ckan-promotion-path]] — the architecture that was planned first, retained
   because it is what promotion would actually cost.

---

## What lives where

| Path | What it is | Editable by |
|---|---|---|
| `sources.yaml` | the source register — the only configuration that matters | anyone, consciously |
| `organizations.yaml` | CKAN-shaped institutions; `owner_org` must resolve here | curator |
| `groups.yaml` | CKAN-shaped groups = IEA Wind Tasks, with renumbering aliases | curator |
| `vocabulary.yaml` | **the terms, defined once** — resource kinds and types, access statuses, and everything else a record page names. The harvest validates against it, every tooltip quotes it, and the About page's Definitions section is rendered from it | anyone, with a definition |
| `schema/ckan-scheming.json` | the written definition of the custom fields; CKAN needs it on promotion day | with `EXTRA_KEYS` |
| `harvest/` | adapters, event log, materialiser, CKAN gate, extraction stub | the harvest tracks |
| `harvest/CONTRACT.md` | **the interface document** — read before writing an adapter | whoever finds it ambiguous |
| `data/events/` | **the source of truth.** Append-only JSONL, one file per identity | never by hand, except deliberately |
| `data/records/` | derived CKAN package dicts. Delete them and `make materialize` rebuilds them | never — it is generated |
| `data/annotations/` | the human-readable record of curatorial intent. Holds no samples on purpose, so a non-zero pending count always means something | curator |
| `docs/examples/annotations/` | the three worked annotation templates, one per identity kind — copy into `data/annotations/` and repoint the key | everyone |
| `data/cache/` | committed LLM extraction cache, content-hash keyed | generated, committed |
| `data/state/last-run.json` | the run report **and** the cron heartbeat | generated every run |
| `data/state/pending-extraction.json` | Tier-3 cache misses waiting for someone with a key | generated |
| `site/` | the Astro renderer and the Pagefind index build | the site track |
| `design/` | DTCG tokens, the derivation script, the design system | with `design/gen.py` re-run |
| `data/fixtures/` | test and gallery fixtures; the catalogue is the specification | every harvest change |
| `data/README.md` | what the `data/` subtree is and the two rules that govern it | with the layout |
| `docs/` | this vault — **the authority** for every decision | everyone |

---

## The pages

| Page | What it answers |
|---|---|
| [[motivation]] | why this exists, what it deliberately is not, and what it costs |
| [[architecture]] | the whole system end to end, with the binding invariants |
| [[record-format]] | what a record and an event actually are |
| [[harvest-anomalies]] | what the real upstreams do that the fixtures encode |
| [[decision-history]] | the requirements record: every requirement verbatim, and what it settled |
| [[ckan-promotion-path]] | the superseded CKAN architecture, retained as the promotion path |

---

## Decision register

Twenty-three ADRs, 0020–0042. ADRs 0001–0019 belonged to the superseded CKAN-first
plan; those numbers are not reused, and the ones explicitly superseded are
tabulated in [[ckan-promotion-path]] §11.

### The premise and the contract

- [[adr-0020-aggregation-only]] — the catalogue asks nothing of anyone
- [[adr-0021-canonical-record-is-a-ckan-package-dict]] — the promotion contract
- [[adr-0037-events-are-the-source-of-truth]] — `data/events/` is truth, `data/records/` derived
- [[adr-0038-source-metadata-is-never-updated-only-annotated]] — the two namespaces
- [[adr-0026-change-detection-by-source-key]] — one change token per source
- [[adr-0027-withdrawn-records-are-retained]] — link rot is the enemy

### Infrastructure and scheduling

- [[adr-0022-hosting-and-automation]] — GitHub Actions + Pages, $0, no GCP
- [[adr-0029-scheduling-and-the-heartbeat-commit]] — weekly cron, 60-day dormancy
- [[adr-0034-toolchain-pinning-and-no-auto-updates]] — `uv.lock`, `npm ci`, pinned runner
- [[adr-0033-harvester-language-python]] — Python, and why not Go or Rust

### The LLM boundary

- [[adr-0024-the-llm-boundary]] — Tier 3 only; identifiers always verified
- [[adr-0025-the-extraction-cache-is-committed]] — reproducible rebuilds
- [[adr-0030-llm-access-via-github-models]] — zero accounts
- [[adr-0031-the-harvest-never-fails-on-llm-unavailability]] — the pending queue
- [[adr-0035-no-vendor-sdk]] — OpenAI-compatible HTTP only
- [[adr-0028-provenance-is-displayed]] — machine-inferred fields are marked

### What the words mean

- [[adr-0040-vocabulary-is-defined-once-and-shown-at-two-levels]] — `vocabulary.yaml`, kind › type, IEA Wind's own publication types
- [[adr-0041-a-mapping-improvement-must-reach-the-existing-corpus]] — mapping versions and the Zenodo DOI backfill
- [[adr-0042-the-concept-doi-is-the-record]] — version DOIs merge into the concept, and are kept

### The site

- [[adr-0032-site-framework-astro]] — Astro renders; it does not own the data
- [[adr-0023-search-via-pagefind]] — build-time index, client-side filters
- [[adr-0036-component-architecture-and-the-gallery]] — no Storybook
- [[adr-0039-design-system]] — tokens, accent bars, WCAG 2.2 AA as a gate

---

## Runbooks

Each is a procedure with exact commands. A runbook nobody has executed is a
hypothesis; every one of these ends with a **Last executed** line, and it is
your job to update it.

**Getting going**

- [[local-dev-setup]] — clone to green test suite
- [[run-a-harvest-locally]] — including the record cap and how to lift it
- [[materialize-and-validate]] — rebuild `data/records/` and pass the CKAN gate
- [[run-the-site-locally]] — `site/` build and dev server

**Changing things**

- [[add-a-source-adapter]] — the full checklist, fixtures included
- [[correct-a-record]] — the whole ADR-0038 annotation matrix, worked
- [[handle-a-withdrawn-record]] — retention, never deletion
- [[drain-the-pending-extraction-queue]] — the human-operated LLM

**Gates and operations**

- [[run-the-a11y-gate]] — pa11y-ci over the gallery, both themes
- [[release-checklist]] — every gate plus the three manual a11y passes
- [[re-enable-a-dormant-cron]] — the 60-day rule
- [[no-secrets-to-rotate]] — why there are no credentials, and the annual check
- [[promote-to-ckan]] — the one-day promotion drill

---

## Conventions

- **British English**, plain, no marketing. Say what happened, not what it enables.
- **Frontmatter on every note** (`type`, `id`, `status`, `date`, `related`,
  `tags`) so Dataview queries work if anyone ever wants them.
- **Wikilinks are bidirectional by convention.** Every ADR links to the
  runbooks it governs, and every runbook links back to the ADRs that justify
  it. An inheritor landing on any note can walk to context.
- **Every command in a runbook is copy-pasteable, and the runbook says whether
  it has been run.** The `last_executed` frontmatter field is the answer, and it
  is kept equal to the body's *Last executed* line. Eleven of the thirteen
  runbooks carry a date. **Two say `never`, honestly**, and no blanket claim
  overrides them:
  - [[promote-to-ckan]] §§2–5 — standing up CKAN is a future decision, not a
    pending task, and running it would cost money;
  - [[re-enable-a-dormant-cron]] §2 — `.github/workflows/` exists and its YAML
    is cross-checked, but a workflow cannot be executed locally and has
    deliberately not been pushed. The first real run is the first proof.

  Where a command belonged to work that was not yet finished, it was marked
  **`SPEC — not yet implemented`** and the note said which track owned it. Every
  track has now landed and every such marker has been retired.
- **"OST" is the Ostschweizer Fachhochschule** (Eastern Switzerland University
  of Applied Sciences) — the author's organisation and the repository's initial
  owner. It is never expanded any other way.
