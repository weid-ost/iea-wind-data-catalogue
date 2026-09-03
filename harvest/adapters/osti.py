"""OSTI adapter — **owner: Track E (osti)**.

Source
    OSTI's public v1 API, ``https://www.osti.gov/api/v1/records`` for the
    listing and ``https://www.osti.gov/api/v1/records/<osti_id>`` for one
    record. Both return a **bare JSON array** of flat record objects; the
    listing carries ``X-Total-Count`` and RFC 5988 ``Link`` headers for
    pagination. Verified live 2026-08-31: no key, no token, no auth; ``/api/``
    is not disallowed by ``https://www.osti.gov/robots.txt``. The v2 API
    (``/api/v2/records``) answers **403** to an anonymous client and is
    therefore not used.

Source key (plan §4.1, ADR-0026)
    ``entry_date`` — OSTI's metadata entry/update timestamp, present on every
    record observed. It moves when OSTI revises a record's metadata (2022
    publications carry 2026 entry dates), which is exactly the semantics
    ADR-0026 asks for. Where it is absent the adapter falls back to
    :func:`~harvest.adapters.base.payload_hash` over a curated subset of
    meaning-bearing fields — never over the whole payload, which would hash
    ``entry_date`` itself and turn append-on-change into append-always.

Identity
    The DOI when OSTI states one **and it resolves** (see below), otherwise
    ``osti|<osti_id>`` (fixture ``osti-02``).

Resolve-or-drop
    OSTI states DOIs minted by three different kinds of agency: its own
    (``10.2172`` reports, ``10.21947`` datasets, ``10.11578`` DOE CODE),
    and publishers' (Copernicus, IOP, Elsevier, IEEE, …). Every one of them is
    resolved against DataCite then Crossref in :meth:`OstiAdapter.harvest`
    before the observation is yielded. A DOI that does not resolve causes the
    **record to be dropped and logged** in :attr:`OstiAdapter.drop_log` — never
    silently accepted, never silently discarded. Resolution is network work and
    therefore lives in ``harvest()``; :meth:`OstiAdapter.map` stays pure.

Fixtures owned
    ``osti-01`` .. ``osti-05``, all captured verbatim from the live API.

Watch for
    * **Mandated duplicates** (``osti-03``). DOE deposit is mandated, so an
      OSTI record frequently *is* a journal article that also lives at
      Crossref, or a dataset that also lives on Zenodo. That is a **merge**,
      not a second record: the identity is the publisher's DOI, so the events
      land on the same slug, and OSTI contributes an extra ``source_url``. The
      signals the dedup track needs are emitted explicitly —
      ``source.extra.osti_mandated_deposit``, ``source.extra.osti_doi_registrant``
      and an ``IsVariantFormOf`` entry in ``related_identifiers`` pointing at
      the OSTI landing page.
    * **Metadata-only entries** (``osti-04``). A record without a
      ``rel: fulltext`` link has no downloadable full text at OSTI. Its
      ``access_status`` is ``metadata-only`` and it gets **no resources at
      all** — the record page must not imply a download that does not exist.
    * **The report number** (``osti-05``). For the large population of OSTI
      records with no DOI, ``NREL/TP-5000-89937`` is the only stable
      human-facing identifier there is, and it is what a reader cites. It is
      carried as a first-class ``report_number`` extra (and mirrored into
      ``related_identifiers``), not buried in ``source.extra``.
    * OSTI crams several keywords into one ``subjects`` entry separated by
      commas (``"POWER TRANSMISSION AND DISTRIBUTION,WIND ENERGY"``). Splitting
      that is parsing a delimited field, not editing the source.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable
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
from harvest.sanitize import sanitize_html

__all__ = [
    "OSTI_API",
    "OSTI_BIBLIO",
    "OSTI_DOI_PREFIXES",
    "PRODUCT_TYPES",
    "TASK_GROUPS",
    "OstiAdapter",
]

log = logging.getLogger(__name__)

#: The public v1 listing endpoint. Overridable from ``sources.yaml``.
OSTI_API = "https://www.osti.gov/api/v1/records"

#: OSTI's own landing-page prefix, used only as a fallback when the payload
#: carries no ``rel: citation`` link. Not a guess: it is the URL OSTI itself
#: puts in that link for every record observed.
OSTI_BIBLIO = "https://www.osti.gov/biblio/"

#: DOI prefixes OSTI itself registers. Anything else means the artifact's DOI
#: belongs to another agency and this OSTI record is a mandated deposit.
OSTI_DOI_PREFIXES: frozenset[str] = frozenset({"10.2172", "10.21947", "10.11578"})

#: ``product_type`` -> the catalogue's ``resource_kind`` vocabulary.
PRODUCT_TYPES: dict[str, str] = {
    "technical report": "report",
    "program document": "report",
    "report": "report",
    "journal article": "publication",
    "conference": "publication",
    "book": "publication",
    "thesis/dissertation": "publication",
    "dataset": "dataset",
    "software": "software",
    "patent": "other",
    "miscellaneous": "other",
}

#: IEA Wind task number -> the canonical ``groups.yaml`` group name.
#:
#: Kept as a literal so that :meth:`OstiAdapter.map` stays pure — it reads no
#: file and no clock. ``tests/test_osti.py`` asserts every value here exists in
#: ``groups.yaml``, the same way ``EXTRA_KEYS`` and ``schema/ckan-scheming.json``
#: are kept in step. The renumberings (19 -> 54, 34 -> 59, 63 -> 25) are
#: resolved here so a record citing the old number lands in the right group.
TASK_GROUPS: dict[int, str] = {
    11: "task-11", 19: "task-54", 25: "task-25", 26: "task-26", 27: "task-27",
    28: "task-28", 29: "task-29", 30: "task-30", 31: "task-31", 32: "task-32",
    33: "task-33", 34: "task-59", 35: "task-35", 36: "task-36", 37: "task-37",
    38: "task-38", 39: "task-39", 40: "task-40", 41: "task-41", 42: "task-42",
    43: "task-43", 44: "task-44", 45: "task-45", 46: "task-46", 47: "task-47",
    48: "task-48", 49: "task-49", 50: "task-50", 51: "task-51", 52: "task-52",
    53: "task-53", 54: "task-54", 55: "task-55", 56: "task-56", 57: "task-57",
    58: "task-58", 59: "task-59", 60: "task-60", 61: "task-61", 62: "task-62",
    63: "task-25", 65: "task-65",
}

# "IEA Wind Task 49", "IEA-Wind TCP Task 49", "(IEA) Wind Task 30",
# "IEA Wind TCT Task 25", "IEA Wind Task 25/63". Between "Wind" and "Task" only
# whole words are allowed, and never a number, so "IEA Wind 15 MW Reference
# Wind Turbine" cannot be read as a task attribution.
_TASK_RE = re.compile(
    r"IEA[\s\-‐-―)]*Wind\b"
    r"(?:\s+[A-Za-z]+){0,3}?"
    r"\s+Tasks?\s*(\d{1,2})"
    r"(?:\s*[/&,]\s*(\d{1,2}))?",
    re.IGNORECASE,
)

_ORCID_RE = re.compile(r"\(\s*ORCID\s*:\s*([0-9Xx\-]{16,25})\s*\)")
_AFFILIATION_RE = re.compile(r"\[([^\[\]]*)\]")
_ISO_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_WHITESPACE_RE = re.compile(r"\s+")

# OSTI packs two subject categories into one ``subjects`` entry with a comma and
# **no space** ("ENGINEERING,WIND ENERGY"), while a single category may itself
# contain commas *with* spaces ("29 ENERGY PLANNING, POLICY, AND ECONOMY").
# Splitting on the space-less comma alone separates the first without shredding
# the second.
_PACKED_SUBJECT_RE = re.compile(r",(?!\s)")

#: The fields hashed by the source-key fallback. Deliberately excludes
#: ``entry_date`` and everything else that churns without a content change.
_HASH_FIELDS = (
    "title", "description", "doi", "publication_date", "product_type",
    "report_number", "authors", "subjects", "journal_name", "journal_volume",
    "journal_issue", "publisher", "links",
)


def _text(value: Any) -> str | None:
    """A stripped string, or ``None`` when the source said nothing."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _osti_id(payload: dict[str, Any], fallback: str = "") -> str:
    return str(payload.get("osti_id") or fallback).strip()


def _links(payload: dict[str, Any]) -> list[dict[str, Any]]:
    links = payload.get("links")
    return [link for link in links if isinstance(link, dict)] if isinstance(links, list) else []


def _link_href(payload: dict[str, Any], rel: str) -> str | None:
    for link in _links(payload):
        if str(link.get("rel", "")).strip().lower() == rel and link.get("href"):
            return str(link["href"]).strip()
    return None


def _citation_urls(payload: dict[str, Any]) -> list[str]:
    """Every "view at source" URL, citation first, in payload order."""
    urls: list[str] = []
    for link in _links(payload):
        rel = str(link.get("rel", "")).strip().lower()
        href = _text(link.get("href"))
        if href and rel.startswith("citation") and href not in urls:
            urls.append(href)
    return urls


def _format_orcid(raw: str) -> str:
    """``0000000203988320`` -> ``0000-0002-0398-8320``; anything else verbatim.

    The same identifier in its canonical spelling — the ORCID equivalent of
    :func:`harvest.doi.normalise_doi`, not an edit to what OSTI said.
    """
    digits = raw.replace("-", "").strip().upper()
    if len(digits) == 16 and re.fullmatch(r"[0-9]{15}[0-9X]", digits):
        return "-".join(digits[index : index + 4] for index in range(0, 16, 4))
    return raw.strip()


def _parse_author(entry: Any) -> Author | None:
    """``"Hall, Matthew [NREL, Golden, CO] (ORCID:0000...)"`` -> an Author.

    OSTI packs affiliation and ORCID into the creator string. Splitting them
    out is field-name mapping; the name itself is left exactly as written.
    """
    if isinstance(entry, dict):  # defensive: a future API shape
        name = _text(entry.get("name") or entry.get("full_name"))
        if not name:
            return None
        return Author(
            name=name,
            orcid=_format_orcid(str(entry["orcid"])) if entry.get("orcid") else None,
            affiliation=_text(entry.get("affiliation")),
        )

    text = _text(entry)
    if not text:
        return None

    orcid: str | None = None
    match = _ORCID_RE.search(text)
    if match:
        orcid = _format_orcid(match.group(1))
        text = text[: match.start()] + text[match.end() :]

    affiliation: str | None = None
    match = _AFFILIATION_RE.search(text)
    if match:
        affiliation = _text(match.group(1))
        text = text[: match.start()] + text[match.end() :]

    name = _WHITESPACE_RE.sub(" ", text).strip().strip(",").strip()
    if not name:
        return None
    return Author(name=name, orcid=orcid, affiliation=affiliation)


def _keywords(payload: dict[str, Any]) -> list[str]:
    """``subjects``, with OSTI's comma-packed entries split out."""
    keywords: list[str] = []
    subjects = payload.get("subjects")
    if not isinstance(subjects, list):
        return keywords
    for subject in subjects:
        for part in _PACKED_SUBJECT_RE.split(str(subject or "")):
            keyword = _WHITESPACE_RE.sub(" ", part).strip()
            if keyword and keyword not in keywords:
                keywords.append(keyword)
    return keywords


def _published_date(payload: dict[str, Any]) -> str | None:
    """The date part of ``publication_date``. Never fabricates a month."""
    raw = _text(payload.get("publication_date"))
    if not raw:
        return None
    candidate = raw.split("T", 1)[0].strip()
    return candidate if _ISO_DATE_RE.match(candidate) else raw


def iea_tasks(*texts: str | None) -> list[str]:
    """Task group names stated in the given text, in order of first mention.

    Pure and deterministic: a regex over what the source wrote, resolved
    through :data:`TASK_GROUPS`. A task number the catalogue does not know is
    **ignored**, because ``groups[].name`` must exist in ``groups.yaml`` or the
    CKAN gate fails — an unknown number is a curator's job, not a guess.
    """
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in _TASK_RE.finditer(str(text)):
            for number in match.groups():
                if number is None:
                    continue
                group = TASK_GROUPS.get(int(number))
                if group and group not in found:
                    found.append(group)
    return found


@register
class OstiAdapter(Adapter):
    """DOE's Office of Scientific and Technical Information."""

    source_name = "osti"
    tier = 1
    source_key_semantics = "entry_date (OSTI metadata update stamp), else payload hash"

    def __init__(self, config: Any = None, client: Any = None) -> None:
        super().__init__(config, client)
        #: Every DOI dropped this run, for the run report. Never silent.
        self.drop_log = DoiDropLog()
        self._own_client: HarvestClient | None = None

    # -- talking to the source ---------------------------------------------
    def _http(self) -> HarvestClient:
        if self.client is not None:
            return self.client
        if self._own_client is None:
            self._own_client = HarvestClient(
                min_interval=float(self.config.get("min_request_interval_seconds", 0.2) or 0.2)
            )
        return self._own_client

    def close(self) -> None:
        if self._own_client is not None:
            self._own_client.close()
            self._own_client = None

    def _query_urls(self) -> list[str]:
        api = str(self.config.get("api") or OSTI_API)
        rows = int(self.config.get("rows", 25) or 25)
        queries = self.config.get("queries") or ['"IEA Wind"']
        return [f"{api}?{urlencode({'q': str(query), 'rows': rows})}" for query in queries]

    @staticmethod
    def _records(body: Any) -> list[dict[str, Any]]:
        """Tolerate the v1 bare array and a future envelope alike."""
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if isinstance(body, dict):
            for key in ("records", "data", "results", "docs"):
                value = body.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def source_key(self, payload: dict[str, Any]) -> str:
        """``entry_date`` if OSTI provided one, else the curated payload hash."""
        entry_date = _text(payload.get("entry_date"))
        if entry_date:
            return entry_date
        return payload_hash({field: payload.get(field) for field in _HASH_FIELDS})

    def harvest(self, max_records: int = DEFAULT_MAX_RECORDS) -> Iterable[RawObservation]:
        client = self._http()
        seen: set[str] = set()
        yielded = 0

        for url in self._query_urls():
            if yielded >= max_records:
                return
            result = client.get(url)
            if not result.ok:
                raise SourceUnreachable(
                    result.error or f"HTTP {result.status_code} from {url}"
                )
            try:
                body = result.json()
            except Exception as exc:
                raise SourceUnreachable(f"OSTI returned a body that is not JSON: {exc}") from exc

            for payload in self._records(body):
                if yielded >= max_records:
                    return
                osti_id = _osti_id(payload)
                if not osti_id or osti_id in seen:
                    continue
                seen.add(osti_id)

                # Resolve-or-drop. Network work, so it happens here and never
                # in map(). An unresolved DOI loses the record, loudly.
                stated = payload.get("doi")
                if stated:
                    resolution = resolve_or_drop(
                        str(stated), client, self.drop_log, context=f"osti:{osti_id}"
                    )
                    if resolution is None:
                        log.warning(
                            "dropping OSTI record %s: stated DOI %r did not resolve",
                            osti_id, stated,
                        )
                        continue

                yield RawObservation(
                    source_system=self.source_name,
                    source_id=osti_id,
                    source_key=self.source_key(payload),
                    url=_link_href(payload, "citation") or f"{OSTI_BIBLIO}{osti_id}",
                    payload=payload,  # VERBATIM
                )
                yielded += 1

    # -- interpreting one payload (pure) ------------------------------------
    def map(self, raw: RawObservation) -> MappedObservation:
        payload = raw.payload
        osti_id = _osti_id(payload, raw.source_id)
        doi = normalise_doi(payload.get("doi"))

        source_urls = _citation_urls(payload)
        landing = source_urls[0] if source_urls else (raw.url or f"{OSTI_BIBLIO}{osti_id}")
        if landing not in source_urls:
            source_urls.insert(0, landing)

        product_type = _text(payload.get("product_type"))
        resource_kind = PRODUCT_TYPES.get((product_type or "").lower(), "other")

        # Availability. No fulltext link means OSTI holds metadata only, and
        # the record must not offer a download it does not have (osti-04).
        fulltext = _link_href(payload, "fulltext")
        access_status = "open" if fulltext else "metadata-only"
        resources = [{"url": fulltext, "name": "Full text at OSTI"}] if fulltext else []

        report_number = _text(payload.get("report_number"))
        license_raw = _text(payload.get("rights"))
        license_id, _mapped = map_license(license_raw)

        related: list[dict[str, str]] = [
            {"relation": "IsIdenticalTo", "identifier": osti_id, "identifier_type": "OSTI"}
        ]
        if report_number:
            related.append(
                {
                    "relation": "IsIdenticalTo",
                    "identifier": report_number,
                    "identifier_type": "Report-Number",
                }
            )

        extra: dict[str, Any] = {
            "osti_id": osti_id,
            "osti_product_type": product_type,
            "osti_entry_date": _text(payload.get("entry_date")),
        }
        if doi:
            registrant = "osti" if doi.split("/", 1)[0] in OSTI_DOI_PREFIXES else "external"
            extra["osti_doi_registrant"] = registrant
            if registrant == "external":
                # The mandated-deposit signal the dedup track reads (osti-03).
                # The identity is already the publisher's DOI, so the events
                # merge onto one slug; this says so out loud.
                extra["osti_mandated_deposit"] = True
                related.append(
                    {
                        "relation": "IsVariantFormOf",
                        "identifier": f"{OSTI_BIBLIO}{osti_id}",
                        "identifier_type": "URL",
                    }
                )
        for key, target in (
            ("report_number", "osti_report_number"),
            ("doe_contract_number", "osti_doe_contract_number"),
            ("conference_info", "osti_conference_info"),
            ("availability", "osti_availability"),
            ("country_publication", "osti_country_publication"),
            ("language", "osti_language"),
            ("journal_name", "osti_journal_name"),
            ("journal_volume", "osti_journal_volume"),
            ("journal_issue", "osti_journal_issue"),
            ("journal_issn", "osti_journal_issn"),
            ("article_type", "osti_article_type"),
        ):
            value = _text(payload.get(key))
            if value:
                extra[target] = value
        for key, target in (
            ("research_orgs", "osti_research_orgs"),
            ("sponsor_orgs", "osti_sponsor_orgs"),
        ):
            value = payload.get(key)
            if isinstance(value, list) and value:
                extra[target] = [str(item) for item in value]

        authors = [author for author in (_parse_author(a) for a in payload.get("authors") or [])
                   if author is not None]

        namespace = SourceNamespace(
            title=_text(payload.get("title")),
            notes=sanitize_html(payload.get("description")),
            doi=doi,
            url=landing,
            source_urls=source_urls,
            authors=authors,
            publisher=_text(payload.get("publisher")),
            published_date=_published_date(payload),
            license_raw=license_raw,
            license_id=license_id,
            resource_kind=resource_kind,
            access_status=access_status,
            container=_text(payload.get("journal_name")),
            keywords=_keywords(payload),
            resources=resources,
            related_identifiers=related,
            iea_task=iea_tasks(payload.get("title"), payload.get("description")),
            withdrawn=False,
            extra=extra,
            # First-class, because for a record with no DOI it is the only
            # stable human-facing identifier a reader can cite (osti-05).
            report_number=report_number,
        )

        provenance = {
            "title": FieldProvenance(extraction_method="api"),
            "notes": FieldProvenance(extraction_method="api"),
            "authors": FieldProvenance(extraction_method="api"),
            "published_date": FieldProvenance(extraction_method="api"),
            "resource_kind": FieldProvenance(extraction_method="api"),
            "access_status": FieldProvenance(extraction_method="api"),
            "keywords": FieldProvenance(extraction_method="api"),
            "related_identifiers": FieldProvenance(extraction_method="api"),
            "license_id": FieldProvenance(extraction_method="pattern"),
        }
        if doi:
            provenance["doi"] = FieldProvenance(extraction_method="api")
        if report_number:
            provenance["report_number"] = FieldProvenance(extraction_method="api")
        if namespace.iea_task:
            provenance["iea_task"] = FieldProvenance(extraction_method="pattern")

        return MappedObservation(
            identity_key=identity_key(
                doi=doi, source_system=self.source_name, source_id=osti_id
            ),
            source_system=self.source_name,
            source_id=osti_id,
            source_key=raw.source_key,
            source=namespace,
            provenance=provenance,
            fetched_at=raw.fetched_at,
        )
