/**
 * Tooltip copy for the chips and badges — a title and a one-line description
 * for every filterable value (usage intuitiveness, item 14a). Kept as data so
 * the wording lives in one place and every chip that shows a value shows the
 * same explanation of it, whether it appears on a card or a record page.
 *
 * Task scopes are summarised from the IEA Wind Task titles and remits
 * (iea-wind.org); the canonical title always comes from `groups.yaml` via the
 * register, so an unknown task still gets a sensible, non-empty tooltip.
 */
import { taskShort, taskTitle } from './registers';
import { sourceLabel } from './record';
import {
  kindDefinition,
  kindLabel,
  statusDefinition,
  statusLabel,
  termDefinition,
  termLabel,
  typeDefinition,
  typeEntry,
  typeLabel,
} from './vocabulary';

export interface Tip {
  title: string;
  body: string;
}

/**
 * Every tooltip body below is the definition from `vocabulary.yaml` verbatim —
 * the same sentence the About page's Definitions section prints (ADR-0040).
 * The wording used to live here in parallel with the page that explained it,
 * which is how a chip and its explanation drift apart.
 */
export const kindTip = (kind: string): Tip => ({
  title: `Kind: ${kindLabel(kind)}`,
  body: kindDefinition(kind) || 'The kind of thing this record describes.',
});

/**
 * The specific type below the kind. The title shows the pair — "Report › IEA
 * Wind Recommended Practice" — because the value is only meaningful under its
 * parent, and the reader is looking at a chip that filters on the specific one.
 */
export const resourceTypeTip = (type: string): Tip => {
  const entry = typeEntry(type);
  const parent = entry?.kind ? `${kindLabel(entry.kind)} › ` : '';
  return {
    title: `${parent}${typeLabel(type)}`,
    body: typeDefinition(type) || 'The specific type of thing this record describes.',
  };
};

export const availabilityTip = (status: string): Tip => ({
  title: `Availability: ${statusLabel(status) || statusLabel('unknown')}`,
  body: statusDefinition(status) || statusDefinition('unknown'),
});

export const sourceTip = (system: string): Tip => ({
  title: `${termLabel('source')}: ${sourceLabel(system)}`,
  body: `${termDefinition('source')} This record's metadata was harvested from ${sourceLabel(
    system
  )}; the chip filters the catalogue to everything that came from there.`,
});

/** Where the artifact itself lives, as against where we read about it. */
export const hostTip = (host: string): Tip => ({
  title: `${termLabel('host')}: ${host}`,
  body: termDefinition('host'),
});

/** The banner on a record that was merged away into another. */
export const mergedTip: Tip = {
  title: termLabel('merged-record'),
  body: termDefinition('merged-record'),
};

export const conceptDoiTip: Tip = {
  title: termLabel('concept-doi'),
  body: termDefinition('concept-doi'),
};

/**
 * A concise scope line per Task. Covers every Task present in the 30 records
 * plus the ones the gallery exercises; anything else falls back to the register
 * title, which is always present.
 */
const TASK_SCOPE: Record<string, string> = {
  'task-26': 'Methods and data for comparing the cost of wind energy across countries and technologies.',
  'task-28': 'Understanding and improving the social acceptance of wind energy projects.',
  'task-30': 'Offshore Code Comparison Collaboration Continued (OC5/OC6) — validating offshore wind modelling tools against measurements.',
  'task-32': 'Advancing and standardising the use of wind lidar for resource assessment and turbine measurements.',
  'task-36': 'Improving wind power forecasting methods and how forecasts are used in operations and markets.',
  'task-37': 'Systems engineering and multidisciplinary optimisation of wind turbines and plants.',
  'task-43': 'Digitalization: open data standards and digital workflows across the wind energy lifecycle.',
  'task-49': 'Integrated Design of Floating Wind Arrays (IDEA) — coupled design methods for floating offshore farms.',
  'task-51': 'Forecasting for a weather-driven, high-renewables energy system.',
  'task-52': 'Large-scale deployment of wind lidar for resource assessment and operations.',
  'task-55': 'REFWIND — open reference wind farms for benchmarking models and methods.',
  'task-57': 'JAM — joint aero-structural modelling of wind turbines.',
};

export const taskTip = (task: string): Tip => {
  const short = taskShort(task);
  const full = taskTitle(task);
  const scope = TASK_SCOPE[task];
  // The register title already reads e.g. "Task 43 — Digitalization"; when we
  // have no bespoke scope line, that title is the best short description there
  // is, and it is never empty.
  return {
    title: `IEA Wind ${short}`,
    body: scope ?? (full === short ? 'An IEA Wind Task; see iea-wind.org for its scope.' : full),
  };
};

export const withdrawnTip: Tip = {
  title: 'Withdrawn upstream',
  body: 'The source has removed or retracted this record. The catalogue keeps it, but no longer offers its files.',
};

export const inferredTip = (count: number): Tip => ({
  title: 'Machine-inferred fields',
  body: `${count} field${count === 1 ? ' was' : 's were'} extracted by a model, not read from an API. Each is marked on the record page.`,
});

export const pinnedTip: Tip = {
  title: 'Pinned extraction',
  body: 'A curator corrected the machine extraction; the correction holds even when the source page changes.',
};
