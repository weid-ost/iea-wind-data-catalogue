---
type: adr
id: ADR-0043
title: What belongs in the catalogue — attribution, or a citation link, recorded either way
status: accepted
date: 2026-09-08
deciders: [project author (OST)]
related: [adr-0020-aggregation-only, adr-0026-change-detection-by-source-key, adr-0027-withdrawn-records-are-retained, adr-0037-events-are-the-source-of-truth, adr-0040-vocabulary-is-defined-once-and-shown-at-two-levels, adr-0041-a-mapping-improvement-must-reach-the-existing-corpus, adr-0042-the-concept-doi-is-the-record, correct-a-record]
tags: [scope, inclusion, provenance, harvest]
---

# ADR-0043 — What belongs in the catalogue

## Status

**Accepted.**

## Context

The catalogue casts a wide net on purpose (ADR-0020: it asks nothing of anyone,
so it has to go and look). One of the ways it looks for software is a GitHub
topic search, and `wind-energy` is a label anyone can apply to anything. The
result, visible in the live catalogue:

- `api-evangelist/sunedison`, `…/makani`, `…/radia` and six more — a
  third-party directory of company API surfaces;
- `DiogoRibeiro7/nuclear_vs_wind_solar_cleanliness`;
- `dofliu/windFarm-Go`, an educational offshore-O&M video game;
- `Bruno-Mascarenhas/site-labmim`, a laboratory's website build.

None has an IEA Wind Task attribution, a DOI, or any connection to the
programme. They are in an IEA Wind catalogue because they carry a topic.

Asked what the rule should be, the project owner stated it:

> "Things that belong in the catalogue must either be **directly attributed to
> IEA Wind** (ie be tagged, or published in our communities or websites in some
> way), or **cited by work which is in the catalogue**, or **cite things which
> are in the catalogue**. And ideally we should record which of those is the
> case."

Testing that against the corpus exposed the real problem. Sixty-two listed
records had no recoverable IEA Wind signal — but many plainly belonged: OSTI's
"OC7 Project Phase II: Code Comparison and Experimental Validation" is Task 56;
"An international benchmark for wind plant wakes" is Task 31 Wakebench; a dozen
Wind Energy Science papers characterise the IEA 15 MW reference turbine.

They matched OSTI's `"IEA Wind"` free-text query, or Crossref's
`query.bibliographic: "IEA Wind"`, against text the adapters never stored.
**The catalogue knew exactly why it fetched each record and threw the reason
away.** Text-matching the stored metadata cannot recover it, and guessing would
be worse than the topic sweep it is meant to fix.

## Decision

### 1. Every scrape records the route that found it

`discovered_via` on the observation, the mapped observation and the event:
`community:iea_wind_task_43`, `org:IEAWindSystems`, `topic:wind-energy`,
`query:iea-wind-task-by-title`, `task-page:task-43`, `backfill:doi`. All seven
adapters set it, and `resolve()` unions it across every scrape of an identity.

It reaches the existing corpus through the mechanism ADR-0041 established: each
adapter's `MAPPING_VERSION` moves, so every record is re-scraped exactly once.
`harvest.adapters.base.stamp()` is the one spelling of that, so a reader can
tell at a glance which adapters version their mapping — all of them.

### 2. `sources.yaml` says which routes are *not* an attribution

Default: a route **is** an IEA Wind attribution. A route exists because somebody
put it in the register deliberately, and almost all of them are attributions by
construction — an IEA Wind community, an IEA Wind organisation, a Task page, a
query naming IEA Wind.

The exceptions are listed per source under `generic_routes`. Today there is one:

```yaml
generic_routes:
  - "topic:wind-energy"
```

`topic:iea-wind` and `topic:iea-task-43` stay attributions: applying one of
those topics is a claim about the work, made by whoever published it.

Putting the judgement in the register rather than in code means a human can see
it, argue with it, and change it without a commit to `harvest/`.

### 3. Three limbs, decided at materialise time, recorded per record

`harvest/inclusion.py` writes two extras on every record:

| `inclusion_basis` | Meaning |
|---|---|
| `direct` | an attributing discovery route, or an IEA Wind Task attribution |
| `cites` | it cites an identity the catalogue holds |
| `cited-by` | an identity the catalogue holds cites it |
| `unassessed` | no route was ever recorded, so nothing is established either way |
| `none` | out of scope |

…and `inclusion_evidence`, one sentence naming the *specific* grounds
("Found through community:iea_wind_task_43, which is an IEA Wind attribution",
"Cites 10.5281/zenodo.…, which the catalogue holds"). That is the "record which
of those is the case" half of the request, and it is rendered on the record page
for every record, not only the excluded ones. **A catalogue that can say why it
holds something is a different proposition from one that cannot.**

Citation edges come from Crossref reference lists — `reference` is now in
`SELECT_FIELDS`, having been deliberately excluded as bulky and useless right up
until the catalogue acquired a rule that needs it — and from
`related_identifiers` relations. Only the DOIs are kept from each reference
entry. Every stated edge fills **both** directions: if A's reference list names
B then A `cites` B and B is `cited-by` A, which matters because Crossref
publishes reference lists but not citing-work lists.

This is necessarily a **catalogue-wide** derivation: "does this cite something in
the catalogue" is not a fact about one record. `materialize_all` therefore
resolves every identity first, builds the citation index, and only then shapes
packages.

### 4. Out of scope is not deleted

An out-of-scope record keeps its page, its URL and its citation, and is excluded
from the listings, the sitemap and the DCAT catalogue — exactly the treatment a
merged-away duplicate gets (ADR-0042), and for the same reason (ADR-0027).

The decision is **re-made from scratch on every materialise**. A repository that
only ever matched a topic search, and is later cited by a Task report, walks
back into the listings with no intervention. That reversibility is what makes an
automatic rule acceptable: nothing is destroyed, and nothing needs a human to
undo.

## Consequences

**Good**

- The question "why is this in an IEA Wind catalogue?" has an answer on every
  record, in a sentence, that a reader can check.
- The topic sweep can stay wide. Its output is now filtered on evidence rather
  than curated by hand, which is the only version of this that survives contact
  with a weekly cron and no maintainer.
- Recording the route fixed a second problem for free: records that matched a
  free-text `"IEA Wind"` query against text we never stored are now provably in
  scope rather than unexplainable.
- The rule is stated on the About page in the owner's own words, so the
  catalogue is answerable to the sentence rather than to our paraphrase of it.

**Costs**

- A full re-harvest, once, to land the routes on the existing corpus. Every
  adapter's mapping version moved at the same time, so the heartbeat commit that
  follows rewrites an event for nearly every record.
- Records harvested before this ADR have no route in their log. They fall back
  to their Task attribution, and where they have none they are `unassessed` —
  **listed, and marked** — rather than excluded, because the rule is meant to
  act on evidence of non-attribution and not on the absence of evidence. The
  re-harvest that ships with this ADR settles all but one of them; the survivor
  is an OSTI record that has rolled out of its query's window and may never be
  re-scraped, which is exactly why the count is worth watching.
- Citation coverage is only as good as Crossref's reference lists. DataCite
  deposits rarely state citations, OSTI states none, and GitHub has no notion of
  one. A conference presentation on Zenodo that cites a Task report will not be
  rescued by limb 2 or 3 unless somebody deposits the reference list — so limb 1
  is doing nearly all the work, and that is worth knowing.
- The rule can exclude something that belongs. That is what
  `data/annotations/` is for (ADR-0038): a curator can attribute a Task by hand,
  which makes it `direct` on the next materialise. The reverse — a record that
  should be excluded but is not — is the quieter risk, and only shows up by
  reading the listings.
- `topic:wind-energy` now costs API calls for records that will not be listed.
  If it never produces an in-scope record, the honest next step is to delete the
  topic from `sources.yaml` rather than keep paying for it.

## Source

`harvest/inclusion.py`; `harvest/models.py` (`discovered_via`);
`sources.yaml` (`generic_routes`); `site/src/components/OutOfScopeBanner.astro`;
the About page's "What belongs in this catalogue"; fixtures `x-13`, `x-14`.
