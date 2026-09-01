/**
 * `groups.yaml` (= the IEA Wind Tasks) and `organizations.yaml`, which are
 * canonical data in the repository rather than site configuration. Task chips,
 * task labels and the institution facet all resolve through here; never
 * string-match a task number, because iea-wind.org renumbered several
 * (19 -> 54, 34 -> 59) and both numbers appear in the wild.
 */
import { GROUPS, ORGANIZATIONS } from '../ckan.mjs';

export interface Group {
  name: string;
  title: string;
  url?: string;
  state?: string;
  aliases?: string[];
}

export interface Organization {
  name: string;
  title: string;
  url?: string;
  /** The Research Organization Registry IRI, when the register carries one. */
  ror?: string;
  country?: string;
}

export const groups: Group[] = GROUPS as Group[];
export const organizations: Organization[] = ORGANIZATIONS as Organization[];

const groupsByName = new Map(groups.map((g) => [g.name, g]));
const orgsByName = new Map(organizations.map((o) => [o.name, o]));

const aliases = new Map<string, string>();
for (const group of groups) for (const alias of group.aliases ?? []) aliases.set(alias, group.name);

/** Resolve a task name or one of its aliases to the canonical group name. */
export const canonicalGroup = (name: string): string => {
  const key = name.trim().toLowerCase();
  return groupsByName.has(key) ? key : (aliases.get(key) ?? key);
};

export const group = (name: string): Group | undefined => groupsByName.get(canonicalGroup(name));

/** "Task 43 — Digitalization", or the raw name if the register does not know it. */
export const taskTitle = (name: string): string => group(name)?.title ?? name;

/** "Task 43" — the short form, for chips. */
export const taskShort = (name: string): string => {
  const title = taskTitle(name);
  const match = title.match(/^(Task [\d/]+)/);
  return match ? match[1] : name;
};

export const taskUrl = (name: string): string | undefined => group(name)?.url;

export const organization = (name?: string): Organization | undefined =>
  name ? orgsByName.get(name) : undefined;

export const organizationTitle = (name?: string): string | undefined =>
  name ? (orgsByName.get(name)?.title ?? name) : undefined;
