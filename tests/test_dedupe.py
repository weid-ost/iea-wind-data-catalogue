"""Cross-source identity merging.

The first test in this file is the most important one: the four-way merge that
identity alone already solves. If it ever needs reconciliation code, something
has gone wrong upstream of here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest.dedupe import (
    DedupeResult,
    MergeCandidate,
    apply_merge,
    dedupe,
    find_candidates,
    load_resolved,
    read_proposals,
    write_proposals,
)
from harvest import config
from harvest.events import read_events, record_scrape, resolve
from harvest.materialize import materialize_all


def scrape(events_dir: Path, key: str, system: str, source_id: str, **source) -> None:
    record_scrape(
        identity_key=key,
        source_system=system,
        source_id=source_id,
        source_key=source.pop("source_key", "1"),
        source=source,
        events_dir=events_dir,
        observed_at=source.pop("observed_at", "2026-08-24T03:11:07Z"),
    )


def extras_of(records_dir: Path, slug: str) -> dict[str, str]:
    package = json.loads((records_dir / f"{slug}.json").read_text(encoding="utf-8"))
    return {extra["key"]: extra["value"] for extra in package["extras"]}


# ---------------------------------------------------------------------------
# x-01 — the merge that needs no merging
# ---------------------------------------------------------------------------


class TestIdentityDoesMostOfIt:
    def test_four_sources_one_doi_one_record_four_source_urls(
        self, repo: Path, events_dir: Path
    ) -> None:
        key = "10.5072/zenodo.1234566"
        scrape(events_dir, key, "zenodo", "1234567", title="Lidar campaign",
               url="https://sandbox.zenodo.org/records/1234567",
               source_urls=["https://sandbox.zenodo.org/records/1234567"], license_id="cc-by")
        scrape(events_dir, key, "datacite", key, title="Lidar campaign",
               url="https://doi.org/10.5072/zenodo.1234566",
               source_urls=["https://doi.org/10.5072/zenodo.1234566"], license_id="cc-by",
               observed_at="2026-08-24T03:12:00Z")
        scrape(events_dir, key, "github", "IEA-Task-43/lidar", title="lidar",
               url="https://github.com/IEA-Task-43/lidar",
               source_urls=["https://github.com/IEA-Task-43/lidar"], license_id="mit",
               observed_at="2026-08-24T03:13:00Z")
        scrape(events_dir, key, "ieawind", "task43/outputs", title="Lidar campaign data",
               url="https://iea-wind.org/task43/outputs/",
               source_urls=["https://iea-wind.org/task43/outputs/"],
               observed_at="2026-08-24T03:14:00Z")

        materialize_all(events_dir, repo / "records", root=repo)
        assert len(list((repo / "records").glob("*.json"))) == 1

        extras = extras_of(repo / "records", "doi-10-5072-zenodo-1234566")
        assert len(json.loads(extras["source_urls"])) == 4
        assert json.loads(extras["source_systems"]) == [
            "datacite", "github", "ieawind", "zenodo"
        ]
        # DataCite outranks the rest in sources.yaml, so it supplies the scalars:
        # the record's landing page is the DOI, not the GitHub repo that also
        # described it.
        package = json.loads(
            (repo / "records" / "doi-10-5072-zenodo-1234566.json").read_text(encoding="utf-8")
        )
        assert package["url"] == "https://doi.org/10.5072/zenodo.1234566"
        assert package["license_id"] == "cc-by"
        assert dedupe(events_dir, root=repo).merges == [], "nothing to reconcile"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_a_shared_doi_across_different_identities_is_automatic(
        self, repo: Path, events_dir: Path
    ) -> None:
        """osti-03: a mandated OSTI deposit of an already-published article."""
        scrape(events_dir, "10.1088/1742-6596/2265/2/022001", "crossref",
               "10.1088/1742-6596/2265/2/022001", title="Nacelle lidar validation",
               doi="10.1088/1742-6596/2265/2/022001",
               url="https://doi.org/10.1088/1742-6596/2265/2/022001")
        scrape(events_dir, "osti|1854723", "osti", "1854723",
               title="Nacelle Lidar Validation", doi="10.1088/1742-6596/2265/2/022001",
               url="https://www.osti.gov/biblio/1854723",
               source_urls=["https://www.osti.gov/biblio/1854723"])

        [candidate] = find_candidates(load_resolved(events_dir), root=repo)
        assert candidate.kind == "shared-doi"
        assert candidate.automatic is True
        assert candidate.primary == "10.1088/1742-6596/2265/2/022001"
        assert candidate.secondary == "osti|1854723"

    def test_a_zenodo_doi_badge_joins_a_repository_to_its_archive(
        self, repo: Path, events_dir: Path
    ) -> None:
        """zen-04 / gh-02: the free join key."""
        scrape(events_dir, "10.5281/zenodo.7654321", "zenodo", "7654321",
               title="digital-wra-data-standard: v1.3.0", doi="10.5281/zenodo.7654321",
               url="https://zenodo.org/records/7654321")
        scrape(events_dir, "github|IEA-Task-43/digital-wra-data-standard", "github",
               "IEA-Task-43/digital-wra-data-standard", title="digital-wra-data-standard",
               url="https://github.com/IEA-Task-43/digital-wra-data-standard",
               related_identifiers=[{"relation": "IsIdenticalTo",
                                     "identifier": "10.5281/zenodo.7654321",
                                     "identifier_type": "DOI"}])

        [candidate] = find_candidates(load_resolved(events_dir), root=repo)
        assert candidate.kind == "related-identifier"
        assert candidate.primary == "10.5281/zenodo.7654321"
        assert candidate.automatic is True

    def test_a_github_url_in_related_identifiers_joins_the_other_way(
        self, repo: Path, events_dir: Path
    ) -> None:
        scrape(events_dir, "10.5281/zenodo.7654321", "zenodo", "7654321",
               title="software release", doi="10.5281/zenodo.7654321",
               url="https://zenodo.org/records/7654321",
               related_identifiers=[{
                   "relation": "IsSourceOf",
                   "identifier": "https://github.com/IEA-Task-43/digital-wra-data-standard",
                   "identifier_type": "URL"}])
        scrape(events_dir, "github|IEA-Task-43/digital-wra-data-standard", "github",
               "IEA-Task-43/digital-wra-data-standard", title="digital-wra-data-standard",
               url="https://github.com/IEA-Task-43/digital-wra-data-standard")

        [candidate] = find_candidates(load_resolved(events_dir), root=repo)
        assert candidate.primary == "10.5281/zenodo.7654321"
        assert candidate.secondary == "github|IEA-Task-43/digital-wra-data-standard"

    def test_a_preprint_loses_to_its_published_version(
        self, repo: Path, events_dir: Path
    ) -> None:
        """cr-04: prefer the published version; link the preprint from it."""
        scrape(events_dir, "10.5194/egusphere-2023-1234", "crossref",
               "10.5194/egusphere-2023-1234", title="Wake turbulence, preprint",
               doi="10.5194/egusphere-2023-1234",
               url="https://doi.org/10.5194/egusphere-2023-1234",
               related_identifiers=[{"relation": "IsPreprintOf",
                                     "identifier": "10.5194/wes-9-101-2024",
                                     "identifier_type": "DOI"}])
        scrape(events_dir, "10.5194/wes-9-101-2024", "crossref", "10.5194/wes-9-101-2024",
               title="Wake turbulence", doi="10.5194/wes-9-101-2024",
               url="https://doi.org/10.5194/wes-9-101-2024")

        [candidate] = find_candidates(load_resolved(events_dir), root=repo)
        assert candidate.kind == "preprint-pair"
        assert candidate.primary == "10.5194/wes-9-101-2024"
        assert candidate.secondary == "10.5194/egusphere-2023-1234"

    def test_a_merely_related_identifier_is_not_a_merge(
        self, repo: Path, events_dir: Path
    ) -> None:
        """dc-06: IsSupplementTo links a paper to its data; it does not merge them."""
        scrape(events_dir, "10.5281/zenodo.111", "zenodo", "111", title="The data",
               doi="10.5281/zenodo.111", url="https://zenodo.org/records/111",
               related_identifiers=[{"relation": "IsSupplementTo",
                                     "identifier": "10.5194/wes-9-101-2024",
                                     "identifier_type": "DOI"}])
        scrape(events_dir, "10.5194/wes-9-101-2024", "crossref", "10.5194/wes-9-101-2024",
               title="The paper", doi="10.5194/wes-9-101-2024",
               url="https://doi.org/10.5194/wes-9-101-2024")
        assert find_candidates(load_resolved(events_dir), root=repo) == []


class TestFuzzyMatchesAreProposalsOnly:
    def _two_near_identical(self, events_dir: Path) -> None:
        for key, system, url in (
            ("10.5281/zenodo.6000006", "zenodo", "https://zenodo.org/records/6000006"),
            ("10.11583/dtu.6000007", "datacite", "https://doi.org/10.11583/dtu.6000007"),
        ):
            scrape(events_dir, key, system, key, doi=key, url=url,
                   title="Probabilistic wake loss estimation for large offshore arrays",
                   authors=[{"name": "Nowak, Piotr"}], published_date="2024-09-02")

    def test_dc08_proposes_but_never_applies(self, repo: Path, events_dir: Path) -> None:
        self._two_near_identical(events_dir)
        result = dedupe(events_dir, root=repo, apply=True)
        assert result.applied == []
        assert result.merges == []
        [proposal] = result.proposals
        assert proposal.automatic is False
        assert proposal.kind == "fuzzy-title"
        assert "REVIEW REQUIRED" in proposal.evidence

    def test_applying_a_fuzzy_candidate_by_hand_is_refused(
        self, repo: Path, events_dir: Path
    ) -> None:
        self._two_near_identical(events_dir)
        records = load_resolved(events_dir)
        [proposal] = [c for c in find_candidates(records, root=repo) if not c.automatic]
        with pytest.raises(ValueError, match="never merges"):
            apply_merge(proposal, records, events_dir=events_dir)

    def test_a_different_year_is_not_a_match(self, repo: Path, events_dir: Path) -> None:
        for key, year in (("10.5281/zenodo.1", "2023-01-01"), ("10.5281/zenodo.2", "2024-01-01")):
            scrape(events_dir, key, "zenodo", key, doi=key, title="One identical title",
                   authors=[{"name": "Nowak, Piotr"}], published_date=year,
                   url=f"https://zenodo.org/records/{key[-1]}")
        assert dedupe(events_dir, root=repo).proposals == []

    def test_no_author_or_year_means_no_guess_at_all(
        self, repo: Path, events_dir: Path
    ) -> None:
        for key in ("10.5281/zenodo.1", "10.5281/zenodo.2"):
            scrape(events_dir, key, "zenodo", key, doi=key, title="One identical title",
                   url=f"https://zenodo.org/records/{key[-1]}")
        assert dedupe(events_dir, root=repo).proposals == []

    def test_a_dissimilar_title_is_not_proposed(self, repo: Path, events_dir: Path) -> None:
        for key, title in (
            ("10.5281/zenodo.1", "Probabilistic wake loss estimation"),
            ("10.5281/zenodo.2", "Blade root strain gauge calibration"),
        ):
            scrape(events_dir, key, "zenodo", key, doi=key, title=title,
                   authors=[{"name": "Nowak, Piotr"}], published_date="2024-09-02",
                   url=f"https://zenodo.org/records/{key[-1]}")
        assert dedupe(events_dir, root=repo).proposals == []


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class TestApplying:
    def _osti_pair(self, events_dir: Path) -> None:
        scrape(events_dir, "10.1088/1742-6596/2265/2/022001", "crossref",
               "10.1088/1742-6596/2265/2/022001", title="Nacelle lidar validation",
               doi="10.1088/1742-6596/2265/2/022001", license_id="cc-by",
               url="https://doi.org/10.1088/1742-6596/2265/2/022001")
        scrape(events_dir, "osti|1854723", "osti", "1854723", license_id="cc-by",
               title="Nacelle Lidar Validation", doi="10.1088/1742-6596/2265/2/022001",
               url="https://www.osti.gov/biblio/1854723")

    def test_a_merge_is_two_annotations_and_no_deletions(
        self, repo: Path, events_dir: Path
    ) -> None:
        self._osti_pair(events_dir)
        result = dedupe(events_dir, root=repo, apply=True)
        assert len(result.applied) == 1

        materialize_all(events_dir, repo / "records", root=repo)
        names = sorted(p.stem for p in (repo / "records").glob("*.json"))
        assert names == ["doi-10-1088-1742-6596-2265-2-022001", "osti-1854723"], (
            "both records are retained; a merge is a suppression, never a deletion"
        )

        primary = extras_of(repo / "records", "doi-10-1088-1742-6596-2265-2-022001")
        assert "https://www.osti.gov/biblio/1854723" in json.loads(primary["source_urls"])
        assert "suppressed" not in primary

        secondary = extras_of(repo / "records", "osti-1854723")
        assert secondary["suppressed"] == "true"
        assert "10.1088" in secondary["local_links"]

    def test_applying_twice_changes_nothing(self, repo: Path, events_dir: Path) -> None:
        self._osti_pair(events_dir)
        dedupe(events_dir, root=repo, apply=True)
        materialize_all(events_dir, repo / "records", root=repo)
        before = {p.name: p.read_text() for p in (repo / "records").glob("*.json")}

        second = dedupe(events_dir, root=repo, apply=True)
        assert second.applied == []
        assert len(second.already_merged) == 1
        materialize_all(events_dir, repo / "records", root=repo)
        after = {p.name: p.read_text() for p in (repo / "records").glob("*.json")}
        assert before == after

    def test_without_apply_nothing_is_written(self, repo: Path, events_dir: Path) -> None:
        self._osti_pair(events_dir)
        result = dedupe(events_dir, root=repo, apply=False)
        assert len(result.merges) == 1 and result.applied == []
        resolved = resolve("osti|1854723", events_dir=events_dir)
        assert not resolved.local.get("suppressed")

    def test_the_merge_is_recorded_as_an_auditable_event(
        self, repo: Path, events_dir: Path
    ) -> None:
        from harvest.events import read_events

        self._osti_pair(events_dir)
        dedupe(events_dir, root=repo, apply=True)
        events = [e for e in read_events("osti|1854723", events_dir)
                  if e.event_type == "annotated"]
        assert len(events) == 1
        assert events[0].actor == "reconcile"
        assert "shared-doi" in (events[0].note or "")


class TestTheProposalFile:
    def test_it_round_trips_and_is_deterministic(self, repo: Path) -> None:
        result = DedupeResult(
            proposals=[MergeCandidate("a", "b", "fuzzy-title", 0.93, "looks alike")],
            merges=[MergeCandidate("c", "d", "shared-doi", 1.0, "same doi", automatic=True)],
        )
        path = write_proposals(result, root=repo)
        first = path.read_text(encoding="utf-8")
        write_proposals(result, root=repo)
        assert path.read_text(encoding="utf-8") == first

        payload = read_proposals(root=repo)
        assert payload["proposals"][0]["primary"] == "a"
        assert payload["merges"][0]["kind"] == "shared-doi"

    def test_reading_before_any_pass_is_empty(self, repo: Path) -> None:
        assert read_proposals(root=repo) == {}


class TestDegradation:
    def test_an_empty_log_is_not_an_error(self, repo: Path, events_dir: Path) -> None:
        result = dedupe(events_dir, root=repo, apply=True)
        assert result.merges == [] and result.proposals == [] and result.errors == []


class TestTaskCandidatesArePromoted:
    """scrape-10: the documented promotion that no code performed.

    DataCite's and Crossref's docstrings both said the reconciler validates
    ``source.extra.iea_task_candidates`` and writes ``local.iea_task``. Nothing
    read the key: a grep across ``harvest/``, ``site/src``, ``docs/`` and
    ``tests/`` found the two producers and one assertion, and no consumer. Every
    DataCite and Crossref task attribution died in an unrendered extra.
    """

    def _seed(self, events_dir: Path, candidates: list[str], **extra) -> str:  # noqa: ANN003
        key = "10.5281/zenodo.4242"
        record_scrape(
            key, "datacite", "4242", "rev-1",
            {
                "title": "A dataset from a task",
                "url": "https://example.org/4242",
                "extra": {"iea_task_candidates": candidates, **extra},
            },
            events_dir=events_dir, observed_at="2026-01-01T00:00:00Z",
        )
        return key

    def test_a_registered_candidate_becomes_a_task(self, repo: Path, events_dir: Path) -> None:
        key = self._seed(events_dir, ["task-43"])

        dedupe(events_dir, root=repo, apply=True, observed_at="2026-02-01T00:00:00Z")

        assert resolve(key, events_dir=events_dir).effective["iea_task"] == ["task-43"]

    def test_it_is_badged_as_inference_not_as_something_a_registry_stated(
        self, repo: Path, events_dir: Path
    ) -> None:
        key = self._seed(events_dir, ["task-43"])

        dedupe(events_dir, root=repo, apply=True, observed_at="2026-02-01T00:00:00Z")

        provenance = resolve(key, events_dir=events_dir).provenance["iea_task"]
        assert provenance.extraction_method == "pattern"

    def test_an_unregistered_candidate_stays_a_candidate(
        self, repo: Path, events_dir: Path
    ) -> None:
        """The reason the adapters refused to write it themselves, still honoured."""
        key = self._seed(events_dir, ["task-999"])

        dedupe(events_dir, root=repo, apply=True, observed_at="2026-02-01T00:00:00Z")

        resolved = resolve(key, events_dir=events_dir)
        assert not resolved.effective.get("iea_task")
        assert resolved.effective["extra"]["iea_task_candidates"] == ["task-999"]

    def test_a_renumbered_candidate_lands_on_the_task_that_exists(
        self, repo: Path, events_dir: Path
    ) -> None:
        key = self._seed(events_dir, ["task-19"])

        dedupe(events_dir, root=repo, apply=True, observed_at="2026-02-01T00:00:00Z")

        assert resolve(key, events_dir=events_dir).effective["iea_task"] == [
            config.canonical_group("task-19")
        ]

    def test_the_promotion_is_reported(self, repo: Path, events_dir: Path) -> None:
        self._seed(events_dir, ["task-43"])

        result = dedupe(events_dir, root=repo, apply=True, observed_at="2026-02-01T00:00:00Z")

        assert [notice["type"] for notice in result.promotions] == ["task_candidate_promoted"]
        assert any(n["type"] == "task_candidate_promoted" for n in result.as_notices())

    def test_a_preview_pass_writes_nothing(self, repo: Path, events_dir: Path) -> None:
        key = self._seed(events_dir, ["task-43"])

        result = dedupe(events_dir, root=repo, apply=False)

        assert result.promotions, "a preview still says what it would do"
        assert not resolve(key, events_dir=events_dir).effective.get("iea_task")

    def test_promoting_twice_appends_once(self, repo: Path, events_dir: Path) -> None:
        key = self._seed(events_dir, ["task-43"])
        dedupe(events_dir, root=repo, apply=True, observed_at="2026-02-01T00:00:00Z")
        before = len(read_events(key, events_dir))

        again = dedupe(events_dir, root=repo, apply=True, observed_at="2026-03-01T00:00:00Z")

        assert again.promotions == []
        assert len(read_events(key, events_dir)) == before

    def test_a_record_with_no_candidates_is_untouched(
        self, repo: Path, events_dir: Path
    ) -> None:
        record_scrape(
            "10.5281/zenodo.1", "zenodo", "1", "rev-1",
            {"title": "T", "url": "https://example.org/1"},
            events_dir=events_dir, observed_at="2026-01-01T00:00:00Z",
        )
        assert dedupe(events_dir, root=repo, apply=True).promotions == []
