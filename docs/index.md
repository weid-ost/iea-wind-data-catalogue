---
type: moc
id: index
status: current
date: 2026-08-31
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

---

## Reading order

If you have twenty minutes:

1. [[architecture]] — the whole system on one page, with the binding invariants.
2. [[record-format]] — what a record and an event actually look like.
3. [[local-dev-setup]] then [[run-a-harvest-locally]] — get it running.

If you are about to change something:

4. The relevant ADR in [[#Decision register]] below. **Do not change behaviour
   an ADR fixes without saying, in the commit message, that you are
   relitigating it.**
5. `harvest/CONTRACT.md` — the interface document the code is written against.
   Where this vault and `CONTRACT.md` disagree about the code, `CONTRACT.md`
   and then the code win; fix the vault in the same change.
6. `fixtures/fixtures-catalogue.md` — new behaviour requires a new fixture.

If you want to know *why*:

7. `plans/02-static-plan.md` — the authoritative architecture document, with
   the ADR register (§8) and decisions log (§9) these ADRs expand.
8. `transcript/conversation-record.md` — the conversation that produced it,
   with every requirement prompt verbatim. Each ADR cites its turn.

---

## What lives where

| Path | What it is | Editable by |
|---|---|---|
| `sources.yaml` | the source register — the only configuration that matters | anyone, consciously |
| `organizations.yaml` | CKAN-shaped institutions; `owner_org` must resolve here | curator |
| `groups.yaml` | CKAN-shaped groups = IEA Wind Tasks, with renumbering aliases | curator |
| `schema/ckan-scheming.json` | the written definition of the custom fields; CKAN needs it on promotion day | with `EXTRA_KEYS` |
| `harvest/` | adapters, event log, materialiser, CKAN gate, extraction stub | the harvest tracks |
| `harvest/CONTRACT.md` | **the interface document** — read before writing an adapter | whoever finds it ambiguous |
| `events/` | **the source of truth.** Append-only JSONL, one file per identity | never by hand, except deliberately |
| `records/` | derived CKAN package dicts. Delete them and `make materialize` rebuilds them | never — it is generated |
| `annotations/` | the human-readable record of curatorial intent | curator |
| `cache/` | committed LLM extraction cache, content-hash keyed | generated, committed |
| `state/last-run.json` | the run report **and** the cron heartbeat | generated every run |
| `state/pending-extraction.json` | Tier-3 cache misses waiting for someone with a key | generated |
| `site/` | the Astro renderer and the Pagefind index build | the site track |
| `design/` | DTCG tokens, the derivation script, the design system | with `design/gen.py` re-run |
| `fixtures/` | test and gallery fixtures; the catalogue is the specification | every harvest change |
| `docs/` | this vault | everyone |
| `plans/` | the two plan documents. Historical record + binding architecture | not without a decision |

---

## Decision register

Twenty ADRs, 0020–0039, expanded from `plans/02-static-plan.md` §8. ADRs
0001–0019 belong to the superseded CKAN-first plan (`plans/01-ckan-plan.md`
§6); several are explicitly superseded — see [[adr-0022-hosting-and-automation]].

### The premise and the contract

- [[adr-0020-aggregation-only]] — the catalogue asks nothing of anyone
- [[adr-0021-canonical-record-is-a-ckan-package-dict]] — the promotion contract
- [[adr-0037-events-are-the-source-of-truth]] — `events/` is truth, `records/` derived
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
- [[run-a-harvest-locally]] — including the five-record cap and how to lift it
- [[materialize-and-validate]] — rebuild `records/` and pass the CKAN gate
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
- **Every command in a runbook is copy-pasteable and has been run.** Where a
  command belongs to work that is not yet finished, it is marked
  **`SPEC — not yet implemented`** and the note says which track owns it. That
  marker is a requirement on that track, not an excuse.
- **"OST" is the Ostschweizer Fachhochschule** (Eastern Switzerland University
  of Applied Sciences) — the author's organisation and the repository's initial
  owner. It is never expanded any other way.
