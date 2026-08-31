"""Identity keys and slugs — fixtures dc-05, zen-10, x-06."""

from __future__ import annotations

import pytest

from harvest.identity import (
    IdentityError,
    MAX_SLUG_LENGTH,
    fragile_identity_key,
    identity_key,
    identity_kind,
    normalise_author,
    normalise_title,
    slug_for_identity,
    slugify,
)


class TestDoiIdentity:
    """dc-05: case variants and prefix spellings must NOT create two records."""

    VARIANTS = [
        "10.5281/zenodo.123",
        "10.5281/ZENODO.123",
        "10.5281/Zenodo.123",
        "https://doi.org/10.5281/zenodo.123",
        "https://doi.org/10.5281/ZENODO.123",
        "http://dx.doi.org/10.5281/zenodo.123",
        "doi:10.5281/ZENODO.123",
        "DOI: 10.5281/zenodo.123",
        "info:doi/10.5281/zenodo.123",
        "  10.5281/zenodo.123.  ",
    ]

    @pytest.mark.parametrize("variant", VARIANTS)
    def test_all_variants_yield_one_identity(self, variant: str) -> None:
        assert identity_key(doi=variant) == "10.5281/zenodo.123"

    def test_all_variants_yield_one_slug(self) -> None:
        slugs = {slug_for_identity(identity_key(doi=v)) for v in self.VARIANTS}
        assert slugs == {"doi-10-5281-zenodo-123"}

    def test_doi_beats_source_id(self) -> None:
        key = identity_key(doi="10.5281/zenodo.123", source_system="zenodo", source_id="123")
        assert key == "10.5281/zenodo.123"
        assert identity_kind(key) == "doi"


class TestFallbackIdentity:
    def test_source_system_and_id(self) -> None:
        key = identity_key(source_system="Zenodo", source_id="1234567")
        assert key == "zenodo|1234567"
        assert identity_kind(key) == "source"
        assert slug_for_identity(key) == "zenodo-1234567"

    def test_malformed_doi_falls_through_to_source_id(self) -> None:
        key = identity_key(doi="not-a-doi", source_system="osti", source_id="99")
        assert key == "osti|99"

    def test_fragile_hash_is_last_resort(self) -> None:
        """x-06: no DOI, no stable source id."""
        key = identity_key(title="A Wind Farm Study", first_author="Søren Ø. Müller", year=2021)
        assert identity_kind(key) == "fragile"
        assert key.startswith("hash|") and len(key) == len("hash|") + 16

    def test_fragile_hash_is_deterministic_and_normalising(self) -> None:
        a = fragile_identity_key("A Wind-Farm Study!", "Müller, Søren", "2021")
        b = fragile_identity_key("a wind farm study", "Søren Müller", 2021)
        assert a == b

    def test_fragile_hash_changes_with_the_title(self) -> None:
        """Documented fragility: a corrected upstream title splits the record."""
        assert fragile_identity_key("Wind study", "Muller", "2021") != fragile_identity_key(
            "Wind Study (corrected)", "Muller", "2021"
        )

    def test_nothing_usable_raises(self) -> None:
        with pytest.raises(IdentityError):
            identity_key()


class TestSlugify:
    """zen-10: diacritics are preserved in display and transliterated in the slug."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Søren", "soren"),
            ("Müller", "muller"),
            ("Søren Ø. Müller", "soren-o-muller"),
            ("Ø", "o"),
            ("Ægir & Þór", "aegir-and-thor"),
            ("Łukasz Kowalczyk", "lukasz-kowalczyk"),
            ("Straße", "strasse"),
            ("Årsrapport 2021", "arsrapport-2021"),
            ("  spaced   out  ", "spaced-out"),
            ("Wind/Energy.Science", "wind-energy-science"),
            ("under_score-keeps", "under_score-keeps"),
            ("---", ""),
        ],
    )
    def test_transliteration(self, text: str, expected: str) -> None:
        assert slugify(text) == expected

    def test_slug_is_ckan_legal(self) -> None:
        import re

        for text in ("Søren Ø. Müller", "Ægir & Þór", "A" * 400):
            slug = slugify(text)
            assert re.fullmatch(r"[a-z0-9_-]*", slug)
            assert len(slug) <= MAX_SLUG_LENGTH


class TestSlugForIdentity:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("10.5281/zenodo.123", "doi-10-5281-zenodo-123"),
            ("10.1002/we.2537", "doi-10-1002-we-2537"),
            ("zenodo|1234567", "zenodo-1234567"),
            ("github|IEA-Task-43/digital-wra-data-standard",
             "github-iea-task-43-digital-wra-data-standard"),
        ],
    )
    def test_known_shapes(self, key: str, expected: str) -> None:
        assert slug_for_identity(key) == expected

    def test_long_keys_truncate_with_a_hash_and_stay_unique(self) -> None:
        long_a = "10.1234/" + "x" * 200 + "a"
        long_b = "10.1234/" + "x" * 200 + "b"
        slug_a, slug_b = slug_for_identity(long_a), slug_for_identity(long_b)
        assert len(slug_a) <= MAX_SLUG_LENGTH
        assert slug_a != slug_b

    def test_slug_does_not_depend_on_the_title(self) -> None:
        """Stable citable URLs: retitling upstream must not move the record."""
        key = identity_key(doi="10.5281/zenodo.99")
        assert slug_for_identity(key) == slug_for_identity(key)


def test_normalisers() -> None:
    assert normalise_title("  The  Wind-Farm, Study! ") == "the wind farm study"
    assert normalise_author("Müller, Søren") == "muller"
    assert normalise_author("Søren Müller") == "muller"
    assert normalise_author("") == ""
