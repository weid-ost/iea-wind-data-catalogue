"""The adapter contract.

An adapter does exactly two things, and deliberately not a third:

``harvest(max_records) -> Iterable[RawObservation]``
    Talk to the source. Yield what it said, **verbatim**, wrapped with the
    identifiers and the change token. No interpretation, no cleaning.

``map(raw) -> MappedObservation``
    Interpret one raw payload into the ``source.*`` namespace plus per-field
    provenance. **Pure** — no network, no clock, no filesystem. This is what
    the fixture tests call, and it is why they can run offline.

It does not write events, materialise records, resolve DOIs against the
network, or decide whether something changed. :func:`run_adapter` does all of
that, once, for every adapter, so those rules cannot be implemented
inconsistently seven times.

**Three rules an adapter must not break.**

1. **``max_records`` is honoured.** ``harvest(max_records)`` yields at most that many
   observations. The default is :data:`harvest.DEFAULT_MAX_RECORDS`.
2. **Change detection is the source key** (ADR-0026). The adapter chooses the
   token and owns its semantics; ``run_adapter`` compares it and skips
   unchanged records **writing no event at all**. Where no trustworthy token
   exists, use :func:`payload_hash` — the universal fallback.
3. **It degrades, it does not crash.** Network failure, auth wall, schema
   change, 500 from upstream: log it, let :func:`run_adapter` mark the source
   unreachable in the run report, and let the other six sources finish
   (fixtures ``wdh-07``, ``iea-12``). Raising out of ``run_adapter`` is a bug.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator

from harvest import DEFAULT_MAX_RECORDS
from harvest import config as _config
from harvest.events import has_changed, record_scrape
from harvest.models import MappedObservation, RawObservation

__all__ = [
    "ADAPTERS",
    "AdapterError",
    "SourceUnreachable",
    "SourceConfig",
    "SourceResult",
    "Adapter",
    "register",
    "get_adapter",
    "available_adapters",
    "load_adapters",
    "payload_hash",
    "run_adapter",
]

log = logging.getLogger(__name__)

#: The adapter registry, keyed by ``source_name`` (= the key in ``sources.yaml``).
ADAPTERS: dict[str, type["Adapter"]] = {}


class AdapterError(Exception):
    """A recoverable adapter failure. Caught by :func:`run_adapter`."""


class SourceUnreachable(AdapterError):
    """The source could not be reached or refused us (network, auth, robots)."""


def payload_hash(payload: Any) -> str:
    """The universal source-key fallback: a hash of the normalised payload.

    Deterministic across runs and interpreters: JSON with sorted keys and no
    insignificant whitespace, SHA-256, first 16 hex characters. Use it when the
    source offers no trustworthy revision or updated-at field (plan §4.1).
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass
class SourceConfig:
    """One entry from ``sources.yaml``, as the adapter receives it."""

    name: str
    enabled: bool = True
    tier: int = 1
    precedence: int | None = None
    source_key: str = ""            # human description of the key's semantics
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, name: str, mapping: dict[str, Any] | None) -> "SourceConfig":
        mapping = dict(mapping or {})
        known = {"enabled", "tier", "precedence", "source_key"}
        return cls(
            name=name,
            enabled=bool(mapping.get("enabled", True)),
            tier=int(mapping.get("tier", 1)),
            precedence=mapping.get("precedence"),
            source_key=str(mapping.get("source_key", "")),
            options={k: v for k, v in mapping.items() if k not in known},
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


@dataclass
class SourceResult:
    """One source's contribution to ``state/last-run.json``."""

    source: str
    enabled: bool = True
    reachable: bool = True
    implemented: bool = True
    seen: int = 0
    changed: int = 0
    skipped_unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    identity_keys: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reachable": self.reachable,
            "implemented": self.implemented,
            "seen": self.seen,
            "changed": self.changed,
            "skipped_unchanged": self.skipped_unchanged,
            "errors": self.errors,
        }


class Adapter(ABC):
    """Base class for every source adapter."""

    #: Must equal the module name and the ``sources.yaml`` key.
    source_name: ClassVar[str] = ""
    #: 1 = structured API (deterministic, never LLM). 3 = HTML + extraction.
    tier: ClassVar[int] = 1
    #: One line describing the change token, for the run report and the docs.
    source_key_semantics: ClassVar[str] = "normalised payload hash"

    def __init__(self, config: SourceConfig | None = None, client: Any = None) -> None:
        self.config = config or SourceConfig(name=self.source_name)
        self.client = client

    # -- the two methods every adapter implements --------------------------
    @abstractmethod
    def harvest(self, max_records: int = DEFAULT_MAX_RECORDS) -> Iterable[RawObservation]:
        """Yield at most ``max_records`` raw observations from the source."""

    @abstractmethod
    def map(self, raw: RawObservation) -> MappedObservation:
        """Map one raw payload to the source namespace. Pure; offline; total."""

    # -- optional hooks ----------------------------------------------------
    def close(self) -> None:
        """Release any client the adapter opened. Default: nothing."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} source={self.source_name!r} tier={self.tier}>"


def register(cls: type[Adapter]) -> type[Adapter]:
    """Class decorator that adds an adapter to :data:`ADAPTERS`."""
    if not cls.source_name:
        raise ValueError(f"{cls.__name__} must set source_name")
    existing = ADAPTERS.get(cls.source_name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"two adapters claim source_name {cls.source_name!r}: "
            f"{existing.__module__}.{existing.__name__} and {cls.__module__}.{cls.__name__}"
        )
    ADAPTERS[cls.source_name] = cls
    return cls


def load_adapters(names: Iterable[str] | None = None, root: Path | None = None) -> dict[str, type[Adapter]]:
    """Import the adapter module for every source in ``sources.yaml``.

    A module that fails to import is logged and skipped — one broken adapter
    must not stop the other six.
    """
    wanted = list(names) if names is not None else list(_config.load_sources(root))
    for name in wanted:
        try:
            importlib.import_module(f"harvest.adapters.{name}")
        except Exception as exc:  # pragma: no cover - defensive
            log.error("adapter module harvest.adapters.%s failed to import: %s", name, exc)
    return dict(ADAPTERS)


def get_adapter(name: str) -> type[Adapter]:
    """Look an adapter up by source name, importing its module if needed."""
    if name not in ADAPTERS:
        load_adapters([name])
    try:
        return ADAPTERS[name]
    except KeyError:
        raise AdapterError(
            f"no adapter registered for source {name!r}; "
            f"known sources: {sorted(ADAPTERS) or '(none)'}"
        ) from None


def available_adapters() -> dict[str, type[Adapter]]:
    return dict(ADAPTERS)


def _iter_capped(iterable: Iterable[RawObservation], max_records: int) -> Iterator[RawObservation]:
    """Enforce the cap even if an adapter forgets to."""
    for index, item in enumerate(iterable):
        if index >= max_records:
            log.warning("adapter yielded more than max_records=%s; truncating", max_records)
            return
        yield item


def run_adapter(
    adapter: Adapter,
    max_records: int = DEFAULT_MAX_RECORDS,
    events_dir: Path | None = None,
    dry_run: bool = False,
) -> SourceResult:
    """Harvest one source: map, detect change, append events. Never raises.

    Every failure mode becomes a line in the returned :class:`SourceResult`,
    which becomes a line in ``state/last-run.json``. That is the whole
    degradation story (fixture ``wdh-07``).
    """
    result = SourceResult(source=adapter.source_name, enabled=adapter.config.enabled)

    if not adapter.config.enabled:
        log.info("source %s disabled in sources.yaml; skipping", adapter.source_name)
        return result

    try:
        observations = _iter_capped(adapter.harvest(max_records=max_records), max_records)
        for raw in observations:
            result.seen += 1
            try:
                mapped = adapter.map(raw)
            except NotImplementedError:
                raise
            except Exception as exc:
                log.exception("map() failed for %s:%s", raw.source_system, raw.source_id)
                result.errors.append(f"map failed for {raw.source_system}:{raw.source_id}: {exc}")
                continue

            if not has_changed(
                mapped.identity_key, mapped.source_system, mapped.source_key, events_dir
            ):
                result.skipped_unchanged += 1
                continue  # ADR-0026: unchanged writes NO event

            if not dry_run:
                try:
                    record_scrape(
                        identity_key=mapped.identity_key,
                        source_system=mapped.source_system,
                        source_id=mapped.source_id,
                        source_key=mapped.source_key,
                        source=mapped.source.model_dump(mode="json", exclude_none=True),
                        provenance=mapped.provenance,
                        events_dir=events_dir,
                    )
                except ValueError as exc:
                    # e.g. a slug collision. One record's problem, not the run's.
                    log.error("could not record %s: %s", mapped.identity_key, exc)
                    result.errors.append(f"{mapped.identity_key}: {exc}")
                    continue
            result.changed += 1
            result.identity_keys.append(mapped.identity_key)

    except NotImplementedError as exc:
        result.implemented = False
        result.errors.append(f"not implemented: {exc}")
        log.warning("adapter %s is a stub: %s", adapter.source_name, exc)
    except SourceUnreachable as exc:
        result.reachable = False
        result.errors.append(str(exc))
        log.warning("source %s unreachable: %s", adapter.source_name, exc)
    except Exception as exc:  # the run must survive anything an adapter does
        result.reachable = False
        result.errors.append(f"{type(exc).__name__}: {exc}")
        log.exception("adapter %s failed", adapter.source_name)
    finally:
        try:
            adapter.close()
        except Exception:  # pragma: no cover - defensive
            log.debug("adapter %s close() failed", adapter.source_name, exc_info=True)

    return result
