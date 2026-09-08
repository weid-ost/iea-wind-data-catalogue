/**
 * Reading `vocabulary.yaml`: labels, plurals, definitions and the parent of
 * every specific type.
 *
 * Nothing in the site hardcodes a label or an explanation any more. A chip's
 * text, its tooltip and the About page's Definitions section all resolve
 * through here, so a term shown to a reader always has a definition behind it
 * and the two cannot drift (ADR-0040). Adding a value to the vocabulary is
 * therefore enough to make it render correctly everywhere.
 */
import {
  AUTHORITIES,
  RESOURCE_KINDS,
  RESOURCE_TYPES,
  ACCESS_STATUSES,
  TERMS,
} from '../vocabulary.mjs';

export interface Authority {
  title: string;
  url?: string;
  note?: string;
}

export interface VocabEntry {
  name: string;
  label: string;
  definition: string;
  /** Only on `resource_types`: the coarse kind this sits under. */
  kind?: string;
  /** Only on `resource_kinds`: the written-out plural ("software", not "softwares"). */
  plural?: string;
  /** Key into `authorities` — who defines the term, when it is not ours to define. */
  authority?: string;
}

export const authorities = AUTHORITIES as Record<string, Authority>;
export const resourceKinds = RESOURCE_KINDS as VocabEntry[];
export const resourceTypes = RESOURCE_TYPES as VocabEntry[];
export const accessStatuses = ACCESS_STATUSES as VocabEntry[];
export const terms = TERMS as VocabEntry[];

const index = (entries: VocabEntry[]) => new Map(entries.map((e) => [e.name, e]));

const kindsByName = index(resourceKinds);
const typesByName = index(resourceTypes);
const statusesByName = index(accessStatuses);
const termsByName = index(terms);

export const kindEntry = (name?: string): VocabEntry | undefined =>
  name ? kindsByName.get(name) : undefined;
export const typeEntry = (name?: string): VocabEntry | undefined =>
  name ? typesByName.get(name) : undefined;
export const statusEntry = (name?: string): VocabEntry | undefined =>
  name ? statusesByName.get(name) : undefined;
export const termEntry = (name?: string): VocabEntry | undefined =>
  name ? termsByName.get(name) : undefined;

/** The coarse kind a specific type sits under — "report" for "best-practice". */
export const parentKind = (type?: string): string | undefined => typeEntry(type)?.kind;

/** The specific types under one kind, in register order. */
export const typesOfKind = (kind: string): VocabEntry[] =>
  resourceTypes.filter((entry) => entry.kind === kind);

/**
 * A label, falling back to the raw value. Falling back rather than throwing is
 * deliberate: a record harvested against a newer vocabulary than the site was
 * built with should render its raw value, not an empty chip.
 */
const labelOf = (entry: VocabEntry | undefined, name?: string): string =>
  entry?.label ?? name ?? '';

export const kindLabel = (name?: string): string => labelOf(kindEntry(name), name);
export const typeLabel = (name?: string): string => labelOf(typeEntry(name), name);
export const statusLabel = (name?: string): string => labelOf(statusEntry(name), name);
export const termLabel = (name?: string): string => labelOf(termEntry(name), name);

export const kindDefinition = (name?: string): string => kindEntry(name)?.definition ?? '';
export const typeDefinition = (name?: string): string => typeEntry(name)?.definition ?? '';
export const statusDefinition = (name?: string): string => statusEntry(name)?.definition ?? '';
export const termDefinition = (name?: string): string => termEntry(name)?.definition ?? '';

export const authorityOf = (entry?: VocabEntry): Authority | undefined =>
  entry?.authority ? authorities[entry.authority] : undefined;

/** `{dataset: 'Dataset', …}` — for the places that still want a plain map. */
export const asLabels = (entries: VocabEntry[]): Record<string, string> =>
  Object.fromEntries(entries.map((e) => [e.name, e.label]));
