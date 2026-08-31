"""The iea-wind.org Tier-3 adapter — fixtures ``iea-01`` .. ``iea-12``.

The order of operations is the whole safety argument, so it is what is tested:

    classify deterministically  →  regex the DOIs  →  resolve every one
                                →  build the record FROM THE RESOLVER

A news post never reaches step two however many DOIs it quotes (``iea-09``).
An unresolvable DOI never reaches step four, and its drop is logged rather than
swallowed (``iea-05``). Nothing on the page is ever trusted as metadata: the
page's only lasting contribution is the ``iea_task`` attribution (``iea-01``).

Every page here is a real capture or an explicitly-marked invention, and the two
model-classified pages replay from the **committed** cache, so the whole suite
runs with no network and no key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest import config
from harvest.adapters.base import SourceConfig, SourceUnreachable, payload_hash, run_adapter
from harvest.adapters.ieawind import (
    IeaWindAdapter,
    classify_page,
    map_crossref,
    map_datacite,
    page_tasks,
)
from harvest.doi import extract_dois
from harvest.events import read_events
from harvest.extract import PROMPT_VERSION, content_hash, lookup_cache, main_text, read_pending
from harvest.http import FetchResult
from harvest.identity import slug_for_identity
from harvest.models import RawObservation

FIXTURES = config.fixtures_dir() / "ieawind"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def html(fixture: dict) -> str:
    return (FIXTURES / fixture["raw"]).read_text(encoding="utf-8")


def page_fixtures() -> list[dict]:
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture["fixture_kind"] == "page":
            out.append(fixture)
    return out


def namespace_fixtures() -> list[dict]:
    out = []
    for path in sorted(FIXTURES.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        if fixture["fixture_kind"] == "source_namespace":
            out.append(fixture)
    return out


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class PageServer:
    """Serves captured pages by URL. Anything not configured 404s (``iea-12``)."""

    def __init__(self, pages: dict[str, object]):
        self.pages = pages
        self.requested: list[str] = []
        self.closed = False

    def get(self, url: str, **kwargs) -> FetchResult:
        self.requested.append(url)
        value = self.pages.get(url)
        if value is None:
            return FetchResult(url=url, status_code=404, changed=True, text="")
        if isinstance(value, int):
            return FetchResult(url=url, status_code=value, changed=True, text="")
        return FetchResult(url=url, status_code=200, changed=True, text=str(value))

    def close(self) -> None:
        self.closed = True


class Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class Resolver:
    """DataCite/Crossref stand-in. Only the DOIs it is told about resolve."""

    def __init__(
        self,
        datacite: dict[str, dict] | None = None,
        crossref: dict[str, dict] | None = None,
        search: list[dict] | None = None,
    ):
        self.datacite = datacite or {}
        self.crossref = crossref or {}
        self.search = search or []
        self.searched: list[str] = []
        self.closed = False

    def get(self, url: str, params=None, **kwargs):
        if url.startswith("https://api.crossref.org/works?") or params:
            self.searched.append(str((params or {}).get("query.bibliographic")))
            wanted = str((params or {}).get("query.bibliographic") or "")
            items = [item for item in self.search if item.get("_query") in (None, wanted)]
            return Response(200, {"message": {"items": items}})
        for prefix, table in (
            ("https://api.datacite.org/dois/", self.datacite),
            ("https://api.crossref.org/works/", self.crossref),
        ):
            if url.startswith(prefix):
                doi = url[len(prefix):]
                if doi in table:
                    return Response(200, table[doi])
                return Response(404, {})
        return Response(404, {})

    def close(self) -> None:
        self.closed = True


def adapter_for(pages, resolver=None, **options) -> IeaWindAdapter:
    task_pages = options.pop("task_pages", [])
    source_config = SourceConfig.from_mapping(
        "ieawind",
        {"tier": 3, "max_records": 5, "task_pages": task_pages,
         "follow_publication_links": False, **options},
    )
    return IeaWindAdapter(config=source_config, client=pages, resolver=resolver or Resolver())


# ---------------------------------------------------------------------------
# The page fixtures: content, sweep, classification (iea-02 .. iea-12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", page_fixtures(), ids=lambda f: f["fixture_id"])
class TestEveryPageFixture:
    def test_main_content_reduces_to_the_pinned_hash(self, fixture: dict) -> None:
        content = main_text(html(fixture))
        assert len(content) == fixture["expected_content_chars"]
        assert content_hash(content) == fixture["expected_content_hash"], (
            "the content hash IS the source key; a drift here is a spurious re-scrape"
        )

    def test_no_markup_survives_into_the_model_input(self, fixture: dict) -> None:
        content = main_text(html(fixture))
        for forbidden in ("<script", "<nav", "<footer", "</div>", "javascript:"):
            assert forbidden not in content

    def test_the_adapter_agrees_with_the_pinned_expectations(self, fixture: dict) -> None:
        url = fixture["page_url"]
        status = fixture.get("serve_status")
        served = status if status else html(fixture)
        server = PageServer({url: served})
        adapter = adapter_for(server)

        page = adapter._read(url, fixture.get("iea_task"), trusted=fixture["trusted"])

        if status and status >= 400:
            assert page is None, "an unreachable page yields nothing"
            assert [n["type"] for n in adapter.notices] == fixture["expected_notice_types"]
            return

        assert page is not None
        assert page["dois"] == fixture["expected_dois"], (
            "expected_dois is what the ADAPTER sweeps, which is empty for a page "
            "it declined to classify as record-bearing"
        )
        assert page["record_bearing"] is fixture.get("expected_record_bearing", True)
        expected_classification = fixture["expected_classification"]
        assert page["classification"].kind == expected_classification["kind"]
        assert page["classification"].method == expected_classification["method"]
        if "expected_iea_task" in fixture:
            assert page["iea_task"] == fixture["expected_iea_task"]
        if fixture.get("expected_notices") == []:
            assert adapter.notices == []
        if "expected_notice_types" in fixture:
            # Reading a page can only raise the notices that reading a page
            # raises; the citation-stage ones (``iea-07``) come later and are
            # pinned exactly by that fixture's own test.
            assert {n["type"] for n in adapter.notices} <= set(fixture["expected_notice_types"])


# ---------------------------------------------------------------------------
# iea-01 — the record is built from the resolver, never from the page
# ---------------------------------------------------------------------------


class TestIea01Canonical:
    fixture = load("iea-01-canonical")

    def test_map_reproduces_the_expected_source_namespace(self) -> None:
        payload = json.loads((FIXTURES / self.fixture["raw"]).read_text(encoding="utf-8"))
        raw = RawObservation(
            source_system="ieawind",
            source_id=self.fixture["source_id"],
            source_key=self.fixture["source_key"],
            url=self.fixture["page_url"],
            payload=payload,
        )
        mapped = IeaWindAdapter().map(raw)
        assert mapped.identity_key == self.fixture["identity_key"]
        assert mapped.source_key == self.fixture["source_key"]
        assert mapped.source.model_dump(exclude_none=True) == self.fixture["source"]
        assert {
            key: value.model_dump(exclude_none=True) for key, value in mapped.provenance.items()
        } == self.fixture["provenance"]

    def test_the_slug_follows_the_doi(self) -> None:
        assert slug_for_identity(self.fixture["identity_key"]) == self.fixture["expected_slug"]

    def test_the_source_key_is_the_page_content_hash(self) -> None:
        content = main_text((FIXTURES / "raw" / "iea-01-canonical.html").read_text(encoding="utf-8"))
        assert content_hash(content) == self.fixture["source_key"]

    def test_the_page_supplies_the_task_and_a_url_and_nothing_else(self) -> None:
        source = self.fixture["source"]
        assert source["iea_task"] == ["task-43"]
        assert self.fixture["page_url"] in source["source_urls"]
        assert source["title"] == "Knowledge engineering for wind energy"
        assert source["publisher"] == "Copernicus GmbH", "publisher comes from Crossref"
        assert source["extra"]["resolved_by"] == "crossref"

    def test_no_field_is_attributed_to_a_model(self) -> None:
        methods = {p["extraction_method"] for p in self.fixture["provenance"].values()}
        assert methods == {"api", "pattern"}, "a deterministic page never spends a model call"

    def test_the_real_page_sweeps_thirty_dois(self) -> None:
        content = main_text((FIXTURES / "raw" / "iea-01-canonical.html").read_text(encoding="utf-8"))
        dois = extract_dois(content)
        assert len(dois) == self.fixture["expected_doi_count"]
        assert dois[: len(self.fixture["expected_dois_head"])] == self.fixture["expected_dois_head"]


# ---------------------------------------------------------------------------
# iea-02 .. iea-05 — the DOI sweep edge cases, and resolve-or-drop
# ---------------------------------------------------------------------------


class TestTheDoiSweep:
    def test_iea02_a_trailing_full_stop_is_not_part_of_the_doi(self) -> None:
        fixture = load("iea-02-doi-punctuation")
        dois = extract_dois(main_text(html(fixture)))
        assert dois == fixture["expected_dois"]
        for absent in fixture["assert_absent_dois"]:
            assert absent not in dois, "the classic regex bug"

    def test_iea03_five_spellings_are_one_identity(self) -> None:
        fixture = load("iea-03-doi-prefixed")
        content = main_text(html(fixture))
        assert extract_dois(content) == fixture["expected_dois"]
        assert len(fixture["expected_dois"]) == 1
        for spelling in ("doi:", "https://doi.org/", "dx.doi.org/", "info:doi/"):
            assert spelling.lower() in content.lower() or spelling in html(fixture).lower()

    def test_iea04_a_wrapped_doi_is_rejoined_and_a_truncated_one_is_dropped(self) -> None:
        fixture = load("iea-04-doi-linebreak")
        dois = extract_dois(main_text(html(fixture)))
        assert dois == fixture["expected_dois"]
        assert set(fixture["expected_resolved"]) | set(fixture["expected_dropped"]) == set(dois)

    def test_iea05_an_unresolvable_doi_is_dropped_and_logged(self) -> None:
        fixture = load("iea-05-invalid-doi")
        url = fixture["page_url"]
        good, bad = fixture["expected_resolved"][0], fixture["expected_dropped"][0]
        resolver = Resolver(crossref={good: {"message": {"DOI": good, "title": ["ok"]}}})
        adapter = adapter_for(
            PageServer({url: html(fixture)}),
            resolver,
            task_pages=[{"iea_task": fixture["iea_task"], "url": url}],
        )

        observations = list(adapter.harvest(limit=5))

        assert [o.source_id for o in observations] == [good]
        assert [drop["doi"] for drop in adapter.drop_log.as_notices()] == [bad]
        assert adapter.drop_log.as_notices()[0]["context"] == url
        assert adapter.drop_log.as_notices()[0]["reason"] == "did-not-resolve"

    def test_a_dropped_doi_is_never_silent(self) -> None:
        fixture = load("iea-05-invalid-doi")
        url = fixture["page_url"]
        adapter = adapter_for(
            PageServer({url: html(fixture)}),
            Resolver(),  # nothing resolves at all
            task_pages=[{"iea_task": fixture["iea_task"], "url": url}],
        )
        assert list(adapter.harvest(limit=5)) == []
        assert len(adapter.drop_log) == 2, "both DOIs dropped, both logged"


# ---------------------------------------------------------------------------
# iea-06 — one DOI, two task pages, one record
# ---------------------------------------------------------------------------


class TestIea06MultiTask:
    fixture = load("iea-06-multi-task")

    def test_map_unions_the_tasks(self) -> None:
        payload = json.loads((FIXTURES / self.fixture["raw"]).read_text(encoding="utf-8"))
        raw = RawObservation(
            source_system="ieawind",
            source_id=self.fixture["source_id"],
            source_key=self.fixture["source_key"],
            payload=payload,
        )
        mapped = IeaWindAdapter().map(raw)
        assert mapped.identity_key == self.fixture["identity_key"]
        assert mapped.source.iea_task == ["task-43", "task-49"]
        assert mapped.source.model_dump(exclude_none=True) == self.fixture["source"]
        assert len(mapped.source.extra["cited_on"]) == 2

    def test_the_source_key_covers_both_pages(self) -> None:
        payload = json.loads((FIXTURES / self.fixture["raw"]).read_text(encoding="utf-8"))
        hashes = sorted({page["content_hash"] for page in payload["pages"]})
        assert len(hashes) == 2
        assert payload_hash(hashes) == self.fixture["source_key"], (
            "the record must re-emit when EITHER citing page moves"
        )

    def test_harvest_unions_within_one_run(self) -> None:
        doi = self.fixture["identity_key"]
        payload = json.loads((FIXTURES / self.fixture["raw"]).read_text(encoding="utf-8"))
        pages = {
            page["url"]: (FIXTURES / "raw" / f"iea-06-multi-task-{task}.html").read_text(
                encoding="utf-8"
            )
            for page, task in zip(payload["pages"], ("task43", "task49"))
        }
        adapter = adapter_for(
            PageServer(pages),
            Resolver(datacite={doi: payload["resolution"]}),
            task_pages=[
                {"iea_task": "task-43", "url": payload["pages"][0]["url"]},
                {"iea_task": "task-49", "url": payload["pages"][1]["url"]},
            ],
        )

        observations = list(adapter.harvest(limit=5))

        assert len(observations) == 1, "one DOI on two pages is ONE record"
        assert observations[0].payload["iea_task"] == ["task-43", "task-49"]
        assert len(observations[0].payload["pages"]) == 2
        assert observations[0].source_key == self.fixture["source_key"]


# ---------------------------------------------------------------------------
# iea-07 — a citation with no DOI
# ---------------------------------------------------------------------------


class TestIea07TitleSearch:
    fixture = load("iea-07-no-doi-citation")

    def test_the_committed_cache_supplies_the_undoi_d_titles(self) -> None:
        content = main_text(html(self.fixture))
        cached, _key = lookup_cache(content, PROMPT_VERSION, "openai/gpt-4o-mini")
        assert cached is not None, "the seeded cache entry must be committed"
        assert cached.model == "claude-fable-5"
        titles = [record.title for record in cached.page().records if not record.doi]
        assert titles == self.fixture["expected_titles_without_doi"]

    def test_an_exact_title_match_is_accepted_and_anything_else_is_flagged(self) -> None:
        url = self.fixture["page_url"]
        accepted_title, accepted_doi = next(iter(self.fixture["expected_title_search_accepts"].items()))
        resolver = Resolver(
            crossref={accepted_doi: {"message": {"DOI": accepted_doi, "title": [accepted_title]}}},
            search=[{"_query": accepted_title, "DOI": accepted_doi, "title": [accepted_title]}],
        )
        adapter = adapter_for(
            PageServer({url: html(self.fixture)}),
            resolver,
            task_pages=[{"iea_task": self.fixture["iea_task"], "url": url}],
        )

        observations = list(adapter.harvest(limit=5))

        assert [o.source_id for o in observations] == [accepted_doi]
        assert observations[0].payload["identifier_source"] == "crossref-title-search"
        assert [n["type"] for n in adapter.notices] == self.fixture["expected_notice_types"]
        assert adapter.notices[0]["title"] == self.fixture["expected_titles_without_doi"][1]

    def test_a_near_match_is_never_accepted(self) -> None:
        url = self.fixture["page_url"]
        title = self.fixture["expected_titles_without_doi"][0]
        resolver = Resolver(
            crossref={"10.9999/near": {"message": {"DOI": "10.9999/near"}}},
            # A plausible but different title: Crossref's top hit for a fuzzy query.
            search=[{"DOI": "10.9999/near", "title": [title + " systems: a review"]}],
        )
        adapter = adapter_for(
            PageServer({url: html(self.fixture)}),
            resolver,
            task_pages=[{"iea_task": self.fixture["iea_task"], "url": url}],
        )

        assert list(adapter.harvest(limit=5)) == []
        assert {n["type"] for n in adapter.notices} == {"unresolved_citation"}

    def test_a_search_result_is_still_put_through_resolve_or_drop(self) -> None:
        url = self.fixture["page_url"]
        title, doi = next(iter(self.fixture["expected_title_search_accepts"].items()))
        resolver = Resolver(search=[{"DOI": doi, "title": [title]}])  # search hit, no resolution
        adapter = adapter_for(
            PageServer({url: html(self.fixture)}),
            resolver,
            task_pages=[{"iea_task": self.fixture["iea_task"], "url": url}],
        )
        assert list(adapter.harvest(limit=5)) == []
        assert [drop["doi"] for drop in adapter.drop_log.as_notices()] == [doi]


# ---------------------------------------------------------------------------
# iea-08 — task renumbering
# ---------------------------------------------------------------------------


class TestIea08Renumbering:
    def test_the_old_number_resolves_to_the_new_group(self) -> None:
        assert config.canonical_group("task-34") == "task-59"
        assert config.canonical_group("task-19") == "task-54"
        assert config.canonical_group("task-59") == "task-59"

    def test_the_page_carries_one_task_not_two(self) -> None:
        fixture = load("iea-08-renumbered-task")
        content = main_text(html(fixture))
        assert "Task 34" in content, "the page really does mention the old number"
        assert page_tasks("task-59", content) == fixture["expected_iea_task"] == ["task-59"]

    def test_the_alias_spelling_is_accepted_as_the_pages_own_task(self) -> None:
        fixture = load("iea-08-renumbered-task")
        assert page_tasks("task-34", main_text(html(fixture))) == ["task-59"]

    def test_a_number_mentioned_in_prose_never_attributes_a_record_elsewhere(self) -> None:
        content = "Task 43 collaborates closely with Task 52 and Task 25 on lidar."
        assert page_tasks("task-43", content) == ["task-43"], (
            "a false task chip is worse than a missing one"
        )

    def test_no_group_is_invented_for_a_retired_number(self) -> None:
        assert "task-34" not in config.group_names()


# ---------------------------------------------------------------------------
# iea-09 — a news post is not a publication list
# ---------------------------------------------------------------------------


class TestIea09NotARecord:
    def test_the_news_post_is_caught_by_pattern_before_any_model(self) -> None:
        fixture = load("iea-09-news-page")
        content = main_text(html(fixture))
        classification = classify_page(fixture["page_url"], content, trusted=fixture["trusted"])
        assert classification is not None, "no model call is needed or made"
        assert classification.kind == "news"
        assert classification.record_bearing is False

    def test_its_real_dois_are_present_but_never_swept(self) -> None:
        fixture = load("iea-09-news-page")
        content = main_text(html(fixture))
        assert extract_dois(content) == fixture["dois_present_on_page"]

        url = fixture["page_url"]
        adapter = adapter_for(
            PageServer({url: html(fixture)}),
            Resolver(crossref={d: {"message": {"DOI": d}} for d in fixture["dois_present_on_page"]}),
            task_pages=[{"iea_task": fixture["iea_task"], "url": url}],
        )
        assert list(adapter.harvest(limit=5)) == [], (
            "a news post never becomes a record, however many DOIs it quotes"
        )
        assert [n["type"] for n in adapter.notices] == ["page_not_record_bearing"]

    def test_the_ambiguous_workshop_page_is_classified_from_the_committed_cache(self) -> None:
        """A *discovered* page (``trusted: false``) — the only model path there is."""
        fixture = load("iea-09-workshop-page")
        content = main_text(html(fixture))
        assert classify_page(fixture["page_url"], content, trusted=False) is None, (
            "the deterministic heuristics must decline — this is the ONLY model path"
        )

        url = fixture["page_url"]
        adapter = adapter_for(
            PageServer({url: html(fixture)}),
            Resolver(datacite={d: {"data": {}} for d in fixture["dois_present_on_page"]}),
        )
        page = adapter._read(url, fixture["iea_task"], trusted=False)

        assert page["classification"].kind == fixture["expected_classification"]["kind"] == "event"
        assert page["classification"].method == "llm"
        assert fixture["expected_cache_model"] == "claude-fable-5"
        assert page["record_bearing"] is False
        assert page["dois"] == [], "an event page's DOIs are never swept"
        assert extract_dois(content) == fixture["dois_present_on_page"], (
            "…even though the page really does cite one"
        )

    def test_a_configured_task_page_is_trusted_but_a_discovered_one_is_not(self) -> None:
        fixture = load("iea-09-workshop-page")
        content = main_text(html(fixture))
        assert classify_page(fixture["page_url"], content, trusted=True).kind == "task-overview"
        assert classify_page(fixture["page_url"], content, trusted=False) is None, (
            "reachable is not the same as record-bearing"
        )

    def test_x07_no_cache_and_no_model_fails_safe_to_no_records(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fixture ``x-07`` through the adapter: queued, skipped, run succeeds."""
        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        fixture = load("iea-09-workshop-page")
        url = fixture["page_url"]
        adapter = adapter_for(
            PageServer({url: html(fixture)}),
            Resolver(datacite={d: {"data": {}} for d in fixture["dois_present_on_page"]}),
        )

        page = adapter._read(url, fixture["iea_task"], trusted=False)

        assert page["record_bearing"] is False, "unclassified means NO records"
        assert page["dois"] == []
        assert page["classification"].method == "pattern"
        assert page["classification"].confidence == 0.0

        queued = read_pending(repo / "state")
        assert len(queued) == 1
        assert queued[0]["url"] == url
        assert "classification unavailable" in queued[0]["reason"]
        assert {n["type"] for n in adapter.notices} == {
            "extraction_queued", "page_not_record_bearing"
        }


class TestAPinnedExtraction:
    """Plan §4.3, through the adapter: the pin holds and the notice fires."""

    def _pin(self, repo: Path, url: str, pinned_against: str) -> None:
        from harvest.extract import ExtractionResult, cache_key, write_cache

        write_cache(
            ExtractionResult(
                key=cache_key(pinned_against),
                model="curator:tom",
                prompt_version=PROMPT_VERSION,
                content_sha256="irrelevant",
                extracted_at="2026-08-31T22:15:04Z",
                fields={
                    "page_kind": "publication-list",
                    "is_record_bearing": True,
                    "confidence": 1.0,
                    "records": [],
                },
                pinned=True,
                pin_source_key=content_hash(pinned_against),
                pin_url=url,
            ),
            repo / "cache",
        )

    def test_a_redesigned_page_keeps_the_human_judgement_and_raises_a_notice(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        fixture = load("iea-09-workshop-page")
        url = fixture["page_url"]
        served = html(fixture)
        # The pin was made against the page as it was; the served page differs.
        self._pin(repo, url, main_text(served) + "\nA paragraph added later.")

        adapter = adapter_for(PageServer({url: served}), Resolver())
        page = adapter._read(url, fixture["iea_task"], trusted=False)

        assert page["classification"].kind == "publication-list", "the pin held"
        assert page["classification"].method == "llm"
        notices = [n for n in adapter.notices if n["type"] == "pin_notice"]
        assert len(notices) == 1
        assert notices[0]["content_hash"] == fixture["expected_content_hash"]
        assert notices[0]["pin_source_key"] != fixture["expected_content_hash"]

    def test_an_unmoved_page_raises_no_pin_notice(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HARVEST_ROOT", str(repo))
        fixture = load("iea-09-workshop-page")
        url = fixture["page_url"]
        served = html(fixture)
        self._pin(repo, url, main_text(served))

        adapter = adapter_for(PageServer({url: served}), Resolver())
        adapter._read(url, fixture["iea_task"], trusted=False)

        assert [n for n in adapter.notices if n["type"] == "pin_notice"] == []


# ---------------------------------------------------------------------------
# iea-10 — boilerplate
# ---------------------------------------------------------------------------


class TestIea10Boilerplate:
    fixture = load("iea-10-boilerplate")

    def test_the_page_really_is_mostly_boilerplate(self) -> None:
        raw = html(self.fixture)
        assert len(raw) == self.fixture["raw_bytes"]
        assert raw.count("<script") >= 20 and "<nav" in raw and "<footer" in raw

    def test_reduction_is_at_least_fiftyfold(self) -> None:
        content = main_text(html(self.fixture))
        ratio = len(html(self.fixture)) / max(len(content), 1)
        assert ratio >= self.fixture["expected_min_reduction_ratio"]

    def test_body_prose_survives_and_furniture_does_not(self) -> None:
        content = main_text(html(self.fixture))
        for present in self.fixture["expected_present_in_text"]:
            assert present in content
        for absent in self.fixture["expected_absent_from_text"]:
            assert absent not in content

    def test_a_task_overview_is_not_a_publication_list(self) -> None:
        """The prose "our published journal papers … are listed here" is not a table."""
        content = main_text(html(self.fixture))
        assert "journal papers" in content.lower()
        classification = classify_page(self.fixture["page_url"], content, trusted=True)
        assert classification.kind == self.fixture["expected_classification"]["kind"]
        assert classification.method == self.fixture["expected_classification"]["method"]


# ---------------------------------------------------------------------------
# iea-11 / iea-12 — recorded gaps and link rot
# ---------------------------------------------------------------------------


class TestIea11PdfOnly:
    def test_the_gap_is_recorded_not_silently_skipped(self) -> None:
        fixture = load("iea-11-pdf-only")
        url = fixture["page_url"]
        adapter = adapter_for(
            PageServer({url: html(fixture)}),
            task_pages=[{"iea_task": fixture["iea_task"], "url": url}],
        )
        assert list(adapter.harvest(limit=5)) == []
        gaps = [n for n in adapter.notices if n["type"] == "coverage_gap_pdf_only"]
        assert len(gaps) == 1
        assert sorted(gaps[0]["pdfs"]) == sorted(fixture["expected_gap_pdfs"])
        assert "out of scope for v1" in gaps[0]["detail"]

    def test_pdf_publication_lists_are_declared_out_of_scope(self) -> None:
        assert config.load_sources()["ieawind"]["pdf_publication_lists"] is False


class TestIea12DeadPage:
    fixture = load("iea-12-dead-page")

    def test_one_dead_page_does_not_stop_the_crawl(self) -> None:
        live = load("iea-05-invalid-doi")
        dead_url, live_url = self.fixture["page_url"], live["page_url"]
        good = live["expected_resolved"][0]
        adapter = adapter_for(
            PageServer({dead_url: 404, live_url: html(live)}),
            Resolver(crossref={good: {"message": {"DOI": good}}}),
            task_pages=[
                {"iea_task": self.fixture["iea_task"], "url": dead_url},
                {"iea_task": live["iea_task"], "url": live_url},
            ],
        )
        observations = list(adapter.harvest(limit=5))
        assert [o.source_id for o in observations] == [good]
        assert [n["type"] for n in adapter.notices][0] == "page_unreachable"

    def test_only_a_total_failure_declares_the_source_unreachable(self) -> None:
        adapter = adapter_for(
            PageServer({}),
            task_pages=[
                {"iea_task": "task-65", "url": "https://iea-wind.org/task11-retired/"},
                {"iea_task": "task-43", "url": "https://iea-wind.org/task43/"},
            ],
        )
        with pytest.raises(SourceUnreachable) as caught:
            list(adapter.harvest(limit=5))
        assert "none of the 2 configured" in str(caught.value)

    def test_existing_records_are_untouched_by_a_dead_page(
        self, repo: Path, events_dir: Path
    ) -> None:
        records = repo / "records"
        (records / "doi-10-5194-wes-9-883-2024.json").write_text("{}", encoding="utf-8")
        before = (records / "doi-10-5194-wes-9-883-2024.json").read_bytes()

        adapter = adapter_for(
            PageServer({}),
            task_pages=[{"iea_task": "task-65", "url": self.fixture["page_url"]}],
        )
        result = run_adapter(adapter, limit=5, events_dir=events_dir)

        assert result.reachable is False and result.changed == 0
        assert list(events_dir.glob("*.jsonl")) == []
        assert (records / "doi-10-5194-wes-9-883-2024.json").read_bytes() == before

    def test_no_task_pages_configured_is_also_unreachable(self) -> None:
        adapter = adapter_for(PageServer({}), task_pages=[])
        with pytest.raises(SourceUnreachable):
            list(adapter.harvest(limit=5))


# ---------------------------------------------------------------------------
# The adapter contract
# ---------------------------------------------------------------------------


class TestTheContract:
    def test_it_is_registered_as_a_tier_three_source(self) -> None:
        from harvest.adapters.base import get_adapter

        assert get_adapter("ieawind") is IeaWindAdapter
        assert IeaWindAdapter.source_name == "ieawind"
        assert IeaWindAdapter.tier == 3
        assert "content hash" in IeaWindAdapter.source_key_semantics

    def test_the_five_record_cap_is_honoured(self) -> None:
        fixture = load("iea-01-canonical")
        url = fixture["page_url"]
        page_html = (FIXTURES / "raw" / "iea-01-canonical.html").read_text(encoding="utf-8")
        dois = extract_dois(main_text(page_html))
        assert len(dois) == 30, "the real page cites thirty"
        adapter = adapter_for(
            PageServer({url: page_html}),
            Resolver(datacite={doi: {"data": {"attributes": {}}} for doi in dois}),
            task_pages=[{"iea_task": "task-43", "url": url}],
        )
        assert len(list(adapter.harvest(limit=5))) == 5, "the limit is five"

    def test_map_is_pure(self) -> None:
        """No network, no clock, no filesystem: the same input, twice, identically."""
        fixture = load("iea-01-canonical")
        payload = json.loads((FIXTURES / fixture["raw"]).read_text(encoding="utf-8"))
        raw = RawObservation(
            source_system="ieawind",
            source_id=fixture["source_id"],
            source_key=fixture["source_key"],
            fetched_at="2026-08-31T00:00:00Z",
            payload=payload,
        )
        first = IeaWindAdapter().map(raw)
        second = IeaWindAdapter().map(raw)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert first.fetched_at == "2026-08-31T00:00:00Z", "'now' is raw.fetched_at"

    def test_map_refuses_an_unknown_resolver_agency(self) -> None:
        raw = RawObservation(
            source_system="ieawind", source_id="10.1/x", source_key="k",
            payload={"doi": "10.1/x", "agency": "vibes", "resolution": {}},
        )
        with pytest.raises(ValueError, match="unknown resolver agency"):
            IeaWindAdapter().map(raw)

    def test_unchanged_content_writes_no_event(self, events_dir: Path) -> None:
        fixture = load("iea-05-invalid-doi")
        url = fixture["page_url"]
        good = fixture["expected_resolved"][0]

        def once() -> None:
            adapter = adapter_for(
                PageServer({url: html(fixture)}),
                Resolver(crossref={good: {"message": {"DOI": good, "title": ["ok"]}}}),
                task_pages=[{"iea_task": fixture["iea_task"], "url": url}],
            )
            return run_adapter(adapter, limit=5, events_dir=events_dir)

        first = once()
        assert first.changed == 1 and first.skipped_unchanged == 0
        second = once()
        assert second.changed == 0 and second.skipped_unchanged == 1, (
            "ADR-0026: an unchanged content hash writes NOTHING"
        )
        assert len(read_events(good, events_dir)) == 1

    def test_close_releases_only_clients_it_opened(self) -> None:
        server, resolver = PageServer({}), Resolver()
        adapter = adapter_for(server, resolver)
        adapter.close()
        assert server.closed is False and resolver.closed is False, (
            "an injected client belongs to the caller"
        )


# ---------------------------------------------------------------------------
# The resolver mappers
# ---------------------------------------------------------------------------


class TestTheResolverMappers:
    def test_datacite_never_fabricates_a_month(self) -> None:
        fields = map_datacite({"data": {"attributes": {"publicationYear": 2024, "titles": [{"title": "T"}]}}})
        assert fields["published_date"] == "2024"

    def test_crossref_keeps_a_year_only_date_year_only(self) -> None:
        fields = map_crossref({"message": {"issued": {"date-parts": [[2024]]}, "title": ["T"]}})
        assert fields["published_date"] == "2024"

    def test_crossref_zero_pads_a_full_date(self) -> None:
        fields = map_crossref({"message": {"issued": {"date-parts": [[2024, 4, 12]]}, "title": ["T"]}})
        assert fields["published_date"] == "2024-04-12"

    def test_an_absent_licence_is_never_inferred_open(self) -> None:
        fields = map_crossref({"message": {"title": ["T"]}})
        assert fields["license_raw"] is None

    def test_an_empty_payload_does_not_explode(self) -> None:
        assert map_datacite({})["title"] is None
        assert map_crossref({})["title"] is None
