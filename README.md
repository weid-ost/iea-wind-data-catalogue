# IEA Wind Data Catalogue — Planning Package

Everything produced in the planning conversation of 2026-08-31, packaged to seed the repository. Start Claude (Code) on this with `CLAUDE.md`, which binds the invariants and points at the authoritative documents.

## Contents

| Path | What it is |
|---|---|
| `CLAUDE.md` | Repo seed for Claude — invariants, conventions, reading order |
| `plans/02-static-plan.md` | **Authoritative architecture**: static site, GitHub-only, event log, no-update annotation model, ADR register 0020–0039, decisions log |
| `plans/01-ckan-plan.md` | The original CKAN/GCP plan — retained as the documented *promotion path*, not the current design |
| `design/design-system.md` | Design system spec: Teresa's Green anchor, accent-bar model, typography, ARIA requirements, a11y gate |
| `design/design-tokens.json` | W3C DTCG tokens (rev 2 — neutral surfaces, panel/badge components) |
| `design/gen.py` + `design/palette.json` | The colour derivation + WCAG verification script and its output — rerun after any palette change |
| `fixtures/fixtures-catalogue.md` | Canonical + edge-case fixtures per source, cross-cutting, and rendering-only sets |
| `transcript/conversation-record.md` | Every user prompt verbatim + faithful per-turn account of responses and artifacts |

## Reading order for a newcomer

1. `plans/02-static-plan.md` §1–§2 (the premise), then §4 (the data model — source keys, no-update, event log).
2. `CLAUDE.md` (the rules distilled).
3. `fixtures/fixtures-catalogue.md` (what correct behaviour looks like at the edges).
4. `design/design-system.md` (how it should look and the a11y gate).
5. `transcript/conversation-record.md` when you want to know *why*.

## Provenance note

The transcript preserves user prompts verbatim; Claude's responses are recorded faithfully but not byte-for-byte (the sandbox had no transcript export). All decisions those responses produced are fully captured in the plans, ADRs and design documents — for a verbatim chat log, use claude.ai's built-in conversation export (Settings → Privacy → Export data, or share the chat).
