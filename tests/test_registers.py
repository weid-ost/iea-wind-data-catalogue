"""sources.yaml, organizations.yaml, groups.yaml and the scheming schema.

These are canonical data, not configuration (plan §2.2), so they get the same
treatment as code: if a group name would fail CKAN, CI says so today rather
than on promotion day.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest import config
from harvest.ckan_compat import NAME_RE
from harvest.events import DEFAULT_SOURCE_PRECEDENCE

SEVEN = {"zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh"}


class TestSources:
    def test_all_seven_sources_are_present(self) -> None:
        assert set(config.load_sources()) == SEVEN

    @pytest.mark.parametrize("name", sorted(SEVEN))
    def test_every_source_declares_the_required_fields(self, name: str) -> None:
        entry = config.load_sources()[name]
        assert isinstance(entry["enabled"], bool)
        assert entry["tier"] in (1, 2, 3)
        assert entry["max_records"] == 5, "the prototype cap is five"
        assert entry["source_key"], "the change token's semantics must be documented"
        assert isinstance(entry["precedence"], int)

    def test_precedence_values_are_unique(self) -> None:
        values = [entry["precedence"] for entry in config.load_sources().values()]
        assert len(set(values)) == len(values)

    def test_precedence_ranks_registries_above_html(self) -> None:
        sources = config.load_sources()
        assert sources["datacite"]["precedence"] < sources["zenodo"]["precedence"]
        assert sources["zenodo"]["precedence"] < sources["ieawind"]["precedence"]

    def test_the_builtin_fallback_precedence_covers_every_source(self) -> None:
        assert set(DEFAULT_SOURCE_PRECEDENCE) == SEVEN

    def test_zenodo_communities_are_real_slugs(self) -> None:
        """Verified live on 2026-08-31 — see the comment in sources.yaml."""
        slugs = {c["slug"] for c in config.load_sources()["zenodo"]["communities"]}
        assert "iea_wind_task_43" in slugs
        assert "ieawindtask32" in slugs and "ieawindtask52" in slugs

    def test_github_orgs_are_real(self) -> None:
        logins = {org["login"] for org in config.load_sources()["github"]["orgs"]}
        assert {"IEAWindSystems", "IEA-Task-43", "IEAWindTask37"} <= logins

    def test_crossref_anchors_on_wind_energy_science(self) -> None:
        journals = config.load_sources()["crossref"]["journals"]
        assert any(j["issn"] == "2366-7451" for j in journals)

    def test_ieawind_task_pages_reference_known_groups(self) -> None:
        known = config.group_names()
        for page in config.load_sources()["ieawind"]["task_pages"]:
            assert page["iea_task"] in known, page
            assert page["url"].startswith("https://iea-wind.org/")

    def test_wdh_never_enumerates_files(self) -> None:
        assert config.load_sources()["wdh"]["enumerate_files"] is False


class TestOrganizations:
    def test_the_expected_institutions_are_present(self) -> None:
        names = config.organization_names()
        assert {"dtu", "nrel", "pnnl", "sandia", "ost", "zenodo-community"} <= names

    def test_every_org_name_is_a_legal_ckan_slug(self) -> None:
        for name in config.organization_names():
            assert NAME_RE.match(name), name

    def test_every_org_has_a_title(self) -> None:
        for org in config.load_organizations():
            assert org.get("title")


class TestGroups:
    def test_every_group_name_is_a_legal_ckan_slug(self) -> None:
        for name in config.group_names():
            assert NAME_RE.match(name), name

    def test_the_current_tasks_are_present(self) -> None:
        names = config.group_names()
        assert {f"task-{n}" for n in range(41, 63)} <= names
        assert "task-11" in names and "task-65" in names

    def test_iea_08_renumbering_aliases(self) -> None:
        """A page citing both the old and the new number resolves to one group."""
        assert config.canonical_group("task-19") == "task-54"   # cold climate
        assert config.canonical_group("task-34") == "task-59"   # WREN
        assert config.canonical_group("task-54") == "task-54"
        assert config.canonical_group("task-59") == "task-59"

    def test_free_text_aliases_resolve(self) -> None:
        assert config.canonical_group("WREN") == "task-59"
        assert config.canonical_group("Cold Climate") == "task-54"
        assert config.canonical_group("digitalization") == "task-43"

    def test_task_36_is_not_aliased_to_task_51(self) -> None:
        """Task 51 succeeds Task 36 with a different scope. Not a renumbering."""
        assert config.canonical_group("task-36") == "task-36"

    def test_an_unknown_group_passes_through_unchanged(self) -> None:
        assert config.canonical_group("task-999") == "task-999"

    def test_aliases_never_collide(self) -> None:
        aliases = config.group_aliases()
        assert len(aliases) == len({a.lower() for a in aliases})

    def test_no_alias_shadows_a_canonical_name(self) -> None:
        names = config.group_names()
        for alias, target in config.group_aliases().items():
            if alias in names:
                assert alias == target, f"{alias!r} is both a group and an alias for {target!r}"


class TestSchemingSchema:
    def test_it_is_valid_json(self) -> None:
        assert json.loads(config.scheming_path().read_text(encoding="utf-8"))

    def test_it_declares_every_custom_extra(self) -> None:
        from harvest.materialize import EXTRA_KEYS

        schema = json.loads(config.scheming_path().read_text(encoding="utf-8"))
        declared = {field["field_name"] for field in schema["dataset_fields"]}
        missing = set(EXTRA_KEYS) - declared
        assert not missing, f"undocumented custom fields: {sorted(missing)}"

    def test_extras_are_marked_as_such(self) -> None:
        from harvest.materialize import EXTRA_KEYS

        schema = json.loads(config.scheming_path().read_text(encoding="utf-8"))
        for field in schema["dataset_fields"]:
            if field["field_name"] in EXTRA_KEYS:
                assert field.get("extras") is True, field["field_name"]


class TestPaths:
    def test_harvest_root_env_override(self, monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
        monkeypatch.setenv("HARVEST_ROOT", str(tmp_path))
        assert config.repo_root() == tmp_path.resolve()
        assert config.events_dir() == tmp_path.resolve() / "events"

    def test_missing_register_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert config.load_sources(tmp_path) == {}
        assert config.load_groups(tmp_path) == []
