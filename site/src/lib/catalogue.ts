/**
 * Loading the catalogue.
 *
 * `records/*.json` is canonical and derived from `events/` (ADR-0037). Astro
 * globs it through the content collection, whose Zod schema is the CKAN-compat
 * gate — a malformed record fails the build.
 *
 * Before the first harvest, `records/` is empty. Rather than build an empty
 * site (which would hide every rendering bug until the day it matters) the
 * catalogue falls back to `fixtures/rendering/`, and says so on the homepage.
 * The fallback is all-or-nothing: one real record and the fixtures disappear.
 */
import { getCollection } from 'astro:content';
import type { CkanPackage } from './record';

export interface CatalogueEntry {
  pkg: CkanPackage;
  /** Where this came from: a real record, or a rendering fixture standing in. */
  origin: 'records' | 'fixtures';
  /** The fixture id, when `origin` is `fixtures` — used to label the gallery. */
  fixtureId?: string;
  fixtureCase?: string;
  fixtureNote?: string;
  /** Hand-written event log carried by a fixture, when `events/` has none. */
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

/** Every record the site renders, sorted by last-seen date, newest first. */
export async function catalogue(): Promise<CatalogueEntry[]> {
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
  return (await catalogue()).find((entry) => entry.pkg.name === name);
}

function lastSeen(entry: CatalogueEntry): string {
  return (entry.pkg.extras ?? []).find((e) => e.key === 'last_seen')?.value ?? '';
}

const byRecency = (a: CatalogueEntry, b: CatalogueEntry): number =>
  lastSeen(b).localeCompare(lastSeen(a)) || a.pkg.title.localeCompare(b.pkg.title);
