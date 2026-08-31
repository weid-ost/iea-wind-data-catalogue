// URL discipline, as a gate.
//
// The site is deployed to a GitHub Pages *project* site, so `base` is a path
// prefix that every internal href must carry exactly once. Astro's `site` also
// already contains that prefix, which makes "resolve a pathname against the
// site" a trap: do it with the leading slash stripped and the prefix is emitted
// twice. That shipped once — every `<link rel="canonical">` on the site pointed
// at `…/iea-wind-data-catalogue/iea-wind-data-catalogue/…`, a URL that 404s —
// and it was invisible to the a11y gate, to Pagefind and to the tests, because
// nothing on the site follows its own canonical link.
//
// So it is checked. Two rules over the built output:
//   1. no URL anywhere repeats the base path segment twice in a row;
//   2. every page's canonical is absolute and ends with that page's own path.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const dist = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const BASE = (process.env.SITE_BASE ?? '/iea-wind-data-catalogue').replace(/^\/|\/$/g, '');

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const path = join(dir, entry);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

const failures = [];
const pages = walk(dist).filter((p) => p.endsWith('.html'));

for (const path of [...pages, ...walk(dist).filter((p) => /\.(xml|jsonld|css)$/.test(p))]) {
  const rel = relative(dist, path).split('\\').join('/');
  const text = readFileSync(path, 'utf8');

  if (BASE) {
    const doubled = text.match(new RegExp(`/${BASE}/${BASE}(?=[/"'<\\s])`));
    if (doubled) failures.push(`${rel}: base path emitted twice: ${doubled[0]}`);
  }

  if (!path.endsWith('.html')) continue;

  // The page's own site-relative path, as it will be served.
  const own = '/' + rel.replace(/index\.html$/, '');
  const canonical = text.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
  if (!canonical) {
    failures.push(`${rel}: no <link rel="canonical">`);
    continue;
  }
  let url;
  try {
    url = new URL(canonical);
  } catch {
    failures.push(`${rel}: canonical is not absolute: ${canonical}`);
    continue;
  }
  const expected = (BASE ? `/${BASE}` : '') + own;
  if (url.pathname !== expected) {
    failures.push(`${rel}: canonical path is ${url.pathname}, expected ${expected}`);
  }
}

if (failures.length) {
  console.error(`check-urls: FAIL — ${failures.length} bad URL(s) in dist/`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(`check-urls: OK — ${pages.length} pages, canonicals correct, base emitted once`);
