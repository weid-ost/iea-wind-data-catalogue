"""The GitHub adapter (Track D) — fixtures gh-01 .. gh-10.

Everything here is offline. ``map()`` is pure by contract, and the two things
that are not — GitHub itself and the DOI resolver — are injected as fakes, so
this suite never touches the network and never touches the real ``events/``.
"""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from harvest import config
from harvest.adapters.base import SourceConfig, SourceUnreachable, payload_hash, run_adapter
from harvest.adapters.github import (
    BADGE_MARKER,
    GitHubAdapter,
    badge_doi_candidates,
    concept_doi_of,
    content_bytes,
    exclusion_reason,
    known_repository_paths,
    readme_text,
    source_key_for,
)
from harvest.events import read_events
from harvest.http import FetchResult
from harvest.identity import slug_for_identity
from harvest.materialize import materialize_all
from harvest.models import RawObservation

FIXTURES = config.fixtures_dir() / "github"


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def load_fixtures() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("gh-*.json"))
    ]


def raw_payload(fixture: dict) -> dict:
    return json.loads((FIXTURES / fixture["raw"]).read_text(encoding="utf-8"))


def observation(fixture: dict) -> RawObservation:
    return RawObservation(
        source_system="github",
        source_id=fixture["source_id"],
        source_key=fixture["source_key"],
        fetched_at="2026-08-31T00:00:00Z",
        payload=raw_payload(fixture),
    )


ALL = load_fixtures()


def fixture_by_id(fixture_id: str) -> dict:
    for fixture in ALL:
        if fixture["fixture_id"] == fixture_id:
            return fixture
    raise AssertionError(f"fixture {fixture_id} is missing")


def test_every_catalogued_fixture_exists() -> None:
    """The catalogue rows this track owns are a required set, not a menu."""
    have = {fixture["fixture_id"] for fixture in ALL}
    assert {
        "gh-01-canonical",
        "gh-02-zenodo-badge",
        "gh-03-stale-badge",
        "gh-04-fork",
        "gh-05-archived",
        "gh-06-no-license",
        "gh-07-renamed",
        "gh-08-personal-account",
        "gh-09-monorepo",
        "gh-10-empty",
    } <= have


# ---------------------------------------------------------------------------
# map() against every fixture — raw payload in, expectation out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ALL, ids=lambda f: f["fixture_id"])
class TestMapEveryFixture:
    def test_identity_and_source_namespace(self, fixture: dict) -> None:
        mapped = GitHubAdapter().map(observation(fixture))
        assert mapped.identity_key == fixture["identity_key"]
        assert mapped.source.model_dump(mode="json", exclude_none=True) == fixture["source"]

    def test_provenance(self, fixture: dict) -> None:
        mapped = GitHubAdapter().map(observation(fixture))
        got = {
            key: value.model_dump(mode="json", exclude_none=True)
            for key, value in mapped.provenance.items()
        }
        assert got == fixture["provenance"]

    def test_the_source_key_is_recomputable_from_the_raw_payload(self, fixture: dict) -> None:
        assert source_key_for(raw_payload(fixture)) == fixture["source_key"]

    def test_the_slug_follows_from_the_identity_key_alone(self, fixture: dict) -> None:
        assert slug_for_identity(fixture["identity_key"]) == fixture["expected_slug"]

    def test_inclusion_matches_the_declared_expectation(self, fixture: dict) -> None:
        reason = exclusion_reason(raw_payload(fixture))
        assert (reason is None) is fixture["expected_included"], reason
        if not fixture["expected_included"]:
            assert reason == fixture["expected_exclusion_reason"]

    def test_everything_is_typed_software(self, fixture: dict) -> None:
        assert fixture["source"]["resource_kind"] == "software"   # gh-01

    def test_map_is_pure_enough_to_run_twice_identically(self, fixture: dict) -> None:
        first = GitHubAdapter().map(observation(fixture))
        second = GitHubAdapter().map(observation(fixture))
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ---------------------------------------------------------------------------
# The named cases
# ---------------------------------------------------------------------------


class TestGh01Canonical:
    def test_a_live_repo_maps_to_a_software_record(self) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        source = fixture["source"]
        assert fixture["identity_key"] == "github|IEA-Task-43/digital_wra_data_standard"
        assert source["url"] == "https://github.com/IEA-Task-43/digital_wra_data_standard"
        assert source["license_id"] == "bsd-3-clause"
        assert source["iea_task"] == ["task-43"]
        assert source["resources"], "the repository itself is a link resource"
        assert all(resource["url"] for resource in source["resources"])

    def test_the_catalogue_links_and_never_mirrors(self) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        for resource in fixture["source"]["resources"]:
            assert resource["url"].startswith("https://github.com/")


class TestGh02ZenodoBadge:
    """The badge is a free join key between the code and the archived release."""

    def test_identity_is_the_resolved_concept_doi(self) -> None:
        fixture = fixture_by_id("gh-02-zenodo-badge")
        assert fixture["identity_key"] == "10.5281/zenodo.15191296"
        assert fixture["expected_slug"] == "doi-10-5281-zenodo-15191296"
        assert fixture["source"]["doi"] == "10.5281/zenodo.15191296"

    def test_the_version_doi_is_kept_as_a_related_identifier(self) -> None:
        fixture = fixture_by_id("gh-02-zenodo-badge")
        assert fixture["source"]["related_identifiers"] == [
            {
                "relation": "HasVersion",
                "identifier": "10.5281/zenodo.15191297",
                "identifier_type": "DOI",
            }
        ]

    def test_the_doi_is_marked_as_pattern_extracted_not_api(self) -> None:
        fixture = fixture_by_id("gh-02-zenodo-badge")
        assert fixture["provenance"]["doi"] == {"extraction_method": "pattern"}

    def test_the_badge_line_is_the_only_doi_source(self) -> None:
        """windIO's README also cites 10.2172/1868328. It is not the identity."""
        text = readme_text(raw_payload(fixture_by_id("gh-02-zenodo-badge")))
        assert "10.2172/1868328" in text
        assert badge_doi_candidates(text) == ["10.5281/zenodo.15191297"]

    def test_the_badge_image_suffix_is_stripped_before_resolution(self) -> None:
        line = f"[![DOI](https://{BADGE_MARKER}/DOI/10.5281/zenodo.15191297.svg)]"
        assert badge_doi_candidates(line) == ["10.5281/zenodo.15191297"]

    def test_a_doi_outside_a_badge_is_never_a_candidate(self) -> None:
        assert badge_doi_candidates("See https://doi.org/10.5072/zenodo.1234567") == []

    def test_a_markdown_badge_does_not_match_as_one_long_doi(self) -> None:
        """The real IEAWindSystems/IEA-22-280-RWT badge. Brackets and
        parentheses are all legal DOI characters, so the whole line matches as
        one malformed DOI unless the markup is tokenised away first."""
        line = (
            "[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.10944127.svg)]"
            "(https://doi.org/10.5281/zenodo.10944127)"
        )
        assert badge_doi_candidates(line) == ["10.5281/zenodo.10944127"]

    def test_an_rst_badge_directive_works_too(self) -> None:
        block = (
            ".. image:: https://zenodo.org/badge/DOI/10.5281/zenodo.15191297.svg\n"
            "  :target: https://doi.org/10.5281/zenodo.15191297\n"
            "  :alt: DOI\n"
        )
        assert badge_doi_candidates(block) == ["10.5281/zenodo.15191297"]


class TestGh03StaleBadge:
    """Resolve-or-drop applies to badges too."""

    def test_a_non_resolving_badge_falls_back_to_the_repo_identity(self) -> None:
        fixture = fixture_by_id("gh-03-stale-badge")
        assert fixture["identity_key"] == "github|IEAWindSystems/windIO"
        assert "doi" not in fixture["source"]
        assert "doi" not in fixture["provenance"]

    def test_the_drop_is_recorded_on_the_record_not_silently_discarded(self) -> None:
        fixture = fixture_by_id("gh-03-stale-badge")
        assert fixture["source"]["extra"]["github_dropped_badge_dois"] == [
            "10.5281/zenodo.99999999"
        ]

    def test_the_resolver_decides_not_the_adapter(self, fake_client) -> None:
        """A badge DOI enters a record only after DataCite or Crossref says yes."""
        adapter = GitHubAdapter(doi_client=fake_client(known={}))
        envelope = raw_payload(fixture_by_id("gh-03-stale-badge"))
        outcome = adapter._resolve_badge(envelope, "IEAWindSystems/windIO")
        assert outcome["doi"] is None
        assert outcome["dropped_dois"] == ["10.5281/zenodo.99999999"]
        assert len(adapter.drop_log) == 1

    def test_a_resolving_badge_is_followed_to_its_concept_doi(self, fake_client) -> None:
        version, concept = "10.5281/zenodo.15191297", "10.5281/zenodo.15191296"

        class Resolver(fake_client):
            def get(self, url: str, **kwargs: Any):
                response = super().get(url, **kwargs)
                if url.endswith(version) and response.status_code == 200:
                    response._payload = {
                        "data": {
                            "attributes": {
                                "relatedIdentifiers": [
                                    {
                                        "relationType": "IsVersionOf",
                                        "relatedIdentifier": concept,
                                        "relatedIdentifierType": "DOI",
                                    }
                                ]
                            }
                        }
                    }
                return response

        adapter = GitHubAdapter(
            doi_client=Resolver(known={version: "datacite", concept: "datacite"})
        )
        envelope = raw_payload(fixture_by_id("gh-02-zenodo-badge"))
        outcome = adapter._resolve_badge(envelope, "IEAWindSystems/windIO")
        assert outcome == {
            "doi": concept,
            "version_doi": version,
            "dropped_dois": [],
            "badge_candidates": [version],
        }
        assert len(adapter.drop_log) == 0

    def test_a_version_doi_whose_concept_doi_is_dead_keeps_the_version(
        self, fake_client
    ) -> None:
        version, concept = "10.5281/zenodo.15191297", "10.5281/zenodo.15191296"

        class Resolver(fake_client):
            def get(self, url: str, **kwargs: Any):
                response = super().get(url, **kwargs)
                if url.endswith(version) and response.status_code == 200:
                    response._payload = {
                        "data": {
                            "attributes": {
                                "relatedIdentifiers": [
                                    {
                                        "relationType": "IsVersionOf",
                                        "relatedIdentifier": concept,
                                        "relatedIdentifierType": "DOI",
                                    }
                                ]
                            }
                        }
                    }
                return response

        adapter = GitHubAdapter(doi_client=Resolver(known={version: "datacite"}))
        outcome = adapter._resolve_badge(
            raw_payload(fixture_by_id("gh-02-zenodo-badge")), "IEAWindSystems/windIO"
        )
        assert outcome["doi"] == version
        assert outcome["dropped_dois"] == [concept]

    def test_concept_doi_extraction(self) -> None:
        payload = {
            "data": {
                "attributes": {
                    "relatedIdentifiers": [
                        {"relationType": "IsSupplementTo",
                         "relatedIdentifier": "https://github.com/x/y",
                         "relatedIdentifierType": "URL"},
                        {"relationType": "IsVersionOf",
                         "relatedIdentifier": "10.5281/ZENODO.1",
                         "relatedIdentifierType": "DOI"},
                    ]
                }
            }
        }
        assert concept_doi_of(payload) == "10.5281/zenodo.1"
        assert concept_doi_of({"data": {"attributes": {}}}) is None
        assert concept_doi_of(None) is None


class TestGh04Fork:
    def test_forks_are_excluded_by_default(self) -> None:
        payload = raw_payload(fixture_by_id("gh-04-fork"))
        assert payload["repository"]["fork"] is True
        assert exclusion_reason(payload) == "fork"

    def test_the_exclusion_can_be_turned_off_deliberately(self) -> None:
        payload = raw_payload(fixture_by_id("gh-04-fork"))
        assert exclusion_reason(payload, exclude_forks=False) is None


class TestGh05Archived:
    """Marked and retained, never deleted (ADR-0027)."""

    def test_an_archived_repo_is_not_excluded(self) -> None:
        payload = raw_payload(fixture_by_id("gh-05-archived"))
        assert payload["repository"]["archived"] is True
        assert exclusion_reason(payload) is None

    def test_the_source_namespace_marks_it(self) -> None:
        fixture = fixture_by_id("gh-05-archived")
        assert fixture["source"]["archived"] is True
        assert fixture["source"]["withdrawn"] is False

    def test_the_record_materialises_with_lifecycle_state_archived(
        self, events_dir: Path, records_dir: Path
    ) -> None:
        fixture = fixture_by_id("gh-05-archived")
        adapter = GitHubAdapter(events_directory=events_dir)
        run_adapter(_StaticAdapter(adapter, [observation(fixture)]),
                    limit=5, events_dir=events_dir)
        result = materialize_all(events_dir, records_dir, validate=False)
        package = json.loads(
            (records_dir / f"{fixture['expected_slug']}.json").read_text(encoding="utf-8")
        )
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert extras["lifecycle_state"] == "archived"
        assert extras["withdrawn"] == "false"
        assert package["state"] == "active"       # CKAN state is NOT the lifecycle
        assert result.written == [fixture["expected_slug"]]


class TestGh06NoLicense:
    """`license: null` means no licence stated. It never means open."""

    def test_no_licence_is_notspecified_and_nothing_is_inferred(self) -> None:
        fixture = fixture_by_id("gh-06-no-license")
        payload = raw_payload(fixture)
        assert payload["repository"]["license"] is None
        assert fixture["source"]["license_id"] == "notspecified"
        assert "license_raw" not in fixture["source"]

    def test_the_record_omits_license_mapped_because_nothing_went_wrong(
        self, events_dir: Path, records_dir: Path
    ) -> None:
        fixture = fixture_by_id("gh-06-no-license")
        adapter = GitHubAdapter(events_directory=events_dir)
        run_adapter(_StaticAdapter(adapter, [observation(fixture)]),
                    limit=5, events_dir=events_dir)
        outcome = materialize_all(events_dir, records_dir, validate=False)
        package = json.loads(
            (records_dir / f"{fixture['expected_slug']}.json").read_text(encoding="utf-8")
        )
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert package["license_id"] == "notspecified"
        assert "license_mapped" not in extras
        assert "license_raw" not in extras
        assert outcome.unmapped_licenses == []

    def test_a_licence_github_could_not_name_is_flagged_not_guessed(self) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        payload = copy.deepcopy(raw_payload(fixture))
        payload["repository"]["license"] = {"spdx_id": "NOASSERTION"}
        mapped = GitHubAdapter().map(
            RawObservation(source_system="github", source_id="x", source_key="k",
                           payload=payload)
        )
        assert mapped.source.license_id == "notspecified"
        assert mapped.source.license_raw == "NOASSERTION"   # flagged by the gate


class TestGh07Renamed:
    """The redirect is followed and the identity key survives it."""

    def test_the_fixture_maps_the_old_path_onto_the_new_metadata(self) -> None:
        fixture = fixture_by_id("gh-07-renamed")
        assert fixture["identity_key"] == "github|IEA-Task-43/digital_wra_data_standard"
        assert fixture["source"]["url"] == "https://github.com/IEA-Task-43/wra-data-standard"
        assert (
            fixture["source"]["extra"]["github_full_name"]
            == "IEA-Task-43/wra-data-standard"
        )

    def test_the_slug_and_therefore_the_url_do_not_move(self) -> None:
        before = fixture_by_id("gh-01-canonical")
        after = fixture_by_id("gh-07-renamed")
        assert before["expected_slug"] == after["expected_slug"]

    def test_harvest_looks_the_repo_id_up_in_the_event_log(self, events_dir: Path) -> None:
        before, after = fixture_by_id("gh-01-canonical"), fixture_by_id("gh-07-renamed")
        old_path = before["source_id"]

        first = _github_adapter(_fake_github({old_path: raw_payload(before)}), events_dir)
        run_adapter(first, limit=5, events_dir=events_dir)
        assert known_repository_paths(events_dir) == {
            str(after["github_repo_id"]): old_path
        }

        renamed = _github_adapter(
            _fake_github({old_path: raw_payload(after)}), events_dir
        )
        observations = list(renamed.harvest(limit=5))
        assert [obs.source_id for obs in observations] == [old_path]
        assert renamed.map(observations[0]).identity_key == before["identity_key"]

    def test_an_unknown_repo_keeps_its_current_path(self, events_dir: Path) -> None:
        after = fixture_by_id("gh-07-renamed")
        adapter = _github_adapter(
            _fake_github({after["renamed_from"]: raw_payload(after)}), events_dir
        )
        observations = list(adapter.harvest(limit=5))
        assert [obs.source_id for obs in observations] == [after["renamed_to"]]


class TestGh10Empty:
    def test_a_meta_repo_with_no_description_and_no_readme_is_excluded(self) -> None:
        payload = raw_payload(fixture_by_id("gh-10-empty"))
        assert exclusion_reason(payload) == "GitHub meta-repository, not an artifact"

    def test_a_tiny_repo_is_below_the_content_threshold(self) -> None:
        payload = copy.deepcopy(raw_payload(fixture_by_id("gh-01-canonical")))
        payload["repository"]["size"] = 1
        assert content_bytes(payload["repository"]) == 1024
        assert "below the content threshold" in (exclusion_reason(payload) or "")

    def test_a_template_repo_is_excluded(self) -> None:
        payload = copy.deepcopy(raw_payload(fixture_by_id("gh-01-canonical")))
        payload["repository"]["is_template"] = True
        assert exclusion_reason(payload) == "template repository"

    def test_a_repo_with_no_description_and_no_readme_is_excluded(self) -> None:
        payload = copy.deepcopy(raw_payload(fixture_by_id("gh-01-canonical")))
        payload["repository"]["name"] = "something"
        payload["repository"]["description"] = None
        payload["readme"] = None
        assert exclusion_reason(payload) == "no description and no README"


class TestGh08PersonalAccount:
    """Org enumeration alone does not reach it; topic search does."""

    def test_it_lives_in_a_user_account_not_an_org(self) -> None:
        payload = raw_payload(fixture_by_id("gh-08-personal-account"))
        assert payload["repository"]["owner"]["type"] == "User"
        assert "iea-wind" in payload["repository"]["topics"]

    def test_no_task_is_attributed_because_no_org_claims_it(self) -> None:
        fixture = fixture_by_id("gh-08-personal-account")
        assert fixture["source"]["iea_task"] == []
        assert "iea_task" not in fixture["provenance"]

    def test_it_is_still_a_full_record(self) -> None:
        fixture = fixture_by_id("gh-08-personal-account")
        assert fixture["expected_included"] is True
        assert fixture["source"]["license_id"] == "apache"
        assert "iea-wind" in fixture["source"]["keywords"]


class TestGh09Monorepo:
    def test_one_record_per_repository_is_a_documented_limitation(self) -> None:
        import harvest.adapters.github as module

        assert "gh-09" in (module.__doc__ or "")
        assert "One record per repository" in (module.__doc__ or "")

    def test_a_repo_of_three_turbines_is_one_record(self) -> None:
        fixture = fixture_by_id("gh-09-monorepo")
        assert fixture["identity_key"] == "github|IEAWindSystems/IEA-LB-RWT"
        assert "6.3, 6.9, and 7.2 MW" in fixture["source"]["notes"]
        assert fixture["expected_included"] is True


# ---------------------------------------------------------------------------
# The source key (ADR-0026, plan §4.1)
# ---------------------------------------------------------------------------


class TestTheSourceKey:
    def payload(self) -> dict:
        return copy.deepcopy(raw_payload(fixture_by_id("gh-01-canonical")))

    def test_it_is_the_documented_composite(self) -> None:
        payload = self.payload()
        sha = payload["head_ref"]["object"]["sha"]
        tag = payload["latest_release"]["tag_name"]
        digest = payload_hash(
            {
                "description": payload["repository"]["description"],
                "topics": sorted(payload["repository"]["topics"]),
                "license": payload["repository"]["license"]["spdx_id"],
            }
        )
        assert source_key_for(payload) == f"{sha}:{tag}:{digest}"

    def test_it_is_stable_across_runs(self) -> None:
        assert source_key_for(self.payload()) == source_key_for(self.payload())

    @pytest.mark.parametrize(
        "field, value",
        [
            ("pushed_at", "2030-01-01T00:00:00Z"),
            ("updated_at", "2030-01-01T00:00:00Z"),
            ("stargazers_count", 99999),
            ("forks_count", 4242),
            ("open_issues_count", 7),
            ("subscribers_count", 11),
        ],
    )
    def test_churn_does_not_move_it(self, field: str, value: Any) -> None:
        """Including any of these turns append-on-change into append-always."""
        payload = self.payload()
        before = source_key_for(payload)
        payload["repository"][field] = value
        assert source_key_for(payload) == before

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda p: p["repository"].update(description="new"), id="description"),
            pytest.param(lambda p: p["repository"].update(topics=["lidar"]), id="topics"),
            pytest.param(lambda p: p["repository"].update(license={"spdx_id": "MIT"}),
                         id="licence"),
            pytest.param(lambda p: p["head_ref"]["object"].update(sha="0" * 40), id="head-sha"),
            pytest.param(lambda p: p["latest_release"].update(tag_name="v9.9.9"), id="tag"),
        ],
    )
    def test_real_change_moves_it(self, mutate) -> None:
        payload = self.payload()
        before = source_key_for(payload)
        mutate(payload)
        assert source_key_for(payload) != before

    def test_it_survives_a_repo_with_no_release_and_no_ref(self) -> None:
        payload = self.payload()
        payload["head_ref"] = None
        payload["latest_release"] = None
        assert source_key_for(payload).startswith("::")

    def test_the_separator_cannot_occur_in_a_git_tag(self) -> None:
        assert ":" not in fixture_by_id("gh-01-canonical")["source_key"].split(":")[1]


# ---------------------------------------------------------------------------
# harvest(): discovery, the cap, and clean degradation
# ---------------------------------------------------------------------------


class _FakeGitHub:
    """A stand-in for ``HarvestClient`` speaking just enough of the GitHub API."""

    def __init__(self, routes: dict[str, tuple[int, Any, dict]]):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FetchResult:
        self.calls.append(url)
        status, body, headers = self.routes.get(url, (404, None, {}))
        return FetchResult(
            url=url,
            status_code=status,
            changed=True,
            text=json.dumps(body) if body is not None else "",
            headers=headers,
        )

    def close(self) -> None:
        pass


def _fake_github(
    repos: dict[str, dict],
    org: str = "IEA-Task-43",
    listing_status: int = 200,
) -> _FakeGitHub:
    """Routes for one org whose listing offers ``repos`` keyed by path."""
    api = "https://api.github.com"
    listing = [{"full_name": path} for path in repos]
    routes: dict[str, tuple[int, Any, dict]] = {
        f"{api}/orgs/{org}/repos?per_page=100&type=public&sort=full_name&direction=asc": (
            listing_status, listing, {}
        )
    }
    for path, envelope in repos.items():
        repository = envelope["repository"]
        full = repository["full_name"]
        branch = repository["default_branch"]
        routes[f"{api}/repos/{path}"] = (200, repository, {})
        routes[f"{api}/repos/{full}/git/ref/heads/{branch}"] = (
            (200, envelope["head_ref"], {}) if envelope.get("head_ref") else (404, None, {})
        )
        routes[f"{api}/repos/{full}/releases/latest"] = (
            (200, envelope["latest_release"], {})
            if envelope.get("latest_release")
            else (404, None, {})
        )
        routes[f"{api}/repos/{full}/readme"] = (
            (200, envelope["readme"], {}) if envelope.get("readme") else (404, None, {})
        )
    return _FakeGitHub(routes)


def _github_adapter(
    client: _FakeGitHub,
    events_directory: Path,
    org: str = "IEA-Task-43",
    tasks: list[str] | None = None,
    **options: Any,
) -> GitHubAdapter:
    source_config = SourceConfig.from_mapping(
        "github",
        {
            "enabled": True,
            "tier": 1,
            "max_records": 5,
            "api": "https://api.github.com",
            "orgs": [{"login": org, "iea_task": tasks or ["task-43"]}],
            "exclude_forks": True,
            "min_content_bytes": 2048,
            **options,
        },
    )
    return GitHubAdapter(
        config=source_config,
        client=client,
        doi_client=_NoDois(),
        events_directory=events_directory,
    )


class _NoDois:
    """A DOI resolver that knows nothing, so no badge ever resolves."""

    def get(self, url: str, **kwargs: Any):
        class _R:
            status_code = 404

            @staticmethod
            def json() -> dict:
                return {}

        return _R()


class _StaticAdapter(GitHubAdapter):
    """The real ``map()``, a canned ``harvest()``. For materialisation tests."""

    def __init__(self, inner: GitHubAdapter, observations: list[RawObservation]):
        super().__init__(config=inner.config, events_directory=inner._events_directory)
        self._observations = observations

    def harvest(self, limit: int = 5):
        return self._observations[:limit]


class TestHarvest:
    def test_forks_and_empty_repos_never_become_observations(
        self, events_dir: Path
    ) -> None:
        keep = fixture_by_id("gh-01-canonical")
        client = _fake_github(
            {
                fixture_by_id("gh-04-fork")["source_id"]: raw_payload(
                    fixture_by_id("gh-04-fork")
                ),
                keep["source_id"]: raw_payload(keep),
                fixture_by_id("gh-10-empty")["source_id"]: raw_payload(
                    fixture_by_id("gh-10-empty")
                ),
            }
        )
        adapter = _github_adapter(client, events_dir)
        assert [obs.source_id for obs in adapter.harvest(limit=5)] == [keep["source_id"]]

    def test_the_five_record_cap_is_honoured(self, events_dir: Path) -> None:
        base = raw_payload(fixture_by_id("gh-01-canonical"))
        repos = {}
        for index in range(9):
            envelope = copy.deepcopy(base)
            path = f"IEA-Task-43/repo-{index}"
            envelope["repository"]["id"] = 1000 + index
            envelope["repository"]["full_name"] = path
            envelope["repository"]["name"] = f"repo-{index}"
            repos[path] = envelope
        adapter = _github_adapter(_fake_github(repos), events_dir)
        assert len(list(adapter.harvest(limit=5))) == 5

    def test_the_iea_task_comes_from_the_org_configuration(self, events_dir: Path) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        adapter = _github_adapter(
            _fake_github({fixture["source_id"]: raw_payload(fixture)}),
            events_dir,
            tasks=["task-43", "task-52"],
        )
        observations = list(adapter.harvest(limit=5))
        assert adapter.map(observations[0]).source.iea_task == ["task-43", "task-52"]

    def test_a_missing_org_is_a_warning_not_a_failure(self, events_dir: Path) -> None:
        adapter = _github_adapter(_fake_github({}, listing_status=404), events_dir)
        assert list(adapter.harvest(limit=5)) == []

    def test_topic_search_is_only_reached_when_orgs_do_not_fill_the_cap(
        self, events_dir: Path
    ) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        client = _fake_github({fixture["source_id"]: raw_payload(fixture)})
        found = fixture_by_id("gh-06-no-license")
        client.routes[
            "https://api.github.com/search/repositories?q=topic:iea-wind"
            "&per_page=25&sort=updated&order=desc"
        ] = (200, {"items": [{"full_name": found["source_id"]}]}, {})
        payload = raw_payload(found)
        api = "https://api.github.com"
        repository = payload["repository"]
        full = repository["full_name"]
        client.routes[f"{api}/repos/{full}"] = (200, repository, {})
        client.routes[
            f"{api}/repos/{full}/git/ref/heads/{repository['default_branch']}"
        ] = (200, payload["head_ref"], {})
        client.routes[f"{api}/repos/{full}/releases/latest"] = (404, None, {})
        client.routes[f"{api}/repos/{full}/readme"] = (200, payload["readme"], {})

        adapter = _github_adapter(client, events_dir, topics=["iea-wind"])
        assert [obs.source_id for obs in adapter.harvest(limit=5)] == [
            fixture["source_id"],
            found["source_id"],
        ]

    def test_a_repo_reachable_two_ways_is_harvested_once(self, events_dir: Path) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        client = _fake_github({fixture["source_id"]: raw_payload(fixture)})
        client.routes[
            "https://api.github.com/search/repositories?q=topic:iea-wind"
            "&per_page=25&sort=updated&order=desc"
        ] = (200, {"items": [{"full_name": fixture["source_id"]}]}, {})
        adapter = _github_adapter(client, events_dir, topics=["iea-wind"])
        assert len(list(adapter.harvest(limit=5))) == 1

    def test_metadata_only_no_archive_is_ever_downloaded(self, events_dir: Path) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        adapter = _github_adapter(
            _fake_github({fixture["source_id"]: raw_payload(fixture)}), events_dir
        )
        list(adapter.harvest(limit=5))
        for url in adapter.client.calls:
            assert "zipball" not in url and "tarball" not in url
            assert url.startswith("https://api.github.com/")


class TestDegradation:
    """A rate limit disables the source for the run; it never crashes it."""

    def rate_limited(self, status: int = 403) -> _FakeGitHub:
        url = (
            "https://api.github.com/orgs/IEA-Task-43/repos"
            "?per_page=100&type=public&sort=full_name&direction=asc"
        )
        return _FakeGitHub(
            {
                url: (
                    status,
                    {"message": "API rate limit exceeded"},
                    {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1788211505"},
                )
            }
        )

    def test_a_rate_limit_403_raises_source_unreachable(self, events_dir: Path) -> None:
        adapter = _github_adapter(self.rate_limited(), events_dir)
        with pytest.raises(SourceUnreachable, match="rate limit exhausted"):
            list(adapter.harvest(limit=5))

    def test_a_429_is_treated_the_same_way(self, events_dir: Path) -> None:
        adapter = _github_adapter(self.rate_limited(status=429), events_dir)
        with pytest.raises(SourceUnreachable, match="rate limit"):
            list(adapter.harvest(limit=5))

    def test_the_run_reports_it_and_does_not_raise(self, events_dir: Path) -> None:
        adapter = _github_adapter(self.rate_limited(), events_dir)
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.reachable is False
        assert result.seen == 0
        assert "rate limit exhausted" in result.errors[0]
        assert "GITHUB_TOKEN" in result.errors[0]

    def test_a_plain_403_is_still_unreachable(self, events_dir: Path) -> None:
        url = (
            "https://api.github.com/orgs/IEA-Task-43/repos"
            "?per_page=100&type=public&sort=full_name&direction=asc"
        )
        adapter = _github_adapter(
            _FakeGitHub({url: (403, {"message": "forbidden"}, {"x-ratelimit-remaining": "42"})}),
            events_dir,
        )
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.reachable is False

    def test_a_transport_error_degrades_cleanly(self, events_dir: Path) -> None:
        class Broken(_FakeGitHub):
            def get(self, url: str, **kwargs: Any) -> FetchResult:
                return FetchResult(url=url, status_code=None, changed=False,
                                   error="connection reset by peer")

        adapter = _github_adapter(Broken({}), events_dir)
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.reachable is False
        assert "connection reset" in result.errors[0]

    def test_a_500_from_one_repo_stops_that_source_not_the_process(
        self, events_dir: Path
    ) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        client = _fake_github({fixture["source_id"]: raw_payload(fixture)})
        client.routes[f"https://api.github.com/repos/{fixture['source_id']}"] = (
            500, {"message": "boom"}, {}
        )
        result = run_adapter(_github_adapter(client, events_dir), limit=5,
                             events_dir=events_dir)
        assert result.reachable is False
        assert result.changed == 0


class TestChangeDetection:
    def test_a_second_identical_run_writes_no_event(self, events_dir: Path) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        routes = {fixture["source_id"]: raw_payload(fixture)}

        first = run_adapter(_github_adapter(_fake_github(routes), events_dir),
                            limit=5, events_dir=events_dir)
        second = run_adapter(_github_adapter(_fake_github(routes), events_dir),
                             limit=5, events_dir=events_dir)

        assert (first.seen, first.changed, first.skipped_unchanged) == (1, 1, 0)
        assert (second.seen, second.changed, second.skipped_unchanged) == (1, 0, 1)
        assert len(read_events(fixture["identity_key"], events_dir)) == 1

    def test_a_new_release_appends_one_event(self, events_dir: Path) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        run_adapter(
            _github_adapter(_fake_github({fixture["source_id"]: raw_payload(fixture)}),
                            events_dir),
            limit=5, events_dir=events_dir,
        )
        moved = copy.deepcopy(raw_payload(fixture))
        moved["latest_release"]["tag_name"] = "v2.0.0"
        run_adapter(
            _github_adapter(_fake_github({fixture["source_id"]: moved}), events_dir),
            limit=5, events_dir=events_dir,
        )
        assert len(read_events(fixture["identity_key"], events_dir)) == 2

    def test_a_push_that_changes_nothing_we_record_still_moves_the_sha(
        self, events_dir: Path
    ) -> None:
        """Honest about the cost: a commit re-scrapes. It never clobbers a local
        annotation, because local.* is additive (ADR-0038)."""
        fixture = fixture_by_id("gh-01-canonical")
        run_adapter(
            _github_adapter(_fake_github({fixture["source_id"]: raw_payload(fixture)}),
                            events_dir),
            limit=5, events_dir=events_dir,
        )
        pushed = copy.deepcopy(raw_payload(fixture))
        pushed["head_ref"]["object"]["sha"] = "a" * 40
        result = run_adapter(
            _github_adapter(_fake_github({fixture["source_id"]: pushed}), events_dir),
            limit=5, events_dir=events_dir,
        )
        assert result.changed == 1


class TestTheRecordItPromotes:
    def test_the_canonical_record_passes_the_ckan_gate(
        self, repo: Path, events_dir: Path, records_dir: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        fixture = fixture_by_id("gh-01-canonical")
        run_adapter(
            _github_adapter(_fake_github({fixture["source_id"]: raw_payload(fixture)}),
                            events_dir),
            limit=5, events_dir=events_dir,
        )
        outcome = materialize_all(events_dir, records_dir, root=repo)
        assert outcome.violations == []
        package = json.loads(
            (records_dir / f"{fixture['expected_slug']}.json").read_text(encoding="utf-8")
        )
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert package["groups"] == [{"name": "task-43"}]
        assert extras["resource_kind"] == "software"
        assert extras["source_system"] == "github"
        assert extras["source_key"] == fixture["source_key"]
        assert all(isinstance(extra["value"], str) for extra in package["extras"])

    def test_materialisation_is_byte_stable(
        self, repo: Path, events_dir: Path, records_dir: Path
    ) -> None:
        fixture = fixture_by_id("gh-01-canonical")
        run_adapter(
            _github_adapter(_fake_github({fixture["source_id"]: raw_payload(fixture)}),
                            events_dir),
            limit=5, events_dir=events_dir,
        )
        materialize_all(events_dir, records_dir, root=repo, validate=False)
        again = materialize_all(events_dir, records_dir, root=repo, validate=False)
        assert again.written == []


class TestConfiguration:
    def test_the_configured_orgs_are_the_ones_that_exist(self) -> None:
        """`IEAWindTask43` does not exist; Task 43's org is `IEA-Task-43`."""
        logins = {
            org["login"] for org in config.load_sources()["github"]["orgs"]
        }
        assert logins == {
            "IEAWindSystems",
            "IEA-Task-43",
            "IEAWindTask37",
            "IEAWindTask52",
            "IEA-Wind-Task-32",
        }

    def test_the_source_key_string_matches_the_adapter(self) -> None:
        declared = config.load_sources()["github"]["source_key"]
        assert declared == GitHubAdapter.source_key_semantics

    def test_the_cap_is_still_five(self) -> None:
        assert config.load_sources()["github"]["max_records"] == 5

    def test_the_token_env_var_is_used_when_present(self, monkeypatch) -> None:
        adapter = GitHubAdapter(config=SourceConfig.from_mapping("github", {}))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert "Authorization" not in adapter._headers()
        monkeypatch.setenv("GITHUB_TOKEN", "ghs_example")
        assert adapter._headers()["Authorization"] == "Bearer ghs_example"

    def test_the_api_version_header_is_pinned(self) -> None:
        headers = GitHubAdapter(config=SourceConfig.from_mapping("github", {}))._headers()
        assert headers["X-GitHub-Api-Version"] == "2022-11-28"
        assert headers["Accept"] == "application/vnd.github+json"


class TestReadmeDecoding:
    def test_base64_is_decoded(self) -> None:
        payload = {"readme": {"content": base64.b64encode(b"# hello").decode(),
                              "encoding": "base64"}}
        assert readme_text(payload) == "# hello"

    def test_a_missing_readme_is_empty_not_an_error(self) -> None:
        assert readme_text({"readme": None}) == ""
        assert readme_text({}) == ""

    def test_undecodable_content_costs_the_badge_not_the_record(self) -> None:
        assert readme_text({"readme": {"content": "!!!not base64!!!"}}) == ""
