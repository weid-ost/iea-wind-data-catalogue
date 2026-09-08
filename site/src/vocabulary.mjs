// `vocabulary.yaml` — the controlled vocabularies and their definitions.
//
// Canonical repository data, like groups.yaml and organizations.yaml, and read
// the same way (ADR-0040). It is the reason the About page's Definitions
// section, the tooltip on a chip and the label on a facet can never disagree:
// there is one file, and all three read it.
import { readRegister } from './ckan.mjs';

export const AUTHORITIES = readRegister('vocabulary.yaml', 'authorities') ?? {};
export const RESOURCE_KINDS = readRegister('vocabulary.yaml', 'resource_kinds');
export const RESOURCE_TYPES = readRegister('vocabulary.yaml', 'resource_types');
export const ACCESS_STATUSES = readRegister('vocabulary.yaml', 'access_statuses');
export const TERMS = readRegister('vocabulary.yaml', 'terms');
