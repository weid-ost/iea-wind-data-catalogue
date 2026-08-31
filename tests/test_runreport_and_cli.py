"""``state/last-run.json`` and the CLI.

The report is the cron keepalive (plan §3.3): it must be written on **every**
run, including a run in which nothing changed and every source failed, or the
scheduled workflow eventually goes dormant.
"""

from __future__ import annotations

import json
from pathlib import Path

from harvest.adapters.base import SourceResult
from harvest.cli import main
from harvest.runreport import RunReport, read_run_report


class TestRunReport:
    def test_is_written_even_for_a_complete_no_op(self, repo: Path) -> None:
        path = RunReport().write(root=repo)
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["started_at"] and payload["finished_at"]

    def test_shape(self, repo: Path) -> None:
        report = RunReport(limit=5)
        report.add_source("zenodo", SourceResult("zenodo", seen=5, changed=2,
                                                 skipped_unchanged=3))
        report.add_source("wdh", SourceResult("wdh", reachable=False,
                                              errors=["listing needs a token"]))
        report.cache_hits, report.cache_misses = 18, 2
        report.pending_extraction = 4
        report.records_total = 5
        report.add_notices([{"type": "displacement", "field": "resource_kind"}])
        report.dropped_dois = [{"doi": "10.5281/zenodo.9", "reason": "did-not-resolve"}]
        report.unmapped_licenses = [{"name": "x", "license_raw": "ask the author"}]

        payload = json.loads(report.write(root=repo).read_text())
        assert payload["sources"]["zenodo"]["changed"] == 2
        assert payload["sources"]["zenodo"]["skipped_unchanged"] == 3
        assert payload["unreachable_sources"] == ["wdh"]
        assert payload["cache"] == {"hits": 18, "misses": 2, "hit_rate": 0.9}
        assert payload["pending_extraction"] == 4
        assert payload["records"]["total"] == 5
        assert payload["notices"][0]["field"] == "resource_kind"
        assert payload["dropped_dois"][0]["reason"] == "did-not-resolve"
        assert payload["unmapped_licenses"][0]["license_raw"] == "ask the author"

    def test_a_disabled_source_is_not_called_unreachable(self, repo: Path) -> None:
        report = RunReport()
        report.add_source("wdh", SourceResult("wdh", enabled=False, reachable=True))
        assert json.loads(report.write(root=repo).read_text())["unreachable_sources"] == []

    def test_cache_hit_rate_with_no_calls(self) -> None:
        assert RunReport().cache_hit_rate == 0.0

    def test_json_is_sorted_and_newline_terminated(self) -> None:
        payload = RunReport().to_json()
        assert payload.endswith("\n")
        assert payload.index('"cache"') < payload.index('"started_at"')

    def test_read_returns_empty_when_there_has_never_been_a_run(self, repo: Path) -> None:
        assert read_run_report(root=repo) == {}


class TestCli:
    def test_validate_on_an_empty_catalogue(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--root", str(repo), "validate"]) == 0
        assert "OK — 0 record(s)" in capsys.readouterr().out

    def test_materialize_on_an_empty_catalogue(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--root", str(repo), "materialize"]) == 0
        assert "0 record(s)" in capsys.readouterr().out

    def test_run_survives_every_adapter_being_a_stub(self, repo: Path) -> None:
        """The whole point of the degradation rule: exit 0, report the failures."""
        assert main(["--root", str(repo), "run", "--limit", "5"]) == 0
        payload = read_run_report(root=repo)
        assert set(payload["sources"]) == {
            "zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh"
        }
        assert all(source["implemented"] is False for source in payload["sources"].values())
        assert payload["limit"] == 5
        assert payload["ok"] is True

    def test_run_writes_the_heartbeat_even_when_nothing_happens(self, repo: Path) -> None:
        main(["--root", str(repo), "run"])
        assert (repo / "state" / "last-run.json").exists()

    def test_run_defaults_to_a_limit_of_five(self, repo: Path) -> None:
        main(["--root", str(repo), "run"])
        assert read_run_report(root=repo)["limit"] == 5

    def test_run_can_target_one_source(self, repo: Path) -> None:
        main(["--root", str(repo), "run", "--source", "zenodo"])
        assert set(read_run_report(root=repo)["sources"]) == {"zenodo"}

    def test_extract_reports_the_stub_rather_than_crashing(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--root", str(repo), "extract"]) == 2
        assert "Track H" in capsys.readouterr().err

    def test_sources_lists_every_adapter(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--root", str(repo), "sources"]) == 0
        out = capsys.readouterr().out
        for name in ("zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh"):
            assert name in out

    def test_report_without_a_run(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--root", str(repo), "report"]) == 1
        assert "no run report yet" in capsys.readouterr().err
