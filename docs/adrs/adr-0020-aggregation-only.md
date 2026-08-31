---
type: adr
id: ADR-0020
title: Catalogue is aggregation-only; no registration, no user accounts
status: accepted
date: 2026-08-31
deciders: [project author (OST)]
supersedes: [ADR-0009, ADR-0010]
related: [adr-0021-canonical-record-is-a-ckan-package-dict, adr-0022-hosting-and-automation, adr-0038-source-metadata-is-never-updated-only-annotated]
tags: [premise, product]
---

# ADR-0020 — Catalogue is aggregation-only; no registration, no user accounts

## Status

**Accepted.** This is the premise every other decision rests on. Supersedes
ADR-0009 (org/group taxonomy as a permissions model) and ADR-0010
(invite-only registration) from `plans/01-ckan-plan.md`.

## Context

The original brief was a CKAN instance that institutions would log into, claim
their records, and maintain. `plans/01-ckan-plan.md` §1 already flagged that
the entire case for CKAN rested on that one behaviour: at hundreds to low
thousands of public records, nothing about CKAN's *scale* capabilities is
load-bearing.

In turn 3 the author killed it directly:

> "I honestly can't see people loggin gin, going through the claiming process
> of historical artifacts. Ahd, despite my colleague's optimism, I can't see
> anone - having already published a dataset with complete metadata to wherever
> (eg zenodo), why would they then want to also register it a second time with
> our service."

That is the correct read. Registration-based catalogues in loosely-governed
federations do not fail on technology; they fail because the registration step
never happens. CKAN's differentiating feature here — organisations, roles,
dataset ownership, claim workflows — is machinery for a behaviour that will not
occur, and it costs a Postgres instance, a Solr instance and a patch-upgrade
obligation roughly every few months to provide a login screen nobody uses.

The governance context matters too. IEA Wind is a slow, loosely-administered
federation with no named successor and no budget holder; the prototype must
survive being ignored for a year.

## Decision

**The catalogue asks nothing of anyone.** It watches the places where IEA Wind
people already publish and reflects what it finds.

1. There is **no registration, no login, no user accounts and no write API**.
   Everything is public read.
2. Corrections are possible but optional, and are made by whoever is running
   the catalogue rather than by the artifact's author — additively, never by
   editing what the source said
   ([[adr-0038-source-metadata-is-never-updated-only-annotated]]).
3. `organizations.yaml` and `groups.yaml` remain in the repository as
   **canonical data, not accounts**: they are the institutional and task
   attribution the harvest infers, they give promotion day somewhere to hang
   records, and they give the site its facets.
4. Non-technical people who want a correction use a person-shaped process — a
   GitHub issue form or an email to a shared address producing a request
   somebody commits. That is deliberately not a system.

## Consequences

**Good**

- An entire subsystem disappears: authentication, authorisation, invitations,
  claim workflows, password resets, spam registration. With everything public
  there is no read-path authorisation at all.
- The catalogue is useful on day one without anyone agreeing to anything, which
  is the only mode that fits a federation that takes a year to decide.
- It makes the aggregator's epistemic position honest: the catalogue reports
  what sources say, and anyone can check any field against its source.

**Costs, stated plainly** (`plans/02-static-plan.md` §5)

- **No self-service contribution from non-technical people.** GitHub PRs work
  for Task 43's crowd and not at all for a programme manager.
- **No write API.** Nothing can push records in. Given the premise, nothing was
  going to.
- **Institutional optics.** "It's a static site" can read as unserious in a
  committee. The counter is
  [[adr-0021-canonical-record-is-a-ckan-package-dict]]: the records are already
  in CKAN's exact format and standing CKAN up is a day's work whenever there is
  a reason to — see [[promote-to-ckan]].

**Follow-on**

- Every record page carries a "report an issue at the source" link, so fixes
  flow to where they stick.
- `organizations.yaml` includes deliberately explicit fallback owners
  (`zenodo-community`, `unattributed`) so that "we do not know who owns this"
  is visible on the record page rather than invisible.

## Source

`plans/02-static-plan.md` §1, §5, §8 (ADR-0020);
`transcript/conversation-record.md` turns 2–3.
