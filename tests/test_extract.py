"""The Tier-3 extraction layer — ADR-0024, ADR-0025, ADR-0030, ADR-0031, ADR-0035.

Four things are being defended here, and only the first is ordinary:

1. the cache key is exactly ``sha256(content + prompt_version + model_id)`` and
   a committed entry replays byte-identically (ADR-0025);
2. **no identifier the model produced is ever accepted** — a DOI outside the
   supplied context is blanked before the result is even cached (ADR-0024);
3. **nothing about the LLM can fail the run** — no key, rate limit, outage,
   malformed JSON, schema violation: :func:`extract` returns ``None``, the page
   is queued, and the caller carries on (ADR-0031, fixture ``x-07``);
4. inference is one ``httpx`` POST to an OpenAI-compatible endpoint with a
   JSON-schema-constrained response and temperature at the floor — no vendor
   SDK anywhere in the import graph (ADR-0035).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from harvest import extract as extraction
from harvest.extract import (
    BACKFILL_MODELS,
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    EXTRACTION_SCHEMA,
    MAX_EXTRACTIONS,
    PROMPT_VERSION,
    RECORD_BEARING_PAGE_KINDS,
    ExtractionResult,
    PageExtraction,
    cache_key,
    cache_lineage,
    content_hash,
    drain_pending,
    extract,
    lookup_cache,
    main_text,
    max_extractions,
    queue_pending,
    read_cache,
    read_pending,
    reset_stats,
    resolve_endpoint,
    resolve_model,
    resolve_token,
    write_cache,
    write_pending,
)
from harvest.models import FieldProvenance

CONTENT = "Task 43 – Publications\nKnowledge engineering for wind energy, 10.5194/wes-9-883-2024."


@pytest.fixture(autouse=True)
def clean_stats() -> None:
    reset_stats()
    yield
    reset_stats()


def _page(**overrides) -> dict:
    payload = {
        "page_kind": "publication-list",
        "is_record_bearing": True,
        "confidence": 0.9,
        "notes": None,
        "records": [
            {
                "title": "Knowledge engineering for wind energy",
                "doi": "10.5194/wes-9-883-2024",
                "resource_kind": "publication",
                "access_status": None,
                "container": "Wind Energy Science",
                "published_date": "2024",
                "authors": ["Marykovskiy, Yuriy"],
                "confidence": 0.86,
            }
        ],
    }
    payload.update(overrides)
    return payload


class FakePost:
    """Stands in for ``httpx.Client.post``. Records the body it was given."""

    def __init__(self, status: int = 200, message: str | None = None, boom: bool = False):
        self.status = status
        self.message = message
        self.boom = boom
        self.calls: list[dict] = []

    def __call__(self, url, *, json=None, headers=None, **kwargs):  # noqa: A002
        self.calls.append({"url": url, "body": json, "headers": headers})
        if self.boom:
            raise httpx.ConnectError("provider is down")
        return httpx.Response(
            self.status,
            json={"choices": [{"message": {"content": self.message or "{}"}}]},
            request=httpx.Request("POST", url),
        )


@pytest.fixture
def fake_post(monkeypatch: pytest.MonkeyPatch):
    def install(**kwargs) -> FakePost:
        poster = FakePost(**kwargs)
        monkeypatch.setattr(httpx.Client, "post", poster)
        return poster

    return install


# ---------------------------------------------------------------------------
# ADR-0025 — the cache key and the committed cache
# ---------------------------------------------------------------------------


class TestCacheKey:
    def test_key_is_sha256_of_content_prompt_and_model(self) -> None:
        digest = hashlib.sha256()
        digest.update(CONTENT.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(b"v1")
        digest.update(b"\x1f")
        digest.update(b"openai/gpt-4o-mini")
        assert cache_key(CONTENT, "v1", "openai/gpt-4o-mini") == digest.hexdigest()

    def test_every_component_participates(self) -> None:
        base = cache_key(CONTENT, "v1", "m")
        assert cache_key(CONTENT + " ", "v1", "m") != base
        assert cache_key(CONTENT, "v2", "m") != base, "bumping PROMPT_VERSION must invalidate"
        assert cache_key(CONTENT, "v1", "other") != base, "a model change is a lineage split"

    def test_the_source_key_shares_the_cache_key_input(self) -> None:
        """One hash, two purposes — the adapter and the cache cannot disagree."""
        assert content_hash(CONTENT) == hashlib.sha256(CONTENT.encode()).hexdigest()[:16]
        assert cache_key(CONTENT).startswith(hashlib.sha256(CONTENT.encode()).hexdigest()[:0])


class TestTheCommittedCache:
    def test_round_trip_is_byte_stable(self, tmp_path: Path) -> None:
        result = ExtractionResult(
            key=cache_key(CONTENT),
            model=DEFAULT_MODEL,
            prompt_version=PROMPT_VERSION,
            content_sha256=hashlib.sha256(CONTENT.encode()).hexdigest(),
            extracted_at="2026-08-31T22:15:04Z",
            fields=_page(),
            confidence={"page_kind": 0.9},
        )
        path = write_cache(result, tmp_path)
        first = path.read_bytes()
        write_cache(result, tmp_path)
        assert path.read_bytes() == first, "a rebuild must not churn the cache"

        back = read_cache(result.key, tmp_path)
        assert back is not None
        assert back.as_dict() == result.as_dict()
        assert back.page().records[0].doi == "10.5194/wes-9-883-2024"

    def test_a_corrupt_entry_is_a_miss_not_an_exception(self, tmp_path: Path) -> None:
        key = cache_key(CONTENT)
        (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
        assert read_cache(key, tmp_path) is None

    def test_a_missing_entry_is_a_miss(self, tmp_path: Path) -> None:
        assert read_cache(cache_key("nothing here"), tmp_path) is None

    def test_the_committed_cache_replays_offline(self, tmp_path: Path, fake_post) -> None:
        poster = fake_post(status=500)
        seeded = ExtractionResult(
            key=cache_key(CONTENT),
            model=DEFAULT_MODEL,
            prompt_version=PROMPT_VERSION,
            content_sha256=hashlib.sha256(CONTENT.encode()).hexdigest(),
            extracted_at="2026-08-31T22:15:04Z",
            fields=_page(),
        )
        write_cache(seeded, tmp_path)

        got = extract(CONTENT, token="a-key", cache_directory=tmp_path)
        assert got is not None and got.as_dict() == seeded.as_dict()
        assert poster.calls == [], "the cache is consulted FIRST, always"
        assert extraction.STATS.hits == 1 and extraction.STATS.calls == 0


class TestTheBackfillLineage:
    """ADR-0030 §4 — the committed cache holds a second model lineage."""

    def test_claude_fable_5_is_the_seeded_lineage(self) -> None:
        assert "claude-fable-5" in BACKFILL_MODELS

    def test_lookup_falls_back_through_the_lineage(self, tmp_path: Path) -> None:
        seeded = ExtractionResult(
            key=cache_key(CONTENT, PROMPT_VERSION, "claude-fable-5"),
            model="claude-fable-5",
            prompt_version=PROMPT_VERSION,
            content_sha256=hashlib.sha256(CONTENT.encode()).hexdigest(),
            extracted_at="2026-08-31T22:15:04Z",
            fields=_page(),
        )
        write_cache(seeded, tmp_path)

        hit, primary = lookup_cache(CONTENT, PROMPT_VERSION, DEFAULT_MODEL, tmp_path)
        assert hit is not None and hit.model == "claude-fable-5"
        assert primary == cache_key(CONTENT, PROMPT_VERSION, DEFAULT_MODEL), (
            "the queue and a fresh write use the CURRENT model's key, not the backfill's"
        )

    def test_the_lineage_is_overridable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HARVEST_LLM_CACHE_LINEAGE", "some-other-model, claude-fable-5")
        assert cache_lineage("m") == ("m", "some-other-model", "claude-fable-5")
        assert cache_lineage("claude-fable-5")[0] == "claude-fable-5"


class TestPinnedExtractions:
    """Plan §4.3 — a human correction outranks a model's guess, and it holds.

    A pin is the one place "no update" gets a carve-out, because on a Tier-3
    page the extraction *is* the metadata: nobody is being overruled except the
    model. The hard part is that a cache entry is keyed on content, so a
    redesigned page mints a new key — and a pin found only by content would be
    reverted, silently, by the first site refresh. It is found by URL instead.
    """

    def _pin(self, tmp_path: Path, url: str, pinned_against: str) -> ExtractionResult:
        result = ExtractionResult(
            key=cache_key(pinned_against),
            model="curator:tom",
            prompt_version=PROMPT_VERSION,
            content_sha256=hashlib.sha256(pinned_against.encode()).hexdigest(),
            extracted_at="2026-08-31T22:15:04Z",
            fields=_page(page_kind="task-overview"),
            pinned=True,
            pin_source_key=content_hash(pinned_against),
            pin_url=url,
        )
        write_cache(result, tmp_path)
        return result

    def test_pin_fields_survive_a_round_trip(self, tmp_path: Path) -> None:
        url = "https://iea-wind.org/task43/t43-publications/"
        self._pin(tmp_path, url, CONTENT)
        back = read_cache(cache_key(CONTENT), tmp_path)
        assert back is not None
        assert back.pinned is True
        assert back.pin_url == url
        assert back.pin_source_key == content_hash(CONTENT)

    def test_the_pin_holds_when_the_page_is_rewritten(self, tmp_path: Path) -> None:
        url = "https://iea-wind.org/task43/t43-publications/"
        self._pin(tmp_path, url, CONTENT)
        rewritten = CONTENT + "\nThe site was redesigned in September."

        assert read_cache(cache_key(rewritten), tmp_path) is None, "the content key misses"
        hit, primary = lookup_cache(rewritten, PROMPT_VERSION, DEFAULT_MODEL, tmp_path, url=url)
        assert hit is not None and hit.pinned is True, (
            "a site redesign must not silently revert a human's correction"
        )
        assert primary == cache_key(rewritten, PROMPT_VERSION, DEFAULT_MODEL)

    def test_a_held_pin_is_reported_so_a_human_revisits_it(self, tmp_path: Path) -> None:
        url = "https://iea-wind.org/task43/t43-publications/"
        self._pin(tmp_path, url, CONTENT)
        rewritten = CONTENT + " (rewritten)"

        unchanged, _ = lookup_cache(CONTENT, PROMPT_VERSION, DEFAULT_MODEL, tmp_path, url=url)
        moved, _ = lookup_cache(rewritten, PROMPT_VERSION, DEFAULT_MODEL, tmp_path, url=url)

        assert extraction.pin_held(unchanged, CONTENT) is False, "nothing moved; no notice"
        assert extraction.pin_held(moved, rewritten) is True, "the page moved; a notice fires"

    def test_an_ordinary_entry_is_never_mistaken_for_a_pin(self, tmp_path: Path) -> None:
        write_cache(
            ExtractionResult(
                key=cache_key(CONTENT), model=DEFAULT_MODEL, prompt_version=PROMPT_VERSION,
                content_sha256="x", extracted_at="2026-08-31T22:15:04Z", fields=_page(),
            ),
            tmp_path,
        )
        hit, _ = lookup_cache(CONTENT, PROMPT_VERSION, DEFAULT_MODEL, tmp_path, url="https://x/")
        assert extraction.pin_held(hit, CONTENT) is False
        assert extraction.find_pin("https://x/", tmp_path) is None

    def test_a_pin_for_another_page_is_not_served(self, tmp_path: Path) -> None:
        self._pin(tmp_path, "https://iea-wind.org/task43/", CONTENT)
        hit, _ = lookup_cache(
            "totally different content", PROMPT_VERSION, DEFAULT_MODEL, tmp_path,
            url="https://iea-wind.org/task49/",
        )
        assert hit is None

    def test_without_a_url_a_pin_cannot_be_found(self, tmp_path: Path) -> None:
        self._pin(tmp_path, "https://iea-wind.org/task43/", CONTENT)
        hit, _ = lookup_cache("rewritten", PROMPT_VERSION, DEFAULT_MODEL, tmp_path)
        assert hit is None, "the URL is the pin's only stable handle"

    def test_extract_serves_the_pin_instead_of_calling_the_model(
        self, tmp_path: Path, fake_post
    ) -> None:
        url = "https://iea-wind.org/task43/t43-publications/"
        self._pin(tmp_path, url, CONTENT)
        poster = fake_post(message=json.dumps(_page()))

        result = extract(
            CONTENT + " rewritten", token="a-key", context={"url": url}, cache_directory=tmp_path
        )

        assert result is not None and result.pinned is True
        assert poster.calls == [], "a pinned page never spends a model call"
        assert result.page().page_kind == "task-overview"

    def test_a_missing_cache_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert extraction.find_pin("https://x/", tmp_path / "nope") is None

    def test_the_pin_index_notices_a_pin_added_after_it_was_built(self, tmp_path: Path) -> None:
        url = "https://iea-wind.org/task49/"
        assert extraction.find_pin(url, tmp_path) is None  # builds an empty index
        self._pin(tmp_path, url, CONTENT)
        assert extraction.find_pin(url, tmp_path) is not None, (
            "the index is a cache, not a snapshot"
        )

    def test_a_corrupt_entry_does_not_break_the_pin_index(self, tmp_path: Path) -> None:
        url = "https://iea-wind.org/task49/"
        self._pin(tmp_path, url, CONTENT)
        (tmp_path / "deadbeef.json").write_text("{oh no", encoding="utf-8")
        assert extraction.find_pin(url, tmp_path) is not None

    def test_the_documented_pin_extras_are_local_not_source(self) -> None:
        from harvest.materialize import EXTRA_KEYS

        assert "pinned" in EXTRA_KEYS, "the record page renders a pinned extraction"


# ---------------------------------------------------------------------------
# ADR-0031 — degradation is sacred (fixture x-07)
# ---------------------------------------------------------------------------


class TestDegradation:
    def test_no_credential_returns_none_and_never_raises(self, tmp_path: Path) -> None:
        assert resolve_token() is None
        assert extract(CONTENT, cache_directory=tmp_path) is None
        assert extraction.STATS.misses == 1
        assert extraction.STATS.calls == 0, "no key means no call was even attempted"

    @pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
    def test_every_http_failure_is_a_none(self, tmp_path: Path, fake_post, status: int) -> None:
        fake_post(status=status)
        assert extract(CONTENT, token="a-key", cache_directory=tmp_path) is None
        assert extraction.STATS.failures == 1
        assert list(tmp_path.glob("*.json")) == [], "a failure never writes a cache entry"

    def test_a_transport_error_is_a_none(self, tmp_path: Path, fake_post) -> None:
        fake_post(boom=True)
        assert extract(CONTENT, token="a-key", cache_directory=tmp_path) is None
        assert extraction.STATS.failures == 1

    def test_unparseable_json_is_a_none(self, tmp_path: Path, fake_post) -> None:
        fake_post(message="I'm afraid I can't do that.")
        assert extract(CONTENT, token="a-key", cache_directory=tmp_path) is None

    def test_a_schema_violation_is_a_none(self, tmp_path: Path, fake_post) -> None:
        fake_post(message=json.dumps({"page_kind": "not-a-kind", "records": []}))
        assert extract(CONTENT, token="a-key", cache_directory=tmp_path) is None
        assert extraction.STATS.failures == 1

    def test_the_offline_suite_itself_exercises_the_outage_path(self, tmp_path: Path) -> None:
        """No monkeypatching: conftest blocks httpx, which is a provider outage."""
        assert extract(CONTENT, token="a-key", cache_directory=tmp_path) is None
        assert extraction.STATS.failures == 1


class TestTheCallCap:
    def test_default_is_two_hundred(self) -> None:
        assert MAX_EXTRACTIONS == 200 and max_extractions() == 200

    def test_the_cap_is_env_overridable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HARVEST_MAX_EXTRACTIONS", "7")
        assert max_extractions() == 7
        monkeypatch.setenv("HARVEST_MAX_EXTRACTIONS", "not-a-number")
        assert max_extractions() == MAX_EXTRACTIONS

    def test_a_zero_cap_queues_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_post
    ) -> None:
        poster = fake_post(message=json.dumps(_page()))
        monkeypatch.setenv("HARVEST_MAX_EXTRACTIONS", "0")
        assert extract(CONTENT, token="a-key", cache_directory=tmp_path) is None
        assert poster.calls == []

    def test_the_cap_stops_the_second_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_post
    ) -> None:
        poster = fake_post(message=json.dumps(_page()))
        monkeypatch.setenv("HARVEST_MAX_EXTRACTIONS", "1")
        assert extract(CONTENT, token="a-key", cache_directory=tmp_path) is not None
        assert extract(CONTENT + " more", token="a-key", cache_directory=tmp_path) is None
        assert len(poster.calls) == 1


# ---------------------------------------------------------------------------
# The pending queue
# ---------------------------------------------------------------------------


class TestThePendingQueue:
    def test_an_absent_queue_reads_as_empty(self, tmp_path: Path) -> None:
        assert read_pending(tmp_path) == []

    def test_queue_entry_shape(self, tmp_path: Path) -> None:
        queue_pending("https://iea-wind.org/task43/", "abc", "no model", tmp_path)
        (entry,) = read_pending(tmp_path)
        assert set(entry) == {"url", "cache_key", "reason", "queued_at"}
        assert entry["url"] == "https://iea-wind.org/task43/"
        assert entry["cache_key"] == "abc"
        assert entry["queued_at"].endswith("Z")

    def test_dedupe_is_on_cache_key_and_keeps_the_first_sighting(self, tmp_path: Path) -> None:
        queue_pending("https://iea-wind.org/task43/", "abc", "first", tmp_path)
        first_seen = read_pending(tmp_path)[0]["queued_at"]
        queue_pending("https://iea-wind.org/task43/", "abc", "second", tmp_path)
        entries = read_pending(tmp_path)
        assert len(entries) == 1
        assert entries[0]["reason"] == "second", "the newest reason wins"
        assert entries[0]["queued_at"] == first_seen, "queued_at is the FIRST sighting"

    def test_the_queue_is_fifo_and_byte_stable(self, tmp_path: Path) -> None:
        for index in range(3):
            queue_pending(f"https://example.org/{index}", f"k{index}", "queued", tmp_path)
        assert [e["cache_key"] for e in read_pending(tmp_path)] == ["k0", "k1", "k2"]
        path = tmp_path / "pending-extraction.json"
        before = path.read_bytes()
        write_pending(read_pending(tmp_path), tmp_path)
        assert path.read_bytes() == before

    def test_an_unreadable_queue_is_empty_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "pending-extraction.json").write_text("[[[", encoding="utf-8")
        assert read_pending(tmp_path) == []
        queue_pending("https://example.org/", "k", "queued", tmp_path)
        assert len(read_pending(tmp_path)) == 1

    def test_a_dict_shaped_queue_is_tolerated(self, tmp_path: Path) -> None:
        (tmp_path / "pending-extraction.json").write_text(
            json.dumps({"pending": [{"url": "u", "cache_key": "k"}]}), encoding="utf-8"
        )
        assert read_pending(tmp_path)[0]["cache_key"] == "k"

    def test_x07_a_miss_with_no_model_queues_and_the_run_continues(self, tmp_path: Path) -> None:
        """Fixture ``x-07`` end to end, at this layer."""
        cache_directory = tmp_path / "cache"
        state_directory = tmp_path / "state"
        cache_directory.mkdir()
        state_directory.mkdir()

        result = extract(CONTENT, cache_directory=cache_directory)
        assert result is None
        queue_pending(
            "https://iea-wind.org/task43/", cache_key(CONTENT), "no model", state_directory
        )

        assert len(read_pending(state_directory)) == 1
        assert list(cache_directory.glob("*.json")) == []
        # …and nothing raised, which is the entire point.

    def test_x07_the_fixture_file_drives_the_same_path(self, tmp_path: Path) -> None:
        """The same case, driven from ``fixtures/cross-cutting/x-07-cache-miss-no-llm``.

        ADR-0031 §3 says "fixture x-07 exists to hold this line" and the fixture
        catalogue has always listed it, but for a while no such file existed —
        the behaviour was asserted only against a string literal in this module
        (site-05, compliance-07, fixture-compliance-04). It exists now, and this
        is what reads it: the page it holds is deliberately unclassifiable by
        pattern, so the deterministic tier hands it to Tier 3, and Tier 3 has
        neither a cache entry nor a credential.
        """
        from harvest import config
        from harvest.adapters.ieawind import classify_page
        from harvest.doi import extract_dois

        directory = config.fixtures_dir() / "cross-cutting"
        fixture = json.loads(
            (directory / "x-07-cache-miss-no-llm.json").read_text(encoding="utf-8")
        )
        page = (directory / fixture["html"]).read_text(encoding="utf-8")

        content = main_text(page)
        assert len(content) == fixture["expected_content_chars"]
        assert content_hash(content) == fixture["expected_content_hash"]
        assert cache_key(content) == fixture["expected_cache_key"]
        assert extract_dois(content) == fixture["expected_dois"]

        # Deterministic classification declines, so the page escalates.
        assert classify_page(fixture["page_url"], content, trusted=fixture["trusted"]) is None

        cache_directory = tmp_path / "cache"
        state_directory = tmp_path / "state"
        cache_directory.mkdir()
        state_directory.mkdir()

        assert extract(content, cache_directory=cache_directory) is fixture[
            "expected_extraction_result"
        ]
        assert extraction.STATS.calls == fixture["expected_model_calls"], (
            "no credential means no call is even attempted"
        )
        queue_pending(
            fixture["page_url"],
            cache_key(content),
            fixture["expected_pending_reason"],
            state_directory,
        )

        pending = read_pending(state_directory)
        assert len(pending) == fixture["expected_pending_entries"]
        assert pending[0]["url"] == fixture["page_url"]
        assert (
            len(list(cache_directory.glob("*.json")))
            == fixture["expected_cache_entries_written"]
        )

        # Queueing is idempotent: a second pass over the same page adds nothing.
        queue_pending(
            fixture["page_url"],
            cache_key(content),
            fixture["expected_pending_reason"],
            state_directory,
        )
        assert len(read_pending(state_directory)) == fixture["expected_pending_entries"]


class TestTheDrain:
    def test_an_empty_queue_resolves_nothing(self, tmp_path: Path) -> None:
        assert drain_pending(state_directory=tmp_path, cache_directory=tmp_path) == 0

    def test_a_cached_page_drains_offline(self, tmp_path: Path) -> None:
        cache_directory = tmp_path / "cache"
        state_directory = tmp_path / "state"
        cache_directory.mkdir()
        state_directory.mkdir()
        html = f"<html><body><article><p>{CONTENT}</p></article></body></html>"
        content = main_text(html)
        write_cache(
            ExtractionResult(
                key=cache_key(content),
                model=DEFAULT_MODEL,
                prompt_version=PROMPT_VERSION,
                content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                extracted_at="2026-08-31T22:15:04Z",
                fields=_page(),
            ),
            cache_directory,
        )
        queue_pending("https://iea-wind.org/task43/", cache_key(content), "queued", state_directory)

        class Fetcher:
            def get(self, url, **kwargs):
                from harvest.http import FetchResult

                return FetchResult(url=url, status_code=200, changed=True, text=html)

            def close(self) -> None:
                pass

        assert drain_pending(
            state_directory=state_directory, cache_directory=cache_directory, client=Fetcher()
        ) == 1
        assert read_pending(state_directory) == []

    def test_an_unfetchable_page_stays_queued(self, tmp_path: Path) -> None:
        queue_pending("https://iea-wind.org/gone/", "k", "queued", tmp_path)

        class Broken:
            def get(self, url, **kwargs):
                from harvest.http import FetchResult

                return FetchResult(url=url, status_code=None, changed=False, error="dns")

            def close(self) -> None:
                pass

        assert drain_pending(
            state_directory=tmp_path, cache_directory=tmp_path, client=Broken()
        ) == 0
        assert len(read_pending(tmp_path)) == 1

    def test_an_entry_with_no_url_is_dropped_rather_than_kept_forever(self, tmp_path: Path) -> None:
        write_pending([{"cache_key": "k", "reason": "queued", "queued_at": "x"}], tmp_path)

        class Unused:
            def get(self, url, **kwargs):  # pragma: no cover - must never be called
                raise AssertionError("nothing to fetch")

            def close(self) -> None:
                pass

        assert drain_pending(
            state_directory=tmp_path, cache_directory=tmp_path, client=Unused()
        ) == 1
        assert read_pending(tmp_path) == []


# ---------------------------------------------------------------------------
# ADR-0024 — the model never produces an identifier
# ---------------------------------------------------------------------------


class TestIdentifiersNeverComeFromTheModel:
    def test_a_doi_outside_the_context_is_blanked_before_caching(
        self, tmp_path: Path, fake_post
    ) -> None:
        fake_post(
            message=json.dumps(
                _page(
                    records=[
                        {
                            "title": "A real paper",
                            "doi": "10.5194/wes-9-883-2024",
                            "resource_kind": "publication",
                            "confidence": 0.9,
                            "authors": [],
                        },
                        {
                            "title": "A paper whose DOI the model made up",
                            "doi": "10.9999/invented.2026",
                            "resource_kind": "publication",
                            "confidence": 0.9,
                            "authors": [],
                        },
                    ]
                )
            )
        )
        result = extract(
            CONTENT,
            token="a-key",
            context={"dois": ["10.5194/wes-9-883-2024"], "url": "https://iea-wind.org/task43/"},
            cache_directory=tmp_path,
        )
        assert result is not None
        records = result.page().records
        assert records[0].doi == "10.5194/wes-9-883-2024"
        assert records[1].doi is None, "an invented DOI must not survive"
        assert records[1].title, "the record survives; only the identifier is discarded"

        cached = read_cache(result.key, tmp_path)
        assert cached is not None
        assert "10.9999/invented.2026" not in json.dumps(cached.as_dict()), (
            "the hallucinated identifier must not even reach the committed cache"
        )

    def test_the_known_dois_are_put_in_front_of_the_model(self, tmp_path: Path, fake_post) -> None:
        poster = fake_post(message=json.dumps(_page()))
        extract(
            CONTENT,
            token="a-key",
            context={"dois": ["10.5194/wes-9-883-2024"], "url": "https://iea-wind.org/task43/"},
            cache_directory=tmp_path,
        )
        user_message = poster.calls[0]["body"]["messages"][1]["content"]
        assert "KNOWN_DOIS" in user_message
        assert "10.5194/wes-9-883-2024" in user_message
        assert "https://iea-wind.org/task43/" in user_message

    def test_case_and_spacing_do_not_smuggle_a_doi_through(
        self, tmp_path: Path, fake_post
    ) -> None:
        fake_post(
            message=json.dumps(
                _page(
                    records=[
                        {
                            "title": "Same DOI, shouted",
                            "doi": " 10.5194/WES-9-883-2024 ",
                            "resource_kind": "publication",
                            "confidence": 0.9,
                            "authors": [],
                        }
                    ]
                )
            )
        )
        result = extract(
            CONTENT,
            token="a-key",
            context={"dois": ["10.5194/wes-9-883-2024"]},
            cache_directory=tmp_path,
        )
        assert result is not None
        assert result.page().records[0].doi is not None, "a case variant is the same DOI"


# ---------------------------------------------------------------------------
# ADR-0035 — no vendor SDK; structured output; temperature at the floor
# ---------------------------------------------------------------------------


class TestTheRequest:
    def test_it_is_one_post_to_an_openai_compatible_path(self, tmp_path: Path, fake_post) -> None:
        poster = fake_post(message=json.dumps(_page()))
        extract(CONTENT, token="a-key", cache_directory=tmp_path)
        assert len(poster.calls) == 1
        assert poster.calls[0]["url"] == f"{DEFAULT_ENDPOINT}/chat/completions"
        assert poster.calls[0]["headers"]["Authorization"] == "Bearer a-key"
        assert "iea-wind-data-catalogue" in poster.calls[0]["headers"]["User-Agent"]

    def test_temperature_is_at_the_floor_and_output_is_schema_constrained(
        self, tmp_path: Path, fake_post
    ) -> None:
        poster = fake_post(message=json.dumps(_page()))
        extract(CONTENT, token="a-key", cache_directory=tmp_path)
        body = poster.calls[0]["body"]
        assert body["temperature"] == 0.0
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["response_format"]["json_schema"]["schema"] == EXTRACTION_SCHEMA

    def test_no_vendor_sdk_is_imported(self) -> None:
        import sys

        banned = {"openai", "anthropic", "google.generativeai", "cohere", "litellm", "langchain"}
        assert banned.isdisjoint(sys.modules), "ADR-0035: inference is an httpx POST, nothing more"
        source = Path(extraction.__file__).read_text(encoding="utf-8")
        for name in banned:
            assert f"import {name}" not in source

    def test_the_environment_points_the_same_function_elsewhere(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert resolve_endpoint() == DEFAULT_ENDPOINT
        assert resolve_model() == DEFAULT_MODEL
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1/")
        monkeypatch.setenv("MODEL_ID", "some/model")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert resolve_endpoint() == "https://api.example.com/v1"
        assert resolve_model() == "some/model"
        assert resolve_token() == "sk-test"

    def test_github_token_is_the_ci_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-fake")
        assert resolve_token() == "ghs-fake", "ADR-0030: CI uses the built-in token"


class TestACredentialOnlyEverGoesToItsOwnProvider:
    """product-e2e-01: the harvester used to leak the operator's OpenAI key.

    ``resolve_token`` picked the first of ``HARVEST_LLM_TOKEN``,
    ``OPENAI_API_KEY``, ``GITHUB_TOKEN`` that was set, and ``resolve_endpoint``
    independently defaulted to GitHub Models. So a laptop with the OpenAI SDK
    configured — ``OPENAI_API_KEY`` exported, no ``OPENAI_BASE_URL``, which is
    the *normal* OpenAI setup — sent a live OpenAI secret as a Bearer token to
    ``https://models.github.ai`` on every ordinary ``make harvest``. A
    credential handed to a third party is not a config wrinkle; it is the key
    burned.

    The rule is symmetric, and the symmetry is the point: a credential travels
    only to an endpoint the operator explicitly configured for that provider.
    """

    def test_an_openai_key_is_not_sent_to_github_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
        assert resolve_endpoint() == DEFAULT_ENDPOINT, "the default is GitHub's"
        assert resolve_token() is None

    def test_the_harvest_still_degrades_rather_than_failing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ADR-0031: no credential is a supported state, not an error."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
        assert extract(CONTENT, cache_directory=tmp_path) is None

    def test_an_openai_key_goes_to_an_endpoint_configured_for_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        assert resolve_token() == "sk-live-secret"

    def test_a_github_token_is_not_walked_to_a_third_party_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The mirror image: a stray OPENAI_BASE_URL must not export GITHUB_TOKEN."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-repo-scoped")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://inference.evil.example/v1")
        assert resolve_token() is None

    def test_the_harvesters_own_token_goes_wherever_it_is_pointed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HARVEST_LLM_TOKEN is this harvester's own credential, so it is trusted."""
        monkeypatch.setenv("HARVEST_LLM_TOKEN", "harvest-key")
        monkeypatch.setenv("HARVEST_LLM_ENDPOINT", "https://inference.example/v1")
        assert resolve_token() == "harvest-key"

    def test_an_explicit_argument_always_wins(self) -> None:
        assert resolve_token("passed-in") == "passed-in"

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://models.github.ai/inference",
            "https://models.inference.ai.azure.com",
            "https://api.github.com/models",
        ],
    )
    def test_the_openai_key_is_withheld_from_every_endpoint_it_was_not_issued_for(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str
    ) -> None:
        """Including a plausible-looking one that is not the configured OpenAI host."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-fake")
        token = resolve_token(endpoint=endpoint)
        assert token != "sk-live-secret"

    @staticmethod
    def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        """Record every outbound POST. Asserting on the wire, not on the helper."""
        import httpx

        calls: list[dict] = []

        def post(self, url, **kwargs):  # noqa: ANN001, ANN202
            calls.append({"url": url, "headers": dict(kwargs.get("headers") or {})})
            raise httpx.ConnectError("captured; no request is actually sent")

        monkeypatch.setattr(httpx.Client, "post", post)
        return calls

    def test_no_request_at_all_is_made_with_a_borrowed_credential(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = self._capture(monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-live-secret")

        assert extract(CONTENT, cache_directory=tmp_path) is None
        assert calls == [], "the page must queue, not leak the key to GitHub"

    def test_the_ci_path_still_sends_the_github_token_to_github(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The fix must not have disarmed ADR-0030's actual credential path."""
        calls = self._capture(monkeypatch)
        monkeypatch.setenv("GITHUB_TOKEN", "ghs-fake")

        extract(CONTENT, cache_directory=tmp_path)

        assert len(calls) == 1
        assert calls[0]["url"].startswith(DEFAULT_ENDPOINT)
        assert calls[0]["headers"]["Authorization"] == "Bearer ghs-fake"


# ---------------------------------------------------------------------------
# The result and its provenance
# ---------------------------------------------------------------------------


class TestTheResult:
    def test_a_fresh_extraction_is_cached_and_stamped(self, tmp_path: Path, fake_post) -> None:
        fake_post(message=json.dumps(_page()))
        result = extract(CONTENT, token="a-key", cache_directory=tmp_path)
        assert result is not None
        assert result.key == cache_key(CONTENT, PROMPT_VERSION, DEFAULT_MODEL)
        assert result.model == DEFAULT_MODEL
        assert result.prompt_version == PROMPT_VERSION
        assert result.content_sha256 == hashlib.sha256(CONTENT.encode()).hexdigest()
        assert result.extracted_at.endswith("Z")
        assert (tmp_path / f"{result.key}.json").exists()
        assert result.confidence["records"] == pytest.approx(0.86)

    def test_llm_provenance_requires_a_model_and_a_prompt_version(self) -> None:
        provenance = FieldProvenance(
            extraction_method="llm",
            model="claude-fable-5",
            prompt_version=PROMPT_VERSION,
            confidence=0.81,
        )
        assert provenance.model == "claude-fable-5"
        with pytest.raises(Exception):
            FieldProvenance(extraction_method="llm", confidence=0.5)

    def test_only_two_page_kinds_may_bear_records(self) -> None:
        assert RECORD_BEARING_PAGE_KINDS == {"publication-list", "task-overview"}
        for kind in ("news", "event", "person", "other"):
            assert kind not in RECORD_BEARING_PAGE_KINDS

    def test_the_schema_forbids_anything_it_did_not_ask_for(self) -> None:
        assert EXTRACTION_SCHEMA["additionalProperties"] is False
        with pytest.raises(Exception):
            PageExtraction.model_validate({"page_kind": "news", "surprise": 1})


# ---------------------------------------------------------------------------
# main_text — the prompt-injection boundary (fixture iea-10)
# ---------------------------------------------------------------------------


class TestMainText:
    def test_nothing_in_is_nothing_out(self) -> None:
        assert main_text(None) == "" and main_text("") == ""

    def test_script_nav_and_attributes_never_reach_the_model(self) -> None:
        html = """<!DOCTYPE html><html><head><title>t</title>
        <script>window.__DATA__ = {"ignore previous instructions": true};</script></head>
        <body><nav><a href="/menu">Menu</a></nav>
        <article><h1>Task 43 Publications</h1>
        <p>Knowledge engineering for wind energy, 10.5194/wes-9-883-2024.</p></article>
        <footer>Cookie preferences</footer></body></html>"""
        text = main_text(html)
        assert "Knowledge engineering for wind energy" in text
        assert "window.__DATA__" not in text, "a script payload is an injection vector"
        assert "ignore previous instructions" not in text
        assert "Cookie preferences" not in text
        assert "<script" not in text and "href=" not in text


class TestExtractReallyNeverRaises:
    """product-e2e-08: the contract said "never raises"; the first line did."""

    def test_a_string_cache_directory_is_accepted(self, tmp_path: Path) -> None:
        assert extract(CONTENT, cache_directory=str(tmp_path)) is None

    def test_cache_path_coerces(self, tmp_path: Path) -> None:
        from harvest.extract import cache_path

        assert cache_path("k", str(tmp_path)) == tmp_path / "k.json"
