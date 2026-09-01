// Which schema.org type a record page declares, and why it is not always
// `Dataset` (compliance-03).
//
// ADR-0023 says "schema.org `Dataset` JSON-LD on every record page, so Google
// Dataset Search indexes the catalogue", and Dataset Search is the single
// biggest discovery win of going static. But this catalogue is not only
// datasets: of the records harvested so far, roughly a third are datasets and
// the rest are reports, journal articles and software repositories. Typing a
// journal article as a `Dataset` would be:
//
//   * a claim the catalogue cannot stand behind — it exists to reflect what
//     sources say, and no source says a paper is a dataset; and
//   * against Google's own structured-data policy, which asks publishers not
//     to mark up non-datasets as datasets. Dataset Search demotes or drops
//     whole sites for it, which would cost the *real* datasets their
//     eligibility to save the papers' — a bad trade for the thing the ADR
//     actually wants.
//
// So the decision, recorded here because the code is where it bites:
//
//   1. Every record whose `resource_kind` says it holds data — `dataset` and
//      `model` — is typed `Dataset`, and `scripts/check-render.mjs` fails the
//      build if one of them ever is not. That is the ADR's discovery
//      requirement, enforced.
//   2. Every other record keeps its precise type (`ScholarlyArticle`,
//      `Report`, `SoftwareSourceCode`, `CreativeWork`), which is what Google,
//      Crossref and every other consumer asks for, and stays discoverable
//      through the sitemap, the DCAT export at `/catalog.jsonld` and ordinary
//      web search.
//
// ADR-0023 §Decision should carry a sentence recording this narrowing; until
// it does, this comment is the record.

/** `resource_kind` -> schema.org type. */
export const SCHEMA_TYPE = Object.freeze({
  dataset: 'Dataset',
  publication: 'ScholarlyArticle',
  software: 'SoftwareSourceCode',
  report: 'Report',
  model: 'Dataset',
  other: 'CreativeWork',
});

/** The kinds that MUST be typed `Dataset` — the Dataset-Search guarantee. */
export const DATASET_KINDS = Object.freeze(['dataset', 'model']);

/** The type for a kind; an unknown kind is treated as a dataset, as the
 *  materialiser's own default does. */
export const schemaTypeFor = (kind) => SCHEMA_TYPE[kind ?? 'dataset'] ?? 'Dataset';
