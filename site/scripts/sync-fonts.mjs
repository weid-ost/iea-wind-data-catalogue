// design/fonts/*.woff2 -> site/public/fonts/. The fonts are committed once, in
// design/fonts/, because they are part of the design system rather than of the
// site; this copies them into Astro's public directory at build time so there
// is exactly one authoritative copy in the repository.
//
// No CDN, ever: a third-party font request is a third-party dependency, a
// privacy leak and a thing that can rot (design-system.md §3).
import { cpSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const site = join(dirname(fileURLToPath(import.meta.url)), '..');
const from = join(site, '..', 'design', 'fonts');
const to = join(site, 'public', 'fonts');

mkdirSync(to, { recursive: true });
const files = readdirSync(from).filter((f) => f.endsWith('.woff2'));
if (files.length === 0) throw new Error(`no woff2 files in ${from} — the Inter subsets are missing`);
for (const file of files) cpSync(join(from, file), join(to, file));
console.log(`sync-fonts: ${files.length} woff2 -> site/public/fonts/`);
