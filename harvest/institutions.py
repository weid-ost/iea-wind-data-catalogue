"""``owner_org`` — which institution a record is attributed to.

CKAN's ``package_create`` refuses a dataset with no owning organisation on a
default install, so ADR-0021's "POSTable to CKAN with no transformation" claim
is only true if every record carries one. ``organizations.yaml`` exists for
exactly this and was, until now, dead data: no adapter set ``owner_org``, so
all thirty records were org-less and ADR-0023's *institution* facet had nothing
to count (product-e2e-02, site-04, compliance-02).

**Nobody registers anything** — that is the premise of the catalogue — so the
institution is *inferred*, from the strongest signal the metadata carries:

1. an ``owner_org`` a source or a curator stated outright;
2. the **research organisation** the source itself records (OSTI's
   ``research_orgs``) — an institutional statement, not a personal one;
3. the GitHub owner login;
4. the affiliation of the **first author**, then of any other author — the
   deposit's own statement of who made the thing;
5. the ``publisher``, then the container;
6. failing all of that, a per-source fallback that is itself a register entry:
   ``zenodo-community`` for a Zenodo-hosted deposit that states no affiliation,
   ``unattributed`` otherwise.

**The limits of the heuristic, stated plainly.** A first author's affiliation
is not the same fact as "this institution owns this dataset": a co-authored
NREL/DTU dataset is attributed to one of them, and a paper published by
Copernicus on behalf of a university matches nothing and lands in
``unattributed``. So:

* the match is **exact against the register** — ``organizations.yaml`` names,
  titles and curator-written ``aliases``, on a word boundary. Nothing is
  guessed, nothing is fuzzy, and a string the register does not know never
  invents an organisation;
* the value carries ``curator``-free provenance of its own
  (``extraction_method: pattern``), so the record page can say the attribution
  was derived rather than stated;
* ``unattributed`` is a real register entry and a visible curation task, not a
  null hiding in a facet.

To improve attribution you add an alias to ``organizations.yaml``. No code
changes, and the next materialise picks it up.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from harvest import config

__all__ = [
    "DEFAULT_OWNER_ORG",
    "SOURCE_FALLBACK_ORG",
    "match_organization",
    "institution_signals",
    "infer_owner_org",
]

log = logging.getLogger(__name__)

#: Owner of last resort. Must exist in ``organizations.yaml``.
DEFAULT_OWNER_ORG = "unattributed"

#: Where a source's own nature says more than "we do not know".
SOURCE_FALLBACK_ORG: dict[str, str] = {
    "zenodo": "zenodo-community",
}

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def _norm(value: Any) -> str:
    """Lowercase, punctuation-free, single-spaced — for matching only."""
    text = _PUNCT_RE.sub(" ", str(value or "").lower())
    return _SPACE_RE.sub(" ", text).strip()


def _index(root_key: str | None) -> tuple[tuple[str, str], ...]:
    """``((normalised alias, org name), ...)``, longest alias first.

    Longest-first so that "National Renewable Energy Laboratory" wins over a
    shorter alias that happens to be a substring of it.
    """
    root = Path(root_key) if root_key else None
    pairs: list[tuple[str, str]] = []
    for org in config.load_organizations(root):
        name = str(org.get("name") or "")
        if not name:
            continue
        candidates = [name, org.get("title"), *(org.get("aliases") or [])]
        for candidate in candidates:
            normalised = _norm(candidate)
            if normalised:
                pairs.append((normalised, name))
    pairs.sort(key=lambda pair: (-len(pair[0]), pair[0]))
    return tuple(pairs)


def match_organization(text: Any, root: Path | None = None) -> str | None:
    """The register entry ``text`` names, or ``None``.

    An alias matches on a word boundary, so ``"OST"`` matches
    ``"OST - Ostschweizer Fachhochschule"`` but not ``"composting"``.
    """
    haystack = _norm(text)
    if not haystack:
        return None
    for alias, name in _index(str(root) if root else None):
        if alias == haystack:
            return name
        if re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", haystack):
            return name
    return None


def _authors(effective: Mapping[str, Any]) -> list[dict]:
    return [author for author in (effective.get("authors") or []) if isinstance(author, dict)]


def institution_signals(
    effective: Mapping[str, Any],
    source_system: str | None = None,
) -> Iterable[tuple[str, Any]]:
    """``(basis, text)`` pairs to try, strongest first. Order is the policy."""
    authors = _authors(effective)
    extra = effective.get("extra") if isinstance(effective.get("extra"), dict) else {}

    for key in ("osti_research_orgs", "research_orgs", "research_org"):
        value = extra.get(key)
        if isinstance(value, (list, tuple)):
            for item in value:
                yield f"extra.{key}", item
        elif value:
            yield f"extra.{key}", value
    yield "github-owner", extra.get("github_owner")
    if authors:
        yield "first-author-affiliation", authors[0].get("affiliation")
    for author in authors[1:]:
        yield "author-affiliation", author.get("affiliation")
    yield "publisher", effective.get("publisher")
    yield "container", effective.get("container")


def infer_owner_org(
    effective: Mapping[str, Any],
    source_system: str | None = None,
    root: Path | None = None,
) -> tuple[str, str]:
    """``(owner_org, basis)`` for one resolved record. Always returns a slug.

    The returned slug is always a name in ``organizations.yaml`` — the CKAN
    gate re-checks that, and this function is the only thing that populates it.
    """
    stated = effective.get("owner_org")
    if stated:
        known = config.organization_names(root)
        if not known or str(stated) in known:
            return str(stated), "stated"
        log.warning(
            "owner_org %r is not in organizations.yaml; falling back to inference", stated
        )

    for basis, text in institution_signals(effective, source_system):
        if not text:
            continue
        matched = match_organization(text, root)
        if matched:
            return matched, basis

    fallback = SOURCE_FALLBACK_ORG.get(str(source_system or ""), DEFAULT_OWNER_ORG)
    known = config.organization_names(root)
    if known and fallback not in known:
        fallback = DEFAULT_OWNER_ORG
    return fallback, "fallback"
