/**
 * The DCAT export, as a file. DCAT harvesters consume a file anyway, so
 * "read-only DCAT" is barely a loss compared with a write API (plan §5.4).
 */
import type { APIRoute } from 'astro';
import { catalogue } from '../lib/catalogue';
import { dcatCatalogue } from '../lib/jsonld';
import { readLastRun } from '../lib/state';

export const GET: APIRoute = async ({ site }) => {
  const entries = await catalogue();
  const body = dcatCatalogue(
    entries.map((entry) => entry.pkg),
    site?.href ?? '/',
    readLastRun()?.finished_at
  );
  return new Response(`${JSON.stringify(body, null, 2)}\n`, {
    headers: { 'content-type': 'application/ld+json; charset=utf-8' },
  });
};
