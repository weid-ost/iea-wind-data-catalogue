---
type: adr
id: ADR-0021
title: Canonical record = CKAN package dict, validated at build
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
related: [adr-0020-aggregation-only, adr-0037-events-are-the-source-of-truth, adr-0032-site-framework-astro, record-format]
tags: [contract, promotion, records]
---

# ADR-0021 — Canonical record = CKAN package dict, validated at build

## Status

**Accepted.** This is the promotion contract, and it is enforced, not aspired to.

## Context

Turn 3 asked, precisely:

> "Could it use the exact same form of records as the CKAN stored articles in
> the GCS store (ie could be promoted to CKAN later)"

The answer is yes, but the trap is drift. A static site will happily render
records that CKAN's API would reject — a slug with a capital letter, a tag with
a space, a Zenodo licence identifier CKAN has never heard of, an `extras` value
that is a list rather than a string — and nobody discovers this until they try
to promote three thousand records at once, on the day they are trying to
demonstrate that promotion is cheap.

The institutional argument matters as much as the technical one. "It's a static
site" reads as cutting corners in a committee; "the records are already in
CKAN's exact format and standing CKAN up is a day's work" reads as de-risking.
That claim is only worth making if it is continuously true.

## Decision

**The canonical record is a CKAN `package` dict**, serialised as JSON, one file
per record in `records/<slug>.json`, directly POSTable to `package_create`
**with no transformation**.

1. `harvest/materialize.py` shapes every record; `harvest/models.py`
   `CkanPackage` is the type.
2. **A `validate-ckan-compat` gate runs on every harvest and every build**, and
   fails on anything CKAN's API would refuse: `name` slug rules, tag character
   rules, licence-register membership, string-only `extras`, resource URLs
   present, `owner_org` resolving in `organizations.yaml`, `groups[].name`
   resolving in `groups.yaml`, legal `state`. It prints **every** violation,
   not the first. Run it with `uv run python -m harvest validate`.
3. **`schema/ckan-scheming.json` stays in the repository** even though nothing
   consumes it yet. It is the written definition of the record shape, it
   documents the custom fields, and it is the input CKAN needs on promotion day.
   A test enforces that it and `harvest.materialize.EXTRA_KEYS` agree.
4. **No framework-specific field may ever enter the record format.** Astro
   globs `records/*.json` and renders them; it does not own them
   ([[adr-0032-site-framework-astro]]).
5. Structured custom fields are carried as JSON strings inside `extras`,
   because CKAN extras are string-valued. Details in [[record-format]] §4.2.

## Consequences

**Good**

- Promotion costs about a day: stand up the CKAN plan's Terraform, run the
  loader against the JSON. Drill: [[promote-to-ckan]].
- The gate catches an entire class of bug at the moment it is introduced rather
  than at the moment it is expensive.
- The record format is documented by an executable artifact (the validator)
  rather than by prose.

**Costs**

- CKAN's constraints shape a format nothing currently uses: string-only extras
  is the visible wart, and it forces the JSON-in-a-string encoding that every
  renderer must decode.
- CKAN's `state` field cannot express withdrawal, because `deleted` means
  "purgeable". Withdrawal therefore lives in `extras.lifecycle_state` — see
  [[adr-0027-withdrawn-records-are-retained]] and [[record-format]] §4.3.
- Licences must be mapped into CKAN's register rather than kept as SPDX;
  `harvest.licenses` does this, and records the raw value in `license_raw` so
  nothing is lost.

**Enforcement, checkable**

- `uv run python -m harvest validate` exits non-zero on any violation.
- The site build must fail on a malformed record too — the Zod content-collection
  schema in `site/src/content.config.ts` *is* the same gate, per
  `plans/02-static-plan.md` §2.2. Fixture `x-08-ckan-invalid` exists to prove
  it fails.

## Source

`plans/02-static-plan.md` §2.2, §8 (ADR-0021); `harvest/CONTRACT.md` §§7–8;
`transcript/conversation-record.md` turn 3.
