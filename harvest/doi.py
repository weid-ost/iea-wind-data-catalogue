"""DOI extraction, normalisation, and the resolve-or-drop rule.

**The rule that makes an AI harvester safe:** no identifier a model produced is
ever accepted on trust. Every DOI that reaches a record has been resolved
against DataCite and then Crossref. A DOI that does not resolve causes the
record to be **dropped and logged** — never silently discarded (fixtures
``iea-05``, ``gh-03``).

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
_DOI_BODY = r"10\.\d{4,9}/[-._;()/:A-Za-z0-9<>\[\]+*#]+"

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
    "doi ",
)

#: Punctuation a DOI never legitimately ends with in running prose.
_TRAILING_PUNCTUATION = ".,;:!?'\"”’)]}>»"

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
    text = "".join(text.split())  # kill embedded whitespace and newlines

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
