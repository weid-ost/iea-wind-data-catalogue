"""The link checker.

The behaviour that matters most here is a negative: a dead link never deletes,
withdraws or edits anything. Link rot is the failure mode a catalogue exists to
fight, and a checker that removed records on a 404 would be making it worse.
"""

from __future__ import annotations

import json
from pathlib import Path

from harvest.events import record_scrape
from harvest.linkcheck import (
    LinkCheckReport,
    LinkStatus,
    check_records,
    check_url,
    read_link_report,
    urls_for_record,
    write_link_report,
)
from harvest.materialize import materialize_all


class FakeResponse:
    def __init__(self, status_code: int | None, error: str | None = None):
        self.status_code = status_code
        self.error = error
        self.ok = error is None and status_code is not None and status_code < 400


class FakeHttp:
    """A stand-in for :class:`harvest.http.HarvestClient`. GET only."""

    def __init__(self, responses: dict[str, int] | None = None,
                 raise_for: set[str] | None = None):
        self.responses = responses or {}
        self.raise_for = raise_for or set()
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):  # noqa: ANN003
        self.calls.append(url)
        if url in self.raise_for:
            raise ConnectionError("name resolution failed")
        return FakeResponse(self.responses.get(url, 200))


class FakeHttpWithHead(FakeHttp):
    def __init__(self, *args, head_status: dict[str, int] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.head_status = head_status or {}
        self.heads: list[str] = []

    def head(self, url: str, **kwargs):  # noqa: ANN003
        self.heads.append(url)
        return FakeResponse(self.head_status.get(url, self.responses.get(url, 200)))


PACKAGE = {
    "name": "doi-10-5281-zenodo-8000008",
    "title": "Task 11 final technical report",
    "url": "https://iea-wind.org/task11/publications/",
    "extras": [
        {"key": "source_url", "value": "https://iea-wind.org/task11/publications/"},
        {"key": "source_urls",
         "value": json.dumps(["https://iea-wind.org/task11/publications/",
                              "https://zenodo.org/records/8000009"])},
        {"key": "local_links",
         "value": json.dumps([{"url": "https://example.org/related", "label": "Related"}])},
    ],
    "resources": [{"url": "https://iea-wind.org/wp-content/uploads/task11-final.pdf"}],
}


class TestUrlsForRecord:
    def test_it_finds_every_outbound_url_exactly_once(self) -> None:
        assert urls_for_record(PACKAGE) == [
            "https://iea-wind.org/task11/publications/",
            "https://zenodo.org/records/8000009",
            "https://example.org/related",
            "https://iea-wind.org/wp-content/uploads/task11-final.pdf",
        ]

    def test_a_record_with_no_links_is_no_requests(self) -> None:
        assert urls_for_record({"name": "x", "title": "x"}) == []

    def test_a_non_http_url_is_ignored(self) -> None:
        assert urls_for_record({"url": "ftp://example.org/x", "resources": []}) == []

    def test_malformed_extras_do_not_crash_it(self) -> None:
        package = {"extras": [{"key": "source_urls", "value": "not json"}]}
        assert urls_for_record(package) == []


class TestCheckUrl:
    def test_a_200_is_alive(self) -> None:
        status = check_url("https://example.org/", FakeHttp())
        assert status.ok and status.status_code == 200 and not status.gone

    def test_a_404_is_gone(self) -> None:
        status = check_url("https://example.org/x", FakeHttp({"https://example.org/x": 404}))
        assert not status.ok and status.gone

    def test_a_503_is_dead_but_not_gone(self) -> None:
        status = check_url("https://example.org/x", FakeHttp({"https://example.org/x": 503}))
        assert not status.ok and not status.gone

    def test_a_transport_error_is_a_result_not_an_exception(self) -> None:
        status = check_url("https://example.org/x",
                           FakeHttp(raise_for={"https://example.org/x"}))
        assert not status.ok and "name resolution" in (status.error or "")

    def test_robots_disallowed_is_skipped_not_dead(self) -> None:
        class Robots:
            def get(self, url, **kwargs):  # noqa: ANN003
                return FakeResponse(None, error="disallowed-by-robots")

        status = check_url("https://example.org/x", Robots())
        assert status.skipped and not status.ok

    def test_head_is_preferred_where_the_client_offers_it(self) -> None:
        client = FakeHttpWithHead()
        status = check_url("https://example.org/", client)
        assert status.method == "HEAD"
        assert client.heads == ["https://example.org/"] and client.calls == []

    def test_a_server_that_refuses_head_falls_back_to_get(self) -> None:
        client = FakeHttpWithHead(head_status={"https://example.org/": 405})
        status = check_url("https://example.org/", client)
        assert status.method == "GET" and status.ok
        assert client.calls == ["https://example.org/"]


class TestCheckRecords:
    def _repo_with_a_record(self, repo: Path, events_dir: Path) -> None:
        record_scrape(
            identity_key="10.5281/zenodo.8000008",
            source_system="ieawind",
            source_id="task11/publications#7",
            source_key="9a8b7c6d5e4f3021",
            source={
                "title": "Task 11 final technical report",
                "doi": "10.5281/zenodo.8000008",
                "url": "https://iea-wind.org/task11/publications/",
                "source_urls": ["https://iea-wind.org/task11/publications/",
                                "https://zenodo.org/records/8000009"],
                "license_id": "cc-by",
                "resources": [
                    {"url": "https://iea-wind.org/wp-content/uploads/task11-final.pdf"}
                ],
            },
            events_dir=events_dir,
            observed_at="2026-08-24T03:14:00Z",
        )
        materialize_all(events_dir, repo / "records", root=repo)

    def test_iea12_a_dead_page_is_reported_and_the_record_is_untouched(
        self, repo: Path, events_dir: Path
    ) -> None:
        self._repo_with_a_record(repo, events_dir)
        before = {p.name: p.read_text() for p in (repo / "records").glob("*.json")}

        client = FakeHttp({
            "https://iea-wind.org/task11/publications/": 404,
            "https://iea-wind.org/wp-content/uploads/task11-final.pdf": 404,
        })
        report = check_records(repo / "records", client, root=repo)

        assert sorted(report.dead_urls) == [
            "https://iea-wind.org/task11/publications/",
            "https://iea-wind.org/wp-content/uploads/task11-final.pdf",
        ]
        assert report.dead_by_record()["doi-10-5281-zenodo-8000008"]
        assert report.unreachable_hosts() == ["iea-wind.org"]

        after = {p.name: p.read_text() for p in (repo / "records").glob("*.json")}
        assert after == before, "a link checker never edits, withdraws or deletes a record"

    def test_a_healthy_catalogue_reports_nothing(self, repo: Path, events_dir: Path) -> None:
        self._repo_with_a_record(repo, events_dir)
        report = check_records(repo / "records", FakeHttp(), root=repo)
        assert report.dead == [] and report.as_notices() == []

    def test_each_url_is_requested_once_however_many_records_share_it(
        self, repo: Path, events_dir: Path
    ) -> None:
        self._repo_with_a_record(repo, events_dir)
        client = FakeHttp()
        check_records(repo / "records", client, root=repo)
        assert len(client.calls) == len(set(client.calls))

    def test_the_limit_caps_the_records_examined(self, repo: Path, events_dir: Path) -> None:
        self._repo_with_a_record(repo, events_dir)
        client = FakeHttp()
        report = check_records(repo / "records", client, root=repo, limit=0)
        assert client.calls == [] and report.records == {}

    def test_a_partially_dead_host_is_not_called_unreachable(self) -> None:
        report = LinkCheckReport(
            checked={
                "https://iea-wind.org/a": LinkStatus("https://iea-wind.org/a", 404),
                "https://iea-wind.org/b": LinkStatus("https://iea-wind.org/b", 200, ok=True),
            },
            records={"r": ["https://iea-wind.org/a", "https://iea-wind.org/b"]},
        )
        assert report.unreachable_hosts() == []
        assert report.dead_by_record() == {"r": ["https://iea-wind.org/a"]}

    def test_notices_say_what_happens_next(self) -> None:
        report = LinkCheckReport(
            checked={"https://x.org/a": LinkStatus("https://x.org/a", 410)},
            records={"r": ["https://x.org/a"]},
        )
        notices = report.as_notices()
        assert notices[0]["type"] == "dead_link" and notices[0]["gone"] is True
        assert "retained" in notices[0]["action"]
        assert notices[1]["type"] == "source_unreachable"

    def test_an_unreadable_record_file_is_skipped_not_fatal(self, repo: Path) -> None:
        (repo / "records").mkdir(exist_ok=True)
        (repo / "records" / "broken.json").write_text("{not json", encoding="utf-8")
        assert check_records(repo / "records", FakeHttp(), root=repo).records == {}


class TestTheStateFile:
    def test_it_round_trips(self, repo: Path) -> None:
        report = LinkCheckReport(
            checked={"https://x.org/a": LinkStatus("https://x.org/a", 404,
                                                   checked_at="2026-08-31T00:00:00Z")},
            records={"r": ["https://x.org/a"]},
            started_at="2026-08-31T00:00:00Z",
            finished_at="2026-08-31T00:00:01Z",
        )
        path = write_link_report(report, root=repo)
        assert path.name == "link-check.json"
        payload = read_link_report(root=repo)
        assert payload["dead"][0]["status_code"] == 404
        assert payload["unreachable_hosts"] == ["x.org"]

    def test_reading_before_any_pass_is_empty(self, repo: Path) -> None:
        assert read_link_report(root=repo) == {}
