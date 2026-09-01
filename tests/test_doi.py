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


class TestATrailingSlashIsNotAnIdentity:
    """scrape-05: one stray slash could permanently squat a record's slug.

    ``/`` was inside the DOI character class and outside the trailing-punctuation
    set, so ``10.5281/zenodo.4549875/`` — the spelling you get by copying a
    browser URL — normalised to a *distinct identity key*. DataCite answers 200
    for it, so resolve-or-drop waved it through; its slug is byte-identical to
    the clean DOI's, so it claimed ``events/doi-10-5281-zenodo-4549875.jsonl``;
    and from then on the real record could never be written, because
    ``append_event``'s collision guard refused it. One citation on one task page
    was enough to delete a record from the catalogue.
    """

    def test_a_trailing_slash_is_stripped(self) -> None:
        assert normalise_doi("10.5281/zenodo.4549875/") == "10.5281/zenodo.4549875"

    def test_the_slashed_and_clean_spellings_are_one_identity(self) -> None:
        assert normalise_doi("https://doi.org/10.5281/zenodo.4549875/") == normalise_doi(
            "10.5281/zenodo.4549875"
        )

    def test_the_sweep_extracts_the_clean_form(self) -> None:
        text = "OpenOA is archived at https://doi.org/10.5281/zenodo.4549875/ and cited widely."
        assert extract_dois(text) == ["10.5281/zenodo.4549875"]

    def test_a_registrant_prefix_with_nothing_after_it_is_not_a_doi(self) -> None:
        assert normalise_doi("10.5281/") is None


class TestAUrlFragmentIsNotPartOfTheDoi:
    """scrape-08: a fragment-linked citation was dropped instead of catalogued.

    ``#`` was in the DOI character class, so
    ``https://doi.org/10.1002/we.2745#abstract`` yielded
    ``10.1002/we.2745#abstract``, which 404s at DataCite and Crossref — so
    resolve-or-drop discarded a perfectly good publication. Query strings were
    already handled, which is what shows fragments were an oversight rather
    than a policy.
    """

    def test_a_fragment_is_stripped(self) -> None:
        assert normalise_doi("https://doi.org/10.1002/we.2745#abstract") == "10.1002/we.2745"

    def test_a_query_string_is_stripped(self) -> None:
        assert normalise_doi("https://doi.org/10.1002/we.2745?utm_source=x") == "10.1002/we.2745"

    def test_the_sweep_stops_at_the_fragment(self) -> None:
        assert extract_dois("see https://doi.org/10.1002/we.2745#abstract now") == [
            "10.1002/we.2745"
        ]

    def test_the_spaced_doi_prefix_is_no_longer_dead_code(self) -> None:
        """`DOI 10.5281/...` is ordinary in a reference list.

        The ``"doi "`` entry in the prefix table could never fire: whitespace
        was collapsed before the table was consulted, so by then the string
        read ``doi10.5281/...``.
        """
        assert normalise_doi("DOI 10.5281/zenodo.10") == "10.5281/zenodo.10"
        assert normalise_doi("doi: 10.5281/zenodo.10") == "10.5281/zenodo.10"
        assert normalise_doi("DOI:10.5281/zenodo.10") == "10.5281/zenodo.10"

    def test_an_ordinary_doi_with_punctuation_in_its_suffix_is_untouched(self) -> None:
        """The character class narrowed; it must not have narrowed too far."""
        assert normalise_doi("10.2314/KXP:1790028361") == "10.2314/kxp:1790028361"
        assert normalise_doi("10.1088/1742-6596/2265/2/022001") == "10.1088/1742-6596/2265/2/022001"
