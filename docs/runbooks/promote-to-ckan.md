---
type: runbook
id: RUN-promote-to-ckan
status: current
date: 2026-08-31
related: [adr-0021-canonical-record-is-a-ckan-package-dict, adr-0020-aggregation-only, adr-0027-withdrawn-records-are-retained, materialize-and-validate, record-format]
tags: [runbook, ckan, promotion, drill]
last_executed: never
---

# Runbook — promote to CKAN

**Goal:** stand up CKAN and load the catalogue into it, in about a day, without
changing a single record file.
**Governed by:** [[adr-0021-canonical-record-is-a-ckan-package-dict]].
**Source:** `plans/01-ckan-plan.md` §§3–4 — the CKAN plan is retained *solely*
as this promotion path. It is not the current architecture.

---

## 0. When this is the right move

Only when the thing that was missing appears: **a named owner and a named budget
holder, and institutions who will actually log in and maintain records.** Those
are separate people and both are required
(`plans/01-ckan-plan.md` §5.5). Without them, do not promote — leave the static
catalogue running at $0 and revisit.

Note that promotion **adds** a renderer; it does not replace one. The static site
and `records/*.json` remain. CKAN buys exactly one thing:
[[adr-0020-aggregation-only]]'s missing half — a multi-institution ownership and
permissions model — and it costs roughly $90/month plus a patch upgrade every
few months.

The claim this runbook makes true is the one that inverts the optics: *the
records are already in CKAN's exact format, and standing CKAN up is a day's
work whenever there is a reason to.*

## 1. Preconditions — check them before spending a day

```sh
make materialize
make validate
```

**`validate-ckan-compat: OK` is the go/no-go.** The gate checks precisely what
CKAN's API refuses ([[record-format]] §4.5). If it is green, `records/*.json`
POSTs unmodified.

Also confirm:

- [ ] every `groups[].name` exists in `groups.yaml`
- [ ] every `owner_org` exists in `organizations.yaml`
- [ ] `schema/ckan-scheming.json` and `harvest.materialize.EXTRA_KEYS` agree
      (a test enforces this — `make test`)
- [ ] a named owner and a named budget holder exist

## 2. Stand up the infrastructure (about half a day)

From `plans/01-ckan-plan.md` §3, in a **fresh, empty GCP project**:

1. **Spike 3 first, if it has not been done**: settle who owns the project, on
   which Google accounts, against which billing account. "Nobody at OST can own
   a billing account" is a stop condition, and it blocks everything else.
2. `terraform apply` — one root module, envs via tfvars. State in a GCS bucket
   **inside the same project**, so it moves with the project. Creates: project +
   APIs, Artifact Registry, Cloud SQL Postgres (automated backups on), GCS
   buckets, Secret Manager entries (**all Terraform-generated; no human ever
   reads a secret**), Cloud Run service + job, Cloud Scheduler, and a billing
   budget alert.
3. **Two deliberate prototype wrinkles**: turn Cloud SQL deletion protection
   **off** (it otherwise blocks the `terraform destroy` half of the rebuild
   test, and people work around that by clicking in the console), and skip PITR
   — daily automated backups are ample.
4. **The most important alert is the budget alert, not the uptime check.** An
   orphaned instance's realistic failure mode is a bill accruing against a card
   nobody watches. Alerts at 50/90/100%, routed to a **shared OST mailbox or
   distribution list, never an individual.**
5. Leave `enable_load_balancer = false`. The `*.run.app` URL is perfectly
   demonstrable, and requesting DNS from IEA Wind for something not yet adopted
   invites a governance conversation you do not need.

**Architecture, for orientation:** one Cloud Run service with `ckan`, `solr` and
`redis` **sidecars**, `max-instances = 1` (two instances means two divergent
Solr indexes — this is correctness, not economy), CPU always-allocated, Cloud
SQL over the built-in connector on a unix socket so **there is no VPC at all**.

## 3. Configure CKAN (about an hour)

- Extensions, minimum only: **`ckanext-scheming`** (required — it consumes
  `schema/ckan-scheming.json`) and **`ckanext-dcat`**. Every extension is an
  upgrade-blocker.
- `ckan.auth.create_user_via_web = false` — invite-only. This removes spam
  registration, the number one recurring admin task on a public CKAN.
- **DataStore and xloader disabled.** This is a catalogue of records and their
  locations; it does not host data files.
- Everything public; no private datasets, no `member` role.
- `robots.txt` disallowing `/dataset?`-style faceted URLs while allowing dataset
  pages.
- **No SMTP in v1.** A sysadmin creates accounts via `user_create` and
  communicates credentials out of band; hide the password-reset link so it does
  not fail silently in front of a user.

## 4. Load the records (about an hour)

Bootstrap the registers first — organisations and groups must exist before a
package can reference them:

```sh
# organizations.yaml -> organization_create
# groups.yaml        -> group_create
```

Then load every record **unmodified**:

```sh
for f in records/*.json; do
  curl -sS -X POST "$CKAN_URL/api/3/action/package_create" \
       -H "Authorization: $CKAN_API_TOKEN" \
       -H 'Content-Type: application/json' \
       --data-binary "@$f"
done
```

Re-running against an existing record uses `package_patch` keyed on `name`.
Because `name` is derived deterministically from the identity key, **every run
is an upsert rather than a duplicate-generator**.

Things that must work first time, and will if the gate was green:

- `extras` values are all strings; structured fields are JSON inside them.
- `license_id` is in CKAN's register.
- `tags` pass CKAN's character rules.
- Withdrawn records load with `state: "active"` and
  `extras.lifecycle_state: "withdrawn"` — CKAN's `deleted` means "hidden and
  purgeable", which [[adr-0027-withdrawn-records-are-retained]] forbids.

## 5. Verify

- Record count in CKAN == `ls records/*.json | wc -l`.
- Spot-check one record against its `records/*.json` field by field.
- `search-index rebuild` completes (expect well under 60 seconds at this scale).
- DCAT output is served by `ckanext-dcat`.
- A withdrawn record is present and visible, not hidden.

## 6. Afterwards

The static site does **not** get switched off. `events/` remains the source of
truth, `records/` remains derived, and CKAN becomes a second renderer of the
same files. The harvest continues to write records; a load step pushes them to
CKAN.

The one thing a rebuild cannot regenerate is human curation — which in this
architecture is already in `events/` and `annotations/` in the repository
(`plans/01-ckan-plan.md` §5.6 had to invent an export job for this; here it is
the design).

## 7. Drills to run while someone still knows how

From `plans/01-ckan-plan.md` §3 Phase 3. **An untested runbook is fiction; a
runbook whose author has left and which was never executed is worse.**

- **Drill 0 — rebuild from zero.** From a clean clone into a fresh empty
  project: `terraform apply`, bootstrap, load, then diff the resulting catalogue
  against the original. **This is the acceptance test for the whole promotion.**
  Anything requiring a manual step gets fixed in code, not written up as an
  instruction.
- Tear down and rebuild (`terraform destroy` then `apply`).
- Restore Cloud SQL from an automated backup.
- Perform a CKAN patch upgrade (2.11.x → 2.11.y) end to end, including reindex.
- Roll back a Cloud Run revision.
- Add and remove a sysadmin.

## 8. What you are signing up for

State it explicitly in the handoff agreement rather than letting it be
discovered: **a patch upgrade roughly every few months**, because only the
latest patch of the current and previous minor CKAN versions receives security
fixes. Plus ≈$90/month, Postgres, Solr and Redis.

---

**Last executed:** never — promotion is a future decision, not a pending task.
§1 was re-verified on 2026-09-01 against the first coherent harvest: `make
materialize` and `make validate` both green over 30 real records, so what CKAN
would receive on promotion day now exists and passes the gate.
