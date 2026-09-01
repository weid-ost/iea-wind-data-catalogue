"""Replay the event log into ``records/*.json``, then run the CKAN gate.

``records/`` is a **derived** directory (ADR-0037): delete it and
``make materialize`` reproduces it exactly. "Exactly" is load-bearing —
materialisation is byte-stable (sorted keys, fixed separators, two-space
indent, trailing newline), so a run in which nothing changed produces no diff
in ``records/`` and the only churn in the heartbeat commit is
``state/last-run.json``.

Withdrawn records are materialised, not deleted (ADR-0027). See
:class:`~harvest.models.CkanPackage` for why withdrawal is not CKAN's
``state: deleted``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from harvest import config
from harvest.ckan_compat import Violation, tagify, validate_records
from harvest.events import iter_identity_keys, log_problems, resolve
from harvest.institutions import infer_owner_org
from harvest.licenses import is_known_license, map_license
from harvest.models import ResolvedRecord, json_extra

__all__ = [
    "MaterializeResult",
    "to_ckan_package",
    "build_extras",
    "dump_record",
    "write_record",
    "materialize_all",
]

log = logging.getLogger(__name__)

#: The custom fields carried in ``extras``, all as strings. Documented in
#: ``schema/ckan-scheming.json`` and in ``harvest/CONTRACT.md``.
EXTRA_KEYS = (
    "access_status",
    "authors",
    "container",
    "curator_notes",
    "doi",
    "embargo_date",
    "first_seen",
    "iea_task",
    "identity_key",
    "identity_kind",
    "last_seen",
    "license_raw",
    "license_mapped",
    "lifecycle_state",
    "local_links",
    "pinned",
    "provenance",
    "published_date",
    "publisher",
    "related_identifiers",
    "report_number",
    "resource_kind",
    "source_id",
    "source_key",
    "source_system",
    "source_systems",
    "source_url",
    "source_urls",
    "suppressed",
    "withdrawn",
    "withdrawn_at",
)


@dataclass
class MaterializeResult:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)
    unmapped_licenses: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def total(self) -> int:
        return len(self.written) + len(self.unchanged)


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json_extra(value)
    return str(value)


def build_extras(resolved: ResolvedRecord) -> list[dict[str, str]]:
    """The custom-field block, as CKAN string extras, sorted by key.

    Structured values (``iea_task``, ``source_urls``, ``provenance``,
    ``authors``, ``related_identifiers``) are JSON encoded **inside** the
    string, compactly and with sorted keys. Empty values are omitted entirely
    rather than written as ``""`` — an absent extra and an empty one mean
    different things on a record page.
    """
    from harvest.identity import identity_kind

    effective = resolved.effective
    license_raw = effective.get("license_raw")
    _, mapped = map_license(license_raw) if license_raw else (None, True)

    source_urls = list(effective.get("source_urls") or [])
    if effective.get("url") and effective["url"] not in source_urls:
        source_urls = [effective["url"], *source_urls]

    provenance = {
        key: value.model_dump(mode="json", exclude_none=True)
        for key, value in sorted(resolved.provenance.items())
    }

    candidates: dict[str, Any] = {
        "identity_key": resolved.identity_key,
        "identity_kind": identity_kind(resolved.identity_key),
        "source_system": resolved.source_system,
        "source_systems": resolved.source_systems or None,
        "source_id": resolved.source_id,
        "source_key": resolved.source_key,
        "source_url": source_urls[0] if source_urls else None,
        "source_urls": source_urls or None,
        "doi": effective.get("doi"),
        # Canonicalised and de-duplicated: one task is one chip and one facet
        # bucket, whatever spelling (or renumbered alias) each source used.
        "iea_task": sorted(
            {config.canonical_group(task) for task in (effective.get("iea_task") or []) if task}
        ) or None,
        "resource_kind": effective.get("resource_kind"),
        "access_status": effective.get("access_status"),
        "embargo_date": effective.get("embargo_date"),
        "authors": effective.get("authors") or None,
        "publisher": effective.get("publisher"),
        "published_date": effective.get("published_date"),
        "container": effective.get("container"),
        # A report/laboratory number (NREL/TP-5000-89937). For the large
        # population of grey literature with no DOI it is the only stable
        # human-facing identifier there is, and it is what a reader cites.
        "report_number": effective.get("report_number"),
        "related_identifiers": effective.get("related_identifiers") or None,
        "license_raw": license_raw,
        "license_mapped": None if license_raw is None else mapped,
        # Local-only, never displaced by a source: the curator note rendered
        # beside a known-wrong upstream value (plan §4.3, fixture x-10).
        "curator_notes": resolved.local.get("curator_notes") or None,
        "local_links": resolved.local.get("links") or None,
        "provenance": provenance or None,
        # LIFECYCLE_STATES names three states. Withdrawal wins; "archived" is
        # what a source says about an artifact it still publishes but no longer
        # maintains — an archived GitHub repository (fixture gh-05). Marked and
        # retained, never deleted (ADR-0027).
        "lifecycle_state": (
            "withdrawn"
            if resolved.withdrawn
            else ("archived" if effective.get("archived") else "active")
        ),
        "withdrawn": resolved.withdrawn,
        "withdrawn_at": resolved.withdrawn_at,
        "first_seen": resolved.first_seen,
        "last_seen": resolved.last_seen,
        "suppressed": True if resolved.local.get("suppressed") else None,
        "pinned": True if resolved.local.get("pinned") else None,
    }

    extras = [
        {"key": key, "value": _string(value)}
        for key, value in candidates.items()
        if value is not None and value != []
    ]
    return sorted(extras, key=lambda extra: extra["key"])


def to_ckan_package(
    resolved: ResolvedRecord,
    root: Path | None = None,
    notices: list[dict] | None = None,
) -> dict[str, Any]:
    """Shape a resolved record as a CKAN package dict, ready to POST.

    ``notices`` collects anything dropped on the way (an unknown task group),
    so the run report says what happened rather than the record quietly
    differing from the event log.
    """
    effective = resolved.effective

    license_id = effective.get("license_id")
    if not is_known_license(license_id):
        license_id, _ = map_license(license_id or effective.get("license_raw"))

    tags: list[str] = []
    for keyword in effective.get("keywords") or []:
        tag = tagify(keyword)
        if tag and tag not in tags:
            tags.append(tag)

    # A group name that is not in groups.yaml fails the CKAN gate, and because
    # events/ is append-only it would fail it again on every subsequent run —
    # one hostile or merely new upstream community would block every deploy
    # (scrape-02). So an unknown group is DROPPED with a notice: the raw
    # attribution stays in the event log and in extras.iea_task, and adding the
    # task to groups.yaml brings it back on the next materialise.
    known_groups = config.group_names(root)
    groups: list[str] = []
    for task in sorted({config.canonical_group(task, root) for task in (effective.get("iea_task") or []) if task}):
        if not known_groups or task in known_groups:
            groups.append(task)
        else:
            message = (
                f"iea_task {task!r} is not in groups.yaml; the group was dropped from the "
                "record (the attribution is kept in extras.iea_task and in the event log)"
            )
            log.warning("%s: %s", resolved.identity_key, message)
            if notices is not None:
                notices.append(
                    {
                        "type": "unknown_group",
                        "identity_key": resolved.identity_key,
                        "group": task,
                        "message": message,
                    }
                )

    resources = []
    for resource in effective.get("resources") or []:
        if isinstance(resource, dict) and resource.get("url"):
            resources.append({k: v for k, v in resource.items() if v is not None})

    title = (effective.get("title") or "").strip() or resolved.identity_key

    package: dict[str, Any] = {
        "name": resolved.slug,
        "title": title,
        "notes": effective.get("notes") or "",
        "license_id": license_id,
        "tags": [{"name": tag} for tag in sorted(tags)],
        "extras": build_extras(resolved),
        "resources": resources,
        "groups": [{"name": group} for group in groups],
        "state": "active",
        "private": False,
    }
    # ADR-0021: a record CKAN would refuse is not CKAN-compatible, and CKAN
    # refuses an unowned dataset. infer_owner_org always returns a register
    # entry, so owner_org is present on every record (product-e2e-02).
    owner_org, _basis = infer_owner_org(effective, resolved.source_system, root)
    package["owner_org"] = owner_org
    if effective.get("url"):
        package["url"] = effective["url"]
    if effective.get("version"):
        package["version"] = str(effective["version"])
    return package


def dump_record(package: dict[str, Any]) -> str:
    """Serialise a record deterministically. Byte-identical across runs."""
    return (
        json.dumps(
            package,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ": "),
        )
        + "\n"
    )


def write_record(package: dict[str, Any], records_directory: Path) -> tuple[Path, bool]:
    """Write one record. Returns ``(path, changed)``; no write when unchanged."""
    records_directory.mkdir(parents=True, exist_ok=True)
    path = records_directory / f"{package['name']}.json"
    payload = dump_record(package)
    if path.exists() and path.read_text(encoding="utf-8") == payload:
        return path, False
    path.write_text(payload, encoding="utf-8")
    return path, True


def materialize_all(
    events_directory: Path | None = None,
    records_directory: Path | None = None,
    root: Path | None = None,
    prune: bool = True,
    validate: bool = True,
    identity_keys: Iterable[str] | None = None,
) -> MaterializeResult:
    """Replay every identity into ``records/``, then run the CKAN-compat gate.

    ``prune`` removes record files with no backing events — the only sanctioned
    deletion, and it can only ever fire for an identity whose events were
    removed by hand. Withdrawn identities keep their events and so keep their
    records.
    """
    events_directory = events_directory or config.events_dir(root)
    records_directory = records_directory or config.records_dir(root)
    result = MaterializeResult()

    seen_keys = list(identity_keys) if identity_keys is not None else list(
        iter_identity_keys(events_directory)
    )
    keys = list(dict.fromkeys(seen_keys))   # a key yielded twice is one record

    expected: set[str] = set()
    claimed: dict[str, str] = {}   # slug -> identity key, to catch collisions
    for identity_key in sorted(keys):
        resolved = resolve(identity_key, events_dir=events_directory)
        package = to_ckan_package(resolved, root=root, notices=result.notices)
        slug = package["name"]
        if slug in claimed and claimed[slug] != identity_key:
            # Two identities rendering to one slug would silently overwrite each
            # other's record file, and the per-file validator could never see it.
            result.violations.append(
                Violation(
                    record=slug,
                    field="name",
                    message=(
                        f"slug collision: identities {claimed[slug]!r} and "
                        f"{identity_key!r} both render to {slug!r}"
                    ),
                    path=str(records_directory / f"{slug}.json"),
                )
            )
            continue
        claimed[slug] = identity_key
        expected.add(slug)
        _, changed = write_record(package, records_directory)
        (result.written if changed else result.unchanged).append(package["name"])
        result.notices.extend(resolved.notices)
        extras = {extra["key"]: extra["value"] for extra in package["extras"]}
        if extras.get("license_mapped") == "false":
            result.unmapped_licenses.append(
                {
                    "identity_key": identity_key,
                    "name": package["name"],
                    "license_raw": extras.get("license_raw", ""),
                }
            )

    if prune and records_directory.exists():
        for path in sorted(records_directory.glob("*.json")):
            if path.stem not in expected:
                log.warning("pruning orphaned record with no events: %s", path.name)
                path.unlink()
                result.pruned.append(path.stem)

    # A line the event log could not read is a fact about this run, not a
    # reason to abort it (eventlog-02): it is reported here and in the run
    # report, and every other identity still materialises.
    result.notices.extend(log_problems())

    if validate:
        result.violations.extend(validate_records(records_directory, root=root))
    return result
