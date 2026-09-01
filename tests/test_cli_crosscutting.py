"""The CLI verbs this track adds, and the ones it wires itself into.

``materialize`` and ``run`` replay ``annotations/`` first, so a curator writes
one YAML file and runs one command. ``dedupe`` and ``linkcheck`` stay separate
verbs: one writes merge decisions, the other talks to seven upstreams, and
neither belongs in an unattended weekly run without being asked for.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from harvest.cli import main
from harvest.events import read_events, record_scrape
from harvest.materialize import materialize_all
from harvest.runreport import read_run_report

KEY = "10.5072/zenodo.1234566"
SLUG = "doi-10-5072-zenodo-1234566"


def seed(repo: Path, key: str = KEY, **source) -> None:
    payload = {
        "title": "Lidar measurements from the Østerild campaign, 2021",
        "url": "https://sandbox.zenodo.org/records/1234567",
        "source_urls": ["https://sandbox.zenodo.org/records/1234567"],
        "license_id": "cc-by",
    }
    payload.update(source)
    record_scrape(
        identity_key=key, source_system="zenodo", source_id="1234567",
        source_key=source.pop("source_key", "1"), source=payload,
        events_dir=repo / "events", observed_at="2026-08-24T03:11:07Z",
    )


def write_annotation(repo: Path, document: dict, name: str = "sample.yaml") -> None:
    (repo / "annotations").mkdir(exist_ok=True)
    (repo / "annotations" / name).write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )


class TestAnnotationsVerb:
    def test_it_applies_and_is_idempotent(self, repo: Path, capsys) -> None:  # noqa: ANN001
        seed(repo)
        write_annotation(repo, {
            "identity_key": KEY,
            "actor": "curator:tom",
            "annotations": [{"local": {"iea_task": ["task-49"]}, "note": "workshop list"}],
        })
        assert main(["--root", str(repo), "annotations"]) == 0
        assert "applied 1" in capsys.readouterr().out
        assert main(["--root", str(repo), "annotations"]) == 0
        assert "applied 0" in capsys.readouterr().out
        annotated = [e for e in read_events(KEY, repo / "events")
                     if e.event_type == "annotated"]
        assert len(annotated) == 1

    def test_dry_run_writes_nothing(self, repo: Path, capsys) -> None:  # noqa: ANN001
        seed(repo)
        write_annotation(repo, {
            "identity_key": KEY, "annotations": [{"local": {"suppressed": True}}],
        })
        assert main(["--root", str(repo), "annotations", "--dry-run"]) == 0
        assert "would apply 1" in capsys.readouterr().out
        assert not [e for e in read_events(KEY, repo / "events")
                    if e.event_type == "annotated"]

    def test_a_bad_file_exits_nonzero_and_names_the_problem(
        self, repo: Path, capsys
    ) -> None:  # noqa: ANN001
        write_annotation(repo, {
            "identity_key": KEY,
            "source": {"title": "a title I would prefer"},
            "annotations": [{"local": {"suppressed": True}}],
        })
        assert main(["--root", str(repo), "annotations"]) == 1
        assert "never edited" in capsys.readouterr().err

    def test_an_empty_directory_is_fine(self, repo: Path) -> None:
        assert main(["--root", str(repo), "annotations"]) == 0


class TestMaterializeAppliesAnnotations:
    def test_one_command_takes_yaml_all_the_way_to_a_record(self, repo: Path) -> None:
        seed(repo)
        write_annotation(repo, {
            "identity_key": KEY,
            "actor": "curator:tom",
            "annotations": [{"local": {"iea_task": ["task-49"]}, "note": "workshop list"}],
        })
        assert main(["--root", str(repo), "materialize"]) == 0
        package = json.loads((repo / "records" / f"{SLUG}.json").read_text(encoding="utf-8"))
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert json.loads(extras["iea_task"]) == ["task-49"]
        assert package["groups"] == [{"name": "task-49"}]

    def test_no_annotations_skips_the_replay(self, repo: Path) -> None:
        seed(repo)
        write_annotation(repo, {
            "identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-49"]}}],
        })
        assert main(["--root", str(repo), "materialize", "--no-annotations"]) == 0
        package = json.loads((repo / "records" / f"{SLUG}.json").read_text(encoding="utf-8"))
        assert package["groups"] == []

    def test_materialising_twice_produces_no_diff(self, repo: Path) -> None:
        seed(repo)
        write_annotation(repo, {
            "identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-49"]}}],
        })
        main(["--root", str(repo), "materialize"])
        before = (repo / "records" / f"{SLUG}.json").read_text(encoding="utf-8")
        main(["--root", str(repo), "materialize"])
        assert (repo / "records" / f"{SLUG}.json").read_text(encoding="utf-8") == before

    def test_run_replays_annotations_too_and_still_writes_the_heartbeat(
        self, repo: Path
    ) -> None:
        seed(repo)
        write_annotation(repo, {
            "identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-49"]}}],
        })
        assert main(["--root", str(repo), "run"]) == 0
        assert (repo / "state" / "last-run.json").exists()
        package = json.loads((repo / "records" / f"{SLUG}.json").read_text(encoding="utf-8"))
        assert package["groups"] == [{"name": "task-49"}]

    def test_a_pending_annotation_reaches_the_run_report(self, repo: Path) -> None:
        write_annotation(repo, {
            "identity_key": KEY, "annotations": [{"local": {"iea_task": ["task-49"]}}],
        })
        assert main(["--root", str(repo), "run"]) == 0
        notices = read_run_report(root=repo)["notices"]
        assert any(notice["type"] == "annotation_pending" for notice in notices)


class TestDedupeVerb:
    def _pair(self, repo: Path) -> None:
        seed(repo, key="10.1088/1742-6596/2265/2/022001",
             doi="10.1088/1742-6596/2265/2/022001", title="Nacelle lidar validation")
        record_scrape(
            identity_key="osti|1854723", source_system="osti", source_id="1854723",
            source_key="1",
            source={"title": "Nacelle Lidar Validation",
                    "doi": "10.1088/1742-6596/2265/2/022001",
                    "url": "https://www.osti.gov/biblio/1854723", "license_id": "cc-by"},
            events_dir=repo / "events", observed_at="2026-08-24T03:15:00Z",
        )

    def test_without_apply_it_reports_and_writes_the_proposal_file(
        self, repo: Path, capsys
    ) -> None:  # noqa: ANN001
        self._pair(repo)
        assert main(["--root", str(repo), "dedupe"]) == 0
        out = capsys.readouterr().out
        assert "would merge" in out and "--apply" in out
        payload = json.loads((repo / "state" / "merge-proposals.json").read_text())
        assert payload["merges"][0]["kind"] == "shared-doi"

    def test_apply_records_the_merge(self, repo: Path, capsys) -> None:  # noqa: ANN001
        self._pair(repo)
        assert main(["--root", str(repo), "dedupe", "--apply"]) == 0
        assert "merged" in capsys.readouterr().out
        materialize_all(repo / "events", repo / "records", root=repo)
        package = json.loads((repo / "records" / "osti-1854723.json").read_text())
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        assert extras["suppressed"] == "true"

    def test_the_threshold_is_adjustable(self, repo: Path, capsys) -> None:  # noqa: ANN001
        seed(repo, key="10.5281/zenodo.1", doi="10.5281/zenodo.1",
             title="Probabilistic wake loss estimation",
             authors=[{"name": "Nowak, Piotr"}], published_date="2024-09-02")
        seed(repo, key="10.5281/zenodo.2", doi="10.5281/zenodo.2",
             title="Deterministic wake gain estimation",
             authors=[{"name": "Nowak, Piotr"}], published_date="2024-09-02")
        assert main(["--root", str(repo), "dedupe"]) == 0
        assert "PROPOSAL" not in capsys.readouterr().out
        assert main(["--root", str(repo), "dedupe", "--threshold", "0.5"]) == 0
        assert "PROPOSAL" in capsys.readouterr().out

    def test_an_empty_catalogue_is_fine(self, repo: Path) -> None:
        assert main(["--root", str(repo), "dedupe"]) == 0


class TestLinkcheckVerb:
    def test_it_writes_the_state_file_without_touching_records(
        self, repo: Path, capsys, monkeypatch
    ) -> None:  # noqa: ANN001
        seed(repo)
        materialize_all(repo / "events", repo / "records", root=repo)
        before = (repo / "records" / f"{SLUG}.json").read_text(encoding="utf-8")

        class FakeResponse:
            status_code = 404
            error = None
            ok = False

        class FakeHttp:
            def get(self, url, **kwargs):  # noqa: ANN003
                return FakeResponse()

        monkeypatch.setattr("harvest.http.HarvestClient", lambda *a, **k: FakeHttp())
        assert main(["--root", str(repo), "linkcheck"]) == 0
        out = capsys.readouterr().out
        assert "DEAD" in out and "record retained" in out
        payload = json.loads((repo / "state" / "link-check.json").read_text())
        assert payload["dead"][0]["status_code"] == 404
        assert (repo / "records" / f"{SLUG}.json").read_text(encoding="utf-8") == before

    def test_an_empty_catalogue_is_fine(self, repo: Path) -> None:
        assert main(["--root", str(repo), "linkcheck"]) == 0

    def test_run_linkcheck_folds_the_notices_into_the_run_report(
        self, repo: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        seed(repo)

        class FakeResponse:
            status_code = 404
            error = None
            ok = False

        class FakeHttp:
            def get(self, url, **kwargs):  # noqa: ANN003
                return FakeResponse()

        monkeypatch.setattr("harvest.http.HarvestClient", lambda *a, **k: FakeHttp())
        assert main(["--root", str(repo), "run", "--linkcheck"]) == 0
        report = read_run_report(root=repo)
        assert any(notice["type"] == "dead_link" for notice in report["notices"])
        assert (repo / "state" / "link-check.json").exists()

    def test_run_does_not_link_check_by_default(self, repo: Path) -> None:
        """A weekly unattended job must not add hundreds of requests unasked."""
        seed(repo)
        assert main(["--root", str(repo), "run"]) == 0
        assert not (repo / "state" / "link-check.json").exists()


class TestTheExistingVerbsStillWork:
    def test_help_lists_every_verb(self, capsys) -> None:  # noqa: ANN001
        import pytest

        with pytest.raises(SystemExit):
            main(["--help"])
        out = capsys.readouterr().out
        for verb in ("run", "materialize", "validate", "annotations", "dedupe",
                     "linkcheck", "extract", "report", "sources"):
            assert verb in out
