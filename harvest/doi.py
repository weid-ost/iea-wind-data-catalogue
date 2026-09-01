"""DOI extraction, normalisation, and the resolve-or-drop rule.

**The rule that makes an AI harvester safe:** no identifier a model produced is
ever accepted on trust. Every DOI that an adapter *inferred* — read out of a
page by the Tier-3 extractor, scraped from a README badge, or stated by a
third party about somebody else's deposit — is resolved against DataCite and
then Crossref before it reaches a record, and a DOI that does not resolve
causes the record to be **dropped and logged**, never silently discarded
(fixtures ``iea-05``, ``gh-03``). That is :func:`resolve_or_drop`, and
``ieawind``, ``osti``, ``crossref`` and ``github``'s badge path all go through
it.

The exception, stated plainly so the invariant is not read wider than it is:
``zenodo`` and ``datacite`` take the DOI **from the registry that minted it**,
in the same response that describes the record. Re-resolving DataCite's own
answer against DataCite proves nothing, so those two adapters normalise and
trust. ``wdh`` states a third-party DOI and does *not* currently resolve it
(scrape-12); the source is disabled by default in ``sources.yaml``, and this
sentence is here rather than a claim that it does.

Extraction handles the four shapes that appear on a task page (fixture
``iea-03``) plus the two classic bugs:

* ``iea-02`` — trailing sentence punctuation: ``...zenodo.1234.``
* ``iea-04`` — the DOI wrapped across a line break in the rendered text.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

__all__ = [
    "DOI_RE",
    "DATACITE_ENDPOINT",
    "CROSSREF_ENDPOINT",
    "DoiResolution",
    "DoiDropLog",
    "normalise_doi",
    "extract_dois",
    "rejoin_wrapped_dois",
    "resolve_doi",
    "resolve_or_drop",
]

log = logging.getLogger(__name__)

DATACITE_ENDPOINT = "https://api.datacite.org/dois/"
CROSSREF_ENDPOINT = "https://api.crossref.org/works/"

# The DOI body: a "10." prefix, 4-9 registrant digits, a slash, then a suffix
# drawn from the character set DOIs actually use in the wild.
#
# ``#`` is deliberately NOT in the class. DOI suffix syntax permits it, but in
# the text this harvester reads a ``#`` after a DOI is a *URL fragment* far more
# often than a suffix character — ``https://doi.org/10.1002/we.2745#abstract``
# is one link to one paper. Including it produced ``10.1002/we.2745#abstract``,
# which resolves 404, so resolve-or-drop threw the publication away (scrape-08).
# ``?`` was already excluded for exactly this reason; fragments were an
# oversight, not a policy. The cost is a hypothetical DOI with a literal ``#``
# in its suffix, which no source in ``sources.yaml`` has ever emitted; the
# benefit is that every fragment-linked citation is catalogued.
_DOI_BODY = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9<>\[\]+*]+"

#: Matches a DOI with any of the four common prefixes, capturing the bare DOI.
DOI_RE = re.compile(
    r"(?:doi:\s*|info:doi/|https?://(?:dx\.)?doi\.org/|https?://doi\.org/)?"
    r"(?P<doi>" + _DOI_BODY + r")",
    re.IGNORECASE,
)

_BARE_DOI_RE = re.compile(r"^" + _DOI_BODY + r"$", re.IGNORECASE)

_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "https://www.doi.org/",
    "http://www.doi.org/",
    "doi.org/",
    "dx.doi.org/",
    "info:doi/",
    "doi:",
)

#: The same prefixes, matched *before* embedded whitespace is collapsed, so the
#: spaced spellings work too. ``DOI 10.5281/zenodo.10`` and ``DOI: 10.5281/…``
#: are both ordinary in a reference list; the old ``"doi "`` entry in
#: :data:`_PREFIXES` could never fire because the space had already been
#: removed by the time the tuple was consulted (scrape-08, dead code).
_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"https?://(?:dx\.|www\.)?doi\.org/"
    r"|(?:dx\.|www\.)doi\.org/"
    r"|doi\.org/"
    r"|info:doi/"
    r"|doi\s*:\s*"
    r"|doi\s+(?=10\.)"
    r")",
    re.IGNORECASE,
)

#: Punctuation a DOI never legitimately ends with in running prose.
#:
#: ``/`` is in the set. A DOI's suffix may *contain* a slash but never ends
#: with one, and ``10.5281/zenodo.4549875/`` — the spelling a copy-pasted
#: browser URL produces — used to normalise to a *distinct identity* whose slug
#: was byte-identical to the clean DOI's. Since DataCite answers 200 for the
#: slashed form, resolve-or-drop let it through, it claimed
#: ``events/doi-10-5281-zenodo-4549875.jsonl``, and the real record could then
#: never be written: ``append_event``'s collision guard refused it forever
#: (scrape-05). One citation with a stray slash squatted a record's slug.
_TRAILING_PUNCTUATION = ".,;:!?'\"”’)]}>»/"

# A line break inside a DOI is removed only when the break follows a "/" or a
# "-", or follows a "." that is itself followed by a digit. That last clause is
# what keeps "et al.\nSmith" and "...zenodo.1234.\nThe next citation" intact:
# ordinary prose after a full stop does not begin with a digit.
_WRAP_RE = re.compile(r"(?<=[/\-])[ \t]*\r?\n[ \t]*|(?<=\.)[ \t]*\r?\n[ \t]*(?=\d)")


class _Fetcher(Protocol):
    """The minimal HTTP surface :func:`resolve_doi` needs. ``httpx.Client`` fits."""

    def get(self, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class DoiResolution:
    """The outcome of resolving one DOI."""

    doi: str
    resolved: bool
    agency: str | None = None  # "datacite" | "crossref" | None
    payload: dict | None = None
    reason: str | None = None

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.resolved


@dataclass
class DoiDropLog:
    """Collects every dropped DOI so the run report can list them.

    Dropping is never silent: each drop is logged at WARNING *and* appended
    here, and :meth:`as_notices` feeds ``state/last-run.json``.
    """

    drops: list[dict] = field(default_factory=list)

    def record(self, doi: str, reason: str, context: str | None = None) -> None:
        entry = {"doi": doi, "reason": reason}
        if context:
            entry["context"] = context
        self.drops.append(entry)
        log.warning("dropped DOI %s: %s%s", doi, reason, f" ({context})" if context else "")

    def as_notices(self) -> list[dict]:
        return sorted(self.drops, key=lambda d: (d["doi"], d["reason"]))

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.drops)


def rejoin_wrapped_dois(text: str) -> str:
    """Undo soft line-wrapping inside DOIs before matching (fixture ``iea-04``)."""
    return _WRAP_RE.sub("", str(text))


def _strip_trailing(candidate: str) -> str:
    """Strip trailing prose punctuation, keeping balanced brackets intact."""
    result = candidate
    while result and result[-1] in _TRAILING_PUNCTUATION:
        if result[-1] == ")" and result.count("(") > result.count(")"):
            break
        if result[-1] == "]" and result.count("[") > result.count("]"):
            break
        result = result[:-1]
    return result


def normalise_doi(value: str | None) -> str | None:
    """Normalise any DOI spelling to the canonical bare lowercase form.

    ``https://doi.org/10.5281/ZENODO.123.`` -> ``10.5281/zenodo.123``

    Returns ``None`` when the input is not a syntactically valid DOI. Never
    raises — callers treat ``None`` as "no DOI here".
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Prefixes first, while the spacing is still intact ("DOI 10.5281/…"), then
    # again after the collapse, which catches a prefix split across a line break.
    while True:
        shortened = _PREFIX_RE.sub("", text, count=1)
        if shortened == text:
            break
        text = shortened

    text = "".join(text.split())  # kill embedded whitespace and newlines

    # A URL fragment or query string is part of the *link*, not of the DOI.
    # Splitting here (rather than only excluding the characters from the body
    # class) means the whole-string form normalises too, so
    # normalise_doi("https://doi.org/10.1002/we.2745#abstract") is the paper
    # rather than None (scrape-08).
    for delimiter in ("#", "?"):
        text = text.split(delimiter, 1)[0]

    lowered = text.lower()
    changed = True
    while changed:
        changed = False
        for prefix in _PREFIXES:
            if lowered.startswith(prefix):
                text = text[len(prefix) :]
                lowered = text.lower()
                changed = True
                break

    text = text.strip().strip("<>")
    text = _strip_trailing(text)
    if not text:
        return None
    if not _BARE_DOI_RE.match(text):
        return None
    return text.lower()


def extract_dois(text: str) -> list[str]:
    """Extract every distinct, syntactically valid DOI from free text.

    Order of first appearance is preserved. Line-wrapped DOIs are rejoined
    first; prefixes and trailing punctuation are normalised away. This is
    *syntactic* only — nothing here proves a DOI exists. Pass the result
    through :func:`resolve_or_drop` before it reaches a record.
    """
    joined = rejoin_wrapped_dois(text or "")
    seen: dict[str, None] = {}
    for match in DOI_RE.finditer(joined):
        normalised = normalise_doi(match.group("doi"))
        if normalised and normalised not in seen:
            seen[normalised] = None
    return list(seen)


def resolve_doi(doi: str, client: _Fetcher, timeout: float = 20.0) -> DoiResolution:
    """Resolve ``doi`` against DataCite, then Crossref.

    ``client`` is injected (``httpx.Client`` in production, a fake in tests) so
    that nothing in the test suite touches the network. Any transport error is
    treated as *unresolved*, never as an exception: a resolver outage must
    degrade the run, not fail it.
    """
    normalised = normalise_doi(doi)
    if not normalised:
        return DoiResolution(doi=str(doi), resolved=False, reason="malformed")

    for agency, endpoint in (("datacite", DATACITE_ENDPOINT), ("crossref", CROSSREF_ENDPOINT)):
        url = endpoint + normalised
        try:
            response = client.get(url, timeout=timeout)
        except Exception as exc:  # transport, DNS, TLS, timeout
            log.info("DOI resolver %s unreachable for %s: %s", agency, normalised, exc)
            continue
        status = getattr(response, "status_code", None)
        if status == 200:
            try:
                payload = response.json()
            except Exception:
                payload = None
            return DoiResolution(doi=normalised, resolved=True, agency=agency, payload=payload)
        if status in (404, 410):
            continue
        log.info("DOI resolver %s returned %s for %s", agency, status, normalised)

    return DoiResolution(doi=normalised, resolved=False, reason="did-not-resolve")


def resolve_or_drop(
    doi: str,
    client: _Fetcher,
    drop_log: DoiDropLog | None = None,
    context: str | None = None,
) -> DoiResolution | None:
    """Return the resolution, or ``None`` and a logged drop.

    This is the only sanctioned way for a DOI to enter a record.
    """
    resolution = resolve_doi(doi, client)
    if resolution.resolved:
        return resolution
    if drop_log is not None:
        drop_log.record(resolution.doi, resolution.reason or "did-not-resolve", context)
    else:
        log.warning(
            "dropped DOI %s: %s%s",
            resolution.doi,
            resolution.reason,
            f" ({context})" if context else "",
        )
    return None


def resolve_all(
    dois: Iterable[str],
    client: _Fetcher,
    drop_log: DoiDropLog | None = None,
    context: str | None = None,
) -> list[DoiResolution]:
    """Resolve many DOIs, keeping only those that resolved."""
    kept: list[DoiResolution] = []
    for doi in dois:
        resolution = resolve_or_drop(doi, client, drop_log, context)
        if resolution is not None:
            kept.append(resolution)
    return kept
