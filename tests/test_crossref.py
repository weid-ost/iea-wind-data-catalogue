"""The Crossref adapter — Track C.

Two layers, deliberately:

* **the fixtures** (``fixtures/crossref/``) are captured from the live API and
  lock the whole mapping down as a regression test;
* **the behavioural tests** below say, in prose and assertions, what each
  fixture in ``fixtures/fixtures-catalogue.md`` is *for*, so that a change that
  silently regenerates a fixture still has to answer to the specification.

Nothing here touches the network. ``map()`` is pure by contract, and every
``harvest()`` test injects a mock transport.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import httpx
import pytest

from harvest import config
from harvest.adapters.base import SourceConfig, SourceUnreachable, get_adapter, run_adapter
from harvest.adapters.crossref import (
    RESOURCE_KINDS_BY_TYPE,
    SELECT_FIELDS,
    CrossrefAdapter,
    published_version_doi,
    source_key_for,
)
from harvest.events import read_events
from harvest.http import HarvestClient
from harvest.identity import slug_for_identity
from harvest.materialize import materialize_all
from harvest.models import RawObservation

FIXTURES = config.fixtures_dir() / "crossref"


# ---------------------------------------------------------------------------
# Fixture plumbing
# ---------------------------------------------------------------------------


def load_fixtures() -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("cr-*.json"))
    ]


def payload_of(fixture_id: str) -> dict:
    return json.loads((FIXTURES / "raw" / f"{fixture_id}.json").read_text(encoding="utf-8"))


def observation(fixture_id: str, **overrides: Any) -> RawObservation:
    """A ``RawObservation`` exactly as ``harvest()`` would have yielded it."""
    payload = overrides.pop("payload", None) or payload_of(fixture_id)
    fields = {
        "source_system": "crossref",
        "source_id": str(payload.get("DOI", "")),
        "source_key": source_key_for(payload),
        "url": payload.get("URL"),
        "payload": payload,
        "fetched_at": "2026-08-31T00:00:00Z",
    }
    fields.update(overrides)
    return RawObservation(**fields)


def mapped(fixture_id: str, **overrides: Any):
    return CrossrefAdapter().map(observation(fixture_id, **overrides))


ALL_FIXTURES = load_fixtures()


# ---------------------------------------------------------------------------
# The fixtures themselves
# ---------------------------------------------------------------------------


class TestTheFixtureSet:
    def test_every_catalogued_fixture_exists(self) -> None:
        """`fixtures-catalogue.md` rows cr-01..cr-07 are not a menu."""
        present = {fixture["fixture_id"].split("-")[1] for fixture in ALL_FIXTURES}
        assert {"01", "02", "03", "04", "05", "06", "07"} <= present

    def test_every_fixture_has_its_verbatim_payload(self) -> None:
        for fixture in ALL_FIXTURES:
            assert (FIXTURES / fixture["raw"]).exists(), fixture["fixture_id"]


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f["fixture_id"])
class TestMapAgainstFixtures:
    def test_identity_key(self, fixture: dict) -> None:
        assert mapped(fixture["fixture_id"]).identity_key == fixture["identity_key"]

    def test_source_namespace(self, fixture: dict) -> None:
        result = mapped(fixture["fixture_id"])
        assert result.source.model_dump(mode="json", exclude_none=True) == fixture["source"]

    def test_provenance(self, fixture: dict) -> None:
        result = mapped(fixture["fixture_id"])
        encoded = {
            key: value.model_dump(mode="json", exclude_none=True)
            for key, value in result.provenance.items()
        }
        assert encoded == fixture["provenance"]

    def test_source_key_and_id(self, fixture: dict) -> None:
        assert source_key_for(payload_of(fixture["fixture_id"])) == fixture["source_key"]
        assert mapped(fixture["fixture_id"]).source_id == fixture["source_id"]

    def test_slug(self, fixture: dict) -> None:
        assert slug_for_identity(fixture["identity_key"]) == fixture["expected_slug"]

    def test_map_is_pure_enough_to_repeat(self, fixture: dict) -> None:
        """No clock, no network, no filesystem: two calls, one answer."""
        first = mapped(fixture["fixture_id"]).model_dump(mode="json")
        second = mapped(fixture["fixture_id"]).model_dump(mode="json")
        assert first == second

    def test_resource_kind_is_a_known_vocabulary_term(self, fixture: dict) -> None:
        from harvest.models import RESOURCE_KINDS

        assert fixture["source"]["resource_kind"] in RESOURCE_KINDS

    def test_the_catalogue_never_mirrors_a_file(self, fixture: dict) -> None:
        assert fixture["source"]["resources"] == []


# ---------------------------------------------------------------------------
# ADR-0026 — the source key is `deposited`, never `indexed`
# ---------------------------------------------------------------------------


class TestSourceKey:
    def test_the_key_is_the_deposited_date_time(self) -> None:
        payload = payload_of("cr-01-canonical")
        assert source_key_for(payload) == payload["deposited"]["date-time"]

    def test_the_key_is_not_the_indexed_date_time(self) -> None:
        payload = payload_of("cr-01-canonical")
        assert source_key_for(payload) != payload["indexed"]["date-time"]

    def test_a_moving_indexed_timestamp_changes_nothing(self) -> None:
        """The whole point of ADR-0026: `indexed` churns, so we ignore it.

        Crossref re-indexes on its own schedule. If the key moved with it,
        every weekly run would append an event for every record and
        append-on-change would mean append-always.
        """
        before = payload_of("cr-01-canonical")
        after = json.loads(json.dumps(before))
        after["indexed"] = {
            "date-parts": [[2027, 1, 1]],
            "date-time": "2027-01-01T00:00:00Z",
            "timestamp": 1798761600000,
        }
        assert source_key_for(after) == source_key_for(before)

    def test_a_new_deposit_does_change_the_key(self) -> None:
        before = payload_of("cr-01-canonical")
        after = json.loads(json.dumps(before))
        after["deposited"]["date-time"] = "2026-02-02T00:00:00Z"
        assert source_key_for(after) != source_key_for(before)

    def test_the_fallback_hash_also_ignores_indexed_and_the_citation_counters(self) -> None:
        """A payload with no `deposited` at all falls back to a content hash.

        The exclusions matter as much as the hash: `indexed` and the citation
        counters move without a single metadata field changing.
        """
        before = payload_of("cr-01-canonical")
        before.pop("deposited")
        after = json.loads(json.dumps(before))
        after["indexed"] = {"date-time": "2027-01-01T00:00:00Z"}
        after["is-referenced-by-count"] = 99
        after["references-count"] = 1234
        assert source_key_for(after) == source_key_for(before)
        assert len(source_key_for(before)) == 16

    def test_the_fallback_hash_still_notices_real_metadata(self) -> None:
        before = payload_of("cr-01-canonical")
        before.pop("deposited")
        after = json.loads(json.dumps(before))
        after["title"] = ["A corrected title"]
        assert source_key_for(after) != source_key_for(before)

    def test_an_unchanged_key_writes_no_second_event(self, events_dir: Path) -> None:
        adapter = _adapter_over(["cr-01-canonical", "cr-03-proceedings"])
        first = run_adapter(adapter, limit=5, events_dir=events_dir)
        second = run_adapter(
            _adapter_over(["cr-01-canonical", "cr-03-proceedings"]),
            limit=5, events_dir=events_dir,
        )
        assert (first.changed, first.skipped_unchanged) == (2, 0)
        assert (second.changed, second.skipped_unchanged) == (0, 2)
        assert len(read_events("10.5194/wes-9-1173-2024", events_dir)) == 1


# ---------------------------------------------------------------------------
# cr-01 — the canonical mapping
# ---------------------------------------------------------------------------


class TestCr01Canonical:
    def test_a_journal_article_is_a_publication(self) -> None:
        assert mapped("cr-01-canonical").source.resource_kind == "publication"

    def test_every_crossref_type_maps_into_the_catalogue_vocabulary(self) -> None:
        from harvest.models import RESOURCE_KINDS

        assert set(RESOURCE_KINDS_BY_TYPE.values()) <= set(RESOURCE_KINDS)

    def test_an_unknown_type_falls_back_to_publication(self) -> None:
        """Crossref registers published literature; `other` would be a lie."""
        payload = payload_of("cr-01-canonical")
        payload["type"] = "some-type-crossref-invented-last-tuesday"
        assert mapped("cr-01-canonical", payload=payload).source.resource_kind == "publication"

    def test_the_identity_is_the_doi(self) -> None:
        result = mapped("cr-01-canonical")
        assert result.identity_key == "10.5194/wes-9-1173-2024"
        assert result.source.doi == result.identity_key

    def test_the_licence_maps_and_keeps_what_the_source_said(self) -> None:
        source = mapped("cr-01-canonical").source
        assert source.license_id == "cc-by"
        assert source.license_raw == "https://creativecommons.org/licenses/by/4.0/"

    def test_an_absent_licence_is_notspecified_and_never_guessed(self) -> None:
        source = mapped("cr-03b-proceedings-article").source
        assert "license" not in payload_of("cr-03b-proceedings-article")
        assert source.license_id == "notspecified"
        assert source.license_raw is None

    def test_the_version_of_record_licence_wins_over_a_text_mining_one(self) -> None:
        source = mapped("cr-02-partial-date").source
        versions = [entry["content-version"] for entry in payload_of("cr-02-partial-date")["license"]]
        assert versions == ["tdm", "tdm", "vor"]
        assert source.license_raw == "http://creativecommons.org/licenses/by-nc-nd/4.0/"

    def test_an_unmappable_licence_is_flagged_not_defaulted_to_open(self) -> None:
        from harvest.licenses import map_license

        raw = mapped("cr-05-markup-in-title").source.license_raw
        assert raw == "http://onlinelibrary.wiley.com/termsAndConditions#vor"
        assert map_license(raw) == ("notspecified", False)

    def test_crossrefs_duplicate_relation_assertions_are_deduplicated(self) -> None:
        """Crossref asserts each relation twice, by subject and by object."""
        raw = payload_of("cr-01-canonical")
        assert len(raw["relation"]["has-preprint"]) == 2
        relations = mapped("cr-01-canonical").source.related_identifiers
        preprints = [r for r in relations if r["relation"] == "HasPreprint"]
        assert preprints == [
            {"relation": "HasPreprint", "identifier": "10.5194/wes-2024-3",
             "identifier_type": "DOI"}
        ]

    def test_the_landing_page_and_the_publisher_page_are_both_kept(self) -> None:
        source = mapped("cr-01-canonical").source
        assert source.url == "https://doi.org/10.5194/wes-9-1173-2024"
        assert "https://wes.copernicus.org/articles/9/1173/2024/" in source.source_urls

    def test_the_jats_abstract_becomes_sanitised_html(self) -> None:
        notes = mapped("cr-01-canonical").source.notes or ""
        assert notes.startswith("<p>Abstract.")
        assert "jats" not in notes


# ---------------------------------------------------------------------------
# cr-02 — partial dates
# ---------------------------------------------------------------------------


class TestCr02PartialDates:
    def test_a_year_only_deposit_stays_year_precision(self) -> None:
        raw = payload_of("cr-02-partial-date")
        assert raw["issued"]["date-parts"] == [[2023]]
        assert mapped("cr-02-partial-date").source.published_date == "2023"

    def test_a_year_and_month_deposit_stays_month_precision(self) -> None:
        raw = payload_of("cr-05b-entity-in-title")
        assert raw["issued"]["date-parts"] == [[2016, 9]]
        assert mapped("cr-05b-entity-in-title").source.published_date == "2016-09"

    def test_a_full_date_is_a_full_date(self) -> None:
        assert mapped("cr-01-canonical").source.published_date == "2024-05-15"

    @pytest.mark.parametrize(
        "parts,expected",
        [
            ([[2023]], "2023"),
            ([[2016, 9]], "2016-09"),
            ([[2024, 5, 15]], "2024-05-15"),
            ([[2024, 12, 1]], "2024-12-01"),
            ([[]], None),
            ([[None]], None),
            ([], None),
        ],
    )
    def test_never_fabricates_a_month_or_a_day(self, parts, expected) -> None:
        from harvest.adapters.crossref import _date_from_parts

        assert _date_from_parts({"date-parts": parts}) == expected

    def test_a_work_with_no_authors_at_all_maps_cleanly(self) -> None:
        assert "author" not in payload_of("cr-02-partial-date")
        assert mapped("cr-02-partial-date").source.authors == []


# ---------------------------------------------------------------------------
# cr-03 — container semantics
# ---------------------------------------------------------------------------


class TestCr03Container:
    def test_the_series_is_the_container(self) -> None:
        source = mapped("cr-03-proceedings").source
        assert source.container == "Journal of Physics: Conference Series"

    def test_volume_and_issue_are_retained(self) -> None:
        """The record format has no first-class volume/issue, so they are
        retained verbatim in ``source.extra`` — the event log keeps them
        forever, and a scheming extension can promote them later."""
        extra = mapped("cr-03-proceedings").source.extra
        assert extra["crossref_volume"] == "1618"
        assert extra["crossref_issue"] == "4"
        assert extra["crossref_page"] == "042029"

    def test_a_true_proceedings_article_keeps_its_event(self) -> None:
        source = mapped("cr-03b-proceedings-article").source
        assert payload_of("cr-03b-proceedings-article")["type"] == "proceedings-article"
        assert source.container == "AIAA Scitech 2021 Forum"
        assert source.extra["crossref_event"]["location"] == "VIRTUAL EVENT"

    def test_the_event_name_is_the_container_when_there_is_no_container_title(self) -> None:
        payload = payload_of("cr-03b-proceedings-article")
        payload.pop("container-title")
        source = mapped("cr-03b-proceedings-article", payload=payload).source
        assert source.container == "AIAA Scitech 2021 Forum"

    def test_task_numbers_named_in_the_text_stay_candidates(self) -> None:
        """Crossref does not state task membership, and ``map()`` is pure, so
        it cannot check a number against ``groups.yaml``. Emitting an
        unregistered group would fail the CKAN gate for the whole run."""
        source = mapped("cr-03-proceedings").source
        assert source.iea_task == []
        assert source.extra["iea_task_candidates"] == ["task-32", "task-37"]


# ---------------------------------------------------------------------------
# cr-04 — the preprint pair
# ---------------------------------------------------------------------------


class TestCr04PreprintPair:
    published = "10.5194/wes-11-2345-2026"
    preprint = "10.22541/au.173542765.58148137/v1"

    def test_the_preprint_takes_the_published_identity(self) -> None:
        result = mapped("cr-04-preprint-pair")
        assert result.identity_key == self.published
        assert result.source.doi == self.published

    def test_and_so_it_is_the_same_record_as_the_published_version(self) -> None:
        assert mapped("cr-04b-published-version").identity_key == self.published

    def test_the_preprint_is_linked_not_lost(self) -> None:
        source = mapped("cr-04-preprint-pair").source
        assert source.extra["crossref_preprint_doi"] == self.preprint
        assert f"https://doi.org/{self.preprint}" in source.source_urls
        assert {"relation": "IsPreprintOf", "identifier": self.published,
                "identifier_type": "DOI"} in source.related_identifiers

    def test_the_published_version_is_the_preferred_landing_page(self) -> None:
        assert mapped("cr-04-preprint-pair").source.url == f"https://doi.org/{self.published}"

    def test_the_source_id_is_still_the_preprints_own_doi(self) -> None:
        assert mapped("cr-04-preprint-pair").source_id == self.preprint

    def test_has_preprint_is_not_is_preprint_of(self) -> None:
        """The published article carries the reverse relation. Reading it as
        `is-preprint-of` would give the published article the preprint's
        identity, which is backwards."""
        assert "has-preprint" in payload_of("cr-04b-published-version")["relation"]
        assert published_version_doi(payload_of("cr-04b-published-version")) is None

    def test_only_posted_content_can_be_a_preprint(self) -> None:
        payload = payload_of("cr-04-preprint-pair")
        payload["type"] = "journal-article"
        assert published_version_doi(payload) is None

    def test_a_malformed_target_doi_is_not_adopted(self) -> None:
        payload = payload_of("cr-04-preprint-pair")
        payload["relation"]["is-preprint-of"][0]["id"] = "not a doi at all"
        assert published_version_doi(payload) is None
        assert mapped("cr-04-preprint-pair", payload=payload).identity_key == self.preprint

    def test_harvest_drops_the_preprint_when_the_published_version_is_in_the_batch(self) -> None:
        adapter, _ = _mock_adapter(["cr-04-preprint-pair", "cr-04b-published-version"])
        yielded = [raw.source_id for raw in adapter.harvest(limit=5)]
        assert yielded == ["10.5194/wes-11-2345-2026"]

    def test_harvest_keeps_the_preprint_when_the_published_version_is_not(self) -> None:
        adapter, _ = _mock_adapter(["cr-04-preprint-pair"])
        yielded = [raw.source_id for raw in adapter.harvest(limit=5)]
        assert yielded == [self.preprint]

    def test_an_asserted_doi_that_does_not_resolve_is_dropped_and_logged(self) -> None:
        """No identifier asserted by a third party is accepted on trust.

        The ``is-preprint-of`` target is Crossref repeating what the preprint
        server said. It is resolved against DataCite and Crossref in
        ``harvest()`` — never in the pure ``map()`` — and an observation whose
        target does not resolve is dropped rather than allowed to invent an
        identity.
        """
        adapter, _ = _mock_adapter(["cr-04-preprint-pair"], resolvable=False)
        assert list(adapter.harvest(limit=5)) == []
        assert [drop["doi"] for drop in adapter.drop_log.as_notices()] == [self.published]
        assert adapter.drop_log.as_notices()[0]["reason"] == "did-not-resolve"


# ---------------------------------------------------------------------------
# cr-05 — markup, entities and escaping
# ---------------------------------------------------------------------------


class TestCr05Markup:
    def test_the_title_is_plain_text_with_the_markup_removed(self) -> None:
        raw = payload_of("cr-05-markup-in-title")["title"][0]
        assert "<i>" in raw and "\n" in raw
        title = mapped("cr-05-markup-in-title").source.title
        assert title == "An improved k ‐ ϵ model applied to a wind turbine wake in atmospheric turbulence"

    def test_the_deposit_is_kept_byte_for_byte(self) -> None:
        extra = mapped("cr-05-markup-in-title").source.extra
        assert extra["crossref_title_raw"] == payload_of("cr-05-markup-in-title")["title"][0]

    def test_the_marked_up_form_is_kept_sanitised_for_a_renderer(self) -> None:
        html = mapped("cr-05-markup-in-title").source.extra["crossref_title_html"]
        assert "<i>k</i>" in html
        assert "<script" not in html

    def test_an_entity_is_decoded_exactly_once(self) -> None:
        """`&amp;` becomes one ampersand. Decoding twice, or not at all, is the
        classic bug: the first shows `&#8220;`-style noise, the second shows
        `&amp;` to the reader."""
        assert "&amp;" in payload_of("cr-05b-entity-in-title")["title"][0]
        title = mapped("cr-05b-entity-in-title").source.title
        assert title == "Wind power forecasting: IEA Wind Task 36 & future research issues"
        assert "&amp;" not in title

    @pytest.mark.parametrize("fixture_id", [f["fixture_id"] for f in ALL_FIXTURES])
    def test_no_title_carries_raw_markup_into_a_record(self, fixture_id: str) -> None:
        title = mapped(fixture_id).source.title or ""
        assert "<" not in title and ">" not in title

    def test_the_title_survives_json_ld(self) -> None:
        """JSON-LD is JSON: the encoder escapes, and a round trip is lossless."""
        for fixture_id in ("cr-05-markup-in-title", "cr-05b-entity-in-title"):
            title = mapped(fixture_id).source.title
            assert json.loads(json.dumps({"name": title}))["name"] == title

    def test_the_title_survives_html_escaping(self) -> None:
        title = mapped("cr-05b-entity-in-title").source.title or ""
        assert escape(title) == "Wind power forecasting: IEA Wind Task 36 &amp; future research issues"

    def test_latex_and_unicode_are_left_alone(self) -> None:
        from harvest.adapters.crossref import _plain

        assert _plain(r"Estimating $\alpha$ &amp; <i>C</i><sub>T</sub>") == (
            r"Estimating $\alpha$ & CT"
        )

    def test_diacritics_are_preserved_in_display(self) -> None:
        names = [author.name for author in mapped("cr-05-markup-in-title").source.authors]
        assert "Sørensen, Niels N." in names


# ---------------------------------------------------------------------------
# cr-06 — authors
# ---------------------------------------------------------------------------


class TestCr06Authors:
    def test_an_organisation_author_has_only_a_name(self) -> None:
        authors = mapped("cr-06-collaboration-author").source.authors
        organisation = authors[0]
        assert organisation.name == (
            "National Renewable Energy Laboratory (NREL), Golden, CO (United States)"
        )
        assert organisation.given is None and organisation.family is None

    def test_people_and_organisations_coexist_in_one_list(self) -> None:
        authors = mapped("cr-06-collaboration-author").source.authors
        assert any(author.family for author in authors)
        assert any(author.family is None for author in authors)

    def test_a_missing_given_name_is_not_an_error(self) -> None:
        payload = payload_of("cr-01-canonical")
        payload["author"] = [{"family": "Boorsma", "sequence": "first"}]
        author = mapped("cr-01-canonical", payload=payload).source.authors[0]
        assert (author.name, author.given, author.family) == ("Boorsma", None, "Boorsma")

    def test_an_unnameable_contributor_is_skipped_rather_than_emitted_empty(self) -> None:
        payload = payload_of("cr-01-canonical")
        payload["author"] = [{"sequence": "first", "affiliation": []}, {"family": "Real"}]
        assert [a.name for a in mapped("cr-01-canonical", payload=payload).source.authors] == ["Real"]

    def test_orcids_are_normalised_to_the_bare_identifier(self) -> None:
        author = mapped("cr-01-canonical").source.authors[0]
        assert author.orcid == "0000-0003-1486-3643"

    def test_affiliations_are_joined_not_dropped(self) -> None:
        payload = payload_of("cr-01-canonical")
        payload["author"] = [
            {"family": "Two", "given": "Hats",
             "affiliation": [{"name": "DTU"}, {"name": "NREL"}]}
        ]
        assert mapped("cr-01-canonical", payload=payload).source.authors[0].affiliation == "DTU; NREL"

    def test_the_resource_kind_of_a_report_is_report(self) -> None:
        assert mapped("cr-06-collaboration-author").source.resource_kind == "report"


# ---------------------------------------------------------------------------
# cr-07 — retraction
# ---------------------------------------------------------------------------


class TestCr07Retraction:
    def test_a_retracted_work_is_flagged(self) -> None:
        assert mapped("cr-07-retraction").source.withdrawn is True

    def test_the_flag_carries_enough_to_render_it_prominently(self) -> None:
        retraction = mapped("cr-07-retraction").source.extra["crossref_retraction"]
        assert retraction["type"] == "retraction"
        assert retraction["label"] == "Retraction"
        assert retraction["retracted_date"] == "2018-07-11"

    def test_the_notice_is_kept_verbatim(self) -> None:
        extra = mapped("cr-07-retraction").source.extra
        assert extra["crossref_update_to"] == payload_of("cr-07-retraction")["update-to"]

    def test_the_retraction_is_provenanced_as_a_derived_flag(self) -> None:
        provenance = mapped("cr-07-retraction").provenance
        assert provenance["withdrawn"].extraction_method == "pattern"

    def test_a_retraction_notice_about_another_work_is_not_itself_retracted(self) -> None:
        """cr-07b, and the trap. This work *is* the notice. Flagging it as
        withdrawn would retract the announcement instead of the article."""
        source = mapped("cr-07b-retraction-notice").source
        assert source.withdrawn is False
        assert {"relation": "IsRetractionOf", "identifier": "10.1051/rees/2021056",
                "identifier_type": "DOI"} in source.related_identifiers

    def test_a_non_retraction_update_does_not_withdraw_anything(self) -> None:
        payload = payload_of("cr-07-retraction")
        payload["update-to"] = [{"DOI": payload["DOI"], "type": "erratum", "label": "Erratum"}]
        payload.pop("updated-by")
        assert mapped("cr-07-retraction", payload=payload).source.withdrawn is False

    def test_the_record_is_retained_with_a_withdrawn_lifecycle(self, repo: Path) -> None:
        """ADR-0027: kept, never deleted; CKAN `state` stays active and the
        withdrawal lives in the extras the site renders the banner from."""
        adapter = _adapter_over(["cr-07-retraction"])
        run_adapter(adapter, limit=5, events_dir=repo / "events")
        result = materialize_all(root=repo)
        assert result.violations == []
        record = json.loads(
            (repo / "records" / "doi-10-1002-we-2194.json").read_text(encoding="utf-8")
        )
        extras = {extra["key"]: extra["value"] for extra in record["extras"]}
        assert record["state"] == "active"
        assert extras["lifecycle_state"] == "withdrawn"
        assert extras["withdrawn"] == "true"


# ---------------------------------------------------------------------------
# harvest(): etiquette, the cap, and degradation
# ---------------------------------------------------------------------------


def _response_for(url: str, fixture_ids: list[str], resolvable: bool) -> httpx.Response:
    """A Crossref-shaped listing response, plus the two DOI resolvers."""
    if url.startswith("https://api.datacite.org/dois/"):
        return httpx.Response(404, json={})
    if url.startswith("https://api.crossref.org/works/"):
        return httpx.Response(200 if resolvable else 404, json={"message": {}})
    items = [payload_of(fixture_id) for fixture_id in fixture_ids]
    return httpx.Response(
        200,
        json={"status": "ok", "message-type": "work-list",
              "message": {"total-results": len(items), "items": items}},
    )


def _mock_adapter(
    fixture_ids: list[str],
    resolvable: bool = True,
    statuses: list[int] | None = None,
    queries: list[dict] | None = None,
) -> tuple[CrossrefAdapter, list[str]]:
    """A ``CrossrefAdapter`` wired to a mock transport. Records every URL."""
    seen: list[str] = []
    listing_statuses = list(statuses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        if url.startswith("https://api.crossref.org/works?"):
            status = listing_statuses.pop(0) if listing_statuses else 200
            if status != 200:
                return httpx.Response(status, json={"status": "error"})
        return _response_for(url, fixture_ids, resolvable)

    client = HarvestClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        respect_robots=False,
        min_interval=0.0,
    )
    options = {
        "api": "https://api.crossref.org/works",
        "mailto": "tom@octue.com",
        "queries": queries if queries is not None else [{"params": {"query.title": "IEA Wind Task"}}],
        "rows": 20,
    }
    adapter = CrossrefAdapter(config=SourceConfig(name="crossref", options=options), client=client)
    return adapter, seen


def _adapter_over(fixture_ids: list[str]) -> CrossrefAdapter:
    return _mock_adapter(fixture_ids)[0]


class TestHarvest:
    def test_the_five_record_cap_is_honoured(self) -> None:
        adapter = _adapter_over([f["fixture_id"] for f in ALL_FIXTURES])
        assert len(list(adapter.harvest(limit=5))) == 5

    def test_a_limit_of_zero_asks_the_api_for_nothing(self) -> None:
        adapter, seen = _mock_adapter(["cr-01-canonical"])
        assert list(adapter.harvest(limit=0)) == []
        assert seen == []

    def test_the_payload_is_verbatim(self) -> None:
        adapter = _adapter_over(["cr-01-canonical"])
        raw = list(adapter.harvest(limit=5))[0]
        assert raw.payload == payload_of("cr-01-canonical")

    def test_the_request_lands_in_the_polite_pool(self) -> None:
        adapter, seen = _mock_adapter(["cr-01-canonical"])
        list(adapter.harvest(limit=5))
        assert "mailto=tom%40octue.com" in seen[0]

    def test_the_request_selects_only_the_fields_we_map(self) -> None:
        """`select` keeps the `reference` array — tens of kilobytes of
        citations we never use — off the wire and out of the fixtures."""
        adapter, seen = _mock_adapter(["cr-01-canonical"])
        list(adapter.harvest(limit=5))
        assert "select=" in seen[0]
        assert "reference%2C" not in seen[0]
        assert "DOI" in SELECT_FIELDS and "reference" not in SELECT_FIELDS

    def test_it_asks_for_a_small_page_not_the_configured_maximum(self) -> None:
        adapter, seen = _mock_adapter(["cr-01-canonical"])
        list(adapter.harvest(limit=5))
        assert "rows=10" in seen[0]

    def test_it_does_not_sort_by_deposited(self) -> None:
        """`sort=deposited` ranks the whole corpus by deposit time and returns
        unrelated chemistry at a five-record cap. Change detection and result
        ordering are different questions."""
        adapter, seen = _mock_adapter(["cr-01-canonical"])
        list(adapter.harvest(limit=5))
        assert "sort=" not in seen[0]

    def test_every_configured_query_is_run_and_results_deduplicate(self) -> None:
        adapter, seen = _mock_adapter(
            ["cr-01-canonical"],
            queries=[{"params": {"query.title": "IEA Wind Task"}},
                     {"params": {"filter": "issn:2366-7451"}}],
        )
        assert len(list(adapter.harvest(limit=5))) == 1  # same DOI from both queries
        assert len([url for url in seen if url.startswith("https://api.crossref.org/works?")]) == 2

    def test_the_real_sources_yaml_queries_are_usable(self) -> None:
        cfg = SourceConfig.from_mapping("crossref", config.load_sources()["crossref"])
        adapter = CrossrefAdapter(config=cfg)
        urls = adapter._query_urls(rows=5)
        assert len(urls) == 2
        assert all(url.startswith("https://api.crossref.org/works?") for url in urls)
        assert all("mailto=tom%40octue.com" in url for url in urls)


class TestDegradation:
    def test_every_query_failing_makes_the_source_unreachable(self) -> None:
        adapter, _ = _mock_adapter(["cr-01-canonical"], statuses=[503])
        with pytest.raises(SourceUnreachable):
            list(adapter.harvest(limit=5))

    def test_one_query_failing_costs_one_query_not_the_run(self) -> None:
        adapter, _ = _mock_adapter(
            ["cr-01-canonical"],
            statuses=[503, 200],
            queries=[{"params": {"query.title": "a"}}, {"params": {"query.title": "b"}}],
        )
        assert len(list(adapter.harvest(limit=5))) == 1

    def test_an_unreachable_source_is_reported_never_raised(self, events_dir: Path) -> None:
        adapter, _ = _mock_adapter(["cr-01-canonical"], statuses=[500])
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.reachable is False and result.errors

    def test_an_upstream_schema_change_does_not_crash_the_run(self, events_dir: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"entries": []}})  # no `items`

        client = HarvestClient(
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            respect_robots=False, min_interval=0.0,
        )
        adapter = CrossrefAdapter(config=SourceConfig(name="crossref"), client=client)
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.reachable is False
        assert "unexpected response shape" in result.errors[0]

    def test_a_malformed_doi_in_the_listing_is_dropped_and_logged(self) -> None:
        payload = payload_of("cr-01-canonical")
        payload["DOI"] = "definitely-not-a-doi"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"message": {"items": [payload]}})

        adapter = CrossrefAdapter(
            config=SourceConfig(name="crossref"),
            client=HarvestClient(
                client=httpx.Client(transport=httpx.MockTransport(handler)),
                respect_robots=False, min_interval=0.0,
            ),
        )
        assert list(adapter.harvest(limit=5)) == []
        assert adapter.drop_log.as_notices()[0]["reason"] == "malformed"

    def test_close_does_not_close_a_client_it_did_not_open(self) -> None:
        adapter, _ = _mock_adapter(["cr-01-canonical"])
        list(adapter.harvest(limit=5))
        adapter.close()
        assert adapter.client is not None  # the injected client is the caller's

    def test_a_robots_refusal_is_a_clean_disable_not_a_crash(self) -> None:
        class Refusing:
            def allowed(self, url: str) -> bool:
                return False

        client = HarvestClient(
            client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
            robots=Refusing(), min_interval=0.0,
        )
        adapter = CrossrefAdapter(config=SourceConfig(name="crossref"), client=client)
        with pytest.raises(SourceUnreachable, match="robots"):
            list(adapter.harvest(limit=5))


# ---------------------------------------------------------------------------
# Registration, and the whole pipeline
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_the_adapter_is_registered_under_its_source_name(self) -> None:
        assert get_adapter("crossref") is CrossrefAdapter
        assert CrossrefAdapter.source_name == "crossref"
        assert CrossrefAdapter.__module__ == "harvest.adapters.crossref"

    def test_it_is_tier_one_and_never_touches_a_model(self) -> None:
        assert CrossrefAdapter.tier == 1

    def test_the_declared_source_key_semantics_match_sources_yaml(self) -> None:
        declared = config.load_sources()["crossref"]["source_key"]
        assert "deposited" in declared and "indexed" in declared
        assert "deposited" in CrossrefAdapter.source_key_semantics

    def test_sources_yaml_keeps_the_prototype_cap(self) -> None:
        cfg = SourceConfig.from_mapping("crossref", config.load_sources()["crossref"])
        assert cfg.max_records == 5 and cfg.precedence == 20 and cfg.tier == 1


class TestEndToEnd:
    def test_harvest_materialize_and_the_ckan_gate(self, repo: Path) -> None:
        adapter = _adapter_over([f["fixture_id"] for f in ALL_FIXTURES])
        result = run_adapter(adapter, limit=5, events_dir=repo / "events")
        assert result.changed == 5 and result.errors == []

        materialized = materialize_all(root=repo)
        assert materialized.violations == []
        assert len(materialized.written) == 5

    def test_materialisation_is_byte_stable(self, repo: Path) -> None:
        run_adapter(_adapter_over(["cr-01-canonical"]), limit=5, events_dir=repo / "events")
        materialize_all(root=repo)
        before = (repo / "records" / "doi-10-5194-wes-9-1173-2024.json").read_bytes()
        again = materialize_all(root=repo)
        after = (repo / "records" / "doi-10-5194-wes-9-1173-2024.json").read_bytes()
        assert before == after and again.written == []

    def test_the_record_is_a_postable_ckan_package(self, repo: Path) -> None:
        run_adapter(_adapter_over(["cr-01-canonical"]), limit=5, events_dir=repo / "events")
        materialize_all(root=repo)
        record = json.loads(
            (repo / "records" / "doi-10-5194-wes-9-1173-2024.json").read_text(encoding="utf-8")
        )
        assert record["name"] == "doi-10-5194-wes-9-1173-2024"
        assert record["license_id"] == "cc-by"
        assert all(isinstance(extra["value"], str) for extra in record["extras"])
        extras = {extra["key"]: extra["value"] for extra in record["extras"]}
        assert extras["source_system"] == "crossref"
        assert extras["source_key"] == "2025-01-22T13:21:59Z"
        assert extras["resource_kind"] == "publication"
        assert extras["container"] == "Wind Energy Science"

    def test_an_unmapped_licence_reaches_the_run_report(self, repo: Path) -> None:
        run_adapter(_adapter_over(["cr-05-markup-in-title"]), limit=5, events_dir=repo / "events")
        result = materialize_all(root=repo)
        assert [entry["license_raw"] for entry in result.unmapped_licenses] == [
            "http://onlinelibrary.wiley.com/termsAndConditions#vor"
        ]
