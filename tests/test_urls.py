"""The URL-scheme allow-list, at every depth it is applied.

Escaping an attribute does **not** disarm ``javascript:``. A registrant
controls their own DataCite ``attributes.url``, their Zenodo file links and
their OSTI full-text hrefs, and the site renders every one of them into an
``href``. Before this, no layer in the pipeline looked at the scheme
(scrape-03, scrape-04, eventlog-06, site-01, site-02): a hostile
``source.url`` travelled from the API response into ``records/*.json``, past
the CKAN gate, and onto the record page.

The defence is one allow-list applied three times — on the way in, on merge,
and at the gate — and this file exercises all three, because a check that only
exists in one of them is a check somebody will route around.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest.ckan_compat import validate_package
from harvest.models import LocalNamespace, SourceNamespace
from harvest.sanitize import sanitize_html
from harvest.urls import clean_url, is_safe_url, safe_resources, safe_url, safe_urls

HOSTILE = [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "//evil.example/x",              # protocol-relative: scheme unknowable here
]

#: The obfuscations a browser sees straight through. WHATWG URL parsing strips
#: leading and trailing C0 controls and spaces and removes tab/CR/LF from
#: anywhere in the input *before* the scheme is read, so every one of these is
#: `javascript:` to a browser. `sanitize._safe_url` used to `.strip()` only
#: Python whitespace, which leaves \x01 and \x0b in place (scrape-04).
OBFUSCATED = [
    "\x01javascript:alert(1)",
    "\x00javascript:alert(1)",
    "\x0bjavascript:alert(1)",
    "\x1fjavascript:alert(1)",
    "\x7fjavascript:alert(1)",
    "java\tscript:alert(1)",
    "java\nscript:alert(1)",
    "java\rscript:alert(1)",
    "\n\tjavascript:alert(1)",
    "\x01data:text/html,<script>alert(1)</script>",
]

SAFE = [
    "https://zenodo.org/records/1234",
    "http://example.org/a?b=c#d",
    "mailto:data@example.org",
    "ftp://ftp.example.org/bulk/wind.nc",
    "ftps://ftp.example.org/bulk/wind.nc",
    "HTTPS://EXAMPLE.ORG/Shouty",
]


class TestTheAllowList:
    @pytest.mark.parametrize("url", HOSTILE)
    def test_a_hostile_scheme_is_refused(self, url: str) -> None:
        assert safe_url(url) is None
        assert is_safe_url(url) is False

    @pytest.mark.parametrize("url", OBFUSCATED)
    def test_control_characters_do_not_hide_a_scheme(self, url: str) -> None:
        assert safe_url(url) is None

    @pytest.mark.parametrize("url", SAFE)
    def test_a_linkable_url_survives(self, url: str) -> None:
        assert safe_url(url) == url.strip()

    def test_the_value_kept_is_the_value_probed(self) -> None:
        """Cleaning must not leave a payload in the string the gate approved.

        Testing a scrubbed probe and then returning the raw value is the
        classic shape of this bug: the check passes on one string and the page
        links to another.
        """
        assert safe_url("  https://example.org/x  ") == "https://example.org/x"
        assert safe_url("https://exam\tple.org/x") == "https://example.org/x"
        assert clean_url("\x01https://example.org/x\x7f") == "https://example.org/x"

    def test_a_relative_url_is_refused_unless_explicitly_allowed(self) -> None:
        assert safe_url("/records/1234") is None
        assert safe_url("/records/1234", allow_relative=True) == "/records/1234"

    def test_a_list_is_filtered_not_emptied(self) -> None:
        assert safe_urls(["javascript:alert(1)", "https://ok.example/x"]) == [
            "https://ok.example/x"
        ]

    def test_a_resource_without_a_linkable_url_is_dropped_entirely(self) -> None:
        """A resource IS a link; a resource you cannot follow is not a resource."""
        resources = [
            {"url": "javascript:alert(1)", "name": "evil.csv"},
            {"url": "https://ok.example/a.csv", "name": "a.csv"},
        ]
        assert safe_resources(resources) == [
            {"url": "https://ok.example/a.csv", "name": "a.csv"}
        ]


class TestTheNamespacesFilterOnTheWayIn:
    """One place, so no adapter can forget — present and future ones alike."""

    def test_a_hostile_source_url_never_reaches_the_namespace(self) -> None:
        source = SourceNamespace(title="T", url="javascript:alert(1)")
        assert source.url is None

    def test_hostile_source_urls_are_filtered(self) -> None:
        source = SourceNamespace(
            title="T", source_urls=["javascript:alert(1)", "https://ok.example/x"]
        )
        assert source.source_urls == ["https://ok.example/x"]

    def test_a_hostile_resource_is_dropped(self) -> None:
        source = SourceNamespace(
            title="T",
            resources=[
                {"url": "data:text/html,<script>1</script>", "name": "x"},
                {"url": "https://ok.example/a.csv", "name": "a.csv"},
            ],
        )
        assert [r["url"] for r in source.resources] == ["https://ok.example/a.csv"]

    def test_a_related_identifier_keeps_its_identifier_and_loses_only_the_url(self) -> None:
        """Losing the DOI because its resolved URL was hostile would be worse."""
        source = SourceNamespace(
            title="T",
            related_identifiers=[
                {"identifier": "10.5281/zenodo.1", "relation": "IsVersionOf",
                 "url": "javascript:alert(1)"}
            ],
        )
        [related] = source.related_identifiers
        assert related["identifier"] == "10.5281/zenodo.1"
        assert "url" not in related

    def test_a_curator_annotation_cannot_smuggle_one_in_either(self) -> None:
        """`local.links` is curator-controlled, and curators paste things."""
        local = LocalNamespace(
            links=[
                {"url": "javascript:alert(1)", "label": "bad"},
                {"url": "https://ok.example/x", "label": "good"},
            ]
        )
        assert [link["url"] for link in local.links] == ["https://ok.example/x"]


class TestTheGateReAssertsIt:
    """Defence in depth: a hand-edited record must be refused too."""

    def _package(self, **overrides) -> dict:  # noqa: ANN003
        package = {
            "name": "doi-10-5281-zenodo-1",
            "title": "T",
            "notes": "n",
            "license_id": "cc-by",
            "owner_org": "dtu",
            "state": "active",
            "private": False,
            "tags": [],
            "groups": [],
            "extras": [],
            "resources": [],
        }
        package.update(overrides)
        return package

    def test_a_hostile_url_fails_the_gate(self) -> None:
        violations = validate_package(self._package(url="javascript:alert(1)"))
        assert any(v.field == "url" for v in violations)

    def test_a_hostile_resource_url_fails_the_gate(self) -> None:
        package = self._package(
            resources=[{"url": "data:text/html,<script>1</script>", "name": "x"}]
        )
        violations = validate_package(package)
        assert any(v.field.startswith("resources[") for v in violations)

    def test_an_ordinary_record_still_passes(self) -> None:
        package = self._package(
            url="https://zenodo.org/records/1",
            resources=[{"url": "https://zenodo.org/records/1/files/a.csv", "name": "a"}],
        )
        assert validate_package(package) == []


class TestTheMergePathFiltersToo:
    """eventlog-06: a merge is the one place a URL crosses an identity boundary."""

    def test_a_merge_does_not_carry_a_hostile_url_onto_the_primary(
        self, repo: Path, events_dir: Path
    ) -> None:
        from harvest.dedupe import dedupe
        from harvest.events import record_scrape, resolve

        shared = {"identifier": "10.5281/zenodo.700", "relation": "IsIdenticalTo"}
        record_scrape(
            "10.5281/zenodo.700", "zenodo", "700", "r1",
            {"title": "Primary", "doi": "10.5281/zenodo.700",
             "url": "https://zenodo.org/records/700"},
            events_dir=events_dir, observed_at="2026-01-01T00:00:00Z",
        )
        # The secondary's `url` is filtered by the namespace, so build the
        # hostile value straight into the event the way a hand-edited log or a
        # future adapter that bypasses SourceNamespace would.
        record_scrape(
            "osti|700", "osti", "700", "r1",
            {"title": "Primary", "doi": "10.5281/zenodo.700",
             "related_identifiers": [shared]},
            events_dir=events_dir, observed_at="2026-01-02T00:00:00Z",
        )
        path = events_dir / "osti-700.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        payload = json.loads(lines[0])
        payload["source"]["url"] = "javascript:alert(1)"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        dedupe(events_dir, root=repo, apply=True, observed_at="2026-02-01T00:00:00Z")

        primary = resolve("10.5281/zenodo.700", events_dir=events_dir)
        links = primary.effective.get("links") or []
        source_urls = primary.effective.get("source_urls") or []
        assert all(is_safe_url(link["url"]) for link in links)
        assert all(is_safe_url(url) for url in source_urls)


class TestTheDescriptionSanitiserUsesTheSameRule:
    """One href policy, not two. scrape-04 was the two drifting apart."""

    @pytest.mark.parametrize("url", OBFUSCATED)
    def test_an_obfuscated_href_inside_a_description_is_stripped(self, url: str) -> None:
        html = f'<p>See <a href="{url}">here</a></p>'
        assert "javascript" not in sanitize_html(html).lower()
        assert "data:text/html" not in sanitize_html(html).lower()

    def test_an_ordinary_link_inside_a_description_survives(self) -> None:
        html = '<p>See <a href="https://example.org/x">here</a></p>'
        assert 'href="https://example.org/x"' in sanitize_html(html)

    def test_a_relative_href_survives_because_the_page_resolves_it(self) -> None:
        html = '<p>See <a href="/records/1">here</a></p>'
        assert 'href="/records/1"' in sanitize_html(html)
