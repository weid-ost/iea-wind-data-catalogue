// The CKAN licence register, mirrored from harvest/licenses.py.
//
// It is duplicated rather than imported because the site build must not need a
// Python interpreter. `tests/test_site_registers_match.py` fails if the two
// lists ever drift, so this is a checked duplication rather than a hopeful one.
// Add an id here, add it there, and add it to harvest.licenses.LICENSE_REGISTER.
export const LICENSES = {
  notspecified: 'License not specified',
  'odc-pddl': 'Open Data Commons Public Domain Dedication and License (PDDL)',
  'odc-odbl': 'Open Data Commons Open Database License (ODbL)',
  'odc-by': 'Open Data Commons Attribution License',
  'cc-zero': 'Creative Commons CCZero',
  'cc-by': 'Creative Commons Attribution',
  'cc-by-sa': 'Creative Commons Attribution Share-Alike',
  gfdl: 'GNU Free Documentation License',
  'other-open': 'Other (Open)',
  'other-pd': 'Other (Public Domain)',
  'other-at': 'Other (Attribution)',
  'uk-ogl': 'UK Open Government Licence (OGL)',
  'cc-nc': 'Creative Commons Non-Commercial (Any)',
  'other-nc': 'Other (Non-Commercial)',
  'other-closed': 'Other (Not Open)',
  mit: 'MIT License',
  apache: 'Apache License 2.0',
  'bsd-2-clause': 'BSD 2-Clause License',
  'bsd-3-clause': 'BSD 3-Clause License',
  'gpl-2.0': 'GNU General Public License v2.0',
  'gpl-3.0': 'GNU General Public License v3.0',
  'lgpl-3.0': 'GNU Lesser General Public License v3.0',
  'agpl-3.0': 'GNU Affero General Public License v3.0',
  'mpl-2.0': 'Mozilla Public License 2.0',
  'epl-2.0': 'Eclipse Public License 2.0',
  unlicense: 'The Unlicense',
  'cc-by-nd': 'Creative Commons Attribution No-Derivatives',
  'cc-nc-sa': 'Creative Commons Attribution Non-Commercial Share-Alike',
  'cc-nc-nd': 'Creative Commons Attribution Non-Commercial No-Derivatives',
};

export const LICENSE_IDS = Object.keys(LICENSES);

/** Display title for a licence id, falling back to the id itself. */
export const licenseTitle = (id) => LICENSES[id] ?? id;
