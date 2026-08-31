---
type: adr
id: ADR-0034
title: Toolchain pinning, and no automated dependency updates
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0032-site-framework-astro, adr-0033-harvester-language-python, adr-0022-hosting-and-automation, local-dev-setup, release-checklist]
tags: [toolchain, longevity, supply-chain]
---

# ADR-0034 — Pinned everything, no auto-updates

## Status

**Accepted.** Split out as its own ADR at the author's request in turn 10:

> "let's stick with python but explicitly mention uv/node pinned dependencies -
> and rhte decision to use the OpenAI API rather than an SDK - in the ADRs."

## Context

This is contrary to normal practice, which is why it needs writing down. The
usual advice — keep dependencies current, let Dependabot open PRs — optimises
for a project with active maintainers and a running service exposed to user
input. This project has neither. There is no runtime, no server, no user input
and no authentication, so dependency CVEs are largely irrelevant to a static
build.

What *is* relevant is that this repository may be untouched for a year or three
and must still build when someone returns to it. A pinned, dormant repo is far
more likely to build in three years than one Dependabot has been bumping
unattended, where the first failure happened eight months ago in a PR nobody
merged.

## Decision

**Pin everything. Update only when you need something.**

Python:

- `.python-version` pins the interpreter — **CPython 3.12.8**. `uv` fetches
  standalone CPython builds, so this survives whatever the runner's tool cache
  holds.
- `pyproject.toml` pins every direct dependency to an exact version
  (`httpx==0.28.1`, `trafilatura==2.2.0`, `pydantic==2.13.5`, `pyyaml==6.0.3`,
  dev `pytest==9.1.1`).
- `uv.lock` is **committed**. Installation is `uv sync --frozen --dev`, in CI
  and locally — `make sync` uses `--frozen` too, so a stale lockfile fails
  loudly instead of being silently rewritten.
- Direct runtime dependencies are capped at four
  ([[adr-0033-harvester-language-python]]).

Node and the site:

- A **pinned Node version** via `actions/setup-node`, never `lts/*`.
- `package-lock.json` **committed**; build with `npm ci`, never `npm install`.

CI:

- A **pinned runner image**: `runs-on: ubuntu-24.04`, **never `ubuntu-latest`**.
  Floating references are how dormant repos break unattended.
- Pinned action versions.

Policy:

- **No Dependabot, no Renovate, no automated dependency PRs.**
- Update when you need a feature or a fix, deliberately, with the test suite and
  the gates as the check ([[release-checklist]]).

## Consequences

**Good**

- A clone in three years reproduces the same environment and the same output,
  which is the property the whole "rebuild from the repo" model rests on.
- Byte-stable materialisation stays byte-stable, because the code producing it
  does not drift underneath the data.
- Reviewing the dependency tree is a finite job.

**Costs**

- **The repository will accumulate known vulnerabilities in build-time
  dependencies, and that is accepted.** The reasoning is stated above; it does
  not generalise to a project with a runtime.
- The residual real risk is **supply-chain compromise of a build-time
  dependency**, because the build runs with a write-capable token. Mitigations:
  minimal dependencies, `npm ci` and `uv sync --frozen` from committed
  lockfiles, and scoped workflow permissions — `contents: write` only where the
  harvest commits, `pages: write` and `id-token: write` only on deploy.
- Upgrades, when they eventually happen, are larger and riskier than a stream of
  small ones. Budget for that rather than pretending otherwise.
- The a11y tooling (`pa11y-ci`, an axe-core runner) is the heaviest dev
  dependency accepted, and it is pinned like everything else. It is justified
  because it is a **gate**, not advice ([[adr-0039-design-system]]).

**Checkable.** `uv sync --frozen --dev` must succeed on a clean clone;
`uv run pytest` must be green; `.github/workflows/*` must contain no
`ubuntu-latest` and no `lts/*`.

## Source

`plans/02-static-plan.md` §3.5, §3.6, §8 (ADR-0034); `pyproject.toml`;
`.python-version`; `Makefile`; `harvest/CONTRACT.md` §13;
`transcript/conversation-record.md` turns 8–10.
