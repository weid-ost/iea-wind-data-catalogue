# IEA Wind Data Catalogue — v2: Static Architecture

**Status:** proposed, supersedes the CKAN-first plan for the prototype
**Date:** 2026-08-31
**Companion:** `IEA-Wind-Catalogue-Plan.md` (CKAN plan — retained as the promotion path, §4)

---

## 1. The premise

You made the argument that kills CKAN: **nobody who has already published a fully-described dataset to Zenodo will log into a second system to describe it again.** Registration-based catalogues in loosely-governed federations don't fail on technology, they fail because the registration step never happens. CKAN's entire differentiating feature here — organisations, roles, dataset ownership, claim workflows — is machinery for a behaviour that won't occur.

So invert it. **The catalogue asks nothing of anyone.** It watches the places where IEA Wind people already publish, and reflects what it finds. Corrections are possible but optional, and are made by whoever is running the catalogue rather than by the artifact's author.

That is a fundamentally different product, and it wants fundamentally less infrastructure.

---

## 2. Your three questions, answered

### 2.1 Can a static site be searchable and filterable "just like CKAN"?

**Yes — and at this scale, noticeably better than CKAN.**

At a few thousand records, client-side search beats a Solr round-trip. CKAN's faceted search does a full page reload per facet click; a static site filters in the browser in single-digit milliseconds with no network at all.

**Recommendation: [Pagefind](https://pagefind.app).** It builds a chunked search index at build time and the browser downloads only the fragments a query needs, so index size stops mattering. It has first-class **filters**, which map directly onto the facets you'd want: task, resource kind, year, licence, source system, institution. No runtime, no server, no index to keep in sync — the index is a build artifact regenerated from the records every time.

Alternatives if you want a fully bespoke UI: MiniSearch or Orama (both fine at this scale, both load one index blob up front). DuckDB-WASM over Parquet is the power-user option and is genuinely excellent for complex filtering, but it's more machinery than a handover artifact should carry.

Two things you get for free that CKAN would have made you work for:

- **Stable, citable record URLs** (`/record/<identity-key>/`) derived deterministically from the DOI, so they survive every rebuild.
- **schema.org `Dataset` JSON-LD on every record page**, which means **Google Dataset Search indexes the catalogue.** For a discovery product whose whole purpose is findability, being in the place researchers actually search from is worth more than any feature CKAN offers.

### 2.2 Can it use the exact same records as CKAN, so it can be promoted later?

**Yes, and this should be an enforced contract rather than an aspiration.**

Make the canonical record **a CKAN `package` dict**, serialised as JSON, one file per record, in the object store and in the repo. The static site is a *renderer* of those records. CKAN, if ever needed, is a second renderer — and the loader that populates it is the same `package_create`/`package_patch` loader already specified in the CKAN plan. Nothing is thrown away by choosing static now.

The trap is drift: a static site will happily render records that CKAN would reject, and you won't discover this until you try to promote 3,000 of them at once. So **run a `validate-ckan-compat` check in the build**, failing on anything CKAN's API would refuse:

- `name` — unique, lowercase, alphanumeric plus `-` and `_`, ≤100 chars
- `tags` — alphanumeric plus `-`, `_`, `.`, 2–100 chars each
- `license_id` — must exist in CKAN's licence register (map everything through a lookup table; Zenodo/SPDX identifiers do not all match CKAN's)
- `extras` — string values only, unless declared in the scheming schema
- `owner_org` / `groups` — must reference entries that exist in `organizations.yaml` / `groups.yaml`, which are themselves part of the canonical data in the repo

Keep the `ckanext-scheming` schema file in the repo even though nothing consumes it yet. It's the written definition of the record shape, it documents the custom fields, and it's the input CKAN needs on the day you promote.

**Promotion then costs about a day:** stand up the CKAN plan's Terraform, run the loader against the JSON, done.

### 2.3 Can the harvester be AI-based?

**Partly — and the boundary matters more than anything else in this document.**

Where an LLM genuinely earns its place:

- **Heterogeneous HTML** — the task microsites, where the alternative is twenty bespoke parsers that each break silently. This is the real win and it's a big one.
- **Classification** — `resource_kind`, task attribution, subject tags against a controlled vocabulary.
- **Duplicate adjudication** — "are these the same artifact?" where there's no shared DOI. As a *proposer*, with the decision recorded.
- **Source discovery** — "does this task have a GitHub org, a Zenodo community, a data page?"

Where it must **not** be used:

- **Anywhere a structured API exists.** Zenodo, DataCite, Crossref, GitHub, OSTI all return clean JSON. Sending that through a model adds cost, latency and hallucination risk for zero benefit. Tier 1 stays deterministic, always.
- **Identifiers, dates, names, licences, URLs.** Never accept a DOI a model produced. **Every extracted DOI is resolved against DataCite or Crossref before the record is accepted**; if it doesn't resolve, the record is dropped and flagged. That single rule converts hallucination from silent corruption into a caught error, and it's cheap.

Three design requirements:

**Structured output.** JSON-schema-constrained responses, validated on receipt. Not free-text parsing.

**Provenance per field.** Every field carries `extraction_method` (`api` | `pattern` | `llm`), and where it's `llm`, the model, prompt version and confidence. The record page shows a visible badge on machine-inferred fields. In a Task 43 / FAIR-literate audience, being honest about which metadata is inferred is the difference between a credible catalogue and one that gets dismissed after someone spots a confidently wrong abstract.

**Cache the extractions, and commit them.** Key on `sha256(page_content + prompt_version + model_id)`. This does two jobs at once. It makes monthly runs nearly free — most pages don't change, so most months invoke the model almost not at all. And it restores the reproducibility your rebuild model depends on: **a rebuild replays the committed cache rather than re-inferring**, so it produces byte-identical output. Without this, "rebuild from the repo" and "AI harvester" are in direct conflict.

**Cost:** the first full run over a few thousand pages at ~4k tokens each on a small fast model lands somewhere in the **$5–20** range. Subsequent monthly runs are pennies. Keep the model behind a one-function interface so it's swappable, and escalate to a larger model only for records flagged low-confidence.

---

## 3. Architecture

### 3.1 The uncomfortable conclusion: you may not need GCP at all

Once Postgres, Redis and Cloud Run are gone, what remains is: run a script on a schedule, commit the output, build a site, serve some files. That is entirely within GitHub's free tier.

**Option A — GitHub only (recommended).**

```
sources.yaml ──┐
corrections/ ──┤
               ▼
      GitHub Actions (cron: monthly + workflow_dispatch)
               │  harvest → extract (cached) → reconcile → validate
               ▼
      records/*.json  +  cache/*.json   ──commit──► repo
               │
               ▼
      build (SSG + Pagefind index) ──► GitHub Pages ──► HTTPS, custom domain
```

- **Cost: $0.** Actions minutes are free on public repos; Pages includes HTTPS and custom domains.
- **Infrastructure: none.** No Terraform, no state, no IAM, no billing account, no Google identity problem — §3.1 of the CKAN plan evaporates entirely.
- **One artifact.** The repo *is* the system: sources, corrections, records, cache, site, docs.
- Only secret is an LLM API key in GitHub Secrets.

**Option B — GCP variant**, if OST policy requires it: Cloud Run Job (harvest) + Cloud Scheduler + **Firebase Hosting** (free tier includes HTTPS on a custom domain — do *not* use raw GCS website hosting, which needs a ~$18/month load balancer to get HTTPS). Roughly **$1–3/month**. Terraform survives, but manages about six resources.

I'd take Option A. Note the lock-in worry that usually argues against this doesn't apply: the output is a directory of JSON and static HTML. Rehosting anywhere is an afternoon, which is exactly the property you wanted from the escape hatch — except now the escape hatch *is* the architecture.

The awkward part is that this leaves your Terraform/IaC preference with almost nothing to do. That's a real loss of a tool you're fluent in, but manufacturing infrastructure to justify managing it is the wrong trade.

### 3.2 Repo layout

```
├── sources.yaml            # the source register (the only "config" that matters)
├── annotations/            # local additions only (tasks, notes, links, pins) — source fields are never edited
├── organizations.yaml      # CKAN-shaped org definitions
├── groups.yaml             # CKAN-shaped group definitions (= tasks)
├── schema/ckan-scheming.json
├── harvest/                # adapters, extractors, reconciler, validators
├── cache/                  # committed LLM extraction cache (content-hash keyed)
├── records/                # canonical CKAN package dicts, one JSON per record
├── site/                   # SSG templates
├── docs/                   # Obsidian vault
└── .github/workflows/
```

### 3.3 Scheduling, and the 60-day dormancy trap

GitHub disables scheduled workflows after **60 days with no repository activity**, and only *commits* count — tags, releases, issues and merged PRs do not. Official wording covers public repositories; reports conflict on private ones, so assume it applies either way.

This mostly solves itself here, because the harvest **commits records back to the repo** on every run, which is exactly the activity the keepalive actions on the Marketplace fake. Four measures make that reliable rather than incidental:

1. **Always commit, even on a no-op run.** Write `state/last-run.json` every time — timestamp, per-source record counts, displacement/pin notices, cache hit rate — so a run where nothing upstream changed still produces a diff. This is the whole fix, and it costs three lines. Implement it inline rather than adding a third-party keepalive action: a Marketplace action running in a workflow with repo write permissions is a supply-chain risk you don't need for something this trivial.
2. **Run weekly, not monthly.** With the extraction cache, a week where nothing changed costs no LLM calls and a minute of runner time. It shortens the exposure window from 30 days to 7 — you'd need eight consecutive failures to reach dormancy, by which time you'd have had eight failure emails.
3. **Surface staleness on the site, not in the Actions tab.** Render "last updated: *date*" on the homepage from `last-run.json`, and style it as a warning past 45 days. Nobody checks a CI dashboard for a dormant project; a stale banner on the front page is seen by whoever next visits, including you. This doubles as honest provenance for users.
4. **Optional dead-man's switch.** If you want independent detection, a free monitor (healthchecks.io or similar) pinged at the end of each run will email if the ping stops. Prefer this over an *external cron trigger*: a monitor that dies costs you monitoring, whereas an external trigger that dies costs you the catalogue. Adding a service to trigger the job would reintroduce exactly the vendor sprawl this architecture removed.

Two practical notes. GitHub warns by email before disabling, and re-enabling is one click, so the worst realistic outcome is a skipped cycle — the harvest is idempotent, so the next run catches up completely. And because pushes made with `GITHUB_TOKEN` deliberately don't trigger further workflows, **build and deploy must happen in the same workflow run as the harvest**, not in a separate workflow listening for the commit. That one catches everybody once.

Add `workflow_dispatch` alongside the cron so on-demand runs are a button.

### 3.4 Plugging in the LLM

This is the only remaining paid dependency, and the only remaining account. Three things matter: keeping the bill small, keeping the key owned by an organisation rather than a person, and making sure the catalogue survives the key expiring.

#### Shrink the problem first: backfill locally

The expensive pass is the first one — a few thousand pages seen for the first time. **Run that on your laptop**, with your own key, and commit the resulting extraction cache. From then on CI only ever handles the delta: a handful of pages a week that actually changed.

That reframes the billing question from "we need a funded API account" to "someone spends about $20 once, and CI needs a key for maybe a dozen calls a week." It also means the initial run isn't fighting free-tier rate limits or Actions' job time ceiling.

#### Provider — decided: GitHub Models in CI, your own key for the one-off backfill

The binding constraint is **account admin, not cost**. An account with a payment method has an owner, an expiry, a recovery path and a person who must still exist; $0.40/month of that is worse than $0.00 of nothing. So the target is *no additional account at all*.

**GitHub Models achieves that.** Inference is free within rate limits, and in Actions the built-in `GITHUB_TOKEN` gains inference access simply by declaring `permissions: models: read` — no PAT, no API key, no repo secret, no vendor account, no card. The endpoint (`https://models.github.ai/inference`) is OpenAI-compatible, so it sits behind the existing `extract()` interface unchanged. GitHub states inference runs on GitHub and Azure infrastructure and that data isn't used for training.

**The free-tier limits are the catch**, and they're the reason this works for the delta but not the backfill. Published figures have been in the region of 10 requests/minute and 50–150 requests/day depending on model tier, with per-request caps around 8k input / 4k output tokens. Check current numbers before relying on them; they have moved and community threads note they could tighten further.

Against our workload:

| Workload | Volume | Fits free tier? |
|---|---|---|
| CI delta | ~15 pages/week | **Comfortably.** Cleaned text of ~3k tokens sits well inside the 8k input cap |
| Local backfill | ~3,000 pages | **No.** At 50/day that's two months. Run it once on your own key (~$20 on a Haiku-class model, ~$10 batched) |

#### Consequence: two model lineages, and why it no longer matters

Last time I argued against mixing providers because it split the cache lineage to save five dollars a year. The trade is different now — the split buys the elimination of an entire account — so accept it, and design the variance away:

**Prefer extraction over generation.** Titles, DOIs, dates, resource kinds and task numbers are *copied* from the page, not composed; descriptions are taken verbatim from source abstracts rather than summarised. If the model's job is to locate and classify rather than to write, output is near-identical across models and the JSON schema enforces structural consistency regardless. Combined with `model_id` already being part of the cache key and recorded per field, mixed lineage becomes an auditable footnote rather than a quality problem.

#### The stronger fallback: no LLM in CI at all

Worth building even if GitHub Models works, because it makes the project immune to any future change in GitHub's free-tier terms.

The delta is roughly fifteen pages a week. That does not need to be automated. CI can run Tier 1 deterministically, queue every Tier-3 cache miss into `state/pending-extraction.json`, and stop. Whenever someone cares — monthly, quarterly, never — they run `make extract` on their own machine with whatever key they personally have, and commit the resulting cache.

The LLM becomes a **human-operated tool rather than a system dependency**. Zero accounts attached to the project, permanently, under any provider's terms. Tier 1 keeps the catalogue current and automatic throughout; only new microsite records wait for someone to bother.

Recommended posture: default to GitHub Models in CI, fall through to the pending queue on any failure, rate limit or unavailability. Both paths cost nothing and neither requires an account that anyone has to administer.

Not recommended: **OpenRouter** (adds a vendor to buy swappability the interface already provides) and **self-hosting on the runner** (Actions runners are CPU-only; small CPU models aren't reliable enough for schema-constrained extraction).

#### If you ever do want a paid key

Anthropic has no ongoing free API tier — new Console accounts get a small one-time trial credit (recently ~$5, phone verification required), and production usage is billed per token. Google's Gemini API is the one with a standing free tier, though it still means a Google account to administer. If you go paid, use **prepaid credits rather than a card on auto-recharge**: a card expires, or belongs to someone who left, or quietly funds a runaway loop, whereas prepaid credit sits there for years and caps the blast radius absolutely. At delta volumes, $50 is roughly a decade.

### 3.5 Site framework — Astro

**Recommendation: Astro.** Familiarity is a legitimate tiebreaker given the time box, but there are three reasons it's the right choice on merits.

**It fits the record contract without distorting it.** Astro's content layer reads plain JSON off disk via a glob loader, so `records/*.json` stays the canonical CKAN-shaped artifact and Astro is a pure renderer. Guard this boundary: *do not* let Astro's content conventions colonise the record format — no frontmatter-flavoured schemas, no framework-specific fields. The day you promote to CKAN, the loader must be able to POST those files unmodified.

**Zod content-collection schemas are the `validate-ckan-compat` gate.** The build fails on a malformed record, which is exactly the enforcement §2.2 asks for, with no separate validator to write or run. Encode CKAN's slug rules, tag character rules and licence lookup there.

**Zero JS by default.** The deployed output is plain HTML with one island for search. Which leads to the point that actually matters for a project that might sit dormant for years:

**The built artifact outlives the build toolchain.** If Astro stops building cleanly in 2030 because Node moved on, the deployed site keeps serving and `records/*.json` is still the catalogue. Toolchain rot costs you "can't rebuild until someone spends an afternoon on dependencies" — not "the catalogue is gone." That bounds the risk enough to accept an npm dependency tree.

**Longevity discipline:**

- **Pin the runner image and Node version explicitly** — `runs-on: ubuntu-24.04`, not `ubuntu-latest`; `actions/setup-node` with a fixed version, not `lts/*`. Floating references are how dormant repos break unattended.
- **Commit `package-lock.json`, build with `npm ci`.**
- **Keep the Astro layer thin.** Every integration is a future migration. Five page types need no UI framework, no CMS adapter, no image pipeline.
- **Do not auto-update dependencies.** This is contrary to normal practice and correct here: there is no runtime and no user input, so dependency CVEs are largely irrelevant to a static build. A pinned, dormant repo is far more likely to build in three years than one Dependabot has been bumping unattended. Update only when you need something.
- The residual risk is supply-chain compromise of a *build-time* dependency, since the build runs with a write-capable token. Minimal deps plus `npm ci` from the lockfile is the mitigation; keep workflow permissions scoped (`contents: write` only where the harvest commits, `pages: write` and `id-token: write` only on deploy).

**Pagefind** runs after `astro build`, indexing `dist/`. Filters come from `data-pagefind-filter` attributes rendered onto the record pages, so facets (task, resource kind, year, licence, source) are declared in the template rather than configured separately. Its drop-in UI component is good enough to ship; a custom UI over its JS API is a later nicety.

**Alternatives, briefly.** *Hugo* is the maximum-longevity option — a single pinned Go binary with no dependency tree, building 3,000 pages in about a second, still working untouched in a decade; the cost is Go templates and emitting markdown-with-frontmatter from the harvest step. *Jinja2 in the existing Python codebase* would mean one language, no JS toolchain and nothing to upgrade ever, at the cost of hand-rolling pagination and navigation. Both are defensible; neither is worth trading against your existing fluency and a hard time box.

### 3.6 Harvester language — Python, with discipline

**Recommendation: Python, pinned with `uv`.** Go is genuinely the stronger answer on durability alone; it loses on two other axes that matter more here. Rust is the wrong tool for this job.

**First, the framing that lowers the stakes.** The durable artifact is the data, not the code. `records/*.json` and `cache/*.json` are plain committed JSON. If the harvester rots completely, the site still builds and still serves, and someone rewrites the harvester in whatever is fashionable in 2032 against a record format that hasn't changed. Same argument as §3.5: this decision is lower-stakes than it feels.

**Why not Rust.** Performance is irrelevant — the workload is network-bound, waiting on Zenodo's API. What you'd pay is compile times, real churn in the async and HTTP crate ecosystem, and the steepest learning curve of the three. Every one of those cuts against the single property that matters: a stranger being able to pick this up.

**Why Go is tempting.** Go's compatibility promise is the best in the industry — decade-old programs still compile. A single static binary has no runtime to install. This harvester could be written against the standard library plus `x/net/html`, which is close to zero dependency risk. And Go is arguably *more* readable to a stranger than idiomatic Python: explicit error handling, no decorators, no metaclasses, nothing you need to know before you can trace what a function does.

**Why Python wins anyway:**

1. **Audience.** The likely inheritor is a wind-energy researcher at DTU, PNNL or NREL, not a professional backend engineer. Python is that community's lingua franca. Go removes dependency risk at the cost of shrinking the pool of people who could maintain this — and since nobody is committed to maintaining it yet, that pool is the scarcer resource.
2. **`trafilatura` is genuinely best-in-class** at main-content extraction, and Go's equivalents are merely adequate. Extraction quality feeds directly into LLM input quality and token cost, which is the core of Plan B. That's a substantive technical reason, not a preference.
3. **The dependency-rot concern is largely solved.** The classic Python failure — a pinned C-extension package has no wheel for the runner's newer interpreter, tries to build from source, and dies — is eliminated by pinning the *interpreter itself*. `uv` fetches standalone CPython builds, so `uv.lock` plus a pinned Python version reproduces the environment years later regardless of what the runner has in its tool cache.

**Dependency discipline — cap it at four direct dependencies:**

- `httpx` (HTTP)
- `trafilatura` (main-content extraction; brings lxml)
- `pydantic` (schema validation)
- `pyyaml` (sources and corrections)

**No LLM SDK.** GitHub Models is OpenAI-compatible, so inference is a POST with a JSON body. Vendor SDKs churn faster than almost anything else in this stack, and skipping them removes an entire dependency lineage for about fifteen lines of code — while keeping `extract()` trivially swappable.

Pin the Python version in `.python-version`, commit `uv.lock`, install with `uv sync --frozen`. Same no-auto-update posture as §3.5.

**If you'd rather have the durability than the ecosystem**, Go is defensible and I wouldn't argue hard against it: write it stdlib-only, accept weaker content extraction, and record in the ADR that you traded maintainer familiarity for a build that still works untouched in ten years. Worth knowing either way — choosing Go doesn't add a toolchain, since the repo already has Node for Astro. Both options leave you at two.

### 3.7 Components, and Storybook vs a gallery

The instinct is right — encapsulated, individually reviewable components beat a sprawl of inline markup. But the mechanism needs care, because one obvious choice would quietly undermine §2.1.

#### Astro components for content, custom elements only for interactivity

**Do not build record pages out of custom elements.** A custom element only populates when its class definition loads and `customElements.define()` runs, so the built HTML is a shell and the content arrives via JS. That costs two things this project has explicitly banked on: the zero-JS output property, and — more importantly — reliable indexing by **Google Dataset Search**, which §2.1 identified as the single biggest discovery win of going static. Content that requires script execution to exist is the wrong trade for a catalogue whose entire purpose is findability.

**Astro components (`.astro`) already give you the encapsulation you want.** They're composable, prop-driven, and their styles are automatically scoped per component. They render at build time to plain HTML. That solves "no vomit of HTML everywhere" without shipping a runtime.

**Reserve custom elements for genuine client-side interactivity** — realistically the search/filter island, a copy-citation button, maybe a filter chip bar. Write them vanilla; skip Lit. For two or three widgets, `customElements.define()` with a `<template>` is enough, and vanilla custom elements are the most rot-proof interactive technology available *because they're a platform standard rather than a framework* — no version to migrate, ever. Apply them as progressive enhancement over content that is already in the HTML, so a JS failure degrades to a working page.

#### The call: gallery, not Storybook

Three reasons, in order of weight:

1. **Astro components aren't a first-class Storybook target.** Storybook's renderers cover React, Vue, Svelte, web components and plain HTML — `.astro` isn't among them. Adopting Storybook would push you to write components *as* web components purely to satisfy the tool, which is exactly the mistake above. The tool would be driving the architecture.
2. **The component count doesn't justify it.** Record card, record detail, provenance badge, source badge, task chip, freshness banner, pagination, facet chip, search island — roughly ten. Storybook's value scales with component count and team size; at ten components and one developer it's mostly ceremony.
3. **It's the heaviest dependency tree in the repo**, which you've already noted. Being a dev tool bounds the damage but doesn't eliminate it: a dev tool that won't install is a dev tool nobody uses.

**Instead: a `/dev/components` page in the site itself.** One Astro page importing every component and rendering it against fixture data. Roughly a hundred lines, zero new dependencies, builds with the same command as everything else, and reviewable in a browser exactly like Storybook.

It's also *better* in one specific way. Storybook stories use synthetic args; this gallery should render **real records pulled from `records/`, plus a `fixtures/` set of deliberately pathological cases**: missing DOI, 300-character title, no description, withdrawn record, low-confidence LLM-extracted fields, a record belonging to five tasks at once. That exercises the actual data shape, which is where the bugs will be.

Two build details: mark the page `data-pagefind-ignore` so it stays out of the search index, and `noindex` so it stays out of Google.

**Keep the discipline without the tool.** The valuable part of Storybook is the constraint it imposes: components that are isolated, prop-driven and renderable from fixtures alone. Hold that line and Storybook stays cheap to adopt later if it's ever warranted — which it would be if more than one front-end contributor appears. Until then, don't pay for it.

If you want the accessibility checking that Storybook's addon would have provided, run `axe-core` or `pa11y` over a handful of built pages as an optional CI step. That's one dev dependency instead of several hundred.

Register the account against a **shared OST address or distribution list**, never a personal mailbox — same bus-factor reasoning as the GitHub and Google accounts. Set a spend alert anyway.

Store the key as a repo-level Actions secret. Two guardrails, because the repo is public: **never run the harvest workflow on `pull_request`** (secrets aren't exposed to fork-PR runs, but don't rely on that as the only defence), and put the workflow behind an environment whose deployment branch is restricted to `main`.

#### Cost control

- **Feed clean text, not raw HTML.** Extract main content with trafilatura or readability before the model sees it. This cuts tokens by roughly an order of magnitude and *improves* accuracy — boilerplate nav and footers are the main source of spurious extractions.
- **Keep identifiers out of the generative path entirely.** Regex the page for DOIs, GitHub URLs and ORCIDs deterministically, pass that list to the model as context, and ask it to *assign* identifiers to records rather than transcribe them. Combined with the resolve-or-drop rule, hallucinated identifiers become structurally impossible rather than merely unlikely.
- **Cap calls per run** (`MAX_EXTRACTIONS=200`). If a task site redesigns and invalidates 3,000 cache entries, the backlog drains over several weeks instead of arriving as one surprise bill. Report the remaining backlog in `last-run.json`.
- **Small model by default**, escalating only when validation fails or confidence is low.
- Temperature at minimum for stability. Determinism isn't guaranteed even so — which is why the cache, not the temperature, is what makes rebuilds reproducible.
- Batch APIs offer roughly 50% off with a 24-hour turnaround and would suit a weekly cron, but they add asynchronous complexity to a workflow that currently completes in one run. Not worth it at this spend.

#### Degradation — the part that actually matters

**The harvest must never fail because the LLM is unavailable.** Key expired, credits exhausted, provider outage, rate limit hit: the run continues.

- Tier 1 (Zenodo, DataCite, Crossref, GitHub, OSTI) is fully deterministic and carries the majority of records. It keeps working regardless.
- Tier 3 reads the committed cache first. On a cache miss with no working key, it skips the page, appends it to `state/pending-extraction.json`, and moves on.
- The site renders normally; only new task-site records stop appearing, and the pending count shows on the homepage next to the freshness banner.

An unfunded year therefore costs you some new microsite records and nothing else. That is what makes the billing dependency tolerable in an organisation that may take a year to decide anything.

---

## 4. Reconciliation — resolved by mostly abolishing it

Two decisions here: the change-detection mechanism (the **source key**), and the policy that source metadata is **never edited, only annotated**. The second deletes most of what this section used to contain.

### 4.1 The source key

Every adapter defines one **record-level change token** whose semantics it owns. If the key differs from the last scrape, upstream is assumed updated and its metadata is taken wholesale; if it doesn't, the record is skipped and no event is written. One comparison per record, no field diffing, no timestamp philosophy.

The design burden moves to choosing a trustworthy key per source — which is the right place for it, because trustworthiness is source-specific:

| Source | Key | Note |
|---|---|---|
| Zenodo | record revision id (with the version DOI) | InvenioRDM increments it on metadata edits; verify field name during Phase 1 |
| DataCite | `attributes.updated` | reflects client metadata pushes |
| Crossref | `deposited` | **not** `indexed`, which churns without content change |
| GitHub | composite: default-branch SHA + latest release tag + hash(description, topics, licence) | no single trustworthy field exists |
| OSTI | metadata-updated field if provided, else fallback | |
| HTML pages (Tier 3) | normalised content hash | **this already exists — it's the LLM cache key** |

**Universal fallback: a hash of the normalised source payload.** Any adapter whose source lacks a usable token uses the hash, so key selection can never block an adapter — and note that the fallback quietly reunifies the two models: a content hash *is* value comparison, done at record granularity where it's cheap.

A noisy key now costs only a redundant re-scrape and a no-op event, not a clobbered human edit — because of 4.2.

### 4.2 Source metadata is never updated, only annotated

**Adopted.** The original is always authoritative; the catalogue displays what the source says, verbatim as of the last scrape. Local intervention is **additive only**. This is right for three reasons:

1. **It matches the catalogue's epistemic role.** This is an aggregator, not an authority. If a Zenodo record has a typo'd title, then "a typo'd title" is *what Zenodo says*, and the fix belongs at Zenodo, where the author can actually make it and where every other consumer of that metadata benefits. A catalogue that edits upstream metadata forks the truth and starts a drift war it cannot win. Put a "report an issue at the source" link on every record so fixes flow to where they stick.
2. **It deletes a subsystem.** No precedence rules, no supersession semantics, no conflict lifecycle, no normalisation-for-comparison. For a project defined by handoff-to-a-stranger, removing a subsystem is worth more than any feature.
3. **It makes trust checkable.** Anyone can verify any displayed field against its source. There is never a "why does your catalogue disagree with Zenodo" conversation.

**The record is two namespaces:**

- `source.*` — verbatim upstream metadata. Replaced wholesale when the source key changes. Never locally editable.
- `local.*` — additions the sources don't provide: `iea_task[]` attribution, `resource_kind` where the source doesn't type it, curator notes, cross-record relationships (merges, preprint↔published links), `suppressed` for noise records. Latest local event wins within this namespace.

**Collision rule** (yours, with one refinement): if a source later starts providing a field that had been locally added, **the source value displaces the local one for scalars**, the displaced value is retained in the event log, and a notice appears in the run report. For **set-valued enrichments** — `iea_task` above all — union rather than displace: Zenodo adding a Task 43 community must not erase a hand-added Task 49 attribution.

### 4.3 The two places "no update" needs a carve-out

**Tier-3 records, where the "original" is our own inference.** An LLM-extracted record has no authoritative structured source — the extraction *is* the metadata. A human correcting it isn't overriding an authority, they're outranking a model's guess about our own output. So corrections there are legitimate, and they're implemented as **pinned extractions**: the corrected object replaces the cache entry and is marked pinned. On a later scrape where the page's content hash changes, the pin *holds* but a notice fires — the page moved beneath a human judgement, and a human decides. In practice this population is small: the resolve-or-drop rule means any Tier-3 record with a DOI takes its metadata from the DOI resolver, which then counts as an authoritative source under the normal rule.

**Upstream that is known wrong and won't be fixed.** Display the wrong value anyway — verbatim — with a visible **curator note** beside it: "OST note: the licence stated at source appears incorrect; see …". Both truths on the page is better epistemics than a silent override, it's honest to a FAIR-literate audience, and it keeps the pressure where it belongs: on the source.

### 4.4 What remains of the event log

Still `events/<identity-key>.jsonl`, append-only, **append-on-change only** (a scrape whose source key is unchanged writes nothing; `state/last-run.json` records that the run happened). Still ordered by our observation time, with the source key and any source-provided timestamp carried as payload. Still the source of truth from which `records/*.json` is derived and regenerable.

But its job has shrunk to three things: history and provenance for the record page; the store of local annotations; and displacement/pin notices for the run report. Resolution is now trivial — latest scrape for `source.*`, latest local event for `local.*`, collision handled per 4.2 — and the monthly human task is reading a short notice list rather than adjudicating conflicts.

Growth stays proportional to real change: ~3,000 events at seeding, a few hundred a year after, versus ~300 MB/year if every record were snapshotted every week.

**Deletions and disappearances:** when a record vanishes upstream, do not delete it. Append a `withdrawn` event, keep the page with a notice, and preserve the URL so existing citations survive. Link rot is the failure mode a catalogue exists to fight; silently dropping records makes it worse.

---

## 5. What you actually lose

Worth being clear-eyed, because two of these are real:

1. **No self-service contributions from non-technical people.** GitHub PRs work fine for Task 43's crowd and not at all for a programme manager. Mitigate with a simple form (GitHub issue form, or a Google/Microsoft form feeding an inbox) that produces a correction request someone commits. That's a person-shaped process, not a system — and given nobody was going to log in anyway, it costs nothing real.
2. **No write API.** Nothing else can push records in. Given the premise, nothing was going to.
3. **Institutional optics.** Some stakeholders find a recognised platform reassuring, and "it's a static site" can read as unserious in a committee. The counter is strong and true: *the records are already in CKAN's exact format, and standing up CKAN is a day's work whenever there's a reason to.* Say it that way and the optics problem inverts — you've de-risked, not cut corners.
4. Read-only DCAT (publish `catalog.jsonld` as a file — which is what DCAT harvesters consume anyway, so this is barely a loss).

---

## 6. Cost

| | Static (Option A) | Static (Option B, GCP) | CKAN |
|---|---|---|---|
| Hosting | $0 | $0 (Firebase free tier) | $65–115/mo |
| Compute | $0 (Actions) | ~$1–3/mo | included above |
| Database | — | — | $15–30/mo |
| LLM | ~$5–20 once, then pennies | same | same if used |
| **Steady state** | **≈ $0** | **≈ $2/mo** | **≈ $90/mo** |

The strategic point is not the money, it's that **a $0 system with no billing account cannot be switched off by an unpaid invoice or an expired card.** For a project with no budget holder, sitting dormant inside a poorly-administered federation, that's the property that determines whether it exists in three years.

---

## 7. Revised phases

| Phase | Content | Effort |
|---|---|---|
| 0 | Record schema + CKAN-compat validator + `sources.yaml` register; 20 records by hand end-to-end | 2–3 d |
| 1 | Tier-1 deterministic harvesters (Zenodo, DataCite, Crossref, GitHub, OSTI); identity/dedup | 5–7 d |
| 2 | Site build: SSG, record pages, Pagefind search + filters, JSON-LD, sitemap, DCAT export | 4–5 d |
| 3 | LLM extraction layer for task sites + WDH, with cache, provenance and DOI verification | 4–6 d |
| 4 | Reconciliation, corrections, conflict report, link checker, docs vault | 3–5 d |

**≈ 3–5 weeks**, against 5–8 for the CKAN route, and the result has no operational surface at all. Phases 0–2 alone produce something demonstrable and genuinely useful — that's the cut line if your time box is tight.

---

## 8. Revised ADR register

Superseded from v1: **0001** (platform → static, CKAN as promotion path), **0002/0004** (Terraform and CI → GitHub Actions only; IaC largely unnecessary), **0003** (no compute), **0007** (no Solr; Pagefind at build time), **0009/0010** (no orgs, no users, no registration), **0015** (no GCP identity problem).

New:

| ID | Decision |
|---|---|
| 0020 | Catalogue is aggregation-only; no registration, no user accounts [premise] |
| 0021 | Canonical record = CKAN package dict, validated at build [promotion contract] |
| 0022 | Hosting and automation [GitHub Actions + Pages; Firebase variant documented] |
| 0023 | Search [Pagefind; client-side filters] |
| 0024 | LLM boundary [Tier-3 HTML only; never where an API exists; identifiers always verified] |
| 0025 | Extraction cache committed to repo [reproducibility of AI harvest] |
| 0026 | Change detection [per-source record-level **source key**, chosen per adapter; normalised payload hash as universal fallback] |
| 0037 | `events/` is the source of truth; `records/` is a derived materialised view |
| 0038 | **Source metadata is never updated, only annotated** [two namespaces; scalar collisions displace with notice; set-valued enrichments union; Tier-3 corrections = pinned extractions; known-wrong upstream handled by visible curator notes] |
| 0039 | Design system [DTCG tokens; Teresa's Green №236 anchor with computed AA derivatives; hue-locked ramp; violet reserved for machine inference; Inter self-hosted throughout (tabular figures for metadata); medium-weight headings; hover/focus-only link underlines; borders over shadows; light+dark; pa11y-ci gate over the gallery in both themes; rev 2: colour never fills surfaces — neutral backgrounds + left accent bars + outline badges; token-only components enforced in CI] |
| 0027 | Withdrawn records retained, never deleted |
| 0028 | Provenance display [machine-inferred fields visibly marked] |
| 0029 | Scheduling [weekly cron + `workflow_dispatch`; heartbeat commit every run; staleness shown on site] |
| 0030 | LLM access [GitHub Models via `GITHUB_TOKEN` in CI — zero accounts; own key for one-off local backfill] |
| 0031 | LLM degradation [harvest never fails on LLM unavailability; Tier 1 unaffected; misses queue as pending] |
| 0032 | Site framework [Astro + Pagefind; records stay canonical JSON] |
| 0033 | Harvester language [Python; Go and Rust considered and why they lost] |
| 0034 | Toolchain pinning [`uv.lock` + `.python-version` + standalone CPython; `package-lock.json` + pinned Node + pinned runner image; no automated dependency updates] |
| 0035 | LLM access via OpenAI-compatible HTTP, **no vendor SDK** [removes a fast-churning dependency lineage; keeps `extract()` swappable] |
| 0036 | Component architecture [Astro components for content; vanilla custom elements only for interactivity; `/dev/components` gallery instead of Storybook] |

---

## 9. Decisions log

All open questions resolved as of 2026-08-31.

| Question | Decision |
|---|---|
| Architecture | Static site (Option A), CKAN retained as documented promotion path |
| Hosting and automation | GitHub Actions + GitHub Pages. No GCP, no Terraform, no billing account |
| Repo visibility | **Public** — free unlimited Actions minutes, matches the open-data premise, extractions and corrections visible for trust |
| Repo ownership | **OST** in the first instance |
| LLM in CI | **GitHub Models** via `GITHUB_TOKEN` with `permissions: models: read` — zero accounts, zero secrets, zero billing |
| LLM backfill | One-off local run on the author's own key (~$20, Haiku-class); cache committed to the repo |
| LLM fallback | Pending-extraction queue drained locally on demand; no CI LLM dependency required at all |
| Site framework | **Astro**, thin, with Pagefind; records stay canonical JSON |
| Components | Astro components for content; vanilla custom elements for the few interactive bits; `/dev/components` gallery, no Storybook |
| Design | DTCG tokens anchored on Teresa's Green №236 (`design-tokens.json` + `design-system.md`); WCAG 2.2 AA as a build gate |
| Toolchain | Pinned throughout — `uv.lock`, `.python-version`, `package-lock.json`, fixed Node version, fixed runner image, no auto-updates |
| Scheduling | Weekly cron + `workflow_dispatch`, heartbeat commit every run, staleness banner on the site |
| Reconciliation | Record-level source key per adapter; **source metadata never edited, only annotated** (two namespaces); `events/` is truth, `records/` derived |

**Remaining setup items** (actions, not decisions):

1. Confirm the OST GitHub org exists and add a second owner — one owner is a single point of failure with a job offer.
2. Verify current GitHub Models free-tier rate limits before depending on them; the design degrades safely if they tighten.
3. Fix the time box. Phases 0–2 alone produce something demonstrable; that's the cut line.
