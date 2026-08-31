/**
 * Reading a CKAN package dict, which is all the site ever does with data.
 *
 * `extras` is CKAN's string-valued custom-field block: lists and objects are
 * JSON *inside* the string (harvest/CONTRACT.md §7). Every accessor below
 * decodes that, and every one of them tolerates absence — an absent extra and
 * an empty one mean different things on a record page, so nothing here invents
 * a default that would make "we don't know" look like "no".
 */
import { LICENSES } from '../licenses.mjs';

export type Extras = Record<string, string>;

export interface Provenance {
  extraction_method: 'api' | 'pattern' | 'llm';
  source_system?: string;
  model?: string;
  prompt_version?: string;
  confidence?: number;
  pinned?: boolean;
}

export interface Author {
  name: string;
  orcid?: string;
  affiliation?: string;
}

export interface CuratorNote {
  note: string;
  field?: string;
  added_at?: string;
}

export interface CkanPackage {
  name: string;
  title: string;
  notes?: string;
  license_id: string;
  tags?: { name: string }[];
  extras?: { key: string; value: string }[];
  resources?: { url: string; name?: string; format?: string; description?: string }[];
  groups?: { name: string }[];
  owner_org?: string;
  url?: string;
  version?: string;
  state?: string;
  private?: boolean;
}

export const extrasOf = (pkg: CkanPackage): Extras =>
  Object.fromEntries((pkg.extras ?? []).map((e) => [e.key, e.value]));

export const extra = (pkg: CkanPackage, key: string): string | undefined => {
  const value = extrasOf(pkg)[key];
  return value === undefined || value === '' ? undefined : value;
};

/** A JSON-encoded extra, decoded. Returns `fallback` when absent or unparseable. */
export function jsonExtra<T>(pkg: CkanPackage, key: string, fallback: T): T {
  const raw = extra(pkg, key);
  if (raw === undefined) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export const boolExtra = (pkg: CkanPackage, key: string): boolean => extra(pkg, key) === 'true';

export const provenanceOf = (pkg: CkanPackage): Record<string, Provenance> =>
  jsonExtra<Record<string, Provenance>>(pkg, 'provenance', {});

export const authorsOf = (pkg: CkanPackage): Author[] => jsonExtra<Author[]>(pkg, 'authors', []);

export const tasksOf = (pkg: CkanPackage): string[] => jsonExtra<string[]>(pkg, 'iea_task', []);

export const sourceUrlsOf = (pkg: CkanPackage): string[] =>
  jsonExtra<string[]>(pkg, 'source_urls', []);

export const sourceSystemsOf = (pkg: CkanPackage): string[] => {
  const systems = jsonExtra<string[]>(pkg, 'source_systems', []);
  if (systems.length) return systems;
  const single = extra(pkg, 'source_system');
  return single ? [single] : [];
};

export const curatorNotesOf = (pkg: CkanPackage): CuratorNote[] =>
  jsonExtra<CuratorNote[]>(pkg, 'curator_notes', []);

export const localLinksOf = (pkg: CkanPackage): { url: string; label?: string }[] =>
  jsonExtra<{ url: string; label?: string }[]>(pkg, 'local_links', []);

export const relatedIdentifiersOf = (
  pkg: CkanPackage
): { relation?: string; identifier: string; identifier_type?: string }[] =>
  jsonExtra(pkg, 'related_identifiers', []);

export const isWithdrawn = (pkg: CkanPackage): boolean =>
  extra(pkg, 'lifecycle_state') === 'withdrawn' || boolExtra(pkg, 'withdrawn');

export const isSuppressed = (pkg: CkanPackage): boolean => boolExtra(pkg, 'suppressed');

/**
 * A retraction is a fact the *source* states, via an `IsRetractedBy` /
 * `IsRetractionOf` related identifier (fixture cr-07). It is not withdrawal:
 * a retracted paper is still at the publisher.
 */
export const retractionOf = (pkg: CkanPackage) =>
  relatedIdentifiersOf(pkg).find((r) => /retract/i.test(r.relation ?? ''));

/** Fields whose value a model inferred. These render the violet badge (x-05). */
export const inferredFields = (pkg: CkanPackage): string[] =>
  Object.entries(provenanceOf(pkg))
    .filter(([, p]) => p.extraction_method === 'llm')
    .map(([field]) => field)
    .sort();

export const licenseTitleOf = (pkg: CkanPackage): string =>
  (LICENSES as Record<string, string>)[pkg.license_id] ?? pkg.license_id;

/** The publication year, or '' — never a fabricated month (fixture cr-02). */
export const yearOf = (pkg: CkanPackage): string =>
  (extra(pkg, 'published_date') ?? '').slice(0, 4);

/**
 * A date exactly as precise as the source stated it. `2024` stays `2024`;
 * `2024-06-01` becomes `1 June 2024`.
 */
export function formatDate(value?: string): string {
  if (!value) return '';
  if (/^\d{4}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  if (/^\d{4}-\d{2}$/.test(value))
    return date.toLocaleDateString('en-GB', { year: 'numeric', month: 'long', timeZone: 'UTC' });
  return date.toLocaleDateString('en-GB', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    timeZone: 'UTC',
  });
}

/** "Müller, S. Ø., Okafor, C. and 148 others" — full list lives on the record page. */
export function authorSummary(authors: Author[], limit = 3): string {
  if (authors.length === 0) return '';
  const shown = authors.slice(0, limit).map((a) => a.name);
  const rest = authors.length - shown.length;
  if (rest === 0)
    return shown.length === 1 ? shown[0] : `${shown.slice(0, -1).join(', ')} and ${shown.at(-1)}`;
  return `${shown.join(', ')} and ${rest} other${rest === 1 ? '' : 's'}`;
}

/** Strip tags for meta descriptions and JSON-LD; the stored HTML is already sanitised. */
export const plainText = (html?: string): string =>
  (html ?? '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ')
    .trim();

export const truncate = (text: string, length: number): string =>
  text.length <= length ? text : `${text.slice(0, length - 1).trimEnd()}…`;

/**
 * A citation string for the copy button. Deliberately plain — the catalogue
 * cites what the source states and does not invent a style guide's worth of
 * punctuation it cannot verify.
 */
export function citationFor(pkg: CkanPackage): string {
  const authors = authorsOf(pkg).map((a) => a.name);
  const people =
    authors.length === 0 ? '' : authors.length > 5 ? `${authors[0]} et al. ` : `${authors.join('; ')}. `;
  const year = yearOf(pkg);
  const doi = extra(pkg, 'doi');
  const container = extra(pkg, 'container');
  const publisher = extra(pkg, 'publisher');
  // For a report with no DOI the laboratory's number is what a reader cites,
  // so it goes in the citation rather than only in the metadata list.
  const reportNumber = extra(pkg, 'report_number');
  const version = pkg.version ? ` (version ${pkg.version})` : '';
  return [
    `${people}${year ? `(${year}). ` : ''}${pkg.title}${version}.`,
    container ? ` ${container}.` : publisher ? ` ${publisher}.` : '',
    reportNumber ? ` ${reportNumber}.` : '',
    doi ? ` https://doi.org/${doi}` : extra(pkg, 'source_url') ? ` ${extra(pkg, 'source_url')}` : '',
  ]
    .join('')
    .trim();
}

/** Human label for a source system, used by the source badge and the facet. */
export const SOURCE_LABELS: Record<string, string> = {
  zenodo: 'Zenodo',
  datacite: 'DataCite',
  crossref: 'Crossref',
  github: 'GitHub',
  osti: 'OSTI',
  wdh: 'Wind Data Hub',
  ieawind: 'iea-wind.org',
};

export const sourceLabel = (system: string): string => SOURCE_LABELS[system] ?? system;

/**
 * Where a reader should go to report a metadata problem. Corrections belong at
 * the source, where the author can actually make them and every other consumer
 * benefits (plan §4.2).
 */
export function reportIssueUrl(system: string, pkg: CkanPackage): string | undefined {
  const url = sourceUrlsOf(pkg).find((u) => matchesSystem(u, system)) ?? extra(pkg, 'source_url');
  if (!url) return undefined;
  if (system === 'github') return `${url.replace(/\/$/, '')}/issues`;
  return url;
}

function matchesSystem(url: string, system: string): boolean {
  const host: Record<string, RegExp> = {
    zenodo: /zenodo\.org/,
    github: /github\.com/,
    osti: /osti\.gov/,
    wdh: /(wdh|a2e)\.energy\.gov/,
    ieawind: /iea-wind\.org/,
    crossref: /doi\.org|crossref/,
    datacite: /doi\.org|datacite/,
  };
  return (host[system] ?? /$^/).test(url);
}

export const ACCESS_LABELS: Record<string, string> = {
  open: 'Open access',
  restricted: 'Restricted',
  embargoed: 'Embargoed',
  'registration-required': 'Registration required',
  'metadata-only': 'Metadata only',
  unknown: 'Access unknown',
};

export const RESOURCE_KIND_LABELS: Record<string, string> = {
  dataset: 'Dataset',
  publication: 'Publication',
  software: 'Software',
  report: 'Report',
  model: 'Model',
  other: 'Other',
};
