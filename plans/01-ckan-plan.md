# IEA Wind Data Catalogue — Build & Handoff Plan

**Status:** draft for review
**Author:** (you) / OST
**Date:** 2026-08-31

---

## 0. The organising principle: the repo is the product

You've said the handover model is *not* "here are the keys to a running system" but "here is an automated process — run it again in your own project." That is a better constraint than the one I originally designed for, and it changes the target:

**The deliverable is a repository that builds the entire catalogue from nothing.** `terraform apply` in an empty GCP project, then `harvest run`, and you have the catalogue. The prototype instance is a demo, not an asset. Nobody inherits state, nobody inherits secrets, nobody needs Workload Identity Federation trust relationships repointed.

Three consequences follow, and they should be enforced ruthlessly:

1. **Any manual click is a defect.** Bootstrapping — sysadmin account, organisations, task groups, scheming schema, site title, `robots.txt` — is code in the repo, not steps someone once performed in the CKAN admin UI. If it only exists in the running instance's database, it does not exist.
2. **Secrets are generated, never authored.** Terraform creates them (`random_password` → Secret Manager) and no human ever reads them. This is what makes "no need to rotate on handover" actually true rather than merely hoped for.
3. **The acceptance test is a rebuild, not a backup restore.** Before calling the prototype done, stand up a second instance in a clean project from a clean clone and diff the resulting catalogue. That is the thing you are actually shipping, so it's the thing that must be tested.

There is exactly one class of state that a rebuild cannot reproduce: **human curation edits.** §5.6 handles it, and it's the only reason the export pipeline is load-bearing rather than merely prudent.

**Non-goals (write these down and defend them):**

- We do **not** host data files. This is a catalogue of *records and their locations*. CKAN's DataStore and xloader are disabled. This removes the single largest source of CKAN operational pain and most of the storage cost.
- We do **not** use `ckanext-harvest`. (See ADR-0006 — this is the most important technical decision in the plan.)
- We do **not** build a custom CKAN theme beyond a logo, colours and a homepage snippet. Themes are the second largest source of CKAN upgrade pain.
- We do **not** implement SSO in v1.

---

## 1. Honest assessment of CKAN before you commit

Your colleague's recommendation is defensible, but you should make the choice with open eyes, because you're the one who has to hand it over.

**CKAN requires three backing services** — PostgreSQL, Solr and Redis — and that is not optional. It is a 2007-era Pylons application that has been carried forward onto Flask. The upgrade path between minor versions periodically requires a Solr schema change and a reindex. Current stable is the 2.11.x line; 2.12 is in development and 2.13 is on master. Only the latest patch of the current and previous minor versions receive security fixes, so **you are signing whoever inherits this up for a patch upgrade roughly every few months.** That is the real maintenance obligation, and it needs to be stated explicitly in the handoff agreement rather than discovered.

**What you get in exchange, and why it's still the right call here:** requirement #3 (onboarding organisations, assigning ownership of records to institutions) is a user-and-permissions problem, and CKAN has that built. Organisations, roles, dataset ownership, an invite flow, and a write API all exist. Building that on top of a static site would mean building an admin application, which is a far worse thing to hand over. CKAN also gives you a stable public API and DCAT output, which means the catalogue is harvestable by others and its contents are portable if the project dies.

**But note what the confirmed scale does to this argument.** At hundreds to low thousands of records, all public, nothing about CKAN's *scale* capabilities is load-bearing. Client-side search over 2,000 records in a browser is instant. So the entire case for CKAN now rests on one thing: **the multi-institution ownership and permissions model in §5.** If that requirement softens — if it turns out OST curates everything centrally and institutions never log in — then CKAN is carrying a Solr instance, a Postgres instance and a quarterly patch obligation to provide a login screen nobody uses. Worth holding in mind for the fundamentals conversation.

**The escape hatch, which should be built from day one** (see §5.6): every harvest run writes normalised JSONL to a GCS bucket, and a monthly job exports the whole catalogue as DCAT. At this scale that export is not a degraded fallback — a few thousand records with Pagefind or lunr client-side search is a genuinely good user experience, arguably faster than CKAN. Nobody is trapped, and the fallback is cheap enough to be a live option rather than a disaster plan.

---

## 2. Recommended architecture

```
                    ┌─────────────────────────────────────┐
  users ──HTTPS──►  │  LB + Cloud Armor  (PHASE 2 ONLY)   │  skip for prototype;
                    └──────────────────┬──────────────────┘  use the *.run.app URL
                                       │
                    ┌──────────────────▼──────────────────┐
                    │  Cloud Run service  (min=1, max=1)  │
                    │  ┌───────────┐ ┌──────┐ ┌────────┐  │
                    │  │ ckan      │ │ solr │ │ redis  │  │  3 containers, 1 instance
                    │  │ (ingress) │ │ side │ │ side   │  │
                    │  └─────┬─────┘ └──────┘ └────────┘  │
                    └────────┼─────────────┬──────────────┘
                             │             │ GCS FUSE volume
                   Cloud SQL │ (unix       │
                   Auth      │  socket)    ▼
                   ┌─────────▼──────┐   ┌──────────────────┐
                   │ Cloud SQL      │   │ GCS bucket       │
                   │ PostgreSQL     │   │ uploads/ exports/│
                   └────────────────┘   │ tfstate/ harvest/│
                                        └──────────────────┘
  Cloud Scheduler ──weekly──► Cloud Run Job (harvester) ──CKAN API──► ckan
```

**Why this shape:**

- **One Cloud Run service with sidecars** keeps Solr and Redis inside the same deployable unit. Cloud Run supports multiple containers per service with container start-order dependencies, so `ckan` can be declared to depend on `solr` and `redis`. One `terraform apply`, one revision, one rollback. Sidecar Solr and Redis are ephemeral: Solr's index is fully rebuildable from Postgres, and Redis only holds sessions and a job queue we barely use. Losing them on restart costs a reindex and a re-login.
- **Memorystore is deliberately avoided.** It requires a VPC and Direct VPC egress from Cloud Run — more resources, more IAM, ~$40/mo, and nothing gained for a catalogue with a handful of concurrent editors.
- **Cloud SQL is reached through Cloud Run's built-in connector over a unix socket**, which means **no VPC at all** in this design. That is a large simplification for whoever inherits it: there is no networking to understand.
- **`max-instances = 1` is required, not an economy.** Each instance would carry its own Solr index; two instances means two divergent indexes. Concurrency of 80 on a single instance is far more than this catalogue will ever need. The cost is a few seconds of unavailability during revision switchover, which Cloud Run drains gracefully.
- **CPU must be always-allocated** whenever an instance exists, because Solr's JVM needs CPU outside request handling. **`min-instances` is now a cost lever, not a correctness requirement** — see the scale discussion below.
- **The edge (LB + Cloud Armor) is deferred to Phase 2.** It exists to give you a custom domain and to rate-limit crawlers across CKAN's combinatorial facet URL space. With `max-instances = 1` a crawler storm makes the site slow rather than expensive, which is a tolerable prototype failure mode. `robots.txt` disallowing faceted URLs does most of the work for free. Keep it behind a Terraform variable (`enable_load_balancer`) so flipping it on is one line.

### The Solr question, resolved by scale

Hundreds to low thousands of records changes this from a design risk into a non-issue.

A full `ckan search-index rebuild` at 2,000 datasets takes **tens of seconds**, comfortably inside Cloud Run's 240-second startup probe budget. Spike 1 becomes a five-minute confirmation rather than a decision gate, and **ADR-0003 Option B (the Compute Engine VM fallback) can be dropped from the plan entirely.** The index itself will be tens of megabytes; a 512 MB Solr heap is generous.

Two things follow that are actively useful:

- **Reindexing becomes routine rather than exceptional.** Run it after every harvest and nightly. This eliminates the entire class of "the search index has drifted from the database" bugs that make larger CKAN sites miserable, at a cost of under a minute of background CPU.
- **Scale-to-zero becomes viable.** A cold start is container pull + JVM start + reindex ≈ 60–90 seconds. Too slow to inflict on a random public visitor, but perfectly fine for an instance that exists to be demonstrated to a steering committee. Set `min-instances = 0` while the prototype is dormant and flip to `1` the week of a demo. Note this in the runbook as a deliberate lever, or someone will "fix" the slow first page load by rearchitecting something.

Right-sizing: **1 vCPU / 4 GiB** is enough for CKAN + Solr + Redis at this scale. Remember Cloud Run's local filesystem is in-memory and counts against the memory limit, so the Solr index lives in that 4 GiB.

**Indicative cost:**

| Mode | Configuration | ~Monthly |
|---|---|---|
| Dormant prototype | `min=0`, no LB, `*.run.app` | **$20–35** |
| Under active evaluation | `min=1`, no LB | **$65–85** |
| Adopted / production | `min=1`, LB + Armor + custom domain | **$90–115** |

Cloud SQL smallest shared-core with backups (~$15–30) is the floor in every mode. There is no named budget holder yet, so **build in dormant mode by default** — a $25/month prototype can sit alive for a year while the IEA decides, and that survivability is worth more than fast page loads on an instance nobody is looking at.

---

## 3. Plan A — Deploying CKAN to GCP

### Phase 0 — Spikes (3–5 days)

Do these before writing any Terraform. Each one can invalidate a decision above, and finding that out now is cheap.

| # | Spike | Kill criterion |
|---|-------|----------------|
| 1 | Build the CKAN image, load ~3k synthetic datasets locally, time a full `search-index rebuild` | Confirmation only — expect well under 60s. Record the number in the vault |
| 2 | Run the 3-container stack on Cloud Run in a scratch project; confirm sidecar start-order, GCS FUSE mount for `ckan.storage_path`, and Cloud SQL socket connection | FUSE unusable for uploads ⇒ use `ckanext-s3filestore` against GCS S3-compat, or accept no uploads |
| 3 | Settle GCP identity and billing ownership (see §3.1) — who owns the project, on which Google accounts, against which billing account | Nobody at OST can own a billing account ⇒ stop and resolve; this blocks everything |
| 4 | Probe the Wind Data Hub API surface at `wdh.energy.gov/api/...` — is dataset *listing* available unauthenticated, or only file retrieval? | Auth required ⇒ request a token from PNNL now, it will have lead time; fall back to sitemap crawl |
| 5 | Enumerate Zenodo communities via `GET /api/communities?q=iea+wind`. `iea_wind_task_43` is confirmed to exist; find the rest | — |

### Phase 1 — Infrastructure (5–8 days)

#### 3.1 GCP identity and ownership — a Microsoft shop with no GCP org

This is now the trickiest non-technical decision, and it's worth being precise about it.

A GCP **Organization** resource requires either Google Workspace or **Cloud Identity Free** (up to 50 users, no cost) with a verified domain. OST has neither, is a Microsoft shop, and getting a DNS TXT record onto the corporate domain to stand up a Google identity tenant means a conversation with IT that may be slow and may attract questions you'd rather not answer for a prototype.

**Recommendation for the prototype: a standalone project with no Organization.**

- Owners are two or three **Google accounts created against OST email addresses**. A Google account can be created on any email address without Workspace.
- Billing is an OST-controlled Cloud Billing account. This is the piece that actually matters for continuity — projects move between billing accounts trivially, and the billing account is where finance-side control lives.
- **State the downside plainly in the ADR:** these are consumer Google accounts. OST IT cannot administer, recover or offboard them. If you leave, your account goes with you. Mitigations: at least two owners from day one, and the fact that everything is reproducible from the repo, so losing access to the prototype instance costs a rebuild rather than the project.

**If it gets adopted:** create Cloud Identity Free on the owning organisation's domain and build the new instance inside that Organization from the start — org policies and real offboarding come free. Retrofitting identity onto an existing project is tedious, but since the adoption path is "rebuild from the repo" anyway, that cost is already paid. Do **not** stand up Cloud Identity for the prototype; it's the wrong moment to spend that political capital. (ADR-0015.)

**Repo layout** (single repo — this is the product):

```
├── infra/            # Terraform: one root module, envs via tfvars
├── ckan/             # Dockerfile, ckan.ini template, pinned extensions
├── harvest/          # Python package, one adapter per source
├── docs/             # Obsidian vault (§6)
└── .github/workflows/
```

**Terraform, not HCP.** Remote state goes in a GCS bucket *inside the same project* with versioning and object-lock. State therefore moves with the project during handoff, and there is no second account to keep in sync. The "GCP-native alternative" you asked about is **Infrastructure Manager**, but it executes Terraform under the hood — it replaces where the state lives, not the tool you learn, and it adds an API surface. Plain Terraform CLI + GCS backend is strictly simpler. (ADR-0002.)

**No CI/CD in v1.** This reverses my earlier recommendation, on your point that nobody will be repointing WIF trust relationships at handover. Workload Identity Federation exists to let a *machine in GitHub* act as a *principal in GCP*; with one engineer and a disposable instance, that machinery buys nothing and costs a trust configuration that a future rebuilder has to understand and recreate.

Deployment is instead a documented local command:

```
make deploy   # gcloud builds submit (no repo connection needed)
              # → terraform apply
```

`gcloud builds submit` uploads the local source directory straight to Cloud Build, so **no GitHub↔GCP integration exists at all** — the repo is just where the code lives, and a rebuilder clones it and runs `make`. GitHub Actions is a Phase-2 addition if the project is adopted and gets more than one contributor. Note in passing that Cloud Source Repositories is closed to new customers, so "move Git into GCP" was never an option; stay on GitHub, under the OST org. (ADR-0004, revised.)

**Terraform state still lives in a GCS bucket**, not on your laptop — it's free, it survives a lost machine, and it lets a second person run `apply`. But it is now genuinely disposable: a rebuild starts from empty state by design.

**Resources to create:** project + APIs, Artifact Registry, Cloud SQL Postgres (automated backups on), GCS buckets (uploads / exports / tfstate / harvest), Secret Manager entries (`SECRET_KEY` — mandatory from CKAN 2.11 — DB password, harvest API tokens; all Terraform-generated), Cloud Run service + job, Cloud Scheduler, a billing budget alert. LB, managed certificate and Cloud Armor sit behind `enable_load_balancer = false`.

Two deliberate wrinkles for a disposable instance: **turn Cloud SQL deletion protection off in the prototype tfvars** (it will otherwise block the `terraform destroy` half of your rebuild test, and people work around that by clicking in the console, which breaks rule 1), and skip PITR — automated daily backups are ample when the only irreplaceable state is curation edits that are already being exported.

**The most important alert is the budget alert, not the uptime check.** An orphaned prototype's realistic failure mode is not downtime, it's a bill quietly accruing against a card nobody is watching. Set a budget with alerts at 50/90/100% routed to a **shared OST mailbox or distribution list** — never an individual, since the whole premise is that individuals move on.

**Domain:** don't chase `catalogue.iea-wind.org` yet. Requesting DNS changes from IEA Wind for something that isn't adopted invites a governance conversation you don't need, and the `*.run.app` URL is perfectly demonstrable. Nothing depends on the hostname except `ckan.site_url`, which is one variable. Move this to the adoption checklist.

### Phase 2 — CKAN configuration

- **Extensions, kept to the minimum:** `ckanext-scheming` (custom metadata fields — required, see §4.3) and `ckanext-dcat` (DCAT-AP output, makes the catalogue harvestable by others and underwrites the escape hatch). Defer `ckanext-spatial`; put bounding boxes in extras for now. Every extension is an upgrade-blocker, so each one needs a line in ADR-0008 justifying it.
- **`ckan.auth.create_user_via_web = false`.** Invite-only. This single setting removes spam registration, which is otherwise the number one recurring admin task on a public CKAN, and it aligns exactly with the onboarding model in §5.
- **No SMTP in v1.** Since OST has no Workspace, the alternatives were a Microsoft 365 relay (Microsoft has been retiring basic auth for client submission, so this now means OAuth2 that CKAN's mail layer won't do without work) or a transactional provider like SendGrid or Mailgun, which adds a vendor account for a prototype with maybe twenty users. Neither is worth it, because **with invite-only registration and public-read content, CKAN's only uses for email are account invitations and password resets.** At this user count a sysadmin creates the account via `user_create` and communicates the credential out of band from their own Outlook, and resets passwords the same way. That's a runbook, not a service. Disable or hide the password-reset link so it doesn't fail silently in front of a user.
  *If adopted and the user count passes ~50*, add a provider — and procure it **through GCP Marketplace** so the subscription bills through the project and travels with it, rather than becoming a fourth account someone has to remember exists. (ADR-0011, revised.)
- **Everything public.** No private datasets and no `member` role, which removes read-path authorisation from the design entirely and makes aggressive caching safe if you later add the LB and CDN.
- **DataStore and xloader disabled.** Catalogue only.
- **`robots.txt`** disallowing `/dataset?`-style faceted URLs while allowing dataset pages, so search engines index records but don't crawl the facet cartesian product.

### Phase 3 — Drills

Do these once, while you are still here, and write the runbook from what actually happened rather than from what you expected to happen.

**Drill 0 — rebuild from zero. This is the acceptance test for the entire project.** From a clean clone in a fresh, empty GCP project: `terraform apply`, bootstrap, harvest, and then diff the resulting catalogue against the original. Anything that requires a manual step gets fixed in code, not written up as an instruction. Everything else on this list is secondary to this one, because this is literally the thing you are handing over.

Then:

1. Tear down and rebuild (`terraform destroy` then `apply`) — confirms deletion protection and bucket retention don't block it.
2. Restore Cloud SQL from an automated backup — the one thing a rebuild can't give you back is curation state.
3. Perform a CKAN patch upgrade (2.11.x → 2.11.y) end to end, including reindex.
4. Roll back a Cloud Run revision.
5. Add and remove a sysadmin.

An untested runbook is fiction. A runbook whose author has left and which was never executed is worse.

---

## 4. Plan B — Seeding the catalogue by harvest

### 4.1 The architectural decision: standalone harvesters, not `ckanext-harvest`

`ckanext-harvest` is the "obvious" choice and it is the wrong one here. It introduces long-running gather/fetch worker daemons, a Redis-backed job queue you must keep healthy, harvest source objects that live in the database as hidden state, and failure modes that are debugged through the CKAN admin UI. It would make Redis load-bearing (currently it is not) and it would add a second always-on process to a design that has exactly one.

Instead: **plain Python harvesters that run as a Cloud Run Job and write to CKAN through its public API** (`package_create` / `package_patch`). They run locally with no CKAN installed, they're unit-testable, their state is a file in GCS, and their failures appear in Cloud Logging as stack traces. Whoever inherits this can read them. (ADR-0006.)

### 4.2 Source ladder — identifiers before APIs before HTML

Scraping HTML is the last resort, not the first move. In descending order of preference:

**Tier 1 — structured identifier APIs (do these first; they will yield the majority of records):**

| Source | Access | Notes |
|---|---|---|
| **Zenodo communities** | `GET /api/records?communities=<slug>&size=100` | `iea_wind_task_43` confirmed. Enumerate others via the communities search. Use an API token for the higher rate limit. Now InvenioRDM-backed — expect the newer record schema. |
| **DataCite** | `GET api.datacite.org/dois?query=...` | Catches IEA Wind DOIs *outside* Zenodo — DTU Data, 4TU, NREL's catalogue, institutional repositories. Metadata is CC0. This is the highest-value non-obvious source. |
| **Crossref** | `GET api.crossref.org/works?query=...` | Journal papers. Task outputs cluster in *Wind Energy Science* and *J. Phys. Conf. Series*. |
| **OpenAIRE Graph** | REST | Cross-links publications, datasets and software to projects; good for finding things the other three miss. |
| **GitHub** | REST API | Org-level enumeration (`IEAWindTask37`, the Task 43 org, Task 49's repos) plus topic/keyword search. Capture repo, licence, latest release, and any Zenodo DOI badge — the badge is a free join key to Tier 1. |
| **OSTI** | REST API | DOE-funded outputs, which covers much of the US contribution. |

**Tier 2 — sitemaps and embedded structured metadata:**

- `iea-wind.org` is a WordPress site with a clean `/task-directory/` index and predictable `/taskNN/` pages. Crawl the sitemap, then parse task pages. These pages contain formatted citations with DOIs inline (Task 49's page is a good example — dozens of DOIs in reference lists), so **a DOI-regex pass over task page text, feeding back into the DataCite and Zenodo resolvers, is likely the single highest-yield harvester in the whole project.**
- **Wind Data Hub** (`wdh.energy.gov`, formerly the a2e Data Archive and Portal, run by PNNL for DOE WETO). An `/api/datasets/{project}/{dataset}/...` path exists; Spike 4 determines whether listing is open. Note this is a *federal* system — do not hammer it, identify yourself in the User-Agent, and consider emailing PNNL first. A friendly email to the WDH team may get you a bulk metadata export and save the whole adapter.

**Tier 3 — per-site HTML adapters:** individual task microsites, which are heterogeneous and change without warning. Write one small adapter per site, expect them to break, and make breakage non-fatal (log and continue).

**Explicitly out of scope for v1:** `community.ieawind.org` (Higher Logic) is member-authenticated. Scraping behind a login is a policy question, not a technical one — flag it, don't do it.

### 4.3 Metadata model

Custom fields via `ckanext-scheming`. The minimum viable set:

- `resource_kind` — controlled vocabulary: `dataset` | `publication` | `software` | `report` | `standard`
- `doi`, `source_system`, `source_id`, `source_url`
- `iea_task` — multi-valued (a dataset can belong to several tasks)
- `first_seen`, `last_seen`, `harvest_run_id`
- `curation_status` — `machine` | `human_reviewed` | `human_owned`
- `link_status`, `link_checked_at`

### 4.4 Identity, idempotency and not stomping on humans

- **Identity key:** normalised DOI where one exists; otherwise `sha256(source_system + '|' + source_id)`. The CKAN dataset `name` slug is derived deterministically from this, so every run is an upsert rather than a duplicate-generator.
- **Concept vs version DOIs:** Zenodo issues both. Catalogue the *concept* DOI as the record and treat versions as resources beneath it, otherwise every new version creates a duplicate record.
- **Cross-source dedup:** the same artifact will arrive from Zenodo, DataCite *and* an iea-wind.org citation. Merge on DOI. Keep every discovery path in a `source_url` list — provenance is what lets a human later adjudicate a bad merge.
- **The rule that matters most:** if `curation_status == human_owned`, the harvester updates **only** `last_seen` and `link_status`. It never overwrites a human's edits. A harvester that silently reverts a curator's careful work will destroy trust in the catalogue within a month, and trust is the thing you cannot restore after handoff.

### 4.5 Run mechanics

Cloud Run Job triggered weekly by Cloud Scheduler. Two stages, and keep them separate: **harvest → normalised JSONL in GCS**, then **load → CKAN API**. Separation means you can re-run the load against yesterday's harvest, diff two runs before applying, and — critically — the JSONL is a portable catalogue in its own right.

Every run must be dry-runnable (`--plan`) and must emit a summary: created / updated / unchanged / skipped-human-owned / errors, per source. Politeness throughout: descriptive User-Agent with a contact address, `robots.txt` respected, conditional GET with ETags, and a modest concurrency cap. Harvest *metadata and links only* — never mirror the files.

---

## 5. Plan C — Onboarding organisations

**During the prototype, this is a design plus a scripted runbook plus one pilot — not an operating process.** With no successor in place, you should not be recruiting institutions into a system that might be switched off. Build the taxonomy, script the provisioning, and onboard **one** willing pilot organisation to prove the workflow end to end.

The obvious pilot is **IEA Wind Task 43 (Wind Energy Digitalization)**. Cataloguing, data standards, FAIR principles and open tooling are literally their remit; they already run a GitHub org and a Zenodo community (`iea_wind_task_43`); and if this project is ever adopted by anyone inside IEA Wind, they are the most plausible owner. Treat Task 43 as both the pilot and the adoption pitch target.

### 5.1 The taxonomy decision (get this right, it's expensive to change)

CKAN gives you two grouping constructs, and they are not interchangeable:

- **Organizations** own datasets and carry permissions. Each dataset has exactly one `owner_org`.
- **Groups** are thematic, many-to-many, and carry no ownership.

Therefore: **Organizations = institutions** (DTU, NREL, PNNL, Fraunhofer IWES, ATU Sligo…), because ownership and maintenance responsibility live with institutions and their staff. **Groups = IEA Wind Tasks**, because a single dataset legitimately belongs to Task 43 *and* Task 49, and because tasks start, end and renumber (Task 19 → Task 54, Task 34 → Task 59) in ways that would be destructive if they controlled permissions. (ADR-0009.)

### 5.2 Bootstrap state

At seeding time, ownership is mostly unknown. Everything lands in a holding organisation — `ost-curated` — and is tagged into the appropriate Task group. Nothing is blocked on knowing who owns what.

### 5.3 The claim workflow (deliberately boring)

1. **Request** — an institution expresses interest via a GitHub issue form in the repo. No new service, free, auditable, and the queue is visible to whoever inherits the project. A `mailto:` to a shared address is an acceptable alternative; a bespoke web form is not.
2. **Verify** — the requester has an institutional email address and is confirmed as a Task participant by the relevant Operating Agent. This is a human check and should stay one.
3. **Provision** — a scripted runbook creates the CKAN organisation, invites the named steward as org admin, and bulk-transfers matching datasets out of `ost-curated` via `package_patch owner_org=...`.
4. **Agree** — the steward acknowledges a one-page **Steward Agreement**: keep your records' metadata current, respond to link-rot reports within 30 days, licence metadata as CC0 or CC-BY. One page. If it needs a lawyer, it will never be signed by anyone at the IEA.
5. **Record** — the organisation is marked claimed, with steward name and date recorded in the vault.

### 5.4 Roles

| Role | Who | Count |
|---|---|---|
| Sysadmin | OST + one named IEA Wind contact | Exactly 2, never more |
| Org admin | Institutional steward | 1–2 per org |
| Editor | Institutional staff | as needed |
| Member | read-only for private records | rare |

Two sysadmins is a genuine constraint: one is a bus-factor of one, three is nobody's responsibility.

### 5.5 Adoption package (not a handover)

Since the model is "rebuild in your own project," adoption is a checklist against the *repo*, not the instance:

- Named owner **and named budget holder** identified — separate people, both required. Without these, do not proceed; leave the prototype dormant instead.
- Receiving org creates Cloud Identity Free on their domain, giving a real GCP Organization (§3.1).
- Repo forked or transferred to the receiving org's GitHub.
- `terraform apply` into their new project, run **by them, with you watching**. This is the whole handover. If it doesn't work first time in their hands, the prototype was not finished.
- Harvest re-run from scratch; catalogue diffed against the prototype's export.
- **Curation state migrated** via the export/import path (§5.6) — the only thing a rebuild cannot regenerate.
- Custom domain, load balancer and Cloud Armor enabled; CI added if there's more than one contributor.
- A dated review point recorded: "if no organisation has claimed records in 12 months, here is the decision to make."

Nothing here involves secret rotation, WIF, or moving projects between organisations. That's the point.

### 5.6 Curation state and the exit hatch

A nightly job exports the full catalogue as DCAT and JSONL to a GCS bucket, and **commits the human-curated subset back to the repo.**

That second half is new, and it exists because of the rebuild model. Machine-harvested records are regenerable by definition — re-run the scrapers. But the moment a human corrects a title, merges two duplicates, or claims a record for their institution, that edit exists **only** in the prototype's Postgres. If the adoption path is "tear it down and build a new one," every such edit is destroyed unless it's captured somewhere the rebuild can read.

So: records with `curation_status != machine` are exported as JSONL, committed to `curation/` in the repo, and **replayed by the bootstrap step of every rebuild**, after harvest and before the site goes live. Human judgement becomes source code. This is the single most important thing to get right if you want the prototype's curation effort to survive into production, and it's easy to defer until it's too late.

The same export doubles as the exit hatch: if funding stops, the catalogue survives as flat files and republishes as a static searchable site — which, at a few thousand records, is a perfectly good product in its own right. Write the runbook titled "How to shut this down without losing anything." Given your read on the IEA, it may be the most-used page in the vault, and its existence makes the project far easier for a nervous stakeholder to approve.

---

## 6. Documentation outputs (Obsidian vault)

Lives at `docs/` in the same repo, so documentation versions with the code.

**Under the rebuild model the vault matters more, not less.** The audience is no longer a colleague you can brief over coffee; it's a stranger, possibly eighteen months from now, holding a repo and trying to work out whether to run it. The ADRs are what stop them relitigating every decision from scratch, and the "why not" sections are the valuable part — future-you will be tempted by `ckanext-harvest` all over again.

```
docs/
├── 00-Home.md                    # MOC: links to every index below
├── 10-Decisions/                 # ADR-0001-…
├── 20-Runbooks/                  # RUN-…
├── 30-Architecture/              # system, data model, environments
├── 40-Sources/                   # one note per harvest source
├── 50-Onboarding/                # process, steward agreement, org register
├── 60-Operations/                # cost, alerts, upgrade calendar, SLOs
├── 90-Meta/                      # conventions, glossary, review log
└── _templates/                   # ADR, runbook, source note
```

**Conventions:** YAML frontmatter (`type`, `id`, `status`, `date`, `deciders`, `related`, `tags`) on every note so Dataview can generate "decisions pending", "runbooks not reviewed in 6 months", "unclaimed organisations". Wikilinks are mandatory in both directions — every ADR links to the runbooks it affects and every runbook links back to the ADR that justifies it, so an inheritor landing on any note can walk to context. Every source note in `40-Sources/` links to its harvester module path, its rate limits, its contact human, and its known failure modes.

**ADRs to write** (recommended decision in brackets):

| ID | Decision |
|---|---|
| 0001 | Catalogue platform [CKAN, with static-export escape hatch] |
| 0002 | IaC tool and state backend [Terraform + GCS backend; not HCP, not Infra Manager] |
| 0003 | Compute platform [Cloud Run multi-container; VM fallback **dropped** — scale removes the risk] |
| 0004 | Build and deploy [**no CI in v1**; local `make deploy` via Cloud Build; Actions deferred to adoption] |
| 0005 | Datastore posture [links-only; DataStore and xloader off] |
| 0006 | Harvest architecture [standalone jobs, not `ckanext-harvest`] |
| 0007 | Search index persistence [ephemeral Solr; rebuild on deploy **and nightly**] |
| 0008 | Extension allowlist [`scheming`, `dcat`; each justified] |
| 0009 | Org/Group taxonomy [orgs = institutions, groups = tasks] |
| 0010 | Registration model [invite-only] |
| 0011 | Email transport [**none in v1**; admin-provisioned accounts; Marketplace provider if adopted] |
| 0012 | Custom domain and edge [LB + Cloud Armor **deferred to adoption**] |
| 0013 | Backup and retention [daily automated; no PITR; deletion protection off in prototype] |
| 0014 | Continuity model [**rebuild from repo**, not state transfer] |
| 0015 | GCP identity and project ownership [standalone project, consumer Google accounts, OST billing; Cloud Identity only at adoption] |
| 0016 | Curation state persistence [export to repo, replay at bootstrap] |
| 0017 | Instance posture [`min-instances = 0` while dormant; `1` under evaluation] |

**Runbooks to write** (each ends with "last executed on: ____" — an unexecuted runbook is a hypothesis):

Deploy a change · Roll back a revision · Add/remove a sysadmin · Onboard an organisation · Transfer dataset ownership in bulk · Run a harvest manually / dry-run · Add a new harvest source · Fix a failed harvest · Rebuild the search index · Patch-upgrade CKAN · Upgrade a minor version (incl. Solr schema) · Restore the database · Rotate secrets · Investigate "the site is slow" · Handle a link-rot report · Export the catalogue · Shut it down safely · Transfer the project and repo to a new owner.

---

## 7. Sequencing and effort

| Phase | Content | Effort (1 engineer) |
|---|---|---|
| 0 | Spikes 1–5; DNS request submitted; budget holder named | 3–5 d |
| 1 | Terraform, Cloud Run, Cloud SQL, CI/CD, CKAN running at a `run.app` URL | 5–8 d |
| 2 | Scheming schema, Tier-1 harvesters (Zenodo, DataCite, Crossref, GitHub), load pipeline, dedup | 8–12 d |
| 3 | Tier-2/3 harvesters (iea-wind.org, WDH), link checker, export job | 4–6 d |
| 4 | Onboarding process, org bootstrap, vault completion, drills, handoff | 5–8 d |

**≈ 5–8 weeks at full time, 3 months realistically part-time.** The IEA-side critical path (DNS, WDH access, naming a successor and a budget holder) is longer than the engineering and does not depend on you, which is exactly why those requests go out in week one.

---

## 8. Answers recorded, and what's still open

**Settled (2026-08-31):**

| Question | Answer | Effect |
|---|---|---|
| Record count | Hundreds → low thousands | Solr risk eliminated; VM fallback dropped; scale-to-zero viable; static fallback becomes a real option |
| Google Workspace | None; OST is Microsoft | No SMTP in v1; admin-provisioned accounts; no Cloud Identity for the prototype |
| Successor / budget holder | None yet; would exist on adoption | Continuity model becomes rebuild-from-repo; no WIF, no secret rotation, no project transfer |
| Read access | Fully public | No read-path authorisation; caching safe; static export equivalent |
| OST GCP org | None | Standalone project, consumer Google accounts, OST billing account (ADR-0015) |
| IEA Wind GitHub | Task-level orgs only | Repo lives under OST; Task 43's org is the plausible adoption target |

**Still open:**

1. **Is there an OST GitHub organisation**, or would this sit under a personal account? Same bus-factor question as the Google accounts, same answer (two owners minimum).
2. **Who is the second GCP project owner?** One owner is not a design, it's a single point of failure with a job offer.
3. **Who authorises ~$25–85/month** and on what payment instrument? Small enough to be invisible, large enough to be cancelled by surprise.
4. **Is Task 43 approachable as a pilot**, and does anyone there already know you? A warm introduction is worth more than the rest of §5.
5. **What is your actual time box** before the new job absorbs you? Everything above is phaseable, but the phase boundaries should be chosen against a real date rather than discovered when you run out of time. If the honest answer is under three weeks, we should cut scope now — probably to Tier-1 harvesters plus a static export, with CKAN deferred.
