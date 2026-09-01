"""The event log — the source of truth (ADR-0037).

``events/<slug>.jsonl``, one file per identity, append-only, **append-on-change
only**. A scrape whose source key matches the last one for that source writes
nothing at all; the fact that the run happened is recorded in
``state/last-run.json`` instead (ADR-0026).

``records/`` is a materialised view of this directory and can be deleted and
regenerated at any time. This directory cannot.

Note on filenames: ADR-0037 speaks of ``events/<identity-key>.jsonl``, but an
identity key contains ``/`` and ``|``. The file stem is therefore the
:func:`~harvest.identity.slug_for_identity` rendering of the key — the same
slug used for ``records/<slug>.json``, the CKAN ``package.name`` and the site
URL — and every line carries the unabbreviated ``identity_key`` inside it.

**Resolution (ADR-0038), implemented in :func:`resolve`:**

1. ``scraped`` replaces that *source system's* contribution wholesale. When
   several systems describe one identity (fixture ``x-01``), each system's
   block is replaced independently and the blocks are composed in the
   precedence order declared in ``sources.yaml``.
2. ``annotated`` sets ``local`` fields, latest event wins per field — except
   for :data:`~harvest.models.SET_VALUED_FIELDS`, which union.
3. Effective value = source if the source provides one, else local; set-valued
   fields union both. A scalar the source has taken over is a **displacement**:
   the local value is still in the log, and a notice is raised.
4. ``withdrawn`` marks the record withdrawn. It is never deleted (ADR-0027).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from harvest import config
from harvest.identity import disambiguated_slug, slug_for_identity
from harvest.models import (
    SET_VALUED_FIELDS,
    sanitise_payload,
    Event,
    FieldProvenance,
    ResolvedRecord,
    utcnow,
)

__all__ = [
    "DEFAULT_SOURCE_PRECEDENCE",
    "event_path",
    "append_event",
    "read_events",
    "read_all_events",
    "iter_identity_keys",
    "last_event",
    "last_source_key",
    "has_changed",
    "resolve",
    "replay",
    "record_scrape",
    "annotate",
    "withdraw",
    "raise_notice",
    "NOTICE_EVENT_TYPES",
]

log = logging.getLogger(__name__)

#: Fallback precedence when ``sources.yaml`` declares none. Lower wins.
#: Rationale: DOI registries state the authoritative bibliographic record;
#: repositories state the artifact; HTML pages are inference and rank last.
DEFAULT_SOURCE_PRECEDENCE: dict[str, int] = {
    "datacite": 10,
    "crossref": 20,
    "zenodo": 30,
    "osti": 40,
    "wdh": 50,
    "github": 60,
    "ieawind": 90,
}

_EMPTY = (None, "", [], {})

#: Event types that carry a ``notice`` rather than metadata. All three end
#: up in ``ResolvedRecord.notices`` and therefore on the record page and in
#: the run report. ``merge_proposal`` is here because a proposal that lives
#: only in ``state/merge-proposals.json`` is not durable: that file is
#: rewritten wholesale on every pass, while ADR-0037 makes ``events/`` the
#: source of truth, and the spec requires a proposal to be recorded rather
#: than applied silently (compliance-10).
NOTICE_EVENT_TYPES = ("displacement_notice", "pin_notice", "merge_proposal")


# ---------------------------------------------------------------------------
# Reading and writing
# ---------------------------------------------------------------------------


def event_path(identity_key: str, events_dir: Path | None = None) -> Path:
    """``events/<slug>.jsonl`` for an identity key.

    Usually just :func:`~harvest.identity.slug_for_identity`. When the plain
    slug is already owned by a *different* identity — the slug is lossy, two
    DOIs can render to one (site-07) — this returns the
    :func:`~harvest.identity.disambiguated_slug` file instead. First writer
    keeps the plain name, so no existing record ever moves; the late arrival
    gets a distinct log rather than being refused a place in the catalogue.

    Read and write agree because both go through here.
    """
    directory = events_dir or config.events_dir()
    plain = directory / f"{slug_for_identity(identity_key)}.jsonl"
    incumbent = _identity_in_file(plain)
    if incumbent is None or incumbent == identity_key:
        return plain

    fallback = directory / f"{disambiguated_slug(identity_key)}.jsonl"
    if _identity_in_file(fallback) == identity_key:
        return fallback

    # Neither canonical name holds this identity, but a hand-written or
    # hand-renamed file still might. Losing an existing log because its name is
    # unexpected would be the worse failure by far, so look before writing a
    # new one. Reached only by an identity whose plain slug is taken, which is
    # the collision case and therefore vanishingly rare.
    for path in sorted(directory.glob("*.jsonl")):
        if _identity_in_file(path) == identity_key:
            return path
    return fallback


def _identity_in_file(path: Path) -> str | None:
    """The identity key already claiming this file, or ``None`` if it is new.

    A corrupt first line is skipped rather than raised on: the file's owner is
    whichever identity the first *readable* line names.
    """
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line).get("identity_key")
            except json.JSONDecodeError as exc:
                _log_problem(path, number, "malformed event line", str(exc))
    return None


def append_event(identity_key: str, event: Event, events_dir: Path | None = None) -> Path:
    """Append one event. Creates the file and directory as needed.

    The only write path into ``events/``. Never rewrites or reorders a line.

    Two identities whose slugs collide must never interleave into one log —
    the slug is short and lossy, the identity key is not — so
    :func:`event_path` routes the late arrival to its
    :func:`~harvest.identity.disambiguated_slug`. The guard below therefore
    fires only if *that* file is somehow owned by a third identity, which a
    sha256 cannot produce by accident; it stays as an assertion. Callers get a
    ``ValueError``; ``run_adapter`` turns it into one logged, skipped record
    rather than a failed run.
    """
    if event.identity_key != identity_key:
        raise ValueError(
            f"event.identity_key {event.identity_key!r} does not match {identity_key!r}"
        )
    path = event_path(identity_key, events_dir)
    incumbent = _identity_in_file(path)
    if incumbent is not None and incumbent != identity_key:
        raise ValueError(
            f"slug collision: {path.name} is already owned by identity "
            f"{incumbent!r}; refusing to append events for {identity_key!r}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(event.to_jsonl() + "\n")
    return path


#: Every event line this process refused to read, as ``{path, line, reason}``.
#: Collected rather than raised: one truncated line — the residue of a killed
#: append — must not stop the other 29 identities materialising, and must not
#: stop ``state/last-run.json`` being written (CONTRACT rule 5, ADR-0029).
#: ``cmd_run`` drains this into the run report so a skipped line is loud.
LOG_PROBLEMS: list[dict[str, Any]] = []


def _log_problem(path: Path, number: int, reason: str, detail: str) -> None:
    problem = {
        "type": "event_log_problem",
        "path": path.name,
        "line": number,
        "reason": reason,
        "message": detail,
    }
    if problem not in LOG_PROBLEMS:
        LOG_PROBLEMS.append(problem)
    log.error("%s:%s: %s (%s) — line skipped", path, number, reason, detail)


def log_problems() -> list[dict[str, Any]]:
    """The skipped-line notices collected so far, for the run report."""
    return list(LOG_PROBLEMS)


def reset_log_problems() -> None:
    """Clear the collected notices. Tests and long-lived processes call this."""
    LOG_PROBLEMS.clear()


def read_events(identity_key: str, events_dir: Path | None = None) -> list[Event]:
    """Every event for one identity, in observation order (stable on ties).

    **Skip-and-report, never raise.** Two lines are refused and reported:

    * one that is not a valid :class:`~harvest.models.Event` — truncated,
      corrupt, or carrying a field this version does not know;
    * one whose ``identity_key`` is not this identity's. A stranger's events
      sitting in this file would otherwise be folded straight into this
      record, which is exactly the corruption
      :func:`append_event`'s collision guard exists to prevent (eventlog-03).
      The guard covers the write path; this covers a hand-written log.
    """
    path = event_path(identity_key, events_dir)
    if not path.exists():
        return []
    events: list[Event] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = Event.from_jsonl(line)
            except Exception as exc:  # json, pydantic, anything a bad byte does
                _log_problem(path, number, "malformed event line", str(exc))
                continue
            if event.identity_key != identity_key:
                _log_problem(
                    path,
                    number,
                    "event belongs to another identity",
                    f"{event.identity_key!r} in the log of {identity_key!r}",
                )
                continue
            events.append(event)
    return _in_order(events)


def _in_order(events: Sequence[Event]) -> list[Event]:
    """Sort by ``observed_at``, keeping file order as the tie-break."""
    ordered = sorted(enumerate(events), key=lambda pair: (pair[1].observed_at, pair[0]))
    return [event for _, event in ordered]


def iter_identity_keys(events_dir: Path | None = None) -> Iterator[str]:
    """Every identity key with at least one event, in slug order."""
    directory = events_dir or config.events_dir()
    if not directory.exists():
        return
    for path in sorted(directory.glob("*.jsonl")):
        identity_key = _identity_in_file(path)
        if identity_key is None:
            continue
        if path.stem not in (slug_for_identity(identity_key), disambiguated_slug(identity_key)):
            # A hand-written log whose filename renders neither the identity's
            # plain slug nor its collision-disambiguated one. Left readable —
            # the events are not lost — but reported, because a mismatch means
            # the file the record materialises to is not the file the harvest
            # would append to, and the two would silently diverge.
            _log_problem(
                path,
                1,
                "file name does not match its identity",
                f"{identity_key!r} renders to {slug_for_identity(identity_key)!r}",
            )
        yield identity_key


def read_all_events(events_dir: Path | None = None) -> dict[str, list[Event]]:
    """``{identity_key: [Event, ...]}`` for the whole log."""
    return {key: read_events(key, events_dir) for key in iter_identity_keys(events_dir)}


def last_event(
    identity_key: str,
    events_dir: Path | None = None,
    event_type: str | None = None,
    source_system: str | None = None,
) -> Event | None:
    """The most recent matching event, or ``None``.

    ``source_system`` is matched case-insensitively, to the same rule
    :class:`~harvest.models.Event` normalises writes by (eventlog-08). A
    caller asking about ``"Zenodo"`` must not miss ``zenodo``'s events and
    conclude the source key has moved — that would re-emit every record.
    """
    wanted = source_system.strip().lower() if isinstance(source_system, str) else source_system
    for event in reversed(read_events(identity_key, events_dir)):
        if event_type and event.event_type != event_type:
            continue
        if wanted and event.source_system != wanted:
            continue
        return event
    return None


def last_source_key(
    identity_key: str,
    source_system: str | None = None,
    events_dir: Path | None = None,
) -> str | None:
    """The source key of the last ``scraped`` event, optionally per system."""
    event = last_event(
        identity_key, events_dir, event_type="scraped", source_system=source_system
    )
    return event.source_key if event else None


def has_changed(
    identity_key: str,
    source_system: str,
    source_key: str,
    events_dir: Path | None = None,
) -> bool:
    """ADR-0026 change detection: has this source's change token moved?

    ``False`` means **skip the record and write no event**. The run report
    still counts it as seen.
    """
    return last_source_key(identity_key, source_system, events_dir) != source_key


# ---------------------------------------------------------------------------
# Convenience writers
# ---------------------------------------------------------------------------


def record_scrape(
    identity_key: str,
    source_system: str,
    source_id: str,
    source_key: str,
    source: dict[str, Any],
    provenance: dict[str, FieldProvenance] | None = None,
    events_dir: Path | None = None,
    observed_at: str | None = None,
    actor: str | None = None,
) -> Event:
    """Append a ``scraped`` event. Caller must already have checked
    :func:`has_changed` — this function does not check for you.

    ``source`` is sanitised here, not only in ``SourceNamespace``: this is
    the write path, and it accepts a plain dict, so the URL-scheme
    allow-list and the length caps have to live where every caller passes
    through rather than where a well-behaved adapter happens to.
    """
    event = Event(
        observed_at=observed_at or utcnow(),
        event_type="scraped",
        identity_key=identity_key,
        source_key=source_key,
        source_system=source_system,
        source_id=source_id,
        source=sanitise_payload(source),
        provenance=provenance or {},
        actor=actor or f"harvest/{source_system}",
    )
    append_event(identity_key, event, events_dir)
    return event


def annotate(
    identity_key: str,
    local: dict[str, Any],
    actor: str = "curator",
    note: str | None = None,
    provenance: dict[str, FieldProvenance] | None = None,
    events_dir: Path | None = None,
    observed_at: str | None = None,
) -> Event:
    """Append an ``annotated`` event — an additive local change.

    Sanitised on the same terms as a scrape: a curator pastes URLs too.
    """
    event = Event(
        observed_at=observed_at or utcnow(),
        event_type="annotated",
        identity_key=identity_key,
        local=sanitise_payload(local),
        provenance=provenance or {},
        actor=actor,
        note=note,
    )
    append_event(identity_key, event, events_dir)
    return event


def withdraw(
    identity_key: str,
    source_system: str | None = None,
    note: str | None = None,
    events_dir: Path | None = None,
    observed_at: str | None = None,
) -> Event:
    """Append a ``withdrawn`` event. The record is retained, never deleted."""
    event = Event(
        observed_at=observed_at or utcnow(),
        event_type="withdrawn",
        identity_key=identity_key,
        source_system=source_system,
        actor=f"harvest/{source_system}" if source_system else "harvest",
        note=note,
    )
    append_event(identity_key, event, events_dir)
    return event


def raise_notice(
    identity_key: str,
    event_type: str,
    notice: dict[str, Any],
    events_dir: Path | None = None,
    observed_at: str | None = None,
    note: str | None = None,
) -> Event:
    """Append a ``displacement_notice``, ``pin_notice`` or ``merge_proposal``."""
    if event_type not in NOTICE_EVENT_TYPES:
        raise ValueError(f"not a notice event type: {event_type!r}")
    event = Event(
        observed_at=observed_at or utcnow(),
        event_type=event_type,  # type: ignore[arg-type]
        identity_key=identity_key,
        notice=notice,
        actor="reconcile",
        note=note,
    )
    append_event(identity_key, event, events_dir)
    return event


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _union(*values: Iterable[Any] | None) -> list[Any]:
    """Order-preserving union, tolerant of unhashable members (dicts)."""
    out: list[Any] = []
    seen: set[str] = set()
    for group in values:
        if not group:
            continue
        if isinstance(group, (str, bytes)):
            group = [group]
        for item in group:
            token = json.dumps(item, sort_keys=True, default=str)
            if token in seen:
                continue
            seen.add(token)
            out.append(item)
    return out


def _is_empty(value: Any) -> bool:
    return any(value is empty or value == empty for empty in _EMPTY) and not isinstance(value, bool)


def _load_precedence(precedence: dict[str, int] | None) -> dict[str, int]:
    if precedence is not None:
        return precedence
    declared: dict[str, int] = {}
    for name, cfg in config.load_sources().items():
        if isinstance(cfg, dict) and cfg.get("precedence") is not None:
            declared[name] = int(cfg["precedence"])
    return declared or DEFAULT_SOURCE_PRECEDENCE


def _compose_source(
    by_system: dict[str, dict[str, Any]],
    precedence: dict[str, int],
) -> dict[str, Any]:
    """Compose several systems' source blocks into one ``source`` view.

    Higher-precedence systems (lower number) win scalars; set-valued fields
    union across all of them, so a four-way merge keeps four source URLs
    (fixture ``x-01``).
    """
    order = sorted(
        by_system,
        key=lambda system: (precedence.get(system, 500), system),
        reverse=True,  # apply worst first so the best overwrites last
    )
    composed: dict[str, Any] = {}
    for system in order:
        block = by_system[system]["source"]
        for key, value in block.items():
            if key in SET_VALUED_FIELDS:
                composed[key] = _union(composed.get(key), value)
            elif not _is_empty(value):
                composed[key] = value
            else:
                composed.setdefault(key, value)
    return composed


def resolve(
    identity_key: str,
    events: Sequence[Event] | None = None,
    events_dir: Path | None = None,
    precedence: dict[str, int] | None = None,
) -> ResolvedRecord:
    """Fold an identity's events into its resolved state (ADR-0038)."""
    log_events = list(events) if events is not None else read_events(identity_key, events_dir)
    ranks = _load_precedence(precedence)

    by_system: dict[str, dict[str, Any]] = {}
    local: dict[str, Any] = {}
    local_provenance: dict[str, FieldProvenance] = {}
    source_provenance: dict[str, FieldProvenance] = {}
    notices: list[dict] = []
    # Withdrawal is a *positive assertion by one source system*, and only that
    # system can take it back (ADR-0027, eventlog-01). ``{system: observed_at}``
    # — a manual ``withdraw()`` with no system is filed under "" and no scrape
    # can clear it, because no scrape observed what the human observed.
    withdrawn_by: dict[str, str] = {}
    first_seen: str | None = None
    last_seen: str | None = None
    last_scrape: Event | None = None

    for event in log_events:
        # "Seen" means seen UPSTREAM. A scrape is an observation of the
        # artifact; so is a withdrawal, which is an observation of its absence.
        # An annotation, a displacement notice or a merge proposal is the
        # catalogue talking to itself, and letting one of those advance
        # `last_seen` puts a date on the record page that no source vouched for
        # — the reconciler noting a probable duplicate would read as "we
        # checked upstream today".
        if event.event_type in ("scraped", "withdrawn"):
            first_seen = first_seen or event.observed_at
            last_seen = event.observed_at

        if event.event_type == "scraped":
            system = event.source_system or "unknown"
            by_system[system] = {
                "source": dict(event.source),
                "source_key": event.source_key,
                "source_id": event.source_id,
                "observed_at": event.observed_at,
            }
            # A scrape replaces that system's provenance wholesale too.
            source_provenance = {
                key: value
                for key, value in source_provenance.items()
                if value.source_system and value.source_system != system
            }
            for key, value in event.provenance.items():
                source_provenance[key] = value.model_copy(
                    update={"source_system": value.source_system or system}
                )
            last_scrape = event
            claim = event.source.get("withdrawn")
            if claim is True:
                withdrawn_by[system] = event.observed_at
            elif claim is False:
                # An explicit "I looked, and it is still there" from the system
                # that withdrew it. An *absent* flag says nothing and clears
                # nothing: an ordinary scrape from another source system must
                # never resurrect a tombstoned record.
                withdrawn_by.pop(system, None)

        elif event.event_type == "annotated":
            for key, value in event.local.items():
                if key in SET_VALUED_FIELDS:
                    local[key] = _union(local.get(key), value)
                else:
                    local[key] = value
            local_provenance.update(event.provenance)

        elif event.event_type == "withdrawn":
            withdrawn_by[event.source_system or ""] = event.observed_at

        elif event.event_type in NOTICE_EVENT_TYPES:
            notices.append(
                {
                    "type": event.event_type,
                    "observed_at": event.observed_at,
                    "identity_key": identity_key,
                    **(event.notice or {}),
                }
            )

    withdrawn = bool(withdrawn_by)
    #: The earliest still-standing withdrawal: when this record stopped being
    #: available, not when the last system noticed.
    withdrawn_at = min(withdrawn_by.values()) if withdrawn_by else None

    source = _compose_source(by_system, ranks)

    # --- effective view, and the displacement notices it implies -----------
    effective: dict[str, Any] = {}
    for key in sorted(set(source) | set(local)):
        source_value = source.get(key)
        local_value = local.get(key)
        if key in SET_VALUED_FIELDS:
            effective[key] = _union(source_value, local_value)
            continue
        if not _is_empty(source_value):
            effective[key] = source_value
            if not _is_empty(local_value) and local_value != source_value:
                notices.append(
                    {
                        "type": "displacement",
                        "identity_key": identity_key,
                        "field": key,
                        "displaced_local_value": local_value,
                        "source_value": source_value,
                        "implicit": True,
                    }
                )
        elif local_value is not None:
            effective[key] = local_value

    # Local provenance fills the gaps the sources left; it never overwrites a
    # source's account of where a value came from. That matters most on the
    # set-valued fields: ``iea_task`` is a UNION, so when Zenodo's community
    # slug contributed ``task-43`` and a curator added ``task-49``, stamping
    # the whole field ``curator`` would erase the fact that a source stated
    # part of it — the exact inversion of the honesty this provenance exists
    # for. The source's claim is the stronger one and stays.
    provenance = {**source_provenance}
    for key, value in local_provenance.items():
        if key not in source_provenance:
            provenance[key] = value

    return ResolvedRecord(
        identity_key=identity_key,
        # From the event log, not from the key alone: if this identity lost a
        # slug collision its record, its URL and its log file must all agree on
        # the disambiguated name (site-07).
        slug=event_path(identity_key, events_dir).stem,
        source=source,
        local=local,
        effective=effective,
        provenance=provenance,
        source_key=last_scrape.source_key if last_scrape else None,
        source_system=last_scrape.source_system if last_scrape else None,
        source_id=last_scrape.source_id if last_scrape else None,
        source_systems=sorted(by_system),
        first_seen=first_seen,
        last_seen=last_seen,
        withdrawn=withdrawn,
        withdrawn_at=withdrawn_at,
        notices=notices,
        event_count=len(log_events),
    )


def replay(
    identity_key: str,
    events: Sequence[Event] | None = None,
    events_dir: Path | None = None,
    precedence: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Replay an identity's events into its CKAN package dict.

    This is the function ``records/*.json`` is generated by. Import cycle is
    avoided by importing the shaper lazily.
    """
    from harvest.materialize import to_ckan_package

    resolved = resolve(identity_key, events, events_dir, precedence)
    return to_ckan_package(resolved)
