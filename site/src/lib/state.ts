/**
 * `state/last-run.json` — the heartbeat the harvest writes on every run
 * (ADR-0029), and the only reason anyone finds out the catalogue has gone
 * quiet. Plan §3.3.3: "Nobody checks a CI dashboard for a dormant project; a
 * stale banner on the front page is seen by whoever next visits."
 *
 * Past 45 days the banner is a warning (fixture r-08). It doubles as honest
 * provenance: this is when the catalogue last looked.
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { repoRoot } from '../repo-root.mjs';

export const STALE_AFTER_DAYS = 45;

export interface LastRun {
  ok?: boolean;
  started_at?: string;
  finished_at?: string;
  harvest_version?: string;
  limit?: number;
  events_appended?: number;
  pending_extraction?: number;
  unreachable_sources?: string[];
  dropped_dois?: string[];
  unmapped_licenses?: { identity_key?: string; name?: string; license_raw?: string }[];
  notices?: Record<string, unknown>[];
  cache?: { hits?: number; misses?: number; hit_rate?: number };
  records?: { total?: number; written?: number; unchanged?: number; pruned?: number };
  sources?: Record<string, Record<string, unknown>>;
}

export interface Freshness {
  lastRun?: LastRun;
  finishedAt?: string;
  ageDays?: number;
  stale: boolean;
  pending: number;
  unreachable: string[];
  /** True when there is no run report at all — a fresh checkout, not a stale one. */
  neverRun: boolean;
}

export function readLastRun(): LastRun | undefined {
  const path = join(repoRoot, 'state', 'last-run.json');
  if (!existsSync(path)) return undefined;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as LastRun;
  } catch {
    return undefined;
  }
}

/** Freshness derived from a run report; `now` is injectable so the gallery can
 *  render the stale state from fixture r-08 without waiting 45 days. */
export function freshness(lastRun = readLastRun(), now = new Date()): Freshness {
  if (!lastRun?.finished_at)
    return { lastRun, stale: false, pending: 0, unreachable: [], neverRun: true };
  const finished = new Date(lastRun.finished_at);
  const ageDays = Math.floor((now.getTime() - finished.getTime()) / 86_400_000);
  return {
    lastRun,
    finishedAt: lastRun.finished_at,
    ageDays,
    stale: ageDays >= STALE_AFTER_DAYS,
    pending: lastRun.pending_extraction ?? 0,
    unreachable: lastRun.unreachable_sources ?? [],
    neverRun: false,
  };
}

/**
 * `state/link-check.json` — what `harvest linkcheck` last found.
 *
 * Link rot is the failure mode a catalogue exists to fight, so the result is
 * rendered on the record it belongs to rather than left in a state file nobody
 * opens (product-e2e-05, harvest/CONTRACT.md §9). Absent or unreadable means
 * "not checked", never "fine".
 */
export interface DeadLink {
  url: string;
  status_code?: number | null;
  reason?: string;
  checked_at?: string;
}

export interface LinkCheck {
  started_at?: string;
  finished_at?: string;
  checked?: number;
  dead?: DeadLink[];
  dead_by_record?: Record<string, string[]>;
  unreachable_hosts?: string[];
}

export function readLinkCheck(): LinkCheck | undefined {
  const path = join(repoRoot, 'state', 'link-check.json');
  if (!existsSync(path)) return undefined;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as LinkCheck;
  } catch {
    return undefined;
  }
}

/** The dead links recorded against one record slug, with what came back. */
export function deadLinksFor(name: string, check = readLinkCheck()): DeadLink[] {
  const urls = check?.dead_by_record?.[name] ?? [];
  if (urls.length === 0) return [];
  const byUrl = new Map((check?.dead ?? []).map((link) => [link.url, link]));
  return urls.map((url) => byUrl.get(url) ?? { url });
}

/** The event log for one record, if `events/` has been populated. */
export function readEvents(slug: string): Record<string, unknown>[] {
  const path = join(repoRoot, 'events', `${slug}.jsonl`);
  if (!existsSync(path)) return [];
  return readFileSync(path, 'utf8')
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      try {
        return JSON.parse(line) as Record<string, unknown>;
      } catch {
        return { event_type: 'unreadable', observed_at: '', note: line.slice(0, 80) };
      }
    });
}

/** A rendering fixture from `fixtures/rendering/ui/` (r-07, r-08). */
export function uiFixture<T>(id: string): T {
  return JSON.parse(
    readFileSync(join(repoRoot, 'fixtures', 'rendering', 'ui', `${id}.json`), 'utf8')
  ) as T;
}

export function hasRecords(): boolean {
  const dir = join(repoRoot, 'records');
  return existsSync(dir) && readdirSync(dir).some((f) => f.endsWith('.json'));
}
