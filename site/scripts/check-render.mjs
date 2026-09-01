// The render-safety gate.
//
// Two halves, and both are needed:
//
//   A. **The helpers, exercised.** `src/safety.mjs` is the only thing standing
//      between a hostile upstream string and the deployed page, so this feeds
//      it the attack it was written for — the fixture `rep-09-hostile-markup`,
//      whose title really does contain `</script><img src=x onerror=…>` — and
//      fails if the output could break out. A gate nobody has watched fail is
//      a gate you are guessing about (same reasoning as check-ckan-gate).
//
//   B. **The built output, re-read.** Escaping helps nobody if a template
//      forgets to call it, so `dist/` is checked for what must never appear:
//      a JSON-LD block that does not parse, a `javascript:` href, a script tag
//      inside a record description. Plus the promises the plan makes about the
//      output — six facets (ADR-0023 §3), every dataset typed `Dataset`, no
//      invented DCAT licence, no download list on a withdrawn record.
//
// Findings: scrape-01, site-01, site-02, site-03, product-e2e-02/site-04,
// product-e2e-04, compliance-03.
import { readdirSync, readFileSync, existsSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { jsonForHtml, safeHref, safeHtml } from '../src/safety.mjs';
import { DATASET_KINDS, schemaTypeFor } from '../src/schema-types.mjs';
import { repoRoot } from '../src/repo-root.mjs';

const site = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(site, 'dist');
const failures = [];
const fail = (message) => failures.push(message);

const read = (path) => readFileSync(path, 'utf8');
const walk = (dir) =>
  readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });

// ============================================================ A. the helpers

const HOSTILE_FIXTURE = join(repoRoot, 'fixtures', 'rendering', 'rep-09-hostile-markup.json');
if (!existsSync(HOSTILE_FIXTURE)) {
  fail(
    'fixtures/rendering/rep-09-hostile-markup.json is missing — it is the input this gate ' +
      'exists to prove is neutralised (scrape-01). Never delete it to make the gate pass.'
  );
}

if (existsSync(HOSTILE_FIXTURE)) {
  const fixture = JSON.parse(read(HOSTILE_FIXTURE));
  const record = fixture.record;

  // The fixture must actually carry the attack, or this whole file is theatre.
  if (!record.title.includes('</script>')) {
    fail('rep-09-hostile-markup: the title no longer contains `</script>` — the fixture has been defanged');
  }
  if (!/<script/i.test(record.notes ?? '')) {
    fail('rep-09-hostile-markup: the notes no longer contain a <script> tag — the fixture has been defanged');
  }

  // A.1 — JSON-LD cannot break out of its <script> element.
  const serialised = jsonForHtml({ '@type': 'Dataset', name: record.title, description: record.notes });
  if (/<\/script/i.test(serialised) || serialised.includes('<')) {
    fail('jsonForHtml left a raw `<` in the JSON-LD — a title containing </script> would break out (scrape-01)');
  }
  if (JSON.parse(serialised).name !== record.title) {
    fail('jsonForHtml changed the data it escaped — the JSON-LD no longer says what the record says');
  }

  // A.2 — a description cannot ship script or an event handler.
  const sanitised = safeHtml(record.notes ?? '');
  for (const [pattern, what] of [
    [/<script/i, 'a <script> tag'],
    [/\son[a-z]+\s*=/i, 'an on* event handler'],
    [/javascript:/i, 'a javascript: URL'],
    [/<img/i, 'an <img> tag (outside the allow-list)'],
  ]) {
    if (pattern.test(sanitised)) fail(`safeHtml left ${what} in a harvested description (site-02)`);
  }
  if (!sanitised.includes('<p>')) {
    fail('safeHtml dropped the ordinary markup too — a description is prose and links, and must survive');
  }

  // A.3 — a curator link with a hostile scheme is not linkable.
  const localLinks = JSON.parse(
    (record.extras ?? []).find((extra) => extra.key === 'local_links')?.value ?? '[]'
  );
  if (!localLinks.some((link) => /^\s*javascript:/i.test(link.url))) {
    fail('rep-09-hostile-markup: local_links no longer carries a javascript: URL — the fixture has been defanged');
  }
  for (const link of localLinks) {
    const href = safeHref(link.url);
    if (href !== undefined && !/^(https?|mailto|ftps?):/i.test(href)) {
      fail(`safeHref admitted a link it should have refused: ${link.url} (site-01)`);
    }
  }
}

// Vectors that are not in the fixture but must stay refused.
for (const hostile of [
  'javascript:alert(1)',
  'JaVaScript:alert(1)',
  ' javascript:alert(1)',
  'java\tscript:alert(1)',
  '\u0001javascript:alert(1)',
  'data:text/html,<script>alert(1)</script>',
  'vbscript:msgbox(1)',
  '//evil.example/x',
]) {
  if (safeHref(hostile) !== undefined) fail(`safeHref admitted ${JSON.stringify(hostile)}`);
}
for (const safe of ['https://zenodo.org/records/1', 'mailto:data@example.org', 'ftp://ftp.example.org/x']) {
  if (safeHref(safe) !== safe) fail(`safeHref refused a legitimate link: ${safe}`);
}
if (safeHtml('<a href="java&#115;cript:alert(1)">x</a>').includes('href')) {
  fail('safeHtml kept an entity-encoded javascript: href');
}

// ============================================================ B. the output

if (!existsSync(join(dist, 'index.html'))) {
  console.error('check-render: dist/ is missing — run `npm run build` first.');
  process.exit(1);
}

const htmlFiles = walk(dist).filter((path) => path.endsWith('.html'));
const rel = (path) => relative(dist, path).split('\\').join('/');

// B.1 — every embedded JSON-LD block parses, and contains no raw markup.
const SCRIPT_RE = /<script type="application\/ld\+json">([\s\S]*?)<\/script>/g;
let jsonLdBlocks = 0;
const typesByPage = new Map();
for (const path of htmlFiles) {
  const text = read(path);
  SCRIPT_RE.lastIndex = 0;
  let match;
  while ((match = SCRIPT_RE.exec(text)) !== null) {
    jsonLdBlocks += 1;
    const body = match[1];
    if (/[<>]/.test(body)) {
      fail(`${rel(path)}: JSON-LD contains a raw < or > — it can break out of its <script> element (scrape-01)`);
      continue;
    }
    try {
      const parsed = JSON.parse(body);
      typesByPage.set(rel(path), parsed['@type']);
    } catch (error) {
      fail(`${rel(path)}: JSON-LD does not parse (${error.message})`);
    }
  }
}
if (jsonLdBlocks === 0) fail('no JSON-LD found anywhere in dist/ — record pages must carry it (ADR-0023)');

// B.2 — no hostile href, no script or handler inside a rendered description.
for (const path of htmlFiles) {
  const text = read(path);
  for (const [pattern, what] of [
    [/href\s*=\s*["']?\s*(javascript|data|vbscript|file)\s*:/i, 'a non-linkable URL scheme in an href (site-01)'],
    [/<div class="record__notes[^"]*"[^>]*>(?:(?!<\/div>)[\s\S])*?<script/i, 'a <script> inside a record description (site-02)'],
    [/<div class="record__notes[^"]*"[^>]*>(?:(?!<\/div>)[\s\S])*?\son(?:error|load|click|mouseover)\s*=/i, 'an event handler inside a record description (site-02)'],
  ]) {
    if (pattern.test(text)) fail(`${rel(path)}: ${what}`);
  }
}

// B.3 — the DCAT export: a licence is an IRI or it is absent (site-03).
const catalogPath = join(dist, 'catalog.jsonld');
if (!existsSync(catalogPath)) {
  fail('dist/catalog.jsonld is missing — the DCAT export is what harvesters consume (plan §5.4)');
} else {
  const raw = read(catalogPath);
  if (/[<>]/.test(raw)) {
    fail('catalog.jsonld contains a raw < or > — escape them as the inline JSON-LD does (scrape-01)');
  }
  const catalogue = JSON.parse(raw);
  for (const dataset of catalogue['dcat:dataset'] ?? []) {
    const licence = dataset['dct:license'];
    if (licence !== undefined && !/^https?:\/\//.test(licence)) {
      fail(
        `catalog.jsonld: ${dataset['@id']} states dct:license "${licence}" — DCAT expects an IRI, ` +
          'so omit it when the licence is unmapped (site-03)'
      );
    }
  }
}

// B.4 — the six facets ADR-0023 §3 names, all of them, in the built HTML.
const EXPECTED_FACETS = ['task', 'kind', 'year', 'licence', 'source', 'institution'];
const searchPage = join(dist, 'search', 'index.html');
if (!existsSync(searchPage)) {
  fail('dist/search/index.html is missing');
} else {
  const rendered = new Set(
    [...read(searchPage).matchAll(/data-facet="([a-z-]+)"/g)].map((match) => match[1])
  );
  const missing = EXPECTED_FACETS.filter((facet) => !rendered.has(facet));
  if (missing.length) {
    fail(
      `/search/ renders ${rendered.size} facets, not six: missing ${missing.join(', ')}. ` +
        'ADR-0023 §3 names task, resource kind, year, licence, source system and institution.'
    );
  }
}

// B.5 — every record that holds data is typed `Dataset` (compliance-03), and
//        a withdrawn record offers no downloads (product-e2e-04).
const recordsDir = join(repoRoot, 'records');
const fixturesDir = join(repoRoot, 'fixtures', 'rendering');
const recordFiles = existsSync(recordsDir)
  ? readdirSync(recordsDir).filter((f) => f.endsWith('.json')).map((f) => join(recordsDir, f))
  : [];
const extraOf = (pkg, key) => (pkg.extras ?? []).find((e) => e.key === key)?.value;
const isWithdrawn = (pkg) =>
  extraOf(pkg, 'lifecycle_state') === 'withdrawn' || extraOf(pkg, 'withdrawn') === 'true';

let datasetsChecked = 0;
for (const path of recordFiles) {
  const pkg = JSON.parse(read(path));
  const page = `record/${pkg.name}/index.html`;
  if (!typesByPage.has(page)) {
    fail(`${page}: no parsed JSON-LD — every record page carries it (ADR-0023)`);
    continue;
  }
  const kind = extraOf(pkg, 'resource_kind') ?? 'dataset';
  const declared = [typesByPage.get(page)].flat();
  if (DATASET_KINDS.includes(kind)) {
    datasetsChecked += 1;
    if (!declared.includes('Dataset')) {
      fail(
        `${page}: resource_kind is "${kind}" but the JSON-LD says @type ${declared.join('+')} — ` +
          'a record that holds data must be typed Dataset or Google Dataset Search cannot index it'
      );
    }
  } else if (!declared.includes(schemaTypeFor(kind))) {
    fail(`${page}: @type ${declared.join('+')} does not match resource_kind "${kind}"`);
  }
}

// The withdrawn case is exercised by the gallery, which renders every fixture's
// record body: a files section is `<uid>-files-heading`, and `uid` is the
// record's slug, so its absence is checkable without parsing the page.
const withdrawnFixtures = existsSync(fixturesDir)
  ? readdirSync(fixturesDir)
      .filter((f) => f.endsWith('.json'))
      .map((f) => JSON.parse(read(join(fixturesDir, f))))
      .filter((fixture) => fixture.fixture_kind === 'record' && isWithdrawn(fixture.record))
  : [];
if (withdrawnFixtures.length === 0) {
  fail('no withdrawn rendering fixture exists — the withdrawn record page is then never rendered anywhere');
}
const allHtml = htmlFiles.map(read).join('\n');
for (const fixture of withdrawnFixtures) {
  const pkg = fixture.record;
  if ((pkg.resources ?? []).length === 0) {
    fail(
      `${fixture.fixture_id}: a withdrawn fixture with no resources cannot prove the files section is ` +
        'suppressed — give it one (product-e2e-04)'
    );
  }
  if (allHtml.includes(`${pkg.name}-files-heading`)) {
    fail(
      `${fixture.fixture_id}: a withdrawn record still renders "Files at the source" — the runbook says ` +
        'never imply the artifact is downloadable (product-e2e-04)'
    );
  }
}
for (const pkg of recordFiles.map((path) => JSON.parse(read(path)))) {
  if (isWithdrawn(pkg) && allHtml.includes(`${pkg.name}-files-heading`)) {
    fail(`record/${pkg.name}/: withdrawn, but still renders a live file list (product-e2e-04)`);
  }
}

// B.6 — link rot reaches a human (product-e2e-05). The gallery renders the
//        note from fixture r-09; if the fixture's dead URL is not on the page,
//        `state/link-check.json` is being written and read by nobody.
const rotFixture = join(repoRoot, 'fixtures', 'rendering', 'ui', 'r-09-dead-link.json');
if (!existsSync(rotFixture)) {
  fail('fixtures/rendering/ui/r-09-dead-link.json is missing — the link-rot note is then rendered nowhere');
} else {
  const { link_check: check } = JSON.parse(read(rotFixture));
  const urls = Object.values(check.dead_by_record ?? {}).flat();
  if (urls.length === 0) {
    fail('r-09-dead-link declares no dead link — the fixture proves nothing');
  }
  const gallery = join(dist, 'dev', 'components', 'index.html');
  const rendered = existsSync(gallery) ? read(gallery) : '';
  for (const url of urls) {
    if (!rendered.includes(url)) {
      fail(`/dev/components/ does not render the dead link ${url} — state/link-check.json reaches no reader (product-e2e-05)`);
    }
  }
}

// ============================================================ verdict

if (failures.length) {
  console.error(`check-render: FAIL — ${failures.length} problem(s)`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}
console.log(
  `check-render: OK — ${jsonLdBlocks} JSON-LD block(s) parse and cannot break out, ` +
    `${datasetsChecked} dataset(s) typed Dataset, six facets rendered, no unlinkable href`
);
