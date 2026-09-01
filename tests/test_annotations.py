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

KEY = "10.5072/zenodo.1234566"


def write_annotation(directory: Path, name: str, document: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def seed_scrape(events_dir: Path, key: str = KEY, **source) -> None:
    payload = {
        "title": "Lidar measurements from the Østerild campaign, 2021",
        "url": "https://sandbox.zenodo.org/records/1234567",
        "source_urls": ["https://sandbox.zenodo.org/records/1234567"],
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
        assert not (events_dir / "doi-10-5072-zenodo-1234566.jsonl").exists()
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
                    "url": "https://sandbox.zenodo.org/records/1234567"},
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
        extras = extras_of(repo / "records", "doi-10-5072-zenodo-1234566")
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
            (repo / "records" / "doi-10-5072-zenodo-1234566.json").read_text(encoding="utf-8")
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
        assert (repo / "records" / "doi-10-5072-zenodo-1234566.json").exists()
        assert extras_of(repo / "records", "doi-10-5072-zenodo-1234566")["suppressed"] == "true"


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


# ---------------------------------------------------------------------------
# eventlog-04 — an annotation adds; it does not assert a source claim
# ---------------------------------------------------------------------------


class TestWhatACuratorMayAssert:
    """ADR-0038's "additive only", enforced rather than merely intended.

    ``local`` was validated against ``LocalNamespace``, which carries every
    field a *source* carries. So an annotation could write
    ``license_id: cc-by`` — an open licence, on a record page, with no
    ``license_raw`` behind it, no source that said it, and no way for a reader
    to tell a human had asserted it. The catalogue's entire claim is that it
    reports what upstream says and marks everything else; a field set this wide
    made that claim unenforceable.

    The remedy is a closed field set plus provenance on what remains.
    """

    @pytest.mark.parametrize(
        "field,value",
        [
            ("license_id", "cc-by"),
            ("license_raw", "CC BY 4.0"),
            ("title", "A better title"),
            ("doi", "10.5281/zenodo.9"),
            ("publisher", "Zenodo"),
            ("notes", "rewritten abstract"),
            ("authors", [{"name": "Nobody"}]),
        ],
    )
    def test_an_annotation_cannot_state_a_source_claim(
        self, repo: Path, field: str, value: object
    ) -> None:
        path = write_annotation(
            repo / "annotations", "bad.yaml",
            {"identity_key": KEY, "annotations": [{"local": {field: value}}]},
        )
        with pytest.raises(AnnotationError, match="annotations may not set"):
            load_annotation_file(path, root=repo)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("iea_task", ["task-43"]),
            ("resource_kind", "dataset"),
            ("access_status", "open"),
            ("curator_notes", [{"field": "license_id", "note": "looks wrong"}]),
            ("links", [{"url": "https://example.org/x", "label": "x"}]),
            ("suppressed", True),
            ("owner_org", "dtu"),
        ],
    )
    def test_what_a_curator_may_add_still_works(
        self, repo: Path, field: str, value: object
    ) -> None:
        path = write_annotation(
            repo / "annotations", "good.yaml",
            {"identity_key": KEY, "annotations": [{"local": {field: value}}]},
        )
        [annotation] = load_annotation_file(path, root=repo)
        assert field in annotation.local

    def test_the_error_says_what_to_do_instead(self, repo: Path) -> None:
        """A refusal that does not name the alternative just gets worked around."""
        path = write_annotation(
            repo / "annotations", "bad.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"license_id": "cc-by"}}]},
        )
        with pytest.raises(AnnotationError, match="curator_notes"):
            load_annotation_file(path, root=repo)

    def test_an_unknown_owner_org_is_refused(self, repo: Path) -> None:
        """It would fail the CKAN gate; say so at the annotation, not the gate."""
        path = write_annotation(
            repo / "annotations", "bad.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"owner_org": "not-an-org"}}]},
        )
        with pytest.raises(AnnotationError, match="organizations.yaml"):
            load_annotation_file(path, root=repo)

    def test_what_a_curator_sets_is_badged_as_a_curator_assertion(
        self, repo: Path, events_dir: Path
    ) -> None:
        """Otherwise an annotated `resource_kind` reads as an API's statement."""
        seed_scrape(events_dir)
        write_annotation(
            repo / "annotations", "a.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"resource_kind": "dataset"}}]},
        )
        apply_annotations(repo / "annotations", events_dir, root=repo)

        provenance = resolve(KEY, events_dir=events_dir).provenance
        assert provenance["resource_kind"].extraction_method == "curator"

    def test_it_does_not_overwrite_what_a_source_already_accounted_for(
        self, repo: Path, events_dir: Path
    ) -> None:
        """`iea_task` is a UNION. Stamping the union `curator` erases the source.

        Zenodo's community slug contributed task-43 and the curator added
        task-49; claiming the whole field is a human assertion is exactly the
        inversion of the honesty the provenance exists for.
        """
        from harvest.models import FieldProvenance

        record_scrape(
            KEY, "zenodo", "1234567", "1",
            {"title": "T", "iea_task": ["task-43"]},
            provenance={"iea_task": FieldProvenance(extraction_method="pattern")},
            events_dir=events_dir, observed_at="2026-08-24T03:11:07Z",
        )
        write_annotation(
            repo / "annotations", "a.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-49"]}}]},
        )
        apply_annotations(repo / "annotations", events_dir, root=repo)

        resolved = resolve(KEY, events_dir=events_dir)
        assert sorted(resolved.effective["iea_task"]) == ["task-43", "task-49"]
        assert resolved.provenance["iea_task"].extraction_method == "pattern"


class TestTaskSpellingIsNormalisedOnStore:
    """eventlog-05: validated case-insensitively, stored verbatim.

    ``iea_task`` was checked against ``groups.yaml`` case- and
    space-insensitively but written to the log exactly as the file spelled it.
    ``Task 43`` and ``task-43`` therefore both validated and then became two
    chips on the record page and two buckets in the task facet — one task
    presented as two.
    """

    @pytest.mark.parametrize("spelling", ["Task 43", "TASK-43", " task-43 ", "task_43", "Task-043"])
    def test_every_spelling_stores_as_one(self, repo: Path, spelling: str) -> None:
        path = write_annotation(
            repo / "annotations", "a.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": [spelling]}}]},
        )
        [annotation] = load_annotation_file(path, root=repo)
        assert annotation.local["iea_task"] == ["task-43"]

    def test_two_spellings_of_one_task_are_one_value(self, repo: Path) -> None:
        path = write_annotation(
            repo / "annotations", "a.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": ["Task 43", "task-43"]}}]},
        )
        [annotation] = load_annotation_file(path, root=repo)
        assert annotation.local["iea_task"] == ["task-43"]

    def test_the_record_shows_one_chip_not_two(self, repo: Path, events_dir: Path) -> None:
        record_scrape(
            KEY, "zenodo", "1234567", "1",
            {"title": "T", "url": "https://sandbox.zenodo.org/records/1234567",
             "iea_task": ["TASK-43"]},
            events_dir=events_dir, observed_at="2026-08-24T03:11:07Z",
        )
        write_annotation(
            repo / "annotations", "a.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": ["Task 43"]}}]},
        )
        apply_annotations(repo / "annotations", events_dir, root=repo)
        materialize_all(root=repo)

        extras = extras_of(repo / "records", "doi-10-5072-zenodo-1234566")
        assert json.loads(extras["iea_task"]) == ["task-43"]
        package = json.loads(
            (repo / "records" / "doi-10-5072-zenodo-1234566.json").read_text(encoding="utf-8")
        )
        assert [group["name"] for group in package["groups"]] == ["task-43"]


class TestOneBadAnnotationFileDoesNotStopTheRun:
    """compliance-04 / CONTRACT rule 5. A curator's typo is not a build break."""

    def test_malformed_yaml_is_collected_not_raised(self, repo: Path, events_dir: Path) -> None:
        seed_scrape(events_dir)
        (repo / "annotations").mkdir(parents=True, exist_ok=True)
        (repo / "annotations" / "broken.yaml").write_text(
            "identity_key: x\nlocal: [unclosed\n", encoding="utf-8"
        )
        write_annotation(
            repo / "annotations", "good.yaml",
            {"identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-43"]}}]},
        )

        outcome = apply_annotations(repo / "annotations", events_dir, root=repo)

        assert outcome.errors, "the broken file must be reported"
        assert any("broken.yaml" in error for error in outcome.errors)
        assert outcome.applied, "the good file must still apply"
        assert resolve(KEY, events_dir=events_dir).effective["iea_task"] == ["task-43"]
