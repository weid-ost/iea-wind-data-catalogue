"""What the built site promises, checked without building it.

The heavy end of this lives in ``site/scripts/check-render.mjs``, which reads
``dist/`` after a build and is wired into ``npm run build`` and ``npm run
gates``. These are the cheap half: the promises are *in the source*, so a
Python-only checkout still fails when one is quietly dropped. Each maps to a
finding the site shipped with:

* **product-e2e-02 / site-04** — ADR-0023 §3 names six facets; the institution
  one existed in code but had nothing to count, and nothing asserted the count.
* **compliance-03** — ADR-0023 wants Google Dataset Search to index the
  catalogue, so a record that holds data must be typed ``Dataset``.
* **site-03** — DCAT expects a licence IRI, and a human label is not one.
* **product-e2e-05** — link rot is the failure mode the catalogue exists to
  fight, and ``state/link-check.json`` was rendered nowhere.
* **product-e2e-06** — "5 softwares · 2 others": slugs are not English.
* **product-e2e-07 / site-08** — the sitemap declares the catalogue's real
  locations. Search and browse were merged into the index (``/``), whose
  pagination is a ``?page=N`` query the island applies over the embedded set, so
  there is one indexable catalogue location rather than a paginated path chain.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harvest import config

SITE = config.repo_root() / "site"

pytestmark = pytest.mark.skipif(not SITE.exists(), reason="site/ is not in this checkout")

#: ADR-0023 §3, in the words the ADR uses.
ADR_0023_FACETS = ("task", "kind", "year", "licence", "source", "institution")


def read(*parts: str) -> str:
    return SITE.joinpath(*parts).read_text(encoding="utf-8")


class TestTheSixFacets:
    def test_the_facet_module_declares_all_six(self) -> None:
        source = read("src", "lib", "facets.ts")
        missing = [facet for facet in ADR_0023_FACETS if f"name: '{facet}'" not in source]
        assert not missing, f"facets.ts declares no {missing} facet (ADR-0023 §3 names six)"

    def test_the_record_page_declares_the_institution_filter(self) -> None:
        """A facet Pagefind never sees on a record page cannot have values."""
        detail = read("src", "components", "RecordDetail.astro")
        assert "institution:${pkg.owner_org}" in detail

    def test_every_record_carries_an_owning_organisation(self) -> None:
        """Without `owner_org` the facet is filtered out and vanishes silently."""
        records = sorted((config.repo_root() / "records").glob("*.json"))
        if not records:
            pytest.skip("records/ is empty in this checkout")
        orgless = [
            path.name
            for path in records
            if not json.loads(path.read_text(encoding="utf-8")).get("owner_org")
        ]
        assert not orgless, f"records with no owner_org — the institution facet loses them: {orgless}"

    def test_the_gate_checks_the_rendered_facet_set(self) -> None:
        gate = read("scripts", "check-render.mjs")
        for facet in ADR_0023_FACETS:
            assert f"'{facet}'" in gate


class TestSchemaOrgTyping:
    def test_a_record_that_holds_data_is_typed_dataset(self) -> None:
        types = read("src", "schema-types.mjs")
        assert "dataset: 'Dataset'" in types
        assert "model: 'Dataset'" in types

    def test_the_narrowing_is_documented_where_it_bites(self) -> None:
        """ADR-0023 says Dataset on every page; this catalogue is not all data.

        The decision may be argued with — but not silently. Whoever changes it
        should have to read why it is what it is.
        """
        types = read("src", "schema-types.mjs")
        assert "ADR-0023" in types
        assert "Dataset Search" in types
        assert "schema-types.mjs" in read("README.md")

    def test_the_gate_enforces_it(self) -> None:
        assert "DATASET_KINDS" in read("scripts", "check-render.mjs")


class TestTheDcatExport:
    def test_an_unmapped_licence_is_omitted_not_invented(self) -> None:
        jsonld = read("src", "lib", "jsonld.ts")
        assert "'dct:license': licenseUrl(pkg.license_id)," in jsonld
        assert "licenseTitleOf" not in jsonld, (
            "the DCAT export is emitting a human licence label where an IRI belongs (site-03)"
        )


class TestLinkRotIsRendered:
    """product-e2e-05: the checker's output has to reach a human."""

    FIXTURE = config.fixtures_dir() / "rendering" / "ui" / "r-09-dead-link.json"

    def test_the_state_file_is_read(self) -> None:
        state = read("src", "lib", "state.ts")
        assert "link-check.json" in state
        assert "deadLinksFor" in state

    def test_the_record_page_renders_the_note(self) -> None:
        detail = read("src", "components", "RecordDetail.astro")
        assert "LinkRotNote" in detail
        assert "deadLinksFor(pkg.name" in detail

    def test_the_note_never_deletes_anything(self) -> None:
        """ADR-0027: a dead link is a fact about a check, not a reason to hide."""
        note = read("src", "components", "LinkRotNote.astro")
        assert "kept exactly as it was" in note

    def test_the_fixture_exists_and_the_gallery_renders_it(self) -> None:
        assert self.FIXTURE.exists()
        fixture = json.loads(self.FIXTURE.read_text(encoding="utf-8"))
        assert fixture["fixture_kind"] == "ui_state"
        assert fixture["link_check"]["dead_by_record"], "a fixture with no dead link proves nothing"
        assert "r-09-dead-link" in read("src", "pages", "dev", "components.astro")


class TestTheHomepageCounts:
    def test_kind_counts_are_not_slugs_with_an_s(self) -> None:
        # The catalogue page surfaces per-kind counts through the ``kind`` facet
        # (a numeric count beside a ``RESOURCE_KIND_LABELS`` legend), not an
        # inline pluralised noun, so the naive ``${kind}s`` that produced "5
        # softwares" must not reappear on it (product-e2e-06).
        index = read("src", "pages", "index.astro")
        assert "${kind}${n === 1 ? '' : 's'}" not in index

    def test_software_has_no_plural_s(self) -> None:
        record = read("src", "lib", "record.ts")
        assert "software: 'software'" in record
        assert "other: 'other records'" in record


class TestTheSitemap:
    def test_the_catalogue_and_records_are_declared(self) -> None:
        # Search and browse merged into ``/``; the sitemap declares the catalogue
        # index, the about page and every record page — and no longer a browse
        # pagination chain or a separate search page (product-e2e-07 / site-08).
        sitemap = read("src", "pages", "sitemap.xml.ts")
        assert "${base}/`" in sitemap
        assert "${base}/about/`" in sitemap
        assert "/record/${entry.pkg.name}/" in sitemap
        assert "browse" not in sitemap
        assert "/search/" not in sitemap

    def test_the_catalogue_page_owns_its_page_size(self) -> None:
        # One page-size constant, imported by the page that paginates, so the two
        # cannot drift (the invariant the old browse route and sitemap shared).
        assert "CATALOGUE_PAGE_SIZE = 20" in read("src", "lib", "paginate.ts")
        index = read("src", "pages", "index.astro")
        assert "CATALOGUE_PAGE_SIZE" in index
        assert "pageSize={CATALOGUE_PAGE_SIZE}" in index

    def test_the_gallery_stays_out_of_it(self) -> None:
        assert "/dev/" not in read("src", "pages", "sitemap.xml.ts").split("APIRoute")[1]
