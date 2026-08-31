// The accessibility gate: serve dist/, run pa11y-ci over the URL list twice —
// once per theme — and fail the build on any violation.
//
// Both themes, because dark-mode contrast regressions are the most common kind
// and most pipelines never test them. The theme is forced by the `?theme=`
// query parameter that <theme-toggle> honours (design-system.md §7).
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { serve } from './serve.mjs';

const site = process.cwd();
const dist = join(site, 'dist');
if (!existsSync(join(dist, 'index.html'))) {
  console.error('a11y: dist/ is missing or stale — run `npm run build` first.');
  process.exit(1);
}

const base = (process.env.SITE_BASE ?? '/iea-wind-data-catalogue').replace(/\/$/, '');
const port = Number(process.env.A11Y_PORT ?? 4321);
const origin = `http://localhost:${port}`;

const config = JSON.parse(readFileSync(join(site, '.pa11yci'), 'utf8'));

// A record listed in .pa11yci disappears the day its fixture stops standing in
// for a real record. Say so loudly rather than silently auditing six pages.
const missing = config.paths.filter((path) => !existsSync(join(dist, path, 'index.html')));
if (missing.length) {
  console.error(`a11y: these .pa11yci paths are not in dist/: ${missing.join(', ')}`);
  console.error('Update .pa11yci to name records that exist (the gallery still covers every fixture).');
  process.exit(1);
}

const urls = ['light', 'dark'].flatMap((theme) =>
  config.paths.map((path) => `${origin}${base}${path}?theme=${theme}`)
);

const generated = join(site, '.astro', 'pa11yci.generated.json');
mkdirSync(join(site, '.astro'), { recursive: true });
writeFileSync(generated, JSON.stringify({ defaults: config.defaults, urls }, null, 2));

const server = await serve({ root: dist, base: base || '/', port });
const stop = () => server.close();
process.on('SIGINT', () => {
  stop();
  process.exit(130);
});

console.log(`a11y: auditing ${urls.length} URLs (${config.paths.length} pages x 2 themes)`);

const pa11y = spawn(
  process.execPath,
  [join(site, 'node_modules', '.bin', 'pa11y-ci'), '--config', generated],
  { cwd: site, stdio: 'inherit' }
);

pa11y.on('exit', (code) => {
  stop();
  process.exit(code ?? 1);
});
