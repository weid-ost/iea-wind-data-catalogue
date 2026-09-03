"""Repository paths and the YAML registers.

Every path is resolved relative to the repository root, which is the parent of
this package unless ``$HARVEST_ROOT`` overrides it. Tests override it with a
``tmp_path`` so nothing in this module ever writes into the real repo.

Everything the harvest reads or writes lives under ``data/`` — the catalogue's
whole state, in one subtree, separable from the code that produces it. The
registers (``sources.yaml`` and friends) stay at the root: they are hand-edited
configuration, not harvested data.

Nothing here does I/O at import time.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "repo_root",
    "data_dir",
    "events_dir",
    "records_dir",
    "cache_dir",
    "state_dir",
    "annotations_dir",
    "fixtures_dir",
    "sources_path",
    "organizations_path",
    "groups_path",
    "scheming_path",
    "last_run_path",
    "pending_extraction_path",
    "load_yaml",
    "load_sources",
    "load_organizations",
    "load_groups",
    "organization_names",
    "group_names",
    "group_aliases",
    "canonical_group",
]


def repo_root() -> Path:
    """The repository root. ``$HARVEST_ROOT`` wins if set."""
    override = os.environ.get("HARVEST_ROOT")
    if override:
        return Path(override).resolve()
    return Path(__file__).resolve().parent.parent


def data_dir(root: Path | None = None) -> Path:
    """``data/`` — every directory the harvest reads or writes, and nothing else.

    One subtree for the whole catalogue state, so that "what is the data" and
    "what is the code" are answerable by looking at the top level.
    """
    return (root or repo_root()) / "data"


def events_dir(root: Path | None = None) -> Path:
    """``data/events/`` — append-only JSONL, one file per identity (the truth)."""
    return data_dir(root) / "events"


def records_dir(root: Path | None = None) -> Path:
    """``data/records/`` — derived CKAN package dicts, one JSON per record."""
    return data_dir(root) / "records"


def cache_dir(root: Path | None = None) -> Path:
    """``data/cache/`` — committed LLM extraction cache (ADR-0025)."""
    return data_dir(root) / "cache"


def state_dir(root: Path | None = None) -> Path:
    """``data/state/`` — run report and the pending-extraction queue."""
    return data_dir(root) / "state"


def annotations_dir(root: Path | None = None) -> Path:
    """``data/annotations/`` — local additions only; source is never edited."""
    return data_dir(root) / "annotations"


def fixtures_dir(root: Path | None = None) -> Path:
    """``data/fixtures/`` — the test and gallery fixture set."""
    return data_dir(root) / "fixtures"


def sources_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "sources.yaml"


def organizations_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "organizations.yaml"


def groups_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "groups.yaml"


def scheming_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "schema" / "ckan-scheming.json"


def last_run_path(root: Path | None = None) -> Path:
    return state_dir(root) / "last-run.json"


def pending_extraction_path(root: Path | None = None) -> Path:
    return state_dir(root) / "pending-extraction.json"


def load_yaml(path: Path) -> Any:
    """Load a YAML document, returning ``{}`` for a missing or empty file."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


@functools.lru_cache(maxsize=32)
def _cached_yaml(path_str: str, mtime: float, size: int) -> Any:
    return load_yaml(Path(path_str))


def _load_register(path: Path) -> Any:
    """Load a register, cached on (path, mtime, size) so an edit invalidates it."""
    try:
        stat = path.stat()
    except OSError:
        return {}
    return _cached_yaml(str(path), stat.st_mtime, stat.st_size)


def load_sources(root: Path | None = None) -> dict[str, dict]:
    """``sources.yaml`` -> ``{source_name: config}``."""
    doc = _load_register(sources_path(root))
    return dict(doc.get("sources", {}))


def load_organizations(root: Path | None = None) -> list[dict]:
    """``organizations.yaml`` -> list of CKAN-shaped organization dicts."""
    doc = _load_register(organizations_path(root))
    return list(doc.get("organizations", []))


def load_groups(root: Path | None = None) -> list[dict]:
    """``groups.yaml`` -> list of CKAN-shaped group dicts (= IEA Wind tasks)."""
    doc = _load_register(groups_path(root))
    return list(doc.get("groups", []))


def organization_names(root: Path | None = None) -> set[str]:
    return {str(org["name"]) for org in load_organizations(root) if org.get("name")}


def group_names(root: Path | None = None) -> set[str]:
    return {str(grp["name"]) for grp in load_groups(root) if grp.get("name")}


def group_aliases(root: Path | None = None) -> dict[str, str]:
    """``{alias: canonical_group_name}`` — the task renumbering map (fixture iea-08).

    IEA Wind renumbered several tasks (19 -> 54, 34 -> 59). Both numbers appear
    in the wild, on the same page, so both must resolve to one group.
    """
    aliases: dict[str, str] = {}
    for grp in load_groups(root):
        name = str(grp.get("name", ""))
        for alias in grp.get("aliases", []) or []:
            aliases[str(alias)] = name
    return aliases


def canonical_group(name: str, root: Path | None = None) -> str:
    """Resolve a task group name or alias to its canonical group name."""
    name = str(name).strip().lower()
    if name in group_names(root):
        return name
    return group_aliases(root).get(name, name)
