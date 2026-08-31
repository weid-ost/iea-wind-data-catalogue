# Fixtures Catalogue

**Purpose:** the fixture set that drives both the harvester's tests and the `/dev/components` gallery. Every row is a real or hand-built record saved under `fixtures/<source>/<id>.json`, plus its raw upstream payload under `fixtures/<source>/raw/<id>.json` so parsing can be tested without network access.

**Two jobs, one set.** The harvester asserts *"this input produces that record"*; the gallery renders the resulting record to check it doesn't look broken. Fixtures that only exercise one of those are still worth having — a 300-character title is a rendering problem, not a parsing one.

**Convention:** `<src>-NN-<slug>`. Canonical fixtures are `01`. Prefer real upstream payloads captured verbatim (redacted only if necessary) over invented ones; invent only for cases you can't find in the wild.

---

## Zenodo

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `zen-01-canonical` | Published dataset, DOI, ORCID'd creators, license, files, in `iea_wind_task_43` | Baseline mapping | Full record, `resource_kind: dataset` |
| `zen-02-concept-vs-version` | Record with `conceptdoi` and three versions | **The most important Zenodo case** | Catalogue the *concept* DOI; versions become resources. Never one record per version |
| `zen-03-version-metadata-drift` | v2 has a different title and an added author | Version selection | Display latest version's metadata under the concept identity; retain first-seen date |
| `zen-04-software-record` | `resource_type: software`, mirrors a GitHub repo | Cross-source dedup | Merge with the GitHub record; keep both `source_url`s |
| `zen-05-restricted-access` | Metadata public, files restricted | Availability honesty | Record it, mark access status. Never imply the files are downloadable |
| `zen-06-embargoed` | `access_right: embargoed` with a future `embargo_date` | Temporal state | Record with embargo noted; re-check after the date |
| `zen-07-html-description` | Description contains raw HTML, including a `<script>` | Sanitisation | Strip to safe subset before both rendering **and** LLM input |
| `zen-08-no-license` | Licence absent or a free-text string | CKAN `license_id` mapping | Map through the lookup table; unmappable → `notspecified`, flagged |
| `zen-09-many-creators` | 150+ creators | Rendering, not parsing | Truncate with "and N others" in cards; full list on the record page |
| `zen-10-diacritics` | `Søren`, `Müller`, `Ø` in titles and names | Encoding, slug generation | Preserved in display; transliterated in `name` slug |
| `zen-11-multi-community` | Record in two IEA Wind communities | Double-harvest | One record, `iea_task` multi-valued |
| `zen-12-tombstone` | Withdrawn record; DOI resolves to a tombstone page | Deletion policy | `status: withdrawn`, page retained, never deleted |

---

## DataCite

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `dc-01-canonical` | Findable DOI, one title, typed `Dataset` | Baseline | Full record |
| `dc-02-multi-title` | `AlternativeTitle` and `TranslatedTitle` present | Title selection | Primary title only; alternates into a secondary field |
| `dc-03-type-mismatch` | `resourceTypeGeneral: Other` for something clearly a dataset | Classification | LLM classifies `resource_kind`; source type retained as provenance |
| `dc-04-non-findable` | `state: registered` (not `findable`) or a draft DOI | Filtering | Skip; log. Don't publish records for DOIs that don't resolve |
| `dc-05-case-variant` | `10.5281/ZENODO.123` vs lowercase | Identity normalisation | Lowercase-normalise before hashing; must not create two records |
| `dc-06-related-identifiers` | `IsVersionOf`, `IsSupplementTo`, `IsPartOf` populated | Relationship extraction | Use for version resolution and paper↔dataset linking; don't create records for the targets |
| `dc-07-publisher-object` | Publisher as a structured object (schema 4.5+) rather than a string | Schema version tolerance | Handle both shapes |
| `dc-08-duplicate-registration` | Same work registered by Zenodo *and* an institutional repository | Dedup without a shared DOI | Fuzzy match on title + first author + year; propose merge, flag for review |
| `dc-09-nonstandard-rights` | Rights string with no URI and no SPDX match | Licence mapping failure | Unmappable → flagged, not silently dropped |

---

## Crossref

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `cr-01-canonical` | Journal article in *Wind Energy Science* | Baseline | `resource_kind: publication` |
| `cr-02-partial-date` | `date-parts` with year only | Date handling | Year-precision date; never fabricate a month or day |
| `cr-03-proceedings` | *J. Phys. Conf. Series* paper — `type: proceedings-article` | Container semantics | Series as container; volume/issue retained |
| `cr-04-preprint-pair` | Preprint (`posted-content`) with `is-preprint-of` the published article | Duplicate suppression | Prefer the published version; link the preprint, don't list it separately |
| `cr-05-markup-in-title` | Title containing `<i>`, `&amp;`, or LaTeX math | Escaping | Rendered correctly in HTML *and* in JSON-LD |
| `cr-06-collaboration-author` | Author entry with no given name, or a collaboration name | Author model | Tolerate; don't crash on missing `given` |
| `cr-07-retraction` | Work with an `update-to` retraction notice | Integrity | Prominent retraction flag on the record |

---

## GitHub

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `gh-01-canonical` | Active repo in a task org, licence, topics, releases | Baseline | `resource_kind: software` |
| `gh-02-zenodo-badge` | README contains a Zenodo DOI badge | **Free join key** | Extract DOI, merge with the Zenodo record |
| `gh-03-stale-badge` | Badge points at a version DOI, or a DOI that no longer resolves | Badge trust | Resolve before use; resolve-or-drop applies to badges too |
| `gh-04-fork` | Fork of a reference-turbine repo | Noise suppression | Exclude forks by default, or you will catalogue fifty copies |
| `gh-05-archived` | Archived repo | Lifecycle | Mark archived; retain, don't delete |
| `gh-06-no-license` | `license: null` | Legal accuracy | Show "no licence stated" — never infer or default to open |
| `gh-07-renamed` | Repo renamed or transferred since last harvest | Identity stability | Follow the redirect; identity key must survive the rename |
| `gh-08-personal-account` | Task code living in an individual's account, not an org | Discovery | Reachable via topic/keyword search, not org enumeration alone |
| `gh-09-monorepo` | One repo containing several distinct artifacts | Granularity | One record per repo for v1; note the limitation |
| `gh-10-empty` | Template, docs-only or empty repo | Noise | Exclude below a content threshold |

Note: harvest with the Actions `GITHUB_TOKEN` for the 5,000/hr limit rather than the 60/hr unauthenticated one — it's already present in the workflow.

---

## OSTI

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `osti-01-canonical` | DOE-funded report with OSTI ID and DOI | Baseline | `resource_kind: report` |
| `osti-02-no-doi` | OSTI ID only, no DOI | Identity fallback | Key on `source_system\|source_id` |
| `osti-03-mandated-duplicate` | Deposit duplicating a journal article or Zenodo record | Dedup | Merge; OSTI becomes an additional `source_url` |
| `osti-04-metadata-only` | No public full text | Availability | Record with access status; don't imply a download |
| `osti-05-report-number` | Report number is the only stable human-facing identifier | Citation | Surface it on the record page |

---

## iea-wind.org (Tier 2/3)

This is where most extraction bugs will live.

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `iea-01-canonical` | A `/taskNN/` page with a publications list of formatted citations | Baseline DOI sweep | DOIs extracted, resolved, records created from the resolver not the page |
| `iea-02-doi-punctuation` | `...zenodo.1234.` — trailing full stop inside the citation | **The classic regex bug** | Strip trailing punctuation; `10.5281/zenodo.1234` |
| `iea-03-doi-prefixed` | `doi:`, `https://doi.org/`, `dx.doi.org/`, bare `10.x/y` on one page | Prefix normalisation | All four normalise to the same identity |
| `iea-04-doi-linebreak` | DOI split across two lines in the rendered text | Whitespace handling | Rejoin before matching |
| `iea-05-invalid-doi` | Typo'd DOI that doesn't resolve | Resolve-or-drop | Dropped **and logged** — never silently discarded |
| `iea-06-multi-task` | Same DOI cited on Task 43 and Task 49 pages | Multi-attribution | One record, both tasks in `iea_task` |
| `iea-07-no-doi-citation` | Publication listed as title + journal + year only | Fuzzy lookup | Crossref title search; accept only on high-confidence match, else flag |
| `iea-08-renumbered-task` | Page referencing both old and new task numbers (19→54, 34→59) | Task identity | Canonical task number with alias mapping |
| `iea-09-news-page` | Event announcement or news post caught by the crawl | False positive | Must **not** become a record; classification test |
| `iea-10-boilerplate` | Page with heavy nav, cookie banner, footer | LLM input hygiene | trafilatura output contains body text only |
| `iea-11-pdf-only` | Publication list inside a linked PDF, not in HTML | Coverage gap | Out of scope for v1 — record the gap explicitly |
| `iea-12-dead-page` | Completed task whose page 404s or redirects | Link rot | Existing records retained; source marked unreachable |

---

## Wind Data Hub

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `wdh-01-canonical` | Project/dataset entry with instrument, temporal and spatial coverage | Baseline | `resource_kind: dataset` |
| `wdh-02-no-doi` | Identified by project/dataset code only | Identity fallback | `source_system\|source_id` |
| `wdh-03-huge-file-count` | Dataset with tens of thousands of files | Granularity | Catalogue the dataset. **Never enumerate files** |
| `wdh-04-open-ended-coverage` | Ongoing collection, no end date | Temporal model | Null end date, not a fabricated one |
| `wdh-05-legacy-url` | Old `a2e.energy.gov` URL from a citation | URL normalisation | Follow redirect; canonicalise to `wdh.energy.gov` |
| `wdh-06-registration-required` | Download requires an account | Availability honesty | Stated plainly on the record |
| `wdh-07-auth-wall` | Listing endpoint requires a token (pending Spike 4) | Degradation | Adapter disables itself cleanly; existing records untouched |

---

## Cross-cutting

These exercise reconciliation and provenance rather than any single adapter, and they're the ones most likely to catch real bugs.

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `x-01-four-way-merge` | Same artifact from Zenodo, DataCite, GitHub and an iea-wind.org citation | Dedup and provenance | One record; four entries in `source_url` |
| `x-02-annotation-survives` | Local `iea_task` and curator note present; source key changes on re-scrape | §4 namespaces | `source.*` replaced wholesale; `local.*` untouched |
| `x-03-scalar-displacement` | Source begins providing a field previously added locally | §4 collision rule | Source value displaces; displaced value in event log; notice in run report |
| `x-04-set-union` | Zenodo community implies Task 43; Task 49 was hand-attributed | §4 collision rule | Union — hand attribution survives |
| `x-09-pinned-extraction` | Pinned Tier-3 extraction; page content hash changes on re-scrape | §4.3 carve-out | Pin holds **and** a notice fires |
| `x-10-curator-note` | Known-wrong upstream licence with a curator note attached | §4.3 carve-out | Wrong value displayed verbatim; note rendered beside it |
| `x-05-low-confidence` | LLM-extracted fields below the confidence threshold | Provenance display | Visible "machine-inferred" badge in the gallery |
| `x-06-no-identifier` | No DOI, no stable source ID | Worst-case identity | Deterministic hash key; documented as fragile |
| `x-07-cache-miss-no-llm` | Tier-3 page with no cache entry and no LLM available | §3.4 degradation | Skipped, queued to `pending-extraction.json`, run succeeds |
| `x-08-ckan-invalid` | Record whose slug, tag or licence would fail CKAN validation | §2.2 promotion contract | Build fails at the Zod gate |

---

## Rendering-only fixtures

Purely for the gallery — these are about layout not parsing, and every one of them is a real thing that will happen.

| ID | Case |
|---|---|
| `r-01-long-title` | 300-character title, no natural break points |
| `r-02-no-description` | Record with title and DOI only |
| `r-03-five-tasks` | Record attributed to five tasks — chip overflow |
| `r-04-withdrawn` | Withdrawn record with a notice banner |
| `r-05-cjk-and-diacritics` | Mixed scripts in author names |
| `r-06-single-result` | Search returning exactly one result |
| `r-07-empty-search` | Search returning nothing |
| `r-08-stale-banner` | `last-run.json` older than 45 days, warning state |
