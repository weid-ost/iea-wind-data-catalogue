---
type: adr
id: ADR-0033
title: Harvester language — Python, pinned with uv; Go and Rust considered
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0034-toolchain-pinning-and-no-auto-updates, adr-0035-no-vendor-sdk, adr-0032-site-framework-astro, local-dev-setup]
tags: [harvest, language, longevity]
---

# ADR-0033 — Harvester language: Python, with discipline

## Status

**Accepted.**

## Context

Turn 9:

> "Could we build this service in rust or go; that way eliminate problems with
> python dependencies over the years? Or perhaps the readability of python is
> better?"

The concern is legitimate. The classic Python failure in a dormant repository is
that a pinned C-extension package has no wheel for the runner's newer
interpreter, tries to build from source, and dies.

**The framing that lowers the stakes:** the durable artifact is the *data*, not
the code. `records/*.json` and `cache/*.json` are plain committed JSON. If the
harvester rots completely, the site still builds and still serves, and someone
rewrites the harvester in whatever is fashionable in 2032 against a record
format that has not changed. This decision is lower-stakes than it feels.

## Decision

**Python, pinned with `uv`.**

Why not Rust: performance is irrelevant — the workload is network-bound, waiting
on Zenodo's API. What you would pay is compile times, real churn in the async and
HTTP crate ecosystem, and the steepest learning curve of the three. Every one of
those cuts against the single property that matters: a stranger being able to
pick this up.

Why Go is tempting: its compatibility promise is the best in the industry, a
single static binary has no runtime to install, this harvester could be written
against the standard library plus `x/net/html`, and Go is arguably *more*
readable to a stranger than idiomatic Python — explicit error handling, no
decorators, no metaclasses.

**Why Python wins anyway:**

1. **Audience.** The likely inheritor is a wind-energy researcher at DTU, PNNL
   or NREL, not a professional backend engineer. Python is that community's
   lingua franca. Go removes dependency risk at the cost of shrinking the pool
   of people who could maintain this — and since nobody is committed to
   maintaining it yet, that pool is the scarcer resource.
2. **`trafilatura` is genuinely best-in-class** at main-content extraction, and
   Go's equivalents are merely adequate. Extraction quality feeds directly into
   LLM input quality and token cost, which is the core of the Tier-3 design.
   That is a substantive technical reason, not a preference.
3. **The dependency-rot concern is largely solved** by pinning the *interpreter
   itself*. `uv` fetches standalone CPython builds, so `uv.lock` plus
   `.python-version` reproduces the environment years later regardless of what
   the runner has in its tool cache.

**Dependency discipline — capped at four direct runtime dependencies:**

| Dependency | For |
|---|---|
| `httpx` | HTTP |
| `trafilatura` | main-content extraction (brings lxml) |
| `pydantic` | schema validation |
| `pyyaml` | the YAML registers |

Dev adds `pytest`. All pinned exactly in `pyproject.toml`; `.python-version`
pins CPython 3.12.8; `uv.lock` is committed; CI runs `uv sync --frozen`.

**No fifth dependency.** Not `beautifulsoup4` (the sanitiser is stdlib
`html.parser`), not `requests`, not `python-dateutil`, and above all **no LLM
SDK** ([[adr-0035-no-vendor-sdk]]). If you are certain you need a fifth, that is
an ADR, not a commit.

## Consequences

**Good**

- The inheritor pool is as wide as it can be.
- Interpreter pinning removes the one Python failure mode that actually bites
  dormant repositories.
- Four dependencies is a small enough surface to audit by hand.

**Costs**

- Python is the less durable of the two serious candidates, and this ADR
  knowingly trades roughly a decade of unattended-build confidence for
  maintainer familiarity. If that trade is ever revisited, the honest way to
  write it is: *stdlib-only Go, accepting weaker content extraction*. Choosing
  Go would not add a toolchain, since the repository already has Node for Astro
  ([[adr-0032-site-framework-astro]]) — both options leave you at two.
- The four-dependency cap makes some tasks more work than they would be with a
  library. That is the intended pressure.
- `trafilatura` brings `lxml`, which is the one C extension in the tree and
  therefore the one place the wheel-rot failure could still appear. Interpreter
  pinning is the mitigation.

## Source

`plans/02-static-plan.md` §3.6, §8 (ADR-0033); `pyproject.toml`;
`.python-version`; `harvest/CONTRACT.md` §13;
`transcript/conversation-record.md` turn 9.
