/**
 * schema.org `Dataset` JSON-LD, and the DCAT catalogue export.
 *
 * This is the single biggest discovery win of going static (plan §2.1): the
 * catalogue's whole purpose is findability, and being in **Google Dataset
 * Search** is worth more than any feature a registration-based platform offers.
 * The JSON-LD is rendered into the built HTML rather than injected by script,
 * because content that needs script execution to exist is not reliably indexed.
 *
 * Nothing here invents a field. Where the record does not state something, the
 * property is simply absent.
 */
import {
  authorsOf,
  extra,
  isWithdrawn,
  plainText,
  sourceUrlsOf,
  tasksOf,
  type CkanPackage,
} from './record';
import { taskTitle } from './registers';
import { LICENSES } from '../licenses.mjs';
import { schemaTypeFor } from '../schema-types.mjs';

const drop = <T extends Record<string, unknown>>(object: T): T =>
  Object.fromEntries(
    Object.entries(object).filter(([, value]) =>
      Array.isArray(value) ? value.length > 0 : value !== undefined && value !== null && value !== ''
    )
  ) as T;

export function datasetJsonLd(pkg: CkanPackage, canonical: string): Record<string, unknown> {
  const kind = extra(pkg, 'resource_kind') ?? 'dataset';
  const doi = extra(pkg, 'doi');
  const authors = authorsOf(pkg);
  const tasks = tasksOf(pkg);

  return drop({
    '@context': 'https://schema.org',
    // `Dataset` for everything that holds data, the precise type otherwise.
    // The reasoning, and the gate that keeps datasets typed `Dataset`, live in
    // `src/schema-types.mjs`.
    '@type': schemaTypeFor(kind),
    '@id': canonical,
    url: canonical,
    name: pkg.title,
    description: plainText(pkg.notes) || undefined,
    identifier: doi ? `https://doi.org/${doi}` : undefined,
    sameAs: sourceUrlsOf(pkg),
    datePublished: extra(pkg, 'published_date'),
    version: pkg.version,
    inLanguage: 'en',
    creator: authors.map((author) =>
      drop({
        '@type': 'Person',
        name: author.name,
        identifier: author.orcid ? `https://orcid.org/${author.orcid}` : undefined,
        affiliation: author.affiliation
          ? { '@type': 'Organization', name: author.affiliation }
          : undefined,
      })
    ),
    publisher: extra(pkg, 'publisher')
      ? { '@type': 'Organization', name: extra(pkg, 'publisher') }
      : undefined,
    license: licenseUrl(pkg.license_id),
    keywords: [
      ...(pkg.tags ?? []).map((tag) => tag.name),
      ...tasks.map((task) => taskTitle(task)),
    ],
    isAccessibleForFree: extra(pkg, 'access_status') === 'open' ? true : undefined,
    conditionsOfAccess: conditionsOfAccess(pkg),
    creativeWorkStatus: isWithdrawn(pkg) ? 'Withdrawn' : undefined,
    distribution: (pkg.resources ?? []).map((resource) =>
      drop({
        '@type': 'DataDownload',
        contentUrl: resource.url,
        name: resource.name,
        encodingFormat: resource.format,
      })
    ),
  });
}

function licenseUrl(id: string): string | undefined {
  // Only emit a licence URL we can stand behind; the register carries them.
  const known: Record<string, string> = {
    'cc-by': 'https://creativecommons.org/licenses/by/4.0/',
    'cc-by-sa': 'https://creativecommons.org/licenses/by-sa/4.0/',
    'cc-zero': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'cc-nc': 'https://creativecommons.org/licenses/by-nc/4.0/',
    'cc-nc-sa': 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
    'cc-nc-nd': 'https://creativecommons.org/licenses/by-nc-nd/4.0/',
    'cc-by-nd': 'https://creativecommons.org/licenses/by-nd/4.0/',
    mit: 'https://opensource.org/licenses/MIT',
    apache: 'https://opensource.org/licenses/Apache-2.0',
    'bsd-3-clause': 'https://opensource.org/licenses/BSD-3-Clause',
    'bsd-2-clause': 'https://opensource.org/licenses/BSD-2-Clause',
    'gpl-3.0': 'https://opensource.org/licenses/GPL-3.0',
    'gpl-2.0': 'https://opensource.org/licenses/GPL-2.0',
    'lgpl-3.0': 'https://opensource.org/licenses/LGPL-3.0',
    'agpl-3.0': 'https://opensource.org/licenses/AGPL-3.0',
    'mpl-2.0': 'https://opensource.org/licenses/MPL-2.0',
    'epl-2.0': 'https://opensource.org/licenses/EPL-2.0',
    unlicense: 'https://unlicense.org/',
  };
  return known[id];
}

function conditionsOfAccess(pkg: CkanPackage): string | undefined {
  const status = extra(pkg, 'access_status');
  if (!status || status === 'open') return undefined;
  const embargo = extra(pkg, 'embargo_date');
  if (status === 'embargoed' && embargo) return `Embargoed until ${embargo}`;
  return status.replace(/-/g, ' ');
}

/**
 * `catalog.jsonld` — the read-only DCAT export. DCAT harvesters consume a file,
 * which is why publishing one is barely a loss compared with a write API
 * (plan §5.4).
 */
export function dcatCatalogue(
  packages: CkanPackage[],
  site: string,
  modified?: string
): Record<string, unknown> {
  const base = site.replace(/\/$/, '');
  return {
    '@context': {
      dcat: 'http://www.w3.org/ns/dcat#',
      dct: 'http://purl.org/dc/terms/',
      foaf: 'http://xmlns.com/foaf/0.1/',
      vcard: 'http://www.w3.org/2006/vcard/ns#',
    },
    '@type': 'dcat:Catalog',
    '@id': `${base}/catalog.jsonld`,
    'dct:title': 'IEA Wind Data Catalogue',
    'dct:description':
      'Datasets, publications and software from the IEA Wind Tasks, aggregated from where they are already published. Metadata is reflected verbatim from its sources.',
    'dct:license': 'https://creativecommons.org/publicdomain/zero/1.0/',
    'dct:modified': modified,
    'dcat:dataset': packages.map((pkg) => dcatDataset(pkg, base)),
  };
}

function dcatDataset(pkg: CkanPackage, base: string): Record<string, unknown> {
  const doi = extra(pkg, 'doi');
  return drop({
    '@type': 'dcat:Dataset',
    '@id': `${base}/record/${pkg.name}/`,
    'dct:identifier': doi ? `https://doi.org/${doi}` : extra(pkg, 'identity_key'),
    'dct:title': pkg.title,
    'dct:description': plainText(pkg.notes) || undefined,
    'dct:issued': extra(pkg, 'published_date'),
    'dct:modified': extra(pkg, 'last_seen'),
    'dct:publisher': extra(pkg, 'publisher')
      ? { '@type': 'foaf:Agent', 'foaf:name': extra(pkg, 'publisher') }
      : undefined,
    // DCAT expects an IRI here, and the human label is not one. An unmapped
    // licence used to emit "License not specified" as though that were the name
    // of a licence — twelve of thirty records told harvesters they had one
    // (site-03). Where no IRI is known the property is simply absent, exactly as
    // the schema.org path already does; the record page still shows the source's
    // own wording under "Licence as stated".
    'dct:license': licenseUrl(pkg.license_id),
    'dct:type': extra(pkg, 'resource_kind'),
    'dcat:keyword': [
      ...(pkg.tags ?? []).map((tag) => tag.name),
      ...tasksOf(pkg).map((task) => taskTitle(task)),
    ],
    'dcat:landingPage': extra(pkg, 'source_url'),
    'dcat:distribution': (pkg.resources ?? []).map((resource) =>
      drop({
        '@type': 'dcat:Distribution',
        'dcat:accessURL': resource.url,
        'dct:title': resource.name,
        'dcat:mediaType': resource.format,
      })
    ),
  });
}

export const LICENSE_NAMES = LICENSES;
