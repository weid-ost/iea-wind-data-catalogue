"""Licence mapping — fixtures zen-08, dc-09, gh-06."""

from __future__ import annotations

import pytest

from harvest.ckan_compat import validate_package
from harvest.licenses import (
    LICENSE_IDS,
    UNMAPPED_LICENSE_ID,
    as_ckan_register,
    is_known_license,
    map_license,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Zenodo / SPDX identifiers
        ("cc-by-4.0", "cc-by"),
        ("CC-BY-4.0", "cc-by"),
        ("CC BY 4.0", "cc-by"),
        ("cc-by-sa-4.0", "cc-by-sa"),
        ("cc-zero", "cc-zero"),
        ("CC0-1.0", "cc-zero"),
        ("cc0", "cc-zero"),
        ("cc-by-nc-4.0", "cc-nc"),
        ("cc-by-nd-4.0", "cc-by-nd"),
        ("MIT", "mit"),
        ("mit-license", "mit"),
        ("Apache-2.0", "apache"),
        ("Apache License 2.0", "apache"),
        ("GPL-3.0-or-later", "gpl-3.0"),
        ("gpl-3.0-only", "gpl-3.0"),
        ("BSD-3-Clause", "bsd-3-clause"),
        ("MPL-2.0", "mpl-2.0"),
        ("odbl-1.0", "odc-odbl"),
        # Free text and URLs
        ("Creative Commons Attribution 4.0 International", "cc-by"),
        ("https://creativecommons.org/licenses/by/4.0/", "cc-by"),
        ("http://creativecommons.org/publicdomain/zero/1.0/", "cc-zero"),
        ("All rights reserved", "other-closed"),
        ("proprietary", "other-closed"),
        # CKAN ids map to themselves
        ("notspecified", "notspecified"),
        ("other-open", "other-open"),
    ],
)
def test_maps_known_licences(raw: str, expected: str) -> None:
    license_id, mapped = map_license(raw)
    assert (license_id, mapped) == (expected, True)
    assert license_id in LICENSE_IDS


@pytest.mark.parametrize(
    "raw",
    [
        "Free for academic use, contact the author",             # zen-08
        "Rights reserved by the consortium until 2027",          # dc-09
        "See LICENCE.txt in the archive",
        "Data may be used with permission of DTU",
    ],
)
def test_zen_08_unmappable_flags_rather_than_guessing(raw: str) -> None:
    license_id, mapped = map_license(raw)
    assert license_id == UNMAPPED_LICENSE_ID
    assert mapped is False, "an unrecognised licence must be FLAGGED, not silently defaulted"


@pytest.mark.parametrize("raw", [None, "", "   ", "none", "unknown", "n/a"])
def test_gh_06_absent_licence_is_notspecified_but_not_a_failure(raw) -> None:  # noqa: ANN001
    """`license: null` means 'no licence stated'. Nothing went wrong."""
    license_id, mapped = map_license(raw)
    assert license_id == UNMAPPED_LICENSE_ID
    assert mapped is True


def test_never_infers_an_open_licence() -> None:
    """The one mapping that must never happen: unknown -> something open."""
    for raw in ("Free for academic use", "see licence file", "ask the author"):
        license_id, _ = map_license(raw)
        assert license_id == "notspecified"


def test_every_register_id_passes_the_ckan_gate() -> None:
    for license_id in sorted(LICENSE_IDS):
        package = {
            "name": "a-record", "title": "T", "license_id": license_id,
            "tags": [], "extras": [], "resources": [], "groups": [],
        }
        assert validate_package(package, set(), set()) == []


def test_is_known_license() -> None:
    assert is_known_license("cc-by")
    assert not is_known_license("cc-by-4.0")
    assert not is_known_license(None)
    assert not is_known_license("")


def test_ckan_register_export_shape() -> None:
    register = as_ckan_register()
    assert {entry["id"] for entry in register} == set(LICENSE_IDS)
    assert all({"id", "title", "url"} <= entry.keys() for entry in register)
    assert register == sorted(register, key=lambda entry: entry["id"])
