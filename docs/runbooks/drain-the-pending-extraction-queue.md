---
type: runbook
id: RUN-drain-the-pending-extraction-queue
status: current
date: 2026-08-31
related: [adr-0031-the-harvest-never-fails-on-llm-unavailability, adr-0025-the-extraction-cache-is-committed, adr-0030-llm-access-via-github-models, adr-0024-the-llm-boundary, adr-0035-no-vendor-sdk, correct-a-record]
tags: [runbook, llm, tier-3]
last_executed: never
---

# Runbook — drain the pending-extraction queue

**Goal:** turn queued Tier-3 pages into committed cache entries, on your own
machine, with whatever key you personally have.
**Governed by:** [[adr-0031-the-harvest-never-fails-on-llm-unavailability]],
[[adr-0025-the-extraction-cache-is-committed]],
[[adr-0030-llm-access-via-github-models]].

---

## 0. Why this is a human-operated job

The delta is roughly fifteen pages a week. **That does not need to be
automated.** CI runs Tier 1 deterministically, queues every Tier-3 cache miss
into `state/pending-extraction.json`, and stops. Whenever someone cares —
monthly, quarterly, never — they run this and commit the resulting cache.

The LLM is therefore **a tool, not a system dependency**. Zero accounts attached
to the project, permanently, under any provider's terms. Tier 1 keeps the
catalogue current and automatic throughout; only new microsite records wait for
someone to bother.

**An unqueued backlog is not an incident.** It is a number on the homepage.

## 1. Check the backlog

```sh
uv run python -m harvest report | python3 -m json.tool | grep -E 'pending_extraction|"hits"|"misses"|hit_rate'
```

`pending_extraction` is the queue length. The queue itself is
`state/pending-extraction.json`: a list of `{url, cache_key, reason, queued_at}`,
deduped on `cache_key`.

A sudden jump of hundreds means either a task site redesigned (invalidating its
content hashes) or `PROMPT_VERSION` was bumped, which invalidates the whole
cache deliberately. Both drain over weeks rather than in one pass, because
`harvest.extract.MAX_EXTRACTIONS = 200` caps calls per run.

## 2. Point the extractor at a key

No key is stored in the repository and none should be. Locally you supply your
own, per shell:

```sh
export HARVEST_LLM_ENDPOINT="https://api.example.com/v1"   # OpenAI-compatible
export HARVEST_LLM_TOKEN="sk-…"                            # your own key
export HARVEST_LLM_MODEL="…"                               # e.g. a Haiku-class model
```

Defaults, if you set nothing, are GitHub Models
(`https://models.github.ai/inference`) and `openai/gpt-4o-mini`, which is what
CI uses via the built-in `GITHUB_TOKEN` with `permissions: models: read`. The
free tier fits the weekly delta comfortably and does **not** fit a full backfill
— see §5.

Do not add a vendor SDK. Inference is one `httpx` POST to
`{endpoint}/chat/completions` ([[adr-0035-no-vendor-sdk]]).

## 3. Drain

```sh
make extract                                 # = uv run python -m harvest extract
uv run python -m harvest extract --limit 25  # a smaller bite
```

Expected output:

```
extract: resolved N pending extraction(s)
```

> **`SPEC — not yet implemented`.** Today this exits **2** with
> `extract: harvest.extract.drain_pending — owner: Track H (extraction)`.
> `read_cache`, `write_cache`, `queue_pending`, `read_pending`, `extract` and
> `drain_pending` are stubs. `cache_key` is implemented and must not be changed:
> the Tier-3 source key derives from the same normalised content, and the
> adapters and the reconciler have to agree on it.
>
> **Requirements on the extraction track**, all checkable:
> - `uv run python -m harvest extract` exits **0** and prints
>   `extract: resolved N pending extraction(s)`.
> - `extract()` returns `None` — never raises — on no token, rate limit,
>   outage or schema-validation failure (fixture `x-07`).
> - a cache miss with no model appends `{url, cache_key, reason, queued_at}` to
>   `state/pending-extraction.json`, deduped on `cache_key`, and the run
>   succeeds.
> - cache entries are written to `cache/<key>.json` as byte-stable JSON, where
>   `key == sha256(content + prompt_version + model_id)`.
> - `state/last-run.json` carries real `cache.hits`, `cache.misses` and
>   `pending_extraction` values.
> - no more than `MAX_EXTRACTIONS` model calls per invocation.
> - content passed to `extract()` is `trafilatura` main content run through
>   `harvest.sanitize.html_to_text` — **never raw HTML**.
> - identifiers are supplied as `context` from `harvest.doi.extract_dois` and
>   every one is put through `harvest.doi.resolve_or_drop`; unresolvable ⇒ the
>   record is dropped and appears in `dropped_dois`.
> - every model-produced field carries
>   `FieldProvenance(extraction_method="llm", model=…, prompt_version=…,
>   confidence=…)`.

## 4. Commit the cache

```sh
git status --short cache state
git add cache state records events
git commit -m "extract: drain N pending Tier-3 extractions"
```

**The cache is committed on purpose.** A rebuild replays it rather than
re-inferring, which is the only reason "rebuild from the repo" and "AI
harvester" are not in direct conflict, and it is what makes weekly runs nearly
free. In a public repository it is also what makes the extraction auditable.

## 5. The one-off backfill

The expensive pass is the first one — a few thousand pages seen for the first
time. **Run it on your laptop with your own key**, not in CI:

```sh
export HARVEST_LLM_ENDPOINT=… HARVEST_LLM_TOKEN=… HARVEST_LLM_MODEL=…
uv run python -m harvest extract --limit 3000
git add cache && git commit -m "extract: initial Tier-3 backfill"
```

Budget roughly **$20 once** on a Haiku-class model. GitHub Models' free tier —
in the region of 10 requests/minute and 50–150 requests/day — would take about
two months for the same work, which is why the backfill is local and the delta
is CI.

After that, CI only ever handles a handful of pages a week.

## 6. Pinning a wrong extraction

If the model got something wrong, do not re-run it and hope. Pin the correction:
[[correct-a-record]] §3.6. The corrected object replaces the cache entry, is
marked `pinned`, and records `pin_source_key` — the content hash it was made
against. When the page later changes, **the pin holds and a `pin_notice`
fires**.

## 7. Bumping the prompt

`harvest.extract.PROMPT_VERSION` is part of the cache key. **Bump it on any
prompt change** — that is what makes the invalidation deliberate and visible.
Expect the entire cache to miss, expect `pending_extraction` to jump, and expect
the backlog to drain over several weeks at 200 calls per run. Say so in the
commit message.

---

**Last executed:** never — the extraction track has not landed. The queue
inspection in §1 and the environment variables in §2 are verified against
`harvest/extract.py` and `harvest/cli.py`; the drain command currently exits 2
by design.
