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
import { ACCESS_LABELS, RESOURCE_KIND_LABELS, sourceLabel } from './record';

export interface Tip {
  title: string;
  body: string;
}

const KIND_BODY: Record<string, string> = {
  dataset: 'A published collection of measured or modelled data.',
  publication: 'A written output such as a journal article, conference paper or thesis.',
  software: 'Code, tools or libraries published for others to reuse.',
  report: 'A technical or project report — often grey literature, frequently without a DOI.',
  model: 'A published simulation model or numerical dataset.',
  other: "A record that doesn't fit the other entry types.",
};

export const kindTip = (kind: string): Tip => ({
  title: `Entry type: ${RESOURCE_KIND_LABELS[kind] ?? kind}`,
  body: KIND_BODY[kind] ?? 'The kind of thing this record describes.',
});

const AVAILABILITY_BODY: Record<string, string> = {
  open: 'Freely accessible — no account, request or embargo stands between you and the files.',
  restricted: 'Access is limited: it may need a request, an approval, or membership at the source.',
  'registration-required': 'You must create an account or sign in at the source before you can access it.',
  embargoed: 'Under embargo now; the source states a date on which access opens.',
  'metadata-only': 'Only the metadata is published — the files themselves are not available here or at the source.',
  unknown: 'The source does not state the access conditions, so the catalogue does not assume them.',
};

export const availabilityTip = (status: string): Tip => ({
  title: `Availability: ${ACCESS_LABELS[status] ?? ACCESS_LABELS.unknown}`,
  body: AVAILABILITY_BODY[status] ?? AVAILABILITY_BODY.unknown,
});

export const sourceTip = (system: string): Tip => ({
  title: `Source: ${sourceLabel(system)}`,
  body: `Harvested from ${sourceLabel(system)}. Filter the catalogue to everything that came from this source.`,
});

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
