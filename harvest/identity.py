"""Identity keys and slugs.

An **identity key** is the catalogue's notion of "the same artifact". It is
derived by a strict three-step preference order:

1. ``10.5281/zenodo.123``      — the lowercase-normalised DOI, when one exists.
2. ``zenodo|1234567``          — ``source_system|source_id``, when the source
                                 has a stable identifier of its own.
3. ``hash|<16 hex>``           — a deterministic hash of normalised
                                 title + first author + year. **Fragile.** It
                                 changes if the upstream title is corrected, so
                                 a corrected title creates a second record.
                                 Fixture ``x-06`` exists to keep this honest.

A **slug** is the filesystem- and CKAN-safe rendering of an identity key. It is
the CKAN ``package.name``, the record filename stem (``records/<slug>.json``),
the event-log filename stem (``events/<slug>.jsonl``) and the site URL segment
(``/record/<slug>/``). It is derived from the identity key **and nothing else**,
so it is stable across metadata edits: a retitled dataset keeps its URL.

Diacritics are transliterated, never dropped: ``Søren`` -> ``soren``,
``Müller`` -> ``muller`` (fixture ``zen-10``). Display text keeps them.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = [
    "IdentityError",
    "MAX_SLUG_LENGTH",
    "identity_key",
    "identity_kind",
    "slugify",
    "slug_for_identity",
    "disambiguated_slug",
    "normalise_title",
    "normalise_author",
    "fragile_identity_key",
]


class IdentityError(ValueError):
    """Raised when no identity key can be derived from the inputs given."""


#: CKAN caps ``package.name`` at 100 characters.
MAX_SLUG_LENGTH = 100

#: Characters NFKD will not decompose; map them explicitly.
_TRANSLITERATIONS = {
    "ø": "o", "Ø": "o",
    "æ": "ae", "Æ": "ae",
    "œ": "oe", "Œ": "oe",
    "å": "a", "Å": "a",
    "ß": "ss",
    "đ": "d", "Đ": "d",
    "ð": "d", "Ð": "d",
    "þ": "th", "Þ": "th",
    "ł": "l", "Ł": "l",
    "ı": "i", "İ": "i",
    "ħ": "h", "Ħ": "h",
    "ŋ": "n", "Ŋ": "n",
    "ĸ": "k",
    "µ": "u",
    "№": "no",
    "&": " and ",
    "'": "", "’": "",
}

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9_-]+")
_SLUG_COLLAPSE_RE = re.compile(r"-{2,}")
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _transliterate(text: str) -> str:
    """Fold a Unicode string to ASCII, expanding what NFKD cannot decompose."""
    for source, target in _TRANSLITERATIONS.items():
        text = text.replace(source, target)
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.encode("ascii", "ignore").decode("ascii")


def slugify(text: str, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Transliterate ``text`` to a CKAN-legal slug: ``[a-z0-9_-]``, 2-100 chars.

    ``Søren Ø. Müller`` -> ``soren-o-muller`` (fixture ``zen-10``).

    An empty or wholly non-Latin input yields ``""``; callers must supply a
    fallback (``slug_for_identity`` always does).
    """
    folded = _transliterate(str(text)).lower()
    folded = folded.replace("/", "-").replace(".", "-").replace("|", "-")
    folded = folded.replace(" ", "-").replace("\t", "-")
    folded = _SLUG_STRIP_RE.sub("-", folded)
    folded = _SLUG_COLLAPSE_RE.sub("-", folded).strip("-_")
    if len(folded) > max_length:
        folded = folded[:max_length].rstrip("-_")
    return folded


def normalise_title(title: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace, for hashing only."""
    folded = _transliterate(str(title)).lower()
    folded = _PUNCT_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", folded).strip()


def normalise_author(author: str) -> str:
    """Normalise an author string to ``surname`` for hashing only.

    Accepts ``Müller, Søren`` and ``Søren Müller`` and yields ``muller`` for
    both. Deliberately crude: this feeds the fragile identity path only.
    """
    text = str(author).strip()
    if not text:
        return ""
    if "," in text:
        surname = text.split(",", 1)[0]
    else:
        surname = text.split()[-1]
    return normalise_title(surname).replace(" ", "")


def fragile_identity_key(title: str, first_author: str = "", year: str | int = "") -> str:
    """The last-resort identity key: ``hash|<16 hex>`` (fixture ``x-06``).

    Fragile by construction — see the module docstring. Always logged as such
    by the caller so the population using it stays visible in the run report.
    """
    payload = "\x1f".join(
        (
            normalise_title(title),
            normalise_author(first_author),
            str(year or "").strip(),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"hash|{digest}"


def identity_key(
    *,
    doi: str | None = None,
    source_system: str | None = None,
    source_id: str | None = None,
    title: str | None = None,
    first_author: str | None = None,
    year: str | int | None = None,
) -> str:
    """Derive the identity key, preferring DOI > source id > fragile hash.

    ``doi`` is normalised here (case, prefixes, trailing punctuation) via
    :func:`harvest.doi.normalise_doi`, so ``10.5281/ZENODO.123``,
    ``https://doi.org/10.5281/zenodo.123`` and ``doi:10.5281/zenodo.123.``
    all produce one identity (fixture ``dc-05``).

    Raises :class:`IdentityError` when nothing usable was supplied.
    """
    from harvest.doi import normalise_doi  # local import: avoids a cycle

    if doi:
        normalised = normalise_doi(doi)
        if normalised:
            return normalised

    if source_system and source_id:
        system = str(source_system).strip().lower()
        identifier = str(source_id).strip()
        if system and identifier:
            return f"{system}|{identifier}"

    if title:
        return fragile_identity_key(title, first_author or "", year or "")

    raise IdentityError(
        "cannot derive an identity key: need a DOI, a source_system+source_id, "
        "or at minimum a title"
    )


def identity_kind(key: str) -> str:
    """``'doi'`` | ``'source'`` | ``'fragile'`` — which rule produced ``key``."""
    if key.startswith("hash|"):
        return "fragile"
    if key.startswith("10.") and "/" in key:
        return "doi"
    if "|" in key:
        return "source"
    return "source"


def slug_for_identity(key: str) -> str:
    """Render an identity key as its slug — the CKAN name and file stem.

    ``10.5281/zenodo.123``  -> ``doi-10-5281-zenodo-123``
    ``zenodo|1234567``      -> ``zenodo-1234567``
    ``hash|ab12cd34...``    -> ``hash-ab12cd34...``

    Over-long keys (long OSTI paths, long DOI suffixes) are truncated and given
    an 8-hex suffix of the full key.

    **This mapping is not injective, and pretending otherwise was the bug**
    (site-07). :func:`slugify` folds ``.``, ``/``, ``:``, ``|`` and every other
    non-``[a-z0-9_-]`` character to ``-``, so ``10.2314/KXP:1790028361``,
    ``10.2314/KXP.1790028361`` and ``10.2314/KXP-1790028361`` — three DOIs a
    registry may legitimately mint, and the first of which is a live record in
    this catalogue — all render to ``doi-10-2314-kxp-1790028361``. Truncation
    was given a hash suffix; collision was not, so the second identity to
    arrive simply got no record file and failed the run.

    Suffixing *every* slug would be injective and would also rewrite every URL
    in the catalogue for a case that occurs roughly never, so the resolution
    lives where the ambiguity is actually observable: the event log knows which
    identity owns which file, and :func:`harvest.events.event_path` hands a
    late arrival its :func:`disambiguated_slug` instead of refusing it. First
    writer keeps the plain slug — so no existing URL moves — and the loser gets
    a stable, distinct one rather than being dropped.
    """
    kind = identity_kind(key)
    prefix = "doi-" if kind == "doi" else ""
    body = slugify(prefix + key, max_length=MAX_SLUG_LENGTH)
    if not body:
        body = "record"
    if len(body) < 2:
        body = f"{body}-record"
    if len(slugify(prefix + key, max_length=10_000)) > MAX_SLUG_LENGTH:
        body = _with_digest(body, key)
    return body


def _with_digest(body: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    return f"{body[: MAX_SLUG_LENGTH - 9].rstrip('-_')}-{digest}"


def disambiguated_slug(key: str) -> str:
    """The slug this identity takes when another already owns its plain one.

    Deterministic in the key alone (an 8-hex sha256 suffix), so a record that
    lands here keeps its URL for as long as the collision stands — and, because
    the suffix is derived from the *identity key* rather than from arrival
    order, replaying the whole event log reproduces the same allocation.
    """
    return _with_digest(slug_for_identity(key), key)
