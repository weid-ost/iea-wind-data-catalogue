# Fixtures Catalogue

**Purpose:** the fixture set that drives both the harvester's tests and the `/dev/components` gallery. Every row is a real or hand-built record saved under `fixtures/<source>/<id>.json`, plus its raw upstream payload under `fixtures/<source>/raw/<id>.json` so parsing can be tested without network access.

**Two jobs, one set.** The harvester asserts *"this input produces that record"*; the gallery renders the resulting record to check it doesn't look broken. Fixtures that only exercise one of those are still worth having — a 300-character title is a rendering problem, not a parsing one.

**Convention:** `<src>-NN-<slug>`. Canonical fixtures are `01`. Prefer real upstream payloads captured verbatim (redacted only if necessary) over invented ones; invent only for cases you can't find in the wild. A `b`-suffixed id (`cr-03b`) is a second fixture discharging the same row from the other side of the pair.

**Completeness is a gate.** `tests/test_fixtures.py::TestTheCatalogueMatchesTheTree` compares the ids in these tables against `fixtures/*/*.json` and `fixtures/rendering/ui/*.json` in both directions: every row has a file, every file has a row, and no id is used twice. A row that is deliberately realised somewhere else says so in its Expected-handling cell with the words **realised as `<id>`**, and only then may it have no file of its own. The catalogue drifted seventeen fixtures and one duplicate id before that gate existed (fixture-compliance-05, -06).

**Invented fixtures declare themselves**, in the expectation *and* in the raw payload, and they never wear a live identifier: synthetic DOIs sit on the reserved DataCite test prefix `10.5072`, which does not resolve. See `fixtures/README.md`.

---

## Zenodo

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `zen-01-canonical` | **INVENTED** — published dataset, DOI, ORCID'd creators, license, files, in `iea_wind_task_43`, all in one payload because no single live record carries all of it | Baseline mapping | Full record, `resource_kind: dataset`. Identifiers on the reserved `10.5072` test prefix and the sandbox host: the reference fixture must not wear a real record's ids (fixture-compliance-02) |
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
| `dc-03-type-mismatch` | `resourceTypeGeneral: Other` for something clearly a dataset | Deterministic mapping | `resource_kind: other` — the DataCite type table maps it and nothing else touches it. **No model call.** DataCite is `tier: 1`, and ADR-0024's first rule is that no model may be called on a payload a tier-1 adapter produced, however wrong the upstream type looks. The raw `datacite_types` are retained verbatim as provenance so a later classifier has something to work from |
| `dc-04-non-findable` | `state: registered` (not `findable`) or a draft DOI | Filtering | Skip; log. Don't publish records for DOIs that don't resolve |
| `dc-05-case-variant` | `10.5281/ZENODO.123` vs lowercase | Identity normalisation | Lowercase-normalise before hashing; must not create two records |
| `dc-06-related-identifiers` | `IsVersionOf`, `IsSupplementTo`, `IsPartOf` populated | Relationship extraction | Use for version resolution and paper↔dataset linking; don't create records for the targets |
| `dc-07-publisher-object` | Publisher as a structured object (schema 4.5+) rather than a string | Schema version tolerance | Handle both shapes |
| `dc-08-duplicate-registration` | Same work registered by Zenodo *and* an institutional repository | Dedup without a shared DOI | Fuzzy match on title + first author + year; propose merge, flag for review. **Deferred by design — realised as `x-21-dedupe-fuzzy-proposal`.** Nothing about this case is a DataCite *parsing* problem: both payloads map perfectly and the work only appears twice once two adapters have run, so the expectation belongs in the reconciliation harness, which replays both event streams and asserts the proposal is never auto-applied. `harvest/dedupe.py` cites `dc-08` by name at the site of the rule |
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

The `b`-suffixed fixtures are the other end of a pair. Four Crossref rows only mean something when both sides are held at once — a preprint is only a duplicate of *something* — so each of these is a second captured payload discharging the row beside it, and `tests/test_crossref.py` loads them together.

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `cr-03b-proceedings-article` | A genuine `type: proceedings-article` (AIAA Scitech 2021), container plus `event` | Pairs with `cr-03` | Container is the series; the `event` block does not become a second container |
| `cr-04b-published-version` | The published *Wind Energy Science* article that `cr-04`'s preprint became | Pairs with `cr-04` | This is the record that is listed; the preprint links to it and is not listed separately |
| `cr-05b-entity-in-title` | Title containing the entity `&amp;`, plus a year-and-month `issued` date | Pairs with `cr-05` | Entity decoded once and re-escaped on render; month-precision date, never a fabricated day |
| `cr-07b-retraction-notice` | A retraction *notice* — `update-to` naming a different DOI — which is not itself retracted | Pairs with `cr-07` | No retraction flag on the notice. The flag belongs on the work that was retracted, not on the announcement |

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
| `iea-13-workshop-page` | Real captured workshop write-up whose slug gives nothing away and which cites a real DOI | The Tier-3 escalation itself | Deterministic heuristics return `None`; the page reaches the model; the committed cache entry (`claude-fable-5`) classifies it `event`, zero records, one `page_not_record_bearing` notice — and it replays offline. *(Numbered 13, not 09: it shared `iea-09` with the news page, so "fixture iea-09" named two different rows — fixture-compliance-06.)* |

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
| `x-05-low-confidence` | **INVENTED** — Tier-3 fields the model extracted at confidence 0.42 and 0.38, beside one pattern-extracted field | Provenance display (ADR-0028 §5, §7) | The badge appears on exactly the two `llm` fields and not on the pattern one; the record stays `state: active` — low confidence is badged, never hidden. The *rendering* side of this row is `rep-05-llm-inferred`, which is what `/dev/components` draws |
| `x-06-no-identifier` | No DOI, no stable source ID | Worst-case identity | Deterministic hash key; documented as fragile |
| `x-07-cache-miss-no-llm` | **INVENTED** — Tier-3 page with no cache entry and no LLM available | §3.4 degradation (ADR-0031) | The page is deliberately unclassifiable by pattern, so it escalates; `extract()` returns `None` without attempting a call, no cache entry is written, the URL is queued once to `state/pending-extraction.json`, and the run reports `ok: true`. Idempotent: a second pass queues nothing new |
| `x-08-ckan-invalid` | Record whose slug, tag or licence would fail CKAN validation | §2.2 promotion contract | Build fails at the Zod gate |

### Reconciliation fixtures (`x-2N`)

Dedup, link rot and the joins between sources. These arrived with the reconciler rather than with any adapter, and several of them are the artifact that actually discharges a row above — the "realised as" pointers below run both ways.

| ID | Case | Tests | Expected handling |
|---|---|---|---|
| `x-20-dedupe-badge-join` | Zenodo software record and GitHub repository joined by the DOI badge, then merged | Realises `gh-02` end to end | The badge DOI is resolved before it is trusted; one record survives, carrying both `source_url`s, and the merge is **automatic** because the join key is exact |
| `x-21-dedupe-fuzzy-proposal` | The same work in Zenodo and an institutional repository with no shared DOI | Realises `dc-08` | A merge **proposal** written to `state/merge-proposals.json` and never applied. Applying it twice changes nothing |
| `x-22-dedupe-preprint-pair` | A Crossref preprint declaring `IsPreprintOf` the published article | Realises `cr-04` | The published version is listed; the preprint is linked from it, not listed separately |
| `x-23-link-rot` | A dead task page and a dead file link | Realises `iea-12` at record level | Reported in the run report and rendered as a "source link unreachable" note. The link is never removed and the record is never deleted (ADR-0027) |
| `x-24-osti-mandated-duplicate` | An OSTI deposit duplicating a journal article, joined on the DOI it states | Realises `osti-03` | Merged on the stated DOI; OSTI becomes an additional `source_url` |

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
| `r-09-dead-link` | `link-check.json` with a record whose source link no longer responds — the "source link unreachable" note |

Every record in this section is **invented**, and every identifier in it sits on the reserved `10.5072` DataCite test prefix or the Zenodo sandbox host. That was not true until the reconciliation pass: these fixtures were bound to live third-party DOIs, so the gallery rendered a retraction flag over a real, unretracted *Wind Energy* paper and a "withdrawn upstream" banner over a live Zenodo dataset, and attributed both to named researchers who had nothing to do with them (fixture-compliance-01). `tests/test_fixtures.py::test_rendering_fixtures_only_cite_reserved_identifiers` keeps it that way.

### The `rep-NN` family — what *ordinary* looks like

Representative, not pathological: one per source system and one per interesting-but-not-broken state, so the gallery shows normal next to awkward and the site has something to render before `records/` is populated.

| ID | Case |
|---|---|
| `rep-01-lidar-dataset` | The ordinary Zenodo dataset — the shape of `zen-01`, rendered |
| `rep-02-wes-publication` | The ordinary journal article: container, volume, collaboration author |
| `rep-03-github-software` | The ordinary software record: a repository with a DOI badge |
| `rep-04-restricted-access` | Metadata public, files behind an access wall — the availability-honesty state |
| `rep-05-llm-inferred` | Machine-inferred fields carrying the violet provenance badge — the rendering side of `x-05` |
| `rep-06-retracted-publication` | The retraction flag, which must read differently from `r-04`'s withdrawal banner |
| `rep-07-curator-note` | A curator note rendered beside the wrong-but-verbatim upstream value (`x-10`, rendered) |
| `rep-08-embargoed` | An embargo with a future release date |
| `rep-09-hostile-markup` | Hostile upstream strings: a title that breaks out of `<script>`, a description carrying script and an event handler, a curator link with a `javascript:` scheme |

`rep-09-hostile-markup` is not decoration: `site/scripts/check-render.mjs` loads it by name and fails the site build if the render-safety escaping stops neutralising it (scrape-01, site-01, site-02).
