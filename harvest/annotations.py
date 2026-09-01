"""``annotations/`` — curatorial intent, replayed into ``annotated`` events.

The runbook [[correct-a-record]] says two things about this directory, and both
are implemented here:

1. ``annotations/<slug>.yaml`` is *the human-readable record of curatorial
   intent* — what a curator did and why, kept next to the events it produced.
2. Replaying it into ``annotated`` events must be **idempotent**: running
   ``python -m harvest materialize`` a hundred times appends the annotation
   once.

Idempotence is achieved without adding a field to :class:`~harvest.models.Event`
(that model is ``extra="forbid"``, and rightly so). Each declared annotation has
a **fingerprint** — a hash over ``identity_key``, ``actor``, ``note`` and the
canonical JSON of its ``local`` block — and the same fingerprint is computed for
every ``annotated`` event already in the log. Anything already present is
skipped. ``observed_at`` is deliberately excluded from the fingerprint, so a
replay never depends on the clock.

**ADR-0038 is enforced here, not merely documented.**

* An annotation may only set ``local.*``. A file containing a ``source:`` key is
  refused outright: source metadata is never edited, only annotated.
* The ``local`` block is validated against
  :class:`~harvest.models.LocalNamespace`, so ``iea_task: task-49`` (a string
  where a list belongs) fails here rather than three steps downstream.
* ``iea_task`` values must resolve, through
  :func:`harvest.config.canonical_group`, to a group that exists in
  ``groups.yaml``. An invented task name would otherwise sail through to the
  CKAN gate and fail the whole run for one typo.

**An annotation waits for its record.** An annotation is additive *to a record*.
Applying one for an identity nothing has ever harvested would materialise a
record whose title is its own identity key and whose every other field is empty
— a phantom, and one the CKAN gate would happily accept. So an annotation whose
identity has no events yet is reported as **pending** and applied on the run
that first harvests it. A file that genuinely means to create an identity from
nothing says ``allow_new: true``.

Nothing in this module edits or reorders an event. The only write path is
:func:`harvest.events.annotate`.

File format::

    identity_key: "10.5281/zenodo.1234566"
    actor: "curator:tom"
    annotations:
      - local:
          iea_task: ["task-49"]
        note: "Task 49 attribution from the IDEA workshop participant list"
      - local:
          curator_notes:
            - field: license_id
              note: "OST note: the licence stated at source appears incorrect."
        actor: "curator:someone-else"     # per-entry override, optional
        observed_at: "2026-08-28T09:00:00Z"   # optional; omit and 'now' is used
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from harvest import config
from harvest.events import annotate, read_events
from harvest.models import (
    LOCAL_CURATOR_FIELDS,
    SET_VALUED_FIELDS,
    Event,
    FieldProvenance,
    LocalNamespace,
)

__all__ = [
    "ANNOTATION_SUFFIXES",
    "DEFAULT_ACTOR",
    "Annotation",
    "AnnotationError",
    "AnnotationResult",
    "fingerprint",
    "event_fingerprint",
    "load_annotation_file",
    "load_annotations",
    "existing_fingerprints",
    "curator_provenance",
    "apply_annotations",
    "check_pins",
]

log = logging.getLogger(__name__)

ANNOTATION_SUFFIXES = (".yaml", ".yml")

DEFAULT_ACTOR = "curator"


class AnnotationError(ValueError):
    """A malformed annotation file, or one that tries to edit ``source.*``."""


@dataclass(frozen=True)
class Annotation:
    """One declared ``annotated`` event, as read from a YAML file."""

    identity_key: str
    local: dict[str, Any]
    actor: str = DEFAULT_ACTOR
    note: str | None = None
    observed_at: str | None = None
    path: str | None = None
    #: Allow this annotation to bring an identity into existence on its own.
    #: Off by default: an annotation is *additive to a record*, and applying one
    #: for an identity nothing has ever harvested would materialise a record
    #: whose title is its own identity key and whose every field is empty.
    allow_new: bool = False

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.identity_key, self.local, self.actor, self.note)


@dataclass
class AnnotationResult:
    """What one replay of ``annotations/`` did."""

    applied: list[Annotation] = field(default_factory=list)
    skipped: list[Annotation] = field(default_factory=list)
    pending: list[Annotation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_notices(self) -> list[dict]:
        """Errors and pending annotations, for ``state/last-run.json`` → ``notices``."""
        notices: list[dict] = [
            {"type": "annotation_error", "message": message} for message in self.errors
        ]
        for annotation in self.pending:
            notices.append(
                {
                    "type": "annotation_pending",
                    "identity_key": annotation.identity_key,
                    "source": annotation.path,
                    "message": (
                        "annotation waits for the record to be harvested; it will apply "
                        "itself on the run that first sees this identity"
                    ),
                }
            )
        return notices

    def summary(self) -> str:
        parts = [
            f"annotations: {len(self.applied)} applied",
            f"{len(self.skipped)} already present",
        ]
        if self.pending:
            parts.append(f"{len(self.pending)} waiting for their record")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Fingerprints — the whole idempotence story
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)


def fingerprint(
    identity_key: str,
    local: dict[str, Any],
    actor: str | None,
    note: str | None,
) -> str:
    """A stable identity for one annotation, independent of when it was applied.

    ``observed_at`` is excluded on purpose: an annotation declared in YAML
    without a timestamp must not append a second event on the next run merely
    because the clock moved.
    """
    payload = _canonical(
        {
            "identity_key": identity_key,
            "actor": actor or DEFAULT_ACTOR,
            "note": note or "",
            "local": local or {},
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def event_fingerprint(event: Event) -> str:
    """The fingerprint of an ``annotated`` event already in the log."""
    return fingerprint(event.identity_key, dict(event.local), event.actor, event.note)


def existing_fingerprints(identity_key: str, events_dir: Path | None = None) -> set[str]:
    """Fingerprints of every ``annotated`` event already recorded for an identity."""
    return {
        event_fingerprint(event)
        for event in read_events(identity_key, events_dir)
        if event.event_type == "annotated"
    }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _validate_local(local: Any, where: str, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(local, dict) or not local:
        raise AnnotationError(f"{where}: 'local' must be a non-empty mapping")

    # ADR-0038, eventlog-04: an annotation adds what no source stated. It does
    # not get to *state* a source claim. `license_id: cc-by` in an annotation
    # would put an open licence on a record page with no `license_raw` behind
    # it and no way for a reader to tell a human asserted it — so the field set
    # is closed, and the way to say "the stated licence looks wrong" is a
    # curator_note rendered beside it.
    unknown = sorted(set(local) - LOCAL_CURATOR_FIELDS)
    if unknown:
        raise AnnotationError(
            f"{where}: annotations may not set {', '.join(repr(k) for k in unknown)} — "
            f"an annotation adds only {', '.join(sorted(LOCAL_CURATOR_FIELDS))}. "
            "A correction to a value the source states is a curator_notes entry, "
            "not a local assertion (ADR-0038)"
        )

    try:
        validated = LocalNamespace.model_validate(local)
    except Exception as exc:  # pydantic ValidationError
        raise AnnotationError(f"{where}: not a valid local namespace: {exc}") from exc

    if "owner_org" in local:
        known_orgs = config.organization_names(root)
        if known_orgs and str(local["owner_org"]) not in known_orgs:
            raise AnnotationError(
                f"{where}: owner_org {local['owner_org']!r} is not in organizations.yaml — "
                "the CKAN gate would reject the record"
            )

    # Check the NORMALISED spelling, not the one the file happened to use:
    # `Task-043` and `task-43` are one group, and the register only knows the
    # canonical form. Reading the raw dict here meant a legal spelling was
    # refused while the value that would actually be stored was fine.
    tasks = validated.iea_task
    known = config.group_names(root)
    for task in tasks:
        canonical = config.canonical_group(str(task), root)
        if known and canonical not in known:
            raise AnnotationError(
                f"{where}: iea_task {task!r} resolves to {canonical!r}, which is not in "
                "groups.yaml — the CKAN gate would reject the record"
            )

    # Store what the model normalised (task spellings, unsafe link schemes
    # dropped), not what the file happened to say.
    cleaned = validated.model_dump(mode="json", exclude_none=True)
    return {key: cleaned[key] for key in local if key in cleaned}


def curator_provenance(local: dict[str, Any]) -> dict[str, FieldProvenance]:
    """One ``curator`` provenance entry per field the annotation sets.

    Without this an annotated ``resource_kind`` is indistinguishable on the
    record page from one an API stated. Provenance is the whole basis of the
    catalogue's honesty claim, so a human assertion says so (eventlog-04).
    """
    return {
        field_name: FieldProvenance(extraction_method="curator")
        for field_name in sorted(local)
    }


def load_annotation_file(path: Path, root: Path | None = None) -> list[Annotation]:
    """Parse one ``annotations/*.yaml`` into its declared annotations.

    Raises :class:`AnnotationError` on anything malformed — the caller collects
    the message rather than letting one bad file stop the replay.
    """
    try:
        document = config.load_yaml(path)
    except Exception as exc:
        # A YAML syntax error is one curator's typo, not a reason for the whole
        # run to die before it can write state/last-run.json (compliance-04).
        raise AnnotationError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise AnnotationError(f"{path.name}: top level must be a mapping")
    if "source" in document:
        raise AnnotationError(
            f"{path.name}: annotations may not set 'source' — source metadata is never "
            "edited, only annotated (ADR-0038)"
        )

    identity_key = document.get("identity_key")
    if not isinstance(identity_key, str) or not identity_key.strip():
        raise AnnotationError(f"{path.name}: 'identity_key' is required (the key, not the slug)")
    identity_key = identity_key.strip()

    file_actor = str(document.get("actor") or DEFAULT_ACTOR)
    file_allow_new = bool(document.get("allow_new", False))
    entries = document.get("annotations")
    if not isinstance(entries, list) or not entries:
        raise AnnotationError(f"{path.name}: 'annotations' must be a non-empty list")

    annotations: list[Annotation] = []
    for index, entry in enumerate(entries):
        where = f"{path.name}: annotations[{index}]"
        if not isinstance(entry, dict):
            raise AnnotationError(f"{where}: must be a mapping")
        if "source" in entry:
            raise AnnotationError(
                f"{where}: annotations may not set 'source' — source metadata is never "
                "edited, only annotated (ADR-0038)"
            )
        local = _validate_local(entry.get("local"), where, root)
        note = entry.get("note")
        observed_at = entry.get("observed_at")
        annotations.append(
            Annotation(
                identity_key=identity_key,
                local=local,
                actor=str(entry.get("actor") or file_actor),
                note=str(note) if note is not None else None,
                observed_at=str(observed_at) if observed_at is not None else None,
                path=str(path),
                allow_new=bool(entry.get("allow_new", file_allow_new)),
            )
        )
    return annotations


def load_annotations(
    directory: Path | None = None,
    root: Path | None = None,
    errors: list[str] | None = None,
) -> list[Annotation]:
    """Every annotation declared under ``annotations/``, in file order.

    A file that cannot be parsed is logged and appended to ``errors`` rather
    than raising: one malformed annotation must not stop the others being
    applied, exactly as one broken adapter does not stop the harvest.
    """
    directory = directory if directory is not None else config.annotations_dir(root)
    out: list[Annotation] = []
    if not directory.exists():
        return out
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in ANNOTATION_SUFFIXES or not path.is_file():
            continue
        try:
            out.extend(load_annotation_file(path, root))
        except Exception as exc:  # AnnotationError, and anything a bad file does
            log.error("%s", exc)
            if errors is not None:
                errors.append(str(exc))
    return out


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def apply_annotations(
    directory: Path | None = None,
    events_dir: Path | None = None,
    root: Path | None = None,
    dry_run: bool = False,
    annotations: Sequence[Annotation] | None = None,
) -> AnnotationResult:
    """Replay ``annotations/`` into ``annotated`` events, idempotently.

    Called by ``python -m harvest materialize`` and ``python -m harvest run``
    before the replay, so a curator's YAML reaches ``records/`` in one command.
    Returns the outcome; never raises.
    """
    result = AnnotationResult()
    declared = (
        list(annotations)
        if annotations is not None
        else load_annotations(directory, root, errors=result.errors)
    )
    events_directory = events_dir if events_dir is not None else config.events_dir(root)

    seen: dict[str, set[str]] = {}
    known: dict[str, bool] = {}
    for annotation in declared:
        key = annotation.identity_key
        if key not in seen:
            events = read_events(key, events_directory)
            known[key] = bool(events)
            seen[key] = {
                event_fingerprint(event) for event in events if event.event_type == "annotated"
            }
        if annotation.fingerprint in seen[key]:
            result.skipped.append(annotation)
            continue
        if not known[key] and not annotation.allow_new:
            # Nothing has ever harvested this identity. Applying now would
            # materialise a record with no source at all. Wait for the harvest.
            log.info(
                "annotation for %s waits for the record to exist (%s)",
                key,
                annotation.path,
            )
            result.pending.append(annotation)
            continue
        if not dry_run:
            try:
                annotate(
                    key,
                    annotation.local,
                    actor=annotation.actor,
                    note=annotation.note,
                    provenance=curator_provenance(annotation.local),
                    events_dir=events_directory,
                    observed_at=annotation.observed_at,
                )
            except ValueError as exc:  # e.g. a slug collision — one record's problem
                message = f"{key}: {exc}"
                log.error("could not annotate %s", message)
                result.errors.append(message)
                continue
        seen[key].add(annotation.fingerprint)
        known[key] = True
        result.applied.append(annotation)

    log.info("%s", result.summary())
    return result


# ---------------------------------------------------------------------------
# Pins (ADR-0038 §4.3, fixture x-09)
# ---------------------------------------------------------------------------


def check_pins(
    events_dir: Path | None = None,
    root: Path | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Fire a ``pin_notice`` wherever a pinned page has moved beneath the pin.

    A pin is a human judgement about *our own* Tier-3 inference, recorded as
    ``local.pinned`` with ``local.pin_source_key`` — the page content hash the
    judgement was made against. **The pin holds**: the extraction cache serves
    the pinned object, so the record keeps the corrected value. What must not
    happen silently is the page changing underneath it, so when the latest
    scrape's source key differs from ``pin_source_key`` this appends one
    ``pin_notice`` and a human decides whether the pin is still right.

    Idempotent: one notice per observed source key, not one per run. The notice
    is stamped with the *triggering scrape's* ``observed_at`` rather than with
    the clock, because that is when the conflict came into existence — and
    because a replay of the same log must produce the same events.
    """
    from harvest.events import iter_identity_keys, raise_notice, read_events, resolve

    directory = events_dir if events_dir is not None else config.events_dir(root)
    notices: list[dict] = []

    for identity_key in sorted(iter_identity_keys(directory)):
        resolved = resolve(identity_key, events_dir=directory)
        if not resolved.local.get("pinned"):
            continue
        pin_source_key = resolved.local.get("pin_source_key")
        if not pin_source_key or not resolved.source_key:
            continue
        if resolved.source_key == pin_source_key:
            continue

        events = read_events(identity_key, directory)
        already = {
            (event.notice or {}).get("observed_source_key")
            for event in events
            if event.event_type == "pin_notice"
        }
        if resolved.source_key in already:
            continue
        triggering_scrape = next(
            (event for event in reversed(events) if event.event_type == "scraped"), None
        )

        notice = {
            "reason": "page-content-hash-changed",
            "pin_source_key": str(pin_source_key),
            "observed_source_key": str(resolved.source_key),
            "source_system": resolved.source_system,
            "action": "the pin holds; a human decides whether it is still right",
        }
        if not dry_run:
            raise_notice(
                identity_key,
                "pin_notice",
                notice,
                events_dir=directory,
                observed_at=triggering_scrape.observed_at if triggering_scrape else None,
                note=(
                    "the page moved beneath a pinned extraction "
                    f"({pin_source_key} -> {resolved.source_key})"
                ),
            )
        notices.append({"type": "pin_notice", "identity_key": identity_key, **notice})
        log.warning(
            "pin_notice: %s — page content hash moved from %s to %s",
            identity_key,
            pin_source_key,
            resolved.source_key,
        )
    return notices


def annotated_fields(annotations: Iterable[Annotation]) -> set[str]:
    """Every ``local.*`` field the given annotations touch. Handy in reports."""
    fields: set[str] = set()
    for annotation in annotations:
        fields.update(annotation.local)
    return fields


def set_valued(field_name: str) -> bool:
    """Whether a local field unions rather than being replaced (ADR-0038)."""
    return field_name in SET_VALUED_FIELDS
