"""Materialisation — byte-stability, CKAN shape, withdrawal retention, pruning."""

from __future__ import annotations

import json
from pathlib import Path

from harvest.events import annotate, record_scrape, withdraw
from harvest.materialize import dump_record, materialize_all, to_ckan_package
from harvest.events import resolve

KEY = "10.5281/zenodo.1234"

SOURCE = {
    "title": "Lidar measurements from the Østerild campaign",
    "notes": "Ten-minute statistics from a scanning lidar.",
    "doi": KEY,
    "url": "https://zenodo.org/records/1234",
    "source_urls": ["https://zenodo.org/records/1234"],
    "authors": [{"name": "Søren Ø. Müller", "orcid": "0000-0002-1825-0097"}],
    "publisher": "Zenodo",
    "published_date": "2024-06-01",
    "license_raw": "CC-BY-4.0",
    "license_id": "cc-by",
    "resource_kind": "dataset",
    "access_status": "open",
    "keywords": ["lidar", "wind energy", "Østerild"],
    "resources": [{"url": "https://zenodo.org/records/1234/files/data.csv",
                   "name": "data.csv", "format": "CSV"}],
    "version": "2.0",
}


def seed(events_dir: Path, **overrides) -> None:  # noqa: ANN003
    source = {**SOURCE, **overrides}
    record_scrape(KEY, "zenodo", "1234", "rev-1", source,
                  events_dir=events_dir, observed_at="2026-01-01T00:00:00Z")


class TestCkanShape:
    def test_record_is_a_ckan_package(self, repo: Path, events_dir: Path) -> None:
        seed(events_dir)
        annotate(KEY, {"iea_task": ["task-43", "task-49"]}, events_dir=events_dir,
                 observed_at="2026-01-02T00:00:00Z")
        result = materialize_all(root=repo)
        assert result.ok, result.violations

        package = json.loads((repo / "records" / "doi-10-5281-zenodo-1234.json").read_text())
        assert package["name"] == "doi-10-5281-zenodo-1234"
        assert package["title"] == SOURCE["title"]
        assert package["license_id"] == "cc-by"
        assert package["state"] == "active"
        assert {g["name"] for g in package["groups"]} == {"task-43", "task-49"}
        assert {t["name"] for t in package["tags"]} == {"lidar", "wind-energy", "osterild"}
        assert package["resources"][0]["url"].endswith("data.csv")

    def test_extras_values_are_all_strings(self, repo: Path, events_dir: Path) -> None:
        seed(events_dir)
        annotate(KEY, {"iea_task": ["task-43"]}, events_dir=events_dir,
                 observed_at="2026-01-02T00:00:00Z")
        materialize_all(root=repo)
        package = json.loads((repo / "records" / "doi-10-5281-zenodo-1234.json").read_text())
        assert all(isinstance(extra["value"], str) for extra in package["extras"])

    def test_structured_extras_are_json_in_a_string(self, repo: Path, events_dir: Path) -> None:
        seed(events_dir)
        annotate(KEY, {"iea_task": ["task-49", "task-43"]}, events_dir=events_dir,
                 observed_at="2026-01-02T00:00:00Z")
        materialize_all(root=repo)
        package = json.loads((repo / "records" / "doi-10-5281-zenodo-1234.json").read_text())
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert json.loads(extras["iea_task"]) == ["task-43", "task-49"]   # sorted
        assert json.loads(extras["source_urls"]) == [SOURCE["url"]]
        assert json.loads(extras["authors"])[0]["name"] == "Søren Ø. Müller"
        assert extras["identity_key"] == KEY
        assert extras["identity_kind"] == "doi"
        assert extras["license_raw"] == "CC-BY-4.0"
        assert extras["license_mapped"] == "true"

    def test_x10_curator_note_reaches_the_record(self, repo: Path, events_dir: Path) -> None:
        """Known-wrong upstream: the wrong value stays, the note sits beside it."""
        seed(events_dir, license_raw="CC-BY-4.0")
        annotate(
            KEY,
            {"curator_notes": [{"field": "license_id",
                                "note": "OST note: the licence at source appears incorrect."}]},
            events_dir=events_dir, observed_at="2026-01-02T00:00:00Z",
        )
        materialize_all(root=repo)
        package = json.loads((repo / "records" / "doi-10-5281-zenodo-1234.json").read_text())
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert package["license_id"] == "cc-by", "the source value is displayed verbatim"
        note = json.loads(extras["curator_notes"])[0]
        assert note["field"] == "license_id" and "appears incorrect" in note["note"]

    def test_provenance_encodes_tersely(self, repo: Path, events_dir: Path) -> None:
        from harvest.models import FieldProvenance

        record_scrape(
            KEY, "zenodo", "1234", "rev-1", SOURCE,
            provenance={
                "title": FieldProvenance(extraction_method="api"),
                "resource_kind": FieldProvenance(
                    extraction_method="llm", model="openai/gpt-4o-mini",
                    prompt_version="v1", confidence=0.72),
            },
            events_dir=events_dir, observed_at="2026-01-01T00:00:00Z",
        )
        materialize_all(root=repo)
        package = json.loads((repo / "records" / "doi-10-5281-zenodo-1234.json").read_text())
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        provenance = json.loads(extras["provenance"])
        assert provenance["title"] == {"extraction_method": "api", "source_system": "zenodo"}
        assert provenance["resource_kind"]["model"] == "openai/gpt-4o-mini"
        assert provenance["resource_kind"]["confidence"] == 0.72
        assert "pinned" not in provenance["title"]

    def test_unmapped_licence_is_flagged_in_the_result(self, repo: Path, events_dir: Path) -> None:
        seed(events_dir, license_raw="Free for academic use", license_id=None)
        result = materialize_all(root=repo)
        assert result.ok
        assert result.unmapped_licenses[0]["license_raw"] == "Free for academic use"
        package = json.loads((repo / "records" / "doi-10-5281-zenodo-1234.json").read_text())
        assert package["license_id"] == "notspecified"

    def test_diacritics_survive_in_display_and_transliterate_in_the_slug(
        self, repo: Path, events_dir: Path
    ) -> None:
        """zen-10, from both ends."""
        seed(events_dir)
        materialize_all(root=repo)
        text = (repo / "records" / "doi-10-5281-zenodo-1234.json").read_text(encoding="utf-8")
        assert "Østerild" in text and "Søren" in text
        assert (repo / "records" / "doi-10-5281-zenodo-1234.json").exists()


class TestByteStability:
    def test_materialising_twice_is_byte_identical(self, repo: Path, events_dir: Path) -> None:
        seed(events_dir)
        annotate(KEY, {"iea_task": ["task-43"]}, events_dir=events_dir,
                 observed_at="2026-01-02T00:00:00Z")
        materialize_all(root=repo)
        first = (repo / "records" / "doi-10-5281-zenodo-1234.json").read_bytes()

        second_result = materialize_all(root=repo)
        second = (repo / "records" / "doi-10-5281-zenodo-1234.json").read_bytes()

        assert first == second
        assert second_result.written == [], "an unchanged record must not be rewritten"
        assert second_result.unchanged == ["doi-10-5281-zenodo-1234"]

    def test_rebuilding_from_scratch_reproduces_the_same_bytes(
        self, repo: Path, events_dir: Path
    ) -> None:
        """records/ is derived: delete it and it comes back identical."""
        seed(events_dir)
        materialize_all(root=repo)
        path = repo / "records" / "doi-10-5281-zenodo-1234.json"
        original = path.read_bytes()
        path.unlink()
        materialize_all(root=repo)
        assert path.read_bytes() == original

    def test_dump_is_sorted_and_newline_terminated(self) -> None:
        payload = dump_record({"b": 1, "a": 2})
        assert payload.endswith("\n")
        assert payload.index('"a"') < payload.index('"b"')

    def test_key_order_in_events_does_not_change_the_record(
        self, repo: Path, events_dir: Path
    ) -> None:
        volatile = {"last_seen", "source_key"}

        def stable(package: dict) -> str:
            package = dict(package)
            package["extras"] = [e for e in package["extras"] if e["key"] not in volatile]
            return dump_record(package)

        seed(events_dir)
        first = stable(to_ckan_package(resolve(KEY, events_dir=events_dir)))
        shuffled = dict(reversed(list(SOURCE.items())))
        record_scrape(KEY, "zenodo", "1234", "rev-2", shuffled,
                      events_dir=events_dir, observed_at="2026-02-01T00:00:00Z")
        second = stable(to_ckan_package(resolve(KEY, events_dir=events_dir)))
        assert first == second


class TestWithdrawalAndPruning:
    def test_withdrawn_records_are_materialised_not_deleted(
        self, repo: Path, events_dir: Path
    ) -> None:
        seed(events_dir)
        materialize_all(root=repo)
        withdraw(KEY, "zenodo", events_dir=events_dir, observed_at="2026-03-01T00:00:00Z")
        result = materialize_all(root=repo)

        path = repo / "records" / "doi-10-5281-zenodo-1234.json"
        assert path.exists()
        assert result.pruned == []
        package = json.loads(path.read_text())
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert extras["withdrawn"] == "true"
        assert extras["withdrawn_at"] == "2026-03-01T00:00:00Z"
        assert package["title"] == SOURCE["title"], "the metadata is kept, not blanked"

    def test_a_record_with_no_events_is_pruned(self, repo: Path, events_dir: Path) -> None:
        seed(events_dir)
        materialize_all(root=repo)
        (repo / "records" / "orphan.json").write_text('{"name": "orphan"}\n')
        result = materialize_all(root=repo)
        assert result.pruned == ["orphan"]
        assert not (repo / "records" / "orphan.json").exists()

    def test_pruning_can_be_disabled(self, repo: Path, events_dir: Path) -> None:
        seed(events_dir)
        (repo / "records" / "orphan.json").write_text(
            '{"name": "orphan", "title": "T", "license_id": "notspecified"}\n'
        )
        result = materialize_all(root=repo, prune=False)
        assert result.pruned == []
        assert (repo / "records" / "orphan.json").exists()


class TestValidationIsRunOnMaterialise:
    def test_an_invalid_group_reference_fails_the_gate(
        self, repo: Path, events_dir: Path
    ) -> None:
        seed(events_dir)
        annotate(KEY, {"iea_task": ["task-999"]}, events_dir=events_dir,
                 observed_at="2026-01-02T00:00:00Z")
        result = materialize_all(root=repo)
        assert not result.ok
        assert any("task-999" in str(v) for v in result.violations)

    def test_an_empty_catalogue_validates(self, repo: Path) -> None:
        assert materialize_all(root=repo).ok

    def test_a_slug_collision_is_refused_at_the_event_log(self, events_dir: Path) -> None:
        """Two identities rendering to one slug must never share an event file."""
        import pytest

        record_scrape("zenodo|a.b", "zenodo", "x", "rev-1", {"title": "first"},
                      events_dir=events_dir, observed_at="2026-01-01T00:00:00Z")
        with pytest.raises(ValueError, match="slug collision"):
            record_scrape("zenodo|a-b", "zenodo", "x", "rev-1", {"title": "second"},
                          events_dir=events_dir, observed_at="2026-01-01T00:00:00Z")

    def test_the_defence_in_depth_check_also_lives_in_materialize(
        self, repo: Path, events_dir: Path
    ) -> None:
        """Hand-written event files can still collide; materialize says so."""
        for stem, key in (("zenodo-a-b", "zenodo|a-b"), ("zenodo-a-b-2", "zenodo|a.b")):
            (events_dir / f"{stem}.jsonl").write_text(
                json.dumps({
                    "observed_at": "2026-01-01T00:00:00Z", "event_type": "scraped",
                    "identity_key": key, "source_system": "zenodo", "source_id": "x",
                    "source_key": "rev-1", "source": {"title": key},
                }) + "\n",
                encoding="utf-8",
            )
        result = materialize_all(root=repo)
        assert not result.ok
        assert any("slug collision" in str(v) for v in result.violations)
