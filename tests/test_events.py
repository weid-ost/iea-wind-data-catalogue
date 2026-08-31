"""The event log and ADR-0038 resolution — fixtures x-01..x-04, x-09, zen-12.

These are the tests most likely to catch a real bug, because they encode the
one rule the whole catalogue turns on: **source metadata is never edited, only
annotated.**
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harvest.events import (
    annotate,
    append_event,
    event_path,
    has_changed,
    iter_identity_keys,
    last_source_key,
    raise_notice,
    read_events,
    record_scrape,
    replay,
    resolve,
    withdraw,
)
from harvest.models import Event, FieldProvenance

KEY = "10.5281/zenodo.1234"


def scrape(events_dir: Path, *, source_key: str, system: str = "zenodo", at: str, **source):  # noqa: ANN003
    return record_scrape(
        identity_key=KEY,
        source_system=system,
        source_id="1234",
        source_key=source_key,
        source=source,
        events_dir=events_dir,
        observed_at=at,
    )


class TestFileLayout:
    def test_event_file_is_named_by_the_slug(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="r1", at="2026-01-01T00:00:00Z", title="T")
        assert event_path(KEY, events_dir).name == "doi-10-5281-zenodo-1234.jsonl"

    def test_every_line_carries_the_full_identity_key(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="r1", at="2026-01-01T00:00:00Z", title="T")
        line = event_path(KEY, events_dir).read_text().strip()
        assert '"identity_key":"10.5281/zenodo.1234"' in line

    def test_append_only(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="r1", at="2026-01-01T00:00:00Z", title="One")
        scrape(events_dir, source_key="r2", at="2026-02-01T00:00:00Z", title="Two")
        assert len(read_events(KEY, events_dir)) == 2

    def test_events_are_returned_in_observation_order(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="r2", at="2026-02-01T00:00:00Z", title="Two")
        scrape(events_dir, source_key="r1", at="2026-01-01T00:00:00Z", title="One")
        assert [e.source_key for e in read_events(KEY, events_dir)] == ["r1", "r2"]

    def test_iter_identity_keys(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="r1", at="2026-01-01T00:00:00Z", title="One")
        record_scrape("osti|99", "osti", "99", "k", {"title": "Two"}, events_dir=events_dir)
        assert sorted(iter_identity_keys(events_dir)) == ["10.5281/zenodo.1234", "osti|99"]

    def test_mismatched_identity_key_is_refused(self, events_dir: Path) -> None:
        event = Event(event_type="scraped", identity_key="other")
        with pytest.raises(ValueError):
            append_event(KEY, event, events_dir)

    def test_a_malformed_line_is_a_clear_error(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="r1", at="2026-01-01T00:00:00Z", title="One")
        with event_path(KEY, events_dir).open("a") as handle:
            handle.write("{not json\n")
        with pytest.raises(ValueError, match="malformed event line"):
            read_events(KEY, events_dir)


class TestChangeDetection:
    """ADR-0026: an unchanged source key writes NO event."""

    def test_first_sighting_has_changed(self, events_dir: Path) -> None:
        assert has_changed(KEY, "zenodo", "rev-1", events_dir) is True

    def test_same_key_has_not_changed(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="T")
        assert has_changed(KEY, "zenodo", "rev-1", events_dir) is False

    def test_different_key_has_changed(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="T")
        assert has_changed(KEY, "zenodo", "rev-2", events_dir) is True

    def test_change_detection_is_per_source_system(self, events_dir: Path) -> None:
        """Four sources on one identity must not mask each other's changes."""
        scrape(events_dir, source_key="rev-1", system="zenodo", at="2026-01-01T00:00:00Z")
        assert has_changed(KEY, "datacite", "rev-1", events_dir) is True
        assert last_source_key(KEY, "zenodo", events_dir) == "rev-1"
        assert last_source_key(KEY, "datacite", events_dir) is None


class TestX02AnnotationSurvives:
    """Local annotation survives a wholesale source replacement."""

    def test_annotation_survives_a_source_key_change(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z",
               title="Lidar campaign", notes="First description")
        annotate(
            KEY,
            {
                "iea_task": ["task-43"],
                "curator_notes": [{"note": "Also relevant to floating wind."}],
            },
            events_dir=events_dir,
            observed_at="2026-01-15T00:00:00Z",
        )
        scrape(events_dir, source_key="rev-2", at="2026-02-01T00:00:00Z",
               title="Lidar campaign (revised)", notes="Second description")

        resolved = resolve(KEY, events_dir=events_dir)
        # source.* replaced wholesale
        assert resolved.source["title"] == "Lidar campaign (revised)"
        assert resolved.source["notes"] == "Second description"
        # local.* untouched
        assert resolved.local["iea_task"] == ["task-43"]
        assert resolved.local["curator_notes"][0]["note"].startswith("Also relevant")
        assert resolved.effective["iea_task"] == ["task-43"]

    def test_source_replacement_drops_a_field_the_source_no_longer_sends(
        self, events_dir: Path
    ) -> None:
        """Wholesale means wholesale: no field-level merge of successive scrapes."""
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z",
               title="T", version="1.0")
        scrape(events_dir, source_key="rev-2", at="2026-02-01T00:00:00Z", title="T")
        assert "version" not in resolve(KEY, events_dir=events_dir).source


class TestX03ScalarDisplacement:
    """A source that starts providing a field displaces the local scalar."""

    def test_source_displaces_local_scalar_and_raises_a_notice(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="T")
        annotate(KEY, {"resource_kind": "dataset"}, events_dir=events_dir,
                 observed_at="2026-01-15T00:00:00Z")
        scrape(events_dir, source_key="rev-2", at="2026-02-01T00:00:00Z",
               title="T", resource_kind="software")

        resolved = resolve(KEY, events_dir=events_dir)
        assert resolved.effective["resource_kind"] == "software"      # source wins
        assert resolved.local["resource_kind"] == "dataset"           # retained in the log
        displacements = [n for n in resolved.notices if n["type"] == "displacement"]
        assert len(displacements) == 1
        assert displacements[0]["field"] == "resource_kind"
        assert displacements[0]["displaced_local_value"] == "dataset"
        assert displacements[0]["source_value"] == "software"

    def test_no_notice_when_the_values_agree(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="T")
        annotate(KEY, {"resource_kind": "dataset"}, events_dir=events_dir,
                 observed_at="2026-01-15T00:00:00Z")
        scrape(events_dir, source_key="rev-2", at="2026-02-01T00:00:00Z",
               title="T", resource_kind="dataset")
        assert [n for n in resolve(KEY, events_dir=events_dir).notices
                if n["type"] == "displacement"] == []

    def test_local_fills_a_gap_the_source_leaves(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="T")
        annotate(KEY, {"resource_kind": "dataset"}, events_dir=events_dir,
                 observed_at="2026-01-15T00:00:00Z")
        assert resolve(KEY, events_dir=events_dir).effective["resource_kind"] == "dataset"

    def test_an_explicit_displacement_notice_event_is_carried_through(
        self, events_dir: Path
    ) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="T")
        raise_notice(KEY, "displacement_notice",
                     {"field": "license_id", "displaced_local_value": "cc-by"},
                     events_dir=events_dir, observed_at="2026-02-01T00:00:00Z")
        notices = resolve(KEY, events_dir=events_dir).notices
        assert notices[0]["type"] == "displacement_notice"
        assert notices[0]["field"] == "license_id"


class TestX04SetUnion:
    """Set-valued enrichments union. A hand-added task is never erased."""

    def test_zenodo_community_does_not_erase_a_hand_attributed_task(
        self, events_dir: Path
    ) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="T")
        annotate(KEY, {"iea_task": ["task-49"]}, events_dir=events_dir,
                 observed_at="2026-01-15T00:00:00Z")
        scrape(events_dir, source_key="rev-2", at="2026-02-01T00:00:00Z",
               title="T", iea_task=["task-43"])

        resolved = resolve(KEY, events_dir=events_dir)
        assert sorted(resolved.effective["iea_task"]) == ["task-43", "task-49"]
        assert [n for n in resolved.notices if n["type"] == "displacement"] == []

    def test_repeated_annotations_union_rather_than_replace(self, events_dir: Path) -> None:
        annotate(KEY, {"iea_task": ["task-43"]}, events_dir=events_dir,
                 observed_at="2026-01-01T00:00:00Z")
        annotate(KEY, {"iea_task": ["task-49"]}, events_dir=events_dir,
                 observed_at="2026-01-02T00:00:00Z")
        annotate(KEY, {"iea_task": ["task-43"]}, events_dir=events_dir,
                 observed_at="2026-01-03T00:00:00Z")
        assert resolve(KEY, events_dir=events_dir).local["iea_task"] == ["task-43", "task-49"]

    def test_scalar_annotations_are_latest_wins(self, events_dir: Path) -> None:
        annotate(KEY, {"resource_kind": "dataset"}, events_dir=events_dir,
                 observed_at="2026-01-01T00:00:00Z")
        annotate(KEY, {"resource_kind": "software"}, events_dir=events_dir,
                 observed_at="2026-01-02T00:00:00Z")
        assert resolve(KEY, events_dir=events_dir).local["resource_kind"] == "software"


class TestX01FourWayMerge:
    """One artifact seen by four sources is one record with four source URLs."""

    def test_four_sources_compose_by_precedence_and_union_source_urls(
        self, events_dir: Path
    ) -> None:
        for system, key, url, title in [
            ("ieawind", "hash-a", "https://iea-wind.org/task43/", "Cited on the task page"),
            ("github", "sha-b", "https://github.com/IEA-Task-43/x", "x"),
            ("zenodo", "rev-c", "https://zenodo.org/records/1234", "Zenodo title"),
            ("datacite", "2026-01-01", "https://doi.org/10.5281/zenodo.1234", "DataCite title"),
        ]:
            record_scrape(KEY, system, "1234", key,
                          {"title": title, "url": url, "source_urls": [url]},
                          events_dir=events_dir, observed_at="2026-01-01T00:00:00Z")

        resolved = resolve(KEY, events_dir=events_dir)
        assert resolved.source_systems == ["datacite", "github", "ieawind", "zenodo"]
        assert len(resolved.effective["source_urls"]) == 4
        # DataCite has the lowest precedence number, so it states the title.
        assert resolved.effective["title"] == "DataCite title"

    def test_one_source_changing_does_not_wipe_the_others(self, events_dir: Path) -> None:
        record_scrape(KEY, "github", "1234", "sha-a",
                      {"source_urls": ["https://github.com/a/b"]}, events_dir=events_dir,
                      observed_at="2026-01-01T00:00:00Z")
        record_scrape(KEY, "zenodo", "1234", "rev-1",
                      {"source_urls": ["https://zenodo.org/records/1"]}, events_dir=events_dir,
                      observed_at="2026-01-02T00:00:00Z")
        record_scrape(KEY, "zenodo", "1234", "rev-2",
                      {"source_urls": ["https://zenodo.org/records/1"]}, events_dir=events_dir,
                      observed_at="2026-02-01T00:00:00Z")
        urls = resolve(KEY, events_dir=events_dir).effective["source_urls"]
        assert "https://github.com/a/b" in urls


class TestWithdrawal:
    """ADR-0027 / fixture zen-12: kept, never deleted."""

    def test_withdrawn_record_is_retained_with_its_metadata(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z",
               title="A tombstoned dataset")
        withdraw(KEY, "zenodo", note="404 at source", events_dir=events_dir,
                 observed_at="2026-03-01T00:00:00Z")
        resolved = resolve(KEY, events_dir=events_dir)
        assert resolved.withdrawn is True
        assert resolved.withdrawn_at == "2026-03-01T00:00:00Z"
        assert resolved.effective["title"] == "A tombstoned dataset"
        assert event_path(KEY, events_dir).exists()

    def test_a_withdrawn_record_still_materialises(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="Gone")
        withdraw(KEY, "zenodo", events_dir=events_dir, observed_at="2026-03-01T00:00:00Z")
        package = replay(KEY, events_dir=events_dir)
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert extras["lifecycle_state"] == "withdrawn"
        assert extras["withdrawn"] == "true"
        assert package["state"] == "active", "CKAN 'deleted' would hide and permit purging"

    def test_annotations_on_a_withdrawn_record_still_apply(self, events_dir: Path) -> None:
        scrape(events_dir, source_key="rev-1", at="2026-01-01T00:00:00Z", title="Gone")
        withdraw(KEY, "zenodo", events_dir=events_dir, observed_at="2026-03-01T00:00:00Z")
        annotate(KEY, {"curator_notes": [{"note": "Superseded by 10.5281/zenodo.9"}]},
                 events_dir=events_dir, observed_at="2026-03-02T00:00:00Z")
        resolved = resolve(KEY, events_dir=events_dir)
        assert resolved.withdrawn and resolved.local["curator_notes"]


class TestProvenance:
    def test_provenance_is_carried_and_replaced_per_source(self, events_dir: Path) -> None:
        record_scrape(
            KEY, "ieawind", "1234", "hash-1", {"resource_kind": "dataset"},
            provenance={"resource_kind": FieldProvenance(
                extraction_method="llm", model="openai/gpt-4o-mini",
                prompt_version="v1", confidence=0.72)},
            events_dir=events_dir, observed_at="2026-01-01T00:00:00Z",
        )
        resolved = resolve(KEY, events_dir=events_dir)
        assert resolved.provenance["resource_kind"].extraction_method == "llm"
        assert resolved.provenance["resource_kind"].source_system == "ieawind"

    def test_llm_provenance_must_declare_model_and_prompt_version(self) -> None:
        with pytest.raises(ValueError):
            FieldProvenance(extraction_method="llm")
        FieldProvenance(extraction_method="api")  # no extra requirements


class TestReplayIsPure:
    def test_resolve_accepts_events_without_touching_disk(self) -> None:
        events = [
            Event(observed_at="2026-01-01T00:00:00Z", event_type="scraped", identity_key=KEY,
                  source_system="zenodo", source_id="1", source_key="rev-1",
                  source={"title": "In memory"}),
            Event(observed_at="2026-01-02T00:00:00Z", event_type="annotated", identity_key=KEY,
                  local={"iea_task": ["task-43"]}),
        ]
        resolved = resolve(KEY, events=events, precedence={"zenodo": 30})
        assert resolved.effective["title"] == "In memory"
        assert resolved.effective["iea_task"] == ["task-43"]
        assert resolved.event_count == 2

    def test_no_events_yields_an_empty_resolution(self, events_dir: Path) -> None:
        resolved = resolve("10.5281/zenodo.nothing", events_dir=events_dir)
        assert resolved.event_count == 0 and resolved.effective == {}
