---
type: architecture
id: ckan-promotion-path
status: retained
date: 2026-09-03
related: [index, motivation, decision-history, promote-to-ckan, adr-0020-aggregation-only, adr-0021-canonical-record-is-a-ckan-package-dict, adr-0022-hosting-and-automation]
tags: [ckan, promotion, gcp, historical]
---

# The CKAN promotion path

**Status: retained, not current.** This is not the architecture. It is the
architecture that was planned first, superseded by
[[adr-0020-aggregation-only]], and kept for exactly one reason: it is what
promotion to CKAN would actually cost, so
[[adr-0021-canonical-record-is-a-ckan-package-dict]] is a checkable promise
rather than a hopeful one.

Read [[motivation]] for why it was superseded and [[decision-history]] for when.
The **procedure** lives in [[promote-to-ckan]]; this page is the design that
procedure implements, and the reasoning a future promoter would otherwise have
to redo.

Promotion **adds** a renderer; it does not replace one. The static site and
`data/records/*.json` remain. CKAN buys exactly one thing: the missing half of
[[adr-0020-aggregation-only]] — a multi-institution ownership and permissions
model — for roughly $90/month plus a patch upgrade every few months.

---

## 1. When this is the right move

Only when the thing that was missing appears: **a named owner and a named budget
holder, and institutions who will actually log in and maintain records.** Those
are separate people and both are required. Without them, do not promote.

The whole case for CKAN rests on that permissions model. At hundreds to low
thousands of public records, nothing about CKAN's *scale* capabilities is
load-bearing — client-side search over 2,000 records is instant. If it turns out
that one organisation curates everything centrally and institutions never log
in, CKAN is carrying a Solr instance, a Postgres instance and a quarterly patch
obligation in order to provide a login screen nobody uses.

**The honest cost, stated so it is not discovered later.** CKAN requires three
backing services — PostgreSQL, Solr and Redis — and that is not optional. It is
a 2007-era Pylons application carried forward onto Flask. The upgrade path
between minor versions periodically requires a Solr schema change and a reindex,
and only the latest patch of the current and previous minor versions receives
security fixes. Promotion signs whoever inherits it up for **a patch upgrade
roughly every few months.** That belongs in the adoption agreement, not in a
surprise.

What is bought in exchange is real: organisations, roles, dataset ownership, an
invite flow, a write API, a stable public API and DCAT output. Building that on
top of a static site would mean building an admin application, which is a far
worse thing to hand over.

---

## 2. The organising principle that survived

One idea from the CKAN plan outlived it and became the whole static
architecture: **the repository is the product.**

The handover model is not "here are the keys to a running system" but "here is an
automated process — run it again in your own project." Three consequences were
enforced ruthlessly then and are enforced ruthlessly now:

1. **Any manual click is a defect.** Bootstrapping is code in the repository, not
   steps someone once performed in an admin UI. If it exists only in a running
   instance's database, it does not exist.
2. **Secrets are generated, never authored.** Terraform would create them
   (`random_password` → Secret Manager) and no human ever reads them. This is
   what makes "no need to rotate on handover" true rather than hoped for. In the
   static architecture it went further: there are no secrets at all
   ([[no-secrets-to-rotate]]).
3. **The acceptance test is a rebuild, not a backup restore.** Stand up a second
   instance in a clean project from a clean clone and diff the result.

The CKAN plan had **exactly one class of state a rebuild could not reproduce:
human curation edits**, and it had to invent a nightly export job to commit them
back to the repository. In the static architecture that problem does not exist —
curation *is* `data/events/` and `data/annotations/` in the repository, replayed
by `make materialize`. That is the single clearest illustration of what the
inversion bought.

---

## 3. What would be stood up

```
                    ┌─────────────────────────────────────┐
  users ──HTTPS──►  │  LB + Cloud Armor  (adoption only)  │  skip while evaluating;
                    └──────────────────┬──────────────────┘  use the *.run.app URL
                                       │
                    ┌──────────────────▼──────────────────┐
                    │  Cloud Run service  (min=1, max=1)  │
                    │  ┌───────────┐ ┌──────┐ ┌────────┐  │
                    │  │ ckan      │ │ solr │ │ redis  │  │  3 containers, 1 instance
                    │  │ (ingress) │ │ side │ │ side   │  │
                    │  └─────┬─────┘ └──────┘ └────────┘  │
                    └────────┼─────────────┬──────────────┘
                   Cloud SQL │ (unix        │ GCS FUSE volume
                   Auth      │  socket)     ▼
                   ┌─────────▼──────┐   ┌──────────────────┐
                   │ Cloud SQL      │   │ GCS bucket       │
                   │ PostgreSQL     │   │ uploads/ exports/│
                   └────────────────┘   │ tfstate/         │
                                        └──────────────────┘
```

**Why this shape**, with the reasoning that is easy to get wrong:

- **One Cloud Run service with sidecars** keeps Solr and Redis inside the same
  deployable unit, with container start-order dependencies. One `terraform
  apply`, one revision, one rollback. Both sidecars are ephemeral: Solr's index
  rebuilds from Postgres and Redis holds only sessions and a barely-used job
  queue, so losing them on restart costs a reindex and a re-login.
- **Memorystore is deliberately avoided.** It requires a VPC and Direct VPC
  egress — more resources, more IAM, ~$40/mo, nothing gained for a catalogue
  with a handful of concurrent editors.
- **Cloud SQL is reached through Cloud Run's built-in connector over a unix
  socket**, so there is **no VPC at all**. That is a large simplification for
  whoever inherits it: there is no networking to understand.
- **`max-instances = 1` is required, not an economy.** Each instance carries its
  own Solr index; two instances means two divergent indexes. Concurrency of 80
  on one instance is far more than this catalogue will ever need.
- **CPU must be always-allocated** whenever an instance exists, because Solr's
  JVM needs CPU outside request handling.
- **The edge (LB + Cloud Armor) is deferred**, behind an `enable_load_balancer`
  variable. With `max-instances = 1` a crawler storm makes the site slow rather
  than expensive, and a `robots.txt` disallowing faceted URLs does most of the
  work for free.
- **Right-sizing: 1 vCPU / 4 GiB.** Cloud Run's local filesystem is in-memory
  and counts against the limit, so the Solr index lives inside that 4 GiB.

**Solr, resolved by scale.** A full `ckan search-index rebuild` at 2,000
datasets takes tens of seconds, comfortably inside Cloud Run's 240-second
startup probe budget, and the index is tens of megabytes. Two useful
consequences: reindexing becomes **routine** — run it after every harvest and
nightly, which deletes the entire class of "the index has drifted from the
database" bugs; and **scale-to-zero becomes viable**, at a 60–90 second cold
start. Too slow for a random visitor, fine for an instance that exists to be
demonstrated. Set `min-instances = 0` while dormant and flip to `1` the week of
a demo — and record that as a deliberate lever, or somebody will "fix" the slow
first page load by rearchitecting something.

| Mode | Configuration | ~Monthly |
|---|---|---|
| Dormant | `min=0`, no LB, `*.run.app` | **$20–35** |
| Under active evaluation | `min=1`, no LB | **$65–85** |
| Adopted / production | `min=1`, LB + Armor + custom domain | **$90–115** |

Cloud SQL smallest shared-core with backups (~$15–30) is the floor in every
mode. Compare [[motivation]] §5.

---

## 4. Identity and ownership — the blocking question

**This is the trickiest non-technical decision and it blocks everything else.**
Settle it before writing any Terraform.

A GCP **Organization** resource requires Google Workspace or **Cloud Identity
Free** (up to 50 users, no cost) with a verified domain. OST has neither, is a
Microsoft shop, and getting a DNS TXT record onto the corporate domain means a
conversation with IT that may be slow.

**For a prototype: a standalone project with no Organization.** Owners are two or
three Google accounts created against OST email addresses (a Google account can
be created on any email address without Workspace); billing is an OST-controlled
Cloud Billing account, which is the piece that matters for continuity because
projects move between billing accounts trivially.

**The downside, stated plainly:** those are consumer Google accounts. OST IT
cannot administer, recover or offboard them. If the author leaves, their account
goes with them. Mitigations are two owners from day one, and the fact that
everything is reproducible from the repository — so losing access costs a
rebuild, not the project.

**On adoption:** create Cloud Identity Free on the owning organisation's domain
and build inside that Organization from the start. Since the adoption path is
"rebuild from the repository" anyway, the retrofit cost is already paid. Do
**not** stand up Cloud Identity for a prototype; it is the wrong moment to spend
that political capital.

**"Nobody can own a billing account" is a stop condition**, not a risk to
mitigate.

---

## 5. Tooling decisions worth not relitigating

- **Terraform CLI + GCS backend, not HCP, not Infrastructure Manager.** Remote
  state goes in a bucket *inside the same project*, with versioning and object
  lock, so state moves with the project and there is no second account to keep
  in sync. Infrastructure Manager executes Terraform under the hood — it
  replaces where state lives, not the tool you learn, and it adds an API
  surface.
- **No CI/CD for a prototype.** Workload Identity Federation exists to let a
  machine in GitHub act as a principal in GCP; with one engineer and a
  disposable instance it buys nothing and costs a trust configuration a future
  rebuilder must understand and recreate. Deployment is `make deploy` →
  `gcloud builds submit` (which uploads the local source directory, so no
  GitHub↔GCP integration exists at all) → `terraform apply`.
- **Two deliberate wrinkles for a disposable instance:** turn Cloud SQL deletion
  protection **off** in the prototype tfvars, or it blocks the `terraform
  destroy` half of the rebuild test and people work around it by clicking in the
  console — which breaks rule 1; and skip PITR, because automated daily backups
  are ample when the only irreplaceable state is curation, which is already in
  the repository.
- **The most important alert is the budget alert, not the uptime check.** An
  orphaned prototype's realistic failure mode is not downtime, it is a bill
  quietly accruing against a card nobody watches. Alerts at 50/90/100% routed to
  a **shared mailbox or distribution list**, never an individual — the whole
  premise is that individuals move on.
- **Do not chase a custom domain early.** Requesting DNS changes from IEA Wind
  for something unadopted invites a governance conversation. Nothing depends on
  the hostname except `ckan.site_url`, which is one variable.
- **Extensions, kept to the minimum:** `ckanext-scheming` (required — it is what
  `schema/ckan-scheming.json` is for) and `ckanext-dcat`. Defer
  `ckanext-spatial`; bounding boxes go in extras. Every extension is an
  upgrade-blocker.
- **`ckan.auth.create_user_via_web = false`** — invite-only. This one setting
  removes spam registration, otherwise the number one recurring admin task on a
  public CKAN.
- **No SMTP.** With invite-only registration and public-read content, CKAN's only
  uses for email are invitations and password resets; at this user count a
  sysadmin creates the account and communicates the credential out of band. That
  is a runbook, not a service. Disable or hide the password-reset link so it does
  not fail silently in front of a user.
- **Everything public. DataStore and xloader disabled.** This is a catalogue of
  records and their locations; it does not host data files. That removes the
  single largest source of CKAN operational pain and most of the storage cost.
- **No custom theme beyond a logo, colours and a homepage snippet.** Themes are
  the second largest source of CKAN upgrade pain.
- **Standalone harvesters, not `ckanext-harvest`** — the most important technical
  decision in the original plan, and the one that survived intact into
  `harvest/adapters/`. `ckanext-harvest` introduces long-running gather/fetch
  worker daemons, a Redis-backed job queue you must keep healthy, harvest source
  objects that live in the database as hidden state, and failure modes debugged
  through the CKAN admin UI. It would make Redis load-bearing (it is not) and add
  a second always-on process to a design with exactly one.

---

## 6. The taxonomy — this part is already live

**Get this right; it is expensive to change.** CKAN's two grouping constructs are
not interchangeable: **Organizations** own datasets and carry permissions (one
`owner_org` per dataset); **Groups** are thematic, many-to-many, and carry no
ownership.

Therefore **Organizations = institutions** (DTU, NREL, PNNL, Fraunhofer IWES,
ATU Sligo…), because ownership and maintenance responsibility live with
institutions and their staff; and **Groups = IEA Wind Tasks**, because one
dataset legitimately belongs to Task 43 *and* Task 49, and because tasks start,
end and renumber (Task 19 → 54, Task 34 → 59) in ways that would be destructive
if they controlled permissions.

**This decision is already implemented**, which is most of why promotion is a
day's work: `organizations.yaml` and `groups.yaml` are CKAN-shaped registers,
`groups.yaml` carries the renumbering aliases, and the CKAN-compat gate refuses
any record whose `owner_org` or `groups` do not resolve against them. See
[[record-format]] and [[adr-0021-canonical-record-is-a-ckan-package-dict]].

At seeding, ownership is mostly unknown: everything lands in a holding
organisation (`ost-curated`) and is tagged into the right Task group, so nothing
is blocked on knowing who owns what.

---

## 7. Onboarding, if institutions ever do log in

This is the half of the product that the static architecture does not have. It
is a design plus a scripted runbook plus **one** pilot — not an operating
process. With no successor in place, do not recruit institutions into a system
that might be switched off.

The obvious pilot is **IEA Wind Task 43 (Wind Energy Digitalization)**:
cataloguing, data standards, FAIR principles and open tooling are literally
their remit, they already run a GitHub org and the `iea_wind_task_43` Zenodo
community, and they are the most plausible owner if this is ever adopted. Pilot
and adoption target are the same people.

**The claim workflow, deliberately boring:**

1. **Request** — an institution expresses interest via a GitHub issue form in the
   repository. No new service, free, auditable, and the queue is visible to
   whoever inherits the project. A `mailto:` to a shared address is an acceptable
   alternative; a bespoke web form is not.
2. **Verify** — the requester has an institutional email address and is confirmed
   as a Task participant by the relevant Operating Agent. A human check, and it
   should stay one.
3. **Provision** — a scripted runbook creates the CKAN organisation, invites the
   named steward as org admin, and bulk-transfers matching datasets out of
   `ost-curated` via `package_patch owner_org=…`.
4. **Agree** — the steward acknowledges a one-page **Steward Agreement**: keep
   your records' metadata current, respond to link-rot reports within 30 days,
   licence metadata as CC0 or CC-BY. One page. If it needs a lawyer it will never
   be signed by anyone at the IEA.
5. **Record** — the organisation is marked claimed, with steward name and date.

**Roles**, with one genuine constraint:

| Role | Who | Count |
|---|---|---|
| Sysadmin | OST + one named IEA Wind contact | Exactly 2, never more |
| Org admin | Institutional steward | 1–2 per org |
| Editor | Institutional staff | as needed |
| Member | read-only for private records | rare |

Two sysadmins is deliberate: one is a bus factor of one, three is nobody's
responsibility.

---

## 8. Adoption is a checklist against the repository

Not a handover. Since the model is "rebuild in your own project":

- Named owner **and** named budget holder identified — separate people, both
  required. Without these, do not proceed; leave the static catalogue running at
  $0 and revisit.
- Receiving organisation creates Cloud Identity Free on their domain, giving a
  real GCP Organization (§4).
- Repository forked or transferred to the receiving organisation's GitHub.
- `terraform apply` into their new project, run **by them, with you watching**.
  This is the whole handover. **If it does not work first time in their hands,
  the prototype was not finished.**
- Harvest re-run from scratch; the catalogue diffed against the static site's
  records.
- Custom domain, load balancer and Cloud Armor enabled; CI added if there is more
  than one contributor.
- A dated review point recorded: *"if no organisation has claimed records in 12
  months, here is the decision to make."*

Nothing here involves secret rotation, WIF, or moving projects between
organisations. That is the point — and in the static architecture the curation
migration step, which was the one irreducible piece of work, disappears
entirely.

---

## 9. Drills, to run while somebody still knows how

**An untested runbook is fiction. A runbook whose author has left and which was
never executed is worse.**

**Drill 0 — rebuild from zero. This is the acceptance test for the whole
promotion.** From a clean clone into a fresh, empty project: `terraform apply`,
bootstrap, load, then diff the resulting catalogue against the original.
Anything requiring a manual step gets fixed in code, not written up as an
instruction. Everything else is secondary.

Then: tear down and rebuild (`terraform destroy` then `apply`); restore Cloud SQL
from an automated backup; perform a patch upgrade (2.11.x → 2.11.y) end to end
including the reindex; roll back a Cloud Run revision; add and remove a sysadmin.

The executable procedure is [[promote-to-ckan]].

---

## 10. Spikes that would still need doing

Each can invalidate something above, and finding out early is cheap.

| Spike | Kill criterion |
|---|---|
| Build the image, load ~3k synthetic datasets locally, time a full `search-index rebuild` | Confirmation only — expect well under 60s. Record the number. |
| Run the 3-container stack on Cloud Run in a scratch project; confirm sidecar start-order, the GCS FUSE mount for `ckan.storage_path`, and the Cloud SQL socket | FUSE unusable for uploads ⇒ `ckanext-s3filestore` against GCS S3-compat, or accept no uploads |
| **Settle identity and billing ownership (§4)** | Nobody can own a billing account ⇒ stop. This blocks everything else. |

The two source-side spikes from the original plan are **done**: the Wind Data
Hub's listing surface was probed and found to sit behind an authentication wall
(handled by disabling the source rather than guessing — fixture `wdh-07`,
[[harvest-anomalies]]), and the Zenodo communities were enumerated into
`sources.yaml`.

---

## 11. Superseded decisions

The CKAN plan carried its own ADR register, numbered 0001–0019. Those numbers
are **not reused**; the current register starts at 0020. Explicitly superseded:

| Old | Was | Superseded by |
|---|---|---|
| 0001 | catalogue platform = CKAN | [[adr-0020-aggregation-only]] |
| 0002, 0004 | Terraform + GCS state; local `make deploy`, no CI | [[adr-0022-hosting-and-automation]] — GitHub Actions only; IaC largely unnecessary |
| 0003 | Cloud Run multi-container | [[adr-0022-hosting-and-automation]] — no compute |
| 0007 | ephemeral Solr, rebuilt nightly | [[adr-0023-search-via-pagefind]] — no Solr; index built at build time |
| 0009, 0010 | orgs/groups taxonomy; invite-only registration | [[adr-0020-aggregation-only]] — no accounts, no registration. **The taxonomy itself survives** as `organizations.yaml` / `groups.yaml` (§6) |
| 0015 | GCP identity and project ownership | [[adr-0022-hosting-and-automation]] — there is no GCP identity problem |

The rest (datastore posture, harvest architecture, extension allowlist, email
transport, edge, backups, continuity, curation persistence, instance posture) are
either moot without CKAN or captured above — with two that outlived their ADR
and became load-bearing here: **links-only, no hosted data files**, and
**standalone harvesters** (§5).
