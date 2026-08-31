"""The data model: raw observations, events, and the canonical CKAN record.

Three shapes matter, and they are strictly ordered:

    RawObservation  --map()-->  Event(source=..., local=...)  --replay()-->  CKAN package dict
       adapter                       events/<slug>.jsonl                      records/<slug>.json
     (verbatim)                     (source of truth)                          (derived)

**ADR-0038 — two namespaces, and the rules that govern them.**

``source.*``
    Verbatim upstream metadata, mapped into the field names below but never
    edited in content. Replaced **wholesale** every time the source key
    changes. There is no field-level merge of successive scrapes: the latest
    scrape is what the source says, and what the source says is the truth
    this catalogue reports.

``local.*``
    Additive annotation only — task attribution, curator notes, cross-links,
    suppression, Tier-3 pins. **Latest local event wins, per field.** Local
    never edits a source value; it can only supply one the source lacks.

Collision, when the source later starts providing a field a curator had added:
    * **scalars** — the source value displaces the local one. The displaced
      value stays in the event log forever, and a ``displacement_notice``
      event is appended so the run report can surface it (fixture ``x-03``).
    * **set-valued fields**, ``iea_task`` above all — **union**, never
      displace. Zenodo adding a Task 43 community must not erase a
      hand-attributed Task 49 (fixture ``x-04``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "EventType",
    "ExtractionMethod",
    "SET_VALUED_FIELDS",
    "RESOURCE_KINDS",
    "ACCESS_STATUSES",
    "LIFECYCLE_STATES",
    "CKAN_STATES",
    "utcnow",
    "json_extra",
    "FieldProvenance",
    "Author",
    "SourceNamespace",
    "LocalNamespace",
    "RawObservation",
    "MappedObservation",
    "Event",
    "CkanTag",
    "CkanExtra",
    "CkanResource",
    "CkanPackage",
    "ResolvedRecord",
]

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

EventType = Literal[
    "scraped",              # upstream metadata observed; source.* replaced wholesale
    "annotated",            # local addition by a curator or a reconciler
    "withdrawn",            # the artifact vanished upstream; the record is retained
    "displacement_notice",  # a source value displaced a local scalar (§4.2)
    "pin_notice",           # a pinned Tier-3 extraction held while the page moved (§4.3)
]

ExtractionMethod = Literal["api", "pattern", "llm"]

#: Fields whose values are sets. Collisions on these UNION rather than displace.
#: ``iea_task`` is the one ADR-0038 names explicitly; the rest follow the same
#: logic (an artifact reached from four sources has four source URLs, x-01).
SET_VALUED_FIELDS: frozenset[str] = frozenset(
    {"iea_task", "source_urls", "keywords", "related_identifiers", "curator_notes", "links"}
)

RESOURCE_KINDS = ("dataset", "publication", "software", "report", "model", "other")

ACCESS_STATUSES = (
    "open",
    "restricted",
    "embargoed",
    "registration-required",
    "metadata-only",
    "unknown",
)

#: How the record's own lifecycle is expressed. See ``CkanPackage.state`` for
#: why withdrawal is NOT carried in CKAN's ``state`` field.
LIFECYCLE_STATES = ("active", "archived", "withdrawn")

CKAN_STATES = ("active", "deleted", "draft")


def utcnow() -> str:
    """Second-precision UTC timestamp, ``Z``-suffixed. Used for ``observed_at``."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_extra(value: Any) -> str:
    """Encode a list/dict as the compact, deterministic string an extra holds.

    CKAN extras are string-valued. Structured custom fields (``iea_task``,
    ``source_urls``, ``provenance``, ...) are therefore carried as JSON *inside*
    a string. Sorted keys and fixed separators keep ``records/*.json``
    byte-stable across runs.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class FieldProvenance(BaseModel):
    """How one field's value came to be known.

    ``api``     — read from a structured API response. Deterministic.
    ``pattern`` — regex or rule over text (a DOI sweep, a Zenodo badge).
    ``llm``     — inferred by a model. ``model``, ``prompt_version`` and
                  ``confidence`` are **required** in that case, and the site
                  renders a visible "machine-inferred" badge (ADR-0028,
                  fixture ``x-05``).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    extraction_method: ExtractionMethod
    model: str | None = None
    prompt_version: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_system: str | None = None
    #: Optional-and-None-by-default so that an ordinary field's provenance
    #: serialises as exactly ``{"extraction_method": "api"}`` — the encoded
    #: provenance blob is on every record and terseness is worth having.
    pinned: bool | None = None

    @field_validator("extraction_method")
    @classmethod
    def _known_method(cls, value: str) -> str:
        return value

    def model_post_init(self, _context: Any) -> None:
        if self.extraction_method == "llm" and not (self.model and self.prompt_version):
            raise ValueError(
                "llm-extracted fields must record model and prompt_version "
                "(ADR-0028); confidence is strongly expected too"
            )


class Author(BaseModel):
    """A creator. ``name`` is the only required part — Crossref emits
    collaboration entries with no given name at all (fixture ``cr-06``)."""

    model_config = ConfigDict(extra="allow")

    name: str
    given: str | None = None
    family: str | None = None
    orcid: str | None = None
    affiliation: str | None = None


# ---------------------------------------------------------------------------
# The two namespaces
# ---------------------------------------------------------------------------


class SourceNamespace(BaseModel):
    """Upstream metadata, mapped to catalogue field names, otherwise verbatim.

    Adapters produce this. **Every field is optional** — the mapping must
    degrade rather than fail. Unknown upstream fields go in ``extra`` so
    nothing is lost, and ``extra`` is never rendered without a curator opting
    it in.

    Replaced wholesale on a source-key change. Never edited locally.
    """

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    notes: str | None = None            # description; HTML-sanitised before storage
    doi: str | None = None              # normalised, and RESOLVED, before it lands here
    url: str | None = None              # canonical landing page
    source_urls: list[str] = Field(default_factory=list)
    authors: list[Author] = Field(default_factory=list)
    publisher: str | None = None
    published_date: str | None = None   # ISO 8601; may be year-only (fixture cr-02)
    version: str | None = None
    license_raw: str | None = None      # exactly what the source said
    license_id: str | None = None       # mapped through harvest.licenses
    resource_kind: str | None = None
    access_status: str | None = None
    embargo_date: str | None = None
    container: str | None = None        # journal / series / community title
    keywords: list[str] = Field(default_factory=list)
    resources: list[dict] = Field(default_factory=list)  # links only, never mirrors
    related_identifiers: list[dict] = Field(default_factory=list)
    iea_task: list[str] = Field(default_factory=list)    # when the source states it
    withdrawn: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class LocalNamespace(BaseModel):
    """Additions the sources do not provide. Additive only, latest-wins.

    A curator may add any key here; the declared ones below are the ones the
    site and the reconciler understand.
    """

    model_config = ConfigDict(extra="allow")

    iea_task: list[str] = Field(default_factory=list)   # SET-VALUED: unions, never displaces
    resource_kind: str | None = None
    access_status: str | None = None
    curator_notes: list[dict] = Field(default_factory=list)  # [{field?, note, added_at?}]
    links: list[dict] = Field(default_factory=list)          # [{url, label}]
    source_urls: list[str] = Field(default_factory=list)
    suppressed: bool = False            # noise; kept but not listed
    pinned: bool = False                # Tier-3 pinned extraction (§4.3)
    pin_source_key: str | None = None   # the content hash the pin was made against


# ---------------------------------------------------------------------------
# Adapter I/O
# ---------------------------------------------------------------------------


class RawObservation(BaseModel):
    """One artifact as an adapter found it, before any interpretation.

    ``payload`` is the upstream response **verbatim**. It is what
    ``fixtures/<source>/raw/<id>.json`` holds, and what ``map()`` is tested
    against offline.
    """

    model_config = ConfigDict(extra="forbid")

    source_system: str          # must equal the adapter's ``source_name``
    source_id: str              # the upstream's own stable id, as a string
    source_key: str             # the change token (plan §4.1); adapter owns its semantics
    fetched_at: str = Field(default_factory=utcnow)
    url: str | None = None      # landing page, if the adapter knows it cheaply
    payload: dict[str, Any] = Field(default_factory=dict)


class MappedObservation(BaseModel):
    """The result of ``Adapter.map()``: an identity, a source namespace, provenance."""

    model_config = ConfigDict(extra="forbid")

    identity_key: str
    source_system: str
    source_id: str
    source_key: str
    source: SourceNamespace
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    fetched_at: str = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# The event log
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """One line of ``events/<slug>.jsonl``.

    Append-only, append-on-change, ordered by *our* observation time. A scrape
    whose source key is unchanged writes **nothing** (ADR-0026) — the run is
    still recorded, in ``state/last-run.json``.
    """

    model_config = ConfigDict(extra="forbid")

    observed_at: str = Field(default_factory=utcnow)
    event_type: EventType
    identity_key: str
    source_key: str | None = None
    source_system: str | None = None
    source_id: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    local: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    notice: dict[str, Any] | None = None   # displacement_notice / pin_notice payload
    actor: str | None = None               # "harvest/zenodo", "curator:tom", "reconcile"
    note: str | None = None

    def to_jsonl(self) -> str:
        """Serialise to one deterministic JSONL line (no trailing newline)."""
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> "Event":
        return cls.model_validate(json.loads(line))


# ---------------------------------------------------------------------------
# The canonical record: a CKAN package dict
# ---------------------------------------------------------------------------


class CkanTag(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str


class CkanExtra(BaseModel):
    """CKAN extras are **string-valued**. Structured data is JSON-in-a-string."""

    model_config = ConfigDict(extra="forbid")
    key: str
    value: str

    @field_validator("value", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            return json_extra(value)
        return str(value)


class CkanResource(BaseModel):
    """A link to something upstream. The catalogue never mirrors files."""

    model_config = ConfigDict(extra="allow")
    url: str
    name: str | None = None
    description: str | None = None
    format: str | None = None


class CkanPackage(BaseModel):
    """The canonical record. ``records/*.json`` must be POSTable, unmodified,
    to CKAN ``package_create``.

    **On ``state``.** CKAN's ``state`` is its own row lifecycle: ``deleted``
    means "hidden and purgeable", which is exactly what ADR-0027 forbids for a
    withdrawn record. So ``state`` stays ``active`` for every record the
    catalogue retains, and withdrawal is carried in the extras
    ``lifecycle_state`` (``active`` | ``archived`` | ``withdrawn``),
    ``withdrawn`` and ``withdrawn_at``. The site renders the withdrawal banner
    from those; CKAN, on promotion day, still ingests the record.
    """

    model_config = ConfigDict(extra="allow")

    name: str                                   # the slug; = file stem = URL segment
    title: str
    notes: str = ""
    license_id: str = "notspecified"
    tags: list[CkanTag] = Field(default_factory=list)
    extras: list[CkanExtra] = Field(default_factory=list)
    resources: list[CkanResource] = Field(default_factory=list)
    owner_org: str | None = None
    groups: list[dict] = Field(default_factory=list)   # [{"name": "task-43"}]
    url: str | None = None
    version: str | None = None
    state: str = "active"
    private: bool = False

    def extras_map(self) -> dict[str, str]:
        return {extra.key: extra.value for extra in self.extras}

    def to_json_dict(self) -> dict[str, Any]:
        """The exact dict written to ``records/<name>.json``."""
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["extras"] = sorted(payload.get("extras", []), key=lambda e: e["key"])
        payload["tags"] = sorted(payload.get("tags", []), key=lambda t: t["name"])
        payload["groups"] = sorted(payload.get("groups", []), key=lambda g: g.get("name", ""))
        return payload


class ResolvedRecord(BaseModel):
    """The intermediate state ``harvest.events.resolve()`` produces.

    Kept separate from the CKAN package so that the resolution logic is
    testable without the CKAN shaping, and so the record page can render the
    two namespaces side by side.
    """

    model_config = ConfigDict(extra="forbid")

    identity_key: str
    slug: str
    source: dict[str, Any] = Field(default_factory=dict)
    local: dict[str, Any] = Field(default_factory=dict)
    effective: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, FieldProvenance] = Field(default_factory=dict)
    source_key: str | None = None
    source_system: str | None = None
    source_id: str | None = None
    source_systems: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    withdrawn: bool = False
    withdrawn_at: str | None = None
    notices: list[dict] = Field(default_factory=list)
    event_count: int = 0
