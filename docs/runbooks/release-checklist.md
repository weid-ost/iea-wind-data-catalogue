---
type: runbook
id: RUN-release-checklist
status: current
date: 2026-08-31
related: [adr-0039-design-system, adr-0021-canonical-record-is-a-ckan-package-dict, adr-0034-toolchain-pinning-and-no-auto-updates, run-the-a11y-gate, materialize-and-validate, promote-to-ckan]
tags: [runbook, release, gates]
last_executed: 2026-09-01
---

# Runbook — release checklist

**Goal:** everything that must be true before the site is deployed to the
public URL.
**Governed by:** [[adr-0039-design-system]] §7,
[[adr-0021-canonical-record-is-a-ckan-package-dict]],
[[adr-0034-toolchain-pinning-and-no-auto-updates]].

---

## 1. The automated gates

```sh
make gates
```

which runs, in order:

```sh
make test          # uv run pytest
make validate      # uv run python -m harvest validate
make build-tokens  # uv run python design/gen.py
cd site && npm run gates
```

Every one must pass. `make gates` fails on the first failure, so run the parts
individually while iterating.

| # | Gate | Command | Passes when |
|---|---|---|---|
| 1 | tests | `make test` | `2080 passed, 476 skipped` or better; **no new skips without a reason**. The skips are the fixture-kind parametrisations stepping over fixtures of the other kinds, which is by design |
| 2 | CKAN-compat | `make validate` | `validate-ckan-compat: OK — N record(s)` |
| 3 | replay determinism | `rm -f records/*.json && make materialize && git status --short records/` | **no changes** — the acceptance test for ADR-0037 |
| 4 | palette + contrast | `make build-tokens` | every pair reports `PASS`; **`design/palette.json` unchanged** unless a colour was deliberately altered |
| 5 | token discipline | `cd site && npm run gates` | no hex literals, raw `px` colours or off-scale spacing outside `tokens.css` |
| 6 | accessibility | `cd site && npm run gates` | `pa11y-ci` green over the URL list, **in both themes** |
| 7 | site build | `make site` | `astro build` **and** Pagefind complete; a malformed record fails the build |
| 8 | pinning | `grep -rn "ubuntu-latest\|lts/\*" .github/workflows/` | **no matches** |

All eight gates run today: `site/` and `.github/workflows/` both exist, and
gates 1–8 were verified **locally** on 2026-09-01 against the first coherent
harvest. Neither workflow has itself executed — nothing has been pushed — so
the CI *encoding* of these gates is cross-checked against this checklist by
reading, not by a green run. That is not academic: gate 4 in `ci.yml` was
reading `palette.json` from the repository root, which `design/gen.py` stopped
writing, so it would have crashed with `FileNotFoundError` on every push
(compliance-01). It now runs the same `git status --short design/` diff this
section documents, and treats any contrast `FAIL` as fatal, since the Rev 3
palette reports `ALL PAIRS PASS`.

### On gate 4

`make build-tokens` runs `uv run python design/gen.py`, which writes
`design/palette.json` in place — it no longer drops a stray `palette.json` in
whatever directory you happened to be in. So the check is just a diff:

```sh
uv run python design/gen.py
git status --short design/      # expect: nothing
```

Anything listed there is a deliberate colour change or a bug. Read the script's
own WCAG block too: every pair must print `PASS` and the summary must read
`ALL PAIRS PASS`. The decorative hairline border (`n300` on white, 1.51) is
reported at a 1.0 floor rather than judged at 3:1, because WCAG 1.4.11 governs
components you must perceive to operate, not decoration; every border that
*does* carry meaning — focus, input, strong — is a separate token held to 3:1
in the shipped block.

Verified on 2026-09-01: regenerating reproduces `design/palette.json` and
`design/design-tokens.json` byte for byte, with every pair PASS.

## 2. The three manual accessibility passes

**Automated checks catch roughly a third of real issues.** These are part of the
release, not optional extras ([[run-the-a11y-gate]] §4). Record the date each
was last done.

- [ ] **Keyboard-only walk.** Mouse unplugged. Skip link is the first focusable
      element; tab home → search → filter → result → record → theme toggle and
      back. No traps, focus always visible, focus order matches reading order,
      every target ≥ 24px.
      *Last done: ________*
- [ ] **Screen-reader smoke test.** VoiceOver or NVDA, following search → result
      → record. Listen for the live result count, the zero-results announcement
      (`r-07`), the withdrawn banner (`r-04`, `role="status"`), and whether
      provenance badges read as meaningful text rather than as colour (`x-05`).
      *Last done: ________*
- [ ] **200% zoom / 320px reflow.** No horizontal scrolling, nothing clipped,
      the 300-character title (`r-01`) wrapping with dignity, five task chips
      (`r-03`) overflowing gracefully.
      *Last done: ________*

## 3. Content checks

- [ ] `state/last-run.json` → `finished_at` is recent, and the freshness banner
      is **not** in its warning state (past 45 days).
- [ ] `state/last-run.json` → `notices` read and understood. This is the
      curator's short monthly job: displacement and pin notices, not a log.
- [ ] `unmapped_licenses` is empty, or every entry is a known upstream oddity.
      **Never infer an open licence** to clear this.
- [ ] `dropped_dois` reviewed — DOIs that failed resolve-or-drop are a signal
      about a task page, not noise.
- [ ] `unreachable_sources` is empty, or each one is expected. Two are:
      **`wdh`** always (the auth wall, fixture `wdh-07`), and **`github`**
      whenever the run had no `$GITHUB_TOKEN` — 60 requests/hour is not enough
      for one harvest. Anything else on that list needs an explanation before
      release.
- [ ] Withdrawn records still render, with their banner, at their original URLs.
- [ ] `/dev/components` is `noindex` **and** `data-pagefind-ignore`.
- [ ] Every new component appears in the gallery; every new harvest behaviour
      ships with its fixture.

## 4. Promotion-contract check

- [ ] `make validate` green — this is what makes the "CKAN is a day's work"
      claim true ([[promote-to-ckan]]).
- [ ] `harvest.materialize.EXTRA_KEYS` and `schema/ckan-scheming.json` agree
      (enforced by `make test`; if you added a key to one, add it to the other).
- [ ] Every `groups[].name` resolves in `groups.yaml`; every `owner_org`
      resolves in `organizations.yaml`.

## 5. Deploy

Deployment is the harvest workflow, not a separate action: **build and deploy
run in the same workflow run as the harvest**, because pushes made with
`GITHUB_TOKEN` do not trigger further workflows. Trigger it with
`workflow_dispatch` if you need it now. To publish what is already on `main`
without harvesting (a site-only change, say), tick **skip_harvest**: build and
deploy run alone. That path makes no heartbeat commit.

The repository's Pages setting must be **GitHub Actions** (workflow-based
deployment), not "deploy from a branch". The built site is a workflow
artifact and is never committed.

After deploying:

- [ ] the public URL serves, over HTTPS;
- [ ] a record page renders with its JSON-LD present in **view-source**, not
      injected by script;
- [ ] search returns results and at least one filter narrows them;
- [ ] the theme toggle works and survives a reload.

## 6. Commit and note

```sh
git add -A
git commit -m "release: <what changed>"
```

Update the **Last executed** line at the bottom of every runbook you actually
ran. An unexecuted runbook is a hypothesis.

---

**Last executed:** 2026-09-01 — **all eight gates**, against the first coherent
harvest rather than an empty checkout: `2080 passed, 476 skipped`;
`validate-ckan-compat: OK — 30 record(s)`; `make materialize` byte-stable;
`design/gen.py` reproduces `design/palette.json` exactly with every contrast
pair PASS; `check-tokens`, `check-ckan-gate` and `check-urls` all OK;
`pa11y-ci` **14/14 URLs passed** (7 pages × 2 themes, three of them real
harvested record pages); `astro build` + Pagefind complete, 36 pages, 30
records indexed; and `grep -rn "ubuntu-latest\|lts/\*" .github/workflows/`
returns nothing.
