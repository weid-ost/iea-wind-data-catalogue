# Harvest anomalies — initial run dossier

**What this is.** A catalogue of the *unusual or unexpected* things the harvest hit
during the first full ("initial") run, recorded so we can inspect them and decide
how the pipeline should behave when it meets them again. Nothing here is
necessarily a bug — much of it is the pipeline correctly refusing to guess. The
point is to turn each surprise into a **conscious decision**.

Every finding ends with a **Decision needed** — a crisp question for the human.
Answered decisions graduate to an ADR, a fixture, or an adapter change; until
then they live here.

## Headline findings (read these first)

1. **Real data loss (7 records).** The ieawind DOI extractor over-captures
   markdown link syntax, so 7 Crossref/IOP/ASME/MDPI publications whose *only*
   entry point was `iea-wind.org/task49/` were dropped and are absent from the
   catalogue. One-line regex fix + re-harvest recovers them. → **§B, §E1–E2**
2. **Systemic Zenodo duplication (~50 pairs).** Zenodo's real version vocabulary
   (`IsVersionOf`/`HasVersion`) is in no relation set, so every concept/version
   chain is catalogued twice. One relation-set change collapses the bulk. → **§I2**
3. **Two off-topic false positives.** A moth-taxonomy paper and a *Primula*
   primrose-taxonomy paper were swept in via Task 43's Zenodo community and
   confidently tagged — evidence that community membership must not confer a task
   or bypass a topicality check. → **§G1, §H4**
4. **Task tagging is a harvest-source artefact.** 45% of records carry no task;
   43 name a task verbatim in their title but aren't tagged; identical-title twins
   disagree because one copy came from Zenodo (community) and the other from
   DataCite (no promotion). → **§H1–H3, H6**
5. **Two sources under-collected.** GitHub rate-limited to 13 records (needs
   `GITHUB_TOKEN`); WDH still walled behind a credential (0 records). → **§A**
6. **Everything else held.** 0 CKAN-compat violations, 0 bad DOIs in records, 0
   provenance contradictions, clean cross-registry reconciliation (28 records),
   and the site builds/renders all 291. The pipeline mostly *over-rejects* (safe)
   rather than contaminating (unsafe).

---

## The run

| | |
|---|---|
| Date | 2026-09-01 → 02 (observation time in events) |
| Command | `python -m harvest run --limit 100` |
| Per-source cap | `max_records: 100` (raised from the prototype's 5 in `sources.yaml`) |
| Records after | **291** (was 30) |
| Events appended | 291 |
| Validation | **0 violations** (CKAN-compat gate clean) |
| Pending Tier-3 extractions | 13 (no LLM available this run) |
| Dropped DOIs | 18 |
| Unmapped licences | 6 |
| Merge proposals | 1 (review required) |

### Per source

| Source | Seen | New events | Reachable | Notes |
|---|---|---|---|---|
| datacite | 100 | 95 | ✅ | hit the raised cap — more available upstream |
| zenodo | 100 | 95 | ✅ | hit the raised cap — more available upstream |
| crossref | 40 | 35 | ✅ | |
| osti | 37 | 32 | ✅ | |
| ieawind | 31 | 26 | ✅ | Tier-3; many pages queued/timed-out (see §T) |
| github | 13 | 8 | ❌ | **rate-limited mid-run** (see A1) |
| wdh | 0 | 0 | ❌ | credential wall (see A2) |

After cross-registry reconciliation the 321 "seen" collapse to 291 unique
records; **28 records are reconciled across more than one registry** (the same
DOI seen by e.g. both DataCite and Crossref).

Primary source of each record: zenodo 99, datacite 80, crossref 36, osti 32,
ieawind 31, github 13.
Resource kinds: publication 120, other 62, report 54, dataset 32, software 23.

---

## A. Source reachability (pipeline-level)

### A1. GitHub rate-limit exhausted mid-harvest
**What:** the GitHub adapter ran unauthenticated and hit the 60-requests/hour
anonymous ceiling partway through, so it disabled itself and only contributed
**13 records instead of the requested 100**. Log:
`GitHub rate limit exhausted (resets at epoch 1788308451); set $GITHUB_TOKEN for 5,000 requests/hour`.
**Why it matters:** the degradation is clean (records untouched, source marked
unreachable) but the initial run under-collected GitHub badly. In CI this is a
non-issue — `GITHUB_TOKEN` is present with 5,000/hr — but any local/large run
needs the token.
**Decision needed:** (a) document that large local runs must export
`GITHUB_TOKEN`; (b) should the adapter *fail louder* (non-zero, or a prominent
notice) when it collects far fewer than the cap because of rate-limiting, rather
than only a WARNING?

### A2. Wind Data Hub has no unauthenticated machine-readable listing
**What:** WDH contributed **0 records**. `/api/info` advertises an AWS API
Gateway that answers `403 Missing Authentication Token`; the site's
`/api/datasets/_search` proxy rejects GET with 404 and unauthenticated POST with
419 (CSRF). The adapter disables itself and leaves existing records untouched.
**Why it matters:** WDH is a first-class intended source but is currently
uncollectable without a credential (`$WDH_API_TOKEN`).
**Decision needed:** pursue a WDH API credential / contact, or formally scope WDH
out of v1 and stop listing it as enabled?

---

## T. Tier-3 (iea-wind.org) — extraction backlog & coverage gaps

The Tier-3 `ieawind` adapter is deterministic first; anything it can't classify
by pattern queues for the LLM. **No model was available this run**, so 13 pages
queued to `state/pending-extraction.json` and none were extracted (cache
hit-rate 0%). This is the designed degradation, but the initial run therefore
*under-collects iea-wind.org*. Observed notice types:

- **`page_unreachable` (6):** read timeouts on task landing pages —
  `task25-63`, `task42`, `task45/t45-publications`, `task47`, `task51`,
  `task57`. iea-wind.org is slow/flaky under sequential fetching.
- **`page_not_record_bearing` / `extraction_queued` (10 each):** publication-list
  and workplan pages classified `other` with confidence 0.0 and queued for the
  LLM (task41/42/43/44/48/52/53 lists, the site-wide `/publications/`, and
  `annual-report-2025`).
- **`coverage_gap_pdf_only` (1):** Task 48's publication list lives inside linked
  PDFs (`Task48.Annual.Report.2024…pdf`, `Task-48-Final-Technical-Report…pdf`);
  PDF parsing is out of scope for v1 (fixture `iea-11`).

**Decisions needed:**
1. Run the LLM extraction pass (`make extract`) now that a backlog exists, or wait
   for CI? (GitHub Models was in retirement brownout as of the last check — is a
   model actually reachable?)
2. iea-wind.org timeouts: raise the per-request timeout / add retry+backoff for
   this host specifically, or accept the gaps?
3. PDF-only task pages (Task 48, and likely others): in scope for a v2, or
   permanently a documented coverage gap?

---

## B. Dropped DOIs (18) — identifier resolution

Every DOI must resolve against DataCite/Crossref or the record is dropped and
logged (invariant). 18 were dropped this run, **all from iea-wind.org task
pages**, all `did-not-resolve`. Two distinct causes:

1. **Extraction over-capture (16):** the DOI strings carry trailing
   markdown/HTML-link junk — e.g.
   `10.1016/j.egyr.2026.109072](https://doi.org/10.1016/j.egyr.2026.109072`
   and `10.3390/su16072899.` (trailing period). These are *malformed*, so they
   correctly fail to resolve — but the underlying DOIs are probably real. The
   extractor is grabbing the markdown link target, not the bare DOI. All 16 came
   from `https://iea-wind.org/task49/`.
2. **Genuinely non-resolving (2):** two clean Zenodo DOIs from
   `task43/t43-publications/` (`10.5281/zenodo.10176528`, `…3524532`) that did
   not resolve — worth a manual check (deleted? embargoed? typo upstream?).

The **invariant held** — no invented/garbled DOI became a record. But we lost ~16
probably-real citations to a parsing bug.
**Decision needed:** fix the ieawind DOI extraction to strip markdown/URL
wrappers and trailing punctuation before validation (recover ~16 records), then
re-run? (The identifier-dimension subagent section below pins the exact
`file:line`.)

---

## C. Unmapped licences (6)

Six upstream licence strings didn't map to a known id and fell back to
`notspecified`:

| identity | raw licence | likely target |
|---|---|---|
| 10.1002/we.1588 | `http://onlinelibrary.wiley.com/termsAndConditions#vor` | publisher terms → `notspecified`? |
| 10.1260/0309-524x.36.1.1 | `https://journals.sagepub.com/page/policies/text-and-data-mining-license` | TDM terms → `notspecified`? |
| 10.2314/kxp:1790028361 | `Open Access` | too vague to map |
| 10.2314/kxp:1790034779 | `Open Access` | too vague to map |
| 10.34657/21046 | `Creative Commons Attribution-NonDerivs 3.0 Germany` | `cc-by-nd-3.0-de` (jurisdiction variant) |
| 10.34657/21591 | `Creative Commons Attribution-NonDerivs 3.0 Germany` | `cc-by-nd-3.0-de` (jurisdiction variant) |

Plus **64 / 291 records (22%) are `notspecified`** overall — see the
Licensing dimension below for the full histogram.
**Decision needed:** extend `harvest/licenses.py` with (a) CC jurisdiction
variants (`…-3.0-de` etc.), and decide policy for (b) vague strings like
"Open Access" and (c) publisher-terms URLs (map to a sentinel, or leave
`notspecified`?).

---

## D. Merge proposal (1) — cross-source duplicate

**What:** `zenodo.20218022` vs `zenodo.20218023` — title similarity 1.00, same
first author (*lozon*), same year (2026), **no shared DOI**. Flagged
`fuzzy-title`, confidence 1.0, **REVIEW REQUIRED — no merge applied** (fuzzy
matches are never auto-applied).
**Why it matters:** almost certainly the same work deposited twice, or two parts
of one deposit / a Zenodo version pair. Consecutive ids are a strong signal.
**Decision needed:** merge these two? And more generally — how should we treat
Zenodo version chains / consecutive-id sibling deposits (see the dedup dimension
below for other candidates found)?

---

<!-- The five sections below are filled from the corpus-mining subagents. -->

## E. Identifiers & resolution (corpus)

This dimension diagnoses the §B dropped-DOI story to the line, and confirms the
record-side gate is otherwise clean.

### E1. Root cause of the markdown over-capture (the §B bug, pinned)
**What:** the DOI body regex `_DOI_BODY` at `harvest/doi.py:65` includes `[`, `]`,
`(`, `)`, `:`, `/`, so when trafilatura renders a citation as
`[10.xxxx/…](https://doi.org/10.xxxx/…)` (because `harvest/extract.py:439` sets
`include_links=True`), `DOI_RE.finditer` greedily consumes the `](https://doi.org/…`
seam. The `#`/`?` split at `doi.py:215-216` strips URL fragments but not the `](`
markdown seam. Extraction is at `harvest/adapters/ieawind.py:634`. Reproduced:
`extract_dois("[10.3390/su16072899.](https://doi.org/10.3390/su16072899)")` →
`['10.3390/su16072899.](https://doi.org/10.3390/su16072899']`. **How common:**
16/18 drops (89%).
**Decision needed:** split candidates on `]`/`)` (or remove `[](): ` from
`_DOI_BODY`) before validation, or set `include_links=False` for the DOI sweep?

### E2. The bug caused **real, invisible data loss** — 7 publications lost ⚠️
**What:** of the 16 over-captured drops, 9 also reached `records/` via a
cleaner link on the same page, but **7 have no record anywhere**:
`10.1016/j.egyr.2026.109072` (Energy Reports), `10.1088/1742-6596/2767/6/062019`,
`…/2875/1/012039`, `…/2875/1/012048` (J. Phys. Conf. Ser.),
`10.1115/iowtc2025-165418`, `10.1115/omae2025-157008` (ASME),
`10.3390/su16072899` (MDPI Sustainability). These are Crossref-registered — the
Zenodo/DataCite adapters never harvest them, so `iea-wind.org/task49/` was their
**only entry point**. This is the highest-value finding of the run.
**Decision needed:** fix forward (a re-harvest recovers them) — is that
sufficient, or do you want a one-off backfill confirmation?

### E3. `did-not-resolve` conflates malformed junk with genuine non-resolution
**What:** all 16 over-captured drops are tagged `did-not-resolve`, identical to
the 2 genuinely-clean drops — because the junk *passes* `normalise_doi()` and so
never gets the `malformed` reason that exists at `doi.py:263-265` (vs `:285`). A
curator can't tell "our parser broke" from "this DOI isn't registered."
**Decision needed:** split drop reasons into
`malformed` / `did-not-resolve` / `resolver-unreachable` in the run report?

### E4. Two clean Zenodo DOIs genuinely failed — and a transport error silently drops
**What:** the only 2 non-junk drops — `10.5281/zenodo.10176528`,
`10.5281/zenodo.3524532` (both from `task43/t43-publications/`) — are well-formed
and absent from `records/`. The run was cold (`cache hits 0 / misses 10`), so a
transient DataCite/Crossref hiccup is plausible, and `doi.py:271-272` treats
*any* transport error as continue→drop (no retry).
**Decision needed:** re-resolve these two by hand (genuine-absence vs transient);
and should a 5xx/transport failure be a distinct outcome that **retries** instead
of dropping a possibly-valid record?

### E5. Cross-registry reconciliation is sound (confirmation, not a defect)
**What:** 28 records carry >1 `source_systems` (e.g. `doi-10-2172-2382797` →
`["crossref","ieawind","osti"]`; 20× `["datacite","zenodo"]`), each a single
record file. **Zero duplicate DOIs and zero duplicate `identity_key`s** across all
291. Source-key/precedence is behaving.
**Decision needed:** none — logged as confirmation.

### E6. 12 DOI-less identities, with a case asymmetry to confirm
**What:** `identity_kind=source` for 12 records (11 GitHub repos + one OSTI biblio
`osti-2482267` with `doi=None`), all with a populated `source_url` — **no
dead-end citations exist**. Note `identity_key` preserves upstream case
(`IEAWindSystems/IEA-15-240-RWT`) while the record `name` slug is lowercased.
**Decision needed:** confirm case-preserving `identity_key` vs lowercase `name` is
intended (so an upstream GitHub rename doesn't fork identity)?

### E7. Record-side DOI gate is clean (confirmation)
**What:** all 279 DOI-bearing records have canonical DOIs — no trailing
punctuation, no `doi:` prefix, no whitespace, no URL/markdown junk, no dupes. The
junk lives only in the drop log. The failure mode is over-*rejection* (E1–E2),
never contamination — the safer direction.

## F. Licensing & access (corpus)

`license_id` distribution — all valid against `harvest/licenses.py` (0 invalid ids
leaked). Anomalies are in the *raw*, *access*, and *mapping-flag* fields.

| `license_id` | count | open? |
|---|---:|---|
| cc-by | 203 | yes |
| notspecified | 64 | unknown |
| apache | 14 | yes |
| bsd-3-clause | 3 | yes |
| cc-nc-nd | 2 | no |
| cc-nc | 2 | no |
| odc-odbl | 1 | yes |
| other-closed | 1 | no |
| cc-zero | 1 | yes |

### F1. A BSD-3 record stored as `notspecified` despite `license_mapped=true`
**What:** `doi-10-11578-dc-20260708-1` (OSTI code) has
`license_raw = 'BSD 3-clause "New" or "Revised" License'`, `license_mapped = true`,
yet `license_id = notspecified`. `map_license()` on that exact string returns
`('bsd-3-clause', True)` today (alias at `harvest/licenses.py:167-168`) —
`mapped=true` + `notspecified` should be impossible for non-empty input
(`:217-225`). **How common:** 1 — the only record where
`map_license(license_raw) != stored license_id` across all 291.
**Decision needed:** stale record from before the alias existed, or a live OSTI
adapter wiring bug? Re-replay from `events/` and confirm it becomes
`bsd-3-clause` — yes/no?

### F2. Licence-vs-access contradictions
**What:** two records where licence and access disagree:
`doi-10-5281-zenodo-3763078` is `license_id=other-closed` (all-rights-reserved)
but `access_status=open`; `doi-10-5281-zenodo-18967947` is `cc-by` but
`access_status=restricted`. **How common:** 3 records carry a non-open access
status (`restricted`×2, `metadata-only`×1).
**Decision needed:** reconcile `access_status` against the licence's `is_open`
flag, or keep them independent facts and add an "access overrides licence" badge?

### F3. The 6 unmapped licences — proposed mappings
Two are publisher-terms URLs (Wiley VOR `#vor`, SAGE TDM policy) — not licences;
two are the vague string `Open Access`; two are `Creative Commons
Attribution-NonDerivs 3.0 Germany` (a jurisdiction port). The mapper's `_norm`
strips `version` and trailing `-3-0` but not `-germany`, and no alias covers the
"NonDerivs" spelling (`harvest/licenses.py:92-93,160-161,107-114,231`).
**Decision needed:** (a) add "NonDerivs" + jurisdiction-suffix aliases →
`cc-by-nd`? (b) map bare "Open Access" → `other-open`, or is that over-asserting
reuse rights (keep `notspecified`)? (c) map publisher-terms URLs → `other-closed`?

### F4. Possible verbatim-source violation — mapped ids in `license_raw`
**What:** 6 records have `license_raw` equal to a register *id* rather than
upstream text (`odc-odbl`, `bsd-3-clause`×3, `other-closed`, `cc-nc`). Some are
genuinely Zenodo vocabulary ids, but `odc-odbl` / `bsd-3-clause` look like *our*
internal ids round-tripped into the raw slot — a potential ADR-0038 violation.
**Decision needed:** audit the Zenodo adapter's licence write-path — is upstream
text preserved verbatim?

### F5. `license_mapped` flag is absent on 57 records (conflated with "no licence")
**What:** `license_mapped` is `true` on 228, `false` on 6, and **absent on 57**
(records with no upstream licence). `map_license(None)` returns `mapped=True`
(`:217-218`) but these omit the flag, so "flag absent" silently conflates with
"licence-was-absent". **Decision needed:** emit `license_mapped` on every record
so absence is unambiguous — yes/no?

### F6. `notspecified` (64, 22%) is heavily OSTI-skewed
**What:** by source: OSTI 31, datacite 14, crossref 13, github 3, ieawind 2,
zenodo 1. 38 of the 64 are also `access_status=open` (readable, no reuse licence
stated). **Decision needed:** acceptable, or should OSTI records attempt a
Crossref/DataCite licence cross-fill before defaulting?

### F7. Embargo & withdrawal machinery is entirely unexercised
**What:** across all 291 records — `embargo_date` present on **0**, `withdrawn`
`false` on **all 291**, `lifecycle_state` `active` on **all 291**;
`access_status` is only ever `open`/`restricted`/`metadata-only` or **absent (140
records)** — never `embargoed` or `unknown`. So the "withdrawn records are kept"
invariant and all date-edge handling have no live example to validate against,
and ~half the corpus has no access signal.
**Decision needed:** add fixtures for a past-embargo, a far-future embargo, and a
withdrawn-but-available record; and should absent `access_status` default to
`unknown`?

## G. Bibliographic metadata quality (corpus)

Encoding is mostly clean (no classic mojibake / U+FFFD / LaTeX found), but there
is one off-topic record, systemic Zenodo duplication, and a lot of raw-HTML and
placeholder leakage.

### G1. Off-topic false positive — a moth-taxonomy paper ⚠️
**What:** `doi-10-5281-zenodo-3789250`, title `"Ennomini Duponchel 1845"` — a
Lepidoptera (geometrid moth) tribe catalogue from *Northern Forestry Centre,
Natural Resources Canada*, harvested via `ieawind`, `resource_kind=publication`.
The only obvious non-wind record in 291.
**Decision needed:** add a relevance/topic gate (or suppress via `local.*`) for
off-domain content?

### G2. Systemic Zenodo concept-DOI vs version-DOI duplication (~48 clusters)
**What:** near-consecutive Zenodo IDs are the same work as a concept record + a
version record, e.g. `zenodo-19706900`/`-19706901` (both "…Task 49 Floating Wind
Innovation Ranking Report"), `-17605818`/`-17605819`/`-17627028` (×3),
`-17641605`/`-21964488` (both "WINPACT"). **~48 title clusters** (≈45 pairs, 3
triples) — dozens of the 99 Zenodo records are duplicates the reader sees as
repeats. (See §I for the dedup dimension's take.)
**Decision needed:** collapse concept/version DOIs into one record (keep latest,
link the rest)?

### G3. Raw HTML / entities / boilerplate left in abstracts (notes)
**What:** 128/291 records carry HTML in `notes` (`<p>`, `<strong>`, `<a href>`,
`<ul><li>`); 81/291 contain literal `\xa0`; 27 start with a literal `"Abstract"`
label. One record is **double-encoded**: `egusphere-egu2020-14253` has
`&amp;amp;lt;p&amp;amp;gt;` (HTML escaped twice). 
**Decision needed:** strip/normalise HTML + boilerplate at ingest (and
iteratively unescape until stable)?

### G4. HTML/entities inside titles
**What:** `doi-10-11583-dtu-31889632` title
`"<b>Common Fallacies in </b><b>Calculating Levelised </b>…"` (source's
fragmented `<b>` runs preserved); 3 Zenodo titles contain `&amp;amp;` (e.g.
"…O&amp;amp;M Costs"). 5 titles total. **Decision needed:** strip tags/decode
entities in titles at ingest?

### G5. ResearchGate titles carry document-type labels & duplicated prefixes
**What:** `doi-10-13140-2-1-3576-5768` → `"Conference Paper Offshore Code
Comparison…"`; `doi-10-13140-rg-2-2-25536-55041` → `"IEA Wind TCP Task 49 The IEA
Wind Task 49 Reference…"` (doubled prefix). All 3 ResearchGate (`10.13140/*`)
records also have `publisher="Unpublished"`.
**Decision needed:** special-case ResearchGate cleanup, or suppress it as a
low-quality source?

### G6. Institution-as-author and very high author counts
**What:** `doi-10-2172-2382797` / `-2447928` mix corporate entries ("Danish
Energy Agency", "…IEA Wind…", "KETEP") into the personal-author list; 4 records
exceed 30 authors (max 38). **Decision needed:** segregate corporate vs personal
authors and cap displayed lists?

### G7. Missing authors (15) — mostly GitHub
**What:** 15 records have no `authors`; 11 are GitHub repos (the adapter yields no
author metadata), the rest a book chapter, `osti-2482267`, and two Zenodo.
**Decision needed:** fall back to GitHub owner/org as author; log author-less DOIs
for review?

### G8. Placeholder junk leaked as real metadata
**What:** `doi-10-2314-kxp-1790028361` author `[{"name":"Unknown"}]`;
`10.34657/21046`,`-21591` notes `"[no abstract available]"`;
`10.2314/kxp:1790034779` notes just `"Diagramm"`. Plus **36 records with empty
notes** (~12%). **Decision needed:** normalise known placeholders to empty; and
backfill empty abstracts (Tier-3) vs show a graceful "no description" state?

### G9. Inconsistent date granularity + one malformed timestamp
**What:** `doi-10-17632-vskgsgnwj8` `published_date="2022-05-31T12:35:31.111"`
(ms timestamp); 25 records are year-only/year-month instead of ISO
`YYYY-MM-DD`; one title/date >2yr mismatch. No future/pre-1990 dates (good).
**Decision needed:** normalise dates to ISO `YYYY-MM-DD` (truncate timestamps,
keep a precision flag)?

### G10. Publisher field oddities
**What:** 17 `publisher=null`; 3 `"Unpublished"` (ResearchGate); bracketed/library
values like `"[MSH Medical School Hamburg]"`, `"Hannover : Technische
Informationsbibliothek"`. **Decision needed:** normalise publisher strings?

### G11. Short / all-caps / long / typographic titles
**What:** short codes (`"WINPACT"`×2, `"ROMEO Project"`), ALL-CAPS
(`"JAM VIRTUAL KICKOFF"`), a 359-char German Task-28 title (UI-overflow risk), and
14 titles with typographic dashes/curly-quotes. **Decision needed:** title
display limits + punctuation normalisation for matching (preserve display)?

## H. Classification & task tagging (corpus)

Schema is valid — every tagged task resolves in `groups.yaml` (0 orphans), every
`owner_org` in `organizations.yaml` (0 orphans). The problems are **coverage** and
**provenance of the task chip**, not validity.

`resource_kind`: publication 120 · other 62 · report 54 · dataset 32 · software 23.
`iea_task`: 131 of 291 records carry **no task**; of the 160 tagged, task-43 (57)
and task-52 (27) dominate.

### H1. 45% of records have no task — and it's exactly the three DOI-metadata sources
**What:** no-task by source — crossref 33/36, osti 26/32, datacite 72/80; but
zenodo 0/99, ieawind 0/31, github 0/13. Task attribution is effectively binary by
source: "membership" sources (Zenodo community, GitHub org, IEA page) tag ~100%;
"candidate-promotion" sources (Crossref/DataCite/OSTI) tag ~13%. The promotion
path (`harvest/dedupe.py:592` `promote_task_candidates`, fed by
`crossref.py:593`/`datacite.py:693`) fired for only 17 records.
**Decision needed:** is `promote_task_candidates` actually invoked in the run
pipeline (and does the promoted tag survive dedupe merges)? Why 17 and not the 43
in H2?

### H2. 43 untagged records name a registered task verbatim in their title
**What:** e.g. `zenodo-19706900` "The IEA Wind **Task 49** Floating Wind…" → `[]`;
`doi-10-2172-1183175` "IEA Wind **Task 26**: …" → `[]`; `doi-10-24406-publica-3869`
"IEA Wind TCP **Task 52**: …" → `[]`. The adapters' own `_TASK_RE`
(`crossref.py:228`) finds a known group in 43 untagged records. (Correctly-untagged:
Task 23/24 titles, whose tasks have no group — "a false chip is worse than a
missing one".)
**Decision needed:** should a task named in the title be a guaranteed promotion?
Currently membership beats explicit self-identification.

### H3. Same title, two records, different task tag — the chip depends on which copy
**What:** 28 twin-groups where an identical title exists as two records (a Zenodo
concept/version pair, or Zenodo-native vs DataCite view of one DOI) that **disagree
on `iea_task`** — e.g. "IEA Wind Task 52 WG1: … metrics" → `zenodo-21619014`
tagged `task-52`, `zenodo-21619015` tagged `[]`. Cleanest proof that a chip is a
harvest-source artefact: the Zenodo copy inherits the community task, the DataCite
copy of the same object inherits nothing. A user faceting by task loses one of each
pair. (Overlaps §I / §G2.)
**Decision needed:** merge twins before materialise, or at least union their task
tags, so classification can't depend on adapter identity.

### H4. Second off-topic false positive — a *Primula* botany paper tagged Task 43 ⚠️
**What:** `doi-10-5281-zenodo-4602200` "FIG. 9 in Typification of *Primula* L. taxa
names (Primulaceae)…" — plant taxonomy, no wind connection — carries a confident
`iea_task=["task-43"]` because it was in Task 43's Zenodo community. (Companion to
the moth paper in §G1.)
**Decision needed:** add a topicality gate; treat as proof that community
membership alone must not confer a task.

### H5. task-43 (the biggest bucket) rides on Zenodo-community membership, not content
**What:** task-43's 57 records = ieawind 28, zenodo 26, github 2, datacite 1; the
Zenodo share rides entirely on the `iea_wind_task_43` community mapping
(`zenodo.py:182,565`), which sweeps in kickoff minutes, the botany paper, and
cross-posted work. The chip means "deposited in this community", weaker than "is
output of this task" — and nothing on the record distinguishes the two.
**Decision needed:** distinguish community-derived from content-evidenced task
tags (they currently share `extraction_method`)?

### H6. Outright title/tag contradiction — a "Task 55" paper tagged Task 43
**What:** `doi-10-5281-zenodo-21873396` "IEA Wind **Task 55** – windIO and
interoperability" is tagged `iea_task=["task-43"]` (cross-posted into the Task 43
community). Also `zenodo-15290993` "IEA Wind Task **44 and 52** Workshop…" tagged
only `task-52` (Task 44 dropped — union incomplete). **Decision needed:** when the
title names task X but membership implies Y, which wins — and should the mismatch
be flagged rather than silently resolved to Y?

### H7. `resource_kind: other` (62, 21%) is a dumping ground hiding real software
**What:** `other` is bigger than dataset+software combined — mostly Zenodo
presentations, kickoff/AGM minutes, seminar series. But it also swallows genuine
software: `zenodo-3823877` "mocalum: python package for Monte-Carlo lidar
uncertainty modeling" → `other`; `zenodo-3923536` "e-WindLidar toolbox" → `other`
(`extract.py:224` defaults kind to `other`). **Decision needed:** add a
`presentation`/`event` kind (or fold minutes/talks into `publication`), and
reclassify Zenodo software releases as `software`?

### H8. 184 records (63%) have empty `tags`, split cleanly by source
**What:** `tags:[]` for crossref 36/36, github 13/13, ieawind 23/31, zenodo 62/99,
datacite 48/80 — only OSTI reliably carries tags. The two largest sources
contribute zero, so tag-faceting covers ~a third of the catalogue.
**Decision needed:** acceptable for v1, or derive tags (container title, task,
abstract keywords) so coverage isn't a function of source?

### H9. 27 records owned by OST (the curator's own institution), inferred from affiliation
**What:** `owner_org=ost` on 27 harvested records (13 datacite, 14 zenodo), all
Task 43 culture/digitalisation material. `organizations.yaml` describes `ost` as
the *curator/repo* owner, not a harvest-attribution target — so the harvest is
self-assigning ownership from author affiliation. (Catch-alls: `zenodo-community`
108, `unattributed` 48 → 156/291 have no real institutional owner.)
**Decision needed:** confirm OST-by-affiliation is intended (and update the
`organizations.yaml` note), or is this accidental self-assignment?

### H10. Multi-task union works — 4 records, all sensible (no action)
`[task-37,task-55]` reference turbine, `[task-36,task-51]` forecasting,
`[task-32,task-43]` ×2. The union logic is fine; the gap is under-tagging (H1/H2),
not over-unioning.

## I. Cross-source dedup & provenance (corpus)

Provenance hygiene is excellent (0 records with missing provenance, 0
source-system contradictions). The dedup story, however, has one systemic gap
that explains most of the §G2/§H3 duplication.

### I1. The merge-review queue is stale — 1 proposal on disk, 51 detectable ⚠️
**What:** `state/merge-proposals.json` lists 1 proposal (from the earlier
30-record run) with `"merges": []`. Re-running the shipped detector over the
*current* `events/` returns **51 candidates: 50 fuzzy-title + 1 automatic
related-identifier merge**. `run` does not invoke `dedupe` (separate verb), so the
queue was never regenerated after the 291-record harvest — the curator's review
list shows 2% of what the code flags.
**Decision needed / action:** ✅ **Done** — I re-ran `python -m harvest dedupe`
(no `--apply`); `state/merge-proposals.json` now holds **50 fuzzy-title proposals +
1 automatic (DTU) merge candidate**, reflecting the full 291-record corpus. Nothing
was merged (no `--apply`).

### I2. Zenodo's version vocabulary falls through — every version chain is a silent dup ⚠️
**What:** related-identifier census: **`IsVersionOf` ×170, `HasVersion` ×39**.
Neither is in `SAMENESS_/SUPERSEDED_BY_/SUPERSEDES_RELATIONS`
(`harvest/dedupe.py:96-119`) — the only version relation recognised is
`IsNewVersionOf`, which Zenodo **never emits**. Result: **51 pairs where both the
concept DOI and a version DOI are catalogued as separate records** (e.g.
`20218022 HasVersion 20218023`). The auto-reconciler was built to collapse exactly
this, but the real vocabulary bypasses it, leaving it all to the fuzzy pass (which
only *proposes*). **This one fix resolves the bulk of the 51.**
**Decision needed:** add `isversionof`/`hasversion` to a version-relation set and
auto-merge version DOIs onto the concept (root) DOI?

### I3. Cross-year version chains escape even the fuzzy fallback
**What:** the fuzzy pass buckets on `(first-author surname, year)`
(`dedupe.py:416-421`), so a version chain crossing a year boundary is never
compared — e.g. `zenodo.10818798`(2024)⇄`4710168`(2025);
`15778456`&`15784506`(2025)⇄`15191296`(2026) (a triple). These are definitively
linked upstream yet invisible to both passes. Fixed by I2 (relation merge is
year-agnostic).

### I4. Cross-registry duplicate hidden by author-name bucketing
**What:** `10.1088/1742-6596/2875/1/012009` "IEA Wind Task 49: Reference Site
Conditions…" (crossref+osti, author *creane*) vs `10.2172/2447928` "IEA Wind TCP
Task 49: Reference Site Conditions…" (author normalised to
*nationalrenewableenergylaboratorynrel*) — title similarity **0.971**, no shared
DOI. Different author strings → different buckets → never compared.
**Decision needed:** add a year-only or title-shingle bucket pass (or normalise
institutional authors) so cross-registry dupes with divergent authors surface?

### I5. The DTU automatic merge is detected but unapplied
**What:** `10.11583/dtu.31889632.v1` declares `IsIdenticalTo
10.11583/dtu.31889632` — `IsIdenticalTo` *is* in `SAMENESS_RELATIONS`, so it's a
0.95 automatic `related-identifier` merge, but `merges:[]` on disk and no
`merged_into` annotation exists. **Decision needed:** `dedupe --apply` to record
it (two `annotated` events), or leave DTU as two records?

### I6. Fuzzy-title alone would over-merge — a true-negative to preserve
**What:** `zenodo.13769728` "…Rotor Inflow Benchmarks - **Unstable** Cases"
(doubrawa) vs `zenodo.15747683` "… - **Stable** Cases" (rybchuk) — similarity
0.981 but genuinely distinct companion datasets. Validates "proposals never
auto-merge". **Decision needed:** confirm fuzzy stays proposal-only, and any new
auto-merge (I2) keys strictly on explicit version/identity relations, not
similarity?

### I7. Heterogeneous source_keys, one malformed value
**What:** the same identity mixes timestamp and payload-hash source-keys across
adapters (expected per ADR-0026), but `doi-10-5281-zenodo-18967947` carries a
source_key `3@10.5281/zenodo.21037963` — pointing at a *different* DOI (its
sibling version). Looks like a leaked version marker, not a change token. Churn is
otherwise low (max 4 events/log; no runaway re-scrape). **Decision needed:**
investigate `3@…` — valid key or Zenodo-adapter version-handling bug?

### I8. 33 DOIs are pattern-extracted (regex), not API-returned
**What:** provenance shows `license_id` pattern-inferred on all 291 (mapping-table
based), and **33 DOIs carry `extraction_method: pattern`** — scraped by regex
rather than returned by an API. Not model-produced (invariant intact), but they're
the DOIs most worth spot-checking against DataCite/Crossref. **Decision needed:**
spot-verify the 33 pattern-provenance DOIs resolve?

**Candidate pairs (abridged):** 42 Zenodo concept⇄version pairs on `10.5281/zenodo.`
(primary = lower/concept id), incl. the flagged `20218022/20218023`; 3 cross-year
links / triples (I3); 3 single-work triples (`…rettenmeier`, `…schlipf`,
`…schicker`); 1 cross-registry pair (I4); the DTU relation merge (I5). Full list in
the dedup agent's raw output.

---

## Decisions register

Every open question, triaged by priority. Tick when resolved; answered items
graduate to an ADR / fixture / adapter change.

### P1 — correctness & data loss (fix before this data is considered "good")

- [ ] **§E1/E2** Fix the ieawind DOI extractor (split on `]`/`)` or drop
      `[](): ` from `_DOI_BODY`, `harvest/doi.py:65`) and re-harvest to recover the
      **7 lost publications**.
- [ ] **§I2** Add `isversionof`/`hasversion` to a version-relation set
      (`harvest/dedupe.py:96-119`) and auto-merge version DOIs onto the concept
      DOI — collapses ~50 Zenodo duplicate pairs.
- [ ] **§F1** Investigate the BSD-3 record stored as `notspecified` despite
      `license_mapped=true` (`doi-10-11578-dc-20260708-1`) — stale record or live
      OSTI wiring bug? Re-replay to confirm.
- [ ] **§H1** Confirm `promote_task_candidates` is actually invoked post-dedupe;
      45% of records (all crossref/osti/datacite) carry no task.

### P2 — quality & correctness of what's shown

- [ ] **§G1/H4** Add a topicality gate (or `local.*` suppression) — a moth paper
      and a *Primula* paper are in the catalogue; community membership must not
      auto-confer a task.
- [ ] **§H2/H3/H6** Promote title-named registered tasks; union/merge same-title
      twins so a task chip doesn't depend on which adapter saw the record; flag
      title-vs-membership contradictions (Task 55 tagged Task 43).
- [ ] **§H7** Reclassify Zenodo software (mocalum, e-WindLidar) out of `other`;
      add a `presentation`/`event` kind for minutes/talks (62 records in `other`).
- [ ] **§G3/G4** Strip/normalise HTML + entities in `notes` (128 records) and
      titles (5); iteratively unescape the double-encoded abstract.
- [ ] **§C/F3** Extend `licenses.py`: CC jurisdiction variants (`…-3.0-de` →
      `cc-by-nd`); decide policy for "Open Access" and publisher-terms URLs.
- [ ] **§F2** Reconcile / badge licence-vs-access contradictions (open licence +
      restricted access, and vice versa).
- [ ] **§E4/I5** Merge the DTU `IsIdenticalTo` pair (`dedupe --apply`); re-resolve
      the 2 genuinely-failed Zenodo DOIs (transient vs absent).

### P3 — coverage, hygiene, and follow-ups

- [ ] **§A1** Document `GITHUB_TOKEN` for large local runs; make rate-limit
      under-collection louder than a WARNING.
- [ ] **§A2** Pursue a WDH credential, or formally scope WDH out of v1.
- [ ] **§T1/T2/T3** Run `make extract` if a model is reachable; per-host
      timeout/retry for iea-wind.org; decide PDF-only pages (Task 48) scope.
- [ ] **§E3** Split drop reasons into `malformed`/`did-not-resolve`/`resolver-unreachable`.
- [ ] **§G8/F6** Normalise placeholder strings ("Unknown", "[no abstract
      available]") to empty; decide backfill vs graceful-empty for 36 empty notes.
- [ ] **§G9** Normalise dates to ISO `YYYY-MM-DD` (one ms-timestamp, 25 reduced-precision).
- [ ] **§G6/G7/G10** Segregate corporate authors; GitHub owner-as-author fallback;
      normalise publisher strings.
- [ ] **§H8** Decide whether to derive `tags` (184 records have none).
- [ ] **§H9** Confirm OST-by-affiliation ownership is intended (27 records).
- [ ] **§I4** Add a year-only / title-shingle dedup bucket for cross-registry dupes.
- [ ] **§I7** Investigate the malformed source_key `3@10.5281/zenodo.21037963`.
- [ ] **§I8/F5** Spot-verify the 33 pattern-extracted DOIs; add embargo/withdrawn
      fixtures (the machinery is entirely unexercised).
- [ ] **§F7** Default absent `access_status` to `unknown`; emit `license_mapped`
      on every record.

### Corpus-integrity confirmations (no action — recorded as passing)

- CKAN-compat gate: **0 violations**. Site builds; 291 pages indexed; 32 typed `Dataset`.
- **0** bad DOIs in records; **0** duplicate DOIs/identity_keys; **0** provenance contradictions.
- Cross-registry reconciliation sound (28 multi-source records, no wrong-work merges).
- Fuzzy-title dedup correctly stays proposal-only (the Stable/Unstable pair proves it must).
