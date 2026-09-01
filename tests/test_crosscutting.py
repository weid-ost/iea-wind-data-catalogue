"""``fixtures/cross-cutting/`` — every fixture, replayed end to end.

Each fixture's ``raw/<id>.json`` holds the **inputs**: a hand-written event
stream, optionally the curator YAML a human would have written, optionally the
HTTP statuses the link checker will see. The expectation file holds the
**outcome**: the materialised CKAN records, the notices, the merge decisions.

The replay here is the real pipeline — ``append_event`` → ``apply_annotations``
→ ``check_pins`` → ``materialize_all`` → ``dedupe`` / ``linkcheck`` — so a
fixture that stops matching means a behaviour changed, not that a mock drifted.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from harvest import config
from harvest.annotations import apply_annotations, check_pins
from harvest.ckan_compat import validate_records
from harvest.dedupe import dedupe
from harvest.events import append_event, resolve
from harvest.linkcheck import check_records
from harvest.materialize import materialize_all
from harvest.models import Event

FIXTURES = config.fixtures_dir() / "cross-cutting"
REAL_ROOT = Path(__file__).resolve().parent.parent


def fixture_ids() -> list[str]:
    return sorted(
        path.stem
        for path in FIXTURES.glob("x-*.json")
        if (FIXTURES / "raw" / path.name).exists()
    )


def load(fixture_id: str) -> tuple[dict, dict]:
    fixture = json.loads((FIXTURES / f"{fixture_id}.json").read_text(encoding="utf-8"))
    raw = json.loads((FIXTURES / "raw" / f"{fixture_id}.json").read_text(encoding="utf-8"))
    return fixture, raw


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.error = None
        self.ok = status_code < 400


class FakeHttp:
    def __init__(self, responses: dict[str, int]):
        self.responses = responses

    def get(self, url: str, **kwargs):  # noqa: ANN003
        return FakeResponse(self.responses.get(url, 200))


def build_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name in ("organizations.yaml", "groups.yaml", "sources.yaml"):
        shutil.copy(REAL_ROOT / name, root / name)
    for name in ("events", "records", "annotations", "state"):
        (root / name).mkdir()
    return root


def replay(raw: dict, root: Path) -> Path:
    """Append the fixture's events, replaying its annotations at the declared point."""
    events_dir = root / "events"
    annotations = raw.get("annotations_yaml")
    cut = raw.get("annotations_applied_before_event_index")

    def apply_now() -> None:
        if annotations is None:
            return
        (root / "annotations" / "sample.yaml").write_text(
            yaml.safe_dump(annotations, sort_keys=False), encoding="utf-8"
        )
        outcome = apply_annotations(root / "annotations", events_dir, root=root)
        assert not outcome.errors, outcome.errors
        assert outcome.applied, "the fixture's annotations must actually apply"

    for index, payload in enumerate(raw["events"]):
        if cut == index:
            apply_now()
        event = Event.model_validate(payload)
        append_event(event.identity_key, event, events_dir)
    if cut is None or cut >= len(raw["events"]):
        apply_now()

    check_pins(events_dir, root=root)
    return events_dir


def materialised(root: Path) -> dict[str, dict]:
    result = materialize_all(root / "events", root / "records", root=root)
    assert not result.violations, [str(v) for v in result.violations]
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((root / "records").glob("*.json"))
    }


def notices_for(root: Path, keys: list[str]) -> list[dict]:
    """Every notice the fixture's identities raise.

    No timestamps are stripped: a notice's ``observed_at`` is the observation
    that caused it, never the wall clock, so a replay of the same log produces
    the same notices to the second.
    """
    out: list[dict] = []
    for key in keys:
        out.extend(resolve(key, events_dir=root / "events").notices)
    return out


@pytest.mark.parametrize("fixture_id", fixture_ids())
class TestEveryCrossCuttingFixture:
    def test_the_records_match_the_expectation(self, fixture_id: str, tmp_path: Path) -> None:
        fixture, raw = load(fixture_id)
        root = build_root(tmp_path)
        replay(raw, root)
        assert materialised(root) == fixture["expected_records"]

    def test_the_notices_match_the_expectation(self, fixture_id: str, tmp_path: Path) -> None:
        fixture, raw = load(fixture_id)
        root = build_root(tmp_path)
        replay(raw, root)
        materialised(root)
        assert notices_for(root, fixture["identity_keys"]) == fixture["expected_notices"]

    def test_the_records_pass_the_ckan_gate(self, fixture_id: str, tmp_path: Path) -> None:
        fixture, raw = load(fixture_id)
        root = build_root(tmp_path)
        replay(raw, root)
        materialised(root)
        assert validate_records(root / "records", root=root) == []

    def test_materialisation_is_byte_stable(self, fixture_id: str, tmp_path: Path) -> None:
        fixture, raw = load(fixture_id)
        root = build_root(tmp_path)
        replay(raw, root)
        materialised(root)
        before = {p.name: p.read_text(encoding="utf-8") for p in (root / "records").glob("*.json")}
        materialised(root)
        after = {p.name: p.read_text(encoding="utf-8") for p in (root / "records").glob("*.json")}
        assert after == before

    def test_the_dedupe_outcome_matches(self, fixture_id: str, tmp_path: Path) -> None:
        fixture, raw = load(fixture_id)
        if "expected_dedupe" not in fixture:
            pytest.skip("not a dedupe fixture")
        root = build_root(tmp_path)
        events_dir = replay(raw, root)
        materialised(root)

        preview = dedupe(events_dir, root=root, apply=False)
        assert [c.as_dict() for c in preview.merges] == fixture["expected_dedupe"]["merges"]
        assert [c.as_dict() for c in preview.proposals] == fixture["expected_dedupe"]["proposals"]

        stamp = raw.get("merge_observed_at")
        applied = dedupe(events_dir, root=root, apply=True, observed_at=stamp)
        assert [c.as_dict() for c in applied.applied] == fixture["expected_applied_merges"]
        assert materialised(root) == fixture["expected_records_after_merge"]

        again = dedupe(events_dir, root=root, apply=True, observed_at=stamp)
        assert again.applied == [], "applying a merge twice must change nothing"
        assert materialised(root) == fixture["expected_records_after_merge"]

    def test_the_link_check_outcome_matches(self, fixture_id: str, tmp_path: Path) -> None:
        fixture, raw = load(fixture_id)
        if "expected_link_check" not in fixture:
            pytest.skip("not a link-check fixture")
        root = build_root(tmp_path)
        replay(raw, root)
        records = materialised(root)

        report = check_records(root / "records", FakeHttp(raw["http_responses"]), root=root)
        expected = fixture["expected_link_check"]
        assert sorted(report.dead_urls) == expected["dead_urls"]
        assert report.dead_by_record() == expected["dead_by_record"]
        assert report.unreachable_hosts() == expected["unreachable_hosts"]
        assert report.as_notices() == expected["notices"]
        assert materialised(root) == records, "link rot never edits a record"


class TestTheFixturesThemselvesSayWhatTheyMean:
    def test_x01_is_one_record_with_four_source_urls(self) -> None:
        fixture, _ = load("x-01-four-way-merge")
        assert fixture["expected_record_names"] == ["doi-10-5072-zenodo-1234566"]
        extras = {e["key"]: e["value"] for e in fixture["record"]["extras"]}
        assert len(json.loads(extras["source_urls"])) == 4
        assert json.loads(extras["source_systems"]) == [
            "datacite", "github", "ieawind", "zenodo"
        ]

    def test_x02_keeps_local_and_replaces_source_wholesale(self) -> None:
        fixture, raw = load("x-02-annotation-survives")
        record = fixture["record"]
        extras = {e["key"]: e["value"] for e in record["extras"]}
        assert record["title"] == raw["events"][1]["source"]["title"]
        assert "version" not in record, "the source stopped sending it, so the record loses it"
        assert json.loads(extras["iea_task"]) == ["task-31"]
        assert "curator_notes" in extras

    def test_x03_records_the_displacement(self) -> None:
        fixture, _ = load("x-03-scalar-displacement")
        extras = {e["key"]: e["value"] for e in fixture["record"]["extras"]}
        assert extras["resource_kind"] == "software"
        [notice] = fixture["expected_notices"]
        assert notice["type"] == "displacement"
        assert notice["displaced_local_value"] == "model"

    def test_x04_unions_the_tasks(self) -> None:
        fixture, _ = load("x-04-set-union")
        extras = {e["key"]: e["value"] for e in fixture["record"]["extras"]}
        assert json.loads(extras["iea_task"]) == ["task-43", "task-49"]
        assert fixture["record"]["groups"] == [{"name": "task-43"}, {"name": "task-49"}]

    def test_x05_badges_exactly_the_machine_inferred_fields(self) -> None:
        """The catalogue's Expected handling for x-05, asserted rather than promised.

        ADR-0028 §5 puts a visible machine-inferred badge on every field whose
        ``extraction_method`` is ``llm``, and §7 keeps the record visible however
        low the confidence is. So: the two model-extracted fields carry a
        confidence under the threshold, ``iea_task`` came from a pattern and must
        carry no badge at all, and the record is ``state: active`` — present, not
        suppressed.
        """
        fixture, _ = load("x-05-low-confidence")
        record = fixture["record"]
        extras = {e["key"]: e["value"] for e in record["extras"]}
        provenance = json.loads(extras["provenance"])

        llm = sorted(
            field for field, p in provenance.items() if p["extraction_method"] == "llm"
        )
        assert llm == fixture["expected_llm_fields"]
        for field in llm:
            entry = provenance[field]
            assert entry["confidence"] < fixture["expected_confidence_ceiling"]
            # ADR-0028 §2: an llm field without model and prompt_version is
            # unconstructible, and the badge has nothing to say without them.
            assert entry["model"] and entry["prompt_version"]
        assert provenance["iea_task"]["extraction_method"] == "pattern"
        assert "confidence" not in provenance["iea_task"]
        assert record["state"] == "active", "low confidence is badged, never hidden"

    def test_x06_is_two_records_because_the_key_is_fragile(self) -> None:
        fixture, _ = load("x-06-no-identifier")
        assert len(fixture["expected_record_names"]) == 2
        for record in fixture["expected_records"].values():
            extras = {e["key"]: e["value"] for e in record["extras"]}
            assert extras["identity_kind"] == "fragile"

    def test_x06_fuzzy_pass_proposes_the_merge_it_created(self, tmp_path: Path) -> None:
        """The fragile key's designed recovery path: propose, never auto-merge."""
        _, raw = load("x-06-no-identifier")
        root = build_root(tmp_path)
        events_dir = replay(raw, root)
        result = dedupe(events_dir, root=root, apply=True)
        assert result.applied == []
        [proposal] = result.proposals
        assert proposal.kind == "fuzzy-title" and proposal.automatic is False

    def test_x09_the_pin_holds_and_the_notice_fires(self) -> None:
        fixture, _ = load("x-09-pinned-extraction")
        extras = {e["key"]: e["value"] for e in fixture["record"]["extras"]}
        assert extras["pinned"] == "true"
        assert extras["resource_kind"] == "software", "the pin holds"
        [notice] = fixture["expected_notices"]
        assert notice["type"] == "pin_notice"
        assert notice["pin_source_key"] != notice["observed_source_key"]

    def test_x10_shows_the_wrong_value_verbatim_with_the_note_beside_it(self) -> None:
        fixture, _ = load("x-10-curator-note")
        record = fixture["record"]
        assert record["license_id"] == "cc-by", "exactly what the source said"
        extras = {e["key"]: e["value"] for e in record["extras"]}
        note = json.loads(extras["curator_notes"])[0]
        assert note["field"] == "license_id" and "CC-BY-NC" in note["note"]

    def test_x21_is_a_proposal_and_nothing_was_applied(self) -> None:
        fixture, _ = load("x-21-dedupe-fuzzy-proposal")
        assert fixture["expected_dedupe"]["merges"] == []
        assert fixture["expected_applied_merges"] == []
        [proposal] = fixture["expected_dedupe"]["proposals"]
        assert proposal["automatic"] is False
        assert fixture["expected_records_after_merge"] == fixture["expected_records"]

    def test_x22_lists_the_published_version_and_links_the_preprint(self) -> None:
        fixture, _ = load("x-22-dedupe-preprint-pair")
        after = fixture["expected_records_after_merge"]
        published = {e["key"]: e["value"]
                     for e in after["doi-10-5194-wes-9-101-2024"]["extras"]}
        preprint = {e["key"]: e["value"]
                    for e in after["doi-10-5194-egusphere-2023-1234"]["extras"]}
        assert "suppressed" not in published
        assert preprint["suppressed"] == "true"
        assert "egusphere" in published["local_links"]

    def test_x24_makes_osti_an_additional_source_url(self) -> None:
        fixture, _ = load("x-24-osti-mandated-duplicate")
        after = fixture["expected_records_after_merge"]
        primary = {e["key"]: e["value"]
                   for e in after["doi-10-1088-1742-6596-2265-2-022001"]["extras"]}
        assert "https://www.osti.gov/biblio/1854723" in json.loads(primary["source_urls"])
        assert sorted(after) == [
            "doi-10-1088-1742-6596-2265-2-022001", "osti-1854723"
        ], "the duplicate is retained, not deleted"


class TestX08EndToEnd:
    """The gate's failure case, exercised through ``harvest validate``.

    ``x-08`` is a record CKAN would refuse. It is a *fixture*, never a committed
    record: the point of the CKAN-compat gate is that such a thing can never
    reach ``records/`` unnoticed.
    """

    def test_it_is_not_and_never_becomes_a_committed_record(self) -> None:
        fixture = json.loads(
            (FIXTURES / "x-08-ckan-invalid.json").read_text(encoding="utf-8")
        )
        assert fixture["expect_violations"] is True
        assert not (config.records_dir() / f"{fixture['record']['name']}.json").exists()

    def test_dropped_into_records_it_fails_the_gate_and_the_cli_exits_nonzero(
        self, repo: Path, capsys
    ) -> None:  # noqa: ANN001
        from harvest.cli import main

        fixture = json.loads(
            (FIXTURES / "x-08-ckan-invalid.json").read_text(encoding="utf-8")
        )
        # The filename stem must be legal even though the record's name is not,
        # or the file could never have been written by materialize in the first place.
        (repo / "records" / "x-08-ckan-invalid.json").write_text(
            json.dumps(fixture["record"], indent=2), encoding="utf-8"
        )

        violations = validate_records(repo / "records", root=repo)
        fields = {violation.field for violation in violations}
        assert {"name", "license_id", "tags[0]", "owner_org", "groups[0]", "state"} <= fields
        assert any("must be a string" in v.message for v in violations)

        assert main(["--root", str(repo), "validate"]) == 1
        assert "validate-ckan-compat: FAIL" in capsys.readouterr().err

    def test_a_valid_catalogue_still_passes(self, repo: Path, tmp_path: Path) -> None:
        from harvest.cli import main

        _, raw = load("x-01-four-way-merge")
        for payload in raw["events"]:
            event = Event.model_validate(payload)
            append_event(event.identity_key, event, repo / "events")
        materialize_all(repo / "events", repo / "records", root=repo)
        assert main(["--root", str(repo), "validate"]) == 0


class TestDefensiveLimits:
    """scrape-07 / scrape-11: nothing upstream may inflate this repository.

    ``events/*.jsonl`` and ``records/*.json`` are committed on every change and
    then rendered as HTML and indexed by Pagefind. With no cap anywhere, one
    upstream description of ten million characters produced a 10 MB event line,
    a 10 MB record, a 10 MB page and a Pagefind entry to match — and the CKAN
    gate reported zero violations.
    """

    def test_a_giant_description_is_truncated_and_marked(self) -> None:
        from harvest.models import MAX_TEXT_LENGTH, TRUNCATION_MARKER, SourceNamespace

        source = SourceNamespace(title="T", notes="A" * 10_000_000)

        assert len(source.notes) == MAX_TEXT_LENGTH
        assert source.notes.endswith(TRUNCATION_MARKER), (
            "the page must not imply the description simply ended there"
        )

    def test_an_ordinary_description_is_untouched(self) -> None:
        from harvest.models import SourceNamespace

        notes = "Ten-minute statistics from a scanning lidar." * 100
        assert SourceNamespace(title="T", notes=notes).notes == notes

    def test_collections_are_capped(self) -> None:
        from harvest.models import MAX_COLLECTION_ITEMS, SourceNamespace

        source = SourceNamespace(
            title="T",
            keywords=[f"kw-{n}" for n in range(5000)],
            resources=[{"url": f"https://example.org/{n}.csv"} for n in range(5000)],
        )

        assert len(source.keywords) == MAX_COLLECTION_ITEMS
        assert len(source.resources) == MAX_COLLECTION_ITEMS

    def test_the_gate_refuses_an_inflated_record(self) -> None:
        """Defence in depth: the caps must hold for a hand-edited record too."""
        from harvest.ckan_compat import validate_package
        from harvest.models import MAX_TEXT_LENGTH

        package = {
            "name": "x-1", "title": "T", "notes": "A" * (MAX_TEXT_LENGTH + 1),
            "license_id": "cc-by", "owner_org": "dtu", "state": "active",
            "private": False, "tags": [], "groups": [], "extras": [], "resources": [],
        }

        violations = validate_package(package, {"dtu"}, set())

        assert any(v.field == "notes" for v in violations)

    def test_a_record_written_through_the_pipeline_stays_small(
        self, tmp_path: Path
    ) -> None:
        """The end-to-end claim, measured in bytes on disk."""
        from harvest.events import record_scrape

        root = build_root(tmp_path)
        record_scrape(
            "10.5281/zenodo.1", "zenodo", "1", "rev-1",
            {"title": "T", "url": "https://example.org/1",
             "notes": "A" * 10_000_000,
             "keywords": [f"kw-{n}" for n in range(5000)]},
            events_dir=root / "events", observed_at="2026-01-01T00:00:00Z",
        )

        result = materialize_all(root / "events", root / "records", root=root)

        assert not result.violations, [str(v) for v in result.violations]
        record = root / "records" / "doi-10-5281-zenodo-1.json"
        assert record.stat().st_size < 1_000_000
        assert (root / "events" / "doi-10-5281-zenodo-1.jsonl").stat().st_size < 1_000_000
