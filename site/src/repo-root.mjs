// Finding the repository root, from anywhere.
//
// `import.meta.url` is the obvious answer and the wrong one: Astro bundles
// server modules into `dist/.prerender/chunks/`, so a path derived from the
// module's own location points at the build output. The working directory is
// `site/` for every entry point that matters (npm scripts, astro build, the
// gate scripts), so walk up from there until `sources.yaml` appears.
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

export function findRepoRoot(start = process.cwd()) {
  let dir = resolve(start);
  for (let i = 0; i < 8; i += 1) {
    if (existsSync(join(dir, 'sources.yaml'))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(
    `could not find the repository root (no sources.yaml above ${start}) — ` +
      'run the site commands from site/, as the Makefile and the runbooks do'
  );
}

export const repoRoot = findRepoRoot();
