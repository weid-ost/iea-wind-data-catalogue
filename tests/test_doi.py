"""DOI extraction, normalisation and resolve-or-drop — iea-02, iea-03, iea-04, iea-05."""

from __future__ import annotations

import logging

import pytest

from harvest.doi import (
    DoiDropLog,
    extract_dois,
    normalise_doi,
    rejoin_wrapped_dois,
    resolve_all,
    resolve_doi,
    resolve_or_drop,
)


class TestNormalise:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10.5281/zenodo.1234", "10.5281/zenodo.1234"),
            ("10.5281/ZENODO.1234", "10.5281/zenodo.1234"),
            ("doi:10.5281/zenodo.1234", "10.5281/zenodo.1234"),
            ("DOI: 10.5281/zenodo.1234", "10.5281/zenodo.1234"),
            ("https://doi.org/10.5281/zenodo.1234", "10.5281/zenodo.1234"),
            ("http://dx.doi.org/10.5281/zenodo.1234", "10.5281/zenodo.1234"),
            ("https://www.doi.org/10.5281/zenodo.1234", "10.5281/zenodo.1234"),
            ("info:doi/10.5281/zenodo.1234", "10.5281/zenodo.1234"),
            ("<10.5281/zenodo.1234>", "10.5281/zenodo.1234"),
            ("10.5281/zenodo.1234.", "10.5281/zenodo.1234"),
            ("10.5281/zenodo.1234,", "10.5281/zenodo.1234"),
            ("10.5281/zenodo.1234;", "10.5281/zenodo.1234"),
            ("10.5281/zenodo.1234).", "10.5281/zenodo.1234"),
            ("10.5281/zeno\ndo.1234", "10.5281/zenodo.1234"),
        ],
    )
    def test_normalises(self, raw: str, expected: str) -> None:
        assert normalise_doi(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "not a doi", "10.5281", "11.5281/x",
                                     "https://example.org/thing"])
    def test_rejects(self, raw) -> None:  # noqa: ANN001
        assert normalise_doi(raw) is None

    def test_balanced_brackets_survive(self) -> None:
        assert normalise_doi("10.1002/(sici)1099-1824") == "10.1002/(sici)1099-1824"


class TestExtract:
    def test_iea_02_trailing_full_stop(self) -> None:
        """The classic regex bug: a sentence-final DOI keeps the full stop."""
        text = "See Smith et al., https://doi.org/10.5281/zenodo.1234. The next citation follows."
        assert extract_dois(text) == ["10.5281/zenodo.1234"]

    def test_iea_02_variants(self) -> None:
        for suffix in (".", ",", ";", ")", "].", '".'):
            text = f"available at 10.5281/zenodo.1234{suffix}"
            assert extract_dois(text) == ["10.5281/zenodo.1234"], suffix

    def test_iea_03_four_prefixes_are_one_doi(self) -> None:
        """All four spellings on one page normalise to the same identity."""
        page = """
        Cited as doi:10.5281/zenodo.1234 in one place,
        as https://doi.org/10.5281/zenodo.1234 in another,
        as dx.doi.org/10.5281/zenodo.1234 in a third,
        and bare: 10.5281/zenodo.1234
        """
        assert extract_dois(page) == ["10.5281/zenodo.1234"]

    def test_iea_04_linebreak_after_slash(self) -> None:
        text = "Available from https://doi.org/10.5281/\nzenodo.1234 (accessed 2026)."
        assert "10.5281/zenodo.1234" in extract_dois(text)

    def test_iea_04_linebreak_after_dot_before_digit(self) -> None:
        text = "Dataset DOI 10.5281/zenodo.\n1234 was used throughout."
        assert "10.5281/zenodo.1234" in extract_dois(text)

    def test_iea_04_linebreak_after_hyphen(self) -> None:
        text = "See 10.1016/j.renene.2021.01-\n234 for details."
        assert "10.1016/j.renene.2021.01-234" in extract_dois(text)

    def test_rejoin_does_not_glue_prose(self) -> None:
        """A full stop followed by prose is a sentence end, not a wrapped DOI."""
        text = "Published at 10.5281/zenodo.1234.\nThe following section explains."
        assert extract_dois(text) == ["10.5281/zenodo.1234"]
        assert "The following" in rejoin_wrapped_dois(text)

    def test_multiple_dois_keep_page_order_and_dedupe(self) -> None:
        page = "10.1002/we.2537 then 10.5281/zenodo.1 then 10.1002/WE.2537 again"
        assert extract_dois(page) == ["10.1002/we.2537", "10.5281/zenodo.1"]

    def test_empty_input(self) -> None:
        assert extract_dois("") == []
        assert extract_dois(None) == []  # type: ignore[arg-type]


class TestResolveOrDrop:
    def test_resolves_against_datacite(self, fake_client) -> None:  # noqa: ANN001
        client = fake_client({"10.5281/zenodo.1234": "datacite"})
        result = resolve_doi("https://doi.org/10.5281/ZENODO.1234", client)
        assert result.resolved and result.agency == "datacite"
        assert result.doi == "10.5281/zenodo.1234"

    def test_falls_through_to_crossref(self, fake_client) -> None:  # noqa: ANN001
        client = fake_client({"10.1002/we.2537": "crossref"})
        result = resolve_doi("10.1002/we.2537", client)
        assert result.resolved and result.agency == "crossref"
        assert client.calls[0].startswith("https://api.datacite.org/")

    def test_iea_05_non_resolving_is_dropped_and_logged(self, fake_client, caplog) -> None:  # noqa: ANN001
        client = fake_client({})
        drops = DoiDropLog()
        with caplog.at_level(logging.WARNING):
            assert resolve_or_drop("10.5281/zenodo.99999999", client, drops, "task43 page") is None
        assert len(drops) == 1
        assert drops.drops[0]["doi"] == "10.5281/zenodo.99999999"
        assert drops.drops[0]["reason"] == "did-not-resolve"
        assert "dropped DOI" in caplog.text  # never silent

    def test_malformed_doi_is_dropped_not_raised(self, fake_client) -> None:  # noqa: ANN001
        drops = DoiDropLog()
        assert resolve_or_drop("nonsense", fake_client({}), drops) is None
        assert drops.drops[0]["reason"] == "malformed"

    def test_resolver_outage_degrades_rather_than_raising(self, fake_client) -> None:  # noqa: ANN001
        client = fake_client({"10.1002/we.1": "crossref"}, raise_for={"datacite"})
        assert resolve_doi("10.1002/we.1", client).resolved is True

    def test_total_outage_is_unresolved_not_an_exception(self, fake_client) -> None:  # noqa: ANN001
        client = fake_client({"10.1002/we.1": "crossref"}, raise_for={"datacite", "crossref"})
        assert resolve_doi("10.1002/we.1", client).resolved is False

    def test_resolve_all_keeps_only_resolvable(self, fake_client) -> None:  # noqa: ANN001
        client = fake_client({"10.5281/zenodo.good": "datacite"})
        drops = DoiDropLog()
        kept = resolve_all(["10.5281/zenodo.good", "10.5281/zenodo.bad"], client, drops)
        assert [r.doi for r in kept] == ["10.5281/zenodo.good"]
        assert [d["doi"] for d in drops.as_notices()] == ["10.5281/zenodo.bad"]
