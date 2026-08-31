"""DataCite adapter — **owner: Track B (datacite)**. Tier 1, deterministic.

Source
    ``https://api.datacite.org/dois``, queried with DataCite's Elasticsearch
    ``query`` parameter. The queries live in the ``datacite`` block of
    ``sources.yaml`` and nowhere else.

Why query strings and not client ids or prefixes
    Verified live on 2026-08-31: ``https://api.datacite.org/clients?query=IEA
    Wind`` returns **zero** clients. There is no IEA Wind DataCite client to
    sweep. The 391 IEA-Wind-relevant DOIs reachable from the configured
    queries are spread across ``cern.zenodo`` (353), ``doe.pnnl``, ``tib.ubs``,
    ``tib.fraunirb``, ``dk.dtic``, ``rg.rg``, ``arxiv.content``, ``tib.tib``,
    ``bl.mendeley``, ``ethz.zhaw`` and ``doe.osti``. A client-id strategy would
    therefore have to enumerate other people's repositories, and a *prefix*
    strategy is worse still — the dominant prefix is ``10.5281``, which is the
    whole of Zenodo. So: query strings, one net cast per configured query,
    sorted ``-updated`` so the window tracks the source key.

Source key (plan §4.1, ADR-0026)
    ``attributes.updated`` — DataCite bumps it when a client pushes metadata,
    which is exactly the event worth noticing. Nothing else on the payload is
    used, so a view counter ticking over does not produce an event.

Identity
    The DOI, lowercase-normalised through :func:`harvest.identity.identity_key`.
    ``10.5281/ZENODO.123`` and ``10.5281/zenodo.123`` are one record, never two
    (fixture ``dc-05``). ``source_id`` is normalised the same way, so a case
    variant does not even churn the source key.

Fixtures owned
    ``dc-01`` .. ``dc-09`` (``dc-08`` is a reconciliation case, not a mapping
    one, and belongs to the reconciler track).

Watch for
    * ``state`` must be ``findable``. A ``registered`` or ``draft`` DOI is
      skipped by :meth:`DataCiteAdapter.harvest` and logged — never publish a
      record for a DOI that does not resolve (``dc-04``). The public API only
      *serves* findable DOIs, but the guard is cheap and the fixture is
      explicit.
    * Multiple titles: the **primary** title is the one with no ``titleType``.
      ``AlternativeTitle``, ``TranslatedTitle`` and ``Subtitle`` go to
      ``source.extra["alternate_titles"]`` (``dc-02``).
    * ``publisher`` is a string in schema <= 4.4 and an object in 4.5+. Both are
      accepted; the object is also kept verbatim in
      ``source.extra["datacite_publisher"]`` (``dc-07``).
    * ``relatedIdentifiers`` feed version resolution and paper<->dataset
      linking. **No record is ever created for a target** (``dc-06``) — this
      adapter yields exactly the DOIs the queries returned and nothing else.
    * A rights string with no SPDX identifier and no recognised text is
      ``notspecified`` AND flagged by ``license_mapped: false``, never silently
      dropped (``dc-09``). An open licence is never inferred.
    * ``resourceTypeGeneral: "Other"`` is left for later classification: the
      deterministic mapping yields ``other`` and the whole ``types`` block is
      retained verbatim in ``source.extra["datacite_types"]`` so a classifier
      can see what the source actually said (``dc-03``). Tier 1 never calls a
      model itself (ADR-0024).
    * IEA Wind task attribution is **not** emitted as ``source.iea_task``.
      DataCite states it only as free text (``publisher: "IEA Wind Task 49"``,
      subjects, titles), and a group name that is not in ``groups.yaml`` would
      fail the CKAN gate for the entire run — DataCite carries real DOIs for
      Task 23 and Task 24, which have no group. The parsed candidates are put
      in ``source.extra["iea_task_candidates"]`` for the reconciler to promote
      to ``local.iea_task`` after checking the register. ``map()`` is pure and
      cannot read ``groups.yaml`` to check for itself.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from urllib.parse import urlencode

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, SourceUnreachable, register
from harvest.doi import normalise_doi
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
from harvest.sanitize import sanitize_html

__all__ = ["DataCiteAdapter", "DEFAULT_API", "REQUIRED_STATE"]

log = logging.getLogger(__name__)

DEFAULT_API = "https://api.datacite.org/dois"

#: The only DOI state that may become a record (``dc-04``).
REQUIRED_STATE = "findable"

#: ``types.resourceTypeGeneral`` (lowercased) -> ``source.resource_kind``.
#: ``None`` means "the general type says nothing useful; try the free text".
_RESOURCE_TYPE_GENERAL: dict[str, str | None] = {
    "dataset": "dataset",
    "software": "software",
    "computationalnotebook": "software",
    "workflow": "software",
    "model": "model",
    "report": "report",
    "outputmanagementplan": "report",
    "text": "publication",
    "journalarticle": "publication",
    "conferencepaper": "publication",
    "conferenceproceeding": "publication",
    "book": "publication",
    "bookchapter": "publication",
    "dissertation": "publication",
    "preprint": "publication",
    "peerreview": "publication",
    "datapaper": "publication",
    "standard": "publication",
    "audiovisual": "other",
    "award": "other",
    "collection": "other",
    "event": "other",
    "image": "other",
    "instrument": "other",
    "interactiveresource": "other",
    "physicalobject": "other",
    "project": "other",
    "service": "other",
    "sound": "other",
    "other": None,
}

#: Free-text fallback over ``types.resourceType`` / ``types.schemaOrg``, tried
#: in order so that the more specific phrase wins. Deterministic, no model.
_FREE_TEXT_HINTS: tuple[tuple[str, str], ...] = (
    ("computationalnotebook", "software"),
    ("sourcecode", "software"),
    ("source code", "software"),
    ("software", "software"),
    ("technical report", "report"),
    ("deliverable", "report"),
    ("report", "report"),
    ("journal article", "publication"),
    ("journalarticle", "publication"),
    ("scholarlyarticle", "publication"),
    ("conference", "publication"),
    ("proceedings", "publication"),
    ("dissertation", "publication"),
    ("thesis", "publication"),
    ("preprint", "publication"),
    ("presentation", "publication"),
    ("poster", "publication"),
    ("article", "publication"),
    ("paper", "publication"),
    ("database", "dataset"),
    ("data set", "dataset"),
    ("dataset", "dataset"),
    ("model", "model"),
)

#: ``info:eu-repo/semantics/*`` is the only access vocabulary DataCite carries.
#: Anything else leaves ``access_status`` unset — access is never inferred from
#: the licence.
_EU_REPO_ACCESS: dict[str, str] = {
    "openaccess": "open",
    "closedaccess": "restricted",
    "restrictedaccess": "restricted",
    "embargoedaccess": "embargoed",
}

#: "IEA Wind [TCP] Task 49", "IEA-Wind Task 25/63". The "IEA Wind" adjacency is
#: required: a bare "Task 4" in a title means nothing.
_TASK_RE = re.compile(
    r"iea[\s\u2010-\u2015-]*wind(?:\s+tcp)?\s+task\s+(\d{1,3})(?:\s*/\s*(\d{1,3}))?",
    re.IGNORECASE,
)


def _first_string(value: Any) -> str | None:
    """Coerce a DataCite scalar-or-object field to a non-empty string."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        return _first_string(value.get("name") or value.get("title"))
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@register
class DataCiteAdapter(Adapter):
    """Harvest IEA-Wind-relevant DOIs from the DataCite REST API."""

    source_name = "datacite"
    tier = 1
    source_key_semantics = "attributes.updated — DataCite's own metadata-push timestamp"

    # -- harvest ----------------------------------------------------------
    def __init__(self, config=None, client: Any = None) -> None:
        super().__init__(config=config, client=client)
        self._own_client: HarvestClient | None = None

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        if self._own_client is None:
            self._own_client = HarvestClient(
                min_interval=float(self.config.get("min_request_interval_seconds", 0.2))
            )
        return self._own_client

    def close(self) -> None:
        if self._own_client is not None:
            self._own_client.close()
            self._own_client = None

    def _search_urls(self, page_size: int) -> list[str]:
        """One URL per configured query. Config lives in ``sources.yaml`` only."""
        api = str(self.config.get("api") or DEFAULT_API)
        queries = [str(q) for q in (self.config.get("queries") or []) if str(q).strip()]
        if not queries:
            log.warning("datacite: sources.yaml declares no queries; nothing to harvest")
        sort = str(self.config.get("sort") or "-updated")
        urls = []
        for query in queries:
            params = [("query", query), ("page[size]", str(page_size)), ("sort", sort)]
            urls.append(f"{api}?{urlencode(params)}")
        return urls

    @staticmethod
    def source_key_for(payload: dict[str, Any]) -> str:
        """``attributes.updated``, normalised to second precision and ``Z``.

        The normalisation exists because DataCite spells the same instant two
        ways: the ``/dois`` listing returns ``2026-08-30T09:55:55Z`` and the
        ``/dois/{doi}`` single-record endpoint returns
        ``2026-08-30T09:55:55.000Z`` (verified 2026-08-31). Without this, a
        change of endpoint would rewrite the source key for every record and
        turn append-on-change into append-always (ADR-0026). The semantics are
        unchanged: this is still DataCite's metadata-push timestamp.
        """
        attributes = payload.get("attributes") or payload
        raw = str(attributes.get("updated") or "").strip()
        if not raw:
            return ""
        text = raw[:-1] if raw.endswith("Z") else raw
        if "." in text:
            text = text.split(".", 1)[0]
        return f"{text}Z" if raw.endswith("Z") else text

    @staticmethod
    def is_findable(payload: dict[str, Any]) -> bool:
        """``dc-04`` — only ``state: findable`` DOIs may become records.

        A ``registered`` or ``draft`` DOI has no resolvable landing page, so
        publishing a record for it would publish a dead citation.
        """
        state = str((payload.get("attributes") or {}).get("state") or "").strip().lower()
        return state == REQUIRED_STATE

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        # Ask for a page rather than exactly ``limit``, because non-findable
        # DOIs and cross-query duplicates are dropped after the response
        # arrives; but never more than one page, and never more than the
        # configured ceiling. In practice the first query fills the cap and no
        # second request is made at all.
        required_state = str(self.config.get("state") or REQUIRED_STATE).strip().lower()
        page_size = max(limit, min(int(self.config.get("page_size", 25) or 25), limit * 5))
        urls = self._search_urls(page_size)

        seen: set[str] = set()
        yielded = 0
        reached = 0
        errors: list[str] = []

        for url in urls:
            if yielded >= limit:
                break
            result = self._client().get(url)
            if result.status_code == 304:
                reached += 1
                log.info("datacite: 304 for %s; listing unchanged", url)
                continue
            if not result.ok:
                errors.append(result.error or f"HTTP {result.status_code}")
                log.warning("datacite: query failed (%s): %s", errors[-1], url)
                continue
            reached += 1
            try:
                document = result.json()
            except Exception as exc:
                errors.append(f"unparseable response: {exc}")
                log.warning("datacite: unparseable response from %s: %s", url, exc)
                continue

            for item in _as_list(document.get("data")):
                if yielded >= limit:
                    break
                if not isinstance(item, dict):
                    continue
                attributes = item.get("attributes") or {}
                doi = normalise_doi(attributes.get("doi") or item.get("id"))
                if not doi:
                    log.warning("datacite: item with no usable DOI: %r", item.get("id"))
                    continue
                if doi in seen:
                    continue
                seen.add(doi)

                state = str(attributes.get("state") or "").strip().lower()
                if state != required_state:
                    # dc-04: skipped AND logged. Never silent.
                    log.warning(
                        "datacite: skipping %s — state is %r, not %r",
                        doi, state or "(absent)", required_state,
                    )
                    continue

                yield RawObservation(
                    source_system=self.source_name,
                    source_id=doi,
                    source_key=self.source_key_for(item),
                    url=attributes.get("url") or None,
                    payload=item,          # VERBATIM
                )
                yielded += 1

        if reached == 0 and urls:
            raise SourceUnreachable(
                "; ".join(errors) or "no DataCite query could be reached"
            )
        if errors:
            log.warning("datacite: %s of %s queries failed", len(errors), len(urls))

    # -- map --------------------------------------------------------------
    def map(self, raw: RawObservation) -> MappedObservation:
        """Pure. No network, no clock, no filesystem (``harvest/CONTRACT.md`` §2)."""
        payload = raw.payload or {}
        attributes = payload.get("attributes") or payload

        doi = normalise_doi(attributes.get("doi") or payload.get("id")) or None

        title, alternate_titles = self._titles(attributes)
        notes, other_descriptions = self._descriptions(attributes)
        license_raw, license_id = self._license(attributes)
        resource_kind, kind_method = self._resource_kind(attributes)
        publisher, publisher_object = self._publisher(attributes)
        landing_url = _first_string(attributes.get("url")) or raw.url

        keywords = self._keywords(attributes)
        related = self._related_identifiers(attributes)
        access_status = self._access_status(attributes)
        container_block = attributes.get("container")
        if not isinstance(container_block, dict):
            container_block = {}
        container = _first_string(container_block.get("title"))
        candidates = self._task_candidates(attributes, title, alternate_titles, notes)

        extra: dict[str, Any] = {}
        if attributes.get("state"):
            extra["datacite_state"] = attributes["state"]
        client_id = (
            ((payload.get("relationships") or {}).get("client") or {}).get("data") or {}
        ).get("id")
        if client_id:
            extra["datacite_client_id"] = client_id
        if attributes.get("schemaVersion"):
            extra["datacite_schema_version"] = attributes["schemaVersion"]
        if attributes.get("types"):
            extra["datacite_types"] = attributes["types"]
        if publisher_object is not None:
            extra["datacite_publisher"] = publisher_object
        if container_block:
            extra["datacite_container"] = container_block
        if attributes.get("identifiers"):
            extra["datacite_identifiers"] = attributes["identifiers"]
        if attributes.get("rightsList"):
            extra["rights_list"] = attributes["rightsList"]
        if alternate_titles:
            extra["alternate_titles"] = alternate_titles
        if other_descriptions:
            extra["other_descriptions"] = other_descriptions
        if candidates:
            extra["iea_task_candidates"] = candidates

        source = SourceNamespace(
            title=title,
            notes=notes,
            doi=doi,
            url=landing_url,
            source_urls=[landing_url] if landing_url else [],
            authors=self._authors(attributes),
            publisher=publisher,
            published_date=self._published_date(attributes),
            version=_first_string(attributes.get("version")),
            license_raw=license_raw,
            license_id=license_id,
            resource_kind=resource_kind,
            access_status=access_status,
            container=container,
            keywords=keywords,
            resources=self._resources(attributes),
            related_identifiers=related,
            withdrawn=attributes.get("isActive") is False,
            extra=extra,
        )

        provenance: dict[str, FieldProvenance] = {}
        api = FieldProvenance(extraction_method="api")
        pattern = FieldProvenance(extraction_method="pattern")
        for field, populated in (
            ("title", title),
            ("notes", notes),
            ("doi", doi),
            ("authors", source.authors),
            ("publisher", publisher),
            ("published_date", source.published_date),
            ("version", source.version),
            ("container", container),
            ("keywords", keywords),
            ("related_identifiers", related),
            ("resources", source.resources),
        ):
            if populated:
                provenance[field] = api
        if license_id:
            provenance["license_id"] = pattern
        if resource_kind:
            provenance["resource_kind"] = FieldProvenance(extraction_method=kind_method)
        if access_status:
            provenance["access_status"] = pattern

        return MappedObservation(
            identity_key=identity_key(
                doi=doi,
                source_system=self.source_name,
                source_id=raw.source_id,
                title=title,
            ),
            source_system=self.source_name,
            source_id=normalise_doi(raw.source_id) or raw.source_id,
            source_key=raw.source_key,
            source=source,
            provenance=provenance,
            fetched_at=raw.fetched_at,
        )

    # -- mapping helpers (all pure) ---------------------------------------
    @staticmethod
    def _titles(attributes: dict[str, Any]) -> tuple[str | None, list[dict[str, str]]]:
        """``dc-02`` — the primary title is the one with no ``titleType``."""
        titles = [t for t in _as_list(attributes.get("titles")) if isinstance(t, dict)]
        primary: str | None = None
        alternates: list[dict[str, str]] = []
        for entry in titles:
            text = _first_string(entry.get("title"))
            if not text:
                continue
            title_type = _first_string(entry.get("titleType"))
            if title_type is None and primary is None:
                primary = text
                continue
            alternate: dict[str, str] = {"title": text}
            if title_type:
                alternate["title_type"] = title_type
            language = _first_string(entry.get("lang"))
            if language:
                alternate["lang"] = language
            alternates.append(alternate)
        if primary is None and alternates:
            # Every title was typed; the first one is all we have to show.
            promoted = alternates.pop(0)
            primary = promoted["title"]
        return primary, alternates

    @staticmethod
    def _descriptions(attributes: dict[str, Any]) -> tuple[str | None, list[dict[str, str]]]:
        """Abstract first; everything else kept, sanitised, in ``extra``."""
        entries = [d for d in _as_list(attributes.get("descriptions")) if isinstance(d, dict)]
        cleaned: list[dict[str, str]] = []
        for entry in entries:
            text = _first_string(entry.get("description"))
            if not text:
                continue
            item = {"description": sanitize_html(text)}
            kind = _first_string(entry.get("descriptionType"))
            if kind:
                item["description_type"] = kind
            cleaned.append(item)
        if not cleaned:
            return None, []
        index = next(
            (i for i, e in enumerate(cleaned) if e.get("description_type") == "Abstract"), 0
        )
        primary = cleaned.pop(index)
        return primary["description"], cleaned

    @staticmethod
    def _authors(attributes: dict[str, Any]) -> list[Author]:
        authors: list[Author] = []
        for creator in _as_list(attributes.get("creators")):
            if not isinstance(creator, dict):
                continue
            name = _first_string(creator.get("name"))
            if not name:
                given = _first_string(creator.get("givenName")) or ""
                family = _first_string(creator.get("familyName")) or ""
                name = ", ".join(part for part in (family, given) if part) or None
            if not name:
                continue
            orcid = None
            for identifier in _as_list(creator.get("nameIdentifiers")):
                if not isinstance(identifier, dict):
                    continue
                scheme = str(identifier.get("nameIdentifierScheme") or "").upper()
                value = _first_string(identifier.get("nameIdentifier"))
                if scheme == "ORCID" and value:
                    orcid = value.rsplit("/", 1)[-1]
                    break
            affiliations = [
                _first_string(item) for item in _as_list(creator.get("affiliation"))
            ]
            affiliation = next((a for a in affiliations if a), None)
            authors.append(
                Author(
                    name=name,
                    given=_first_string(creator.get("givenName")),
                    family=_first_string(creator.get("familyName")),
                    orcid=orcid,
                    affiliation=affiliation,
                )
            )
        return authors

    @staticmethod
    def _publisher(attributes: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
        """``dc-07`` — schema <= 4.4 says a string, 4.5+ says an object."""
        raw = attributes.get("publisher")
        if isinstance(raw, dict):
            return _first_string(raw), raw
        return _first_string(raw), None

    @staticmethod
    def _published_date(attributes: dict[str, Any]) -> str | None:
        """The ``Issued`` date verbatim; else the publication year, year-only.

        A month is never fabricated. Where several ``Issued`` dates exist (a
        full date and a bare year, which happens), the most specific real
        string wins.
        """
        best: str | None = None
        for entry in _as_list(attributes.get("dates")):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("dateType") or "").strip().lower() != "issued":
                continue
            value = _first_string(entry.get("date"))
            if value and (best is None or len(value) > len(best)):
                best = value
        if best:
            return best
        year = attributes.get("publicationYear")
        return str(year) if year else None

    @staticmethod
    def _license(attributes: dict[str, Any]) -> tuple[str | None, str | None]:
        """``dc-09`` — map the first rights entry that maps; flag the rest.

        Candidates are taken **within one rights entry** in the order SPDX
        identifier, rights text, rights URI, so ``license_raw`` and
        ``license_id`` never come from different statements. Nothing is
        inferred: an unmappable rights string yields ``notspecified`` and
        ``license_mapped: false`` downstream.
        """
        entries = [r for r in _as_list(attributes.get("rightsList")) if isinstance(r, dict)]
        fallback: str | None = None
        for entry in entries:
            candidates = [
                _first_string(entry.get("rightsIdentifier")),
                _first_string(entry.get("rights")),
                _first_string(entry.get("rightsUri")),
            ]
            for candidate in candidates:
                if not candidate:
                    continue
                if fallback is None:
                    fallback = candidate
                mapped_id, mapped = map_license(candidate)
                if mapped and mapped_id != "notspecified":
                    return candidate, mapped_id
        if fallback is not None:
            return fallback, "notspecified"
        return None, "notspecified"

    @classmethod
    def _resource_kind(cls, attributes: dict[str, Any]) -> tuple[str, str]:
        """``dc-03`` — deterministic only. Returns ``(kind, extraction_method)``.

        ``resourceTypeGeneral: "Other"`` falls through to the free-text
        ``resourceType``/``schemaOrg`` hint; if that says nothing either, the
        kind is ``other`` and the whole ``types`` block is retained in
        ``source.extra["datacite_types"]`` for a later classifier. Tier 1 never
        calls a model itself (ADR-0024).
        """
        types = attributes.get("types") or {}
        if not isinstance(types, dict):
            types = {}
        general = str(types.get("resourceTypeGeneral") or "").strip().lower()
        if general in _RESOURCE_TYPE_GENERAL:
            mapped = _RESOURCE_TYPE_GENERAL[general]
            if mapped:
                return mapped, "api"
        free_text = " ".join(
            str(types.get(key) or "") for key in ("resourceType", "schemaOrg")
        ).lower()
        for needle, kind in _FREE_TEXT_HINTS:
            if needle in free_text:
                return kind, "pattern"
        return "other", "api"

    @staticmethod
    def _keywords(attributes: dict[str, Any]) -> list[str]:
        keywords: list[str] = []
        for entry in _as_list(attributes.get("subjects")):
            value = _first_string(entry.get("subject") if isinstance(entry, dict) else entry)
            if value and value not in keywords:
                keywords.append(value)
        return keywords

    @staticmethod
    def _related_identifiers(attributes: dict[str, Any]) -> list[dict[str, str]]:
        """``dc-06`` — links only. No record is ever created for a target.

        DOI-typed targets are case-normalised so the set-valued union across
        sources actually unions (``dc-05`` applies to link targets too).
        """
        related: list[dict[str, str]] = []
        for entry in _as_list(attributes.get("relatedIdentifiers")):
            if not isinstance(entry, dict):
                continue
            identifier = _first_string(entry.get("relatedIdentifier"))
            if not identifier:
                continue
            identifier_type = _first_string(entry.get("relatedIdentifierType"))
            if identifier_type and identifier_type.upper() == "DOI":
                identifier = normalise_doi(identifier) or identifier
            item: dict[str, str] = {"identifier": identifier}
            if identifier_type:
                item["identifier_type"] = identifier_type
            relation = _first_string(entry.get("relationType"))
            if relation:
                item["relation"] = relation
            if item not in related:
                related.append(item)
        return related

    @staticmethod
    def _access_status(attributes: dict[str, Any]) -> str | None:
        """``info:eu-repo/semantics/*`` only. Access is never inferred."""
        for entry in _as_list(attributes.get("rightsList")):
            if not isinstance(entry, dict):
                continue
            for value in (entry.get("rights"), entry.get("rightsUri")):
                text = str(value or "").strip().lower()
                if "info:eu-repo/semantics/" not in text:
                    continue
                token = text.rsplit("/", 1)[-1]
                if token in _EU_REPO_ACCESS:
                    return _EU_REPO_ACCESS[token]
        return None

    @staticmethod
    def _resources(attributes: dict[str, Any]) -> list[dict[str, str]]:
        """Links, never mirrors. DataCite is a registry: usually empty."""
        resources: list[dict[str, str]] = []
        for value in _as_list(attributes.get("contentUrl")):
            url = _first_string(value)
            if url and not any(r["url"] == url for r in resources):
                resources.append({"url": url})
        return resources

    @staticmethod
    def _task_candidates(
        attributes: dict[str, Any],
        title: str | None,
        alternates: list[dict[str, str]],
        notes: str | None = None,
    ) -> list[str]:
        """Parsed ``IEA Wind Task N`` mentions — **candidates only**.

        Not promoted to ``source.iea_task``: a group name absent from
        ``groups.yaml`` fails the CKAN gate for the whole run, and ``map()`` is
        pure so it cannot check the register. The reconciler validates these
        and writes ``local.iea_task``.
        """
        haystacks: list[str] = []
        publisher = attributes.get("publisher")
        text = _first_string(publisher)
        if text:
            haystacks.append(text)
        if title:
            haystacks.append(title)
        if notes:
            haystacks.append(notes)
        haystacks.extend(entry["title"] for entry in alternates)
        for entry in _as_list(attributes.get("subjects")):
            value = _first_string(entry.get("subject")) if isinstance(entry, dict) else None
            if value:
                haystacks.append(value)

        found: list[str] = []
        for haystack in haystacks:
            for match in _TASK_RE.finditer(haystack):
                for number in match.groups():
                    if not number:
                        continue
                    name = f"task-{int(number)}"
                    if name not in found:
                        found.append(name)
        return sorted(found)
