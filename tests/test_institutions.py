"""``owner_org`` — every record is attributed to an institution in the register.

CKAN's ``package_create`` refuses a dataset with no owning organisation on a
default install, so ADR-0021's "POSTable to CKAN with no transformation" was
simply false: no adapter set ``owner_org``, all thirty records were org-less,
ADR-0023's *institution* facet had nothing to count, and
``organizations.yaml``'s twelve curated entries were dead data
(product-e2e-02, site-04, compliance-02).

Nobody registers anything with this catalogue — that is its premise — so the
institution is **inferred**, and these tests pin both halves of that: what the
heuristic gets right, and what it deliberately refuses to guess.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest import config
from harvest.ckan_compat import validate_package
from harvest.events import record_scrape
from harvest.institutions import DEFAULT_OWNER_ORG, infer_owner_org, match_organization
from harvest.materialize import materialize_all

KEY = "10.5281/zenodo.1234"


class TestMatchingAgainstTheRegister:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("National Renewable Energy Laboratory", "nrel"),
            ("NREL", "nrel"),
            ("Technical University of Denmark", "dtu"),
            ("DTU Wind and Energy Systems", "dtu"),
            ("Pacific Northwest National Laboratory", "pnnl"),
            ("Sandia National Laboratories", "sandia"),
            ("IEA Wind TCP", "iea-wind"),
        ],
    )
    def test_a_known_institution_is_matched(self, text: str, expected: str) -> None:
        assert match_organization(text) == expected

    def test_nothing_is_invented(self) -> None:
        """A string the register does not know must never become an organisation."""
        assert match_organization("Institute of Imaginary Wind Studies") is None
        assert match_organization("Copernicus Publications") is None
        assert match_organization("") is None

    def test_an_alias_matches_on_a_word_boundary_not_a_substring(self) -> None:
        """`OST` is a register entry. `composting` is not an institution."""
        assert match_organization("OST") == "ost"
        assert match_organization("industrial composting research") is None

    def test_the_longest_alias_wins(self) -> None:
        """So a short alias inside a longer name cannot hijack the match."""
        assert match_organization("National Renewable Energy Laboratory (NREL)") == "nrel"


class TestTheSignalOrder:
    def test_a_stated_owner_org_is_taken_as_stated(self) -> None:
        assert infer_owner_org({"owner_org": "dtu"}) == ("dtu", "stated")

    def test_a_stated_org_the_register_does_not_know_is_not_trusted(self) -> None:
        """It would fail the CKAN gate; inference is better than a broken record."""
        owner, basis = infer_owner_org({"owner_org": "made-up-lab", "publisher": "NREL"})
        assert owner == "nrel"
        assert basis != "stated"

    def test_the_research_organisation_beats_the_first_author(self) -> None:
        """OSTI's `research_orgs` is an institutional statement; an affiliation is personal."""
        effective = {
            "extra": {"osti_research_orgs": ["National Renewable Energy Laboratory"]},
            "authors": [{"name": "A", "affiliation": "Technical University of Denmark"}],
        }
        assert infer_owner_org(effective)[0] == "nrel"

    def test_the_github_owner_is_used_when_there_is_one(self) -> None:
        effective = {"extra": {"github_owner": "IEA-Task-43"}}
        assert infer_owner_org(effective)[0] == "iea-wind"

    def test_the_first_authors_affiliation_beats_a_later_one(self) -> None:
        effective = {
            "authors": [
                {"name": "A", "affiliation": "Technical University of Denmark"},
                {"name": "B", "affiliation": "Sandia National Laboratories"},
            ]
        }
        owner, basis = infer_owner_org(effective)
        assert owner == "dtu"
        assert basis == "first-author-affiliation"

    def test_the_publisher_is_the_last_real_signal(self) -> None:
        assert infer_owner_org({"publisher": "Sandia National Laboratories"})[0] == "sandia"


class TestWhatItRefusesToGuess:
    """The heuristic's limits, pinned so nobody later mistakes them for bugs."""

    def test_an_unattributable_record_gets_a_real_register_entry(self) -> None:
        owner, basis = infer_owner_org({"title": "Something", "publisher": "Elsevier"})
        assert owner == DEFAULT_OWNER_ORG
        assert basis == "fallback"

    def test_the_fallback_is_a_curation_task_not_a_null_in_a_facet(self) -> None:
        assert DEFAULT_OWNER_ORG in config.organization_names()

    def test_a_zenodo_deposit_that_states_no_affiliation_says_so_specifically(self) -> None:
        """`zenodo-community` is a truer statement than `unattributed`."""
        owner, _ = infer_owner_org({"title": "T"}, source_system="zenodo")
        assert owner == "zenodo-community"

    def test_a_copublished_dataset_is_attributed_to_one_of_its_institutions(self) -> None:
        """Stated plainly: this is attribution, not ownership, and it picks one."""
        effective = {
            "authors": [
                {"name": "A", "affiliation": "National Renewable Energy Laboratory"},
                {"name": "B", "affiliation": "Technical University of Denmark"},
            ]
        }
        assert infer_owner_org(effective)[0] == "nrel"


class TestEveryRecordCarriesOne:
    def test_the_value_is_always_a_register_entry(self) -> None:
        """The CKAN gate checks this; the inference must not be able to break it."""
        known = config.organization_names()
        for effective in (
            {},
            {"publisher": "Nobody At All"},
            {"owner_org": "not-a-real-org"},
            {"authors": [{"name": "A", "affiliation": "Ø"}]},
        ):
            owner, _ = infer_owner_org(effective)
            assert owner in known, effective

    def test_a_materialised_record_has_an_owner_and_passes_the_gate(
        self, repo: Path, events_dir: Path
    ) -> None:
        record_scrape(
            KEY, "zenodo", "1234", "rev-1",
            {"title": "T", "url": "https://zenodo.org/records/1234",
             "publisher": "Technical University of Denmark"},
            events_dir=events_dir, observed_at="2026-01-01T00:00:00Z",
        )

        result = materialize_all(root=repo)

        assert result.ok, result.violations
        package = json.loads(
            (repo / "records" / "doi-10-5281-zenodo-1234.json").read_text(encoding="utf-8")
        )
        assert package["owner_org"] == "dtu"
        assert validate_package(package, known_orgs=config.organization_names(repo)) == []

    def test_an_org_less_record_is_refused_by_the_gate(self) -> None:
        """The gate is what makes "always populated" enforceable rather than hoped."""
        package = {
            "name": "x-1", "title": "T", "notes": "n", "license_id": "cc-by",
            "state": "active", "private": False,
            "tags": [], "groups": [], "extras": [], "resources": [],
        }
        violations = validate_package(package, known_orgs=config.organization_names())
        assert any(v.field == "owner_org" for v in violations)
