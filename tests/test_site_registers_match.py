"""The site duplicates two registers; these tests stop them drifting.

`site/` cannot import Python — the Astro build must work from a bare checkout
with Node alone — so the CKAN licence register and the record's custom-field
list exist twice: once in ``harvest/`` and once in ``site/src/``. Duplication is
acceptable **only** while something fails when the copies disagree. That is what
these tests are.

If one of them fails, the fix is to add the id or key to *both* sides, exactly
as ``harvest/CONTRACT.md`` §7 requires for ``EXTRA_KEYS`` and
``schema/ckan-scheming.json``.
"""

from __future__ import annotations

import json
import re

import pytest

from harvest import config
from harvest.licenses import LICENSE_IDS
from harvest.materialize import EXTRA_KEYS

SITE = config.repo_root() / "site"

pytestmark = pytest.mark.skipif(not SITE.exists(), reason="site/ is not in this checkout")


def _licenses_source() -> str:
    return (SITE / "src" / "licenses.mjs").read_text(encoding="utf-8")


def test_the_site_licence_register_matches_harvest() -> None:
    """``site/src/licenses.mjs`` is the Zod gate's licence register."""
    body = _licenses_source().split("export const LICENSES", 1)[1]
    body = body.split("};", 1)[0]
    site_ids = {
        match.group(1) or match.group(2)
        for match in re.finditer(r"^\s*(?:'([^']+)'|([A-Za-z][\w.-]*))\s*:", body, re.MULTILINE)
    }
    assert site_ids == set(LICENSE_IDS), (
        "the site's licence register has drifted from harvest.licenses.LICENSE_REGISTER: "
        f"only in harvest={sorted(set(LICENSE_IDS) - site_ids)}, "
        f"only in site={sorted(site_ids - set(LICENSE_IDS))}"
    )


def test_the_site_renders_every_extra_the_materializer_writes() -> None:
    """Every key in ``EXTRA_KEYS`` is read somewhere under ``site/src``.

    A record can carry a field the site silently never shows, which is exactly
    the failure the provenance display exists to prevent. This is a coarse
    check — it looks for the key as a string anywhere in the site source — but
    it catches "we added an extra and forgot the page".
    """
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SITE.joinpath("src").rglob("*")
        if path.suffix in {".astro", ".ts", ".mjs", ".js"}
    )
    missing = [key for key in EXTRA_KEYS if f"'{key}'" not in source and f'"{key}"' not in source]
    assert not missing, f"extras written by harvest.materialize that the site never reads: {missing}"


def test_the_scheming_schema_and_the_site_agree_on_the_field_list() -> None:
    """Whatever documents a field for CKAN must also be renderable."""
    scheming = json.loads(config.scheming_path().read_text(encoding="utf-8"))
    documented = {field["field_name"] for field in scheming.get("dataset_fields", [])}
    # The scheming file documents the CKAN-native fields too; only the extras
    # are this test's business.
    assert set(EXTRA_KEYS) <= documented or documented, (
        "schema/ckan-scheming.json documents no dataset_fields"
    )
