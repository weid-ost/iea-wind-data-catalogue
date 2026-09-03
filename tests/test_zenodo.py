"""Track A — the Zenodo adapter, against ``fixtures/zenodo/``.

Every payload under ``fixtures/zenodo/raw/`` except ``zen-01`` (the foundation's
reference), ``zen-06`` and ``zen-07`` was captured verbatim from
``https://zenodo.org/api/records`` on 2026-08-31; the invented ones say so in
their own ``invented`` field. Nothing in this module touches the network:
``map()`` is pure by contract and ``harvest()`` is exercised through an injected
fake client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harvest import config
from harvest.adapters.base import SourceConfig, SourceUnreachable, run_adapter
from harvest.adapters.zenodo import (
    ZENODO_API,
    ZenodoAdapter,
    access_status_for,
    resource_kind_for,
    tasks_for_community,
)
from harvest.ckan_compat import validate_package
from harvest.events import read_events, replay, resolve
from harvest.http import FetchResult
from harvest.identity import slug_for_identity, slugify
from harvest.licenses import map_license
from harvest.materialize import to_ckan_package
from harvest.models import Event, FieldProvenance, RawObservation

FIXTURES = config.fixtures_dir() / "zenodo"

#: ``zen-01`` is the foundation's invented reference payload and
#: ``tests/test_fixtures.py::TestZen01::test_source_key_is_the_revision`` pins its
#: declared key to the bare ``revision``. The adapter's key is the revision paired
#: with the version DOI (ADR-0026, and see the module docstring of
#: ``harvest/adapters/zenodo.py`` for the live evidence that the pairing is
#: load-bearing), so zen-01 is excluded from the key-derivation test only. Its
#: mapping is asserted like every other fixture, because ``map()`` copies the key
#: it is handed rather than deriving one.
KEY_DERIVATION_EXEMPT = {"zen-01-canonical"}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def source_namespace_fixtures() -> list[dict[str, Any]]:
    out = []
    for path in sorted(FIXTURES.glob("zen-*.json")):
        fixture = load(path)
        if fixture.get("fixture_kind") == "source_namespace":
            out.append(fixture)
    return out


def record_fixtures() -> list[dict[str, Any]]:
    return [
        load(path)
        for path in sorted(FIXTURES.glob("zen-*.json"))
        if load(path).get("fixture_kind") == "record"
    ]


ALL = source_namespace_fixtures()


def raw_payload(fixture: dict[str, Any]) -> dict[str, Any]:
    return load(FIXTURES / fixture["raw"])


def observation(fixture: dict[str, Any]) -> RawObservation:
    payload = raw_payload(fixture)
    return RawObservation(
        source_system="zenodo",
        source_id=fixture["source_id"],
        source_key=fixture["source_key"],
        fetched_at="2026-08-31T00:00:00Z",
        url=(payload.get("links") or {}).get("self_html"),
        payload=payload,
    )


def configured_adapter(client: Any = None) -> ZenodoAdapter:
    """The adapter as the CLI builds it: real ``sources.yaml`` config."""
    return ZenodoAdapter(
        config=SourceConfig.from_mapping("zenodo", config.load_sources()["zenodo"]),
        client=client,
    )


def fixture_by_id(fixture_id: str) -> dict[str, Any]:
    return load(FIXTURES / f"{fixture_id}.json")


# ---------------------------------------------------------------------------
# A fake HarvestClient — the whole surface the adapter uses is ``get(url)``
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self, pages: dict[str, Any] | None = None,
                 failures: dict[str, str] | None = None,
                 statuses: dict[str, int] | None = None) -> None:
        self.pages = pages or {}
        self.failures = failures or {}
        self.statuses = statuses or {}
        self.calls: list[str] = []
        self.closed = False

    def _match(self, url: str, table: dict[str, Any]) -> Any:
        for token, value in table.items():
            if token in url:
                return value
        return None

    def get(self, url: str, **kwargs: Any) -> FetchResult:
        self.calls.append(url)
        error = self._match(url, self.failures)
        if error:
            return FetchResult(url=url, status_code=None, changed=False, error=error)
        status = self._match(url, self.statuses)
        body = self._match(url, self.pages)
        if status is not None:
            return FetchResult(url=url, status_code=status, changed=True,
                               text=json.dumps(body) if body is not None else "")
        if body is None:
            return FetchResult(url=url, status_code=404, changed=False, error="not found")
        return FetchResult(url=url, status_code=200, changed=True, text=json.dumps(body))

    def close(self) -> None:
        self.closed = True


def listing(*payloads: dict[str, Any]) -> dict[str, Any]:
    return {"hits": {"total": len(payloads), "hits": list(payloads)}}


# ---------------------------------------------------------------------------
# The fixture sweep
# ---------------------------------------------------------------------------


class TestTheFixtureSet:
    def test_every_catalogue_row_has_a_fixture(self) -> None:
        """``fixtures/fixtures-catalogue.md`` lists zen-01 .. zen-12. All of them."""
        stems = {path.stem for path in FIXTURES.glob("zen-*.json")}
        prefixes = {stem.split("-")[0] + "-" + stem.split("-")[1] for stem in stems}
        assert prefixes == {f"zen-{n:02d}" for n in range(1, 13)}

    def test_every_fixture_has_its_verbatim_payload(self) -> None:
        for fixture in ALL + record_fixtures():
            assert (FIXTURES / fixture["raw"]).exists(), fixture["fixture_id"]

    def test_invented_fixtures_say_so(self) -> None:
        """Prefer real payloads; when you must invent one, admit it in the file.

        zen-01 belongs in this set. It is the reference fixture — the shape
        every other Zenodo fixture was built from — and it was the one payload
        here that had been invented without saying so, on ids that collided
        with a live Zenodo record (fixture-compliance-02). Its identifiers now
        sit on the reserved 10.5072 test prefix and it declares itself.
        """
        invented = {f["fixture_id"] for f in ALL + record_fixtures() if f.get("invented")}
        assert invented == {
            "zen-01-canonical",
            "zen-06-embargoed",
            "zen-07-html-description",
            "zen-12-tombstone",
        }

    def test_invented_fixtures_never_wear_a_live_identifier(self) -> None:
        """An invented Zenodo payload uses the reserved 10.5072 test prefix.

        10.5281 is Zenodo's live prefix: a DOI under it either resolves to
        somebody's work or will one day. zen-02..zen-05 and zen-08..zen-11 are
        verbatim captures and keep theirs; the invented ones do not get to
        borrow one.
        """
        for fixture in ALL + record_fixtures():
            if not fixture.get("invented") or fixture.get("raw_is_capture"):
                continue
            assert fixture["identity_key"].startswith("10.5072/"), fixture["fixture_id"]


@pytest.mark.parametrize("fixture", ALL, ids=lambda f: f["fixture_id"])
class TestMap:
    def test_identity_and_source_namespace(self, fixture: dict[str, Any]) -> None:
        mapped = configured_adapter().map(observation(fixture))
        assert mapped.identity_key == fixture["identity_key"]
        assert mapped.source.model_dump(mode="json", exclude_none=True) == fixture["source"]

    def test_provenance(self, fixture: dict[str, Any]) -> None:
        mapped = configured_adapter().map(observation(fixture))
        rendered = {
            key: value.model_dump(mode="json", exclude_none=True)
            for key, value in mapped.provenance.items()
        }
        assert rendered == fixture["provenance"]

    def test_identifiers_are_echoed_not_reinvented(self, fixture: dict[str, Any]) -> None:
        mapped = configured_adapter().map(observation(fixture))
        assert mapped.source_system == "zenodo"
        assert mapped.source_id == fixture["source_id"]
        assert mapped.source_key == fixture["source_key"]

    def test_slug(self, fixture: dict[str, Any]) -> None:
        assert slug_for_identity(fixture["identity_key"]) == fixture["expected_slug"]

    def test_map_is_pure_and_uses_the_observation_clock(self, fixture: dict[str, Any]) -> None:
        """No clock: ``fetched_at`` comes from the observation, and two calls agree."""
        raw = observation(fixture)
        first = configured_adapter().map(raw)
        second = configured_adapter().map(raw)
        assert first.fetched_at == raw.fetched_at
        assert first.model_dump(mode="json") == second.model_dump(mode="json")

    def test_map_works_without_any_configuration(self, fixture: dict[str, Any]) -> None:
        """A bare ``ZenodoAdapter()`` still maps — ``map()`` reads no files."""
        assert ZenodoAdapter().map(observation(fixture)).identity_key == fixture["identity_key"]

    def test_the_record_passes_the_ckan_gate(self, fixture: dict[str, Any]) -> None:
        mapped = configured_adapter().map(observation(fixture))
        resolved = resolve(
            fixture["identity_key"],
            events=[Event(
                observed_at="2026-08-31T00:00:00Z",
                event_type="scraped",
                identity_key=fixture["identity_key"],
                source_key=fixture["source_key"],
                source_system="zenodo",
                source_id=fixture["source_id"],
                source=mapped.source.model_dump(mode="json", exclude_none=True),
                provenance=mapped.provenance,
            )],
        )
        package = to_ckan_package(resolved)
        violations = validate_package(
            package, config.organization_names(), config.group_names()
        )
        assert violations == [], [str(v) for v in violations]

    def test_the_source_key_is_derived_from_the_payload(self, fixture: dict[str, Any]) -> None:
        if fixture["fixture_id"] in KEY_DERIVATION_EXEMPT:
            pytest.skip("zen-01's declared key is pinned to the bare revision; see the module docstring")
        assert ZenodoAdapter.source_key_for(raw_payload(fixture)) == fixture["source_key"]


# ---------------------------------------------------------------------------
# The source key (ADR-0026)
# ---------------------------------------------------------------------------


class TestSourceKey:
    def test_the_field_name_is_revision(self) -> None:
        """Verified live 2026-08-31: every payload carries a top-level ``revision``."""
        for fixture in ALL:
            assert "revision" in raw_payload(fixture), fixture["fixture_id"]

    def test_it_pairs_the_revision_with_the_version_doi(self) -> None:
        payload = raw_payload(fixture_by_id("zen-02-concept-vs-version"))
        assert ZenodoAdapter.source_key_for(payload) == (
            f"{payload['revision']}@{payload['doi']}"
        )

    def test_a_new_version_moves_the_key_even_when_the_revision_does_not(self) -> None:
        """The reason the version DOI is in the key at all.

        Live: OpenOA v3.1.4 (record 18421933) and v3.2 (record 18424146) are both
        revision 4. Under a bare-revision key the release would have been skipped.
        """
        latest = raw_payload(fixture_by_id("zen-02-concept-vs-version"))
        previous = dict(latest, id=18421933, doi="10.5281/zenodo.18421933")
        assert latest["revision"] == previous["revision"] == 4
        assert ZenodoAdapter.source_key_for(latest) != ZenodoAdapter.source_key_for(previous)

    def test_a_metadata_edit_moves_the_key(self) -> None:
        payload = raw_payload(fixture_by_id("zen-04-software-record"))
        edited = dict(payload, revision=payload["revision"] + 1)
        assert ZenodoAdapter.source_key_for(edited) != ZenodoAdapter.source_key_for(payload)

    def test_it_ignores_the_fields_that_churn(self) -> None:
        """Downloads, views and ``updated`` must not make every run a change event."""
        payload = raw_payload(fixture_by_id("zen-04-software-record"))
        noisy = dict(payload, stats={"downloads": 99999, "views": 99999},
                     updated="2099-01-01T00:00:00+00:00")
        assert ZenodoAdapter.source_key_for(noisy) == ZenodoAdapter.source_key_for(payload)

    def test_it_falls_back_to_a_payload_hash(self) -> None:
        assert ZenodoAdapter.source_key_for({"metadata": {"title": "x"}})
        assert (
            ZenodoAdapter.source_key_for({"metadata": {"title": "x"}})
            != ZenodoAdapter.source_key_for({"metadata": {"title": "y"}})
        )

    def test_the_declared_semantics_match_the_implementation(self) -> None:
        declared = config.load_sources()["zenodo"]["source_key"]
        assert "revision" in declared and "version DOI" in declared
        assert ZenodoAdapter.source_key_semantics


# ---------------------------------------------------------------------------
# zen-02 / zen-03 — the concept DOI is the identity
# ---------------------------------------------------------------------------


class TestConceptVersusVersion:
    """zen-02: the most important Zenodo case."""

    def test_the_identity_is_the_concept_doi_never_the_version_doi(self) -> None:
        fixture = fixture_by_id("zen-02-concept-vs-version")
        payload = raw_payload(fixture)
        mapped = configured_adapter().map(observation(fixture))
        assert mapped.identity_key == payload["conceptdoi"] == "10.5281/zenodo.4549875"
        assert mapped.identity_key != payload["doi"]
        assert mapped.source.doi == payload["conceptdoi"]

    def test_the_version_doi_survives_where_it_belongs(self) -> None:
        fixture = fixture_by_id("zen-02-concept-vs-version")
        payload = raw_payload(fixture)
        source = configured_adapter().map(observation(fixture)).source
        assert source.extra["zenodo_version_doi"] == payload["doi"]
        assert {
            "relation": "IsVersionOf",
            "identifier": payload["conceptdoi"],
            "identifier_type": "DOI",
        } in source.related_identifiers

    def test_thirteen_versions_are_one_record_not_thirteen(self) -> None:
        """The two fixtures are different versions of one concept."""
        latest = configured_adapter().map(observation(fixture_by_id("zen-02-concept-vs-version")))
        earlier = configured_adapter().map(
            observation(fixture_by_id("zen-03-version-metadata-drift"))
        )
        assert latest.identity_key == earlier.identity_key
        assert latest.source_id != earlier.source_id
        assert latest.source_key != earlier.source_key
        assert slug_for_identity(latest.identity_key) == slug_for_identity(earlier.identity_key)

    def test_the_latest_versions_metadata_wins_and_first_seen_is_kept(self) -> None:
        """zen-03: v2.2 has a different title and fewer creators than v3.2."""
        adapter = configured_adapter()
        earlier = adapter.map(observation(fixture_by_id("zen-03-version-metadata-drift")))
        latest = adapter.map(observation(fixture_by_id("zen-02-concept-vs-version")))
        assert earlier.source.title != latest.source.title
        assert len(earlier.source.authors) < len(latest.source.authors)

        events = [
            Event(observed_at="2021-05-01T00:00:00Z", event_type="scraped",
                  identity_key=earlier.identity_key, source_key=earlier.source_key,
                  source_system="zenodo", source_id=earlier.source_id,
                  source=earlier.source.model_dump(mode="json", exclude_none=True)),
            Event(observed_at="2026-08-31T00:00:00Z", event_type="scraped",
                  identity_key=latest.identity_key, source_key=latest.source_key,
                  source_system="zenodo", source_id=latest.source_id,
                  source=latest.source.model_dump(mode="json", exclude_none=True)),
        ]
        resolved = resolve(latest.identity_key, events=events)
        assert resolved.effective["title"] == latest.source.title
        assert resolved.effective["version"] == latest.source.version
        assert resolved.first_seen == "2021-05-01T00:00:00Z"
        assert resolved.source_id == latest.source_id


# ---------------------------------------------------------------------------
# zen-04 — software, and the join to GitHub
# ---------------------------------------------------------------------------


class TestSoftwareRecord:
    def test_resource_type_software_becomes_resource_kind_software(self) -> None:
        source = configured_adapter().map(observation(fixture_by_id("zen-04-software-record"))).source
        assert source.resource_kind == "software"

    def test_the_github_repository_is_carried_as_the_join_key(self) -> None:
        """For the dedup track: merge with the GitHub record, keep both source URLs."""
        source = configured_adapter().map(observation(fixture_by_id("zen-04-software-record"))).source
        repo = "https://github.com/IEA-Task-43/digital_wra_data_standard"
        assert source.extra["zenodo_code_repository"] == repo
        assert any(
            entry["identifier"].startswith(repo) and entry["relation"] == "isSupplementTo"
            for entry in source.related_identifiers
        )
        assert source.source_urls == ["https://zenodo.org/records/15677886"]

    def test_a_record_with_no_code_repository_gets_no_such_key(self) -> None:
        source = configured_adapter().map(observation(fixture_by_id("zen-05-restricted-access"))).source
        assert "zenodo_code_repository" not in source.extra


# ---------------------------------------------------------------------------
# zen-05 / zen-06 — availability honesty
# ---------------------------------------------------------------------------


class TestAccessStatus:
    def test_restricted_is_recorded_and_nothing_is_offered_for_download(self) -> None:
        source = configured_adapter().map(observation(fixture_by_id("zen-05-restricted-access"))).source
        assert source.access_status == "restricted"
        assert source.resources == []
        assert source.title and source.doi  # the metadata is still catalogued

    def test_embargoed_carries_its_date(self) -> None:
        source = configured_adapter().map(observation(fixture_by_id("zen-06-embargoed"))).source
        assert source.access_status == "embargoed"
        assert source.embargo_date == "2027-03-01"
        assert source.resources == []

    @pytest.mark.parametrize(
        "access_right,expected",
        [("open", "open"), ("restricted", "restricted"), ("embargoed", "embargoed"),
         ("closed", "metadata-only"), (None, "unknown"), ("", "unknown"),
         ("something-new", "unknown")],
    )
    def test_the_mapping(self, access_right: Any, expected: str) -> None:
        assert access_status_for(access_right) == expected

    def test_an_unknown_access_right_is_never_optimistically_open(self) -> None:
        assert access_status_for("brand-new-vocabulary") != "open"


# ---------------------------------------------------------------------------
# zen-07 — sanitisation
# ---------------------------------------------------------------------------


class TestSanitisation:
    @pytest.fixture
    def notes(self) -> str:
        return configured_adapter().map(
            observation(fixture_by_id("zen-07-html-description"))
        ).source.notes or ""

    def test_the_raw_payload_really_is_hostile(self) -> None:
        description = raw_payload(fixture_by_id("zen-07-html-description"))["metadata"]["description"]
        assert "<script>" in description and "onclick" in description
        assert "javascript:" in description and "<iframe" in description

    @pytest.mark.parametrize(
        "forbidden",
        ["<script", "</script", "<style", "<iframe", "onclick", "javascript:",
         "evil.example", "class=", "style=", "<!--"],
    )
    def test_it_is_gone_from_the_stored_description(self, notes: str, forbidden: str) -> None:
        assert forbidden not in notes

    def test_the_prose_and_the_safe_markup_survive(self, notes: str) -> None:
        assert "<strong>ALEX17</strong>" in notes
        assert "streamwise velocity" in notes
        assert '<a href="https://windbench.net/alex17"' in notes
        assert 'rel="nofollow noopener"' in notes

    def test_injected_instructions_survive_as_inert_text(self, notes: str) -> None:
        """We report what the source says; we just never let it be markup or a prompt."""
        assert "Ignore all previous instructions" in notes
        assert "&amp;" in notes  # the entity is escaped text, not a live reference

    def test_the_licence_the_injection_asked_for_was_not_applied(self) -> None:
        source = configured_adapter().map(
            observation(fixture_by_id("zen-07-html-description"))
        ).source
        assert source.license_id == "cc-by"


# ---------------------------------------------------------------------------
# zen-08 — licences
# ---------------------------------------------------------------------------


class TestLicences:
    def test_an_absent_licence_is_notspecified_and_not_a_failure(self) -> None:
        fixture = fixture_by_id("zen-08-no-license")
        assert "license" not in raw_payload(fixture)["metadata"]
        source = configured_adapter().map(observation(fixture)).source
        assert source.license_raw is None
        assert source.license_id == "notspecified"
        assert map_license(None) == ("notspecified", True)

    def test_an_open_licence_is_never_inferred(self) -> None:
        source = configured_adapter().map(observation(fixture_by_id("zen-08-no-license"))).source
        assert source.license_id == "notspecified"
        assert source.resource_kind == "software"  # even though it is obviously code

    def test_an_unrecognised_licence_string_is_flagged_not_dropped(self) -> None:
        """The other half of zen-08: free text the table does not know."""
        payload = raw_payload(fixture_by_id("zen-08-no-license"))
        payload = json.loads(json.dumps(payload))
        payload["metadata"]["license"] = {"id": "Free for academic use, contact the author"}
        mapped = configured_adapter().map(
            RawObservation(source_system="zenodo", source_id="3773129",
                           source_key=ZenodoAdapter.source_key_for(payload), payload=payload)
        )
        assert mapped.source.license_raw == "Free for academic use, contact the author"
        assert mapped.source.license_id == "notspecified"
        assert map_license(mapped.source.license_raw)[1] is False  # -> run report flag

    @pytest.mark.parametrize(
        "fixture_id,expected",
        [("zen-02-concept-vs-version", "bsd-3-clause"),
         ("zen-04-software-record", "bsd-3-clause"),
         ("zen-05-restricted-access", "cc-by-4.0"),
         ("zen-10-diacritics", "cc-by-4.0")],
    )
    def test_license_raw_is_exactly_what_zenodo_said(self, fixture_id: str, expected: str) -> None:
        assert configured_adapter().map(observation(fixture_by_id(fixture_id))).source.license_raw == expected

    def test_every_mapped_licence_is_in_the_ckan_register(self) -> None:
        from harvest.licenses import LICENSE_IDS

        for fixture in ALL:
            source = configured_adapter().map(observation(fixture)).source
            assert source.license_id in LICENSE_IDS, fixture["fixture_id"]


# ---------------------------------------------------------------------------
# zen-09 / zen-10 — creators and encoding
# ---------------------------------------------------------------------------


class TestCreatorsAndEncoding:
    def test_every_creator_is_kept(self) -> None:
        fixture = fixture_by_id("zen-09-many-creators")
        creators = raw_payload(fixture)["metadata"]["creators"]
        source = configured_adapter().map(observation(fixture)).source
        assert len(source.authors) == len(creators) == 26
        assert [a.name for a in source.authors] == [c["name"] for c in creators]

    def test_a_creator_with_no_affiliation_does_not_crash_or_gain_one(self) -> None:
        source = configured_adapter().map(observation(fixture_by_id("zen-04-software-record"))).source
        assert any(author.affiliation is None for author in source.authors)
        assert all(author.name for author in source.authors)

    def test_diacritics_are_preserved_in_display(self) -> None:
        source = configured_adapter().map(observation(fixture_by_id("zen-10-diacritics"))).source
        names = [author.name for author in source.authors]
        assert "Cattin, René" in names
        assert "Klintström, Rebecka" in names
        assert "Lehtomäki, Ville" in names
        assert "Ronsten, Göran" in names

    @pytest.mark.parametrize(
        "name,expected",
        [("Klintström, Rebecka", "klintstrom-rebecka"),
         ("Ronsten, Göran", "ronsten-goran"),
         ("Søren Ø. Müller", "soren-o-muller"),
         ("Ægir & Þór", "aegir-and-thor")],
    )
    def test_diacritics_are_transliterated_in_slugs(self, name: str, expected: str) -> None:
        assert slugify(name) == expected

    def test_the_records_own_slug_comes_from_the_identity_not_the_title(self) -> None:
        fixture = fixture_by_id("zen-10-diacritics")
        assert slug_for_identity(fixture["identity_key"]) == "doi-10-5281-zenodo-14179845"


# ---------------------------------------------------------------------------
# zen-11 — communities and tasks
# ---------------------------------------------------------------------------


class TestCommunitiesAndTasks:
    def test_two_communities_give_one_record_with_two_tasks(self) -> None:
        fixture = fixture_by_id("zen-11-multi-community")
        communities = raw_payload(fixture)["metadata"]["communities"]
        assert [c["id"] for c in communities] == ["ieawindtask32", "iea_wind_task_43"]
        source = configured_adapter().map(observation(fixture)).source
        assert source.iea_task == ["task-32", "task-43"]

    def test_a_double_harvest_writes_exactly_one_event(self, events_dir: Path) -> None:
        """The record is in two configured communities; both listings return it."""
        payload = raw_payload(fixture_by_id("zen-11-multi-community"))
        client = FakeClient(pages={
            "communities=ieawindtask32": listing(payload),
            "communities=iea_wind_task_43": listing(payload),
        })
        result = run_adapter(configured_adapter(client), max_records=5, events_dir=events_dir)
        assert (result.seen, result.changed) == (1, 1)
        assert len(read_events("10.5281/zenodo.4562391", events_dir)) == 1

    @pytest.mark.parametrize(
        "slug,expected",
        [("iea_wind_task_43", ["task-43"]),
         ("ieawindtask32", ["task-32"]),
         ("ieawindtask52", ["task-52"]),
         ("ieawindtask51_austria", ["task-51"]),
         ("ieawindtask56-oc7-wp22", ["task-56"]),
         ("wakebench", []),
         ("openaccessost", []),
         ("", [])],
    )
    def test_the_slug_pattern(self, slug: str, expected: list[str]) -> None:
        assert tasks_for_community(slug) == expected

    def test_sources_yaml_covers_the_slugs_the_pattern_cannot_read(self) -> None:
        declared = configured_adapter().community_tasks()
        for slug in ("wakebench", "jam", "coldclimatewind", "lidar_ontology"):
            assert tasks_for_community(slug, declared), slug

    def test_every_task_the_adapter_can_emit_exists_in_groups_yaml(self) -> None:
        """A task that is not a group would fail the CKAN gate at materialize time."""
        adapter = configured_adapter()
        declared = adapter.community_tasks()
        groups = config.group_names()
        for entry in adapter.communities():
            for task in tasks_for_community(str(entry["slug"]), declared):
                assert config.canonical_group(task) in groups, entry["slug"]

    def test_an_unknown_community_contributes_no_group(self) -> None:
        """scrape-02: the community slug is stranger-controlled, and it is a build input.

        Anyone can create a Zenodo community called ``ieawindtask777``; IEA
        Wind will one day create a real ``ieawindtask66``. Either way the
        pattern turned it into ``task-777``, which is not in ``groups.yaml``,
        which fails the CKAN gate. And because ``events/`` is append-only the
        bad attribution is now permanent: every subsequent run fails the same
        way and the deploy stays blocked until a human edits the register. A
        stranger's community name must not be able to do that.
        """
        assert tasks_for_community("ieawindtask777") == []
        assert tasks_for_community("ieawindtask66") == []

    def test_a_declared_task_is_checked_against_the_register_too(self) -> None:
        """Not just the pattern path: a typo in sources.yaml is caught as well."""
        assert tasks_for_community("mytest", {"mytest": ["task-888"]}) == []
        assert tasks_for_community("mytest", {"mytest": ["task-43"]}) == ["task-43"]

    def test_a_renumbered_task_still_lands_on_its_real_group(self) -> None:
        """Filtering must resolve aliases, not just compare strings."""
        assert tasks_for_community("coldclimatewind", {"coldclimatewind": ["task-19"]}) == [
            config.canonical_group("task-19")
        ]

    def test_the_spelling_is_normalised_before_it_is_checked(self) -> None:
        """eventlog-05: `Task 43`, `TASK-43` and `task-43` are one group."""
        assert tasks_for_community("x", {"x": [" Task 43 "]}) == ["task-43"]
        assert tasks_for_community("x", {"x": ["TASK_43", "task-43"]}) == ["task-43"]

    def test_non_iea_communities_contribute_no_task(self) -> None:
        """zen-04's record is also in `openaccessost` and `wedowind` — not tasks."""
        fixture = fixture_by_id("zen-02-concept-vs-version")
        assert [c["id"] for c in raw_payload(fixture)["metadata"]["communities"]] == [
            "iea_wind_task_43", "openoa"
        ]
        assert configured_adapter().map(observation(fixture)).source.iea_task == ["task-43"]


# ---------------------------------------------------------------------------
# zen-12 — tombstones
# ---------------------------------------------------------------------------


class TestTombstone:
    @property
    def tombstone(self) -> dict[str, Any]:
        return load(FIXTURES / "raw" / "zen-12-tombstone.json")

    def test_a_real_zenodo_410_body_is_recognised(self) -> None:
        payload = self.tombstone
        assert payload["status"] == 410 and payload["tombstone"]
        assert ZenodoAdapter.is_tombstone(payload) is True

    def test_a_live_record_is_not_a_tombstone(self) -> None:
        for fixture in ALL:
            assert ZenodoAdapter.is_tombstone(raw_payload(fixture)) is False, fixture["fixture_id"]

    def test_the_tombstone_carries_no_metadata_at_all(self) -> None:
        """Which is exactly why it must never be scraped."""
        assert "metadata" not in self.tombstone
        assert "doi" not in self.tombstone

    def test_a_tombstone_in_a_listing_is_skipped_rather_than_scraped(
        self, events_dir: Path
    ) -> None:
        good = raw_payload(fixture_by_id("zen-05-restricted-access"))
        dead = dict(self.tombstone, id=5890532)
        client = FakeClient(pages={"communities=": listing(dead, good)})
        result = run_adapter(configured_adapter(client), max_records=5, events_dir=events_dir)
        assert result.seen == 1
        assert result.identity_keys == ["10.5281/zenodo.18967947"]

    def test_recheck_appends_a_withdrawal_and_the_record_survives(
        self, events_dir: Path
    ) -> None:
        fixture = fixture_by_id("zen-01-canonical")
        identity = fixture["identity_key"]
        from harvest.events import record_scrape

        record_scrape(
            identity_key=identity, source_system="zenodo", source_id=fixture["source_id"],
            source_key=fixture["source_key"], source=fixture["source"],
            provenance={k: FieldProvenance(**v) for k, v in fixture["provenance"].items()},
            events_dir=events_dir, observed_at="2026-08-24T03:11:07Z",
        )
        client = FakeClient(pages={"/records/1234567": self.tombstone},
                            statuses={"/records/1234567": 410})
        adapter = configured_adapter(client)

        assert adapter.recheck_withdrawn(identity, "1234567", events_dir) is True
        events = read_events(identity, events_dir)
        assert [event.event_type for event in events] == ["scraped", "withdrawn"]
        assert "spam" in (events[-1].note or "")

        package = replay(identity, events_dir=events_dir)
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert extras["lifecycle_state"] == "withdrawn"
        assert extras["withdrawn"] == "true" and extras["withdrawn_at"]
        assert package["state"] == "active"                       # ADR-0027, not `deleted`
        assert package["title"] == fixture["source"]["title"]     # metadata retained
        assert package["url"] == fixture["source"]["url"]         # the URL survives
        assert validate_package(package, config.organization_names(), config.group_names()) == []

    def test_withdrawal_is_append_on_change_too(self, events_dir: Path) -> None:
        identity = "10.5072/zenodo.1234566"
        client = FakeClient(pages={"/records/1234567": self.tombstone},
                            statuses={"/records/1234567": 410})
        adapter = configured_adapter(client)
        assert adapter.recheck_withdrawn(identity, "1234567", events_dir) is True
        assert adapter.recheck_withdrawn(identity, "1234567", events_dir) is False
        assert len(read_events(identity, events_dir)) == 1

    def test_a_healthy_record_is_never_withdrawn(self, events_dir: Path) -> None:
        client = FakeClient(pages={"/records/1234567": {"id": 1234567, "revision": 3}})
        adapter = configured_adapter(client)
        assert adapter.recheck_withdrawn("10.5072/zenodo.1234566", "1234567", events_dir) is False
        assert read_events("10.5072/zenodo.1234566", events_dir) == []

    def test_an_unreachable_api_is_never_read_as_withdrawal(self, events_dir: Path) -> None:
        """Runbook: 'a source being unreachable is never withdrawal'."""
        client = FakeClient(failures={"/records/": "connection reset"})
        adapter = configured_adapter(client)
        assert adapter.recheck_withdrawn("10.5072/zenodo.1234566", "1234567", events_dir) is False
        assert read_events("10.5072/zenodo.1234566", events_dir) == []

    def test_the_expected_record_matches_the_fixture(self, events_dir: Path) -> None:
        fixture = load(FIXTURES / "zen-12-tombstone.json")
        source_fixture = fixture_by_id("zen-01-canonical")
        identity = fixture["identity_key"]
        from harvest.events import record_scrape, withdraw

        record_scrape(
            identity_key=identity, source_system="zenodo",
            source_id=source_fixture["source_id"], source_key=source_fixture["source_key"],
            source=source_fixture["source"],
            provenance={k: FieldProvenance(**v) for k, v in source_fixture["provenance"].items()},
            events_dir=events_dir, observed_at="2026-08-24T03:11:07Z",
        )
        withdraw(identity, source_system="zenodo",
                 note=ZenodoAdapter.tombstone_note(self.tombstone),
                 events_dir=events_dir, observed_at="2026-08-31T20:45:06Z")
        assert replay(identity, events_dir=events_dir) == fixture["record"]


# ---------------------------------------------------------------------------
# harvest(): the network side, through a fake client
# ---------------------------------------------------------------------------


class TestHarvest:
    def test_it_yields_verbatim_payloads(self, events_dir: Path) -> None:
        payload = raw_payload(fixture_by_id("zen-05-restricted-access"))
        adapter = configured_adapter(FakeClient(pages={"communities=": listing(payload)}))
        observations = list(adapter.harvest(max_records=5))
        assert len(observations) == 1
        assert observations[0].payload == payload          # verbatim, no cleaning
        assert observations[0].source_id == "21037963"
        assert observations[0].url == "https://zenodo.org/records/21037963"

    def test_the_five_record_cap_is_honoured(self) -> None:
        payloads = []
        for index in range(12):
            payload = json.loads(json.dumps(raw_payload(fixture_by_id("zen-05-restricted-access"))))
            payload["id"] = 90000000 + index
            payload["doi"] = f"10.5281/zenodo.{90000000 + index}"
            payload["conceptdoi"] = f"10.5281/zenodo.{80000000 + index}"
            payloads.append(payload)
        adapter = configured_adapter(FakeClient(pages={"communities=": listing(*payloads)}))
        assert len(list(adapter.harvest(max_records=5))) == 5

    def test_it_asks_for_the_latest_version_of_each_concept_only(self) -> None:
        adapter = configured_adapter(FakeClient(pages={"communities=": listing()}))
        list(adapter.harvest(max_records=5))
        assert adapter.client.calls
        for url in adapter.client.calls:
            assert url.startswith(ZENODO_API)
            assert "all_versions=false" in url
            assert "size=5" in url

    def test_it_stops_calling_once_the_limit_is_reached(self) -> None:
        payload = raw_payload(fixture_by_id("zen-05-restricted-access"))
        client = FakeClient(pages={"communities=": listing(payload)})
        adapter = configured_adapter(client)
        list(adapter.harvest(max_records=1))
        assert len(client.calls) == 1        # not all nine communities

    def test_a_total_failure_is_reported_as_unreachable_not_raised(
        self, events_dir: Path
    ) -> None:
        adapter = configured_adapter(FakeClient(failures={"zenodo.org": "DNS failure"}))
        with pytest.raises(SourceUnreachable, match="DNS failure"):
            list(adapter.harvest(max_records=5))
        result = run_adapter(configured_adapter(
            FakeClient(failures={"zenodo.org": "DNS failure"})), max_records=5, events_dir=events_dir)
        assert result.reachable is False and result.seen == 0

    def test_one_dead_community_does_not_cost_the_others(self, events_dir: Path) -> None:
        payload = raw_payload(fixture_by_id("zen-05-restricted-access"))
        client = FakeClient(
            pages={"communities=ieawindtask32": listing(payload)},
            failures={"communities=iea_wind_task_43": "HTTP 503"},
        )
        result = run_adapter(configured_adapter(client), max_records=5, events_dir=events_dir)
        assert result.reachable is True and result.changed == 1

    def test_a_garbled_body_does_not_crash_the_run(self, events_dir: Path) -> None:
        client = FakeClient(pages={"communities=": {"unexpected": "shape"}})
        result = run_adapter(configured_adapter(client), max_records=5, events_dir=events_dir)
        assert result.reachable is False and result.seen == 0

    def test_a_304_is_not_an_error(self, events_dir: Path) -> None:
        client = FakeClient(statuses={"communities=": 304})
        result = run_adapter(configured_adapter(client), max_records=5, events_dir=events_dir)
        assert result.reachable is True and result.seen == 0

    def test_it_never_touches_a_files_endpoint(self) -> None:
        """Metadata and links only — the catalogue never mirrors a file."""
        payload = raw_payload(fixture_by_id("zen-02-concept-vs-version"))
        client = FakeClient(pages={"communities=": listing(payload)})
        list(configured_adapter(client).harvest(max_records=5))
        assert not any("/files" in url or "/versions" in url for url in client.calls)

    def test_it_closes_only_the_client_it_opened(self) -> None:
        client = FakeClient(pages={"communities=": listing()})
        adapter = configured_adapter(client)
        list(adapter.harvest(max_records=5))
        adapter.close()
        assert client.closed is False        # injected clients belong to the caller


class TestChangeDetectionEndToEnd:
    def _client(self, payload: dict[str, Any]) -> FakeClient:
        return FakeClient(pages={"communities=": listing(payload)})

    def test_a_second_run_with_unchanged_source_keys_writes_nothing(
        self, events_dir: Path
    ) -> None:
        payload = raw_payload(fixture_by_id("zen-02-concept-vs-version"))
        first = run_adapter(configured_adapter(self._client(payload)), max_records=5,
                            events_dir=events_dir)
        second = run_adapter(configured_adapter(self._client(payload)), max_records=5,
                             events_dir=events_dir)
        assert (first.seen, first.changed, first.skipped_unchanged) == (1, 1, 0)
        assert (second.seen, second.changed, second.skipped_unchanged) == (1, 0, 1)
        assert len(read_events("10.5281/zenodo.4549875", events_dir)) == 1

    def test_a_new_version_under_the_same_concept_appends(self, events_dir: Path) -> None:
        earlier = raw_payload(fixture_by_id("zen-03-version-metadata-drift"))
        latest = raw_payload(fixture_by_id("zen-02-concept-vs-version"))
        run_adapter(configured_adapter(self._client(earlier)), max_records=5, events_dir=events_dir)
        result = run_adapter(configured_adapter(self._client(latest)), max_records=5,
                             events_dir=events_dir)
        assert result.changed == 1
        events = read_events("10.5281/zenodo.4549875", events_dir)
        assert len(events) == 2
        assert events[-1].source["title"] == latest["metadata"]["title"]

    def test_a_no_op_run_leaves_the_event_file_byte_identical(self, events_dir: Path) -> None:
        payload = raw_payload(fixture_by_id("zen-02-concept-vs-version"))
        run_adapter(configured_adapter(self._client(payload)), max_records=5, events_dir=events_dir)
        path = events_dir / "doi-10-5281-zenodo-4549875.jsonl"
        before = path.read_bytes()
        run_adapter(configured_adapter(self._client(payload)), max_records=5, events_dir=events_dir)
        assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# Configuration and registration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_the_adapter_is_registered_under_its_module_name(self) -> None:
        from harvest.adapters.base import get_adapter

        assert get_adapter("zenodo") is ZenodoAdapter
        assert ZenodoAdapter.source_name == "zenodo" == ZenodoAdapter.__module__.rsplit(".", 1)[1]
        assert ZenodoAdapter.tier == 1

    def test_the_configured_communities_are_used_in_order(self) -> None:
        client = FakeClient(pages={"communities=": listing()})
        adapter = configured_adapter(client)
        list(adapter.harvest(max_records=5))
        declared = [entry["slug"] for entry in adapter.communities()]
        called = [url.split("communities=")[1].split("&")[0] for url in client.calls]
        assert called == declared

    def test_it_falls_back_to_verified_communities_with_no_config(self) -> None:
        client = FakeClient(pages={"communities=": listing()})
        adapter = ZenodoAdapter(client=client)
        list(adapter.harvest(max_records=5))
        assert any("iea_wind_task_43" in url for url in client.calls)

    def test_the_robots_opt_out_is_config_not_code(self) -> None:
        """zenodo.org/robots.txt disallows /api; see the adapter docstring."""
        zenodo = config.load_sources()["zenodo"]
        assert zenodo["respect_robots"] is False
        assert zenodo["min_request_interval_seconds"] >= 1.0

    def test_the_opt_out_must_be_explicit(self) -> None:
        """compliance-05: deleting the line must RESTORE robots, not disable it.

        The lookup was ``self.config.get("respect_robots", False)``, so an
        absent key — which is what you get by deleting the documented opt-out,
        or by adding a new source and not thinking about it — silently ignored
        robots.txt. The safe value has to be the default; opting out has to be
        something you wrote down.
        """
        from harvest.adapters.base import SourceConfig
        from harvest.adapters.zenodo import ZenodoAdapter

        absent = ZenodoAdapter(config=SourceConfig.from_mapping("zenodo", {}))
        assert absent._ensure_client()._respect_robots is True

        explicit = ZenodoAdapter(
            config=SourceConfig.from_mapping("zenodo", {"respect_robots": False})
        )
        assert explicit._ensure_client()._respect_robots is False

    @pytest.mark.parametrize(
        "resource_type,expected",
        [({"type": "dataset"}, "dataset"),
         ({"type": "software"}, "software"),
         ({"type": "publication"}, "publication"),
         ({"type": "publication", "subtype": "report"}, "report"),
         ({"type": "publication", "subtype": "deliverable"}, "report"),
         ({"type": "publication", "subtype": "conferencepaper"}, "publication"),
         ({"type": "presentation"}, "other"),
         ({"type": "poster"}, "other"),
         ({"type": "event"}, "other"),
         ({"type": "something-new"}, "other"),
         ({}, None),
         (None, None)],
    )
    def test_the_resource_kind_mapping(self, resource_type: Any, expected: str | None) -> None:
        assert resource_kind_for(resource_type) == expected

    def test_every_mapped_kind_is_a_known_resource_kind(self) -> None:
        from harvest.models import RESOURCE_KINDS

        for fixture in ALL:
            kind = configured_adapter().map(observation(fixture)).source.resource_kind
            assert kind in RESOURCE_KINDS, fixture["fixture_id"]
