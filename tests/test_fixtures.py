"""The fixture convention itself.

Every fixture in ``fixtures/<source>/`` must be loadable and well-formed. This
test is deliberately generic: as each track adds its fixtures, this suite grows
with them and catches a malformed fixture before an adapter test does.

See ``fixtures/README.md`` for the layout and ``fixtures/fixtures-catalogue.md``
for the inventory.
"""

from __future__ import annotations

import json
import re
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
    # `ui_state` fixtures live one directory down because the generic record
    # validation must not claim them — but they are still fixtures, and the
    # contract (id, kind, case, honest provenance) applies to them too.
    ui = FIXTURES / "rendering" / "ui"
    if ui.exists():
        files.extend(sorted(ui.glob("*.json")))
    return files


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ident(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


#: Every field a track has used to disclose that it invented a payload. Keeping
#: this in one place is what makes the honesty gate uniform across sources.
DISCLOSURE_FIELDS = ("case", "note", "invented", "provenance_note")


def declares_invented(fixture: dict) -> bool:
    if fixture.get("invented") is True:
        return True
    return any(
        "INVENTED" in (fixture.get(field) or "").upper()
        for field in DISCLOSURE_FIELDS
        if isinstance(fixture.get(field), str)
    )


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
            "source_namespace", "record", "page", "degradation", "ui_state"
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

        The five source tracks each grew their own disclosure habit — ieawind
        and wdh in ``case``, zenodo in ``invented``, datacite in
        ``provenance_note``, github in ``note`` — and the gate used to key off
        ``case`` alone, so it skipped nine of the twenty inventions and asserted
        nothing about them (fixture-compliance-03). It now reads whichever field
        the track chose, and every invented fixture must carry the marker in the
        raw payload too. The one exception is declared, not inferred: a fixture
        whose expectation is invented but whose *payload* is a real capture sets
        ``raw_is_capture: true`` (zen-12's real 410 tombstone body).
        """
        fixture = load(path)
        if not declares_invented(fixture):
            pytest.skip("not declared invented")
        raw = fixture.get("raw")
        if not raw:
            pytest.skip("no raw payload to mark")
        if fixture.get("raw_is_capture"):
            pytest.skip("raw payload is a real capture, declared as such")
        raw_path = path.parent / raw
        assert "INVENTED" in raw_path.read_text(encoding="utf-8").upper(), (
            f"{raw_path.name} is used as an invented fixture but does not say so"
        )

    def test_the_invented_flag_is_a_disclosure_not_a_shrug(self, path: Path) -> None:
        """``invented`` is either ``true`` or the prose explaining why."""
        fixture = load(path)
        if "invented" not in fixture:
            pytest.skip("no invented flag")
        value = fixture["invented"]
        assert isinstance(value, (bool, str))
        if isinstance(value, str):
            assert "INVENTED" in value.upper(), (
                "the invented field carries the disclosure; say INVENTED in it"
            )
        else:
            assert value is True
            assert any(
                "INVENTED" in (fixture.get(field) or "").upper()
                for field in ("case", "note", "provenance_note")
                if isinstance(fixture.get(field), str)
            ), (
                "invented: true needs the prose too — say INVENTED in case/note/"
                "provenance_note so a reader of the fixture is told why"
            )


#: DOIs, wherever they appear in a fixture file.
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"'\\,)\]<]+")

#: The DataCite reserved TEST prefix. Nothing under it resolves at doi.org, so a
#: fixture bound to it can never contradict a real work.
TEST_PREFIX = "10.5072/"


def rendering_files() -> list[Path]:
    directory = FIXTURES / "rendering"
    return sorted(p for p in directory.rglob("*.json")) if directory.exists() else []


@pytest.mark.parametrize("path", rendering_files(), ids=ident)
def test_rendering_fixtures_only_cite_reserved_identifiers(path: Path) -> None:
    """No invented record may be hung on a real work's identifier.

    Every record under ``fixtures/rendering/`` is hand-built: the 300-character
    title, the five-task record, the retraction, the withdrawal. They were
    originally bound to live third-party DOIs, which meant the gallery published
    a retraction flag over a real, unretracted Wind Energy paper and a
    "withdrawn upstream" banner over a live Zenodo dataset
    (fixture-compliance-01). Invention is fine; invention wearing somebody
    else's identifier is not.

    So: every DOI in this directory sits on the reserved 10.5072 test prefix,
    and no record page URL points at a live repository host. The gallery still
    renders exactly the same shapes.
    """
    text = path.read_text(encoding="utf-8")
    offenders = sorted({d for d in DOI_PATTERN.findall(text) if not d.startswith(TEST_PREFIX)})
    assert not offenders, (
        f"{path.name} cites live DOIs {offenders}; rendering fixtures are invented, so "
        f"their identifiers must sit on the reserved {TEST_PREFIX} test prefix"
    )
    assert "https://zenodo.org/" not in text, (
        f"{path.name} points at zenodo.org; an invented record links to the sandbox host"
    )


CATALOGUE = FIXTURES / "fixtures-catalogue.md"

#: A catalogue table row: `| \`<id>\` | … |`.
ROW = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|")

#: The only way a row is allowed to have no file: it says where the case went.
REALISED_AS = re.compile(r"realised as `([a-z0-9-]+)`")


def catalogue_rows() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in CATALOGUE.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            rows.append((match.group(1), line))
    return rows


class TestTheCatalogueMatchesTheTree:
    """`fixtures-catalogue.md` is the specification, so it has to be true.

    CLAUDE.md makes the catalogue authoritative and says "new behaviour ⇒ new
    fixture". Both halves rotted: seventeen fixtures existed with no row, three
    rows named files that did not exist — two of them the artifacts ADR-0028 and
    ADR-0031 nominate as their own "checkable" evidence — and `iea-09` was used
    for two different rows at once (fixture-compliance-04, -05, -06; site-05;
    compliance-07). Nothing failed, because nothing was comparing them.
    """

    def test_no_id_is_used_twice(self) -> None:
        ids = [row_id for row_id, _ in catalogue_rows()]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        assert not duplicates, (
            f"{duplicates} name more than one row — prose citing them is ambiguous"
        )

    def test_every_row_has_a_fixture_or_says_where_the_case_went(self) -> None:
        on_disk = {path.stem for path in ALL}
        orphans = []
        for row_id, line in catalogue_rows():
            if row_id in on_disk:
                continue
            realised = REALISED_AS.search(line)
            if realised and realised.group(1) in on_disk:
                continue
            orphans.append(row_id)
        assert not orphans, (
            f"catalogue rows {orphans} have no fixture file. Add the fixture, or say "
            f"'realised as `<id>`' in the row and point at the artifact that carries it"
        )

    def test_every_fixture_has_a_row(self) -> None:
        rows = {row_id for row_id, _ in catalogue_rows()}
        undocumented = sorted({path.stem for path in ALL} - rows)
        assert not undocumented, (
            f"{undocumented} exist but are in no catalogue table — a reader auditing "
            f"coverage from the catalogue alone would be told those cases are uncovered"
        )


class TestZen01:
    """The reference fixture. If this drifts, the convention has drifted."""

    def test_it_declares_itself_invented_and_uses_reserved_identifiers(self) -> None:
        """The fixture every other Zenodo fixture is shaped on is not a capture.

        It was unmarked, and its ids collided with a real live Zenodo record
        (fixture-compliance-02). Both halves are fixed here: the disclosure and
        the reserved prefix.
        """
        fixture = load(FIXTURES / "zenodo" / "zen-01-canonical.json")
        assert declares_invented(fixture)
        assert fixture["identity_key"].startswith(TEST_PREFIX)
        raw = (FIXTURES / "zenodo" / "raw" / "zen-01-canonical.json").read_text(encoding="utf-8")
        assert "INVENTED" in raw.upper()
        assert "https://zenodo.org/" not in raw

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
