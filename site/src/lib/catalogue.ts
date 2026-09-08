/**
 * Loading the catalogue.
 *
 * `data/records/*.json` is canonical and derived from `data/events/` (ADR-0037). Astro
 * globs it through the content collection, whose Zod schema is the CKAN-compat
 * gate — a malformed record fails the build.
 *
 * Before the first harvest, `data/records/` is empty. Rather than build an empty
 * site (which would hide every rendering bug until the day it matters) the
 * catalogue falls back to `data/fixtures/rendering/`, and says so on the homepage.
 * The fallback is all-or-nothing: one real record and the fixtures disappear.
 */
import { getCollection } from 'astro:content';
import { isOutOfScope, isSuppressed, type CkanPackage } from './record';

export interface CatalogueEntry {
  pkg: CkanPackage;
  /** Where this came from: a real record, or a rendering fixture standing in. */
  origin: 'records' | 'fixtures';
  /** The fixture id, when `origin` is `fixtures` — used to label the gallery. */
  fixtureId?: string;
  fixtureCase?: string;
  fixtureNote?: string;
  /** Hand-written event log carried by a fixture, when `data/events/` has none. */
  events: EventLine[];
}

export interface EventLine {
  observed_at: string;
  event_type: string;
  identity_key?: string;
  source_system?: string;
  source_key?: string;
  actor?: string;
  note?: string;
}

let cache: CatalogueEntry[] | undefined;

/**
 * Every record that exists, listed or not, sorted newest first.
 *
 * Use this to *build* pages. A record merged away into another still has a
 * page, a URL and a citation — it is retained, never deleted (ADR-0027,
 * ADR-0041) — so the page generator, the sitemap and `byName` all work from
 * here. Use `catalogue()` for anything that *lists* records.
 */
export async function allEntries(): Promise<CatalogueEntry[]> {
  if (cache) return cache;

  const real = await getCollection('records');
  if (real.length > 0) {
    cache = real.map((entry) => ({
      pkg: entry.data as unknown as CkanPackage,
      origin: 'records' as const,
      events: [],
    }));
  } else {
    cache = (await fixtures()).map((entry) => ({ ...entry, origin: 'fixtures' as const }));
  }
  return (cache = cache.sort(byRecency));
}

/**
 * The records the catalogue *lists*, sorted by publication date, newest first.
 *
 * Suppressed records are excluded. A suppressed record is one the reconciler
 * merged into another — a Zenodo version DOI folded into its concept DOI, a
 * GitHub repository into the Zenodo deposit that releases it. Listing both put
 * the same artifact in the results twice under two DOIs, which is what an IEA
 * Wind board member reported seeing. The merge always intended this ("retained,
 * citable, and out of the listings" — harvest/dedupe.py); the site was the half
 * that never honoured it.
 *
 * Out-of-scope records are excluded too. The catalogue's scope rule (ADR-0043)
 * is that a record must be directly attributed to IEA Wind, cite something in
 * the catalogue, or be cited by something in it; one that meets none of the
 * three is not an IEA Wind record and does not belong in an IEA Wind listing.
 *
 * "Out of the listings" and "deleted" are different things, and the difference
 * is the whole point in both cases: the record keeps its page, its URL and its
 * citation, and that page says why it is not listed.
 */
export async function catalogue(): Promise<CatalogueEntry[]> {
  return (await allEntries()).filter(
    (entry) => !isSuppressed(entry.pkg) && !isOutOfScope(entry.pkg)
  );
}

/** The gallery's own data: always the fixtures, whether or not records exist. */
export async function fixtures(): Promise<CatalogueEntry[]> {
  const collection = await getCollection('rendering');
  return collection
    .map((entry) => ({
      pkg: entry.data.record as unknown as CkanPackage,
      origin: 'fixtures' as const,
      fixtureId: entry.data.fixture_id as string,
      fixtureCase: entry.data.case as string,
      fixtureNote: entry.data.note as string | undefined,
      events: (entry.data.events ?? []) as EventLine[],
    }))
    .sort((a, b) => (a.fixtureId ?? '').localeCompare(b.fixtureId ?? ''));
}

/** True when nothing has been harvested yet and the fixtures are standing in. */
export async function usingFixtures(): Promise<boolean> {
  return (await catalogue()).every((entry) => entry.origin === 'fixtures');
}

export async function byName(name: string): Promise<CatalogueEntry | undefined> {
  return (await allEntries()).find((entry) => entry.pkg.name === name);
}

function extraValue(entry: CatalogueEntry, key: string): string {
  return (entry.pkg.extras ?? []).find((e) => e.key === key)?.value ?? '';
}

// Order by when the work was *published*, newest first — not by `last_seen`
// (the harvest's observation time). A mass harvest stamps `last_seen` across the
// whole corpus inside one run, so it barely varies and the "recency" sort
// collapses to the title tiebreak, scattering genuinely recent work onto the
// last page. `published_date` is present on every record and is what a reader
// means by "most recent". ISO-ish dates sort lexicographically even at mixed
// granularity (year-only sorts to the start of its year); `last_seen` then title
// break ties deterministically. Undated records fall to the end.
const byRecency = (a: CatalogueEntry, b: CatalogueEntry): number =>
  extraValue(b, 'published_date').localeCompare(extraValue(a, 'published_date')) ||
  extraValue(b, 'last_seen').localeCompare(extraValue(a, 'last_seen')) ||
  a.pkg.title.localeCompare(b.pkg.title);
