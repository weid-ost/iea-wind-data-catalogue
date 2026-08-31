---
type: adr
id: ADR-0030
title: LLM access — GitHub Models via GITHUB_TOKEN in CI; the author's own key for the one-off backfill
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0024-the-llm-boundary, adr-0025-the-extraction-cache-is-committed, adr-0031-the-harvest-never-fails-on-llm-unavailability, adr-0035-no-vendor-sdk, no-secrets-to-rotate, drain-the-pending-extraction-queue]
tags: [llm, accounts, cost]
---

# ADR-0030 — LLM access: GitHub Models in CI, own key for the backfill

## Status

**Accepted.** Reverses the turn-6 recommendation of a single prepaid provider.

## Context

Turn 6 asked for a free tier, preferring Anthropic. Verification found Anthropic
has no ongoing free API tier — new Console accounts get a small one-time trial
credit — and Haiku-class pricing put the workload at roughly $20 for the
backfill and about $0.40/month for the delta. The recommendation was a single
provider with prepaid credits.

Turn 7 reframed it, and the reframing is the decision:

> "it's not the credit I'm worried about; it's the admin of the actual account.
> If it's literally zero, that's infinitely easier than $0.40."

That is the binding constraint: **account admin, not cost.** An account with a
payment method has an owner, an expiry, a recovery path and a person who must
still exist. In an organisation where nobody is committed to maintaining this,
$0.40/month of that is worse than $0.00 of nothing. The target is therefore *no
additional account at all*.

## Decision

**GitHub Models in CI. The author's own key, once, locally, for the backfill.**

1. **In CI**: inference is free within rate limits, and the built-in
   `GITHUB_TOKEN` gains inference access simply by declaring
   `permissions: models: read` in the workflow. **No PAT, no API key, no repo
   secret, no vendor account, no card.** The endpoint
   (`https://models.github.ai/inference`) is OpenAI-compatible, so it sits
   behind the existing `extract()` interface unchanged
   ([[adr-0035-no-vendor-sdk]]). GitHub states inference runs on GitHub and
   Azure infrastructure and that data is not used for training.
2. **The free-tier limits are the catch**, and they are why this works for the
   delta but not the backfill. Published figures have been in the region of
   10 requests/minute and 50–150 requests/day depending on model tier, with
   per-request caps around 8k input / 4k output tokens. **Check the current
   numbers before relying on them**; they have moved.

| Workload | Volume | Fits the free tier? |
|---|---|---|
| CI delta | ~15 pages/week | **Comfortably.** Cleaned text of ~3k tokens sits well inside the 8k input cap |
| Local backfill | ~3,000 pages | **No.** At 50/day that is two months. Run it once on your own key (~$20 on a Haiku-class model) |

3. **Shrink the problem first: backfill locally.** The expensive pass is the
   first one. Run it on a laptop with a personal key and commit the resulting
   extraction cache ([[adr-0025-the-extraction-cache-is-committed]]). CI then
   only ever handles the delta. Locally the same function is pointed at whatever
   key the operator personally has via `$HARVEST_LLM_ENDPOINT`,
   `$HARVEST_LLM_TOKEN` and `$HARVEST_LLM_MODEL`.
4. **Accept the two model lineages and design the variance away.** Splitting the
   cache lineage was previously rejected to save five dollars a year; the trade
   is different now, because the split buys the elimination of an entire
   account. Mitigation is [[adr-0024-the-llm-boundary]]'s "extraction, not
   generation" rule plus schema-constrained output, with `model_id` already part
   of the cache key and recorded per field. Mixed lineage becomes an auditable
   footnote.
5. **If a paid key is ever wanted**: use **prepaid credits, never a card on
   auto-recharge**. A card expires, or belongs to someone who left, or quietly
   funds a runaway loop; prepaid credit sits there for years and caps the blast
   radius absolutely. At delta volumes $50 is roughly a decade. Register any such
   account against a **shared OST address or distribution list, never a personal
   mailbox**, and set a spend alert anyway.

**Rejected:** OpenRouter (adds a vendor to buy swappability the `extract()`
interface already provides) and self-hosting on the runner (Actions runners are
CPU-only; small CPU models are not reliable enough for schema-constrained
extraction).

## Consequences

**Good**

- **Zero accounts, zero secrets, zero billing attached to the project,
  permanently.** See [[no-secrets-to-rotate]].
- If the repository is public, the harvest workflow never needs a secret at all,
  which removes the fork-PR secret-exposure question rather than mitigating it.
- The provider is swappable behind one function, so a change of GitHub's terms
  is a fifteen-line change, not a redesign.

**Costs**

- Dependence on a free tier whose limits have moved before and could tighten
  further. The design degrades safely if they do — that is
  [[adr-0031-the-harvest-never-fails-on-llm-unavailability]], which is the
  stronger fallback and is built regardless.
- Two model lineages in one cache. Auditable, not invisible: `model_id` is in
  the cache key and in `extras.provenance`.
- The backfill depends on one individual's personal key once. Its output is
  committed, so the dependency ends the moment it is done.

**Open setup item** (`plans/02-static-plan.md` §9): verify current GitHub Models
free-tier rate limits before depending on them.

## Source

`plans/02-static-plan.md` §3.4, §8 (ADR-0030), §9; `harvest/extract.py`
docstring; `transcript/conversation-record.md` turns 5–7.
