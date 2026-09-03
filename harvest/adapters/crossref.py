"""Crossref adapter — **owner: Track C (crossref)**. Tier 1, deterministic.

Source
    ``https://api.crossref.org/works``, the REST listing route, with ``mailto``
    in the query so we land in Crossref's `polite pool
    <https://api.crossref.org/swagger-ui/index.html>`_. The project User-Agent
    already carries the same address.

    The queries themselves live in ``sources.yaml`` under ``crossref.queries``
    and were decided and live-verified on 2026-08-31 (see
    :meth:`CrossrefAdapter._query_urls`):

    1. ``query.title=IEA Wind Task`` — the works that *name* an IEA Wind task.
       Relevance-ranked, and the top of that ranking is unambiguously the task
       literature: reference-turbine reports, task benchmarks, task overviews.
    2. ``filter=issn:2366-7451`` + ``query.bibliographic=IEA Wind`` — *Wind
       Energy Science*, the anchor journal, narrowed to the IEA-Wind-relevant
       works (the IEA 15 MW / 22 MW reference turbines, task benchmarks).

    ``sort=deposited`` is deliberately **not** used: it ranks the whole corpus
    by deposit time and returns unrelated chemistry, which is worse than
    useless at a five-record cap. Relevance ranking, verified live, returns the
    right works.

    Every request carries ``select=...`` (see :data:`SELECT_FIELDS`). This is
    an ordinary API parameter: it drops the ``reference`` array — tens of
    kilobytes of citations we neither use nor want in a fixture — and keeps
    every field this adapter maps. What comes back is still verbatim what the
    API said for that request.

Source key (plan §4.1, ADR-0026)
    ``deposited.date-time`` — **not** ``indexed``. ``indexed`` is Crossref's
    own re-indexing timestamp; it moves without any change to the metadata and
    would make every weekly run an append-always run. ``deposited`` moves when
    the publisher deposits metadata, which is exactly the event worth an event.
    See :func:`source_key_for`, and ``test_crossref.py::TestSourceKey``.

Identity
    The DOI, normalised by :func:`harvest.identity.identity_key`. For a
    preprint that Crossref says ``is-preprint-of`` a published article, the
    identity is the **published** article's DOI — see below.

Fixtures owned
    ``cr-01`` .. ``cr-07`` in ``fixtures/crossref/``, plus the ``b`` variants
    that pin down a second real shape of the same behaviour.

The seven behaviours, and how they are implemented
    * **cr-01** — a journal article is ``resource_kind: publication``.
      :data:`RESOURCE_KINDS_BY_TYPE` maps Crossref's ``type`` vocabulary;
      anything unknown falls back to ``publication``, because Crossref is a
      publication registry.
    * **cr-02** — ``date-parts`` may be ``[[2023]]`` or ``[[2016, 9]]``.
      :func:`_date_from_parts` emits ``2023`` and ``2016-09``; it **never**
      fabricates a month or a day.
    * **cr-03** — the container is ``container-title`` (the series, for a
      conference series such as *Journal of Physics: Conference Series*),
      falling back to ``event.name`` for a true ``proceedings-article``.
      ``volume``/``issue``/``page``/``article-number`` are retained in
      ``source.extra`` — the record format has no first-class field for them.
    * **cr-04** — a ``posted-content`` preprint carrying
      ``relation.is-preprint-of`` takes the **published** DOI as its identity,
      so it merges into the published record instead of being listed
      separately; its own DOI is kept in ``source.extra.crossref_preprint_doi``
      and linked from ``source_urls`` and ``related_identifiers``. When the
      published version is in the same batch the preprint is dropped outright.
      The asserted published DOI is a third-party identifier, so it is
      **resolved or the observation is dropped and logged** — that check lives
      in :meth:`harvest`, never in the pure :meth:`map`.
    * **cr-05** — titles carry JATS markup (``<i>``, ``<b>``), entities
      (``&amp;``) and deposit whitespace. ``source.title`` is plain text with
      entities decoded once and whitespace collapsed, so it is correct in
      JSON-LD, in ``<title>`` and in a search index; the sanitised marked-up
      form is kept in ``source.extra.crossref_title_html`` for a renderer that
      wants the italics, and the byte-verbatim deposit in
      ``source.extra.crossref_title_raw``. Nothing is stripped blindly and
      nothing is lost.
    * **cr-06** — an author may be an organisation (``{"name": ...}``, no
      ``given``/``family``) or have no ``given``.
      :class:`harvest.models.Author` needs only ``name``; :func:`_authors`
      builds one from whatever is present and tolerates ``author`` being
      absent entirely (fixture ``cr-02`` has no authors at all).
    * **cr-07** — ``update-to``/``updated-by`` of type ``retraction`` set
      ``source.withdrawn = True`` (ADR-0027 keeps the record and the URL; the
      site renders the banner from ``extras.lifecycle_state``), record the
      relation in ``related_identifiers``, and keep the notice verbatim in
      ``source.extra.crossref_update_to``. A retraction notice *about another
      DOI* is a different work: it keeps its own identity and only gains an
      ``IsRetractionOf`` relation, because the flag belongs on the retracted
      article, which is the reconciler's business.

What this adapter deliberately does not do
    * **No ``resources``.** Crossref's ``link`` entries are text-mining and
      similarity-checking endpoints, most of them behind publisher
      authentication. Publishing them as CKAN resources would imply a download
      that often is not there. They are kept verbatim in
      ``source.extra.crossref_links``; the landing page is in ``source_urls``.
    * **No ``access_status``.** Crossref does not state one, and an open
      licence is not a promise of an open copy. Absent beats guessed.
    * **No ``iea_task``.** Crossref never states task membership. A title
      saying "IEA Wind Task 43" is the *work* saying it, not the registry, and
      a pure ``map()`` cannot check the number against ``groups.yaml``.
      Candidates are recorded in ``source.extra.iea_task_candidates``, and
      :func:`harvest.dedupe.promote_task_candidates` writes the ones that are
      really in ``groups.yaml`` to ``local.iea_task`` with
      ``extraction_method: pattern`` — machine inference, badged as such, never
      passed off as something the registry stated.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Iterator
from urllib.parse import urlencode

from harvest import DEFAULT_MAX_RECORDS
from harvest.adapters.base import Adapter, SourceUnreachable, payload_hash, register
from harvest.doi import DoiDropLog, normalise_doi, resolve_or_drop
from harvest.http import HarvestClient
from harvest.identity import identity_key
from harvest.licenses import map_license
from harvest.models import (
    Author,
    FieldProvenance,
    MappedObservation,
    RawObservation,
    SourceNamespace,
)
from harvest.sanitize import html_to_text, sanitize_html

__all__ = [
    "API",
    "SELECT_FIELDS",
    "RESOURCE_KINDS_BY_TYPE",
    "RETRACTION_TYPES",
    "CrossrefAdapter",
    "source_key_for",
    "published_version_doi",
]

log = logging.getLogger(__name__)

API = "https://api.crossref.org/works"

#: Fields requested with ``select``. Everything this adapter maps, and nothing
#: else — in particular not ``reference``, which is the bulk of a Crossref item
#: and is of no use to a catalogue of metadata and links. Verified against the
#: route's own list of valid selects on 2026-08-31.
SELECT_FIELDS: tuple[str, ...] = (
    "DOI", "type", "title", "subtitle", "short-title", "original-title",
    "container-title", "short-container-title", "group-title", "event",
    "volume", "issue", "page", "article-number", "publisher",
    "publisher-location", "author", "editor", "contributor", "issued",
    "published", "published-print", "published-online", "posted", "created",
    "deposited", "indexed", "license", "link", "URL", "resource", "abstract",
    "subject", "ISSN", "issn-type", "ISBN", "relation", "update-to",
    "updated-by", "update-policy", "funder", "alternative-id", "prefix",
    "member", "references-count", "is-referenced-by-count", "archive",
    "content-domain", "assertion", "standards-body",
)

#: Crossref's ``type`` vocabulary mapped to the catalogue's ``resource_kind``.
#: Unlisted types fall back to ``publication``: Crossref registers published
#: literature, so that is the honest default rather than ``other``.
RESOURCE_KINDS_BY_TYPE: dict[str, str] = {
    "journal-article": "publication",
    "journal-issue": "publication",
    "journal-volume": "publication",
    "journal": "publication",
    "proceedings-article": "publication",
    "proceedings": "publication",
    "proceedings-series": "publication",
    "posted-content": "publication",
    "book": "publication",
    "book-chapter": "publication",
    "book-part": "publication",
    "book-section": "publication",
    "book-series": "publication",
    "book-set": "publication",
    "book-track": "publication",
    "edited-book": "publication",
    "monograph": "publication",
    "reference-book": "publication",
    "reference-entry": "publication",
    "dissertation": "publication",
    "report": "report",
    "report-component": "report",
    "report-series": "report",
    "standard": "report",
    "standard-series": "report",
    "dataset": "dataset",
    "database": "dataset",
    "component": "other",
    "peer-review": "other",
    "grant": "other",
    "other": "other",
}

#: ``update-to``/``updated-by`` types that mean "this work is retracted".
RETRACTION_TYPES: frozenset[str] = frozenset({"retraction", "withdrawal", "removal"})

#: Crossref relation names → DataCite-style relation names, for
#: ``source.related_identifiers``. Unlisted names are camel-cased generically.
_RELATION_NAMES = {
    "is-preprint-of": "IsPreprintOf",
    "has-preprint": "HasPreprint",
    "is-supplement-to": "IsSupplementTo",
    "has-review": "HasReview",
    "has-comment": "HasComment",
    "is-part-of": "IsPartOf",
}

#: Payload keys excluded from the fallback source key: Crossref's own churn.
#: ``indexed`` above all (ADR-0026), plus the citation counters, which move
#: whenever somebody else publishes.
_VOLATILE_KEYS = frozenset(
    {"indexed", "deposited", "score", "is-referenced-by-count", "references-count",
     "reference-count"}
)

# ``<jats:p>`` → ``<p>``. Crossref abstracts and titles are deposited as JATS;
# unwrapping the namespace lets the ordinary allow-list sanitiser see tags it
# knows, instead of dropping every paragraph boundary in the abstract.
_JATS_RE = re.compile(r"(</?)jats:([a-zA-Z][-a-zA-Z0-9]*)")
_WHITESPACE_RE = re.compile(r"\s+")

#: "IEA Wind Task 43", "IEA Wind TCP Task 55", "IEA Task 32", "IEA-Wind Task 11".
_TASK_RE = re.compile(
    r"IEA[\s‐-―\-]*(?:Wind[\s‐-―\-]*)?(?:TCP[\s]*)?Task[\s]*(\d{1,3})",
    re.IGNORECASE,
)
_BARE_TASK_RE = re.compile(r"\bTask[\s]*(\d{1,3})\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pure helpers. Everything below is offline, deterministic and total.
# ---------------------------------------------------------------------------


def _unwrap_jats(text: str) -> str:
    return _JATS_RE.sub(r"\1\2", str(text))


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(text)).strip()


def _plain(text: Any) -> str:
    """Crossref markup → plain text: entities decoded once, whitespace collapsed.

    This is the form that is correct everywhere — HTML (the renderer escapes
    it), JSON-LD (the encoder escapes it), ``<title>``, and the search index.
    """
    if not text:
        return ""
    return _collapse(html_to_text(_unwrap_jats(text)))


def _rich(text: Any) -> str:
    """Crossref markup → the sanitised safe HTML subset, whitespace collapsed."""
    if not text:
        return ""
    return _collapse(sanitize_html(_unwrap_jats(text)))


def _first(value: Any) -> str:
    """Crossref's one-element string arrays (``title``, ``container-title``)."""
    if isinstance(value, list):
        for item in value:
            if item:
                return str(item)
        return ""
    return str(value or "")


def _date_from_parts(node: Any) -> str | None:
    """``{"date-parts": [[2016, 9]]}`` -> ``"2016-09"``. Precision is preserved.

    A year-only deposit stays year-only (fixture ``cr-02``). Fabricating
    ``-01-01`` would invent a fact the source never stated, and the site would
    then show a day that is not true.
    """
    if not isinstance(node, dict):
        return None
    parts = node.get("date-parts") or []
    if not parts or not isinstance(parts[0], list):
        return None
    numbers = [p for p in parts[0] if isinstance(p, int)]
    if not numbers:
        return None
    year = f"{numbers[0]:04d}"
    if len(numbers) == 1:
        return year
    if len(numbers) == 2:
        return f"{year}-{numbers[1]:02d}"
    return f"{year}-{numbers[1]:02d}-{numbers[2]:02d}"


def _published_date(item: dict[str, Any]) -> str | None:
    """The publication date, at whatever precision Crossref stated it."""
    for key in ("issued", "published", "published-print", "published-online",
                "posted", "created"):
        value = _date_from_parts(item.get(key))
        if value:
            return value
    return None


def _orcid(raw: Any) -> str | None:
    """``https://orcid.org/0000-...`` -> ``0000-...``."""
    if not raw:
        return None
    text = str(raw).strip()
    for prefix in ("https://orcid.org/", "http://orcid.org/", "https://www.orcid.org/",
                   "http://www.orcid.org/", "orcid.org/"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    return text or None


def _affiliation(entry: dict[str, Any]) -> str | None:
    names = [
        _collapse(aff.get("name", ""))
        for aff in entry.get("affiliation") or []
        if isinstance(aff, dict) and aff.get("name")
    ]
    return "; ".join(name for name in names if name) or None


def _authors(item: dict[str, Any]) -> list[Author]:
    """Crossref contributors → :class:`~harvest.models.Author` (fixture ``cr-06``).

    Three shapes, all real and all tolerated:

    * ``{"given": "Erik", "family": "Fritz"}``      -> ``Fritz, Erik``
    * ``{"family": "Boorsma"}`` (no given name)     -> ``Boorsma``
    * ``{"name": "National Renewable Energy ..."}`` -> the organisation verbatim

    ``author`` may also be absent altogether (fixture ``cr-02``), which is a
    missing list, not an error.
    """
    authors: list[Author] = []
    for entry in item.get("author") or []:
        if not isinstance(entry, dict):
            continue
        given = _collapse(entry.get("given", "")) or None
        family = _collapse(entry.get("family", "")) or None
        organisation = _collapse(entry.get("name", "")) or None
        if family and given:
            name = f"{family}, {given}"
        elif family:
            name = family
        elif organisation:
            name = organisation
        elif given:
            name = given
        else:
            continue  # nothing nameable; skip rather than emit an empty author
        authors.append(
            Author(
                name=name,
                given=given,
                family=family,
                orcid=_orcid(entry.get("ORCID")),
                affiliation=_affiliation(entry),
            )
        )
    return authors


def _license(item: dict[str, Any]) -> tuple[str | None, str, bool]:
    """``(license_raw, license_id, mapped)`` from Crossref's ``license`` array.

    Crossref deposits one entry per content version. The version of record
    (``vor``) is the one a reader gets, so it wins; ``am`` (accepted
    manuscript) is next; a ``tdm`` text-mining licence is the weakest signal
    and is used only when it is all there is.

    An absent licence is ``notspecified`` with ``mapped=True`` — nothing went
    wrong, Crossref simply carries no licence for this work. **Never infer an
    open licence** from an open-access-looking publisher.
    """
    entries = [e for e in item.get("license") or [] if isinstance(e, dict) and e.get("URL")]
    if not entries:
        return None, map_license(None)[0], True
    ranking = {"vor": 0, "am": 1, "unspecified": 2, "tdm": 3}
    best = min(
        enumerate(entries),
        key=lambda pair: (ranking.get(str(pair[1].get("content-version", "")).lower(), 2), pair[0]),
    )[1]
    raw = str(best["URL"]).strip()
    license_id, mapped = map_license(raw)
    return raw, license_id, mapped


def _relation_name(raw: str) -> str:
    key = str(raw).strip().lower()
    if key in _RELATION_NAMES:
        return _RELATION_NAMES[key]
    return "".join(part.capitalize() for part in re.split(r"[-_]", key) if part)


def _related_identifiers(item: dict[str, Any], own_doi: str | None) -> list[dict[str, str]]:
    """``relation`` + ``update-to``/``updated-by`` → catalogue relation dicts.

    Crossref asserts the same relation twice — once by the subject and once by
    the object — so the list is deduplicated, order preserved.
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(relation: str, identifier: str | None, identifier_type: str = "DOI") -> None:
        if not identifier:
            return
        key = (relation, identifier)
        if key in seen or identifier == own_doi:
            return
        seen.add(key)
        out.append(
            {"relation": relation, "identifier": identifier, "identifier_type": identifier_type}
        )

    relation = item.get("relation")
    if isinstance(relation, dict):
        for name, entries in sorted(relation.items()):
            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                id_type = str(entry.get("id-type", "")).strip()
                identifier = str(entry.get("id", "")).strip()
                if id_type.lower() == "doi":
                    add(_relation_name(name), normalise_doi(identifier))
                elif identifier:
                    add(_relation_name(name), identifier, id_type.upper() or "OTHER")

    for entry in item.get("update-to") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type", "")).strip().lower()
        relation_name = "IsRetractionOf" if kind in RETRACTION_TYPES else f"Is{_relation_name(kind)}Of"
        add(relation_name, normalise_doi(entry.get("DOI")))

    for entry in item.get("updated-by") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type", "")).strip().lower()
        relation_name = "IsRetractedBy" if kind in RETRACTION_TYPES else f"Is{_relation_name(kind)}By"
        add(relation_name, normalise_doi(entry.get("DOI")))

    return out


def _retraction(item: dict[str, Any], own_doi: str | None) -> dict[str, Any] | None:
    """The retraction that applies to **this** work, or ``None`` (fixture ``cr-07``).

    Two shapes, and only one of them retracts the record in hand:

    * ``update-to`` / ``updated-by`` naming **this** DOI — Crossref (via
      Retraction Watch or the publisher) says this work is retracted.
    * ``update-to`` naming **another** DOI — this work *is* the retraction
      notice. The notice is not itself retracted; the flag belongs on the
      article it names, which is the reconciler's job. Only the
      ``IsRetractionOf`` relation is recorded here.
    """
    for key in ("update-to", "updated-by"):
        for entry in item.get(key) or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("type", "")).strip().lower() not in RETRACTION_TYPES:
                continue
            target = normalise_doi(entry.get("DOI"))
            if own_doi and target and target != own_doi:
                continue
            return {
                "type": str(entry.get("type", "")).strip().lower(),
                "label": str(entry.get("label", "")).strip() or "Retraction",
                "source": str(entry.get("source", "")).strip() or None,
                "retracted_date": _date_from_parts(entry.get("updated")),
                "doi": target,
            }
    return None


def published_version_doi(item: dict[str, Any]) -> str | None:
    """The published article a ``posted-content`` preprint says it became.

    ``relation.is-preprint-of`` only — the reverse relation (``has-preprint``)
    is on the published article and means the opposite. Returns ``None`` for
    anything that is not a preprint with a syntactically valid target DOI
    (fixture ``cr-04``).
    """
    if str(item.get("type", "")) != "posted-content":
        return None
    relation = item.get("relation")
    if not isinstance(relation, dict):
        return None
    for entry in relation.get("is-preprint-of") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id-type", "")).lower() != "doi":
            continue
        target = normalise_doi(entry.get("id"))
        if target:
            return target
    return None


def source_key_for(item: dict[str, Any]) -> str:
    """The change token: ``deposited.date-time`` (ADR-0026, plan §4.1).

    **Never ``indexed``.** ``indexed`` is Crossref's re-indexing timestamp; it
    moves on its own schedule, with no change to a single metadata field, and
    keying on it would turn append-on-change into append-always.

    Falls back to the ``deposited`` timestamp, then to the date parts, and
    finally — for a payload with no ``deposited`` at all — to
    :func:`~harvest.adapters.base.payload_hash` over the content fields, with
    ``indexed`` and the citation counters excluded for exactly the same reason.
    """
    deposited = item.get("deposited")
    if isinstance(deposited, dict):
        for key in ("date-time", "timestamp"):
            value = deposited.get(key)
            if value not in (None, ""):
                return str(value)
        stamped = _date_from_parts(deposited)
        if stamped:
            return stamped
    return payload_hash({k: v for k, v in item.items() if k not in _VOLATILE_KEYS})


def _extra(item: dict[str, Any], own_doi: str | None, preprint_of: str | None) -> dict[str, Any]:
    """Everything Crossref said that the record format has no field for.

    Kept verbatim so the event log stays a complete account of the deposit.
    ``source.extra`` is never rendered unless a curator opts in.
    """
    extra: dict[str, Any] = {"crossref_type": str(item.get("type", ""))}

    title_raw = _first(item.get("title"))
    plain, rich = _plain(title_raw), _rich(title_raw)
    if title_raw and title_raw != plain:
        # ADR-0038: whenever the plain title differs from the deposit by so
        # much as a doubled space, the deposit is kept byte-for-byte.
        extra["crossref_title_raw"] = title_raw
    if rich and rich != plain and rich != extra.get("crossref_title_raw"):
        # Only when the marked-up form says something the deposit does not
        # already say verbatim — an entity-only title needs no third copy.
        extra["crossref_title_html"] = rich

    for key, source_key in (
        ("crossref_subtitle", "subtitle"),
        ("crossref_short_container_title", "short-container-title"),
        ("crossref_group_title", "group-title"),
    ):
        value = _plain(_first(item.get(source_key)))
        if value:
            extra[key] = value

    for key, source_key in (
        ("crossref_volume", "volume"),
        ("crossref_issue", "issue"),
        ("crossref_page", "page"),
        ("crossref_article_number", "article-number"),
        ("crossref_update_policy", "update-policy"),
    ):
        value = item.get(source_key)
        if value not in (None, "", [], {}):
            extra[key] = str(value)

    for key, source_key in (
        ("crossref_issn", "ISSN"),
        ("crossref_isbn", "ISBN"),
        ("crossref_event", "event"),
        ("crossref_links", "link"),
        ("crossref_update_to", "update-to"),
        ("crossref_updated_by", "updated-by"),
    ):
        value = item.get(source_key)
        if value not in (None, "", [], {}):
            extra[key] = value

    if preprint_of and own_doi:
        extra["crossref_preprint_doi"] = own_doi

    candidates = _task_candidates(item)
    if candidates:
        extra["iea_task_candidates"] = candidates
    return extra


def _task_candidates(item: dict[str, Any]) -> list[str]:
    """``task-43``-shaped strings named in the metadata. **Not** ``iea_task``.

    Crossref never states task membership, and a pure ``map()`` cannot check a
    task number against ``groups.yaml``. Emitting an unregistered group name
    would fail the CKAN gate for the whole run, so these stay candidates for
    the reconciler and the curator to promote.
    """
    haystack = " ".join(
        str(part)
        for part in (
            _first(item.get("title")),
            _first(item.get("container-title")),
            item.get("abstract") or "",
        )
    )
    found = {f"task-{int(match.group(1))}" for match in _TASK_RE.finditer(haystack)}
    if found:
        # "IEA Wind Task 32 and Task 37" — once an anchored mention establishes
        # that this text is talking about IEA Wind tasks, a bare "Task 37" in
        # the same text is one too. Still only a candidate.
        found |= {f"task-{int(match.group(1))}" for match in _BARE_TASK_RE.finditer(haystack)}
    return sorted(found)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


@register
class CrossrefAdapter(Adapter):
    """Crossref REST, tier 1. See the module docstring for the whole contract."""

    source_name = "crossref"
    tier = 1
    source_key_semantics = "deposited.date-time (never indexed, which churns)"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: HarvestClient | None = None
        self._owns_client = False
        #: Every DOI this adapter refused to accept, for the run report.
        self.drop_log = DoiDropLog()

    # -- configuration -----------------------------------------------------
    def _api(self) -> str:
        return str(self.config.get("api") or API)

    def _queries(self) -> list[dict[str, str]]:
        """The configured query list, as ``[{param: value}, ...]``."""
        configured = self.config.get("queries") or []
        queries: list[dict[str, str]] = []
        for entry in configured:
            if isinstance(entry, dict) and isinstance(entry.get("params"), dict):
                queries.append({str(k): str(v) for k, v in entry["params"].items()})
            elif isinstance(entry, str) and entry.strip():
                queries.append({"query.bibliographic": entry.strip()})
        return queries or [{"query.title": "IEA Wind Task"}]

    def _query_urls(self, rows: int) -> list[str]:
        """One URL per configured query, with ``select``, ``rows`` and ``mailto``."""
        api, urls = self._api(), []
        mailto = str(self.config.get("mailto") or "").strip()
        for params in self._queries():
            query = dict(params)
            query["rows"] = str(max(1, rows))
            query["select"] = ",".join(SELECT_FIELDS)
            if mailto:
                query["mailto"] = mailto
            urls.append(f"{api}?{urlencode(query)}")
        return urls

    # -- harvest -----------------------------------------------------------
    def harvest(self, max_records: int = DEFAULT_MAX_RECORDS) -> Iterable[RawObservation]:
        """Query Crossref and yield at most ``max_records`` verbatim work items.

        Degradation (fixture ``wdh-07``): a query that fails is logged and
        skipped. Only when **every** query fails does the source declare itself
        unreachable, so a single 500 costs one query, not the run.
        """
        max_records = max(0, int(max_records))
        if max_records == 0:
            return

        client = self._open_client()
        items = self._collect(client, max_records)
        for item in self._prefer_published(items, client, max_records):
            doi = str(item.get("DOI", "")).strip()
            yield RawObservation(
                source_system=self.source_name,
                source_id=doi,
                source_key=source_key_for(item),
                url=str(item.get("URL") or "") or None,
                payload=item,  # VERBATIM
            )

    def _open_client(self) -> HarvestClient:
        if self.client is not None:
            self._client = self.client
        else:
            self._client = HarvestClient()
            self._owns_client = True
        return self._client

    def _collect(self, client: HarvestClient, max_records: int) -> list[dict[str, Any]]:
        """Run every configured query; dedupe by DOI, order preserved."""
        # A couple of spare rows per query so that dropping a preprint whose
        # published version is in the same batch does not cost us a record.
        rows = min(int(self.config.get("rows") or 20), max_records + 5)
        collected: dict[str, dict[str, Any]] = {}
        failures: list[str] = []
        urls = self._query_urls(rows)

        for url in urls:
            result = client.get(url)
            if not result.ok:
                failures.append(result.error or f"HTTP {result.status_code}")
                log.warning("crossref query failed (%s): %s", failures[-1], url)
                continue
            try:
                message = result.json()["message"]
                found = message["items"]
            except Exception as exc:  # upstream changed its shape
                failures.append(f"unexpected response shape: {exc}")
                log.warning("crossref returned an unexpected shape for %s: %s", url, exc)
                continue
            for item in found:
                doi = normalise_doi(item.get("DOI"))
                if not doi:
                    self.drop_log.record(str(item.get("DOI")), "malformed", "crossref listing")
                    continue
                collected.setdefault(doi, item)

        if failures and not collected:
            raise SourceUnreachable("; ".join(failures))
        return list(collected.values())

    def _prefer_published(
        self, items: list[dict[str, Any]], client: HarvestClient, max_records: int
    ) -> Iterator[dict[str, Any]]:
        """cr-04: one record per work, and it is the published one.

        A preprint whose published version is in the same batch is dropped —
        the published item already carries the identity. A preprint whose
        published version is *not* in the batch is kept, because it will merge
        into the published record the moment that record appears; its asserted
        published DOI is resolved first (``resolve_or_drop``), and an
        observation whose target DOI does not resolve is dropped and logged
        rather than allowed to invent an identity.
        """
        present = {normalise_doi(item.get("DOI")) for item in items}
        yielded = 0
        for item in items:
            if yielded >= max_records:
                return
            target = published_version_doi(item)
            if target is not None:
                if target in present:
                    log.info(
                        "crossref: dropping preprint %s; its published version %s is in this batch",
                        item.get("DOI"), target,
                    )
                    continue
                if resolve_or_drop(
                    target, client, self.drop_log,
                    f"is-preprint-of asserted by {item.get('DOI')}",
                ) is None:
                    log.warning(
                        "crossref: dropping %s — asserted published DOI %s did not resolve",
                        item.get("DOI"), target,
                    )
                    continue
            yielded += 1
            yield item

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
        self._client = None
        self._owns_client = False

    # -- map ---------------------------------------------------------------
    def map(self, raw: RawObservation) -> MappedObservation:
        """One Crossref work → ``source.*`` plus provenance. Pure and total."""
        item = raw.payload
        own_doi = normalise_doi(item.get("DOI")) or normalise_doi(raw.source_id)
        preprint_of = published_version_doi(item)
        doi = preprint_of or own_doi

        license_raw, license_id, license_mapped = _license(item)
        retraction = _retraction(item, own_doi)
        title = _plain(_first(item.get("title")))

        landing = str(item.get("URL") or "").strip()
        primary = ""
        resource = item.get("resource")
        if isinstance(resource, dict) and isinstance(resource.get("primary"), dict):
            primary = str(resource["primary"].get("URL") or "").strip()

        if preprint_of:
            # Prefer the published version: it is the canonical landing page,
            # and the preprint is linked rather than listed (fixture cr-04).
            url = f"https://doi.org/{preprint_of}"
            source_urls = [url, landing, primary]
        else:
            url = landing or primary or (f"https://doi.org/{doi}" if doi else "")
            source_urls = [url, primary]

        source = SourceNamespace(
            title=title or None,
            notes=_rich(item.get("abstract")) or None,
            doi=doi,
            url=url or None,
            source_urls=_unique(source_urls),
            authors=_authors(item),
            publisher=_collapse(item.get("publisher", "")) or None,
            published_date=_published_date(item),
            license_raw=license_raw,
            license_id=license_id,
            resource_kind=RESOURCE_KINDS_BY_TYPE.get(str(item.get("type", "")), "publication"),
            container=_plain(_first(item.get("container-title")))
            or _plain((item.get("event") or {}).get("name") if isinstance(item.get("event"), dict) else "")
            or None,
            keywords=_unique(_plain(subject) for subject in item.get("subject") or []),
            resources=[],  # links only, and Crossref's links are machine links
            related_identifiers=_related_identifiers(item, own_doi),
            withdrawn=retraction is not None,
            extra=_extra(item, own_doi, preprint_of),
        )
        if retraction is not None:
            source.extra["crossref_retraction"] = retraction

        provenance: dict[str, FieldProvenance] = {
            "title": FieldProvenance(extraction_method="api"),
            "doi": FieldProvenance(extraction_method="api"),
            "published_date": FieldProvenance(extraction_method="api"),
            "resource_kind": FieldProvenance(extraction_method="api"),
            "license_id": FieldProvenance(extraction_method="pattern"),
        }
        if source.notes:
            provenance["notes"] = FieldProvenance(extraction_method="api")
        if source.authors:
            provenance["authors"] = FieldProvenance(extraction_method="api")
        if source.publisher:
            provenance["publisher"] = FieldProvenance(extraction_method="api")
        if source.container:
            provenance["container"] = FieldProvenance(extraction_method="api")
        if source.related_identifiers:
            provenance["related_identifiers"] = FieldProvenance(extraction_method="api")
        if source.keywords:
            provenance["keywords"] = FieldProvenance(extraction_method="api")
        if retraction is not None:
            provenance["withdrawn"] = FieldProvenance(extraction_method="pattern")

        return MappedObservation(
            identity_key=identity_key(
                doi=doi,
                source_system=self.source_name,
                source_id=raw.source_id,
                title=title or None,
                first_author=source.authors[0].name if source.authors else "",
                year=(source.published_date or "")[:4],
            ),
            source_system=self.source_name,
            source_id=raw.source_id,
            source_key=raw.source_key,
            source=source,
            provenance=provenance,
            fetched_at=raw.fetched_at,
        )


def _unique(values: Iterable[str]) -> list[str]:
    """Order-preserving dedupe that drops empties."""
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out
