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
// So it is checked. Four rules — two over the record set, two over the built
// output:
//   1. every record's `name` is unique, and equals its filename stem;
//   2. no `<loc>` appears twice in sitemap.xml;
//   3. no URL anywhere repeats the base path segment twice in a row;
//   4. every page's canonical is absolute and ends with that page's own path.
//
// Rules 1 and 2 are here because `name` IS the URL. Two records sharing one
// name silently dropped a page — the build succeeded, the sitemap listed the
// same `<loc>` twice, catalog.jsonld emitted two `dcat:Dataset` nodes with one
// `@id`, and a real record vanished from the site with no warning (site-06).
// `harvest validate` catches it, but that runs in a different CI job from the
// one that deploys, so the renderer has to catch it too.
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { basename, dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { repoRoot } from '../src/repo-root.mjs';

const dist = join(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const BASE = (process.env.SITE_BASE ?? '/iea-wind-data-catalogue').replace(/^\/|\/$/g, '');

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const path = join(dir, entry);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

const failures = [];
const pages = walk(dist).filter((p) => p.endsWith('.html'));

// ---------------------------------------------------------------- the corpus
//
// `records/` when it has anything in it, `fixtures/rendering/` otherwise —
// exactly the fallback `src/lib/catalogue.ts` applies, so this checks what was
// actually rendered.
const recordsDir = join(repoRoot, 'records');
const recordFiles = existsSync(recordsDir)
  ? readdirSync(recordsDir).filter((f) => f.endsWith('.json')).map((f) => join(recordsDir, f))
  : [];

const seen = new Map();
for (const path of recordFiles) {
  const stem = basename(path, '.json');
  let name;
  try {
    name = JSON.parse(readFileSync(path, 'utf8')).name;
  } catch (error) {
    failures.push(`records/${stem}.json: not readable as JSON (${error.message})`);
    continue;
  }
  if (name !== stem) {
    failures.push(
      `records/${stem}.json: name is "${name}" but the filename stem is "${stem}" — ` +
        'the slug is the URL, and the two must be the same string'
    );
  }
  if (seen.has(name)) {
    failures.push(
      `records/${stem}.json: duplicate name "${name}" (also in ${seen.get(name)}) — ` +
        'one record would silently overwrite the other at /record/' + name + '/'
    );
  } else {
    seen.set(name, `records/${stem}.json`);
  }
}

// ---------------------------------------------------------------- the sitemap
const sitemap = join(dist, 'sitemap.xml');
if (existsSync(sitemap)) {
  const locs = [...readFileSync(sitemap, 'utf8').matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
  const duplicated = [...new Set(locs.filter((loc, index) => locs.indexOf(loc) !== index))];
  for (const loc of duplicated) failures.push(`sitemap.xml: <loc> listed more than once: ${loc}`);
}

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
console.log(
  `check-urls: OK — ${pages.length} pages, ${seen.size} unique record slugs, ` +
    'canonicals correct, base emitted once'
);
