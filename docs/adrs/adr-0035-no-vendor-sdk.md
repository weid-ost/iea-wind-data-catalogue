---
type: adr
id: ADR-0035
title: LLM access via OpenAI-compatible HTTP, with no vendor SDK
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0030-llm-access-via-github-models, adr-0033-harvester-language-python, adr-0034-toolchain-pinning-and-no-auto-updates, adr-0024-the-llm-boundary]
tags: [llm, dependencies, longevity]
---

# ADR-0035 — No vendor SDK: inference is an OpenAI-compatible POST

## Status

**Accepted.** Split out as its own ADR at the author's request in turn 10.

## Context

Vendor SDKs churn faster than almost anything else in this stack: breaking
major versions, shifting auth helpers, transitive dependency trees that dwarf
the four direct dependencies this project allows
([[adr-0033-harvester-language-python]]). In a repository designed to sit
untouched for years ([[adr-0034-toolchain-pinning-and-no-auto-updates]]), an SDK
is the single most likely thing to stop installing.

GitHub Models is OpenAI-compatible. So is essentially every plausible
alternative provider. Inference is therefore a POST with a JSON body.

## Decision

**No LLM SDK, ever. Inference is one `httpx` POST to
`{endpoint}/chat/completions` with a JSON body.**

1. The endpoint default is `https://models.github.ai/inference`
   (`harvest.extract.DEFAULT_ENDPOINT`); the path appended is
   `/chat/completions`.
2. In CI the credential is the built-in `GITHUB_TOKEN` with
   `permissions: models: read` ([[adr-0030-llm-access-via-github-models]]).
   Locally, `$HARVEST_LLM_ENDPOINT`, `$HARVEST_LLM_TOKEN` and
   `$HARVEST_LLM_MODEL` point the same function at whatever key the operator
   personally has.
3. **Everything model-shaped lives behind one function**,
   `harvest.extract.extract()`. Swapping providers is a change to that function
   and nothing else.
4. Responses are **JSON-schema-constrained and validated on receipt with
   pydantic** — the SDK's main genuine convenience, replaced by a dependency the
   project already has.
5. This removes an entire dependency lineage for about fifteen lines of code.

## Consequences

**Good**

- The dependency cap of four survives contact with the LLM feature.
- Provider swappability is a property of the code rather than of a vendor's
  abstraction layer — and it is the reason the OpenRouter option was rejected in
  [[adr-0030-llm-access-via-github-models]]: it would add a vendor to buy
  swappability the interface already provides.
- The wire format is stable and documented, and a stranger can read the request
  in the source without learning an SDK.

**Costs**

- Retries, backoff, streaming and rate-limit handling are hand-rolled. At this
  volume — roughly fifteen pages a week — that is a small amount of code, and
  [[adr-0031-the-harvest-never-fails-on-llm-unavailability]] means the correct
  response to most failures is to return `None` and queue the page rather than
  to retry cleverly.
- If a future provider is *not* OpenAI-compatible, adapting costs more than
  installing their SDK would have. Accepted: the compatible surface is currently
  near-universal, and the alternative is paying the churn cost continuously.
- No SDK means no built-in structured-output helper; the JSON schema is written
  by hand and validated with pydantic. That is a feature — it keeps the schema
  visible in the repository.

## Source

`plans/02-static-plan.md` §3.6, §8 (ADR-0035); `harvest/extract.py` docstring;
`harvest/CONTRACT.md` §13; `transcript/conversation-record.md` turns 9–10.
