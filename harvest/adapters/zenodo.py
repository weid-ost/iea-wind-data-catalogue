"""Zenodo adapter — **owner: Track A (zenodo)**.

Source
    Zenodo's REST API at ``https://zenodo.org/api/records``, filtered by the
    IEA Wind communities listed under ``sources.yaml -> sources.zenodo.communities``.
    Public metadata needs no authentication.

Source key (plan §4.1, ADR-0026) — **VERIFIED LIVE 2026-08-31**
    The field is ``revision`` (a top-level integer on every record; there is no
    ``revision_id`` on this API surface). The key is
    ``"<revision>@<version DOI>"``, exactly as ADR-0026 requires ("record
    revision id (with the version DOI)").

    The version DOI is **not** decoration. The catalogue's identity is the
    *concept* DOI, so a new version arrives as a different record under the same
    identity — and the revision counter restarts per record. Live evidence, from
    the OpenOA concept ``10.5281/zenodo.4549875``:

        record 18424146 (v3.2)   revision 4
        record 18421933 (v3.1.4) revision 4

    A bare-revision key would have compared ``4`` to ``4`` and silently skipped
    the v3.1.4 → v3.2 update, which is precisely the failure ADR-0026 exists to
    prevent. Pairing the revision with the version DOI makes the key change
    whenever *either* the metadata is edited or a new version is released.

    Fallback when a payload carries neither (a tombstone, a schema change):
    :func:`~harvest.adapters.base.payload_hash` over ``metadata``.

Identity
    The **concept DOI**, never the version DOI (fixture ``zen-02``). A record
    with thirteen versions is ONE catalogue record whose files are resources,
    never thirteen records. The latest version's metadata is displayed under the
    concept identity and the first-seen date is kept (``zen-03``), which falls
    out of the event log: ``source.*`` is replaced wholesale per scrape and
    ``first_seen`` comes from the first event.

    The version DOI is not thrown away — it is carried in
    ``source.extra.zenodo_version_doi`` and as an ``IsVersionOf`` entry in
    ``source.related_identifiers``.

robots.txt
    ``https://zenodo.org/robots.txt`` carries ``Disallow: /api``. That directive
    is aimed at search-engine crawlers indexing API responses; Zenodo documents,
    publishes and rate-limits this REST API for exactly the programmatic use
    this catalogue makes of it, and we identify ourselves with a contact
    address. ``sources.yaml`` therefore sets ``respect_robots: false`` for the
    Zenodo block **and** slows the client to one request per second, inside
    Zenodo's documented 60 requests/minute anonymous budget and far gentler than
    the crawl-delay of 10 that a crawler would owe. The decision is config, not
    code: flip ``respect_robots`` back to ``true`` and the adapter reports the
    source as unreachable rather than fetching anything.

Fixtures owned
    ``zen-01`` .. ``zen-12`` under ``fixtures/zenodo/``. All but ``zen-06``,
    ``zen-07`` and the ``zen-01`` reference are real payloads captured verbatim
    from the live API; the invented ones say so in their ``invented`` field.

Watch for
    * ``access_right`` of ``restricted`` / ``embargoed`` -> ``access_status``,
      never imply the files are downloadable (``zen-05``, ``zen-06``). Zenodo
      returns ``files: []`` for restricted records, so there is nothing to link.
    * A record in two IEA Wind communities resolves to one identity with a
      unioned ``iea_task`` (``zen-11``).
    * Tombstoned records get a ``withdrawn`` event — never a ``scraped`` one
      (``zen-12``, ADR-0027, runbook *handle-a-withdrawn-record*). A Zenodo
      tombstone body carries no metadata at all, so scraping it would blank the
      record; :meth:`ZenodoAdapter.is_tombstone` detects it and
      :meth:`ZenodoAdapter.recheck_withdrawn` appends the withdrawal against the
      identity the caller already knows.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import quote

from harvest import DEFAULT_MAX_RECORDS
from harvest import config as _config
from harvest.adapters.base import Adapter, SourceUnreachable, payload_hash, register
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
    normalise_task,
)
from harvest.sanitize import sanitize_html

__all__ = ["ZenodoAdapter", "ZENODO_API", "resource_kind_for", "access_status_for", "tasks_for_community"]

log = logging.getLogger(__name__)

ZENODO_API = "https://zenodo.org/api/records"

#: Zenodo's page size cap on ``/api/records``. Larger values are a 400.
MAX_PAGE_SIZE = 25

#: Communities used when ``sources.yaml`` supplies none (all verified live).
FALLBACK_COMMUNITIES: tuple[dict[str, Any], ...] = (
    {"slug": "iea_wind_task_43"},
    {"slug": "ieawindtask32"},
    {"slug": "ieawindtask52"},
)

#: ``metadata.resource_type.type`` -> catalogue ``resource_kind``.
#: Anything absent falls through to ``other`` — never guessed at, never blank.
RESOURCE_KIND_BY_TYPE: dict[str, str] = {
    "dataset": "dataset",
    "software": "software",
    "model": "model",
    "publication": "publication",
    "poster": "other",
    "presentation": "other",
    "image": "other",
    "video": "other",
    "lesson": "other",
    "physicalobject": "other",
    "event": "other",
    "workflow": "other",
    "other": "other",
}

#: ``publication`` subtypes that are reports rather than literature.
REPORT_SUBTYPES = frozenset({"report", "deliverable", "technicalnote", "milestone"})

#: ``metadata.access_right`` -> catalogue ``access_status``.
ACCESS_STATUS_BY_RIGHT: dict[str, str] = {
    "open": "open",
    "restricted": "restricted",
    "embargoed": "embargoed",
    "closed": "metadata-only",
}

#: Community slugs that spell out their task number: ``iea_wind_task_43``,
#: ``ieawindtask32``, ``ieawindtask51_austria``, ``ieawindtask56-oc7-wp22``.
#: Slugs that do not (``wakebench``, ``jam``, ``coldclimatewind``,
#: ``lidar_ontology``) are mapped by ``sources.yaml`` instead.
_TASK_FROM_SLUG_RE = re.compile(r"ieawindtask0*(\d{1,3})")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Pure helpers — shared by map() and by the tests
# ---------------------------------------------------------------------------


def resource_kind_for(resource_type: Any) -> str | None:
    """``metadata.resource_type`` -> one of :data:`~harvest.models.RESOURCE_KINDS`.

    ``publication`` splits on its subtype: a ``report``/``deliverable`` is a
    ``report``, everything else is a ``publication``. Returns ``None`` when the
    source states no type — an unstated type is not guessed.
    """
    if not isinstance(resource_type, dict):
        return None
    kind = str(resource_type.get("type") or "").strip().lower()
    if not kind:
        return None
    subtype = str(resource_type.get("subtype") or "").strip().lower()
    if kind == "publication" and subtype in REPORT_SUBTYPES:
        return "report"
    return RESOURCE_KIND_BY_TYPE.get(kind, "other")


def access_status_for(access_right: Any) -> str:
    """``metadata.access_right`` -> one of :data:`~harvest.models.ACCESS_STATUSES`.

    An unstated or unrecognised access right is ``unknown``, never ``open``.
    """
    return ACCESS_STATUS_BY_RIGHT.get(str(access_right or "").strip().lower(), "unknown")


def tasks_for_community(
    slug: str,
    declared: dict[str, list[str]] | None = None,
    known: set[str] | None = None,
) -> list[str]:
    """IEA Wind task group names for a Zenodo community slug.

    ``declared`` is the ``sources.yaml`` mapping (which covers the slugs whose
    names carry no number — ``wakebench`` is Task 31). Slugs that do spell out a
    number are recognised by pattern, so the adapter still attributes a task
    when it is constructed with no config at all.

    **A task the register does not know is dropped, not emitted** (scrape-02).
    The community slug is attacker- and stranger-controlled: anyone can create
    a Zenodo community called ``ieawindtask777``, and IEA Wind itself will one
    day create a real ``ieawindtask66``. Either way an unknown ``task-N`` would
    become a ``groups[].name`` that is not in ``groups.yaml``, which fails the
    CKAN gate — and since ``events/`` is append-only, it would fail it on every
    subsequent run too, blocking the deploy until a human edited the register.
    OSTI's adapter has always filtered this way; this mirrors it.
    """
    slug = str(slug or "").strip()
    if not slug:
        return []
    if declared and slug in declared:
        tasks = [normalise_task(task) for task in declared[slug]]
    else:
        match = _TASK_FROM_SLUG_RE.search(_NON_ALNUM_RE.sub("", slug.lower()))
        tasks = [f"task-{int(match.group(1))}"] if match else []

    register = _config.group_names() if known is None else known
    if not register:  # no register available: emit what we found, gate catches it
        return tasks
    kept: list[str] = []
    for task in tasks:
        canonical = _config.canonical_group(task)
        if canonical in register:
            if canonical not in kept:
                kept.append(canonical)
        else:
            log.warning(
                "zenodo community %r implies %r, which is not in groups.yaml; "
                "dropping the attribution rather than writing a record the CKAN "
                "gate would refuse",
                slug,
                canonical,
            )
    return kept


def _file_format(entry: dict[str, Any]) -> str | None:
    """The resource format: the stated type, else the file extension."""
    stated = entry.get("type")
    if stated:
        return str(stated).lower()
    key = str(entry.get("key") or "")
    _, _, suffix = key.rpartition(".")
    return suffix.lower() if suffix and suffix != key else None


def _authors(creators: Any) -> list[Author]:
    authors: list[Author] = []
    for creator in creators or []:
        if not isinstance(creator, dict):
            continue
        name = creator.get("name")
        if not name:
            continue
        authors.append(
            Author(
                name=str(name),
                orcid=creator.get("orcid") or None,
                affiliation=creator.get("affiliation") or None,
            )
        )
    return authors


def _related_identifiers(metadata: dict[str, Any], concept_doi: str | None,
                         version_doi: str | None) -> list[dict[str, Any]]:
    """``IsVersionOf`` the concept, then whatever the source states, verbatim."""
    related: list[dict[str, Any]] = []
    if concept_doi and version_doi and concept_doi != version_doi:
        related.append(
            {"relation": "IsVersionOf", "identifier": concept_doi, "identifier_type": "DOI"}
        )
    for entry in metadata.get("related_identifiers") or []:
        if not isinstance(entry, dict) or not entry.get("identifier"):
            continue
        mapped: dict[str, Any] = {
            "relation": entry.get("relation"),
            "identifier": entry.get("identifier"),
        }
        scheme = entry.get("scheme")
        if scheme:
            mapped["identifier_type"] = str(scheme).upper()
        related.append({k: v for k, v in mapped.items() if v is not None})
    return related


def _license_raw(metadata: dict[str, Any]) -> str | None:
    licence = metadata.get("license")
    if isinstance(licence, dict):
        value = licence.get("id") or licence.get("identifier")
    else:
        value = licence
    value = str(value).strip() if value else ""
    return value or None


def _container(metadata: dict[str, Any]) -> str | None:
    for key in ("journal", "meeting", "imprint"):
        block = metadata.get(key)
        if isinstance(block, dict) and block.get("title"):
            return str(block["title"])
    return None


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


@register
class ZenodoAdapter(Adapter):
    source_name = "zenodo"
    tier = 1
    source_key_semantics = "InvenioRDM record revision id + version DOI"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._client: HarvestClient | None = None
        self._owns_client = False

    # -- configuration -----------------------------------------------------
    @property
    def api(self) -> str:
        return str(self.config.get("api") or ZENODO_API)

    def communities(self) -> list[dict[str, Any]]:
        declared = self.config.get("communities")
        if not declared:
            return [dict(entry) for entry in FALLBACK_COMMUNITIES]
        out: list[dict[str, Any]] = []
        for entry in declared:
            out.append(dict(entry) if isinstance(entry, dict) else {"slug": str(entry)})
        return out

    def community_tasks(self) -> dict[str, list[str]]:
        """``{community slug: [task group name, ...]}`` as ``sources.yaml`` states it."""
        mapping: dict[str, list[str]] = {}
        for entry in self.communities():
            slug = entry.get("slug")
            tasks = entry.get("iea_task")
            if slug and tasks:
                mapping[str(slug)] = [str(task) for task in tasks]
        return mapping

    # -- the change token --------------------------------------------------
    @staticmethod
    def source_key_for(payload: dict[str, Any]) -> str:
        """``"<revision>@<version DOI>"`` — see the module docstring for why both."""
        parts: list[str] = []
        revision = payload.get("revision")
        if revision is None:
            revision = payload.get("revision_id")
        if revision is not None:
            parts.append(str(revision))
        metadata = payload.get("metadata") or {}
        version_doi = normalise_doi(payload.get("doi") or metadata.get("doi"))
        if version_doi:
            parts.append(version_doi)
        if not parts:
            return payload_hash(metadata or payload)
        return "@".join(parts)

    # -- withdrawal --------------------------------------------------------
    @staticmethod
    def is_tombstone(payload: dict[str, Any]) -> bool:
        """Does this payload say the record was removed?

        Zenodo answers a deleted record with HTTP 410 and a body of exactly
        ``{"status": 410, "message": "Record deleted", "tombstone": {...}}``
        (fixture ``zen-12``, captured live). InvenioRDM's newer shapes
        (``deletion_status``, ``is_deleted``) are recognised too.

        A ``404`` body counts as well, but only because the sole caller that can
        see one — :meth:`recheck_withdrawn` — is asking about a record the
        catalogue already holds, which is the runbook's second withdrawal signal
        ("the upstream API returns 404/410 for a record it previously
        returned"). Absence from a listing is **never** withdrawal.
        """
        if not isinstance(payload, dict):
            return False
        if payload.get("tombstone"):
            return True
        if payload.get("is_deleted") is True:
            return True
        deletion_status = payload.get("deletion_status")
        if isinstance(deletion_status, dict) and deletion_status.get("is_deleted"):
            return True
        if isinstance(deletion_status, str) and deletion_status.upper() in {"D", "P", "X"}:
            return True
        status = payload.get("status")
        if isinstance(status, bool):
            return False
        if isinstance(status, int):
            return status in (404, 410)
        return str(status or "").strip().lower() in {"removed", "deleted"}

    @staticmethod
    def tombstone_note(payload: dict[str, Any]) -> str:
        """A human sentence for the ``withdrawn`` event's ``note``."""
        tombstone = payload.get("tombstone") or {}
        reason = (tombstone.get("removal_reason") or {}).get("id") if isinstance(
            tombstone.get("removal_reason"), dict
        ) else tombstone.get("removal_reason")
        bits = ["record removed at Zenodo"]
        if reason:
            bits.append(f"reason: {reason}")
        if tombstone.get("note"):
            bits.append(str(tombstone["note"]))
        if tombstone.get("removal_date"):
            bits.append(f"removed {tombstone['removal_date']}")
        return "; ".join(bits)

    def recheck_withdrawn(
        self,
        identity: str,
        source_id: str,
        events_dir: Path | None = None,
    ) -> bool:
        """Re-fetch one known record; append a ``withdrawn`` event if it is gone.

        Withdrawal is **not** a scrape (runbook *handle-a-withdrawn-record*): a
        Zenodo tombstone body carries no metadata, so a ``scraped`` event would
        replace the source block with nothing and blank the record. The caller
        supplies the identity it already holds — an adapter must never infer
        withdrawal from a record's absence from a listing it could not fetch.

        Returns ``True`` when a withdrawal was appended.
        """
        from harvest.events import last_event, withdraw

        client = self._ensure_client()
        result = client.get(f"{self.api}/{quote(str(source_id))}")
        if result.status_code not in (404, 410):
            return False
        try:
            payload = result.json() if result.text else {"status": result.status_code}
        except ValueError:
            payload = {"status": result.status_code}
        if not self.is_tombstone(payload):
            return False
        if last_event(identity, events_dir, event_type="withdrawn") is not None:
            return False  # already withdrawn; append-on-change
        withdraw(
            identity,
            source_system=self.source_name,
            note=self.tombstone_note(payload),
            events_dir=events_dir,
        )
        return True

    # -- harvest -----------------------------------------------------------
    def _ensure_client(self) -> HarvestClient:
        if self.client is not None:
            return self.client
        if self._client is None:
            self._client = HarvestClient(
                # Defaults to TRUE, and the documented opt-out must be an
                # explicit `respect_robots: false` in sources.yaml. Defaulting
                # to False meant that deleting the line — the obvious way to
                # restore the safe behaviour — silently ignored robots.txt
                # instead (compliance-05).
                respect_robots=self.config.get("respect_robots", True) is not False,
                min_interval=float(self.config.get("min_request_interval_seconds", 1.0)),
            )
            self._owns_client = True
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None
            self._owns_client = False

    def _listing_url(self, slug: str, size: int) -> str:
        return (
            f"{self.api}?communities={quote(slug)}"
            f"&size={size}&sort=mostrecent&all_versions=false"
        )

    def harvest(self, max_records: int = DEFAULT_MAX_RECORDS) -> Iterable[RawObservation]:
        """Yield at most ``max_records`` records across the configured communities.

        Only the latest version of each concept is listed (``all_versions=false``)
        — that is the record whose metadata the concept identity displays.
        A community that cannot be fetched is logged and skipped; the source is
        only reported unreachable when **no** community could be read at all,
        so one dead community never costs the other eight.
        """
        client = self._ensure_client()
        seen_ids: set[str] = set()
        failures: list[str] = []
        reached = False
        yielded = 0

        for community in self.communities():
            if yielded >= max_records:
                break
            slug = str(community.get("slug") or "").strip()
            if not slug:
                continue
            url = self._listing_url(slug, min(max_records, MAX_PAGE_SIZE))
            result = client.get(url)
            if not result.ok:
                failures.append(f"{slug}: {result.error or f'HTTP {result.status_code}'}")
                log.warning("zenodo community %s unreachable: %s", slug, failures[-1])
                continue
            if result.status_code == 304 or not result.text:
                reached = True
                continue
            try:
                hits = result.json()["hits"]["hits"]
            except (ValueError, KeyError, TypeError) as exc:
                failures.append(f"{slug}: unexpected response shape ({exc})")
                log.warning("zenodo community %s returned an unreadable body: %s", slug, exc)
                continue
            reached = True
            for observation in self._observations(hits, seen_ids):
                yield observation
                yielded += 1
                if yielded >= max_records:
                    break

        if not reached:
            raise SourceUnreachable(
                "; ".join(failures) or "no Zenodo community listing could be fetched"
            )

    def _observations(
        self, hits: Iterable[dict[str, Any]], seen_ids: set[str]
    ) -> Iterator[RawObservation]:
        for hit in hits:
            if not isinstance(hit, dict) or hit.get("id") is None:
                continue
            source_id = str(hit["id"])
            if source_id in seen_ids:
                continue          # zen-11: two communities, one record, one event
            seen_ids.add(source_id)
            if self.is_tombstone(hit):
                log.info("zenodo record %s is tombstoned; not scraping it", source_id)
                continue          # zen-12: withdrawal is an event, never a scrape
            yield RawObservation(
                source_system=self.source_name,
                source_id=source_id,
                source_key=self.source_key_for(hit),
                url=(hit.get("links") or {}).get("self_html"),
                payload=hit,      # VERBATIM
            )

    # -- map ---------------------------------------------------------------
    def map(self, raw: RawObservation) -> MappedObservation:
        """Interpret one Zenodo payload. Pure: no network, no clock, no filesystem."""
        payload = raw.payload
        metadata = payload.get("metadata") or {}
        withdrawn = self.is_tombstone(payload)

        version_doi = normalise_doi(payload.get("doi") or metadata.get("doi"))
        concept_doi = normalise_doi(payload.get("conceptdoi"))
        doi = concept_doi or version_doi

        landing = (payload.get("links") or {}).get("self_html") or raw.url
        title = metadata.get("title") or payload.get("title")

        license_raw = _license_raw(metadata)
        license_id, _ = map_license(license_raw)

        access_status = (
            "metadata-only" if withdrawn else access_status_for(metadata.get("access_right"))
        )

        declared_tasks = self.community_tasks()
        tasks: list[str] = []
        for community in metadata.get("communities") or []:
            slug = community.get("id") if isinstance(community, dict) else community
            for task in tasks_for_community(str(slug or ""), declared_tasks):
                if task not in tasks:
                    tasks.append(task)

        resources: list[dict[str, Any]] = []
        for entry in payload.get("files") or []:
            if not isinstance(entry, dict) or not entry.get("key") or not landing:
                continue
            key = str(entry["key"])
            resource = {
                # The key is display text and may contain spaces; the URL may not.
                "url": f"{landing}/files/{quote(key, safe='/')}",
                "name": key,
            }
            file_format = _file_format(entry)
            if file_format:
                resource["format"] = file_format
            resources.append(resource)

        extra: dict[str, Any] = {}
        if payload.get("id") is not None:
            extra["zenodo_record_id"] = payload["id"]
        if payload.get("conceptrecid") is not None:
            extra["zenodo_concept_recid"] = payload["conceptrecid"]
        if version_doi:
            extra["zenodo_version_doi"] = version_doi
        repository = (metadata.get("custom") or {}).get("code:codeRepository")
        if repository:
            # zen-04: the free join key to the GitHub record. The dedup track
            # merges on this rather than guessing from the title.
            extra["zenodo_code_repository"] = str(repository)

        source = SourceNamespace(
            title=title,
            notes=sanitize_html(metadata.get("description")) or None,
            doi=doi,
            url=landing,
            source_urls=[landing] if landing else [],
            authors=_authors(metadata.get("creators")),
            publisher=metadata.get("imprint_publisher") or "Zenodo",
            published_date=metadata.get("publication_date"),
            version=metadata.get("version"),
            license_raw=license_raw,
            license_id=license_id,
            resource_kind=resource_kind_for(metadata.get("resource_type")),
            access_status=access_status,
            embargo_date=metadata.get("embargo_date"),
            container=_container(metadata),
            keywords=[str(word) for word in metadata.get("keywords") or []],
            resources=resources,
            related_identifiers=_related_identifiers(metadata, concept_doi, version_doi),
            iea_task=tasks,
            withdrawn=withdrawn,
            extra=extra,
        )

        provenance: dict[str, FieldProvenance] = {
            "license_id": FieldProvenance(extraction_method="pattern"),
        }
        for field in ("title", "notes", "doi", "authors", "resource_kind"):
            if getattr(source, field):
                provenance[field] = FieldProvenance(extraction_method="api")
        if tasks:
            # Derived from the community slug, not stated as a task by Zenodo.
            provenance["iea_task"] = FieldProvenance(extraction_method="pattern")

        return MappedObservation(
            identity_key=identity_key(
                doi=doi, source_system=self.source_name, source_id=raw.source_id
            ),
            source_system=self.source_name,
            source_id=raw.source_id,
            source_key=raw.source_key,
            source=source,
            provenance=provenance,
            fetched_at=raw.fetched_at,   # purity: "now" is whatever harvest() stamped
        )
