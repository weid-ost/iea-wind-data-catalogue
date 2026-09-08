"""Deriving the specific ``resource_type`` from what the source already said.

**The problem.** ``resource_kind`` has six values and every adapter flattens a
much richer upstream vocabulary into them at map time. DataCite's
"Project deliverable", "Conference Paper" and "Technical Report" all arrive as
``publication`` or ``report``; Zenodo's ``presentation``, ``poster`` and
``event`` all arrive as ``other``. An IEA Wind board member reading the
catalogue asked, reasonably, what "publication", "report" and "other" mean, and
whether IEA Wind's own publication types could be used instead (issues #1, #2,
#3 on the repository). They can: the specific value was never lost, only
unused — each adapter preserves the upstream type verbatim in
``source.extra``.

**So this is a derivation, not a re-harvest** (ADR-0040). It runs at
materialise time over the stored event log, exactly like
:func:`harvest.licenses.map_license` and
:func:`harvest.institutions.infer_owner_org`. Deleting ``data/records/`` and
running ``make materialize`` reclassifies the whole catalogue offline. Note
that re-running the *harvest* would not: change detection is by source key
(ADR-0026), so an unchanged upstream record emits no new event and no adapter
mapping change would ever reach the existing corpus.

**Two levels, and which one wins.** ``resource_kind`` stays the coarse facet —
six values, fixed by the CKAN promotion contract (ADR-0021) and by every
``/catalogue?kind=`` URL already published. ``resource_type`` is the specific
value. When the specific value implies a different parent than the adapter
guessed — a "Project deliverable" the adapter called ``publication`` is grey
literature, not a journal output — **the derived parent wins**, because the
specific signal is better evidence than the flattening that discarded it.

**Order of evidence**, most specific first:

1. **An IEA Wind publication type in the title.** No upstream vocabulary can
   express "Recommended Practice" or "Expert Group Report" — they are IEA
   Wind's own types, and the only place they appear is the title. Matched
   conservatively; see :func:`iea_publication_type`.
2. **The source's own vocabulary**: Zenodo's ``resource_type`` (the richest —
   type *and* subtype), then OSTI, Crossref, DataCite.
3. **Research-software provenance**, for anything already known to be software.
4. **The generic child of whatever kind the adapter recorded**, which is always
   defined, so every record gets a type and "unspecified" is a stated fact
   rather than a gap.

Every value this module can produce must exist in ``vocabulary.yaml`` with a
definition; :func:`check_vocabulary` is the gate that proves it, and it runs in
the test suite. A term that cannot be defined has no business on a chip.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from harvest import config

__all__ = [
    "KIND_OF_TYPE",
    "GENERIC_TYPE_FOR_KIND",
    "iea_publication_type",
    "research_software_type",
    "derive",
    "check_vocabulary",
    "known_types",
    "known_kinds",
]


# ---------------------------------------------------------------------------
# The vocabulary, read from the register
# ---------------------------------------------------------------------------


def _vocabulary(root: Path | None = None) -> dict[str, Any]:
    return config.load_vocabulary(root)


def known_kinds(root: Path | None = None) -> list[str]:
    return [str(entry["name"]) for entry in _vocabulary(root).get("resource_kinds", [])]


def known_types(root: Path | None = None) -> list[str]:
    return [str(entry["name"]) for entry in _vocabulary(root).get("resource_types", [])]


def _kind_of_type(root: Path | None = None) -> dict[str, str]:
    return {
        str(entry["name"]): str(entry["kind"])
        for entry in _vocabulary(root).get("resource_types", [])
    }


#: ``{resource_type: parent resource_kind}``, from ``vocabulary.yaml``. Built
#: eagerly so a typo in this module fails at import rather than at materialise.
KIND_OF_TYPE: dict[str, str] = _kind_of_type()

#: The value used when a kind is known but nothing narrower is. Every kind has
#: one, so ``resource_type`` is never absent.
GENERIC_TYPE_FOR_KIND: dict[str, str] = {
    "dataset": "dataset",
    "publication": "publication",
    "report": "report",
    "software": "other-software",
    "model": "model",
    "other": "other",
}


# ---------------------------------------------------------------------------
# 1. IEA Wind's own publication types, which live only in the title
# ---------------------------------------------------------------------------

#: The phrase, and the ``resource_type`` it names. Order matters: "technical
#: report" must be tried before the bare "report" that is not in this table at
#: all, and "expert group report" before "report" for the same reason.
_IEA_PHRASES: tuple[tuple[str, str], ...] = (
    (r"recommended\s+practices?", "recommended-practice"),
    (r"best\s+practices?", "best-practice"),
    (r"experts?\s+group\s+(?:report|study)", "expert-group-report"),
    (r"topical\s+expert\s+meeting", "tem-proceedings"),
    (r"technical\s+report", "technical-report"),
    (r"annual\s+report", "annual-report"),
)

#: "IEA Wind", "IEA-Wind", "IEA Wind TCP" — the adjacency that makes a phrase in
#: a title a claim about the document's *type* rather than its subject.
_IEA_WIND = re.compile(r"iea[\s‐-―-]*wind", re.IGNORECASE)

#: What may follow the phrase for it to read as a type label: a number ("RP 13"),
#: a separator, "on"/"for the" style continuations that IEA Wind titles use, or
#: the end of the title.
_TYPE_FOLLOWER = r"(?:\s*[:‐-―\-–—,]|\s+\d|\s+on\b|\s*$)"

#: …and what may precede it at the head of a title: nothing, or a separator.
_TYPE_LEADER = r"(?:^|[:‐-―\-–—]\s*)"

#: Upstream types specific enough that a phrase in the title must not override
#: them. A journal article that discusses best practice is still a journal
#: article — "Definition of Best Practice for Testing Icephobic Surfaces" is the
#: real example this guard exists for.
_UNOVERRIDABLE = frozenset({"journal-article", "conference-paper", "thesis", "preprint"})


def iea_publication_type(title: str | None) -> str | None:
    """The IEA Wind publication type a title claims, or ``None``.

    Deliberately conservative — a false positive relabels somebody else's
    journal paper as an IEA Wind Recommended Practice, which is worse than
    leaving it generic. A phrase counts only when it sits in *type position*:

    * the title mentions IEA Wind **and** the phrase is followed by a number, a
      separator, "on", or the end of the title
      ("IEA Wind TCP Recommended practice 13: …", "IEA Wind Task 43 Joint Best
      Practice on Classifying …"); or
    * the phrase opens the title and is followed by a separator or a number
      ("Recommended Practice: Ontology creation …"); or
    * the phrase closes the title after a separator
      ("Ontologies for wind energy domain experts – Recommended Practice").

    It therefore does **not** match "IEA Wind Task 32: best practices **for** the
    certification of …", where the phrase names the subject and not the
    document. Those stay generic, and a curator can annotate one if it matters.
    """
    if not title:
        return None
    text = " ".join(str(title).split())
    has_iea = bool(_IEA_WIND.search(text))
    for phrase, name in _IEA_PHRASES:
        if has_iea and re.search(phrase + _TYPE_FOLLOWER, text, re.IGNORECASE):
            return name
        if re.search(r"^" + phrase + r"(?:\s*[:‐-―\-–—]|\s+\d)", text, re.IGNORECASE):
            return name
        if re.search(r"[:‐-―\-–—]\s*" + phrase + r"\s*$", text, re.IGNORECASE):
            return name
    return None


# ---------------------------------------------------------------------------
# 2. Each source's own vocabulary
# ---------------------------------------------------------------------------

#: Zenodo ``metadata.resource_type.subtype``, which is the most specific thing
#: any source in this catalogue publishes.
_ZENODO_SUBTYPE: dict[str, str] = {
    "article": "journal-article",
    "conferencepaper": "conference-paper",
    "section": "book-chapter",
    "book": "book-chapter",
    "preprint": "preprint",
    "thesis": "thesis",
    "workingpaper": "working-paper",
    "deliverable": "project-deliverable",
    "milestone": "project-deliverable",
    "report": "report",
    "technicalnote": "technical-report",
    "datamanagementplan": "report",
    "proposal": "report",
    "softwaredocumentation": "report",
    "other": "",  # fall through to the type
}

#: Zenodo ``metadata.resource_type.type``.
_ZENODO_TYPE: dict[str, str] = {
    "publication": "publication",
    "dataset": "dataset",
    "software": "research-software",
    "presentation": "presentation",
    "poster": "poster",
    "image": "image",
    "video": "video",
    "lesson": "presentation",
    "event": "meeting-record",
    "model": "model",
    "other": "other",
    "physicalobject": "other",
}

#: OSTI ``product_type``.
_OSTI_PRODUCT: dict[str, str] = {
    "journal article": "journal-article",
    "technical report": "technical-report",
    "conference": "conference-paper",
    "dataset": "dataset",
    "book": "book-chapter",
    "thesis/dissertation": "thesis",
    "software": "research-software",
    "program document": "report",
    "patent": "other",
    "miscellaneous": "other",
}

#: Crossref ``type``.
_CROSSREF_TYPE: dict[str, str] = {
    "journal-article": "journal-article",
    "proceedings-article": "conference-paper",
    "posted-content": "preprint",
    "report": "report",
    "report-component": "report",
    "book-chapter": "book-chapter",
    "monograph": "book-chapter",
    "book": "book-chapter",
    "dissertation": "thesis",
    "dataset": "dataset",
}

#: DataCite ``types.resourceType`` — free text a client chose, so matched on a
#: normalised form. Richer than ``resourceTypeGeneral`` and tried first.
_DATACITE_RESOURCE_TYPE: dict[str, str] = {
    "journal article": "journal-article",
    "article": "journal-article",
    "conference paper": "conference-paper",
    "conferenceobject": "conference-paper",
    "conference proceeding": "conference-paper",
    "conference poster": "poster",
    "poster": "poster",
    "presentation": "presentation",
    "lecture": "presentation",
    "project deliverable": "project-deliverable",
    "deliverable": "project-deliverable",
    "working paper": "working-paper",
    "preprint": "preprint",
    "thesis": "thesis",
    "dissertation": "thesis",
    "technical report": "technical-report",
    "report": "report",
    "book/report": "report",
    "book chapter": "book-chapter",
    "dataset": "dataset",
    "software": "research-software",
    "model": "model",
    "video": "video",
    "image": "image",
    "figure": "image",
}

#: DataCite ``types.resourceTypeGeneral`` — the controlled fallback.
_DATACITE_GENERAL: dict[str, str] = {
    "journalarticle": "journal-article",
    "conferencepaper": "conference-paper",
    "conferenceproceeding": "conference-paper",
    "preprint": "preprint",
    "dissertation": "thesis",
    "book": "book-chapter",
    "bookchapter": "book-chapter",
    "report": "report",
    "dataset": "dataset",
    "software": "research-software",
    "computationalnotebook": "research-software",
    "workflow": "research-software",
    "model": "model",
    "poster": "poster",
    "presentation": "presentation",
    "image": "image",
    "audiovisual": "video",
    "event": "meeting-record",
    "text": "",       # says nothing; fall through
    "other": "",      # ditto
    "collection": "",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _from_zenodo(extra: Mapping[str, Any]) -> str | None:
    entry = extra.get("zenodo_resource_type")
    if not isinstance(entry, Mapping):
        return None
    subtype = _ZENODO_SUBTYPE.get(_norm(entry.get("subtype")).replace(" ", ""))
    if subtype:
        return subtype
    return _ZENODO_TYPE.get(_norm(entry.get("type")).replace(" ", "")) or None


def _from_osti(extra: Mapping[str, Any]) -> str | None:
    return _OSTI_PRODUCT.get(_norm(extra.get("osti_product_type"))) or None


def _from_crossref(extra: Mapping[str, Any]) -> str | None:
    return _CROSSREF_TYPE.get(_norm(extra.get("crossref_type"))) or None


def _from_datacite(extra: Mapping[str, Any]) -> str | None:
    types = extra.get("datacite_types")
    if not isinstance(types, Mapping):
        return None
    specific = _DATACITE_RESOURCE_TYPE.get(_norm(types.get("resourceType")))
    if specific:
        return specific
    return _DATACITE_GENERAL.get(_norm(types.get("resourceTypeGeneral")).replace(" ", "")) or None


#: Most specific vocabulary first, not highest precedence first. Precedence
#: (ADR-0026) settles which source's *value* for a field wins; this settles
#: which source's *vocabulary* is most informative, and Zenodo's is, because it
#: is the only one with a subtype.
_VOCABULARIES = (_from_zenodo, _from_osti, _from_crossref, _from_datacite)


# ---------------------------------------------------------------------------
# 3. Research software, per FAIR4RS
# ---------------------------------------------------------------------------


def research_software_type(effective: Mapping[str, Any]) -> str:
    """``research-software`` or ``other-software``, from research provenance.

    FAIR4RS draws the line at intent — software "created during the research
    process or for a research purpose", as against software merely *used* in
    research. Intent is not in any payload, so the catalogue uses the three
    signals that do travel with a record and that a reader can check:

    * **it has a DOI** — somebody deposited it as a citable research output;
    * **it is attributed to an IEA Wind Task** — it came out of the programme;
    * **it came from a research repository** rather than from a code host —
      Zenodo, OSTI and DataCite hold research deposits by construction.

    Everything else is ``other-software``. That is what separates ``windIO``,
    ``OpenOA`` and the FAD-Toolset from the wind-adjacent company repositories
    a GitHub topic sweep also finds. It is a rule about evidence, not a
    judgement about quality, and the About page says so.
    """
    if effective.get("doi"):
        return "research-software"
    if effective.get("iea_task"):
        return "research-software"
    systems = set(effective.get("_source_systems") or ())
    if systems - {"github", "ieawind", "wdh"}:
        return "research-software"
    return "other-software"


# ---------------------------------------------------------------------------
# The derivation
# ---------------------------------------------------------------------------


def derive(
    effective: Mapping[str, Any],
    source_systems: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """``(resource_kind, resource_type)`` for one resolved record.

    Pure and offline: everything it reads is already in the event log. Returns
    the adapter's kind unchanged and a ``None`` type only when the record has no
    kind at all, which is the one case where inventing a type would be a guess.
    """
    extra = effective.get("extra")
    extra = extra if isinstance(extra, Mapping) else {}
    stored_kind = effective.get("resource_kind")

    # The source vocabularies first: their answer is what an IEA phrase has to
    # beat, and what tells us whether beating it would be wrong.
    derived: str | None = None
    for reader in _VOCABULARIES:
        derived = reader(extra)
        if derived:
            break

    iea = iea_publication_type(effective.get("title"))
    if iea and derived not in _UNOVERRIDABLE:
        derived = iea

    if derived is None and stored_kind:
        derived = GENERIC_TYPE_FOR_KIND.get(str(stored_kind))

    if derived is None:
        return (stored_kind, None)

    kind = KIND_OF_TYPE.get(derived, stored_kind)

    if kind == "software":
        view = dict(effective)
        view["_source_systems"] = source_systems or []
        derived = research_software_type(view)
        kind = "software"

    return (kind, derived)


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def check_vocabulary(root: Path | None = None) -> list[str]:
    """Every problem with the vocabulary register, as readable lines.

    Empty means the register and this module agree. The test suite asserts
    that, so a value can never reach a chip without a definition behind it.
    """
    problems: list[str] = []
    vocab = _vocabulary(root)
    kinds = {str(entry["name"]) for entry in vocab.get("resource_kinds", [])}
    types = {str(entry["name"]): entry for entry in vocab.get("resource_types", [])}
    authorities = set(vocab.get("authorities", {}))

    from harvest.models import ACCESS_STATUSES, RESOURCE_KINDS

    for name in RESOURCE_KINDS:
        if name not in kinds:
            problems.append(f"resource_kind {name!r} is in harvest.models but not vocabulary.yaml")
    for name in kinds - set(RESOURCE_KINDS):
        problems.append(f"resource_kind {name!r} is in vocabulary.yaml but not harvest.models")

    statuses = {str(entry["name"]) for entry in vocab.get("access_statuses", [])}
    for name in ACCESS_STATUSES:
        if name not in statuses:
            problems.append(f"access_status {name!r} is in harvest.models but not vocabulary.yaml")

    for name, entry in types.items():
        parent = str(entry.get("kind", ""))
        if parent not in kinds:
            problems.append(f"resource_type {name!r} names unknown kind {parent!r}")
        if not str(entry.get("definition", "")).strip():
            problems.append(f"resource_type {name!r} has no definition")
        authority = entry.get("authority")
        if authority and authority not in authorities:
            problems.append(f"resource_type {name!r} names unknown authority {authority!r}")

    for entry in vocab.get("resource_kinds", []) + vocab.get("access_statuses", []) + vocab.get("terms", []):
        if not str(entry.get("definition", "")).strip():
            problems.append(f"{entry.get('name')!r} has no definition")

    # Every value the mapping tables can produce must be a defined type.
    produced = set(GENERIC_TYPE_FOR_KIND.values())
    for table in (
        _ZENODO_SUBTYPE, _ZENODO_TYPE, _OSTI_PRODUCT, _CROSSREF_TYPE,
        _DATACITE_RESOURCE_TYPE, _DATACITE_GENERAL,
    ):
        produced |= {value for value in table.values() if value}
    produced |= {name for _, name in _IEA_PHRASES}
    produced |= {"research-software", "other-software"}
    for name in sorted(produced - set(types)):
        problems.append(f"the derivation can produce {name!r}, which vocabulary.yaml does not define")

    for kind in kinds:
        if kind not in GENERIC_TYPE_FOR_KIND:
            problems.append(f"kind {kind!r} has no generic resource_type to fall back to")

    return problems
