"""The two-level classification (ADR-0040).

Three questions came back from IEA Wind about what "publication", "report",
"software" and "other" mean on a record. These are the tests that pin the
answers: that the specific type is derived from what the source already
published, that IEA Wind's own publication types are recognised without
grabbing every paper that merely discusses best practice, and that every value
the code can produce is defined in ``vocabulary.yaml``.
"""

from __future__ import annotations

import pytest

from harvest import config
from harvest.models import ACCESS_STATUSES, RESOURCE_KINDS
from harvest.resource_types import (
    GENERIC_TYPE_FOR_KIND,
    KIND_OF_TYPE,
    check_vocabulary,
    derive,
    iea_publication_type,
    research_software_type,
)


class TestTheVocabularyRegister:
    def test_the_register_and_the_code_agree(self) -> None:
        """The gate that stops a chip appearing with no definition behind it."""
        assert check_vocabulary() == []

    def test_every_type_names_a_kind_that_exists(self) -> None:
        for name, kind in KIND_OF_TYPE.items():
            assert kind in RESOURCE_KINDS, name

    def test_every_kind_has_a_generic_type_to_fall_back_to(self) -> None:
        """So a record with a kind always gets a type, and "unspecified" is a
        stated fact rather than a missing field."""
        for kind in RESOURCE_KINDS:
            assert GENERIC_TYPE_FOR_KIND[kind] in KIND_OF_TYPE

    def test_every_definition_is_a_real_sentence(self) -> None:
        vocab = config.load_vocabulary()
        entries = (
            vocab["resource_kinds"] + vocab["resource_types"]
            + vocab["access_statuses"] + vocab["terms"]
        )
        for entry in entries:
            definition = entry["definition"].strip()
            assert len(definition) > 30, entry["name"]
            assert definition[0].isupper() or definition.startswith('"'), entry["name"]
            # A quoted definition ends with the closing quote after the stop.
            assert definition.rstrip('"').endswith("."), entry["name"]

    def test_the_access_statuses_match_the_model(self) -> None:
        named = {e["name"] for e in config.load_vocabulary()["access_statuses"]}
        assert named == set(ACCESS_STATUSES)

    def test_unknown_is_still_called_unknown(self) -> None:
        """Deliberate. It is a prototype, other gaps in this area are plausible,
        and softening the word would hide them."""
        labels = {e["name"]: e["label"] for e in config.load_vocabulary()["access_statuses"]}
        assert labels["unknown"] == "Access unknown"


class TestIeaPublicationTypes:
    """The IEA Wind types live only in a title, so the matcher has to be tight.

    A false positive relabels somebody else's journal paper as an IEA Wind
    Recommended Practice, which is worse than leaving it generic.
    """

    @pytest.mark.parametrize(
        "title,expected",
        [
            # IEA Wind adjacency, phrase in type position.
            ("IEA Wind TCP Recommended practice 13: Wind Energy Projects in Cold Climates",
             "recommended-practice"),
            ("IEA Wind Task 43 Joint Best Practice on Classifying Digital Twins", "best-practice"),
            ("IEA Wind Task 43 Technical Report - Evolving the Wind Energy Sector",
             "technical-report"),
            ("IEA Wind TCP Annual Report 2025", "annual-report"),
            ("IEA-Wind Topical Expert Meeting on Wake Modelling", "tem-proceedings"),
            ("IEA Wind Expert Group Report on Wind Farm Data Collection", "expert-group-report"),
            # The phrase opens the title.
            ("Recommended Practice: Ontology creation, publication and maintenance",
             "recommended-practice"),
            # The phrase closes it, after a separator.
            ("Ontologies for wind energy domain experts – Recommended Practice",
             "recommended-practice"),
        ],
    )
    def test_it_recognises_a_type_claim(self, title: str, expected: str) -> None:
        assert iea_publication_type(title) == expected

    @pytest.mark.parametrize(
        "title",
        [
            # The real example this guard exists for: a paper ABOUT best practice.
            "Definition of Best Practice for Testing Icephobic Surfaces",
            # IEA adjacency, but the phrase names the subject: "…practices FOR…".
            "IEA Wind Task 32 : best practices for the certification of lidar-assisted control",
            "A review of recommended practices in offshore wind resource assessment",
            "Towards best practice in wind farm noise assessment",
            "",
        ],
    )
    def test_it_refuses_a_subject_mention(self, title: str) -> None:
        assert iea_publication_type(title) is None

    def test_a_recommended_practice_wins_over_the_series_wrapper(self) -> None:
        """IEA Wind's RPs are published as "EXPERT GROUP STUDY ON RECOMMENDED
        PRACTICES 15. …" — that document is Recommended Practice 15, and the
        expert-group wording is the series it appears in. Phrase order in the
        matcher is what settles it, so it is pinned here.
        """
        assert (
            iea_publication_type("IEA Wind Expert Group Study on Recommended Practices 15")
            == "recommended-practice"
        )

    def test_it_does_not_override_a_journal_article(self) -> None:
        """Crossref says journal-article; a phrase in the title does not undo that."""
        effective = {
            "title": "IEA Wind Task 43: best practice on data standards",
            "resource_kind": "publication",
            "extra": {"crossref_type": "journal-article"},
        }
        assert derive(effective, ["crossref"]) == ("publication", "journal-article")


class TestSourceVocabularies:
    @pytest.mark.parametrize(
        "extra,expected",
        [
            # Zenodo — the only source with a subtype, so it is tried first.
            ({"zenodo_resource_type": {"type": "publication", "subtype": "deliverable"}},
             ("report", "project-deliverable")),
            ({"zenodo_resource_type": {"type": "presentation"}}, ("other", "presentation")),
            ({"zenodo_resource_type": {"type": "event"}}, ("other", "meeting-record")),
            ({"zenodo_resource_type": {"type": "poster"}}, ("other", "poster")),
            ({"zenodo_resource_type": {"type": "publication", "subtype": "article"}},
             ("publication", "journal-article")),
            # OSTI.
            ({"osti_product_type": "Technical Report"}, ("report", "technical-report")),
            ({"osti_product_type": "Conference"}, ("publication", "conference-paper")),
            # Crossref.
            ({"crossref_type": "proceedings-article"}, ("publication", "conference-paper")),
            ({"crossref_type": "posted-content"}, ("publication", "preprint")),
            # DataCite: the free-text resourceType beats the controlled general one.
            ({"datacite_types": {"resourceType": "Project deliverable",
                                 "resourceTypeGeneral": "Text"}},
             ("report", "project-deliverable")),
            # …and resourceTypeGeneral is the fallback when it says nothing.
            ({"datacite_types": {"resourceType": "", "resourceTypeGeneral": "ConferencePaper"}},
             ("publication", "conference-paper")),
            # "Text" and "Other" say nothing at all; fall through to the kind.
            ({"datacite_types": {"resourceType": "", "resourceTypeGeneral": "Text"}},
             ("publication", "publication")),
        ],
    )
    def test_it_reads_the_source_vocabulary(self, extra: dict, expected: tuple) -> None:
        effective = {"resource_kind": "publication", "title": "A title", "extra": extra}
        assert derive(effective, ["zenodo"]) == expected

    def test_the_specific_type_corrects_the_coarse_kind(self) -> None:
        """A "Project deliverable" the adapter flattened to `publication` is grey
        literature, and `report` is where a reader looking for it will go."""
        effective = {
            "resource_kind": "publication",
            "title": "WP3 deliverable",
            "extra": {"zenodo_resource_type": {"type": "publication", "subtype": "deliverable"}},
        }
        kind, _ = derive(effective, ["zenodo"])
        assert kind == "report"

    def test_a_record_with_no_signal_gets_the_generic_child(self) -> None:
        for kind in RESOURCE_KINDS:
            got_kind, got_type = derive({"resource_kind": kind, "extra": {}}, [])
            assert got_type is not None, kind
            assert KIND_OF_TYPE[got_type] == got_kind

    def test_a_record_with_no_kind_gets_no_invented_type(self) -> None:
        assert derive({"extra": {}}, []) == (None, None)


class TestResearchSoftware:
    """FAIR4RS draws the line at intent, which is in no payload. These are the
    three signals that do travel with a record."""

    def test_a_doi_is_research_provenance(self) -> None:
        assert research_software_type({"doi": "10.5281/zenodo.1"}) == "research-software"

    def test_a_task_attribution_is_research_provenance(self) -> None:
        assert research_software_type({"iea_task": ["task-43"]}) == "research-software"

    def test_a_research_repository_is_research_provenance(self) -> None:
        assert research_software_type({"_source_systems": ["zenodo"]}) == "research-software"
        assert research_software_type({"_source_systems": ["osti"]}) == "research-software"

    def test_a_bare_code_host_repository_is_not(self) -> None:
        """This is what separates windIO and OpenOA from the wind-adjacent
        company repositories a GitHub topic sweep also finds."""
        assert research_software_type({"_source_systems": ["github"]}) == "other-software"
        assert research_software_type({}) == "other-software"

    def test_both_values_sit_under_software(self) -> None:
        """So "all software" still finds them, and either can be asked for alone."""
        assert KIND_OF_TYPE["research-software"] == "software"
        assert KIND_OF_TYPE["other-software"] == "software"

    def test_derive_routes_software_through_the_rule(self) -> None:
        github = {"resource_kind": "software", "extra": {}}
        assert derive(github, ["github"]) == ("software", "other-software")
        deposited = {"resource_kind": "software", "doi": "10.5281/zenodo.1", "extra": {}}
        assert derive(deposited, ["github"]) == ("software", "research-software")


class TestPurity:
    def test_it_is_offline_and_deterministic(self) -> None:
        """The whole reason this is a materialise-time derivation and not a
        re-harvest: it reads only what the event log already holds."""
        effective = {
            "resource_kind": "publication",
            "title": "IEA Wind TCP Recommended practice 13: Cold Climates",
            "extra": {"datacite_types": {"resourceTypeGeneral": "Report"}},
        }
        first = derive(effective, ["datacite"])
        assert first == derive(dict(effective), ["datacite"]) == ("report", "recommended-practice")
