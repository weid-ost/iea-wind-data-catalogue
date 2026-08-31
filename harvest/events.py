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
from harvest.identity import slug_for_identity
from harvest.models import (
    SET_VALUED_FIELDS,
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


# ---------------------------------------------------------------------------
# Reading and writing
# ---------------------------------------------------------------------------


def event_path(identity_key: str, events_dir: Path | None = None) -> Path:
    """``events/<slug>.jsonl`` for an identity key."""
    directory = events_dir or config.events_dir()
    return directory / f"{slug_for_identity(identity_key)}.jsonl"


def _identity_in_file(path: Path) -> str | None:
    """The identity key already claiming this file, or ``None`` if it is new."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                return json.loads(line).get("identity_key")
    return None


def append_event(identity_key: str, event: Event, events_dir: Path | None = None) -> Path:
    """Append one event. Creates the file and directory as needed.

    The only write path into ``events/``. Never rewrites or reorders a line.

    Refuses to write if a *different* identity key already owns the file. Two
    identities whose slugs collide would otherwise interleave into one log and
    corrupt both records, invisibly — the slug is short and lossy, the identity
    key is not. Callers get a ``ValueError``; ``run_adapter`` turns it into one
    logged, skipped record rather than a failed run.
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


def read_events(identity_key: str, events_dir: Path | None = None) -> list[Event]:
    """Every event for one identity, in observation order (stable on ties)."""
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
                events.append(Event.from_jsonl(line))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{path}:{number}: malformed event line: {exc}") from exc
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
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)["identity_key"]
                break


def read_all_events(events_dir: Path | None = None) -> dict[str, list[Event]]:
    """``{identity_key: [Event, ...]}`` for the whole log."""
    return {key: read_events(key, events_dir) for key in iter_identity_keys(events_dir)}


def last_event(
    identity_key: str,
    events_dir: Path | None = None,
    event_type: str | None = None,
    source_system: str | None = None,
) -> Event | None:
    """The most recent matching event, or ``None``."""
    for event in reversed(read_events(identity_key, events_dir)):
        if event_type and event.event_type != event_type:
            continue
        if source_system and event.source_system != source_system:
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
    :func:`has_changed` — this function does not check for you."""
    event = Event(
        observed_at=observed_at or utcnow(),
        event_type="scraped",
        identity_key=identity_key,
        source_key=source_key,
        source_system=source_system,
        source_id=source_id,
        source=source,
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
    """Append an ``annotated`` event — an additive local change."""
    event = Event(
        observed_at=observed_at or utcnow(),
        event_type="annotated",
        identity_key=identity_key,
        local=local,
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
    """Append a ``displacement_notice`` or ``pin_notice``."""
    if event_type not in ("displacement_notice", "pin_notice"):
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
    withdrawn = False
    withdrawn_at: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    last_scrape: Event | None = None

    for event in log_events:
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
            if event.source.get("withdrawn") is True:
                withdrawn, withdrawn_at = True, event.observed_at
            elif withdrawn and event.source.get("withdrawn") is False:
                withdrawn, withdrawn_at = False, None

        elif event.event_type == "annotated":
            for key, value in event.local.items():
                if key in SET_VALUED_FIELDS:
                    local[key] = _union(local.get(key), value)
                else:
                    local[key] = value
            local_provenance.update(event.provenance)

        elif event.event_type == "withdrawn":
            withdrawn, withdrawn_at = True, event.observed_at

        elif event.event_type in ("displacement_notice", "pin_notice"):
            notices.append(
                {
                    "type": event.event_type,
                    "observed_at": event.observed_at,
                    "identity_key": identity_key,
                    **(event.notice or {}),
                }
            )

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

    provenance = {**source_provenance}
    for key, value in local_provenance.items():
        if key not in effective or key in SET_VALUED_FIELDS or key not in source_provenance:
            provenance[key] = value

    return ResolvedRecord(
        identity_key=identity_key,
        slug=slug_for_identity(identity_key),
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
