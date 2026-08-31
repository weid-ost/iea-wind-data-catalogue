---
type: runbook
id: RUN-no-secrets-to-rotate
status: current
date: 2026-08-31
related: [adr-0022-hosting-and-automation, adr-0030-llm-access-via-github-models, adr-0034-toolchain-pinning-and-no-auto-updates, re-enable-a-dormant-cron]
tags: [runbook, security, continuity, annual]
last_executed: 2026-08-31
---

# Runbook — no secrets to rotate

**Goal:** confirm, annually, that the catalogue still has zero credentials — and
know what to do if that ever stops being true.
**Governed by:** [[adr-0022-hosting-and-automation]],
[[adr-0030-llm-access-via-github-models]].

---

## 0. The claim

**This project has no secrets, no API keys, no service accounts, no billing
account and nothing to rotate.** That is a design property, not an oversight,
and it is the single most important continuity feature the catalogue has.

Why it matters more than the money: a system with no billing account cannot be
switched off by an unpaid invoice or an expired card, and a system with no
credentials cannot be locked out when the person who created them leaves. For a
project with no budget holder, sitting dormant inside a poorly-administered
federation, that is the property that determines whether it exists in three
years.

## 1. Why there is nothing

| Thing that normally needs a credential | Here |
|---|---|
| compute | GitHub Actions, free on a public repo, built-in `GITHUB_TOKEN` |
| hosting + TLS + custom domain | GitHub Pages, included |
| database | there is none |
| object storage | there is none — `records/` and `cache/` are in the repo |
| LLM inference in CI | GitHub Models via the built-in `GITHUB_TOKEN` with `permissions: models: read` — **no PAT, no API key, no repo secret, no vendor account, no card** |
| GitHub API rate limit for the harvest | the same built-in `GITHUB_TOKEN` (5,000/hr rather than 60/hr) |
| LLM inference for a local backfill or queue drain | **the operator's own key**, in their own shell, never in the repo |
| monitoring | optional, and a free ping URL is not a credential worth protecting |

The only place a key ever exists is an individual's shell during a manual
extraction run: `$HARVEST_LLM_TOKEN`. It is never committed, never a repository
secret, and its output — the extraction cache — is committed instead, so the
dependency ends the moment the run does.

## 2. The annual check

Do this once a year, or whenever ownership changes. It takes ten minutes.

### 2.1 Confirm there are still no secrets

GitHub → repository → **Settings → Secrets and variables → Actions**.

**Expected: no repository secrets, no environment secrets.**

If something is there, find out what added it and whether it is genuinely
required. Adding a secret is a real architectural change — write it down as an
ADR, not as a commit — and it re-introduces a rotation obligation and an owner
who must still exist.

```sh
gh secret list --repo <owner>/iea-wind-data-catalogue     # expect: no output
```

### 2.2 Confirm workflow permissions are still scoped

Grep the workflows:

```sh
grep -n "permissions:" -A4 .github/workflows/*.yml
grep -n "ubuntu-latest\|lts/\*" .github/workflows/*.yml   # expect: no matches
```

Expected shape:

- harvest job: `contents: write` (to commit the heartbeat), plus `models: read`
  where Tier-3 extraction runs;
- deploy job: `pages: write`, `id-token: write`;
- **nothing runs on `pull_request`.**

### 2.3 Confirm ownership has not become a single point of failure

- The repository has **at least two owners/admins**. One owner is not a design;
  it is a single point of failure with a job offer. This is an open setup item
  in `plans/02-static-plan.md` §9.
- Any notification address is a **shared OST address or distribution list, never
  a personal mailbox** — same bus-factor reasoning.
- Note: OST is the **Ostschweizer Fachhochschule** (Eastern Switzerland
  University of Applied Sciences), the author's organisation and the
  repository's initial owner.

### 2.4 Confirm the free tiers still hold

- **GitHub Models rate limits.** Published figures have been around 10
  requests/minute and 50–150 requests/day, with ~8k input / 4k output caps.
  **Verify the current numbers**; they have moved. If they tighten, nothing
  breaks — Tier-3 misses queue and someone drains them later
  ([[drain-the-pending-extraction-queue]]).
- **Actions minutes and Pages** remain free on public repositories. If the
  repository were ever made private, that changes; the repository is public
  deliberately.

### 2.5 Confirm the catalogue is actually running

```sh
uv run python -m harvest report | python3 -m json.tool | grep finished_at
```

Stale by more than 45 days means the site is already showing its warning banner.
Go to [[re-enable-a-dormant-cron]].

### 2.6 Confirm it still builds

```sh
make sync
make test
make validate
```

This is the real annual risk, not credentials: toolchain rot. Pinning
([[adr-0034-toolchain-pinning-and-no-auto-updates]]) is the mitigation, and this
is the check that it worked.

## 3. If a credential ever becomes necessary

Preferences, in order:

1. **Do not.** Check whether the built-in `GITHUB_TOKEN` covers it first.
2. If an LLM key is genuinely wanted, use **prepaid credits, never a card on
   auto-recharge.** A card expires, or belongs to someone who left, or quietly
   funds a runaway loop; prepaid credit sits there for years and caps the blast
   radius absolutely. At delta volumes $50 is roughly a decade.
3. Register the account against a **shared OST address or distribution list**,
   never a personal mailbox. Set a spend alert anyway.
4. Store it as a **repository-level Actions secret**, and because the repository
   is public: **never run the harvest workflow on `pull_request`** (secrets are
   not exposed to fork-PR runs, but do not rely on that as the only defence),
   and put the workflow behind an environment whose deployment branch is
   restricted to `main`.
5. Record the rotation obligation *here*, with an owner and a date. The moment
   this file stops being called "no secrets to rotate", the continuity property
   above is gone and someone should know it.

## 4. If the catalogue moves to another owner

There is nothing to transfer but the repository. No secrets, no state, no
projects to move between billing accounts, no Workload Identity Federation trust
relationships to repoint. Fork or transfer the repo, enable Actions and Pages,
and it runs. That is the entire handover.

---

**Last executed:** 2026-08-31 — §2.6 verified (`make sync`, `make test` green,
`make validate` OK). §§2.1–2.2 not yet applicable: `.github/workflows/` does not
exist in this checkout, and there are no secrets because there is no workflow.
