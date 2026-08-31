---
type: adr
id: ADR-0022
title: Hosting and automation — GitHub Actions + GitHub Pages
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
supersedes: [ADR-0001, ADR-0002, ADR-0003, ADR-0004, ADR-0015]
related: [adr-0029-scheduling-and-the-heartbeat-commit, adr-0030-llm-access-via-github-models, adr-0034-toolchain-pinning-and-no-auto-updates, no-secrets-to-rotate]
tags: [infrastructure, hosting, cost]
---

# ADR-0022 — Hosting and automation: GitHub Actions + GitHub Pages

## Status

**Accepted.** Option A of `plans/02-static-plan.md` §3.1. Supersedes ADR-0001
(platform), ADR-0002 (Terraform + GCS state), ADR-0003 (Cloud Run compute) and
ADR-0015 (GCP identity and project ownership) from the CKAN plan. The Firebase
variant (Option B) is documented and **not** chosen.

## Context

Once Postgres, Redis and Cloud Run are gone — which
[[adr-0020-aggregation-only]] removes the need for — what remains is: run a
script on a schedule, commit the output, build a site, serve some files. That is
entirely within GitHub's free tier.

The author's own framing from turn 1 was that fewer services is better:

> "Simplicity is better; especially the fewer the number of different services
> involved the better … Fewer is better."

and from turn 5:

> "We'll definitely go with option A rather than B, for the static architecture."

The GCP identity problem documented at length in `plans/01-ckan-plan.md` §3.1 —
OST is a Microsoft shop with no Google Workspace, no Cloud Identity and no GCP
Organization, so the prototype would run on consumer Google accounts that OST
IT could not administer, recover or offboard — evaporates entirely if there is
no GCP.

## Decision

**GitHub only.**

```
sources.yaml ──┐
annotations  ──┤
               ▼
      GitHub Actions (cron: weekly + workflow_dispatch)
               │  harvest → extract (cached) → reconcile → validate
               ▼
      records/*.json  +  cache/*.json   ──commit──► repo
               │
               ▼
      build (Astro + Pagefind index) ──► GitHub Pages ──► HTTPS, custom domain
```

1. **Compute is GitHub Actions.** Free on public repositories.
2. **Hosting is GitHub Pages.** HTTPS and custom domains included.
3. **No GCP, no Terraform, no state bucket, no IAM, no billing account, no
   Google identity.** The IaC preference the author is fluent in has almost
   nothing left to manage, and manufacturing infrastructure to justify managing
   it is the wrong trade.
4. **The repository is public** (decisions log, turn 6): free unlimited Actions
   minutes, it matches the open-data premise, and extractions and corrections
   are visible, which is what makes an AI-assisted catalogue trustworthy.
5. **Repository ownership is OST** — the Ostschweizer Fachhochschule (Eastern
   Switzerland University of Applied Sciences) — in the first instance.
6. **One artifact.** The repo *is* the system: sources, annotations, events,
   records, cache, site, docs.
7. Workflow permissions stay scoped: `contents: write` only where the harvest
   commits, `pages: write` and `id-token: write` only on deploy.

## Consequences

**Good**

- **Cost is $0**, and the strategic point is not the money: a system with no
  billing account cannot be switched off by an unpaid invoice or an expired
  card. For a project with no budget holder inside a poorly-administered
  federation, that is the property that determines whether it exists in three
  years. Compare ≈$90/month for the CKAN route.
- Nothing to administer means nothing to hand over. See
  [[no-secrets-to-rotate]].
- The usual lock-in objection does not apply: the output is a directory of JSON
  and static HTML, and rehosting anywhere is an afternoon. The escape hatch
  *is* the architecture.

**Costs**

- Everything now depends on one vendor's free tier — Actions minutes, Pages,
  and (separately) GitHub Models. That is mitigated by the output being
  portable, and by [[adr-0031-the-harvest-never-fails-on-llm-unavailability]]
  making the inference tier optional rather than load-bearing.
- The scheduled-workflow dormancy rule is a real operational hazard and needs
  its own decision: [[adr-0029-scheduling-and-the-heartbeat-commit]].
- The build runs with a write-capable token, so a compromised *build-time*
  dependency is the residual supply-chain risk. Mitigation is minimal
  dependencies and lockfile installs
  ([[adr-0034-toolchain-pinning-and-no-auto-updates]]).

**Rejected: Option B, the GCP variant.** Cloud Run Job + Cloud Scheduler +
Firebase Hosting, roughly $1–3/month. Documented in `plans/02-static-plan.md`
§3.1 in case OST policy ever requires it. Note that raw GCS website hosting is
*not* the fallback: it needs a ~$18/month load balancer to get HTTPS.

## Source

`plans/02-static-plan.md` §3.1, §6, §8 (ADR-0022), §9;
`plans/01-ckan-plan.md` §3.1 (the identity problem this removes);
`transcript/conversation-record.md` turns 3, 5, 6.
