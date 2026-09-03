---
type: motivation
id: motivation
status: current
date: 2026-09-03
related: [index, architecture, ckan-promotion-path, decision-history, adr-0020-aggregation-only, adr-0022-hosting-and-automation]
tags: [premise, motivation, scope, cost]
---

# Why this exists

The origin of the requirements, the argument that shaped the architecture, and
what the catalogue deliberately does not do. Read this before [[architecture]]
if you have never seen the project; read [[decision-history]] if you want the
chronology, including the arguments that were lost as well as won.

Every decision named here is fixed by an ADR, and the ADR is the authority. This
page is the *why it was ever worth doing*, not the *what was decided*.

---

## 1. The brief

The project started as one request, recorded here verbatim because it is the
requirements origin and every later decision is a response to some part of it:

> OK, I have a new job, which I'm going to love, but need to get an annoying job
> out of the way first. I need to create and populate a data catalogue. Let's
> make a plan to do this.
>
> I want to use the CKAN product (recommended by a colleague but I don't know
> it), and will use Google Cloud Platform, with which I'm familiar, to host it.
> The data catalogue will be for the IEA-Wind organisation, all of their numerous
> tasks.
>
> The idea is that the data catalogue will be created, then populated by scraping
> all the existing IEA resources (zenodo communities, websites belonging to the
> individual tasks, another data hub that the US contributors to the IEA
> community use) to create a centralised and searchable record of all the data
> products, papers and codebases.... and their locations.
>
> The IEA is a slow, confused, weird, poorly administered, nightmare. I don't
> want to get lumbered with maintaining this product, so once I've created the
> prototype it needs to be handed off; either to someone in IEA or someone else
> in my org (OST). Simplicity is better; especially the fewer the number of
> different services involved the better (eg we'd have to host code somewhere,
> have to trigger updates via a process, have some kind of registration approval
> process, have IAM for administering the system, etc). Fewer is better.
>
> I like to use IAC processes for managing infrastructure state and I'm most
> familiar with Terraform (and hashicorp cloud), although if there's a GCP
> built-in alternative that might be simpler (see previous comment; the fewer
> different services we have to keep admin accounts consistent for, the better).
> Same goes for GitHub.
>
> [...] Outputs (created while executing the plans) should include documentation
> in backlined-markdown (eg obsidian vault style) form that must include: ADRs
> (why we chose what we did), administration Runbooks (how to actually do
> anything maintenance related, like adding admins, upgrading infrastructure,
> etc)

Four requirements are load-bearing, and each is still visible in the
architecture:

| The brief said | The architecture answers |
|---|---|
| populate by scraping what already exists | seven adapters over a `sources.yaml` register — [[architecture]] §2 |
| hand it off; don't get lumbered | the repository *is* the system; there is no state to transfer — [[adr-0022-hosting-and-automation]] |
| fewer services is better | one service: GitHub — [[adr-0022-hosting-and-automation]] |
| ADRs and runbooks as deliverables | `docs/adrs/`, `docs/runbooks/`, and this vault |

The one thing the brief asked for that the architecture *removed* is the
infrastructure-as-code preference. That is a real loss of a tool the author is
fluent in, and it is recorded as such in [[adr-0022-hosting-and-automation]]:
manufacturing infrastructure in order to justify managing it is the wrong trade.

---

## 2. The argument that killed CKAN

The plan was CKAN on GCP for two turns. What ended it was not a technical
finding but an argument about behaviour, made by the author:

> I honestly can't see people loggin gin, going through the claiming process of
> historical artifacts. Ahd, despite my colleague's optimism, I can't see anone -
> having already published a dataset with complete metadata to wherever (eg
> zenodo), why would they then want to also register it a second time with our
> service.

**Nobody who has already published a fully-described dataset to Zenodo will log
into a second system to describe it again.** Registration-based catalogues in
loosely-governed federations do not fail on technology; they fail because the
registration step never happens. And CKAN's entire differentiating feature at
this scale — organisations, roles, dataset ownership, claim workflows — is
machinery for exactly that behaviour.

So the product inverts. **The catalogue asks nothing of anyone.** It watches the
places where IEA Wind people already publish and reflects what it finds.
Corrections are possible but optional, and are made by whoever runs the
catalogue rather than by the artifact's author.

That is a fundamentally different product, and it wants fundamentally less
infrastructure. Everything downstream — no database, no login, no billing
account, records that are CKAN-shaped anyway — follows from this one paragraph.
It is fixed as [[adr-0020-aggregation-only]].

---

## 3. The three questions the inversion had to survive

Going static was only acceptable if it did not cost the three things CKAN was
being bought for. Each got an answer, and each answer is now an ADR.

**Can a static site be searchable and filterable "just like CKAN"?** Yes, and at
a few thousand records noticeably better: CKAN does a full page reload and a
Solr round-trip per facet click, where a static site filters in the browser in
single-digit milliseconds with no network at all. Two discovery properties come
free and turned out to matter more than the search itself — stable, citable
record URLs, and schema.org `Dataset` JSON-LD on every page, which puts the
catalogue in **Google Dataset Search**. See [[adr-0023-search-via-pagefind]].

**Can it use the exact same records as CKAN, so it can be promoted later?** Yes,
and as an enforced contract rather than an aspiration: the canonical record *is*
a CKAN `package` dict, and a `validate-ckan-compat` gate fails the build on
anything CKAN's API would refuse. Nothing is thrown away by choosing static now —
promotion costs about a day ([[ckan-promotion-path]]). See
[[adr-0021-canonical-record-is-a-ckan-package-dict]].

**Can the harvester be AI-based?** Partly — and the boundary matters more than
anything else in the system. A model earns its place on heterogeneous HTML,
where the alternative is twenty bespoke parsers that each break silently. It is
actively harmful anywhere a structured API already returns clean JSON, and it is
never allowed to produce an identifier. See [[adr-0024-the-llm-boundary]].

---

## 4. What the catalogue deliberately does not do

Worth being clear-eyed, because two of these are real losses rather than
non-features.

1. **No self-service contributions from non-technical people.** GitHub pull
   requests work fine for Task 43's crowd and not at all for a programme
   manager. The mitigation is person-shaped, not system-shaped: a simple form (a
   GitHub issue form, or a Microsoft/Google form feeding an inbox) produces a
   correction request that somebody commits as an annotation
   ([[correct-a-record]]). Given that nobody was going to log in anyway, this
   costs nothing real.
2. **No write API.** Nothing else can push records in. Given the premise,
   nothing was going to.
3. **Institutional optics.** Some stakeholders find a recognised platform
   reassuring, and "it's a static site" can read as unserious in a committee.
   The counter is strong and true: *the records are already in CKAN's exact
   format, and standing up CKAN is a day's work whenever there is a reason to.*
   Said that way the optics problem inverts — this is de-risking, not
   corner-cutting.
4. **Read-only DCAT.** `catalog.jsonld` is published as a file, which is what
   DCAT harvesters consume anyway, so this is barely a loss
   (`site/src/lib/jsonld.ts`).

Two further scope decisions are recorded rather than hidden: publication lists
that exist only inside a linked PDF are out of scope for v1 and are reported as
a coverage notice (fixture `iea-11`), and the Wind Data Hub disables itself
behind its authentication wall rather than guessing (fixture `wdh-07`). See
[[architecture]] §8 and [[harvest-anomalies]].

---

## 5. Cost, and why it is the point

|  | Static (GitHub) | Static (GCP variant) | CKAN |
|---|---|---|---|
| Hosting | $0 | $0 (Firebase free tier) | $65–115/mo |
| Compute | $0 (Actions) | ~$1–3/mo | included above |
| Database | — | — | $15–30/mo |
| LLM | ~$5–20 once, then pennies | same | same if used |
| **Steady state** | **≈ $0** | **≈ $2/mo** | **≈ $90/mo** |

The strategic point is not the money. It is that **a $0 system with no billing
account cannot be switched off by an unpaid invoice or an expired card.** For a
project with no budget holder, sitting dormant inside a poorly-administered
federation, that is the property which determines whether it still exists in
three years — and it is why [[adr-0030-llm-access-via-github-models]] chose an
awkward free tier over $0.40/month on somebody's card. The binding constraint
throughout was **account administration, not cost**, stated by the author as:

> it's not the credit I'm worried about; it's the admin of the actual account. If
> it's literally zero, that's infinitely easier than $0.40.

The same reasoning drives [[adr-0029-scheduling-and-the-heartbeat-commit]] (a
cron that dies quietly is worse than one that never existed) and
[[adr-0034-toolchain-pinning-and-no-auto-updates]] (a pinned, dormant repository
is far likelier to build in three years than one Dependabot has been bumping
unattended).

---

## 6. Where to go next

- [[architecture]] — the system end to end.
- [[record-format]] — what a record and an event actually are.
- [[decision-history]] — the chronology, including the two models that were
  proposed and superseded.
- [[ckan-promotion-path]] — what promotion would cost, and when it is right.
- [[index]] — the vault map and the decision register.
