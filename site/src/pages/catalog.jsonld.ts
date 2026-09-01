/**
 * The DCAT export, as a file. DCAT harvesters consume a file anyway, so
 * "read-only DCAT" is barely a loss compared with a write API (plan §5.4).
 */
import type { APIRoute } from 'astro';
import { catalogue } from '../lib/catalogue';
import { dcatCatalogue } from '../lib/jsonld';
import { readLastRun } from '../lib/state';
import { jsonForHtml } from '../safety.mjs';

export const GET: APIRoute = async ({ site }) => {
  const entries = await catalogue();
  const body = dcatCatalogue(
    entries.map((entry) => entry.pkg),
    site?.href ?? '/',
    readLastRun()?.finished_at
  );
  // Same escaping as the inline JSON-LD. A static host may serve a `.jsonld`
  // file as `text/plain` or let a browser sniff it, and a title carrying
  // `</script>` should not depend on that guess (scrape-01). `<` is
  // ordinary JSON: every parser reads back the original string.
  return new Response(`${jsonForHtml(body, 2)}\n`, {
    headers: { 'content-type': 'application/ld+json; charset=utf-8' },
  });
};
