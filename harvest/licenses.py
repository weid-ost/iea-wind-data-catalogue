"""The licence register and the mapping into CKAN ``license_id`` values.

CKAN validates ``license_id`` against a licence register. Zenodo, SPDX, OSTI
and free-text rights statements do **not** all use CKAN's identifiers, so every
licence passes through this lookup on the way in (plan §2.2).

Unmappable input maps to ``notspecified`` and is **flagged in the run report**
— never silently defaulted to something open (fixtures ``zen-08``, ``dc-09``,
``gh-06``). "No licence stated" is a fact about the artifact and is displayed
as such.

:data:`LICENSE_REGISTER` is a superset of CKAN's core register with the
software licences a code catalogue needs. On promotion day it is exported as
CKAN's ``licenses_group_url`` JSON, so an id added here must be added there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "License",
    "LICENSE_REGISTER",
    "LICENSE_IDS",
    "UNMAPPED_LICENSE_ID",
    "map_license",
    "is_known_license",
    "as_ckan_register",
]

#: What an unmappable or absent licence becomes. Always flagged, never assumed.
UNMAPPED_LICENSE_ID = "notspecified"


@dataclass(frozen=True)
class License:
    id: str
    title: str
    url: str = ""
    is_open: bool = False
    osi_approved: bool = False


_REGISTER: tuple[License, ...] = (
    # --- CKAN core register -------------------------------------------------
    License("notspecified", "License not specified"),
    License("odc-pddl", "Open Data Commons Public Domain Dedication and License (PDDL)",
            "http://www.opendefinition.org/licenses/odc-pddl", True),
    License("odc-odbl", "Open Data Commons Open Database License (ODbL)",
            "http://www.opendefinition.org/licenses/odc-odbl", True),
    License("odc-by", "Open Data Commons Attribution License",
            "http://www.opendefinition.org/licenses/odc-by", True),
    License("cc-zero", "Creative Commons CCZero",
            "http://www.opendefinition.org/licenses/cc-zero", True),
    License("cc-by", "Creative Commons Attribution",
            "http://www.opendefinition.org/licenses/cc-by", True),
    License("cc-by-sa", "Creative Commons Attribution Share-Alike",
            "http://www.opendefinition.org/licenses/cc-by-sa", True),
    License("gfdl", "GNU Free Documentation License",
            "http://www.opendefinition.org/licenses/gfdl", True),
    License("other-open", "Other (Open)", "", True),
    License("other-pd", "Other (Public Domain)", "", True),
    License("other-at", "Other (Attribution)", "", True),
    License("uk-ogl", "UK Open Government Licence (OGL)",
            "http://reference.data.gov.uk/id/open-government-licence", True),
    License("cc-nc", "Creative Commons Non-Commercial (Any)",
            "http://creativecommons.org/licenses/by-nc/2.0/"),
    License("other-nc", "Other (Non-Commercial)"),
    License("other-closed", "Other (Not Open)"),
    # --- software licences the catalogue also needs (GitHub, Zenodo) --------
    License("mit", "MIT License", "https://opensource.org/licenses/MIT", True, True),
    License("apache", "Apache License 2.0", "https://opensource.org/licenses/Apache-2.0", True, True),
    License("bsd-2-clause", "BSD 2-Clause License",
            "https://opensource.org/licenses/BSD-2-Clause", True, True),
    License("bsd-3-clause", "BSD 3-Clause License",
            "https://opensource.org/licenses/BSD-3-Clause", True, True),
    License("gpl-2.0", "GNU General Public License v2.0",
            "https://opensource.org/licenses/GPL-2.0", True, True),
    License("gpl-3.0", "GNU General Public License v3.0",
            "https://opensource.org/licenses/GPL-3.0", True, True),
    License("lgpl-3.0", "GNU Lesser General Public License v3.0",
            "https://opensource.org/licenses/LGPL-3.0", True, True),
    License("agpl-3.0", "GNU Affero General Public License v3.0",
            "https://opensource.org/licenses/AGPL-3.0", True, True),
    License("mpl-2.0", "Mozilla Public License 2.0",
            "https://opensource.org/licenses/MPL-2.0", True, True),
    License("epl-2.0", "Eclipse Public License 2.0",
            "https://opensource.org/licenses/EPL-2.0", True, True),
    License("unlicense", "The Unlicense", "https://unlicense.org/", True),
    # --- CC variants Zenodo emits that CKAN core has no id for -------------
    License("cc-by-nd", "Creative Commons Attribution No-Derivatives",
            "https://creativecommons.org/licenses/by-nd/4.0/"),
    License("cc-nc-sa", "Creative Commons Attribution Non-Commercial Share-Alike",
            "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    License("cc-nc-nd", "Creative Commons Attribution Non-Commercial No-Derivatives",
            "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
)

#: ``{license_id: License}`` — the register CKAN validates against.
LICENSE_REGISTER: dict[str, License] = {lic.id: lic for lic in _REGISTER}

#: The set of legal ``license_id`` values.
LICENSE_IDS: frozenset[str] = frozenset(LICENSE_REGISTER)


def _norm(value: str) -> str:
    """Aggressively normalise a licence string for lookup."""
    text = str(value).strip().lower()
    text = text.replace("https://", "").replace("http://", "")
    text = re.sub(r"^(spdx:|info:eu-repo/semantics/)", "", text)
    text = re.sub(r"\bversion\b", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _build_alias_table() -> dict[str, str]:
    table: dict[str, str] = {}

    def add(license_id: str, *aliases: str) -> None:
        for alias in aliases:
            table[_norm(alias)] = license_id

    for lic in _REGISTER:  # every id and title maps to itself
        add(lic.id, lic.id, lic.title)

    add("cc-zero",
        "cc0", "cc0-1.0", "CC0 1.0", "cc-0", "zero", "publicdomain/zero/1.0",
        "creativecommons.org/publicdomain/zero/1.0/",
        "Creative Commons Zero v1.0 Universal",
        "CC0 1.0 Universal (CC0 1.0) Public Domain Dedication",
        "public domain dedication")
    add("cc-by",
        "cc-by-1.0", "cc-by-2.0", "cc-by-2.5", "cc-by-3.0", "cc-by-4.0",
        "CC BY 4.0", "CC-BY", "creativecommons.org/licenses/by/4.0/",
        "creativecommons.org/licenses/by/3.0/",
        "Creative Commons Attribution 4.0 International",
        "Creative Commons Attribution 4.0 International License",
        "Creative Commons Attribution License")
    add("cc-by-sa",
        "cc-by-sa-3.0", "cc-by-sa-4.0", "CC BY-SA 4.0",
        "creativecommons.org/licenses/by-sa/4.0/",
        "Creative Commons Attribution Share Alike 4.0 International",
        "Creative Commons Attribution-ShareAlike 4.0 International")
    add("cc-nc",
        "cc-by-nc-3.0", "cc-by-nc-4.0", "CC BY-NC 4.0",
        "creativecommons.org/licenses/by-nc/4.0/",
        "Creative Commons Attribution Non Commercial 4.0 International")
    add("cc-nc-sa", "cc-by-nc-sa-3.0", "cc-by-nc-sa-4.0", "CC BY-NC-SA 4.0",
        "Creative Commons Attribution Non Commercial Share Alike 4.0 International")
    add("cc-nc-nd", "cc-by-nc-nd-3.0", "cc-by-nc-nd-4.0", "CC BY-NC-ND 4.0",
        "Creative Commons Attribution Non Commercial No Derivatives 4.0 International")
    add("cc-by-nd", "cc-by-nd-3.0", "cc-by-nd-4.0", "CC BY-ND 4.0",
        "Creative Commons Attribution No Derivatives 4.0 International")
    add("mit", "mit", "mit-license", "MIT License", "The MIT License", "expat")
    add("apache",
        "apache-2.0", "apache-2", "asl-2.0", "Apache License 2.0",
        "Apache Software License 2.0", "Apache License, Version 2.0")
    add("bsd-2-clause", "bsd-2-clause", "bsd-2", "simplified bsd", "freebsd")
    add("bsd-3-clause", "bsd-3-clause", "bsd-3", "new bsd", "modified bsd",
        "BSD 3-Clause \"New\" or \"Revised\" License")
    add("gpl-2.0", "gpl-2.0", "gpl-2.0-only", "gpl-2.0-or-later", "gplv2",
        "GNU General Public License v2.0 only")
    add("gpl-3.0", "gpl-3.0", "gpl-3.0-only", "gpl-3.0-or-later", "gplv3", "gpl3",
        "GNU General Public License v3.0 or later",
        "GNU General Public License v3.0 only")
    add("lgpl-3.0", "lgpl-3.0", "lgpl-3.0-only", "lgpl-3.0-or-later", "lgplv3")
    add("agpl-3.0", "agpl-3.0", "agpl-3.0-only", "agpl-3.0-or-later", "agplv3")
    add("mpl-2.0", "mpl-2.0", "mozilla public license 2.0")
    add("epl-2.0", "epl-2.0", "eclipse public license 2.0")
    add("unlicense", "unlicense", "the unlicense")
    add("odc-pddl", "pddl-1.0", "odc-pddl-1.0", "opendatacommons.org/licenses/pddl/1.0/")
    add("odc-odbl", "odbl-1.0", "odc-odbl-1.0", "opendatacommons.org/licenses/odbl/1.0/")
    add("odc-by", "odc-by-1.0", "opendatacommons.org/licenses/by/1.0/")
    add("uk-ogl", "ogl-uk-3.0", "open government licence")
    add("other-closed",
        "closed", "proprietary", "all rights reserved", "copyright",
        "restricted", "other-closed", "info:eu-repo/semantics/closedAccess")
    add("other-open", "open", "other-open", "open licence", "open license")
    add("other-pd", "public domain", "publicdomain", "us government work",
        "public domain (usgov)")
    add("notspecified", "", "none", "null", "n/a", "na", "unknown",
        "not specified", "no license", "no licence", "unspecified", "other")
    return table


_ALIASES: dict[str, str] = _build_alias_table()


def is_known_license(license_id: str | None) -> bool:
    """Is ``license_id`` a legal CKAN ``license_id`` in this register?"""
    return bool(license_id) and str(license_id) in LICENSE_IDS


def map_license(raw: str | None) -> tuple[str, bool]:
    """Map any licence spelling to ``(license_id, mapped)``.

    ``mapped`` is ``False`` when the input was non-empty but unrecognised — the
    caller must flag it in the run report (fixture ``zen-08``). An *absent*
    licence also yields ``notspecified`` but with ``mapped=True``: nothing went
    wrong, the source simply said nothing.

    >>> map_license("CC-BY-4.0")
    ('cc-by', True)
    >>> map_license("Creative Commons Attribution 4.0 International")
    ('cc-by', True)
    >>> map_license("Free for academic use, contact the author")
    ('notspecified', False)
    """
    if raw is None:
        return UNMAPPED_LICENSE_ID, True
    text = str(raw).strip()
    if not text:
        return UNMAPPED_LICENSE_ID, True

    key = _norm(text)
    if not key:
        return UNMAPPED_LICENSE_ID, True
    if key in _ALIASES:
        return _ALIASES[key], True

    # URL forms: try the path tail, then progressively shorter prefixes.
    trimmed = key.strip("-")
    for candidate in (trimmed, re.sub(r"-\d+(-\d+)*$", "", trimmed)):
        if candidate in _ALIASES:
            return _ALIASES[candidate], True

    return UNMAPPED_LICENSE_ID, False


def as_ckan_register() -> list[dict]:
    """Export the register in CKAN's ``licenses_group_url`` JSON shape."""
    return [
        {
            "id": lic.id,
            "title": lic.title,
            "url": lic.url,
            "is_okd_compliant": lic.is_open,
            "is_osi_compliant": lic.osi_approved,
        }
        for lic in sorted(_REGISTER, key=lambda item: item.id)
    ]
