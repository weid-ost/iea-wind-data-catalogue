// Token discipline, as a gate (design-system.md §8).
//
// "Components consume tokens with zero hardcoded values." Ten lines of grep is
// the difference between the system holding for years and eroding one quick fix
// at a time. Allow-listed: the generated tokens.css (the only place raw colour
// literals may exist) and the @font-face declarations in fonts.css.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src');
const ALLOW = new Set(['styles/tokens.css', 'styles/fonts.css']);
const RULES = [
  [/#[0-9a-fA-F]{3,8}\b/, 'hex colour literal'],
  [/\b(?:rgba?|hsla?|oklch|oklab|lab|lch|color-mix)\(/, 'raw colour function'],
  [/:\s*(?:white|black|red|green|blue|grey|gray|silver|orange|purple)\b/, 'named colour'],
  [/\b\d+(?:\.\d+)?px\b/, 'raw px length (use a space/radius/border-width token, or rem)'],
];

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const path = join(dir, entry);
  return statSync(path).isDirectory() ? walk(path) : [path];
});

const failures = [];
for (const path of walk(src).filter((p) => /\.(astro|css|ts|js|mjs)$/.test(p))) {
  const rel = relative(src, path).split('\\').join('/');
  if (ALLOW.has(rel)) continue;
  const styles = path.endsWith('.astro')
    // Only <style> blocks in .astro files: prose, URLs and JSON in the markup
    // are not stylesheets and must not be grepped as if they were.
    ? [...readFileSync(path, 'utf8').matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1])
    : [readFileSync(path, 'utf8')];
  for (const block of styles) {
    block.split('\n').forEach((line, index) => {
      if (/^\s*(\/\/|\/\*|\*)/.test(line)) return;
      for (const [pattern, why] of RULES) {
        if (pattern.test(line)) failures.push(`${rel}: ${why}: ${line.trim()}`);
      }
    });
  }
}

if (failures.length) {
  console.error(`check-tokens: FAIL — ${failures.length} hardcoded value(s) outside tokens.css`);
  for (const failure of failures) console.error(`  - ${failure}`);
  console.error('\nAdd a token; do not add an allow-list entry (run-the-a11y-gate §5).');
  process.exit(1);
}
console.log('check-tokens: OK — components consume tokens only');
