---
type: history
id: decision-history
status: current
date: 2026-09-03
related: [index, motivation, architecture, ckan-promotion-path]
tags: [history, requirements, provenance]
---

# Decision history

**What this is:** the requirements record. Every requirement the author stated,
verbatim (typos preserved — this is the history, not a tidy-up), in the order
they were stated, with what each one settled and which ADR now owns it.

**What it is not:** an authority. Where this page and an ADR disagree, **the ADR
wins** and this page is a bug. Its job is to stop an inheritor re-deriving a
decision from first principles by showing what was actually asked for, what was
proposed and rejected, and — most usefully — the two models that were proposed,
accepted, and then superseded by a better one from the author.

The design work happened in a single sitting on **2026-08-31**, between the
project author (OST) and Claude. The complete responses are not reproduced: the
decisions they contain are distilled into the ADRs, which are the authoritative
form. What is preserved here is every requirement, because a requirement cannot
be re-derived from the thing it caused.

---

## 1. The arc, in five moves

1. **CKAN on GCP** — the brief. Fully planned; retained as [[ckan-promotion-path]].
2. **The inversion** — nobody will log in, so ask nothing of anyone. Static.
3. **Removing the last accounts** — no GCP, then no LLM account either.
4. **Choosing the tools** — Astro, Python, no Storybook, pinned everything.
5. **Reconciliation, twice** — a timestamp model, then an event-stream model,
   then the author's source-key + never-update model, which is what shipped.

---

## 2. The record

### Turn 1 — the brief

Quoted in full in [[motivation]] §1. CKAN on GCP, populated by scraping; hand it
off; fewer services is better; Terraform preferred; ADRs and runbooks as
deliverables.

**Settled:** the problem. Research established CKAN 2.11.x, the IEA Wind task
landscape, the Wind Data Hub (`wdh.energy.gov`, formerly a2e/DAP at PNNL) and the
`iea_wind_task_43` Zenodo community. The CKAN plan was written in full — Cloud
Run multi-container, Cloud SQL over the socket connector, Terraform with GCS
state, standalone harvesters rather than `ckanext-harvest`, orgs = institutions
and groups = tasks. → [[ckan-promotion-path]]

### Turn 2 — scale, and the absence of everything

> Records will likely number in the hundreds, or eventually low thousands. How
> does that affect the SOLR decision?
>
> OST doesn't have a google workspace AFAIK - we're microsoft- but could make one.
>
> There is no named successor or budgetholder, but there would be one if the
> prototype got adopted. Realistically, we wouldn't hand over state - we'd just
> tear up a new instance of everything in a new project and re-run hte scrapers,
> so it's less "handover" and more "repeat using the automated process we built
> in prototyping" -> no need to rotate secrets or mess with WIF to update it.
>
> Yes, public read for everything.
>
> No OST GCP organisation AFAIK, and no existing IEA Wind github (orgs only exist
> for the tasks).

**Settled, and all four answers still bind:**

| Answer | Effect, still live today |
|---|---|
| hundreds → low thousands | removes every argument for a search server → [[adr-0023-search-via-pagefind]] |
| no successor or budget holder | **rebuild from repo, not handover** — the property the whole architecture optimises for |
| fully public read | no read-path authorisation anywhere; makes the public-repo, free-Actions posture possible |
| no GCP org, no IEA Wind GitHub org | the repository lives under OST; Task 43's org is the plausible adoption target |

The "rebuild, don't hand over" answer is the load-bearing one. It is why there is
no state to transfer, no secret to rotate ([[no-secrets-to-rotate]]) and no
project to move.

### Turn 3 — the inversion

> yep, you got it exactly. I honestly can't see people loggin gin, going through
> the claiming process of historical artifacts. Ahd, despite my colleague's
> optimism, I can't see anone - having already published a dataset with complete
> metadata to wherever (eg zenodo), why would they then want to also register it
> a second time with our service.
>
> So, Architecture option 2 becomes: Could we do this entirely as a static site,
> using (preferably) an AI based harvester. So we maintain a central list of
> sources (eg "this community on zenodo", "the wind data hub", a central list of
> modifications (eg manual requests to update or add metadata) and completely
> dispose of postgres, redis and cloud run - instead relying on simple scheduled
> jobs to scrape data (once a month, say, or on demand), reconcile any manual
> corrections by DOI (keep original value and published date, then any
> correction, then any change in metadata due to update of the original and
> prioritise by freshness). Could that static site be searchable and filterable
> just like CKAN? Could it use the exact same form of records as the CKAN stored
> articles in the GCS store (ie could be promoted to CKAN later)
>
> That way there'd be a self maintaining, auto-discovering catalogue, whose
> contents you could tweak a bit if you wanted, with absolutely no server and no
> postgres. Costs should go down well into single digit dollars per month, if
> that.

**Settled:** the architecture. Yes to all three questions —
[[adr-0023-search-via-pagefind]], [[adr-0021-canonical-record-is-a-ckan-package-dict]],
[[adr-0024-the-llm-boundary]] with [[adr-0025-the-extraction-cache-is-committed]]
and [[adr-0028-provenance-is-displayed]]. The premise itself became
[[adr-0020-aggregation-only]], and the conclusion that GCP was now unnecessary
became [[adr-0022-hosting-and-automation]].

**Superseded:** the *freshness rule* proposed in this turn ("prioritise by
freshness") was flagged as a failure mode in the same response and replaced
twice — see turns 11 and 12. Nothing of it survives.

### Turn 4 — the dormancy trap

> No GCP is very good: less billing and admin by far… but how would you schedule
> the rebuild? Github actions schedular stops working if a repo is dormant…

**Settled:** the single most operationally important detail in the system.
GitHub disables scheduled workflows after 60 days with no repository activity,
and only *commits* count. The harvest already commits — made reliable rather
than incidental by four measures: a heartbeat commit **every** run including
no-ops, weekly rather than monthly cadence, staleness shown on the site rather
than in the Actions tab, and an optional external monitor (never an external
cron *trigger*). → [[adr-0029-scheduling-and-the-heartbeat-commit]],
[[re-enable-a-dormant-cron]]

This turn also produced the constraint that shapes `catalogue.yml`: pushes made
with `GITHUB_TOKEN` deliberately do not trigger further workflows, so **build and
deploy must live in the same workflow run as the harvest**. That one catches
everybody once.

### Turn 5 — the LLM, and who pays

> We'll definitely go with option A rather than B, for the static architecture.
> We could do with some recommendations for how to plug in the LLM though.
> Presumably that'll need a billing account somewhere?

**Settled:** GitHub Actions + Pages over the GCP/Firebase variant, and the shape
of the LLM dependency — shrink the problem by running the expensive backfill
locally once, so CI only ever handles the weekly delta; keep the provider behind
one `extract()` function; and the hard rule that **the harvest never fails
because the LLM is unavailable**. →
[[adr-0031-the-harvest-never-fails-on-llm-unavailability]]

### Turn 6 — free tiers, and a standing instruction

> It'll be a public repo, and probably OST owning it in the first instance. If we
> can work on the gemini free tier let's do that, but I prefer you (! :) ) so if
> anthropic has a free tier that's preferred. We can do the first local run on my
> own token budget with yourself. The "No CKAN" budget doesn't need selling, and
> regardless it's up to me to manage human interactions. You do AI stuff, I do
> human stuff - please store a memory that I don't need advice about how to
> handle colleagues.

**Settled:** the repository is **public** (free unlimited Actions minutes, matches
the open-data premise, and makes extractions and corrections visible for trust)
and **OST-owned in the first instance**. Anthropic was found to have no ongoing
free API tier — a one-time trial credit only.

**Also settled, and still binding on anyone working here:** *the human manages
human interactions; keep advice to the technical.* This is a standing
instruction, carried in `CLAUDE.md`.

**Superseded next turn:** the recommendation of a single paid provider.

### Turn 7 — admin, not cost

> it's not the credit I'm worried about; it's the admin of the actual account. If
> it's literally zero, that's infinitely easier than $0.40. So whilst not a hard
> criterion, it seriously helps.

**Settled, by reversing the previous turn.** The binding constraint is *account
administration*, not cost: an account with a payment method has an owner, an
expiry, a recovery path and a person who must still exist. **GitHub Models** —
`permissions: models: read` grants the built-in `GITHUB_TOKEN` inference access —
means zero accounts, zero secrets, zero billing. The free-tier limits fit the
weekly delta but not the backfill, which stays a one-off local run. →
[[adr-0030-llm-access-via-github-models]]

Two consequences were accepted deliberately: **two model lineages**, neutralised
by preferring extraction over generation ([[adr-0024-the-llm-boundary]] rule 5);
and the **stronger fallback** of no LLM in CI at all, with the pending queue
drained by hand whenever somebody cares →
[[adr-0031-the-harvest-never-fails-on-llm-unavailability]],
[[drain-the-pending-extraction-queue]].

### Turn 8 — the site framework

> yep perfect. can you pdate the plan (the open questions I've all resolved) and
> now let's make a comment on the framework itself; we need a static site
> generator right? would we use Astro, which I know a bit?

**Settled:** Astro, on merits beyond familiarity — its content layer glob-loads
plain JSON so records stay canonical and CKAN-shaped, its Zod collection schemas
*are* the CKAN-compat gate, and it emits zero JS by default. The argument that
mattered most: **the built artifact outlives the build toolchain.** If Astro
stops building cleanly in 2030, the deployed site keeps serving and the records
are still the catalogue. Hugo (maximum longevity) and Jinja2 (one language, no
JS toolchain) were considered and are recorded as the alternatives. →
[[adr-0032-site-framework-astro]]

### Turn 9 — the harvester language

> I don't know Hugo, and my next question is about python. Could we build this
> service in rust or go; that way eliminate problems with python dependencies
> over the years? Or perhaps the readability of python is better?

**Settled:** Python, pinned with `uv`. Rust loses on compile times, crate churn
and learning curve for a network-bound workload. Go is genuinely stronger on
durability and loses on two things that matter more: the likely inheritor is a
wind-energy researcher, not a backend engineer; and `trafilatura` is
best-in-class at the content extraction that feeds the LLM. The framing that
lowered the stakes: **the durable artifact is the data, not the code.** →
[[adr-0033-harvester-language-python]]

### Turn 10 — pinning, no SDK, and Storybook

> OK, so let's stick with python but explicitly mention uv/node pinned
> dependencies - and rhte decision to use the OpenAI API rather than an SDK - in
> the ADRs.
>
> Next up is storybook and webcomponents. I don't want a vomit of html everywhere
> - encapsulated webcomponents that can be properly storybooked will be best
> practice here. The storybook itself will be the most vulnerable to dependency
> rot, but as it's an ancillary dev tool I'm less concerned about that. An
> alternative would be a rudimentary components gallery that we could develop
> against - your call.

**Settled:** two decisions split out explicitly at the author's request —
[[adr-0034-toolchain-pinning-and-no-auto-updates]] and [[adr-0035-no-vendor-sdk]].

**Pushed back on, and accepted:** building record pages out of custom elements
would leave the built HTML an empty shell, undermining both the zero-JS property
and — more importantly — indexing by Google Dataset Search, which is the single
biggest discovery win of going static. Astro components give the encapsulation
without a runtime; vanilla custom elements are reserved for genuine
interactivity. The call was **gallery, not Storybook**: `.astro` is not a
Storybook renderer target, so adopting it would push components towards web
components purely to satisfy the tool. → [[adr-0036-component-architecture-and-the-gallery]]

### Turn 11 — the history chain (superseded)

> Perfect. Right, now about this freshness thing, which I thought we'd
> established.
>
> What we want is a history chain, yes? Say we scrape on day 1 (version A) and we
> update/add a piece of metadata (X) on day 2 (version B), and on day 3 we scrape
> again.
>
> If we find that metadata item X (day 3) != X from the latest scrape (day 1),
> then we build with the updated source value. If X (day 3) hasn't changed since
> day 1, then we build with the manual override from day 2.
>
> So basically you're saving an event stream of scrape / edit events in time
> order, where the time is the published source_updated value, with the most
> recent value winning. We can always surface history as part of the actual UI
> later.

**Settled, and still binding:** value comparison beats timestamp comparison,
because source timestamps are unreliable across exactly these sources. The event
log arrived here, with one correction that survived every later revision —
**order by our observation time, and carry any source-provided timestamp as
payload** — plus append-on-change and `events/` as the source of truth. →
[[adr-0037-events-are-the-source-of-truth]]

**Superseded next turn:** per-field diffing. It never shipped.

### Turn 12 — the source key, and never updating

> No, that's not what I've said. I just switched you to fable for better insight
> here. You still need a source key for every source - which for most sources
> will be a source_modified (or similar) timestamp; for github repos it'd
> probably be a tag or even a sha. So if the source key changes, you have to
> assume that the metadata has been updated. The original dataset is the source
> of truth. Then you run the event sequence on the relevant datestamp, check if
> the source key changed, and if it did you have to assume that teh upstream
> source is authoritative.
>
> Also, saying that, maybe we should disallow *update* of metadata entirely. So
> the original is always authoritative. We can *add* items of metadata, useful to
> our own catalogue - which then, if they appear when re-scraped get overwritten.
> What do you think?

**The most consequential turn in the project, and both halves are the author's.**

The **source key** replaces field diffing entirely: one record-level change token
per adapter, whose semantics that adapter owns. If it changed, upstream is
authoritative and its metadata is taken wholesale; if it did not, nothing is
written. One comparison per record — no field diffing, no timestamp philosophy.
The universal fallback is a normalised payload hash, which for Tier 3 is already
the LLM cache key. → [[adr-0026-change-detection-by-source-key]]

**Never update, only annotate** deletes a whole subsystem — no precedence rules,
no supersession semantics, no conflict lifecycle. It is right on three grounds:
it matches the catalogue's epistemic role (an aggregator, not an authority — a
typo'd title is *what Zenodo says*, and the fix belongs at Zenodo); it removes a
subsystem, which for a handoff-to-a-stranger project is worth more than any
feature; and it makes trust checkable, because any displayed field can be
verified against its source. → [[adr-0038-source-metadata-is-never-updated-only-annotated]]

Two refinements were added and both are enforced: scalar collisions **displace**
with a notice, but set-valued enrichments — `iea_task` above all — **union**, so
Zenodo adding a Task 43 community cannot erase a hand-added Task 49 attribution.
And two carve-outs where "no update" needs one: Tier-3 pinned extractions (where
the "original" is our own inference) and visible curator notes for upstream that
is known wrong and will not be fixed.

This turn renamed `corrections/` to `annotations/` — the directory is additive by
name as well as by rule.

### Turn 13 — the design system

> yep that's correct. Good, making progress. Now, we've got everything
> structural. Can you propose a design system (based on the design system theme
> standard) for this? I want it to look MUCH less naff than the very-old CKAN
> whilst being straightforward, professional and content-appropriate. Whoever
> chose the IEA Wind logo was obviously blind and pathologically lacking in
> design skill; it's a mid green that doesn't work anywhere but let's go with
> farrow and ball's teresa's green to start our prototyping. Why? I like it :)
>
> The entire app must be ARIA appropriate, so there should be some ally-checker
> in the loop, too. It'll need a dark and a light mode.

**Settled:** DTCG tokens anchored on Farrow & Ball Teresa's Green №236, with AA
derivatives **computationally solved** rather than eyeballed; violet reserved for
machine inference; light and dark; and accessibility as a **build gate** rather
than a review step. → [[adr-0039-design-system]], [[run-the-a11y-gate]]

### Turn 14 — colour never fills a surface

> yeah, don't do green backgrounds, keep that to black/grey. Look at some of the
> HTML in this chat [...] The highlighted bars along the left of panels in teh
> accent colour are lovely. Of course the fonts aren't appropriate, but we can
> use our own font system.

**Settled:** the rule that gives the site its character — **colour never fills a
surface.** Neutral backgrounds only; colour appears as text, icons, outline
badges, focus rings and 3px square-cornered left accent bars. Tinted status
backgrounds were deleted from the token set, the neutrals were re-derived
near-achromatic, and every contrast pair was re-verified. Token discipline was
codified at the same time: components consume tokens with zero hardcoded values,
enforced by a CI grep. → [[adr-0039-design-system]] §8

### Turn 15 — the handover

> Great, give me the entire data package from this chat [...] I'll set it up for
> claude in a repo to take forward from here.

Everything above became this repository.

---

## 3. What was proposed and rejected

Kept because the tempting mistakes are the ones worth writing down. Each links
to the ADR that owns the refusal.

| Rejected | Why | Owner |
|---|---|---|
| CKAN + Cloud Run + Cloud SQL | the registration behaviour it is built for will not happen | [[adr-0020-aggregation-only]] |
| Solr, or any search server | at low thousands of records, client-side beats a round-trip | [[adr-0023-search-via-pagefind]] |
| MiniSearch, Orama, DuckDB-WASM | fine, but more machinery than a handover artifact should carry | [[adr-0023-search-via-pagefind]] |
| LLM anywhere a structured API exists | cost, latency and hallucination risk for zero benefit | [[adr-0024-the-llm-boundary]] |
| accepting a model-produced identifier | silent corruption in a product whose whole value is findability | [[adr-0024-the-llm-boundary]] |
| a paid LLM account, even at $0.40/mo | the admin is the cost, not the money | [[adr-0030-llm-access-via-github-models]] |
| OpenRouter | adds a vendor to buy swappability the interface already provides | [[adr-0030-llm-access-via-github-models]] |
| self-hosting a model on the runner | Actions runners are CPU-only; small CPU models are not reliable enough for schema-constrained extraction | [[adr-0030-llm-access-via-github-models]] |
| a Marketplace keepalive action | a third party inside a workflow holding `contents: write` | [[adr-0029-scheduling-and-the-heartbeat-commit]] |
| an external cron trigger | a monitor that dies costs you monitoring; a trigger that dies costs you the catalogue | [[adr-0029-scheduling-and-the-heartbeat-commit]] |
| Hugo, Jinja2 | both defensible; neither worth the time box against existing fluency | [[adr-0032-site-framework-astro]] |
| Rust; stdlib-only Go | wrong tool; and the inheritor pool is scarcer than the durability | [[adr-0033-harvester-language-python]] |
| a vendor LLM SDK | the fastest-churning dependency lineage in the stack, for ~15 lines of code | [[adr-0035-no-vendor-sdk]] |
| Storybook | `.astro` is not a renderer target, so the tool would drive the architecture | [[adr-0036-component-architecture-and-the-gallery]] |
| custom elements for record content | empty built HTML breaks Google Dataset Search and zero-JS | [[adr-0036-component-architecture-and-the-gallery]] |
| automated dependency updates | a pinned dormant repo builds in three years; a bumped one does not | [[adr-0034-toolchain-pinning-and-no-auto-updates]] |
| per-field diffing and precedence rules | replaced wholesale by the source key plus never-update | [[adr-0026-change-detection-by-source-key]] |
| editing upstream metadata | forks the truth and starts a drift war the catalogue cannot win | [[adr-0038-source-metadata-is-never-updated-only-annotated]] |
| deleting withdrawn records | link rot is the failure mode a catalogue exists to fight | [[adr-0027-withdrawn-records-are-retained]] |
| batch inference APIs (~50% off, 24h turnaround) | asynchronous complexity in a workflow that completes in one run, at a spend of pennies | [[adr-0024-the-llm-boundary]] |
| a fifth Python dependency | four is a surface you can audit by hand; a fifth is an ADR | [[adr-0033-harvester-language-python]] |

---

## 4. Effort, as estimated at the time

Recorded because the estimate is the only honest way to read the scope, and
because the cut line was explicit.

| Phase | Content | Estimate |
|---|---|---|
| 0 | record schema + CKAN-compat validator + `sources.yaml`; 20 records by hand, end to end | 2–3 d |
| 1 | Tier-1 deterministic harvesters (Zenodo, DataCite, Crossref, GitHub, OSTI); identity and dedup | 5–7 d |
| 2 | site build: SSG, record pages, Pagefind search and filters, JSON-LD, sitemap, DCAT export | 4–5 d |
| 3 | LLM extraction for task sites and WDH, with cache, provenance and DOI verification | 4–6 d |
| 4 | reconciliation, annotations, notices, link checker, docs vault | 3–5 d |

**≈ 3–5 weeks**, against 5–8 for the CKAN route. **Phases 0–2 alone produce
something demonstrable** — that was the stated cut line if the time box got
tight. Every phase has since landed; see [[architecture]] §8 for what is built
and what is only a recorded gap.

---

## 5. Setup items that were never decisions

Left open deliberately, as actions rather than architecture. They are recorded
here so nobody mistakes them for pending design work.

1. **A second owner on the GitHub organisation.** One owner is not a design; it
   is a single point of failure with a job offer.
2. **Verify GitHub Models' current free-tier rate limits** before depending on
   them. Published figures have been around 10 requests/minute and 50–150/day,
   with ~8k-in / 4k-out token caps, and they have moved before. The design
   degrades safely if they tighten
   ([[adr-0031-the-harvest-never-fails-on-llm-unavailability]]) — but check.
3. **Is Task 43 approachable as a pilot?** The plausible adoption target, and a
   person-shaped question rather than a technical one.
