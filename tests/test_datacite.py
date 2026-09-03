"""Track B — the DataCite adapter.

Everything here runs offline. ``map()`` is pure by contract, and ``harvest()``
is exercised against a fake client that replays the captured payloads, so the
suite never touches api.datacite.org.

The fixture inventory these tests enforce is ``fixtures/fixtures-catalogue.md``
rows ``dc-01`` .. ``dc-09``; ``dc-08`` is a reconciliation case (fuzzy merge
across two registrations of one work) rather than a mapping one and belongs to
the reconciler track.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

import pytest

from harvest import config
from harvest.adapters.base import (
    SourceConfig,
    SourceUnreachable,
    get_adapter,
    run_adapter,
)
from harvest.adapters.datacite import REQUIRED_STATE, DataCiteAdapter
from harvest.events import read_events
from harvest.http import FetchResult
from harvest.identity import slug_for_identity
from harvest.materialize import materialize_all
from harvest.models import RawObservation

FIXTURES = config.fixtures_dir() / "datacite"


# ---------------------------------------------------------------------------
# Fixture plumbing
# ---------------------------------------------------------------------------


def load_fixtures() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.json"))
    ]


def raw_payload(fixture: dict) -> dict:
    return json.loads((FIXTURES / fixture["raw"]).read_text(encoding="utf-8"))


def observation(fixture: dict) -> RawObservation:
    payload = raw_payload(fixture)
    return RawObservation(
        source_system="datacite",
        source_id=payload["attributes"]["doi"],
        source_key=fixture["source_key"],
        url=payload["attributes"].get("url"),
        fetched_at="2026-08-31T00:00:00Z",
        payload=payload,
    )


ALL = load_fixtures()
IDS = [fixture["fixture_id"] for fixture in ALL]


def fixture_by_id(fixture_id: str) -> dict:
    return next(f for f in ALL if f["fixture_id"] == fixture_id)


class FakeClient:
    """Replays a canned listing document. Records every URL it was asked for."""

    def __init__(self, responses: list[FetchResult]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str, **_: object) -> FetchResult:
        self.urls.append(url)
        if not self.responses:
            return FetchResult(url=url, status_code=200, changed=True, text='{"data": []}')
        result = self.responses.pop(0)
        return FetchResult(
            url=url,
            status_code=result.status_code,
            changed=result.changed,
            text=result.text,
            headers=result.headers,
            error=result.error,
        )


def listing(*fixture_ids: str) -> FetchResult:
    data = [raw_payload(fixture_by_id(fid)) for fid in fixture_ids]
    return FetchResult(
        url="fake", status_code=200, changed=True,
        text=json.dumps({"data": data, "meta": {"total": len(data)}}),
    )


def adapter_with(responses: list[FetchResult], **options: object) -> DataCiteAdapter:
    settings = {
        "api": "https://api.datacite.org/dois",
        "state": REQUIRED_STATE,
        "page_size": 25,
        "queries": ['"IEA Wind Task"'],
        **options,
    }
    return DataCiteAdapter(
        config=SourceConfig.from_mapping("datacite", {**settings}),
        client=FakeClient(responses),
    )


# ---------------------------------------------------------------------------
# The fixture contract
# ---------------------------------------------------------------------------


def test_every_catalogued_fixture_exists() -> None:
    """fixtures-catalogue.md rows dc-01..dc-09, minus the reconciliation one."""
    expected = {
        "dc-01-canonical",
        "dc-02-multi-title",
        "dc-03-type-mismatch",
        "dc-04-non-findable",
        "dc-05-case-variant",
        "dc-06-related-identifiers",
        "dc-07-publisher-object",
        "dc-09-nonstandard-rights",
    }
    assert expected <= set(IDS)


@pytest.mark.parametrize("fixture", ALL, ids=IDS)
class TestMapMatchesTheFixture:
    def test_identity_key(self, fixture: dict) -> None:
        mapped = DataCiteAdapter().map(observation(fixture))
        assert mapped.identity_key == fixture["identity_key"]

    def test_source_namespace(self, fixture: dict) -> None:
        mapped = DataCiteAdapter().map(observation(fixture))
        assert mapped.source.model_dump(mode="json", exclude_none=True) == fixture["source"]

    def test_provenance(self, fixture: dict) -> None:
        mapped = DataCiteAdapter().map(observation(fixture))
        actual = {
            key: value.model_dump(mode="json", exclude_none=True)
            for key, value in mapped.provenance.items()
        }
        assert actual == fixture["provenance"]

    def test_source_key_is_attributes_updated(self, fixture: dict) -> None:
        payload = raw_payload(fixture)
        assert DataCiteAdapter.source_key_for(payload) == fixture["source_key"]
        assert payload["attributes"]["updated"].startswith(fixture["source_key"][:19])

    def test_slug(self, fixture: dict) -> None:
        assert slug_for_identity(fixture["identity_key"]) == fixture["expected_slug"]

    def test_findable_flag_agrees_with_the_payload(self, fixture: dict) -> None:
        assert DataCiteAdapter.is_findable(raw_payload(fixture)) is fixture["findable"]

    def test_map_is_deterministic(self, fixture: dict) -> None:
        first = DataCiteAdapter().map(observation(fixture))
        second = DataCiteAdapter().map(observation(fixture))
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_map_does_not_mutate_the_payload(self, fixture: dict) -> None:
        payload = raw_payload(fixture)
        before = copy.deepcopy(payload)
        DataCiteAdapter().map(
            RawObservation(
                source_system="datacite",
                source_id=payload["attributes"]["doi"],
                source_key=fixture["source_key"],
                payload=payload,
            )
        )
        assert payload == before

    def test_licence_is_in_the_register(self, fixture: dict) -> None:
        from harvest.licenses import is_known_license

        mapped = DataCiteAdapter().map(observation(fixture))
        assert is_known_license(mapped.source.license_id)

    def test_resource_kind_is_a_known_kind(self, fixture: dict) -> None:
        from harvest.models import RESOURCE_KINDS

        mapped = DataCiteAdapter().map(observation(fixture))
        assert mapped.source.resource_kind in RESOURCE_KINDS

    def test_no_iea_task_is_asserted_from_free_text(self, fixture: dict) -> None:
        """Candidates only — a group outside groups.yaml would fail the gate."""
        mapped = DataCiteAdapter().map(observation(fixture))
        assert mapped.source.iea_task == []


# ---------------------------------------------------------------------------
# dc-02 — title selection
# ---------------------------------------------------------------------------


class TestTitles:
    def test_primary_title_is_the_untyped_one(self) -> None:
        fixture = fixture_by_id("dc-02-multi-title")
        payload = raw_payload(fixture)
        titles = payload["attributes"]["titles"]
        assert len(titles) > 1 and any(t.get("titleType") for t in titles)

        mapped = DataCiteAdapter().map(observation(fixture))
        assert mapped.source.title == titles[0]["title"]
        assert mapped.source.extra["alternate_titles"] == [
            {"title": titles[1]["title"], "title_type": "AlternativeTitle"}
        ]

    def test_translated_title_is_never_the_primary_title(self) -> None:
        """No live IEA-Wind DOI carries one (verified 2026-08-31), so assert it here."""
        payload = {
            "id": "10.5072/example-translated-1",
            "attributes": {
                "doi": "10.5072/example-translated-1",
                "state": "findable",
                "updated": "2026-01-02T03:04:05Z",
                "titles": [
                    {"title": "Wind lidar measurement campaign, Østerild"},
                    {"titleType": "TranslatedTitle", "title": "Vindlidarmålekampagne, Østerild",
                     "lang": "da"},
                    {"titleType": "Subtitle", "title": "Phase II"},
                ],
                "types": {"resourceTypeGeneral": "Dataset"},
            },
        }
        mapped = DataCiteAdapter().map(
            RawObservation(source_system="datacite", source_id=payload["attributes"]["doi"],
                           source_key="2026-01-02T03:04:05Z", payload=payload)
        )
        assert mapped.source.title == "Wind lidar measurement campaign, Østerild"
        assert mapped.source.extra["alternate_titles"] == [
            {"title": "Vindlidarmålekampagne, Østerild", "title_type": "TranslatedTitle",
             "lang": "da"},
            {"title": "Phase II", "title_type": "Subtitle"},
        ]

    def test_a_wholly_typed_title_list_still_yields_a_title(self) -> None:
        payload = {
            "id": "10.5072/example-typed-only",
            "attributes": {
                "doi": "10.5072/example-typed-only",
                "state": "findable",
                "updated": "2026-01-02T03:04:05Z",
                "titles": [{"titleType": "AlternativeTitle", "title": "Only an alternate"}],
            },
        }
        mapped = DataCiteAdapter().map(
            RawObservation(source_system="datacite", source_id=payload["attributes"]["doi"],
                           source_key="x", payload=payload)
        )
        assert mapped.source.title == "Only an alternate"
        assert "alternate_titles" not in mapped.source.extra


# ---------------------------------------------------------------------------
# dc-03 — resource kind
# ---------------------------------------------------------------------------


class TestResourceKind:
    def test_other_is_left_for_classification_with_the_source_type_retained(self) -> None:
        fixture = fixture_by_id("dc-03-type-mismatch")
        payload = raw_payload(fixture)
        assert payload["attributes"]["types"]["resourceTypeGeneral"] == "Other"

        mapped = DataCiteAdapter().map(observation(fixture))
        assert mapped.source.resource_kind == "other"
        # dc-03: the source type is retained verbatim so a classifier can use it.
        assert mapped.source.extra["datacite_types"] == payload["attributes"]["types"]
        # Tier 1 is deterministic; nothing here was inferred by a model.
        assert mapped.provenance["resource_kind"].extraction_method in ("api", "pattern")

    @pytest.mark.parametrize(
        "types,expected,method",
        [
            ({"resourceTypeGeneral": "Dataset"}, "dataset", "api"),
            ({"resourceTypeGeneral": "Software"}, "software", "api"),
            ({"resourceTypeGeneral": "Report"}, "report", "api"),
            ({"resourceTypeGeneral": "JournalArticle"}, "publication", "api"),
            ({"resourceTypeGeneral": "Text"}, "publication", "api"),
            ({"resourceTypeGeneral": "Model"}, "model", "api"),
            ({"resourceTypeGeneral": "Collection"}, "other", "api"),
            ({"resourceTypeGeneral": "Other", "resourceType": "poster"}, "publication", "pattern"),
            ({"resourceTypeGeneral": "Other", "resourceType": "conferenceObject"},
             "publication", "pattern"),
            ({"resourceTypeGeneral": "Other", "resourceType": "Dataset"}, "dataset", "pattern"),
            ({"resourceTypeGeneral": "Other", "schemaOrg": "SoftwareSourceCode"},
             "software", "pattern"),
            ({"resourceTypeGeneral": "Other"}, "other", "api"),
            ({}, "other", "api"),
        ],
    )
    def test_deterministic_mapping(self, types: dict, expected: str, method: str) -> None:
        kind, extraction = DataCiteAdapter._resource_kind({"types": types})
        assert (kind, extraction) == (expected, method)


# ---------------------------------------------------------------------------
# dc-04 — only findable DOIs
# ---------------------------------------------------------------------------


class TestFindableOnly:
    def test_the_fixture_is_not_findable(self) -> None:
        fixture = fixture_by_id("dc-04-non-findable")
        assert fixture["expect_skipped"] is True
        assert DataCiteAdapter.is_findable(raw_payload(fixture)) is False

    def test_harvest_skips_it_and_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        adapter = adapter_with([listing("dc-01-canonical", "dc-04-non-findable")])
        with caplog.at_level(logging.WARNING, logger="harvest.adapters.datacite"):
            observations = list(adapter.harvest(max_records=5))
        assert [o.source_id for o in observations] == ["10.5281/zenodo.20218022"]
        assert any("registered" in record.getMessage() for record in caplog.records)

    @pytest.mark.parametrize("state", ["registered", "draft", "", None])
    def test_no_state_but_findable_is_ever_yielded(self, state: object) -> None:
        payload = copy.deepcopy(raw_payload(fixture_by_id("dc-01-canonical")))
        payload["attributes"]["state"] = state
        response = FetchResult(url="fake", status_code=200, changed=True,
                               text=json.dumps({"data": [payload]}))
        assert list(adapter_with([response]).harvest(max_records=5)) == []


# ---------------------------------------------------------------------------
# dc-05 — DOI case normalisation
# ---------------------------------------------------------------------------


class TestDoiCaseNormalisation:
    def test_the_shouted_doi_is_the_same_record(self) -> None:
        shouted = fixture_by_id("dc-05-case-variant")
        canonical = fixture_by_id("dc-01-canonical")
        assert raw_payload(shouted)["attributes"]["doi"] == "10.5281/ZENODO.20218022"

        a = DataCiteAdapter().map(observation(shouted))
        b = DataCiteAdapter().map(observation(canonical))
        assert a.identity_key == b.identity_key
        assert a.source_id == b.source_id
        assert a.source.doi == b.source.doi
        assert slug_for_identity(a.identity_key) == slug_for_identity(b.identity_key)

    def test_the_millisecond_spelling_is_the_same_source_key(self) -> None:
        """The /dois listing and /dois/{doi} spell one instant two ways."""
        shouted = raw_payload(fixture_by_id("dc-05-case-variant"))
        canonical = raw_payload(fixture_by_id("dc-01-canonical"))
        assert shouted["attributes"]["updated"] != canonical["attributes"]["updated"]
        assert (DataCiteAdapter.source_key_for(shouted)
                == DataCiteAdapter.source_key_for(canonical))

    def test_related_doi_targets_are_normalised_too(self) -> None:
        mapped = DataCiteAdapter().map(observation(fixture_by_id("dc-05-case-variant")))
        assert mapped.source.related_identifiers == [
            {"identifier": "10.5281/zenodo.20218023", "identifier_type": "DOI",
             "relation": "HasVersion"}
        ]

    def test_harvest_dedupes_case_variants_within_a_run(self) -> None:
        adapter = adapter_with(
            [listing("dc-01-canonical", "dc-05-case-variant", "dc-09-nonstandard-rights")]
        )
        observations = list(adapter.harvest(max_records=5))
        assert [o.source_id for o in observations] == [
            "10.5281/zenodo.20218022",
            "10.2314/kxp:1790028361",
        ]

    def test_a_case_variant_writes_no_second_event(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        for fixture_id in ("dc-01-canonical", "dc-05-case-variant"):
            adapter = adapter_with([listing(fixture_id)])
            run_adapter(adapter, max_records=5, events_dir=events)
        identity = fixture_by_id("dc-01-canonical")["identity_key"]
        assert len(read_events(identity, events)) == 1
        assert len(list(events.glob("*.jsonl"))) == 1


# ---------------------------------------------------------------------------
# dc-06 — related identifiers are links, not records
# ---------------------------------------------------------------------------


class TestRelatedIdentifiers:
    def test_relations_are_mapped_not_followed(self) -> None:
        fixture = fixture_by_id("dc-06-related-identifiers")
        mapped = DataCiteAdapter().map(observation(fixture))
        relations = {item["relation"] for item in mapped.source.related_identifiers}
        assert {"Cites", "IsVersionOf", "IsPartOf"} <= relations
        assert all("identifier" in item for item in mapped.source.related_identifiers)

    def test_harvest_creates_no_record_for_any_target(self) -> None:
        fixture = fixture_by_id("dc-06-related-identifiers")
        adapter = adapter_with([listing("dc-06-related-identifiers")])
        observations = list(adapter.harvest(max_records=5))
        assert [o.source_id for o in observations] == [fixture["identity_key"]]

        targets = {
            item["relatedIdentifier"].lower()
            for item in raw_payload(fixture)["attributes"]["relatedIdentifiers"]
        }
        assert targets and not targets & {o.source_id for o in observations}

    def test_is_supplement_to_is_carried(self) -> None:
        mapped = DataCiteAdapter().map(observation(fixture_by_id("dc-02-multi-title")))
        assert {
            "identifier": "10.1016/j.rser.2025.115418",
            "identifier_type": "DOI",
            "relation": "IsSupplementTo",
        } in mapped.source.related_identifiers


# ---------------------------------------------------------------------------
# dc-07 — publisher as string and as object
# ---------------------------------------------------------------------------


class TestPublisher:
    def test_object_publisher_yields_the_same_field_as_a_string_one(self) -> None:
        obj = raw_payload(fixture_by_id("dc-07-publisher-object"))
        string = raw_payload(fixture_by_id("dc-01-canonical"))
        assert isinstance(obj["attributes"]["publisher"], dict)
        assert isinstance(string["attributes"]["publisher"], str)

        mapped_obj = DataCiteAdapter().map(observation(fixture_by_id("dc-07-publisher-object")))
        mapped_str = DataCiteAdapter().map(observation(fixture_by_id("dc-01-canonical")))
        assert mapped_obj.source.publisher == mapped_str.source.publisher == "IEA Wind Task 49"

    def test_the_object_is_retained_verbatim(self) -> None:
        fixture = fixture_by_id("dc-07-publisher-object")
        mapped = DataCiteAdapter().map(observation(fixture))
        assert (mapped.source.extra["datacite_publisher"]
                == raw_payload(fixture)["attributes"]["publisher"])

    def test_a_string_publisher_records_no_object(self) -> None:
        mapped = DataCiteAdapter().map(observation(fixture_by_id("dc-01-canonical")))
        assert "datacite_publisher" not in mapped.source.extra


# ---------------------------------------------------------------------------
# dc-09 — unmappable rights
# ---------------------------------------------------------------------------


class TestRights:
    def test_unmappable_rights_are_flagged_not_dropped(self) -> None:
        from harvest.licenses import map_license

        mapped = DataCiteAdapter().map(observation(fixture_by_id("dc-09-nonstandard-rights")))
        assert mapped.source.license_raw == "Open Access"      # kept verbatim
        assert mapped.source.license_id == "notspecified"      # never guessed
        assert map_license(mapped.source.license_raw) == ("notspecified", False)

    def test_the_run_report_flags_it(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        records = tmp_path / "records"
        run_adapter(adapter_with([listing("dc-09-nonstandard-rights")]),
                    max_records=5, events_dir=events)
        outcome = materialize_all(events_directory=events, records_directory=records,
                                  validate=False)
        assert any("Open Access" in str(entry) for entry in outcome.unmapped_licenses)

    def test_an_absent_rights_list_is_notspecified_but_not_flagged(self) -> None:
        payload = copy.deepcopy(raw_payload(fixture_by_id("dc-01-canonical")))
        payload["attributes"]["rightsList"] = []
        mapped = DataCiteAdapter().map(
            RawObservation(source_system="datacite", source_id=payload["attributes"]["doi"],
                           source_key="k", payload=payload)
        )
        assert mapped.source.license_raw is None
        assert mapped.source.license_id == "notspecified"

    def test_licence_raw_and_id_come_from_one_rights_entry(self) -> None:
        """cc-by-nc-nd-4.0 (SPDX) wins over the eu-repo access statement beside it."""
        mapped = DataCiteAdapter().map(observation(fixture_by_id("dc-03-type-mismatch")))
        assert mapped.source.license_raw == "cc-by-nc-nd-4.0"
        assert mapped.source.license_id == "cc-nc-nd"

    def test_access_status_is_never_inferred_from_a_licence(self) -> None:
        mapped = DataCiteAdapter().map(observation(fixture_by_id("dc-01-canonical")))
        assert mapped.source.license_id == "cc-by"
        assert mapped.source.access_status is None

    def test_eu_repo_access_is_read(self) -> None:
        mapped = DataCiteAdapter().map(observation(fixture_by_id("dc-03-type-mismatch")))
        assert mapped.source.access_status == "open"
        assert mapped.provenance["access_status"].extraction_method == "pattern"


# ---------------------------------------------------------------------------
# harvest(): the cap, degradation, change detection
# ---------------------------------------------------------------------------


class TestHarvest:
    def test_the_limit_is_honoured(self) -> None:
        ids = [f["fixture_id"] for f in ALL if f["findable"]]
        adapter = adapter_with([listing(*ids)], queries=['"IEA Wind Task"'])
        assert len(list(adapter.harvest(max_records=3))) == 3

    def test_the_source_key_is_attributes_updated(self) -> None:
        adapter = adapter_with([listing("dc-01-canonical")])
        observed = list(adapter.harvest(max_records=5))[0]
        payload = raw_payload(fixture_by_id("dc-01-canonical"))
        assert observed.source_key == DataCiteAdapter.source_key_for(payload)
        assert observed.payload == payload      # verbatim

    def test_only_configured_queries_are_requested(self) -> None:
        adapter = adapter_with(
            [listing("dc-01-canonical"), listing("dc-09-nonstandard-rights")],
            queries=['"IEA Wind Task"', 'subjects.subject:"IEA Wind Task"'],
        )
        list(adapter.harvest(max_records=5))
        assert len(adapter.client.urls) == 2
        assert all(url.startswith("https://api.datacite.org/dois?") for url in adapter.client.urls)
        assert "sort=-updated" in adapter.client.urls[0]

    def test_a_transport_failure_disables_the_source_without_crashing(self) -> None:
        broken = FetchResult(url="fake", status_code=None, changed=False,
                             error="connection reset")
        adapter = adapter_with([broken])
        with pytest.raises(SourceUnreachable):
            list(adapter.harvest(max_records=5))

        result = run_adapter(adapter_with([broken]), max_records=5)
        assert result.reachable is False
        assert result.errors and "connection reset" in result.errors[0]

    def test_one_failed_query_does_not_lose_the_other(self) -> None:
        broken = FetchResult(url="fake", status_code=500, changed=False)
        adapter = adapter_with(
            [broken, listing("dc-01-canonical")],
            queries=['"IEA Wind Task"', '"IEA Wind TCP"'],
        )
        assert len(list(adapter.harvest(max_records=5))) == 1

    def test_a_304_listing_yields_nothing_and_is_not_an_error(self) -> None:
        not_modified = FetchResult(url="fake", status_code=304, changed=False)
        adapter = adapter_with([not_modified])
        assert list(adapter.harvest(max_records=5)) == []

    def test_a_malformed_body_is_survived(self) -> None:
        garbage = FetchResult(url="fake", status_code=200, changed=True, text="<html>nope")
        adapter = adapter_with([garbage, listing("dc-01-canonical")],
                               queries=["a", "b"])
        assert len(list(adapter.harvest(max_records=5))) == 1

    def test_an_item_with_no_doi_is_skipped(self) -> None:
        payload = copy.deepcopy(raw_payload(fixture_by_id("dc-01-canonical")))
        payload["attributes"]["doi"] = ""
        payload["id"] = ""
        response = FetchResult(url="fake", status_code=200, changed=True,
                               text=json.dumps({"data": [payload]}))
        assert list(adapter_with([response]).harvest(max_records=5)) == []

    def test_no_queries_configured_yields_nothing_and_does_not_raise(self) -> None:
        adapter = adapter_with([], queries=[])
        assert list(adapter.harvest(max_records=5)) == []


# ---------------------------------------------------------------------------
# Change detection and the end-to-end pipeline (ADR-0026, ADR-0037)
# ---------------------------------------------------------------------------


class TestEndToEnd:
    #: dc-05 is dc-01 shouted, so these five ids are four identities. Five is
    #: also the prototype cap, so a longer list could not be harvested anyway.
    CAPPED = [
        "dc-01-canonical",
        "dc-02-multi-title",
        "dc-03-type-mismatch",
        "dc-05-case-variant",
        "dc-09-nonstandard-rights",
    ]

    def _run(self, tmp_path: Path, fixture_ids: list[str]):
        events = tmp_path / "events"
        return run_adapter(adapter_with([listing(*fixture_ids)]), max_records=5, events_dir=events)

    def test_a_second_identical_run_writes_no_event(self, tmp_path: Path) -> None:
        first = self._run(tmp_path, self.CAPPED)
        assert first.changed == len(self.CAPPED) - 1     # dc-05 is dc-01 shouted

        before = sorted(
            (p.name, p.read_bytes()) for p in (tmp_path / "events").glob("*.jsonl")
        )
        second = self._run(tmp_path, self.CAPPED)
        after = sorted(
            (p.name, p.read_bytes()) for p in (tmp_path / "events").glob("*.jsonl")
        )
        assert second.changed == 0
        assert second.skipped_unchanged == first.changed
        assert before == after

    def test_a_new_source_key_appends_exactly_one_event(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        run_adapter(adapter_with([listing("dc-01-canonical")]), max_records=5, events_dir=events)

        bumped = copy.deepcopy(raw_payload(fixture_by_id("dc-01-canonical")))
        bumped["attributes"]["updated"] = "2026-09-01T00:00:00Z"
        bumped["attributes"]["titles"] = [{"title": "The IEA Wind RFA3 Deep-Water Reference "
                                                   "Array Design (corrected)"}]
        response = FetchResult(url="fake", status_code=200, changed=True,
                               text=json.dumps({"data": [bumped]}))
        run_adapter(adapter_with([response]), max_records=5, events_dir=events)

        identity = fixture_by_id("dc-01-canonical")["identity_key"]
        events_written = read_events(identity, events)
        assert len(events_written) == 2
        assert events_written[-1].source_key == "2026-09-01T00:00:00Z"
        assert events_written[-1].source["title"].endswith("(corrected)")

    def test_records_pass_the_ckan_gate(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        records = tmp_path / "records"
        run_adapter(adapter_with([listing(*self.CAPPED)]), max_records=5, events_dir=events)

        outcome = materialize_all(events_directory=events, records_directory=records)
        assert outcome.violations == [], [str(v) for v in outcome.violations]
        assert outcome.total == len(self.CAPPED) - 1

        for path in records.glob("*.json"):
            package = json.loads(path.read_text(encoding="utf-8"))
            assert path.stem == package["name"]
            assert package["state"] == "active"
            extras = {extra["key"]: extra["value"] for extra in package["extras"]}
            assert extras["source_system"] == "datacite"
            assert all(isinstance(extra["value"], str) for extra in package["extras"])
            # No group is ever asserted from DataCite free text.
            assert package["groups"] == []

    def test_materialisation_is_byte_stable(self, tmp_path: Path) -> None:
        events = tmp_path / "events"
        records = tmp_path / "records"
        run_adapter(adapter_with([listing("dc-01-canonical")]), max_records=5, events_dir=events)
        materialize_all(events_directory=events, records_directory=records)
        first = {p.name: p.read_bytes() for p in records.glob("*.json")}
        outcome = materialize_all(events_directory=events, records_directory=records)
        assert outcome.written == []
        assert {p.name: p.read_bytes() for p in records.glob("*.json")} == first


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_the_registry_knows_the_adapter(self) -> None:
        assert get_adapter("datacite") is DataCiteAdapter

    def test_names_agree_in_four_places(self) -> None:
        assert DataCiteAdapter.source_name == "datacite"
        assert Path(DataCiteAdapter.__module__.replace(".", "/")).name == "datacite"
        assert "datacite" in config.load_sources()

    def test_it_is_tier_one(self) -> None:
        assert DataCiteAdapter.tier == 1
        assert config.load_sources()["datacite"]["tier"] == 1

    def test_the_configured_source_key_describes_attributes_updated(self) -> None:
        declared = config.load_sources()["datacite"]["source_key"]
        assert "attributes.updated" in declared
        assert "attributes.updated" in DataCiteAdapter.source_key_semantics

    def test_the_config_declares_queries_and_findable_only(self) -> None:
        block = config.load_sources()["datacite"]
        assert block["state"] == "findable"
        assert block["queries"], "the query strategy lives in sources.yaml"
        assert block["api"] == "https://api.datacite.org/dois"

    def test_close_is_safe_without_a_client(self) -> None:
        DataCiteAdapter().close()
