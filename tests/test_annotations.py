"""``annotations/`` — ADR-0038's full matrix, driven through the core replay.

Every test here builds a throwaway repository root, appends real events through
``harvest.events``, replays real YAML through ``harvest.annotations``, and reads
the answer out of a materialised CKAN record. Nothing is stubbed, because the
thing under test *is* the interaction between the two namespaces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from harvest.annotations import (
    AnnotationError,
    apply_annotations,
    check_pins,
    fingerprint,
    load_annotation_file,
    load_annotations,
)
from harvest.events import annotate, read_events, record_scrape, resolve
from harvest.materialize import materialize_all

KEY = "10.5281/zenodo.1234566"


def write_annotation(directory: Path, name: str, document: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def seed_scrape(events_dir: Path, key: str = KEY, **source) -> None:
    payload = {
        "title": "Lidar measurements from the Østerild campaign, 2021",
        "url": "https://zenodo.org/records/1234567",
        "source_urls": ["https://zenodo.org/records/1234567"],
        "license_raw": "cc-by-4.0",
        "license_id": "cc-by",
        "withdrawn": False,
    }
    payload.update(source)
    record_scrape(
        identity_key=key,
        source_system=source.pop("source_system", "zenodo"),
        source_id="1234567",
        source_key=source.pop("source_key", "1"),
        source=payload,
        events_dir=events_dir,
        observed_at="2026-08-24T03:11:07Z",
    )


def extras_of(records_dir: Path, slug: str) -> dict[str, str]:
    package = json.loads((records_dir / f"{slug}.json").read_text(encoding="utf-8"))
    return {extra["key"]: extra["value"] for extra in package["extras"]}


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------


class TestParsing:
    def test_a_well_formed_file_parses(self, repo: Path) -> None:
        path = write_annotation(
            repo / "annotations",
            "sample.yaml",
            {
                "identity_key": KEY,
                "actor": "curator:tom",
                "annotations": [
                    {"local": {"iea_task": ["task-49"]}, "note": "from the workshop list"}
                ],
            },
        )
        [annotation] = load_annotation_file(path, root=repo)
        assert annotation.identity_key == KEY
        assert annotation.actor == "curator:tom"
        assert annotation.local == {"iea_task": ["task-49"]}

    def test_a_file_that_tries_to_edit_source_is_refused(self, repo: Path) -> None:
        """The one rule: source metadata is never edited, only annotated."""
        path = write_annotation(
            repo / "annotations",
            "bad.yaml",
            {
                "identity_key": KEY,
                "source": {"title": "a title I would prefer"},
                "annotations": [{"local": {"iea_task": ["task-49"]}}],
            },
        )
        with pytest.raises(AnnotationError, match="never edited"):
            load_annotation_file(path, root=repo)

    def test_a_per_entry_source_block_is_refused_too(self, repo: Path) -> None:
        path = write_annotation(
            repo / "annotations",
            "bad.yaml",
            {
                "identity_key": KEY,
                "annotations": [{"source": {"title": "no"}, "local": {"suppressed": True}}],
            },
        )
        with pytest.raises(AnnotationError, match="never edited"):
            load_annotation_file(path, root=repo)

    def test_a_scalar_where_a_list_belongs_fails_here_not_downstream(self, repo: Path) -> None:
        path = write_annotation(
            repo / "annotations",
            "bad.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": "task-49"}}]},
        )
        with pytest.raises(AnnotationError, match="local namespace"):
            load_annotation_file(path, root=repo)

    def test_an_invented_task_is_refused_before_it_can_fail_the_ckan_gate(
        self, repo: Path
    ) -> None:
        path = write_annotation(
            repo / "annotations",
            "bad.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-999"]}}]},
        )
        with pytest.raises(AnnotationError, match="groups.yaml"):
            load_annotation_file(path, root=repo)

    def test_a_renumbered_task_alias_is_accepted(self, repo: Path) -> None:
        """19 -> 54 and 34 -> 59 are real renumberings; both spellings are legal."""
        path = write_annotation(
            repo / "annotations",
            "alias.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-19"]}}]},
        )
        assert load_annotation_file(path, root=repo)

    def test_one_broken_file_does_not_stop_the_others(self, repo: Path) -> None:
        write_annotation(repo / "annotations", "a-bad.yaml", {"annotations": []})
        write_annotation(
            repo / "annotations",
            "b-good.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"suppressed": True}}]},
        )
        errors: list[str] = []
        loaded = load_annotations(repo / "annotations", root=repo, errors=errors)
        assert len(loaded) == 1
        assert errors and "identity_key" in errors[0]

    def test_missing_directory_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert load_annotations(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


class TestIdempotence:
    def test_replaying_twice_appends_once(self, repo: Path, events_dir: Path) -> None:
        seed_scrape(events_dir)
        write_annotation(
            repo / "annotations",
            "sample.yaml",
            {
                "identity_key": KEY,
                "actor": "curator:tom",
                "annotations": [{"local": {"iea_task": ["task-49"]}, "note": "workshop list"}],
            },
        )
        first = apply_annotations(repo / "annotations", events_dir, root=repo)
        second = apply_annotations(repo / "annotations", events_dir, root=repo)
        third = apply_annotations(repo / "annotations", events_dir, root=repo)

        assert len(first.applied) == 1
        assert second.applied == [] and len(second.skipped) == 1
        assert third.applied == []
        annotated = [e for e in read_events(KEY, events_dir) if e.event_type == "annotated"]
        assert len(annotated) == 1

    def test_the_fingerprint_ignores_the_clock(self) -> None:
        left = fingerprint(KEY, {"iea_task": ["task-49"]}, "curator:tom", "why")
        right = fingerprint(KEY, {"iea_task": ["task-49"]}, "curator:tom", "why")
        assert left == right
        assert left != fingerprint(KEY, {"iea_task": ["task-49"]}, "curator:tom", "different why")

    def test_a_changed_note_is_a_new_annotation(self, repo: Path, events_dir: Path) -> None:
        seed_scrape(events_dir)
        directory = repo / "annotations"
        write_annotation(directory, "s.yaml", {
            "identity_key": KEY,
            "annotations": [{"local": {"iea_task": ["task-49"]}, "note": "first reason"}],
        })
        apply_annotations(directory, events_dir, root=repo)
        write_annotation(directory, "s.yaml", {
            "identity_key": KEY,
            "annotations": [{"local": {"iea_task": ["task-49"]}, "note": "a better reason"}],
        })
        result = apply_annotations(directory, events_dir, root=repo)
        assert len(result.applied) == 1

    def test_dry_run_writes_nothing(self, repo: Path, events_dir: Path) -> None:
        seed_scrape(events_dir)
        write_annotation(repo / "annotations", "s.yaml", {
            "identity_key": KEY, "annotations": [{"local": {"suppressed": True}}],
        })
        result = apply_annotations(repo / "annotations", events_dir, root=repo, dry_run=True)
        assert len(result.applied) == 1
        assert not [e for e in read_events(KEY, events_dir) if e.event_type == "annotated"]


class TestPendingAnnotations:
    def test_an_annotation_for_an_unharvested_identity_waits(
        self, repo: Path, events_dir: Path
    ) -> None:
        """Applying it would materialise a record with no source at all."""
        write_annotation(repo / "annotations", "s.yaml", {
            "identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-49"]}}],
        })
        result = apply_annotations(repo / "annotations", events_dir, root=repo)
        assert result.applied == [] and len(result.pending) == 1
        assert not (events_dir / "doi-10-5281-zenodo-1234566.jsonl").exists()
        assert result.as_notices()[0]["type"] == "annotation_pending"

    def test_it_applies_itself_once_the_record_exists(
        self, repo: Path, events_dir: Path
    ) -> None:
        write_annotation(repo / "annotations", "s.yaml", {
            "identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-49"]}}],
        })
        apply_annotations(repo / "annotations", events_dir, root=repo)
        seed_scrape(events_dir)
        result = apply_annotations(repo / "annotations", events_dir, root=repo)
        assert len(result.applied) == 1

    def test_allow_new_is_the_explicit_opt_out(self, repo: Path, events_dir: Path) -> None:
        write_annotation(repo / "annotations", "s.yaml", {
            "identity_key": KEY,
            "allow_new": True,
            "annotations": [{"local": {"iea_task": ["task-49"]}}],
        })
        result = apply_annotations(repo / "annotations", events_dir, root=repo)
        assert len(result.applied) == 1


# ---------------------------------------------------------------------------
# The ADR-0038 matrix, end to end
# ---------------------------------------------------------------------------


class TestTheCollisionMatrix:
    def test_x02_an_annotation_survives_a_re_scrape(self, repo: Path, events_dir: Path) -> None:
        seed_scrape(events_dir, source_key="1", version="1.0")
        annotate(KEY, {"iea_task": ["task-43"]}, actor="curator:tom", events_dir=events_dir,
                 observed_at="2026-08-25T00:00:00Z")
        record_scrape(
            identity_key=KEY, source_system="zenodo", source_id="1234567", source_key="2",
            source={"title": "A corrected title", "license_id": "cc-by",
                    "url": "https://zenodo.org/records/1234567"},
            events_dir=events_dir, observed_at="2026-08-26T00:00:00Z",
        )
        resolved = resolve(KEY, events_dir=events_dir)
        assert resolved.effective["title"] == "A corrected title"
        assert "version" not in resolved.source, "source.* is replaced wholesale"
        assert resolved.local["iea_task"] == ["task-43"], "local.* is untouched"

    def test_x03_a_source_displaces_a_local_scalar_and_says_so(
        self, repo: Path, events_dir: Path
    ) -> None:
        seed_scrape(events_dir, source_key="1")
        annotate(KEY, {"resource_kind": "dataset"}, actor="curator:tom",
                 events_dir=events_dir, observed_at="2026-08-25T00:00:00Z")
        record_scrape(
            identity_key=KEY, source_system="zenodo", source_id="1234567", source_key="2",
            source={"title": "t", "resource_kind": "software"},
            events_dir=events_dir, observed_at="2026-08-26T00:00:00Z",
        )
        resolved = resolve(KEY, events_dir=events_dir)
        assert resolved.effective["resource_kind"] == "software"
        assert resolved.local["resource_kind"] == "dataset", "the displaced value stays in the log"
        [notice] = [n for n in resolved.notices if n["type"] == "displacement"]
        assert notice["field"] == "resource_kind"
        assert notice["displaced_local_value"] == "dataset"

    def test_x04_set_valued_fields_union_and_the_hand_attribution_survives(
        self, repo: Path, events_dir: Path
    ) -> None:
        seed_scrape(events_dir, source_key="1", iea_task=[])
        annotate(KEY, {"iea_task": ["task-49"]}, actor="curator:tom",
                 events_dir=events_dir, observed_at="2026-08-25T00:00:00Z")
        record_scrape(
            identity_key=KEY, source_system="zenodo", source_id="1234567", source_key="2",
            source={"title": "t", "iea_task": ["task-43"]},
            events_dir=events_dir, observed_at="2026-08-26T00:00:00Z",
        )
        materialize_all(events_dir, repo / "records", root=repo)
        extras = extras_of(repo / "records", "doi-10-5281-zenodo-1234566")
        assert json.loads(extras["iea_task"]) == ["task-43", "task-49"]

    def test_x10_a_curator_note_sits_beside_a_verbatim_wrong_value(
        self, repo: Path, events_dir: Path
    ) -> None:
        seed_scrape(events_dir, source_key="1", license_raw="cc-by-4.0", license_id="cc-by")
        annotate(
            KEY,
            {"curator_notes": [{"field": "license_id", "note": "OST note: source is wrong"}]},
            actor="curator:tom", events_dir=events_dir, observed_at="2026-08-25T00:00:00Z",
        )
        materialize_all(events_dir, repo / "records", root=repo)
        package = json.loads(
            (repo / "records" / "doi-10-5281-zenodo-1234566.json").read_text(encoding="utf-8")
        )
        assert package["license_id"] == "cc-by", "the wrong upstream value is kept, verbatim"
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert "OST note" in extras["curator_notes"]

    def test_a_suppressed_record_is_retained_never_deleted(
        self, repo: Path, events_dir: Path
    ) -> None:
        seed_scrape(events_dir)
        annotate(KEY, {"suppressed": True}, actor="curator:tom", events_dir=events_dir)
        materialize_all(events_dir, repo / "records", root=repo)
        assert (repo / "records" / "doi-10-5281-zenodo-1234566.json").exists()
        assert extras_of(repo / "records", "doi-10-5281-zenodo-1234566")["suppressed"] == "true"


# ---------------------------------------------------------------------------
# Pins (ADR-0038 §4.3)
# ---------------------------------------------------------------------------


class TestPins:
    def _pin(self, events_dir: Path, key: str, pin_source_key: str) -> None:
        annotate(key, {"pinned": True, "pin_source_key": pin_source_key},
                 actor="curator:tom", events_dir=events_dir,
                 observed_at="2026-08-25T00:00:00Z")

    def test_x09_the_pin_holds_and_a_notice_fires_when_the_page_moves(
        self, repo: Path, events_dir: Path
    ) -> None:
        key = "ieawind|task43/tools"
        record_scrape(
            identity_key=key, source_system="ieawind", source_id="task43/tools",
            source_key="a1b2c3d4e5f60718",
            source={"title": "Digital WRA Data Standard", "resource_kind": "report"},
            events_dir=events_dir, observed_at="2026-08-20T00:00:00Z",
        )
        self._pin(events_dir, key, "a1b2c3d4e5f60718")
        # The page changes; the extraction cache is pinned, so the extractor
        # still returns the corrected object.
        record_scrape(
            identity_key=key, source_system="ieawind", source_id="task43/tools",
            source_key="6d4e2f80b19c7a35",
            source={"title": "Digital WRA Data Standard", "resource_kind": "software"},
            events_dir=events_dir, observed_at="2026-08-30T00:00:00Z",
        )

        notices = check_pins(events_dir, root=repo)
        assert len(notices) == 1
        assert notices[0]["reason"] == "page-content-hash-changed"
        assert notices[0]["pin_source_key"] == "a1b2c3d4e5f60718"
        assert notices[0]["observed_source_key"] == "6d4e2f80b19c7a35"

        resolved = resolve(key, events_dir=events_dir)
        assert resolved.effective["resource_kind"] == "software", "the pin holds"
        assert resolved.local["pinned"] is True
        assert [n for n in resolved.notices if n["type"] == "pin_notice"]

    def test_a_pin_notice_fires_once_per_observed_source_key(
        self, repo: Path, events_dir: Path
    ) -> None:
        key = "ieawind|task43/tools"
        record_scrape(identity_key=key, source_system="ieawind", source_id="t",
                      source_key="aaaa", source={"title": "t"}, events_dir=events_dir,
                      observed_at="2026-08-20T00:00:00Z")
        self._pin(events_dir, key, "aaaa")
        record_scrape(identity_key=key, source_system="ieawind", source_id="t",
                      source_key="bbbb", source={"title": "t"}, events_dir=events_dir,
                      observed_at="2026-08-30T00:00:00Z")
        assert len(check_pins(events_dir, root=repo)) == 1
        assert check_pins(events_dir, root=repo) == []

    def test_an_unmoved_page_fires_nothing(self, repo: Path, events_dir: Path) -> None:
        key = "ieawind|task43/tools"
        record_scrape(identity_key=key, source_system="ieawind", source_id="t",
                      source_key="aaaa", source={"title": "t"}, events_dir=events_dir,
                      observed_at="2026-08-20T00:00:00Z")
        self._pin(events_dir, key, "aaaa")
        assert check_pins(events_dir, root=repo) == []
