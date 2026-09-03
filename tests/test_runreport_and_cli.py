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

from conftest import SEVEN_SOURCES, stub_sources

#: A source name deliberately absent from ``sources.yaml``. Used only if every
#: adapter has been implemented, so that a CLI test narrowed to "the sources
#: that cannot talk to anything" still has something inert to run.
NO_SUCH_SOURCE = "__no_adapter__"


def only_stubs() -> list[str]:
    """``["--source", name, ...]`` for every still-stubbed source.

    ``python -m harvest run`` with no ``--source`` walks all seven adapters,
    which for the tests that only care about the heartbeat is a lot of blocked
    requests and a lot of politeness throttling for nothing.
    """
    names = stub_sources() or [NO_SUCH_SOURCE]
    return [argument for name in names for argument in ("--source", name)]


class TestRunReport:
    def test_is_written_even_for_a_complete_no_op(self, repo: Path) -> None:
        path = RunReport().write(root=repo)
        assert path.exists()
        payload = json.loads(path.read_text())
        assert payload["started_at"] and payload["finished_at"]

    def test_shape(self, repo: Path) -> None:
        report = RunReport(max_records=5)
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

    def test_run_survives_every_adapter_failing(self, repo: Path) -> None:
        """The whole point of the degradation rule: exit 0, report the failures.

        Every source fails here, in one of the only two ways a source can fail:
        a stub is ``implemented: false``, and an implemented adapter — with the
        network blocked by ``conftest.no_network`` — is ``reachable: false``.
        Neither raises, neither writes an event, and the run still writes its
        heartbeat. As the remaining tracks land, sources move from the first
        column to the second and this test keeps meaning what it means.
        """
        stubs = set(stub_sources())
        assert main(["--root", str(repo), "run", "--max-records", "5"]) == 0
        payload = read_run_report(root=repo)
        assert set(payload["sources"]) == set(SEVEN_SOURCES)
        for name, source in payload["sources"].items():
            assert source["implemented"] is (name not in stubs), name
            if name not in stubs:
                assert source["reachable"] is False, name
            assert source["changed"] == 0, name
        assert payload["max_records"] == 5
        assert payload["ok"] is True

    def test_run_writes_the_heartbeat_even_when_nothing_happens(self, repo: Path) -> None:
        main(["--root", str(repo), "run", *only_stubs()])
        assert (repo / "state" / "last-run.json").exists()

    def test_run_defaults_to_the_default_max_records(self, repo: Path) -> None:
        from harvest import DEFAULT_MAX_RECORDS

        main(["--root", str(repo), "run", *only_stubs()])
        assert read_run_report(root=repo)["max_records"] == DEFAULT_MAX_RECORDS

    def test_run_can_target_one_source(self, repo: Path) -> None:
        main(["--root", str(repo), "run", "--source", "zenodo"])
        assert set(read_run_report(root=repo)["sources"]) == {"zenodo"}

    def test_extract_drains_an_empty_queue_and_exits_zero(self, repo: Path, capsys) -> None:  # noqa: ANN001
        """The runbook's contract: exit 0, print the count (RUN-drain §3)."""
        assert main(["--root", str(repo), "extract"]) == 0
        assert "extract: resolved 0 pending extraction(s)" in capsys.readouterr().out

    def test_sources_lists_every_adapter(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--root", str(repo), "sources"]) == 0
        out = capsys.readouterr().out
        for name in ("zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh"):
            assert name in out

    def test_report_without_a_run(self, repo: Path, capsys) -> None:  # noqa: ANN001
        assert main(["--root", str(repo), "report"]) == 1
        assert "no run report yet" in capsys.readouterr().err


class TestTheHeartbeatSurvivesTheRunFailing:
    """eventlog-02 / compliance-04 / CONTRACT rule 5, at the CLI boundary.

    ``state/last-run.json`` is the cron keepalive: GitHub disables a scheduled
    workflow after 60 days with no commits, and this file is what every run
    commits. It is also the site's freshness banner. So a run that dies in the
    middle is the worst possible time *not* to write it — a frozen heartbeat
    beside a cron that is still firing is the one failure nobody would notice,
    because the page still says a date and the workflow still shows green.

    Before this, a single truncated line in ``events/`` or one malformed
    ``annotations/*.yaml`` raised out of ``cmd_run`` before ``report.write()``.
    """

    def _corrupt_event_log(self, repo: Path) -> None:
        (repo / "events").mkdir(exist_ok=True)
        (repo / "events" / "doi-10-5281-zenodo-1.jsonl").write_text(
            '{"observed_at": "2026-01-01T00:00:00Z", "event_type": "scraped", '
            '"identity_key": "10.5281/zenodo.1", "source": {"title": "ok", '
            '"url": "https://example.org/1"}}\n'
            '{"observed_at": "2026-01-02T00:0\n',
            encoding="utf-8",
        )

    def test_a_truncated_event_line_does_not_stop_the_heartbeat(self, repo: Path) -> None:
        self._corrupt_event_log(repo)

        main(["--root", str(repo), "run", *only_stubs()])

        assert (repo / "state" / "last-run.json").exists()

    def test_the_skipped_line_is_named_in_the_run_report(self, repo: Path) -> None:
        """Skipping quietly would be worse than crashing. It must be loud."""
        self._corrupt_event_log(repo)

        main(["--root", str(repo), "run", *only_stubs()])

        notices = read_run_report(root=repo)["notices"]
        assert any(notice.get("type") == "event_log_problem" for notice in notices)

    def test_the_good_records_still_materialise(self, repo: Path) -> None:
        self._corrupt_event_log(repo)

        main(["--root", str(repo), "run", *only_stubs()])

        assert (repo / "records" / "doi-10-5281-zenodo-1.json").exists()

    def test_a_malformed_annotation_file_does_not_stop_the_heartbeat(
        self, repo: Path
    ) -> None:
        (repo / "annotations").mkdir(exist_ok=True)
        (repo / "annotations" / "broken.yaml").write_text(
            "identity_key: x\nannotations: [unclosed\n", encoding="utf-8"
        )

        assert main(["--root", str(repo), "run", *only_stubs()]) == 0
        assert (repo / "state" / "last-run.json").exists()

    def test_an_unexpected_crash_still_leaves_a_report_behind(
        self, repo: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        """Belt and braces: whatever breaks, the cron must not go dormant."""
        import harvest.cli as cli

        def explode(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("upstream changed everything at once")

        monkeypatch.setattr(cli, "_run_pipeline", explode)

        assert main(["--root", str(repo), "run", *only_stubs()]) == 1
        payload = read_run_report(root=repo)
        assert payload["ok"] is False
        assert any(notice.get("type") == "run_failed" for notice in payload["notices"])
