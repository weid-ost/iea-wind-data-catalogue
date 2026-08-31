"""The fixture convention itself.

Every fixture in ``fixtures/<source>/`` must be loadable and well-formed. This
test is deliberately generic: as each track adds its fixtures, this suite grows
with them and catches a malformed fixture before an adapter test does.

See ``fixtures/README.md`` for the layout and ``fixtures/fixtures-catalogue.md``
for the inventory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest import config
from harvest.ckan_compat import validate_package
from harvest.models import FieldProvenance, SourceNamespace

FIXTURES = config.fixtures_dir()
SOURCE_DIRS = ("zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh",
               "cross-cutting", "rendering")


def fixture_files() -> list[Path]:
    files: list[Path] = []
    for name in SOURCE_DIRS:
        directory = FIXTURES / name
        if directory.exists():
            files.extend(sorted(p for p in directory.glob("*.json")))
    return files


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ident(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


ALL = fixture_files()


def test_the_fixture_tree_exists() -> None:
    assert FIXTURES.exists()
    assert (FIXTURES / "fixtures-catalogue.md").exists()
    assert ALL, "no fixtures found — each track adds its own under fixtures/<source>/"


@pytest.mark.parametrize("path", ALL, ids=ident)
class TestEveryFixture:
    def test_declares_id_and_kind(self, path: Path) -> None:
        fixture = load(path)
        assert fixture["fixture_id"] == path.stem
        assert fixture["fixture_kind"] in (
            "source_namespace", "record", "page", "degradation"
        )
        assert fixture.get("case"), "say what the fixture is for"

    def test_page_fixtures_pin_a_page_and_its_hash(self, path: Path) -> None:
        """``page`` fixtures pin crawl behaviour, not a record (Tier 3).

        A task page is a citation list: most of what can go wrong there —
        classification, the DOI sweep, boilerplate stripping, a recorded
        coverage gap — happens *before* any record exists, so those fixtures
        hold an HTML page and the expectations about it rather than a
        ``SourceNamespace``.
        """
        fixture = load(path)
        if fixture["fixture_kind"] != "page":
            pytest.skip("not a page fixture")
        raw = path.parent / fixture["raw"]
        assert raw.exists(), f"missing captured page: {raw}"
        assert raw.suffix == ".html"
        assert fixture["page_url"].startswith("http")
        assert fixture["expected_content_hash"]
        assert isinstance(fixture["expected_dois"], list)

    def test_degradation_fixtures_pin_a_source_result(self, path: Path) -> None:
        """``degradation`` fixtures pin *absence*: a source that disables itself.

        There is no record to capture and no page to reduce — the whole point
        is that upstream gave us nothing. So the expectation is the
        :class:`~harvest.adapters.base.SourceResult` the run report will show,
        and the raw payload is the probe transcript that justifies it
        (``wdh-07``).
        """
        fixture = load(path)
        if fixture["fixture_kind"] != "degradation":
            pytest.skip("not a degradation fixture")
        expected = fixture["expected_source_result"]
        assert expected["reachable"] is False
        assert expected["changed"] == 0, "a disabled source appends no events"
        assert fixture["expected_run_ok"] is True, "degradation never fails the run"
        assert fixture["expected_records_untouched"] is True

    def test_source_namespace_fixtures_are_valid(self, path: Path) -> None:
        fixture = load(path)
        if fixture["fixture_kind"] != "source_namespace":
            pytest.skip("not a source_namespace fixture")
        assert fixture["identity_key"]
        assert fixture["source_key"], "the change token must be part of the expectation"
        SourceNamespace.model_validate(fixture["source"])
        for field, provenance in fixture.get("provenance", {}).items():
            FieldProvenance.model_validate(provenance), field

    def test_record_fixtures_pass_or_declare_that_they_fail(self, path: Path) -> None:
        fixture = load(path)
        if fixture["fixture_kind"] != "record":
            pytest.skip("not a record fixture")
        violations = validate_package(
            fixture["record"], config.organization_names(), config.group_names()
        )
        if fixture.get("expect_violations"):
            assert violations, "this fixture exists to FAIL the CKAN gate"
        else:
            assert violations == [], [str(v) for v in violations]

    def test_a_raw_payload_exists_where_one_is_referenced(self, path: Path) -> None:
        fixture = load(path)
        raw = fixture.get("raw")
        if not raw:
            pytest.skip("no raw payload referenced")
        raw_path = path.parent / raw
        assert raw_path.exists(), f"missing verbatim upstream payload: {raw_path}"
        if raw_path.suffix == ".html":
            assert raw_path.read_text(encoding="utf-8").lstrip().startswith("<!DOCTYPE")
        else:
            json.loads(raw_path.read_text(encoding="utf-8"))

    def test_an_invented_payload_says_so(self, path: Path) -> None:
        """Fixtures the tracks had to invent are marked, in the payload itself.

        A captured payload tests the parser against the API; an invented one
        tests it against somebody's idea of the API. Both are legitimate — the
        second only when the shape genuinely cannot be captured — but the
        difference has to be visible to whoever reads the fixture next.
        """
        fixture = load(path)
        if "INVENTED" not in (fixture.get("case") or "").upper():
            pytest.skip("not declared invented")
        raw_path = path.parent / fixture["raw"]
        assert "INVENTED" in raw_path.read_text(encoding="utf-8").upper(), (
            f"{raw_path.name} is used as an invented fixture but does not say so"
        )


class TestZen01:
    """The reference fixture. If this drifts, the convention has drifted."""

    def test_identity_is_the_concept_doi_not_the_version_doi(self) -> None:
        fixture = load(FIXTURES / "zenodo" / "zen-01-canonical.json")
        raw = load(FIXTURES / "zenodo" / "raw" / "zen-01-canonical.json")
        assert fixture["identity_key"] == raw["conceptdoi"]
        assert fixture["identity_key"] != raw["doi"]

    def test_expected_slug_matches_the_identity_rules(self) -> None:
        from harvest.identity import slug_for_identity

        fixture = load(FIXTURES / "zenodo" / "zen-01-canonical.json")
        assert slug_for_identity(fixture["identity_key"]) == fixture["expected_slug"]

    def test_source_key_is_the_revision(self) -> None:
        fixture = load(FIXTURES / "zenodo" / "zen-01-canonical.json")
        raw = load(FIXTURES / "zenodo" / "raw" / "zen-01-canonical.json")
        assert fixture["source_key"] == str(raw["revision"])


class TestX08:
    """The gate's failure case (fixture x-08)."""

    def test_every_declared_violation_is_detected(self) -> None:
        fixture = load(FIXTURES / "cross-cutting" / "x-08-ckan-invalid.json")
        violations = validate_package(
            fixture["record"], config.organization_names(), config.group_names()
        )
        found = {violation.field for violation in violations}
        assert {"name", "license_id", "tags[0]", "owner_org", "groups[0]", "state"} <= found
        assert any("must be a string" in v.message for v in violations)
