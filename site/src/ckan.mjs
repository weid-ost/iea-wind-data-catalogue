// The Zod gate. This IS `validate-ckan-compat` on the site side (plan §2.2,
// ADR-0032, runbook run-the-site-locally §4): a record that CKAN's
// `package_create` would refuse fails the Astro build. Fixture
// `x-08-ckan-invalid` exists to prove it does — see scripts/check-ckan-gate.mjs.
//
// Every rule below mirrors harvest/ckan_compat.py. If you loosen one here you
// have forked the promotion contract; change both, or neither.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'astro/zod';
import yaml from 'js-yaml';
import { LICENSE_IDS } from './licenses.mjs';
import { repoRoot } from './repo-root.mjs';

/** Read one of the repo's canonical YAML registers. */
export function readRegister(file, key) {
  const doc = yaml.load(readFileSync(join(repoRoot, file), 'utf8')) ?? {};
  return doc[key] ?? [];
}

export const GROUPS = readRegister('groups.yaml', 'groups');
export const ORGANIZATIONS = readRegister('organizations.yaml', 'organizations');
const GROUP_NAMES = new Set(GROUPS.map((g) => g.name));
const ORG_NAMES = new Set(ORGANIZATIONS.map((o) => o.name));

export const NAME_RE = /^[a-z0-9_-]{2,100}$/;
export const TAG_RE = /^[A-Za-z0-9._-]{2,100}$/;
export const CKAN_STATES = ['active', 'deleted', 'draft'];

const unique = (values, what) => ({
  message: `duplicate ${what}`,
  check: new Set(values).size === values.length,
});

/** A CKAN `package` dict, exactly as `records/*.json` holds it. */
export const ckanPackage = z
  .object({
    name: z.string().regex(NAME_RE, "not a legal CKAN slug (a-z, 0-9, '-', '_', 2-100 chars)"),
    title: z.string().min(1, 'title is missing or empty'),
    notes: z.string().default(''),
    license_id: z
      .string()
      .refine((id) => LICENSE_IDS.includes(id), {
        message: 'not in the licence register — map it through harvest.licenses.map_license',
      }),
    tags: z.array(z.object({ name: z.string().regex(TAG_RE, 'not a legal CKAN tag') }).passthrough()).default([]),
    extras: z
      .array(
        z.object({
          key: z.string().min(1, 'extras key must be a non-empty string'),
          // The whole point: CKAN accepts string extras and nothing else.
          value: z.string({ invalid_type_error: 'extras value must be a string (encode lists and objects as JSON)' }),
        })
      )
      .default([]),
    resources: z.array(z.object({ url: z.string().min(1, 'resource has no url') }).passthrough()).default([]),
    groups: z
      .array(
        z.object({
          name: z.string().refine((n) => GROUP_NAMES.has(n), { message: 'no such group in groups.yaml' }),
        }).passthrough()
      )
      .default([]),
    owner_org: z
      .string()
      .refine((n) => ORG_NAMES.has(n), { message: 'no such organization in organizations.yaml' })
      .optional(),
    url: z.string().optional(),
    version: z.string().optional(),
    state: z.enum(CKAN_STATES).default('active'),
    private: z.boolean().default(false),
  })
  .passthrough()
  .superRefine((pkg, ctx) => {
    const tags = unique(pkg.tags.map((t) => t.name), 'tag');
    if (!tags.check) ctx.addIssue({ code: 'custom', path: ['tags'], message: tags.message });
    const keys = unique(pkg.extras.map((e) => e.key), 'extras key');
    if (!keys.check) ctx.addIssue({ code: 'custom', path: ['extras'], message: keys.message });
  });

/** `{ok: true, data}` or `{ok: false, errors: [...]}`. Used by the gate script. */
export function validatePackage(value) {
  const result = ckanPackage.safeParse(value);
  return result.success
    ? { ok: true, data: result.data }
    : { ok: false, errors: result.error.issues.map((i) => `${i.path.join('.') || '<root>'}: ${i.message}`) };
}
