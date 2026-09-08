"""What belongs in the catalogue, and on what grounds (ADR-0043).

The rule, as the project owner stated it:

    Things that belong in the catalogue must either be directly attributed to
    IEA Wind (ie be tagged, or published in our communities or websites in some
    way), or cited by work which is in the catalogue, or cite things which are
    in the catalogue. And ideally we should record which of those is the case.

That last sentence is why this module produces an ``inclusion_basis`` and an
``inclusion_evidence`` on every record rather than a silent boolean. A record
that cannot say why it is here does not get to be here, and a record that *is*
here should be able to show its receipt.

**Three limbs, in order of strength:**

``direct``
    The record was reached through a discovery route that is itself an IEA Wind
    attribution — an IEA Wind Zenodo community, an IEA Wind GitHub organisation,
    an iea-wind.org Task page, or a source query that names IEA Wind — or it
    carries an IEA Wind Task attribution. This is why ``discovered_via`` exists
    (ADR-0041): the route is the evidence, and it used to be thrown away.

``cites``
    It cites something already in the catalogue. A paper using the IEA 15 MW
    reference turbine belongs in a catalogue of IEA Wind output even though
    nobody tagged it.

``cited-by``
    Something already in the catalogue cites it.

``unassessed``
    None of the above, and no discovery route was ever recorded for it — so
    nothing has been established either way. Listed, and marked.

Anything else is **out of scope**: kept, still at its own URL, and not listed
(ADR-0027 — nothing is ever deleted). The decision is re-made from scratch on
every materialise, so a repository that later gets cited by a Task report walks
back into the catalogue on its own, and one that only ever matched a generic
topic search stays out.

**The generic route.** Every discovery route in ``sources.yaml`` is an
attribution except the ones it lists under ``generic_routes``. Today that is
``topic:wind-energy`` — a GitHub topic anyone can apply to anything, and the
route that put a company API directory, a wind-farm video game and a
nuclear-versus-wind comparison into an IEA Wind catalogue. Putting the judgement
in the register rather than in this file means a human can see it and change it
without touching code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from harvest import config
from harvest.doi import normalise_doi

__all__ = [
    "INCLUSION_BASES",
    "generic_routes",
    "alias_index",
    "citation_edges",
    "decide",
]

#: Every value ``inclusion_basis`` can take. ``none`` is stated rather than
#: absent: "we looked and found no grounds" is a finding, not a missing field.
#:
#: ``unassessed`` is the difference between *evidence of non-attribution* and
#: *absence of evidence*. A record whose event log predates discovery routes has
#: never been assessed, and excluding it would be punishing it for our own gap.
#: It is listed, marked, and re-decided the moment a scrape records a route —
#: which for most sources is the next run, and for a source with a rolling query
#: window may be never, so the count is worth watching.
INCLUSION_BASES = ("direct", "cites", "cited-by", "unassessed", "none")

#: Relations that mean "this record cites that identifier".
_CITES = frozenset({"references", "cites", "iscitationof"})

#: …and the inverse, stated from the cited side.
_CITED_BY = frozenset({"iscitedby", "citedby", "isreferencedby"})


def generic_routes(root: Path | None = None) -> set[str]:
    """Discovery routes ``sources.yaml`` declares are NOT an IEA Wind attribution.

    Everything else is one. The default is deliberately "attributes": a route
    exists because somebody put it in the register on purpose, and the two
    exceptions are easier to enumerate — and to argue about — than the rule.
    """
    routes: set[str] = set()
    for source in config.load_sources(root).values():
        for route in (source or {}).get("generic_routes") or []:
            routes.add(str(route))
    return routes


def alias_index(records: Mapping[str, Any]) -> dict[str, str]:
    """``{identifier: identity key}`` for every identifier the catalogue answers to.

    Suppressed records are included on purpose. A record merged into another is
    the *same artifact*, so a work citing the version DOI has cited something in
    the catalogue just as surely as one citing the concept DOI (ADR-0042).
    """
    alias: dict[str, str] = {}
    for key, resolved in records.items():
        alias[key.strip().lower()] = key
        doi = normalise_doi(resolved.effective.get("doi"))
        if doi:
            alias.setdefault(doi.lower(), key)
    return alias


def _stated_citations(effective: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """``(identifiers this record cites, identifiers that cite it)``, as stated."""
    cites: list[str] = []
    cited_by: list[str] = []

    extra = effective.get("extra")
    if isinstance(extra, Mapping):
        # Crossref's reference list: the richest source of "cites" edges there
        # is, and the reason `reference` is in SELECT_FIELDS at all (ADR-0043).
        for doi in extra.get("crossref_references") or []:
            value = normalise_doi(doi)
            if value:
                cites.append(value.lower())

    for entry in effective.get("related_identifiers") or []:
        if not isinstance(entry, Mapping):
            continue
        relation = str(entry.get("relation") or "").strip().lower()
        identifier = str(entry.get("identifier") or "").strip().lower()
        if not identifier:
            continue
        if relation in _CITES:
            cites.append(normalise_doi(identifier) or identifier)
        elif relation in _CITED_BY:
            cited_by.append(normalise_doi(identifier) or identifier)

    return cites, cited_by


def citation_edges(records: Mapping[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """``(cites, cited_by)`` over the whole catalogue, keyed by identity.

    Catalogue-wide by necessity: whether a record cites something *in the
    catalogue* is not a fact about that record alone, which is why this runs at
    materialise time over every resolved identity rather than in an adapter.

    Both directions are filled from every stated edge. If A's reference list
    names B, then A cites B **and** B is cited by A — one deposit's metadata
    settles the question for two records, which matters because Crossref
    publishes reference lists but not citing-work lists.
    """
    alias = alias_index(records)
    cites: dict[str, set[str]] = {key: set() for key in records}
    cited_by: dict[str, set[str]] = {key: set() for key in records}

    for key, resolved in records.items():
        stated_cites, stated_cited_by = _stated_citations(resolved.effective)
        for identifier in stated_cites:
            target = alias.get(identifier)
            if target and target != key:
                cites[key].add(target)
                cited_by[target].add(key)
        for identifier in stated_cited_by:
            source = alias.get(identifier)
            if source and source != key:
                cited_by[key].add(source)
                cites[source].add(key)

    return cites, cited_by


def _attributing_routes(resolved: Any, generic: set[str]) -> list[str]:
    """The routes that reached this record and are NOT declared generic."""
    return [
        str(route)
        for route in getattr(resolved, "discovered_via", ()) or ()
        if str(route) not in generic
    ]


def decide(
    identity_key: str,
    resolved: Any,
    cites: Mapping[str, Iterable[str]],
    cited_by: Mapping[str, Iterable[str]],
    generic: set[str],
) -> tuple[str, str]:
    """``(basis, evidence)`` for one record. Never raises; always answers.

    Evidence is a sentence a reader can check, not a code — it is rendered on
    the record page, so "Published in the IEA Wind Task 43 Zenodo community"
    has to mean something to somebody who has never read this file.
    """
    tasks = [str(task) for task in (resolved.effective.get("iea_task") or []) if task]
    routes = _attributing_routes(resolved, generic)

    if routes:
        return "direct", f"Found through {_english(routes)}, which is an IEA Wind attribution."
    if tasks:
        return "direct", f"Attributed to {_english(sorted(tasks))}."

    linked = sorted(cites.get(identity_key) or ())
    if linked:
        return "cites", f"Cites {_english(linked[:3])}, which the catalogue holds."

    linked = sorted(cited_by.get(identity_key) or ())
    if linked:
        return "cited-by", f"Cited by {_english(linked[:3])}, which the catalogue holds."

    if not getattr(resolved, "discovered_via", None):
        # No route was ever recorded, so nothing has been established either
        # way. Excluding it would punish the record for a gap of ours, and the
        # rule is meant to act on evidence of non-attribution, not on its
        # absence. It is listed and marked until a scrape records a route.
        return (
            "unassessed",
            "Harvested before the catalogue recorded discovery routes, and not "
            "re-scraped since; its grounds have not been established.",
        )

    return (
        "none",
        "No IEA Wind attribution, and no citation link to anything in the catalogue.",
    )


def _english(values: list[str]) -> str:
    """``"a"``, ``"a and b"``, ``"a, b and c"`` — evidence is read by people."""
    values = [str(value) for value in values]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return f"{', '.join(values[:-1])} and {values[-1]}"
