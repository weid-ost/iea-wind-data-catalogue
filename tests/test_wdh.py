"""The Wind Data Hub adapter — fixtures ``wdh-01`` .. ``wdh-07``.

The **primary** path is ``wdh-07``: the listing endpoint is walled, so the
adapter disables itself, the run report gains one honest line, and every other
source finishes. Spike 4's probes are captured verbatim in
``fixtures/wdh/raw/wdh-07-auth-wall.json``.

The mapping tests run against real dataset payloads captured from the site, so
``map()`` is verified against WDH's actual shape even though ``harvest()``
cannot reach it. What they defend, in order of how badly each would embarrass
the catalogue:

* ``wdh-06`` — "restricted public" is stated as ``registration-required``.
  Never as ``open``. Promising a download that needs an account is the fastest
  way to lose a user's trust.
* ``wdh-03`` — a dataset with 1.75 million files is **one** resource. Files are
  never enumerated and never mirrored.
* ``wdh-04`` — an open-ended collection has a null end date, not today's.
* ``wdh-05`` — legacy ``a2e.energy.gov`` URLs are canonicalised by string
  rewrite, because ``map()`` is pure and cannot follow a redirect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest import config
from harvest.adapters.base import SourceConfig, SourceUnreachable, run_adapter
from harvest.adapters.wdh import (
    AUTH_WALL_REASON,
    BASE_URL,
    LEGACY_HOSTS,
    WindDataHubAdapter,
    access_status_for,
    canonicalise_wdh_url,
    landing_url,
)
from harvest.models import RawObservation

FIXTURES = config.fixtures_dir() / "wdh"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def raw_payload(fixture: dict) -> dict:
    return json.loads((FIXTURES / fixture["raw"]).read_text(encoding="utf-8"))


def namespace_fixtures() -> list[dict]:
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture["fixture_kind"] == "source_namespace":
            out.append(fixture)
    return out


def observation_for(fixture: dict) -> RawObservation:
    hit = raw_payload(fixture)
    identifier = fixture["source_id"]
    return RawObservation(
        source_system="wdh",
        source_id=identifier,
        source_key=fixture["source_key"],
        url=fixture.get("observed_url") or landing_url(identifier),
        payload=hit,
    )


def adapter_for(**options) -> WindDataHubAdapter:
    return WindDataHubAdapter(
        config=SourceConfig.from_mapping("wdh", {"tier": 2, "max_records": 5, **options})
    )


# ---------------------------------------------------------------------------
# wdh-07 — the primary path
# ---------------------------------------------------------------------------


class TestWdh07TheAuthWall:
    fixture = load("wdh-07-auth-wall")

    def test_no_token_disables_the_source_cleanly(self) -> None:
        with pytest.raises(SourceUnreachable) as caught:
            list(adapter_for().harvest(limit=5))
        for phrase in self.fixture["expected_reason_mentions"]:
            assert phrase in str(caught.value)

    def test_the_reason_names_the_variable_that_would_lift_the_wall(self) -> None:
        assert self.fixture["token_env"] == "WDH_API_TOKEN"
        assert "$WDH_API_TOKEN" in AUTH_WALL_REASON
        assert "existing records are untouched" in AUTH_WALL_REASON

    def test_run_adapter_turns_it_into_one_report_line(self, events_dir: Path) -> None:
        result = run_adapter(adapter_for(), limit=5, events_dir=events_dir)
        assert result.as_dict() == {
            **self.fixture["expected_source_result"],
            "errors": [AUTH_WALL_REASON],
        }
        assert list(events_dir.glob("*.jsonl")) == [], "a disabled source appends no events"

    def test_existing_records_are_untouched(self, repo: Path, events_dir: Path) -> None:
        record = repo / "records" / "doi-10-21947-1406992.json"
        record.write_text('{"name": "doi-10-21947-1406992"}\n', encoding="utf-8")
        before = record.read_bytes()

        run_adapter(adapter_for(), limit=5, events_dir=events_dir)

        assert record.read_bytes() == before

    def test_the_probe_transcript_is_the_evidence(self) -> None:
        probes = {probe["id"]: probe for probe in raw_payload(self.fixture)["probes"]}
        assert probes["api-info"]["response"]["status"] == 200
        assert probes["gateway-datasets"]["response"]["status"] == 403
        assert probes["gateway-datasets"]["response"]["body"]["message"] == (
            "Missing Authentication Token"
        )
        assert probes["search-get"]["response"]["status"] == 404
        assert probes["search-post"]["response"]["status"] == 419
        assert "Disallow: /static/" in probes["robots"]["response"]["body"], (
            "robots permits us; authentication is the obstacle, not etiquette"
        )

    def test_a_token_switches_to_the_gateway_and_still_degrades(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With a credential the adapter tries — and a failure is still not a crash."""
        monkeypatch.setenv("WDH_API_TOKEN", "pretend-token")

        class Refuser:
            def get(self, url, **kwargs):
                from harvest.http import FetchResult

                self.url = url
                self.headers = kwargs.get("headers")
                return FetchResult(url=url, status_code=403, changed=True, text="")

            def close(self) -> None:
                pass

        client = Refuser()
        adapter = WindDataHubAdapter(
            config=SourceConfig.from_mapping("wdh", {"api": f"{BASE_URL}/api"}), client=client
        )
        with pytest.raises(SourceUnreachable, match="403"):
            list(adapter.harvest(limit=5))
        assert client.headers["Authorization"] == "Bearer pretend-token"

    def test_an_unexpected_listing_shape_degrades_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("WDH_API_TOKEN", "pretend-token")

        class Surprising:
            def get(self, url, **kwargs):
                from harvest.http import FetchResult

                return FetchResult(url=url, status_code=200, changed=True, text='{"data": []}')

            def close(self) -> None:
                pass

        adapter = WindDataHubAdapter(config=SourceConfig.from_mapping("wdh", {}), client=Surprising())
        with pytest.raises(SourceUnreachable, match="unexpected listing shape"):
            list(adapter.harvest(limit=5))


# ---------------------------------------------------------------------------
# The mapping fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", namespace_fixtures(), ids=lambda f: f["fixture_id"])
class TestEveryMappingFixture:
    def test_map_reproduces_the_expected_source_namespace(self, fixture: dict) -> None:
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        assert mapped.identity_key == fixture["identity_key"]
        assert mapped.source_id == fixture["source_id"]
        assert mapped.source_key == fixture["source_key"]
        assert mapped.source.model_dump(exclude_none=True) == fixture["source"]
        assert {
            key: value.model_dump(exclude_none=True) for key, value in mapped.provenance.items()
        } == fixture["provenance"]

    def test_the_slug_is_the_one_the_fixture_declares(self, fixture: dict) -> None:
        from harvest.identity import slug_for_identity

        assert slug_for_identity(fixture["identity_key"]) == fixture["expected_slug"]

    def test_the_source_key_comes_from_the_payload(self, fixture: dict) -> None:
        hit = raw_payload(fixture)
        assert WindDataHubAdapter.source_key_for(hit) == fixture["source_key"]

    def test_files_are_never_enumerated(self, fixture: dict) -> None:
        """wdh-03, applied to every dataset: one resource is the dataset page."""
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        dataset_resources = [
            resource for resource in mapped.source.resources
            if resource["url"].startswith(f"{BASE_URL}/ds/")
        ]
        assert len(dataset_resources) == 1
        assert mapped.source.extra["wdh_files_enumerated"] is False
        summary = (raw_payload(fixture).get("_source") or {}).get("dapFileSummary") or {}
        if summary.get("count"):
            assert mapped.source.extra["wdh_file_count"] == summary["count"]
            assert isinstance(mapped.source.extra["wdh_file_count"], int)

    def test_every_url_is_canonical(self, fixture: dict) -> None:
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        urls = [mapped.source.url, *mapped.source.source_urls] + [
            resource["url"] for resource in mapped.source.resources
        ]
        for url in urls:
            assert not any(host in url for host in LEGACY_HOSTS), f"legacy host survived in {url}"

    def test_a_licence_is_never_inferred(self, fixture: dict) -> None:
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        assert mapped.source.license_id == "notspecified"
        assert mapped.source.license_raw is None, "WDH states no licence; we invent none"

    def test_it_is_a_dataset(self, fixture: dict) -> None:
        assert WindDataHubAdapter().map(observation_for(fixture)).source.resource_kind == "dataset"

    def test_map_is_pure(self, fixture: dict) -> None:
        raw = observation_for(fixture)
        first = WindDataHubAdapter().map(raw)
        second = WindDataHubAdapter().map(raw)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ---------------------------------------------------------------------------
# The individual semantics
# ---------------------------------------------------------------------------


class TestWdh01Canonical:
    def test_coverage_and_instrument_ride_in_extra(self) -> None:
        fixture = load("wdh-01-canonical")
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        extra = mapped.source.extra
        assert extra["temporal_coverage_start"] == "2014-08-13T20:30:03.000000Z"
        assert extra["temporal_coverage_end"] == "2016-05-31T20:30:03.000000Z"
        assert extra["wdh_data_level"] == "Raw Data (00)"
        assert mapped.source.doi == "10.21947/1406992"
        assert mapped.provenance["doi"].extraction_method == "api"

    def test_the_doi_is_the_identity(self) -> None:
        fixture = load("wdh-01-canonical")
        assert fixture["identity_key"] == "10.21947/1406992"


class TestWdh02NoDoi:
    def test_identity_falls_back_to_the_project_dataset_code(self) -> None:
        fixture = load("wdh-02-no-doi")
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        assert mapped.identity_key == "wdh|awaken/arm.lidar.sgp_s5.ppi.b1"
        assert mapped.source.doi is None
        assert "doi" not in mapped.provenance, "no DOI, no DOI provenance"

    def test_the_identity_kind_is_source_not_fragile(self) -> None:
        from harvest.identity import identity_kind

        assert identity_kind(load("wdh-02-no-doi")["identity_key"]) == "source"


class TestWdh03HugeFileCount:
    fixture = load("wdh-03-huge-file-count")

    def test_one_and_three_quarter_million_files_are_one_resource(self) -> None:
        mapped = WindDataHubAdapter().map(observation_for(self.fixture))
        assert mapped.source.extra["wdh_file_count"] == 1758855
        assert len(mapped.source.resources) == 1
        assert mapped.source.resources[0]["url"] == f"{BASE_URL}/ds/wfip3/nant.ld.z01.00"

    def test_no_file_name_appears_anywhere_in_the_record(self) -> None:
        mapped = WindDataHubAdapter().map(observation_for(self.fixture))
        dumped = json.dumps(mapped.source.model_dump(mode="json"))
        assert "dapFileSummary" not in dumped
        assert dumped.count("nant.ld.z01.00") <= 6, "identifiers, not a file listing"

    def test_the_count_is_a_number_not_a_list(self) -> None:
        mapped = WindDataHubAdapter().map(observation_for(self.fixture))
        assert isinstance(mapped.source.extra["wdh_file_count"], int)
        assert isinstance(mapped.source.extra["wdh_total_bytes"], int)


class TestWdh04OpenEndedCoverage:
    def test_an_absent_end_stays_null(self) -> None:
        fixture = load("wdh-04-open-ended-coverage")
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        assert mapped.source.extra["temporal_coverage_start"]
        assert mapped.source.extra["temporal_coverage_end"] is None, (
            "never the ingest date, never 'present'"
        )

    def test_the_invented_payload_says_it_is_invented(self) -> None:
        fixture = load("wdh-04-open-ended-coverage")
        assert "INVENTED" in fixture["case"].upper()
        assert "INVENTED" in json.dumps(raw_payload(fixture)).upper()


class TestWdh05LegacyUrls:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("https://a2e.energy.gov/ds/buoy/buoy.z01.00",
             "https://wdh.energy.gov/ds/buoy/buoy.z01.00"),
            ("http://a2e.energy.gov/ds/buoy/buoy.z01.00",
             "https://wdh.energy.gov/ds/buoy/buoy.z01.00"),
            ("http://a2e.energy.gov:80/ds/buoy/buoy.z01.00",
             "https://wdh.energy.gov/ds/buoy/buoy.z01.00"),
            ("https://www.a2e.energy.gov/about",
             "https://wdh.energy.gov/about"),
            ("https://wdh.energy.gov/ds/buoy/buoy.z01.00",
             "https://wdh.energy.gov/ds/buoy/buoy.z01.00"),
            ("https://example.org/elsewhere", "https://example.org/elsewhere"),
        ],
    )
    def test_legacy_hosts_are_rewritten(self, given: str, expected: str) -> None:
        assert canonicalise_wdh_url(given) == expected

    def test_nothing_in_nothing_out(self) -> None:
        assert canonicalise_wdh_url(None) is None
        assert canonicalise_wdh_url("") is None

    def test_a_pre_rename_citation_lands_on_the_current_host(self) -> None:
        fixture = load("wdh-05-legacy-url")
        assert "a2e.energy.gov" in fixture["observed_url"]
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        assert mapped.source.url == "https://wdh.energy.gov/ds/buoy/buoy.z01.00"
        assert all("a2e" not in resource["url"] for resource in mapped.source.resources)


class TestWdh06RegistrationRequired:
    def test_restricted_public_is_stated_plainly(self) -> None:
        fixture = load("wdh-06-registration-required")
        mapped = WindDataHubAdapter().map(observation_for(fixture))
        assert mapped.source.access_status == "registration-required"
        assert mapped.source.extra["wdh_access_level"] == "restricted public"
        assert mapped.source.extra["wdh_access_restriction"] == "project"

    @pytest.mark.parametrize(
        "level,restriction,expected",
        [
            ("public", "none", "open"),
            ("public", "", "open"),
            ("restricted public", "project", "registration-required"),
            ("restricted-public", "none", "registration-required"),
            ("restricted", "none", "restricted"),
            ("private", "none", "restricted"),
            ("public", "project", "registration-required"),
            (None, None, "unknown"),
            ("something new upstream", "", "unknown"),
        ],
    )
    def test_the_access_vocabulary(self, level, restriction, expected: str) -> None:
        assert access_status_for(level, restriction) == expected

    def test_nothing_unknown_is_ever_called_open(self) -> None:
        for level in ("restricted public", "restricted", "private", "internal", None, "mystery"):
            assert access_status_for(level, "none") != "open"


# ---------------------------------------------------------------------------
# The change token
# ---------------------------------------------------------------------------


class TestTheSourceKey:
    def test_last_updated_wins_when_present(self) -> None:
        hit = {"_source": {"identifier": "p/d", "lastUpdated": "2026-03-03T21:02:06.000000Z"}}
        assert WindDataHubAdapter.source_key_for(hit) == "2026-03-03T21:02:06.000000Z"

    def test_the_nightly_reindex_does_not_move_the_key(self) -> None:
        """``_index`` carries a rebuild date; hashing it would write an event a night."""
        base = {"_source": {"identifier": "p/d", "title": "T", "description": "D"}}
        monday = {**base, "_index": "production-datasets_20260831_030003", "_score": 1}
        tuesday = {**base, "_index": "production-datasets_20260901_030007", "_score": 3.7}
        assert WindDataHubAdapter.source_key_for(monday) == (
            WindDataHubAdapter.source_key_for(tuesday)
        )

    def test_a_meaningful_change_does_move_the_key(self) -> None:
        base = {"_source": {"identifier": "p/d", "title": "T", "accessLevel": "public"}}
        changed = {"_source": {**base["_source"], "accessLevel": "restricted public"}}
        assert WindDataHubAdapter.source_key_for(base) != WindDataHubAdapter.source_key_for(changed)

    def test_the_key_is_deterministic(self) -> None:
        hit = {"_source": {"identifier": "p/d", "title": "T"}}
        assert WindDataHubAdapter.source_key_for(hit) == WindDataHubAdapter.source_key_for(hit)


# ---------------------------------------------------------------------------
# The adapter contract
# ---------------------------------------------------------------------------


class TestTheContract:
    def test_it_is_registered_as_a_tier_two_source(self) -> None:
        from harvest.adapters.base import get_adapter

        assert get_adapter("wdh") is WindDataHubAdapter
        assert WindDataHubAdapter.source_name == "wdh"
        assert WindDataHubAdapter.tier == 2

    def test_sources_yaml_declares_the_source_and_forbids_file_enumeration(self) -> None:
        source = config.load_sources()["wdh"]
        assert source["base_url"] == BASE_URL
        assert source["enumerate_files"] is False
        assert "a2e.energy.gov" in source["legacy_hosts"]

    def test_the_landing_url_is_the_dataset_page(self) -> None:
        assert landing_url("buoy/buoy.z01.00") == "https://wdh.energy.gov/ds/buoy/buoy.z01.00"
        assert landing_url("/buoy/buoy.z01.00/") == "https://wdh.energy.gov/ds/buoy/buoy.z01.00"

    def test_close_is_safe_to_call_without_a_client(self) -> None:
        adapter_for().close()
