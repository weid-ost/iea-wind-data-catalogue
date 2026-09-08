"""The catalogue's scope rule (ADR-0043).

    "Things that belong in the catalogue must either be directly attributed to
    IEA Wind (ie be tagged, or published in our communities or websites in some
    way), or cited by work which is in the catalogue, or cite things which are
    in the catalogue. And ideally we should record which of those is the case."

These tests pin all four parts: the three limbs, the order they are tried in,
the evidence recorded for each, and — the part that makes the rule safe — that
a record failing all three is retained rather than deleted.
"""

from __future__ import annotations

import pytest

from harvest import config
from harvest.inclusion import (
    INCLUSION_BASES,
    alias_index,
    citation_edges,
    decide,
    generic_routes,
)
from harvest.models import ResolvedRecord


def record(key: str, *, routes=(), tasks=(), doi=None, references=(), cited_by=()) -> ResolvedRecord:
    related = [
        {"relation": "References", "identifier": r, "identifier_type": "DOI"} for r in references
    ] + [
        {"relation": "IsCitedBy", "identifier": c, "identifier_type": "DOI"} for c in cited_by
    ]
    return ResolvedRecord(
        identity_key=key,
        slug=key.replace("/", "-").replace(".", "-"),
        effective={
            "iea_task": list(tasks),
            "doi": doi,
            "related_identifiers": related,
            "extra": {},
        },
        discovered_via=list(routes),
    )


def resolve_all(*records: ResolvedRecord) -> dict[str, ResolvedRecord]:
    return {r.identity_key: r for r in records}


def basis_of(records, key, generic=frozenset()):
    cites, cited_by = citation_edges(records)
    return decide(key, records[key], cites, cited_by, set(generic))


class TestTheRegisterDeclaresTheGenericRoutes:
    def test_the_generic_routes_are_read_from_sources_yaml(self) -> None:
        """The judgement lives in the register, where a human can see and change
        it, not in the code."""
        assert generic_routes() == {"topic:wind-energy", "query:no-match"}

    def test_every_generic_route_is_one_an_adapter_can_actually_emit(self) -> None:
        """A typo here silently turns an exclusion back into an attribution, and
        nothing else would notice."""
        sources = config.load_sources()
        for name, source in sources.items():
            for route in (source or {}).get("generic_routes") or []:
                kind, _, value = str(route).partition(":")
                assert kind in {"topic", "query", "org", "community", "repo", "task-page"}, route
                if kind == "topic":
                    assert value in [str(t) for t in source.get("topics") or []], route
                if kind == "query" and value != "no-match":
                    declared = [str(q) for q in source.get("queries") or []]
                    assert value in declared, route

    def test_no_match_is_a_recorded_assessment_not_a_gap(self) -> None:
        """`query:no-match` means the adapter went back and asked, and the
        source associates the record with none of our queries. That is evidence
        of non-attribution, so it excludes — unlike having never looked."""
        assert "query:no-match" in generic_routes()

    def test_the_iea_named_topics_are_not_generic(self) -> None:
        """Applying `iea-wind` to a repository is a claim about the work, made by
        whoever published it. `wind-energy` is not."""
        generic = generic_routes()
        assert "topic:iea-wind" not in generic
        assert "topic:iea-task-43" not in generic


class TestDirectAttribution:
    def test_an_attributing_route_is_enough(self) -> None:
        records = resolve_all(record("a", routes=["community:iea_wind_task_43"]))
        basis, evidence = basis_of(records, "a")
        assert basis == "direct"
        assert "community:iea_wind_task_43" in evidence

    def test_a_task_attribution_is_enough(self) -> None:
        records = resolve_all(record("a", tasks=["task-43"]))
        basis, evidence = basis_of(records, "a")
        assert basis == "direct"
        assert "task-43" in evidence

    def test_a_generic_route_alone_is_not(self) -> None:
        records = resolve_all(record("a", routes=["topic:wind-energy"]))
        assert basis_of(records, "a", {"topic:wind-energy"})[0] == "none"

    def test_a_generic_route_beside_an_attributing_one_still_qualifies(self) -> None:
        """A repository in an IEA Wind org that also carries the topic was, in
        fact, reachable both ways. The strongest claim available wins."""
        records = resolve_all(
            record("a", routes=["topic:wind-energy", "org:IEAWindSystems"])
        )
        basis, evidence = basis_of(records, "a", {"topic:wind-energy"})
        assert basis == "direct"
        assert "org:IEAWindSystems" in evidence
        assert "wind-energy" not in evidence


class TestCitationLimbs:
    def test_citing_something_in_the_catalogue_qualifies(self) -> None:
        held = record("10.5281/zenodo.1", routes=["community:iea_wind_task_43"],
                      doi="10.5281/zenodo.1")
        citing = record("github|x/y", routes=["topic:wind-energy"],
                        references=["10.5281/zenodo.1"])
        records = resolve_all(held, citing)
        basis, evidence = basis_of(records, "github|x/y", {"topic:wind-energy"})
        assert basis == "cites"
        assert "10.5281/zenodo.1" in evidence

    def test_one_stated_edge_fills_both_directions(self) -> None:
        """Crossref publishes reference lists but not citing-work lists, so the
        citing side's metadata has to settle the question for the cited side."""
        cited = record("10.5281/zenodo.1", routes=["topic:wind-energy"], doi="10.5281/zenodo.1")
        citing = record("10.5194/wes-1", routes=["topic:wind-energy"],
                        references=["10.5281/zenodo.1"])
        records = resolve_all(cited, citing)
        assert basis_of(records, "10.5194/wes-1", {"topic:wind-energy"})[0] == "cites"
        assert basis_of(records, "10.5281/zenodo.1", {"topic:wind-energy"})[0] == "cited-by"

    def test_citing_something_the_catalogue_does_not_hold_does_not_qualify(self) -> None:
        records = resolve_all(
            record("github|x/y", routes=["topic:wind-energy"], references=["10.1016/j.stranger"])
        )
        assert basis_of(records, "github|x/y", {"topic:wind-energy"})[0] == "none"

    def test_a_crossref_reference_list_supplies_the_edge(self) -> None:
        held = record("10.5281/zenodo.1", routes=["community:iea_wind_task_43"],
                      doi="10.5281/zenodo.1")
        citing = record("10.5194/wes-1", routes=["topic:wind-energy"])
        citing.effective["extra"] = {"crossref_references": ["10.5281/zenodo.1"]}
        records = resolve_all(held, citing)
        assert basis_of(records, "10.5194/wes-1", {"topic:wind-energy"})[0] == "cites"

    def test_a_record_never_cites_itself_into_scope(self) -> None:
        records = resolve_all(
            record("10.5281/zenodo.1", routes=["topic:wind-energy"], doi="10.5281/zenodo.1",
                   references=["10.5281/zenodo.1"])
        )
        assert basis_of(records, "10.5281/zenodo.1", {"topic:wind-energy"})[0] == "none"


class TestTheOrderOfTheLimbs:
    def test_direct_beats_a_citation_link(self) -> None:
        """The strongest available grounds are the ones recorded, so the evidence
        line says the most convincing true thing about the record."""
        held = record("10.5281/zenodo.1", routes=["community:iea_wind_task_43"],
                      doi="10.5281/zenodo.1")
        both = record("10.5194/wes-1", routes=["query:iea-wind-task-by-title"],
                      references=["10.5281/zenodo.1"])
        records = resolve_all(held, both)
        assert basis_of(records, "10.5194/wes-1")[0] == "direct"

    def test_every_basis_is_a_declared_value(self) -> None:
        records = resolve_all(record("a"))
        assert basis_of(records, "a")[0] in INCLUSION_BASES

    def test_an_excluded_record_still_gets_a_reason(self) -> None:
        """"We looked and found no grounds" is a finding, not a missing field."""
        records = resolve_all(record("a", routes=["topic:wind-energy"]))
        basis, evidence = basis_of(records, "a", {"topic:wind-energy"})
        assert basis == "none"
        assert "No IEA Wind attribution" in evidence


class TestAbsenceOfEvidence:
    """`unassessed` is not `none`, and the difference matters.

    A record whose log predates discovery routes has never been assessed.
    Excluding it would punish it for a gap of ours; the rule is meant to act on
    evidence of non-attribution, not on the absence of evidence.
    """

    def test_no_route_at_all_is_unassessed_not_excluded(self) -> None:
        records = resolve_all(record("a"))
        basis, evidence = basis_of(records, "a", {"topic:wind-energy"})
        assert basis == "unassessed"
        assert "not been established" in evidence

    def test_a_generic_route_is_evidence_and_excludes(self) -> None:
        """Having looked and found only a generic route is a real finding."""
        records = resolve_all(record("a", routes=["topic:wind-energy"]))
        assert basis_of(records, "a", {"topic:wind-energy"})[0] == "none"


class TestTheAliasIndex:
    def test_a_record_answers_to_its_identity_and_its_doi(self) -> None:
        records = resolve_all(record("zenodo|123", doi="10.5281/zenodo.1"))
        alias = alias_index(records)
        assert alias["zenodo|123"] == "zenodo|123"
        assert alias["10.5281/zenodo.1"] == "zenodo|123"

    def test_a_merged_away_record_still_counts_as_held(self) -> None:
        """A version DOI merged into its concept is the SAME artifact, so a work
        citing the version DOI has cited something in the catalogue (ADR-0042)."""
        primary = record("10.5281/zenodo.1", routes=["community:jam"], doi="10.5281/zenodo.1")
        merged = record("10.5281/zenodo.2", doi="10.5281/zenodo.2")
        merged.local["suppressed"] = True
        citing = record("github|x/y", routes=["topic:wind-energy"],
                        references=["10.5281/zenodo.2"])
        records = resolve_all(primary, merged, citing)
        assert basis_of(records, "github|x/y", {"topic:wind-energy"})[0] == "cites"


class TestItIsReversible:
    def test_a_later_citation_brings_an_excluded_record_back(self) -> None:
        """The decision is re-made from scratch on every materialise, which is
        what makes an automatic exclusion acceptable: nothing is destroyed, and
        nothing needs a human to undo."""
        orphan = record("github|x/y", routes=["topic:wind-energy"])
        before = resolve_all(orphan)
        assert basis_of(before, "github|x/y", {"topic:wind-energy"})[0] == "none"

        report = record("10.5281/zenodo.9", routes=["community:jam"], doi="10.5281/zenodo.9")
        orphan_cited = record("github|x/y", routes=["topic:wind-energy"],
                              cited_by=["10.5281/zenodo.9"])
        after = resolve_all(report, orphan_cited)
        assert basis_of(after, "github|x/y", {"topic:wind-energy"})[0] == "cited-by"


class TestTheShippedCatalogue:
    """Data invariants, checked against `data/records/` as it actually is."""

    def _records(self):
        import json

        from harvest import config

        for path in sorted(config.records_dir().glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            yield path.stem, {e["key"]: e["value"] for e in record["extras"]}

    def test_every_record_states_a_basis(self) -> None:
        for slug, extras in self._records():
            assert extras.get("inclusion_basis") in INCLUSION_BASES, slug

    def test_nothing_is_left_unassessed(self) -> None:
        """`unassessed` is a gap in the harvest, not a property of the corpus.

        Every adapter records a discovery route at scrape time, and OSTI — whose
        rolling query window is the one place a record can lose its route —
        goes back and asks. So a record in this state means something did not
        run, and the right response is to run it, not to accept the state.
        """
        stranded = [slug for slug, extras in self._records()
                    if extras.get("inclusion_basis") == "unassessed"]
        assert stranded == [], (
            f"{len(stranded)} record(s) have no assessable discovery route: {stranded[:5]}. "
            "Run `python -m harvest run` — the adapters backfill routes — rather than "
            "accepting the state."
        )

    def test_every_record_carries_its_evidence(self) -> None:
        for slug, extras in self._records():
            assert extras.get("inclusion_evidence"), slug
