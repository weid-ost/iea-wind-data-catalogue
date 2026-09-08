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
import {
  accessStatuses,
  asLabels,
  parentKind,
  resourceKinds,
  typeLabel,
} from './vocabulary';

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
 * Hostnames the catalogue can name, for the "Published at" line.
 *
 * `SOURCE_LABELS` above answers a different question — which of our seven
 * adapters read this record — and conflating the two is what made a Zenodo
 * deposit found by reading a Task publication list say "Source: iea-wind.org"
 * with no mention of Zenodo anywhere (issue #2). One is provenance, the other
 * is where the files are; a record page owes the reader both.
 */
const HOST_LABELS: Record<string, string> = {
  'zenodo.org': 'Zenodo',
  'sandbox.zenodo.org': 'Zenodo (sandbox)',
  'github.com': 'GitHub',
  'osti.gov': 'OSTI',
  'www.osti.gov': 'OSTI',
  'wdh.energy.gov': 'Wind Data Hub',
  'a2e.energy.gov': 'Wind Data Hub',
  'iea-wind.org': 'iea-wind.org',
  'doi.org': 'doi.org',
  'dx.doi.org': 'doi.org',
};

export interface Host {
  /** "Zenodo", or the bare hostname when the catalogue has no name for it. */
  label: string;
  url: string;
  hostname: string;
}

const hostnameOf = (url: string): string => {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return '';
  }
};

/**
 * Every distinct place this artifact actually lives, in the order the record
 * lists them, de-duplicated by hostname.
 *
 * This is the link a reader wants: the DOI first when there is one, because it
 * is the citable, persistent route, then each landing page the sources gave us.
 * A record with a Zenodo DOI now says "Published at Zenodo" even when the only
 * system that ever saw it was iea-wind.org.
 */
export function hostsOf(pkg: CkanPackage): Host[] {
  const doi = extra(pkg, 'doi');
  const candidates = [
    ...(doi ? [`https://doi.org/${doi}`] : []),
    ...(pkg.url ? [pkg.url] : []),
    ...(extra(pkg, 'source_url') ? [extra(pkg, 'source_url') as string] : []),
    ...sourceUrlsOf(pkg),
  ];
  const seen = new Set<string>();
  const hosts: Host[] = [];
  for (const url of candidates) {
    const hostname = hostnameOf(url);
    if (!hostname || seen.has(hostname)) continue;
    seen.add(hostname);
    hosts.push({ label: HOST_LABELS[hostname] ?? hostname.replace(/^www\./, ''), url, hostname });
  }
  return hosts;
}

/**
 * Where a reader should go to report a metadata problem. Corrections belong at
 * the source, where the author can actually make them and every other consumer
 * benefits (ADR-0038).
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

/**
 * The availability value a filter uses (the shared `availability=` facet). It
 * is the source's `access_status` when that is a value we have a label for, and
 * `unknown` otherwise — never a guess dressed up as a fact.
 */
export const availabilityOf = (pkg: CkanPackage): string => {
  const status = extra(pkg, 'access_status');
  // Collapse the finer access statuses onto the four shared facet values
  // (open · restricted · embargoed · unknown) so a record's availability chip
  // links to the same bucket the facet filters on. Must match the FACET map in
  // AvailabilityBadge.astro.
  return AVAILABILITY_FACET[status ?? ''] ?? 'unknown';
};

const AVAILABILITY_FACET: Record<string, string> = {
  open: 'open',
  restricted: 'restricted',
  'registration-required': 'restricted',
  embargoed: 'embargoed',
  'metadata-only': 'embargoed',
  unknown: 'unknown',
};

/**
 * Labels come from `vocabulary.yaml` (ADR-0040), which is also what the About
 * page's Definitions section renders. Hardcoding them here is what let a chip
 * say one thing and the page explaining it say another.
 */
export const ACCESS_LABELS: Record<string, string> = asLabels(accessStatuses);

export const RESOURCE_KIND_LABELS: Record<string, string> = asLabels(resourceKinds);

/**
 * The plural of a resource kind, written out — because `${kind}s` produced
 * "5 softwares · 2 others" on the homepage while the facet beside it said
 * "Software" and "Other" (product-e2e-06). Slugs are not English.
 */
export const RESOURCE_KIND_PLURALS: Record<string, string> = Object.fromEntries(
  resourceKinds.map((entry) => [entry.name, entry.plural ?? `${entry.label.toLowerCase()}s`])
);

/**
 * The specific type below `resource_kind` — "Journal article", "IEA Wind
 * Recommended Practice", "Research software". Derived by the harvest from the
 * vocabulary the source already published, so it is present on every record
 * that has a kind at all (ADR-0040).
 */
export const resourceTypeOf = (pkg: CkanPackage): string | undefined =>
  extra(pkg, 'resource_type');

export const resourceTypeLabel = typeLabel;

/**
 * The kind a record should be filed under, preferring the parent of its
 * specific type. The two agree on every record the current harvest wrote; they
 * can differ on one written before the type was derived, and the specific value
 * is the better evidence.
 */
export const resourceKindOf = (pkg: CkanPackage): string | undefined =>
  parentKind(resourceTypeOf(pkg)) ?? extra(pkg, 'resource_kind');

/** "10 reports", "5 software", "1 dataset". */
export function resourceKindCount(kind: string, count: number): string {
  const singular = (RESOURCE_KIND_LABELS[kind] ?? kind).toLowerCase();
  const plural = RESOURCE_KIND_PLURALS[kind] ?? `${singular}s`;
  return `${count} ${count === 1 ? singular : plural}`;
}
