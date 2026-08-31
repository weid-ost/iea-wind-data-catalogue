"""The adapter contract — the 5-record cap, change detection, degradation (wdh-07)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import (
    ADAPTERS,
    Adapter,
    SourceConfig,
    SourceUnreachable,
    get_adapter,
    load_adapters,
    payload_hash,
    run_adapter,
)
from harvest.events import read_events
from harvest.models import (
    FieldProvenance,
    MappedObservation,
    RawObservation,
    SourceNamespace,
)


class FakeAdapter(Adapter):
    """A minimal, complete adapter. Also the worked example in CONTRACT.md."""

    source_name = "fake"
    tier = 1
    source_key_semantics = "record revision"

    def __init__(self, records=None, fail=None, yield_extra=0, **kwargs):  # noqa: ANN003
        super().__init__(**kwargs)
        self.records = records or []
        self.fail = fail
        self.yield_extra = yield_extra
        self.closed = False

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        if self.fail:
            raise self.fail
        for index, record in enumerate(self.records[: limit + self.yield_extra]):
            yield RawObservation(
                source_system="fake",
                source_id=record["id"],
                source_key=record["rev"],
                url=f"https://example.org/{record['id']}",
                payload=record,
            )

    def map(self, raw: RawObservation) -> MappedObservation:
        return MappedObservation(
            identity_key=raw.payload["doi"],
            source_system=raw.source_system,
            source_id=raw.source_id,
            source_key=raw.source_key,
            source=SourceNamespace(
                title=raw.payload["title"],
                doi=raw.payload["doi"],
                url=raw.url,
                source_urls=[raw.url or ""],
            ),
            provenance={"title": FieldProvenance(extraction_method="api")},
        )

    def close(self) -> None:
        self.closed = True


def records(count: int) -> list[dict]:
    return [
        {"id": str(n), "rev": f"rev-{n}", "doi": f"10.5281/zenodo.{n}", "title": f"Record {n}"}
        for n in range(count)
    ]


class TestRegistry:
    def test_all_seven_sources_have_an_adapter(self) -> None:
        registered = load_adapters()
        assert set(registered) >= {
            "zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh"
        }

    def test_source_name_matches_the_module_name(self) -> None:
        for name, adapter_class in load_adapters().items():
            assert adapter_class.__module__ == f"harvest.adapters.{name}"

    def test_get_adapter_by_name(self) -> None:
        assert get_adapter("zenodo").source_name == "zenodo"

    def test_unknown_source_gives_a_useful_error(self) -> None:
        with pytest.raises(Exception, match="no adapter registered"):
            get_adapter("not-a-source")

    def test_every_stub_raises_not_implemented_naming_its_owner(self) -> None:
        for name in ("zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh"):
            adapter = get_adapter(name)(config=SourceConfig(name=name))
            with pytest.raises(NotImplementedError, match="owner: Track"):
                list(adapter.harvest(limit=1))

    def test_registry_is_keyed_by_source_name(self) -> None:
        for name, adapter_class in ADAPTERS.items():
            assert adapter_class.source_name == name


class TestTheFiveRecordCap:
    def test_default_limit_is_five(self) -> None:
        assert DEFAULT_LIMIT == 5

    def test_the_limit_is_honoured(self, events_dir: Path) -> None:
        result = run_adapter(FakeAdapter(records(20)), limit=5, events_dir=events_dir)
        assert result.seen == 5

    def test_the_limit_is_enforced_even_if_an_adapter_ignores_it(
        self, events_dir: Path
    ) -> None:
        adapter = FakeAdapter(records(20), yield_extra=10)
        assert run_adapter(adapter, limit=5, events_dir=events_dir).seen == 5

    def test_max_records_from_sources_yaml_is_five(self) -> None:
        from harvest import config

        for name, mapping in config.load_sources().items():
            assert SourceConfig.from_mapping(name, mapping).max_records == 5


class TestChangeDetection:
    def test_first_run_writes_one_event_per_record(self, events_dir: Path) -> None:
        result = run_adapter(FakeAdapter(records(3)), limit=5, events_dir=events_dir)
        assert (result.seen, result.changed, result.skipped_unchanged) == (3, 3, 0)
        assert len(read_events("10.5281/zenodo.0", events_dir)) == 1

    def test_second_run_with_the_same_source_key_writes_nothing(
        self, events_dir: Path
    ) -> None:
        """ADR-0026: unchanged means NO event, not an empty one."""
        run_adapter(FakeAdapter(records(3)), limit=5, events_dir=events_dir)
        result = run_adapter(FakeAdapter(records(3)), limit=5, events_dir=events_dir)
        assert (result.seen, result.changed, result.skipped_unchanged) == (3, 0, 3)
        assert len(read_events("10.5281/zenodo.0", events_dir)) == 1

    def test_a_changed_source_key_appends(self, events_dir: Path) -> None:
        run_adapter(FakeAdapter(records(1)), limit=5, events_dir=events_dir)
        moved = records(1)
        moved[0]["rev"] = "rev-99"
        run_adapter(FakeAdapter(moved), limit=5, events_dir=events_dir)
        assert len(read_events("10.5281/zenodo.0", events_dir)) == 2

    def test_dry_run_writes_no_events(self, events_dir: Path) -> None:
        result = run_adapter(FakeAdapter(records(3)), limit=5, events_dir=events_dir,
                             dry_run=True)
        assert result.changed == 3
        assert read_events("10.5281/zenodo.0", events_dir) == []


class TestDegradation:
    """wdh-07: an adapter that cannot reach its source disables itself cleanly."""

    def test_source_unreachable_is_reported_not_raised(self, events_dir: Path) -> None:
        adapter = FakeAdapter([], fail=SourceUnreachable("listing endpoint needs a token"))
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.reachable is False
        assert "token" in result.errors[0]

    def test_an_unexpected_exception_is_also_contained(self, events_dir: Path) -> None:
        adapter = FakeAdapter([], fail=RuntimeError("upstream changed its schema"))
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.reachable is False
        assert "RuntimeError" in result.errors[0]

    def test_a_stub_is_reported_as_not_implemented(self, events_dir: Path) -> None:
        adapter = FakeAdapter([], fail=NotImplementedError("owner: Track Z"))
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert result.implemented is False and result.reachable is True

    def test_one_bad_record_does_not_stop_the_others(self, events_dir: Path) -> None:
        class HalfBroken(FakeAdapter):
            def map(self, raw: RawObservation) -> MappedObservation:
                if raw.source_id == "1":
                    raise ValueError("unexpected payload shape")
                return super().map(raw)

        result = run_adapter(HalfBroken(records(3)), limit=5, events_dir=events_dir)
        assert result.seen == 3 and result.changed == 2 and len(result.errors) == 1
        assert result.reachable is True

    def test_a_disabled_source_is_skipped_entirely(self, events_dir: Path) -> None:
        adapter = FakeAdapter(records(3), config=SourceConfig(name="fake", enabled=False))
        result = run_adapter(adapter, limit=5, events_dir=events_dir)
        assert (result.enabled, result.seen, result.changed) == (False, 0, 0)

    def test_a_slug_collision_costs_one_record_not_the_run(self, events_dir: Path) -> None:
        from harvest.events import record_scrape

        record_scrape("10.5281/zenodo.0", "zenodo", "0", "rev-1", {"title": "incumbent"},
                      events_dir=events_dir, observed_at="2026-01-01T00:00:00Z")
        # Same slug, different identity string: the log refuses it, the run lives.
        colliding = [{"id": "0", "rev": "r", "doi": "10.5281/zenodo.0 ", "title": "x"},
                     {"id": "1", "rev": "r", "doi": "10.5281/zenodo.1", "title": "y"}]
        result = run_adapter(FakeAdapter(colliding), limit=5, events_dir=events_dir)
        assert result.reachable is True
        assert result.changed == 1
        assert len(result.errors) == 1 and "slug collision" in result.errors[0]

    def test_close_is_always_called(self, events_dir: Path) -> None:
        adapter = FakeAdapter([], fail=RuntimeError("boom"))
        run_adapter(adapter, limit=5, events_dir=events_dir)
        assert adapter.closed is True


class TestSourceConfig:
    def test_reads_a_sources_yaml_entry(self) -> None:
        from harvest import config

        cfg = SourceConfig.from_mapping("zenodo", config.load_sources()["zenodo"])
        assert cfg.name == "zenodo"
        assert cfg.enabled and cfg.tier == 1 and cfg.max_records == 5
        assert cfg.precedence == 30
        assert cfg.get("communities")[0]["slug"] == "iea_wind_task_43"

    def test_unknown_keys_land_in_options(self) -> None:
        cfg = SourceConfig.from_mapping("x", {"enabled": True, "banana": 3})
        assert cfg.options == {"banana": 3}
        assert cfg.get("banana") == 3
        assert cfg.get("missing", "default") == "default"


class TestPayloadHash:
    def test_is_deterministic_and_key_order_independent(self) -> None:
        assert payload_hash({"a": 1, "b": [2, 3]}) == payload_hash({"b": [2, 3], "a": 1})

    def test_changes_with_content(self) -> None:
        assert payload_hash({"a": 1}) != payload_hash({"a": 2})

    def test_is_short_and_hex(self) -> None:
        digest = payload_hash({"a": 1})
        assert len(digest) == 16 and int(digest, 16) >= 0
