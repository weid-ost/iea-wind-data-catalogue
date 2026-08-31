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

export const GET: APIRoute = async ({ site }) => {
  const base = (site?.href ?? '/').replace(/\/$/, '');
  const entries = await catalogue();
  const urls = [
    { loc: `${base}/`, lastmod: undefined },
    { loc: `${base}/search/`, lastmod: undefined },
    { loc: `${base}/browse/`, lastmod: undefined },
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
