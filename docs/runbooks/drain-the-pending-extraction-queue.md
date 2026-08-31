---
type: runbook
id: RUN-drain-the-pending-extraction-queue
status: current
date: 2026-08-31
related: [adr-0031-the-harvest-never-fails-on-llm-unavailability, adr-0025-the-extraction-cache-is-committed, adr-0030-llm-access-via-github-models, adr-0024-the-llm-boundary, adr-0035-no-vendor-sdk, correct-a-record]
tags: [runbook, llm, tier-3]
last_executed: 2026-09-01
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
extract: M still queued (see state/pending-extraction.json)
```

It exits **0** whether or not anything drained. An entry stays queued when the
page cannot be re-fetched or the model is unavailable; an entry with no `url`
at all is dropped rather than kept forever.

Every one of the requirements this runbook used to list as pending is now
implemented and covered by `tests/test_extract.py`:

- `extract()` returns `None` — never raises — on no token, rate limit, outage,
  unparseable JSON or schema-validation failure (fixture `x-07`).
- a cache miss with no model appends `{url, cache_key, reason, queued_at}` to
  `state/pending-extraction.json`, deduped on `cache_key` with `queued_at`
  pinned to the first sighting, and the run succeeds.
- cache entries are `cache/<key>.json`, byte-stable, where
  `key == sha256(content + prompt_version + model_id)`. `cache_key` is
  unchanged from the foundation: the Tier-3 source key derives from the same
  normalised content, and the adapters and the reconciler have to agree.
- `state/last-run.json` carries real `cache.hits`, `cache.misses` and
  `pending_extraction`.
- no more than `MAX_EXTRACTIONS` model calls per invocation.
- content reaching `extract()` is `trafilatura` main content through
  `harvest.sanitize.html_to_text` — never raw HTML. `harvest.extract.main_text`
  is the single definition, so the adapter, the source key and this drain
  cannot disagree about what "the content" is.
- identifiers are supplied as `context["dois"]` from
  `harvest.doi.extract_dois`, **and a DOI the model returns that was not in
  that list is blanked before the result is cached** — so a hallucinated
  identifier cannot even reach `resolve_or_drop`, let alone a record.
- every model-produced field carries
  `FieldProvenance(extraction_method="llm", model=…, prompt_version=…,
  confidence=…)`, which the model layer refuses to construct without a `model`
  and a `prompt_version`.

> **Observed 2026-09-01: GitHub Models answers `410 Gone`** with
> `github_models_retirement_brownout`. The harvest does not care, which is the
> entire point of [[adr-0031-the-harvest-never-fails-on-llm-unavailability]]:
> the pages queue, the run reports `ok: true`, Tier 1 is untouched. Point
> `$HARVEST_LLM_ENDPOINT` at any OpenAI-compatible provider (§2) to drain.

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
[[correct-a-record]] §3.6. The corrected object replaces the cache entry and is
marked `pinned`, recording both `pin_source_key` — the content hash it was made
against — and `pin_url`, the page it is for.

`pin_url` is what makes the pin survive. Cache entries are keyed on content, so
a page rewrite mints a new key; a pin found only by content would be reverted
by the next site refresh without anybody noticing. `find_pin` looks it up by
URL, `lookup_cache(..., url=…)` serves it whatever the page says today, and when
the content hash no longer matches `pin_source_key` **the pin holds and a
`pin_notice` fires** into `state/last-run.json` so a human revisits it.

Check for held pins in the monthly read:

```sh
uv run python -m harvest report | python3 -m json.tool | grep -A6 pin_notice
```

## 7. Bumping the prompt

`harvest.extract.PROMPT_VERSION` is part of the cache key. **Bump it on any
prompt change** — that is what makes the invalidation deliberate and visible.
Expect the entire cache to miss, expect `pending_extraction` to jump, and expect
the backlog to drain over several weeks at 200 calls per run. Say so in the
commit message.

---

**Last executed:** 2026-09-01. `uv run python -m harvest run --source ieawind
--limit 5` harvested five records live from `iea-wind.org/task43/
t43-publications/` (every DOI resolved against DataCite; nothing came from the
page), queued seven ambiguous pages, and reported `ok: true`. A second run was a
clean no-op — `seen: 5, skipped_unchanged: 5, changed: 0`. `uv run python -m
harvest extract --limit 2` then exited 0 with
`extract: resolved 0 pending extraction(s)` / `extract: 7 still queued`,
because GitHub Models is in its retirement brownout and answered `410 Gone` to
every call. §1, §3 and §6 are verified output; §5's budget is still an
estimate.
