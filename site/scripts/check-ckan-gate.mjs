// Proves the Zod gate bites (plan §2.2, ADR-0032, fixture x-08-ckan-invalid).
//
// `astro build` fails on a malformed record only because src/ckan.mjs rejects
// it. A gate nobody has watched fail is a gate you are guessing about, so this
// runs the *same* schema against the fixture that exists to be refused and
// fails if it is accepted — and checks every violation the fixture declares is
// actually reported.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { validatePackage } from '../src/ckan.mjs';

const repo = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const fixture = JSON.parse(
  readFileSync(join(repo, 'fixtures', 'cross-cutting', 'x-08-ckan-invalid.json'), 'utf8')
);

const result = validatePackage(fixture.record);
if (result.ok) {
  console.error('check-ckan-gate: FAIL — x-08-ckan-invalid PASSED the Zod gate.');
  console.error('The site would render records CKAN refuses. Fix src/ckan.mjs, never the fixture.');
  process.exit(1);
}

// The fixture names the fields it expects to be caught; map its CKAN-gate
// field names onto the Zod issue paths.
const reported = result.errors.join(' | ');
const expected = { name: 'name', license_id: 'license_id', 'tags[0]': 'tags.0.name',
  "extras['iea_task']": 'extras.1.value', owner_org: 'owner_org', 'groups[0]': 'groups.0.name',
  state: 'state' };
const missed = Object.entries(expected)
  .filter(([, path]) => !result.errors.some((e) => e.startsWith(path)))
  .map(([field]) => field);

if (missed.length) {
  console.error(`check-ckan-gate: FAIL — x-08 violations not detected: ${missed.join(', ')}`);
  console.error(`  reported: ${reported}`);
  process.exit(1);
}
console.log(`check-ckan-gate: OK — x-08-ckan-invalid refused with ${result.errors.length} violation(s)`);
