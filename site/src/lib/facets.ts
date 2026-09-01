/**
 * Facet values, computed at build time from the record set so the filter list
 * exists in the built HTML. Pagefind then supplies live counts at runtime from
 * the `data-pagefind-filter` attributes on the record pages — the two must use
 * the same facet names, which is why they are declared here once.
 */
import type { CatalogueEntry } from './catalogue';
import {
  extra,
  sourceLabel,
  tasksOf,
  licenseTitleOf,
  yearOf,
  availabilityOf,
  RESOURCE_KIND_LABELS,
  ACCESS_LABELS,
} from './record';
import { organizationTitle, taskShort } from './registers';

export interface FacetValue {
  value: string;
  label: string;
  count: number;
}

export interface Facet {
  name: string;
  legend: string;
  values: FacetValue[];
}

/**
 * The facets the plan names — task, resource kind, year, licence, source,
 * institution — plus availability, which the catalogue page also filters on
 * (the shared filter vocabulary: `availability=open|restricted|embargoed|…`).
 */
export function facetsFor(entries: CatalogueEntry[]): Facet[] {
  const tally = (
    pick: (entry: CatalogueEntry) => string[],
    label: (value: string) => string
  ): FacetValue[] => {
    const counts = new Map<string, number>();
    for (const entry of entries)
      for (const value of pick(entry)) if (value) counts.set(value, (counts.get(value) ?? 0) + 1);
    return [...counts.entries()]
      .map(([value, count]) => ({ value, label: label(value), count }))
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
  };

  return [
    { name: 'task', legend: 'IEA Wind Task', values: tally((e) => tasksOf(e.pkg), taskShort) },
    {
      name: 'kind',
      legend: 'Resource kind',
      values: tally(
        (e) => [extra(e.pkg, 'resource_kind') ?? ''],
        (v) => RESOURCE_KIND_LABELS[v] ?? v
      ),
    },
    { name: 'year', legend: 'Year', values: tally((e) => [yearOf(e.pkg)], (v) => v) },
    {
      name: 'licence',
      legend: 'Licence',
      values: tally((e) => [e.pkg.license_id], (v) => licenseTitleOf({ license_id: v } as never)),
    },
    {
      name: 'source',
      legend: 'Source',
      values: tally((e) => sourceSystems(e), sourceLabel),
    },
    {
      name: 'institution',
      legend: 'Institution',
      values: tally((e) => [e.pkg.owner_org ?? ''], (v) => organizationTitle(v) ?? v),
    },
    {
      name: 'availability',
      legend: 'Availability',
      values: tally((e) => [availabilityOf(e.pkg)], (v) => ACCESS_LABELS[v] ?? v),
    },
  ].filter((facet) => facet.values.length > 0);
}

function sourceSystems(entry: CatalogueEntry): string[] {
  const raw = (entry.pkg.extras ?? []).find((e) => e.key === 'source_systems')?.value;
  if (raw) {
    try {
      return JSON.parse(raw) as string[];
    } catch {
      /* fall through to the single-system extra */
    }
  }
  const single = extra(entry.pkg, 'source_system');
  return single ? [single] : [];
}
