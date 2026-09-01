"""The render boundary: what the Astro layer does with a hostile string.

The harvest cleans metadata on the way in (``harvest.sanitize``,
``harvest.urls``) and that is the right place for it. It is not the *only*
place it can be needed, because the renderer is what turns a string into
markup:

* ``JSON.stringify`` does not escape ``<``, so a harvested title containing
  ``</script>`` closed the JSON-LD element and everything after it became live
  HTML — stored XSS from any source (scrape-01);
* ``pkg.notes`` is rendered with ``set:html`` and no gate inspected it, so one
  adapter forgetting to sanitise would have shipped script (site-02);
* ``local.links[].url`` and ``resources[].url`` are rendered into ``href``
  attributes, and escaping an attribute does not disarm ``javascript:``
  (site-01).

``site/src/safety.mjs`` is the belt to the harvest's braces. It is plain
``.mjs``, like ``ckan.mjs`` and ``licenses.mjs``, precisely so it can be
exercised from here and from ``site/scripts/check-render.mjs`` rather than only
inside a build.

Two kinds of test below:

1. **Behavioural**, run through node — the helper is fed the attack and the
   output is inspected. Skipped when node is absent, which is why (2) exists.
2. **Structural**, run in Python — the allow-lists on the two sides must be the
   same set, and the sinks in the templates must be the guarded ones. These
   catch "someone wrote ``set:html={pkg.notes}`` again" with no toolchain at
   all.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from harvest import config
from harvest.ckan_compat import validate_package
from harvest.sanitize import ALLOWED_ATTRIBUTES, ALLOWED_SCHEMES, ALLOWED_TAGS, VOID_TAGS
from harvest.urls import ALLOWED_URL_SCHEMES

SITE = config.repo_root() / "site"
SAFETY = SITE / "src" / "safety.mjs"
FIXTURE = config.fixtures_dir() / "rendering" / "rep-09-hostile-markup.json"

pytestmark = pytest.mark.skipif(not SITE.exists(), reason="site/ is not in this checkout")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def run_in_node(script: str) -> dict:
    """Evaluate `script` against site/src/safety.mjs and return its JSON output.

    The script's last expression is written to stdout as JSON by the wrapper,
    so a test reads like an assertion about the helper rather than about
    subprocess plumbing.
    """
    assert NODE is not None
    module = SAFETY.as_posix()
    source = (
        f"import * as safety from '{module}';\n"
        f"const result = (() => {{ {script} }})();\n"
        "process.stdout.write(JSON.stringify(result));\n"
    )
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        capture_output=True,
        text=True,
        cwd=SITE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestTheHostileFixture:
    """The input every check below is aimed at. It is not decoration."""

    def test_it_exists(self) -> None:
        assert FIXTURE.exists(), (
            "fixtures/rendering/rep-09-hostile-markup.json is the renderer's hostile-input "
            "case — correct it, never delete it"
        )

    def test_it_still_carries_the_attack(self) -> None:
        record = fixture()["record"]
        assert "</script>" in record["title"], "the title no longer breaks out of a <script> block"
        assert "<script" in record["notes"], "the description no longer carries a script tag"
        assert "onerror" in record["notes"], "the description no longer carries an event handler"
        links = json.loads(
            next(e["value"] for e in record["extras"] if e["key"] == "local_links")
        )
        assert any(link["url"].lower().startswith("javascript:") for link in links)

    def test_it_is_a_record_ckan_would_accept(self) -> None:
        """The point is that a *valid* record can still be hostile."""
        violations = validate_package(
            fixture()["record"], config.organization_names(), config.group_names()
        )
        assert violations == [], [str(v) for v in violations]

    def test_its_identifier_cannot_implicate_a_real_work(self) -> None:
        record = fixture()["record"]
        doi = next(e["value"] for e in record["extras"] if e["key"] == "doi")
        assert doi.startswith("10.5072/"), "use the DataCite test prefix, which does not resolve"


@needs_node
class TestJsonLdCannotBreakOut:
    """scrape-01: the critical one, and the reason `jsonForHtml` exists."""

    def test_a_hostile_title_is_escaped(self) -> None:
        title = fixture()["record"]["title"]
        serialised = run_in_node(
            f"return safety.jsonForHtml({{name: {json.dumps(title)}}});"
        )
        assert "<" not in serialised and ">" not in serialised
        assert "</script" not in serialised.lower()
        assert "\\u003c" in serialised

    def test_the_data_survives_the_escaping(self) -> None:
        """Escaping is transport, not editing: the JSON must read back identical."""
        title = fixture()["record"]["title"]
        parsed = run_in_node(
            f"return JSON.parse(safety.jsonForHtml({{name: {json.dumps(title)}}})).name;"
        )
        assert parsed == title

    def test_line_separators_are_escaped_too(self) -> None:
        serialised = run_in_node("return safety.jsonForHtml({name: '\\u2028\\u2029'});")
        assert "\\u2028" in serialised and "\\u2029" in serialised


@needs_node
class TestADescriptionCannotShipScript:
    """site-02: `set:html` with an allow-list in front of it."""

    def test_script_and_handlers_are_removed(self) -> None:
        notes = fixture()["record"]["notes"]
        rendered = run_in_node(f"return safety.safeHtml({json.dumps(notes)});")
        lowered = rendered.lower()
        assert "<script" not in lowered
        assert "onerror" not in lowered
        assert "<img" not in lowered
        assert "<iframe" not in lowered
        assert "javascript:" not in lowered

    def test_the_ordinary_markup_survives(self) -> None:
        notes = fixture()["record"]["notes"]
        rendered = run_in_node(f"return safety.safeHtml({json.dumps(notes)});")
        assert "<p>An ordinary first paragraph, which must survive.</p>" in rendered
        assert '<a href="https://example.org/ok">' in rendered

    @pytest.mark.parametrize(
        "html",
        [
            '<a href="java&#115;cript:alert(1)">x</a>',
            "<a href='&#106;avascript:alert(1)'>x</a>",
            '<a href="  javascript:alert(1)">x</a>',
            '<p onclick="alert(1)">x</p>',
            "<scr<script>ipt>alert(1)</script>",
            '<img src=x onerror=alert(1)>',
            '<svg><script>alert(1)</script></svg>',
        ],
    )
    def test_known_evasions(self, html: str) -> None:
        rendered = run_in_node(f"return safety.safeHtml({json.dumps(html)});")
        lowered = rendered.lower()
        assert "javascript:" not in lowered
        assert "<script" not in lowered
        assert "onerror" not in lowered and "onclick" not in lowered


@needs_node
class TestAnHrefIsSchemeChecked:
    """site-01: the same allow-list the harvest applies, applied again here."""

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JaVaScript:alert(1)",
            " javascript:alert(1)",
            "java\tscript:alert(1)",
            "\x01javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "//evil.example/x",
        ],
    )
    def test_a_hostile_scheme_is_refused(self, url: str) -> None:
        assert run_in_node(f"return safety.safeHref({json.dumps(url)}) ?? null;") is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://zenodo.org/records/1234",
            "http://example.org/a?b=c#d",
            "mailto:data@example.org",
            "ftp://ftp.example.org/bulk/wind.nc",
        ],
    )
    def test_a_linkable_url_survives(self, url: str) -> None:
        assert run_in_node(f"return safety.safeHref({json.dumps(url)}) ?? null;") == url

    def test_the_curator_link_in_the_fixture_is_dropped(self) -> None:
        links = json.loads(
            next(
                e["value"]
                for e in fixture()["record"]["extras"]
                if e["key"] == "local_links"
            )
        )
        kept = run_in_node(f"return safety.safeLinks({json.dumps(links)});")
        assert [link["url"] for link in kept] == ["https://example.org/mirror"]


class TestTheTwoAllowListsAreOne:
    """Duplication is acceptable only while something fails when it drifts."""

    def _exported(self, name: str) -> list:
        assert NODE is not None
        return run_in_node(f"return safety.{name};")

    @needs_node
    def test_url_schemes_match(self) -> None:
        assert set(self._exported("ALLOWED_URL_SCHEMES")) == set(ALLOWED_URL_SCHEMES)

    @needs_node
    def test_html_schemes_match(self) -> None:
        assert set(self._exported("ALLOWED_HTML_SCHEMES")) == set(ALLOWED_SCHEMES)

    @needs_node
    def test_tags_match(self) -> None:
        assert set(self._exported("ALLOWED_TAGS")) == set(ALLOWED_TAGS)
        assert set(self._exported("VOID_TAGS")) == set(VOID_TAGS)

    @needs_node
    def test_attributes_match(self) -> None:
        site = {tag: set(values) for tag, values in run_in_node(
            "return safety.ALLOWED_ATTRIBUTES;"
        ).items()}
        assert site == {tag: set(values) for tag, values in ALLOWED_ATTRIBUTES.items()}


class TestTheSinksAreGuarded:
    """No toolchain required: the templates must *call* the helpers.

    Every one of these is a line that shipped unguarded once. A grep is a poor
    test of behaviour and an excellent test of "somebody added another one".
    """

    def _read(self, *parts: str) -> str:
        return SITE.joinpath(*parts).read_text(encoding="utf-8")

    def test_json_ld_is_serialised_through_the_escaper(self) -> None:
        base = self._read("src", "layouts", "Base.astro")
        assert "jsonForHtml(jsonLd)" in base
        assert "JSON.stringify(jsonLd)" not in base

    def test_the_dcat_export_is_escaped_too(self) -> None:
        source = self._read("src", "pages", "catalog.jsonld.ts")
        assert "jsonForHtml(body" in source
        assert "JSON.stringify(body" not in source

    def test_every_set_html_goes_through_the_sanitiser(self) -> None:
        offenders = []
        for path in sorted(SITE.joinpath("src").rglob("*.astro")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "set:html=" not in line:
                    continue
                if "safeHtml(" in line or "jsonForHtml(" in line:
                    continue
                offenders.append(f"{path.relative_to(SITE)}:{number}: {line.strip()}")
        assert not offenders, (
            "a `set:html` that neither sanitises nor escapes its input: " + "; ".join(offenders)
        )

    def test_rendered_links_go_through_the_scheme_allow_list(self) -> None:
        detail = self._read("src", "components", "RecordDetail.astro")
        assert "safeLinks(localLinksOf(pkg))" in detail
        assert "safeLinks(pkg.resources" in detail
        for component in ("SourceBadge.astro", "WithdrawnBanner.astro", "RetractionFlag.astro"):
            assert "safeHref(" in self._read("src", "components", component), component

    def test_a_withdrawn_record_offers_no_downloads(self) -> None:
        """docs/runbooks/handle-a-withdrawn-record.md §3: never imply it is downloadable."""
        detail = self._read("src", "components", "RecordDetail.astro")
        assert "resources.length > 0 && !withdrawn" in detail
        assert "Files as last seen" in detail

    def test_violet_stays_reserved_for_machine_inference(self) -> None:
        """ADR-0028 §5 / ADR-0039 §4: violet means a model guessed, nothing else.

        A pin is a human overruling a model, so painting it violet inverted the
        signal the whole provenance display rests on (compliance-12).
        """
        detail = self._read("src", "components", "RecordDetail.astro")
        for block in detail.split("{pinned &&")[1:]:
            head = block[:400]
            assert 'tone="violet"' not in head, "the pinned badge/panel is violet again"
            assert 'tone="llm"' not in head, "the pinned badge uses the machine-inference tone"


class TestTheGateExistsAndIsWired:
    """A gate that is not in `npm run build` is a script, not a gate."""

    def test_the_render_gate_is_present(self) -> None:
        assert (SITE / "scripts" / "check-render.mjs").exists()

    def test_it_runs_in_the_build_and_in_the_gates(self) -> None:
        package = json.loads((SITE / "package.json").read_text(encoding="utf-8"))
        for script in ("build", "gates"):
            assert "check-render.mjs" in package["scripts"][script], script

    def test_the_url_gate_checks_slug_uniqueness(self) -> None:
        """site-06: two records with one name silently dropped a page."""
        source = (SITE / "scripts" / "check-urls.mjs").read_text(encoding="utf-8")
        assert "duplicate name" in source
        assert "filename stem" in source
