// Astro is a RENDERER (ADR-0032). It reads `records/*.json` by glob and never
// writes to them; no framework-specific field is permitted in the record format.
//
// The Zod schema below is the CKAN-compat gate: a malformed record fails
// `astro build`. It lives in `src/ckan.mjs` so the identical schema can be run
// against fixture `x-08-ckan-invalid` from a plain node script
// (`scripts/check-ckan-gate.mjs`), which is how we prove the gate bites.
import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';
import { ckanPackage } from './ckan.mjs';

const records = defineCollection({
  loader: glob({ pattern: '*.json', base: '../records' }),
  schema: ckanPackage,
});

// The gallery's data, and the fallback that lets the site build before the
// first harvest has run. The same gate applies to the record inside the
// wrapper: a rendering fixture CKAN would refuse is a broken fixture.
const rendering = defineCollection({
  loader: glob({ pattern: '*.json', base: '../fixtures/rendering' }),
  schema: z
    .object({
      fixture_id: z.string(),
      fixture_kind: z.literal('record'),
      case: z.string().min(1),
      note: z.string().optional(),
      record: ckanPackage,
      // Optional hand-written event log, so the event-history component has
      // something to render before `events/` is populated.
      events: z.array(z.record(z.any())).default([]),
    })
    .passthrough(),
});

export const collections = { records, rendering };
