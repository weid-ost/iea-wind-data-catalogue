/**
 * A hand-rolled sitemap. @astrojs/sitemap would be one more integration to
 * migrate later (ADR-0032: "keep the Astro layer thin"), and this is fifteen
 * lines that will still work in a decade.
 *
 * `/dev/components` is deliberately absent — it is `noindex` and
 * `data-pagefind-ignore`.
 */
import type { APIRoute } from 'astro';
import { catalogue } from '../lib/catalogue';
import { extra } from '../lib/record';
import { BROWSE_PAGE_SIZE } from '../lib/paginate';

export const GET: APIRoute = async ({ site }) => {
  const base = (site?.href ?? '/').replace(/\/$/, '');
  const entries = await catalogue();
  // /browse/ is the no-JS path and the crawler's path, so its pagination chain
  // has to be declared, not just its first page (product-e2e-07, site-08). The
  // page size is imported from the same constant the route paginates with, so
  // the two cannot drift.
  const browsePages = Math.max(1, Math.ceil(entries.length / BROWSE_PAGE_SIZE));
  const urls = [
    { loc: `${base}/`, lastmod: undefined },
    { loc: `${base}/search/`, lastmod: undefined },
    ...Array.from({ length: browsePages }, (_, index) => ({
      loc: index === 0 ? `${base}/browse/` : `${base}/browse/${index + 1}/`,
      lastmod: undefined,
    })),
    { loc: `${base}/about/`, lastmod: undefined },
    ...entries.map((entry) => ({
      loc: `${base}/record/${entry.pkg.name}/`,
      lastmod: extra(entry.pkg, 'last_seen')?.slice(0, 10),
    })),
  ];

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls
  .map(
    (url) =>
      `  <url><loc>${url.loc}</loc>${url.lastmod ? `<lastmod>${url.lastmod}</lastmod>` : ''}</url>`
  )
  .join('\n')}
</urlset>
`;
  return new Response(body, { headers: { 'content-type': 'application/xml; charset=utf-8' } });
};
