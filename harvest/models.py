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
import re
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from harvest.urls import (
    safe_links,
    safe_related_identifiers,
    safe_resources,
    safe_url,
    safe_urls,
)

__all__ = [
    "normalise_task",
    "sanitise_payload",
    "truncate_text",
    "MAX_TEXT_LENGTH",
    "MAX_COLLECTION_ITEMS",
    "TRUNCATION_MARKER",
    "LOCAL_CURATOR_FIELDS",
    "LOCAL_RECONCILER_FIELDS",
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
    "merge_proposal",       # the reconciler found a probable duplicate it will NOT merge
]

ExtractionMethod = Literal["api", "pattern", "llm", "curator"]

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


#: ``Task 43``, ``TASK-43``, `` task-43 `` all name one group. The value is
#: validated case- and whitespace-insensitively everywhere, so it must also be
#: *stored* in one spelling — otherwise one task becomes several chips on the
#: record page and several buckets in the task facet (eventlog-05). Alias
#: resolution (19 -> 54) stays in :func:`harvest.config.canonical_group`, which
#: needs the register; this is the register-free half and runs on every write.
_TASK_RE = re.compile(r"^task[\s_-]*0*(\d{1,3})$", re.IGNORECASE)


def normalise_task(value: Any) -> str:
    """Normalise one ``iea_task`` value to its canonical spelling.

    ``" Task 43 "`` -> ``"task-43"``. Anything that is not recognisably a task
    number is lowercased and whitespace-collapsed but otherwise left alone, so
    a name the catalogue does not know still reaches the CKAN gate, which is
    where an unknown group is meant to be caught.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    match = _TASK_RE.match(text)
    if match:
        return f"task-{int(match.group(1))}"
    return text.lower()


#: The longest a single text field may be in a record, and the most items a
#: collection may hold. Nothing in this catalogue enforced either, so one
#: pathological upstream description wrote a 10 MB ``events/*.jsonl`` line and
#: a 10 MB ``records/*.json`` file — both committed to git on every source-key
#: change, then rendered as a 10 MB HTML page and indexed by Pagefind
#: (scrape-07). 64 KiB is far more than any real abstract and far less than a
#: problem; 500 items is more keywords or files than any real deposit has.
MAX_TEXT_LENGTH = 64 * 1024
MAX_COLLECTION_ITEMS = 500

#: Appended where a value was cut, so the record page never implies the
#: description simply ended there. Truncation is a fact about the record and is
#: shown as one — the untruncated value stays in the event log.
TRUNCATION_MARKER = " […truncated]"


def truncate_text(value: Any, limit: int = MAX_TEXT_LENGTH) -> Any:
    """Cap one text field, marking the cut. Non-strings pass through."""
    if not isinstance(value, str) or len(value) <= limit:
        return value
    return value[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


#: Which sanitising rule applies to which key, for payloads that arrive as
#: plain dicts rather than as a validated namespace.
_TEXT_FIELDS = ("title", "notes", "publisher", "container", "license_raw")
_URL_FIELDS = ("url",)
_URL_LIST_FIELDS = ("source_urls",)
_LINK_LIST_FIELDS = ("resources", "links")
_TASK_FIELDS = ("iea_task",)
_CAPPED_LIST_FIELDS = ("keywords", "resources", "source_urls", "related_identifiers", "links")


def sanitise_payload(payload: Any) -> dict[str, Any]:
    """Apply the namespace safety rules to a raw ``source``/``local`` mapping.

    :func:`harvest.events.record_scrape` and :func:`harvest.events.annotate`
    accept plain dicts — that is what a runbook, a fixture and a hand-written
    event all produce — so the ``SourceNamespace`` field validators, which are
    where the URL-scheme allow-list, the length caps and the task
    normalisation live, simply never ran on the write path. The filters were
    only as strong as every caller remembering to build a model first.

    This applies the same rules to the keys that are actually present, and
    leaves absent keys absent: round-tripping through the model would stamp
    empty-list defaults into every event line and change the serialisation of
    the whole log.
    """
    if not isinstance(payload, Mapping):
        return dict(payload or {})
    out = dict(payload)
    for key in _TEXT_FIELDS:
        if key in out:
            out[key] = truncate_text(out[key])
    for key in _URL_FIELDS:
        if out.get(key):
            out[key] = safe_url(out[key])
    for key in _URL_LIST_FIELDS:
        if key in out:
            out[key] = safe_urls(out[key])
    for key in _LINK_LIST_FIELDS:
        if key in out:
            out[key] = safe_links(out[key])
    if "related_identifiers" in out:
        out["related_identifiers"] = safe_related_identifiers(out["related_identifiers"])
    for key in _TASK_FIELDS:
        if key in out:
            seen: list[str] = []
            for task in out[key] or []:
                normalised = normalise_task(task)
                if normalised and normalised not in seen:
                    seen.append(normalised)
            out[key] = seen
    for key in _CAPPED_LIST_FIELDS:
        value = out.get(key)
        if isinstance(value, list) and len(value) > MAX_COLLECTION_ITEMS:
            out[key] = value[:MAX_COLLECTION_ITEMS]
    if isinstance(out.get("keywords"), list):
        out["keywords"] = out["keywords"][:MAX_COLLECTION_ITEMS]
    return out


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
    ``curator`` — asserted by a human in ``annotations/`` or by the reconciler.
                  No source stated it, so the record must not imply one did
                  (eventlog-04). Deliberately *not* violet: violet stays
                  exclusive to machine inference (ADR-0039 §4).
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
    #: Tri-state, and the third state matters. ``None`` — the normal case — is
    #: "this scrape says nothing about withdrawal", and serialises away
    #: entirely (``exclude_none``). Only an explicit ``True`` withdraws, and
    #: only an explicit ``False`` from **the same source system** that withdrew
    #: can un-withdraw: an ordinary scrape from another system must never
    #: resurrect a tombstoned record (ADR-0027, eventlog-01).
    withdrawn: bool | None = None
    owner_org: str | None = None        # institution slug; see harvest.institutions
    extra: dict[str, Any] = Field(default_factory=dict)

    # -- safety filters, applied to every adapter's output at once ----------
    @field_validator("title", "notes", "publisher", "container", "license_raw")
    @classmethod
    def _bounded_text(cls, value: str | None) -> str | None:
        return truncate_text(value)

    @field_validator("keywords")
    @classmethod
    def _bounded_keywords(cls, value: list[str]) -> list[str]:
        return [str(word) for word in (value or [])][:MAX_COLLECTION_ITEMS]

    @field_validator("url")
    @classmethod
    def _url_scheme(cls, value: str | None) -> str | None:
        return safe_url(value) if value else value

    @field_validator("source_urls")
    @classmethod
    def _source_url_schemes(cls, value: list[str]) -> list[str]:
        return safe_urls(value)

    @field_validator("resources")
    @classmethod
    def _resource_schemes(cls, value: list[dict]) -> list[dict]:
        return safe_resources(value)[:MAX_COLLECTION_ITEMS]

    @field_validator("related_identifiers")
    @classmethod
    def _related_schemes(cls, value: list[dict]) -> list[dict]:
        return safe_related_identifiers(value)

    @field_validator("iea_task")
    @classmethod
    def _tasks(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for task in value or []:
            normalised = normalise_task(task)
            if normalised and normalised not in out:
                out.append(normalised)
        return out


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
    owner_org: str | None = None        # institution slug, when the harvest cannot infer it

    @field_validator("links")
    @classmethod
    def _link_schemes(cls, value: list[dict]) -> list[dict]:
        """A link with no followable URL is not a link. Drop the whole entry."""
        return safe_links(value)

    @field_validator("curator_notes")
    @classmethod
    def _note_schemes(cls, value: list[dict]) -> list[dict]:
        """A note is prose about a field; only its optional URL is filtered.

        Dropping the note because the URL beside it was unlinkable would lose
        the one thing on the record a human actually wrote.
        """
        return safe_related_identifiers(value)

    @field_validator("source_urls")
    @classmethod
    def _local_url_schemes(cls, value: list[str]) -> list[str]:
        return safe_urls(value)

    @field_validator("iea_task")
    @classmethod
    def _tasks(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for task in value or []:
            normalised = normalise_task(task)
            if normalised and normalised not in out:
                out.append(normalised)
        return out


#: The ``local.*`` fields an ``annotations/*.yaml`` file may set (ADR-0038,
#: eventlog-04). A curator annotates: attribution, kind, access, notes, links,
#: suppression, pins, institution. A curator does **not** assert what a source
#: said — no ``license_id``, no ``publisher``, no ``title``, no ``doi``. Those
#: are source claims, and a catalogue that lets an annotation invent an open
#: licence with no ``license_raw`` behind it is asserting a fact nobody stated.
#: A correction to a source value is expressed as a ``curator_notes`` entry
#: rendered beside the wrong value (plan §4.3, fixture ``x-10``).
LOCAL_CURATOR_FIELDS: frozenset[str] = frozenset(
    {
        "iea_task",
        "resource_kind",
        "access_status",
        "curator_notes",
        "links",
        "source_urls",
        "suppressed",
        "pinned",
        "pin_source_key",
        "owner_org",
    }
)

#: Fields the reconciler writes into ``local.*`` on a merge. Not curator-settable
#: by hand: a merge is a decision the reconciler records, with its evidence.
LOCAL_RECONCILER_FIELDS: frozenset[str] = frozenset({"merged_into", "merged_from"})


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

    @field_validator("source_system")
    @classmethod
    def _one_spelling(cls, value: str | None) -> str | None:
        """``Zenodo`` and ``zenodo`` are one system, not two (eventlog-08).

        ``source_system`` keys the per-system source block in
        :func:`harvest.events.resolve` and lands verbatim in
        ``extras.source_systems``; two spellings would compose as two systems
        and print twice on the record page.
        """
        return value.strip().lower() or None if isinstance(value, str) else value

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
