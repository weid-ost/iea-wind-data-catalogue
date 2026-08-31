"""Track E — the OSTI adapter.

Everything here runs offline. ``map()`` is pure by contract, so the fixture
tests call it directly on the verbatim payloads in ``fixtures/osti/raw/``;
``harvest()`` is exercised against an injected fake client that answers both
the OSTI listing and the DOI resolvers, so nothing in this file touches
www.osti.gov, DataCite or Crossref.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest import config
from harvest.adapters.base import SourceConfig, SourceUnreachable, run_adapter
from harvest.adapters.osti import (
    OSTI_DOI_PREFIXES,
    PRODUCT_TYPES,
    TASK_GROUPS,
    OstiAdapter,
    iea_tasks,
)
from harvest.events import read_events
from harvest.http import FetchResult
from harvest.identity import slug_for_identity
from harvest.models import RawObservation

FIXTURES = config.fixtures_dir() / "osti"


# ---------------------------------------------------------------------------
# Fixture plumbing
# ---------------------------------------------------------------------------


def load_fixtures() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.json"))
    ]


def raw_for(fixture: dict) -> RawObservation:
    payload = json.loads((FIXTURES / fixture["raw"]).read_text(encoding="utf-8"))
    return RawObservation(
        source_system="osti",
        source_id=fixture["source_id"],
        source_key=fixture["source_key"],
        payload=payload,
        fetched_at="2026-08-31T00:00:00Z",
    )


def payload_of(fixture_id: str) -> dict:
    return json.loads(
        (FIXTURES / "raw" / f"{fixture_id}.json").read_text(encoding="utf-8")
    )


ALL_FIXTURES = load_fixtures()


# ---------------------------------------------------------------------------
# A fake client: the OSTI listing plus the two DOI resolvers
# ---------------------------------------------------------------------------


class FakeOstiClient:
    """Answers ``HarvestClient.get`` for the listing and the DOI resolvers.

    ``resolvable`` is the set of DOIs DataCite will admit to knowing; anything
    else 404s at both agencies and must therefore be dropped.
    """

    def __init__(
        self,
        pages: dict[str, list[dict]] | None = None,
        records: list[dict] | None = None,
        resolvable: set[str] | None = None,
        listing_error: str | None = None,
        listing_status: int = 200,
        listing_body: str | None = None,
    ) -> None:
        self.pages = pages if pages is not None else {}
        self.records = records or []
        self.resolvable = resolvable
        self.listing_error = listing_error
        self.listing_status = listing_status
        self.listing_body = listing_body
        self.calls: list[str] = []
        self.closed = False

    def get(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(url)
        for prefix in ("https://api.datacite.org/dois/", "https://api.crossref.org/works/"):
            if url.startswith(prefix):
                doi = url[len(prefix):]
                known = self.resolvable is None or doi in self.resolvable
                if known and prefix.startswith("https://api.datacite.org"):
                    return FetchResult(url=url, status_code=200, changed=True,
                                       text=json.dumps({"data": {"id": doi}}))
                return FetchResult(url=url, status_code=404, changed=True, text="{}")

        if self.listing_error:
            return FetchResult(url=url, status_code=None, changed=False,
                               error=self.listing_error)
        body = self.listing_body
        if body is None:
            for marker, records in self.pages.items():
                if marker in url:
                    body = json.dumps(records)
                    break
            else:
                body = json.dumps(self.records)
        return FetchResult(url=url, status_code=self.listing_status, changed=True, text=body)

    def close(self) -> None:
        self.closed = True


def adapter_with(client: FakeOstiClient, **options) -> OstiAdapter:  # noqa: ANN003
    mapping = {"enabled": True, "tier": 1, "max_records": 5,
               "api": "https://www.osti.gov/api/v1/records", **options}
    return OstiAdapter(config=SourceConfig.from_mapping("osti", mapping), client=client)


# ---------------------------------------------------------------------------
# The fixtures
# ---------------------------------------------------------------------------


class TestFixtures:
    def test_all_five_catalogue_fixtures_exist(self) -> None:
        assert {fixture["fixture_id"] for fixture in ALL_FIXTURES} == {
            "osti-01-canonical",
            "osti-02-no-doi",
            "osti-03-mandated-duplicate",
            "osti-04-metadata-only",
            "osti-05-report-number",
        }

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f["fixture_id"])
    def test_map_reproduces_the_expectation(self, fixture: dict) -> None:
        mapped = OstiAdapter().map(raw_for(fixture))
        assert mapped.identity_key == fixture["identity_key"]
        assert mapped.source_id == fixture["source_id"]
        assert mapped.source_key == fixture["source_key"]
        assert mapped.source.model_dump(mode="json", exclude_none=True) == fixture["source"]
        assert {
            key: value.model_dump(mode="json", exclude_none=True)
            for key, value in mapped.provenance.items()
        } == fixture["provenance"]

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f["fixture_id"])
    def test_the_slug_follows_from_the_identity(self, fixture: dict) -> None:
        assert slug_for_identity(fixture["identity_key"]) == fixture["expected_slug"]

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f["fixture_id"])
    def test_the_source_key_is_the_payloads_entry_date(self, fixture: dict) -> None:
        payload = json.loads((FIXTURES / fixture["raw"]).read_text(encoding="utf-8"))
        assert fixture["source_key"] == payload["entry_date"]

    @pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f["fixture_id"])
    def test_map_is_pure_and_repeatable(self, fixture: dict) -> None:
        """No clock, no filesystem, no network: two calls, one answer."""
        raw = raw_for(fixture)
        first = OstiAdapter().map(raw)
        second = OstiAdapter().map(raw)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ---------------------------------------------------------------------------
# osti-01 — the canonical report
# ---------------------------------------------------------------------------


class TestOsti01Canonical:
    def test_a_technical_report_is_resource_kind_report(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-01-canonical")
        ))
        assert mapped.source.resource_kind == "report"

    def test_the_identity_is_the_doi(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-01-canonical")
        ))
        assert mapped.identity_key == "10.2172/2447928"
        assert mapped.source.doi == "10.2172/2447928"

    def test_an_osti_minted_doi_is_not_flagged_as_a_mandated_duplicate(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-01-canonical")
        ))
        assert mapped.source.extra["osti_doi_registrant"] == "osti"
        assert "osti_mandated_deposit" not in mapped.source.extra
        assert not [
            related for related in mapped.source.related_identifiers
            if related["relation"] == "IsVariantFormOf"
        ]

    def test_orcids_are_written_in_the_canonical_hyphenated_spelling(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-01-canonical")
        ))
        orcids = [author.orcid for author in mapped.source.authors if author.orcid]
        assert "0000-0002-0398-8320" in orcids
        assert all(len(orcid) == 19 for orcid in orcids)

    def test_affiliations_are_unpacked_from_the_square_brackets(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-01-canonical")
        ))
        author = next(a for a in mapped.source.authors if a.name == "Hall, Matthew")
        assert "[" not in author.name
        assert author.affiliation.startswith("National Renewable Energy Laboratory")


# ---------------------------------------------------------------------------
# osti-02 — identity fallback
# ---------------------------------------------------------------------------


class TestOsti02NoDoi:
    def test_the_identity_falls_back_to_source_system_pipe_source_id(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-02-no-doi")
        ))
        assert mapped.identity_key == "osti|2568356"
        assert slug_for_identity(mapped.identity_key) == "osti-2568356"
        assert mapped.source.doi is None

    def test_the_osti_id_is_still_a_related_identifier(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-02-no-doi")
        ))
        assert {"relation": "IsIdenticalTo", "identifier": "2568356",
                "identifier_type": "OSTI"} in mapped.source.related_identifiers

    def test_no_task_is_invented_when_the_payload_names_none(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-02-no-doi")
        ))
        assert mapped.source.iea_task == []
        assert "iea_task" not in mapped.provenance


# ---------------------------------------------------------------------------
# osti-03 — the mandated duplicate
# ---------------------------------------------------------------------------


class TestOsti03MandatedDuplicate:
    @property
    def mapped(self):  # noqa: ANN201
        return OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-03-mandated-duplicate")
        ))

    def test_the_identity_is_the_publishers_doi_so_the_events_merge(self) -> None:
        """Not a second record: the same slug Crossref's adapter will write to."""
        assert self.mapped.identity_key == "10.5194/wes-11-1429-2026"
        assert slug_for_identity(self.mapped.identity_key) == "doi-10-5194-wes-11-1429-2026"

    def test_the_deposit_is_flagged_for_the_dedup_track(self) -> None:
        extra = self.mapped.source.extra
        assert extra["osti_doi_registrant"] == "external"
        assert extra["osti_mandated_deposit"] is True
        assert self.mapped.source.doi.split("/", 1)[0] not in OSTI_DOI_PREFIXES

    def test_osti_contributes_a_source_url_and_a_variant_form_relation(self) -> None:
        source = self.mapped.source
        assert "https://www.osti.gov/biblio/3363065" in source.source_urls
        assert {
            "relation": "IsVariantFormOf",
            "identifier": "https://www.osti.gov/biblio/3363065",
            "identifier_type": "URL",
        } in source.related_identifiers

    def test_the_journal_reaches_container_for_the_merge(self) -> None:
        assert self.mapped.source.container == "Wind Energy Science (Online)"

    def test_osti_ranks_below_crossref_so_crossref_wins_the_scalars(self) -> None:
        sources = config.load_sources()
        assert sources["osti"]["precedence"] > sources["crossref"]["precedence"]


# ---------------------------------------------------------------------------
# osti-04 — honest availability
# ---------------------------------------------------------------------------


class TestOsti04MetadataOnly:
    def test_no_fulltext_link_means_metadata_only_and_no_resources(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-04-metadata-only")
        ))
        assert mapped.source.access_status == "metadata-only"
        assert mapped.source.resources == []

    def test_the_raw_payload_really_has_no_fulltext_link(self) -> None:
        payload = payload_of("osti-04-metadata-only")
        assert "fulltext" not in {link["rel"] for link in payload["links"]}

    def test_a_fulltext_link_gives_open_access_and_one_linked_resource(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-01-canonical")
        ))
        assert mapped.source.access_status == "open"
        assert mapped.source.resources == [
            {"url": "https://www.osti.gov/servlets/purl/2447928",
             "name": "Full text at OSTI"}
        ]

    def test_the_catalogue_links_and_never_mirrors(self) -> None:
        for fixture in ALL_FIXTURES:
            mapped = OstiAdapter().map(raw_for(fixture))
            for resource in mapped.source.resources:
                assert resource["url"].startswith("https://www.osti.gov/")


# ---------------------------------------------------------------------------
# osti-05 — the report number
# ---------------------------------------------------------------------------


class TestOsti05ReportNumber:
    def test_it_is_a_first_class_field_on_the_source_namespace(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-05-report-number")
        ))
        assert mapped.source.report_number == "NREL/PR-5C00-80872"
        assert mapped.provenance["report_number"].extraction_method == "api"

    def test_it_is_also_a_related_identifier(self) -> None:
        mapped = OstiAdapter().map(raw_for(
            next(f for f in ALL_FIXTURES if f["fixture_id"] == "osti-05-report-number")
        ))
        assert {"relation": "IsIdenticalTo", "identifier": "NREL/PR-5C00-80872",
                "identifier_type": "Report-Number"} in mapped.source.related_identifiers

    def test_it_is_declared_as_a_ckan_extra_in_both_places(self) -> None:
        from harvest.materialize import EXTRA_KEYS

        schema = json.loads(config.scheming_path().read_text(encoding="utf-8"))
        declared = {field["field_name"] for field in schema["dataset_fields"]}
        assert "report_number" in EXTRA_KEYS
        assert "report_number" in declared

    def test_it_survives_into_the_materialised_record(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from harvest.materialize import materialize_all

        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        payload = payload_of("osti-05-report-number")
        client = FakeOstiClient(records=[payload])
        result = run_adapter(adapter_with(client), limit=5, events_dir=repo / "events")
        assert result.changed == 1

        materialized = materialize_all(root=repo)
        assert materialized.violations == []
        record = json.loads((repo / "records" / "osti-1891471.json").read_text("utf-8"))
        extras = {extra["key"]: extra["value"] for extra in record["extras"]}
        assert extras["report_number"] == "NREL/PR-5C00-80872"
        assert record["groups"] == [{"name": "task-25"}]


# ---------------------------------------------------------------------------
# Task attribution
# ---------------------------------------------------------------------------


class TestTaskAttribution:
    def test_every_task_group_exists_in_groups_yaml(self) -> None:
        """The literal map and groups.yaml are one contract stated twice."""
        known = config.group_names()
        missing = {group for group in TASK_GROUPS.values() if group not in known}
        assert not missing, f"TASK_GROUPS points at groups that do not exist: {sorted(missing)}"

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("IEA Wind TCP Task 49: Reference Site Conditions", ["task-49"]),
            ("run under the International Energy Agency (IEA) Wind Task 30", ["task-30"]),
            ("activities in the IEA Wind TCT Task 25", ["task-25"]),
            ("IEA-Wind Task 43 digitalisation", ["task-43"]),
            ("IEA Wind Task 25/63 reporting", ["task-25"]),
            ("the former IEA Wind Task 36 Forecasting", ["task-36"]),
            ("IEA Wind Technology Collaboration Programme Task 11", ["task-11"]),
        ],
    )
    def test_the_task_pattern_reads_what_the_source_wrote(
        self, text: str, expected: list[str]
    ) -> None:
        assert iea_tasks(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "the IEA-Wind 15MW Reference Wind Turbine",
            "the IEA Wind 15 MW Reference Wind Turbine",
            "a new IEA Wind Task with a much broader scope",
            "Task 49 without the programme name",
            "17 WIND ENERGY",
        ],
    )
    def test_a_reference_turbine_is_never_read_as_a_task(self, text: str) -> None:
        assert iea_tasks(text) == []

    def test_renumbered_tasks_resolve_to_the_current_group(self) -> None:
        assert iea_tasks("IEA Wind Task 19 cold climates") == ["task-54"]
        assert iea_tasks("IEA Wind Task 34 WREN") == ["task-59"]
        assert iea_tasks("IEA Wind Task 63") == ["task-25"]

    def test_an_unknown_task_number_is_ignored_not_guessed(self) -> None:
        """groups[].name must exist in groups.yaml or the CKAN gate fails."""
        assert iea_tasks("IEA Wind Task 99") == []

    def test_first_mention_order_is_preserved_and_deduplicated(self) -> None:
        assert iea_tasks("IEA Wind Task 43 and IEA Wind Task 25 and IEA Wind Task 43") == [
            "task-43", "task-25"
        ]


# ---------------------------------------------------------------------------
# The source key (ADR-0026)
# ---------------------------------------------------------------------------


class TestSourceKey:
    def test_it_is_the_entry_date_when_osti_provides_one(self) -> None:
        payload = payload_of("osti-01-canonical")
        assert OstiAdapter().source_key(payload) == payload["entry_date"]

    def test_it_falls_back_to_a_hash_when_entry_date_is_missing(self) -> None:
        payload = {k: v for k, v in payload_of("osti-01-canonical").items()
                   if k != "entry_date"}
        key = OstiAdapter().source_key(payload)
        assert key != "" and len(key) == 16

    def test_the_fallback_hash_ignores_fields_that_churn(self) -> None:
        base = {k: v for k, v in payload_of("osti-01-canonical").items()
                if k != "entry_date"}
        noisy = {**base, "other_number": "MainId:99999", "country_publication": "Elsewhere"}
        assert OstiAdapter().source_key(base) == OstiAdapter().source_key(noisy)

    def test_the_fallback_hash_moves_when_the_content_moves(self) -> None:
        base = {k: v for k, v in payload_of("osti-01-canonical").items()
                if k != "entry_date"}
        edited = {**base, "title": base["title"] + " (revised)"}
        assert OstiAdapter().source_key(base) != OstiAdapter().source_key(edited)


# ---------------------------------------------------------------------------
# harvest(): the cap, verbatim payloads, resolve-or-drop, degradation
# ---------------------------------------------------------------------------


def synthetic(osti_id: str, doi: str | None = None, entry: str = "2026-01-01T00:00:00Z") -> dict:
    payload = {
        "osti_id": osti_id,
        "title": f"Synthetic OSTI record {osti_id}",
        "entry_date": entry,
        "product_type": "Technical Report",
        "publication_date": "2025-01-01T00:00:00Z",
        "authors": ["Doe, Jane"],
        "subjects": ["17 WIND ENERGY"],
        "links": [{"rel": "citation", "href": f"https://www.osti.gov/biblio/{osti_id}"}],
    }
    if doi:
        payload["doi"] = doi
    return payload


class TestHarvest:
    def test_the_five_record_cap_is_honoured(self) -> None:
        client = FakeOstiClient(records=[synthetic(str(n)) for n in range(20)])
        observations = list(adapter_with(client).harvest(limit=5))
        assert len(observations) == 5

    def test_payloads_are_yielded_verbatim(self) -> None:
        payload = payload_of("osti-01-canonical")
        client = FakeOstiClient(records=[payload], resolvable={"10.2172/2447928"})
        observation = next(iter(adapter_with(client).harvest(limit=5)))
        assert observation.payload == payload
        assert observation.source_system == "osti"
        assert observation.source_id == "2447928"
        assert observation.source_key == payload["entry_date"]
        assert observation.url == "https://www.osti.gov/biblio/2447928"

    def test_one_record_is_not_harvested_twice_across_queries(self) -> None:
        client = FakeOstiClient(
            pages={"first": [synthetic("1"), synthetic("2")],
                   "second": [synthetic("2"), synthetic("3")]},
        )
        adapter = adapter_with(client, queries=["first", "second"])
        ids = [observation.source_id for observation in adapter.harvest(limit=5)]
        assert ids == ["1", "2", "3"]

    def test_a_transport_failure_disables_the_source_cleanly(self, events_dir: Path) -> None:
        """wdh-07's pattern: report it, never crash the run."""
        client = FakeOstiClient(listing_error="connection reset")
        result = run_adapter(adapter_with(client), limit=5, events_dir=events_dir)
        assert result.reachable is False
        assert "connection reset" in result.errors[0]

    def test_an_http_error_raises_source_unreachable(self) -> None:
        client = FakeOstiClient(records=[], listing_status=503)
        with pytest.raises(SourceUnreachable, match="503"):
            list(adapter_with(client).harvest(limit=5))

    def test_a_non_json_body_raises_source_unreachable(self) -> None:
        client = FakeOstiClient(listing_body="<html>maintenance</html>")
        with pytest.raises(SourceUnreachable, match="not JSON"):
            list(adapter_with(client).harvest(limit=5))

    def test_an_unexpected_envelope_shape_yields_nothing_rather_than_crashing(self) -> None:
        client = FakeOstiClient(listing_body=json.dumps({"unexpected": "shape"}))
        assert list(adapter_with(client).harvest(limit=5)) == []

    def test_an_envelope_around_the_array_is_tolerated(self) -> None:
        client = FakeOstiClient(listing_body=json.dumps({"records": [synthetic("7")]}))
        assert [o.source_id for o in adapter_with(client).harvest(limit=5)] == ["7"]


class TestResolveOrDrop:
    def test_a_resolving_doi_is_kept(self) -> None:
        client = FakeOstiClient(records=[synthetic("1", "10.2172/1")],
                                resolvable={"10.2172/1"})
        observations = list(adapter_with(client).harvest(limit=5))
        assert [o.source_id for o in observations] == ["1"]

    def test_a_doi_that_does_not_resolve_drops_the_record_and_logs_it(self) -> None:
        adapter = adapter_with(
            FakeOstiClient(records=[synthetic("1", "10.2172/nope"), synthetic("2")],
                           resolvable=set())
        )
        observations = list(adapter.harvest(limit=5))
        assert [o.source_id for o in observations] == ["2"]
        assert adapter.drop_log.as_notices() == [
            {"doi": "10.2172/nope", "reason": "did-not-resolve", "context": "osti:1"}
        ]

    def test_a_record_without_a_doi_is_never_sent_to_a_resolver(self) -> None:
        client = FakeOstiClient(records=[synthetic("1")])
        list(adapter_with(client).harvest(limit=5))
        assert not [call for call in client.calls if "doi" in call or "works" in call]

    def test_resolution_happens_in_harvest_never_in_map(self) -> None:
        """map() is pure: it must not touch a client even when one is injected."""
        client = FakeOstiClient(records=[])
        adapter = adapter_with(client)
        adapter.map(raw_for(ALL_FIXTURES[0]))
        assert client.calls == []


# ---------------------------------------------------------------------------
# End to end: events, change detection, the CKAN gate
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def _payloads(self) -> list[dict]:
        return [
            payload_of(name)
            for name in (
                "osti-01-canonical",
                "osti-02-no-doi",
                "osti-03-mandated-duplicate",
                "osti-04-metadata-only",
                "osti-05-report-number",
            )
        ]

    def _client(self) -> FakeOstiClient:
        return FakeOstiClient(
            records=self._payloads(),
            resolvable={"10.2172/2447928", "10.5194/wes-11-1429-2026"},
        )

    def test_a_first_run_writes_one_event_per_record(self, repo: Path) -> None:
        result = run_adapter(adapter_with(self._client()), limit=5,
                             events_dir=repo / "events")
        assert (result.seen, result.changed, result.skipped_unchanged) == (5, 5, 0)
        assert len(list((repo / "events").glob("*.jsonl"))) == 5
        assert len(read_events("10.2172/2447928", repo / "events")) == 1

    def test_a_second_run_writes_nothing_at_all(self, repo: Path) -> None:
        """ADR-0026: an unchanged entry_date means no event, not an empty one."""
        events = repo / "events"
        run_adapter(adapter_with(self._client()), limit=5, events_dir=events)
        before = {path: path.read_bytes() for path in sorted(events.glob("*.jsonl"))}
        result = run_adapter(adapter_with(self._client()), limit=5, events_dir=events)
        assert (result.seen, result.changed, result.skipped_unchanged) == (5, 0, 5)
        assert {path: path.read_bytes() for path in sorted(events.glob("*.jsonl"))} == before

    def test_a_moved_entry_date_appends(self, repo: Path) -> None:
        events = repo / "events"
        run_adapter(adapter_with(self._client()), limit=5, events_dir=events)
        moved = self._payloads()
        moved[0] = {**moved[0], "entry_date": "2027-01-01T00:00:00Z"}
        run_adapter(
            adapter_with(FakeOstiClient(
                records=moved,
                resolvable={"10.2172/2447928", "10.5194/wes-11-1429-2026"},
            )),
            limit=5,
            events_dir=events,
        )
        assert len(read_events("10.2172/2447928", events)) == 2

    def test_the_records_pass_the_ckan_gate(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from harvest.materialize import materialize_all

        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        run_adapter(adapter_with(self._client()), limit=5, events_dir=repo / "events")
        result = materialize_all(root=repo)
        assert result.violations == []
        assert len(result.written) == 5

    def test_materialisation_is_byte_stable(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from harvest.materialize import materialize_all

        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        run_adapter(adapter_with(self._client()), limit=5, events_dir=repo / "events")
        materialize_all(root=repo)
        again = materialize_all(root=repo)
        assert again.written == [] and len(again.unchanged) == 5

    def test_a_metadata_only_record_offers_no_download(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from harvest.materialize import materialize_all

        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        run_adapter(adapter_with(self._client()), limit=5, events_dir=repo / "events")
        materialize_all(root=repo)
        record = json.loads((repo / "records" / "osti-2323276.json").read_text("utf-8"))
        extras = {extra["key"]: extra["value"] for extra in record["extras"]}
        assert extras["access_status"] == "metadata-only"
        assert record["resources"] == []


# ---------------------------------------------------------------------------
# Configuration and vocabulary
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_sources_yaml_declares_this_adapter(self) -> None:
        osti = config.load_sources()["osti"]
        assert osti["enabled"] is True
        assert osti["tier"] == 1
        assert osti["max_records"] == 5
        assert osti["api"] == "https://www.osti.gov/api/v1/records"

    def test_the_source_key_semantics_are_documented_where_they_are_read(self) -> None:
        osti = config.load_sources()["osti"]
        assert "entry_date" in osti["source_key"]
        assert "entry_date" in OstiAdapter.source_key_semantics

    def test_every_product_type_maps_into_the_resource_kind_vocabulary(self) -> None:
        from harvest.models import RESOURCE_KINDS

        assert set(PRODUCT_TYPES.values()) <= set(RESOURCE_KINDS)

    def test_an_unknown_product_type_is_other_not_a_guess(self) -> None:
        payload = {**synthetic("1"), "product_type": "Interpretive Dance"}
        mapped = OstiAdapter().map(RawObservation(
            source_system="osti", source_id="1", source_key="k", payload=payload
        ))
        assert mapped.source.resource_kind == "other"

    def test_an_absent_licence_is_notspecified_and_never_inferred_open(self) -> None:
        for fixture in ALL_FIXTURES:
            mapped = OstiAdapter().map(raw_for(fixture))
            assert mapped.source.license_id == "notspecified"
            assert mapped.source.license_raw is None
