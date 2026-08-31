"""The CKAN-compat gate — fixture x-08.

Every case here is a record CKAN's `package_create` would refuse. The gate
exists so that promotion day is a day's work rather than a three-thousand
record archaeology project.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest.ckan_compat import (
    Violation,
    format_violations,
    main,
    tagify,
    validate_package,
    validate_records,
)

ORGS = {"dtu", "nrel"}
GROUPS = {"task-43", "task-49"}


def valid_package(**overrides) -> dict:  # noqa: ANN003
    package = {
        "name": "doi-10-5281-zenodo-123",
        "title": "A perfectly ordinary record",
        "notes": "",
        "license_id": "cc-by",
        "tags": [{"name": "lidar"}],
        "extras": [{"key": "identity_key", "value": "10.5281/zenodo.123"}],
        "resources": [],
        "groups": [{"name": "task-43"}],
        "owner_org": "dtu",
        "state": "active",
    }
    package.update(overrides)
    return package


def fields(violations: list[Violation]) -> set[str]:
    return {violation.field for violation in violations}


def test_a_valid_package_passes() -> None:
    assert validate_package(valid_package(), ORGS, GROUPS) == []


class TestX08Rejections:
    @pytest.mark.parametrize(
        "name",
        [
            "Doi-10-5281-Zenodo-123",       # uppercase
            "doi 10 5281",                  # spaces
            "doi.10.5281",                  # dots
            "doi/10/5281",                  # slashes
            "søren-dataset",                # non-ASCII
            "a",                            # too short
            "x" * 101,                      # too long
            "",                             # empty
        ],
    )
    def test_illegal_slug(self, name: str) -> None:
        assert "name" in fields(validate_package(valid_package(name=name), ORGS, GROUPS))

    @pytest.mark.parametrize(
        "tag",
        [
            "wind energy",          # space
            "wind/energy",          # slash
            "wind:energy",          # colon
            "Søren",                # non-ASCII
            "a",                    # too short
            "x" * 101,              # too long
        ],
    )
    def test_illegal_tag(self, tag: str) -> None:
        violations = validate_package(valid_package(tags=[{"name": tag}]), ORGS, GROUPS)
        assert "tags[0]" in fields(violations)

    def test_duplicate_tag(self) -> None:
        package = valid_package(tags=[{"name": "lidar"}, {"name": "lidar"}])
        assert "tags[1]" in fields(validate_package(package, ORGS, GROUPS))

    @pytest.mark.parametrize("license_id", ["cc-by-4.0", "CC-BY", "MIT-License", "", None])
    def test_licence_not_in_register(self, license_id) -> None:  # noqa: ANN001
        assert "license_id" in fields(
            validate_package(valid_package(license_id=license_id), ORGS, GROUPS)
        )

    @pytest.mark.parametrize(
        "value", [["task-43"], {"a": 1}, 42, True, None, 3.14]
    )
    def test_non_string_extra_value(self, value) -> None:  # noqa: ANN001
        package = valid_package(extras=[{"key": "iea_task", "value": value}])
        violations = validate_package(package, ORGS, GROUPS)
        assert any("must be a string" in v.message for v in violations)

    def test_duplicate_extras_key(self) -> None:
        package = valid_package(
            extras=[{"key": "doi", "value": "a"}, {"key": "doi", "value": "b"}]
        )
        assert "extras[1]" in fields(validate_package(package, ORGS, GROUPS))

    def test_unknown_owner_org(self) -> None:
        assert "owner_org" in fields(
            validate_package(valid_package(owner_org="uni-of-nowhere"), ORGS, GROUPS)
        )

    def test_unknown_group(self) -> None:
        package = valid_package(groups=[{"name": "task-999"}])
        assert "groups[0]" in fields(validate_package(package, ORGS, GROUPS))

    def test_missing_title(self) -> None:
        assert "title" in fields(validate_package(valid_package(title="   "), ORGS, GROUPS))

    def test_resource_without_url(self) -> None:
        package = valid_package(resources=[{"name": "no url here"}])
        assert "resources[0]" in fields(validate_package(package, ORGS, GROUPS))

    def test_illegal_state(self) -> None:
        assert "state" in fields(validate_package(valid_package(state="withdrawn"), ORGS, GROUPS))

    def test_all_violations_are_reported_not_just_the_first(self) -> None:
        package = valid_package(
            name="Bad Name",
            license_id="cc-by-4.0",
            tags=[{"name": "wind energy"}],
            extras=[{"key": "iea_task", "value": ["task-43"]}],
            owner_org="nowhere",
        )
        violations = validate_package(package, ORGS, GROUPS)
        assert len(violations) >= 5
        assert fields(violations) >= {"name", "license_id", "tags[0]", "owner_org"}


class TestValidateRecordsDirectory:
    def test_empty_directory_is_valid(self, records_dir: Path) -> None:
        assert validate_records(records_dir, known_orgs=ORGS, known_groups=GROUPS) == []

    def test_filename_must_match_the_slug(self, records_dir: Path) -> None:
        (records_dir / "wrong-stem.json").write_text(json.dumps(valid_package()))
        violations = validate_records(records_dir, known_orgs=ORGS, known_groups=GROUPS)
        assert any("does not match filename stem" in v.message for v in violations)

    def test_invalid_json_is_a_violation_not_a_crash(self, records_dir: Path) -> None:
        (records_dir / "broken.json").write_text("{not json")
        violations = validate_records(records_dir, known_orgs=ORGS, known_groups=GROUPS)
        assert any("invalid JSON" in v.message for v in violations)

    def test_cli_exits_nonzero_and_lists_every_violation(
        self, repo: Path, records_dir: Path, capsys
    ) -> None:  # noqa: ANN001
        bad = valid_package(name="Bad Name", license_id="nope")
        (records_dir / "Bad Name.json").write_text(json.dumps(bad))
        assert main(["--records", str(records_dir), "--root", str(repo)]) == 1
        captured = capsys.readouterr()
        assert "FAIL" in captured.err
        assert "name" in captured.err and "license_id" in captured.err

    def test_cli_exits_zero_on_an_empty_catalogue(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--records", str(repo / "records"), "--root", str(repo)]) == 0
        assert "OK" in capsys.readouterr().out


class TestTagify:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("lidar", "lidar"),
            ("wind energy", "wind-energy"),
            ("Wind/Energy", "wind-energy"),
            ("Søren", "soren"),
            ("Müller", "muller"),
            ("v1.2.3", "v1.2.3"),
            ("a", ""),          # below CKAN's 2-char minimum: dropped
            ("!!!", ""),
            ("", ""),
        ],
    )
    def test_coercion(self, text: str, expected: str) -> None:
        assert tagify(text) == expected

    def test_output_is_always_a_legal_tag_or_empty(self) -> None:
        for text in ("wind energy", "Søren Ø", "x" * 400, "a/b:c;d", "施設"):
            tag = tagify(text)
            if tag:
                package = valid_package(tags=[{"name": tag}])
                assert validate_package(package, ORGS, GROUPS) == [], tag


def test_format_violations_is_readable() -> None:
    rendered = format_violations([Violation("rec", "name", "bad", path="records/rec.json")])
    assert "records/rec.json" in rendered and "bad" in rendered
