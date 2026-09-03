"""``state/last-run.json`` — the heartbeat, and the only run-level output.

It does two jobs at once, and the second is the reason it is non-negotiable:

1. It is the **freshness banner** the site renders. "Last updated: *date*",
   styled as a warning past 45 days (fixture ``r-08``), plus the pending
   extraction backlog and any unreachable sources. Nobody checks a CI
   dashboard for a dormant project; a stale banner on the front page is seen
   by whoever next visits.

2. It is written **on every run, even a complete no-op**, so every run
   produces a commit. GitHub disables scheduled workflows after 60 days with
   no repository activity and only commits count. This file is the keepalive
   (plan §3.3) — implemented inline rather than by adding a Marketplace action
   with write permissions to the supply chain.

So: write it last, write it always, write it even when the harvest failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from harvest import __version__, config
from harvest.models import utcnow

__all__ = ["RunReport", "write_run_report", "read_run_report"]


@dataclass
class RunReport:
    """The run report. Every field ends up in ``state/last-run.json``."""

    started_at: str = field(default_factory=utcnow)
    finished_at: str | None = None
    harvest_version: str = __version__
    max_records: int | None = None
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    unreachable_sources: list[str] = field(default_factory=list)
    records_total: int = 0
    records_written: int = 0
    records_unchanged: int = 0
    records_pruned: int = 0
    events_appended: int = 0
    notices: list[dict] = field(default_factory=list)
    dropped_dois: list[dict] = field(default_factory=list)
    unmapped_licenses: list[dict] = field(default_factory=list)
    cache_hits: int = 0
    cache_misses: int = 0
    pending_extraction: int = 0
    validation_violations: list[str] = field(default_factory=list)
    ok: bool = True

    # -- assembly ----------------------------------------------------------
    def add_source(self, name: str, result: Any) -> None:
        """Record one source's outcome. Accepts a ``SourceResult`` or a dict."""
        payload = result.as_dict() if hasattr(result, "as_dict") else dict(result)
        self.sources[name] = payload
        self.events_appended += int(payload.get("changed", 0) or 0)
        if payload.get("enabled", True) and not payload.get("reachable", True):
            if name not in self.unreachable_sources:
                self.unreachable_sources.append(name)

    def add_notices(self, notices: Iterable[Mapping[str, Any]]) -> None:
        for notice in notices:
            self.notices.append(dict(notice))

    @property
    def cache_hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return round(self.cache_hits / total, 4) if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at or utcnow(),
            "harvest_version": self.harvest_version,
            "max_records": self.max_records,
            "ok": self.ok,
            "sources": {name: self.sources[name] for name in sorted(self.sources)},
            "unreachable_sources": sorted(self.unreachable_sources),
            "records": {
                "total": self.records_total,
                "written": self.records_written,
                "unchanged": self.records_unchanged,
                "pruned": self.records_pruned,
            },
            "events_appended": self.events_appended,
            "notices": self.notices,
            "dropped_dois": self.dropped_dois,
            "unmapped_licenses": self.unmapped_licenses,
            "cache": {
                "hits": self.cache_hits,
                "misses": self.cache_misses,
                "hit_rate": self.cache_hit_rate,
            },
            "pending_extraction": self.pending_extraction,
            "validation_violations": self.validation_violations,
        }

    def to_json(self) -> str:
        return (
            json.dumps(
                self.as_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ": "),
            )
            + "\n"
        )

    def write(self, path: Path | None = None, root: Path | None = None) -> Path:
        """Write ``state/last-run.json``. Call this even when the run failed."""
        target = path or config.last_run_path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        return target


def write_run_report(report: RunReport, path: Path | None = None,
                     root: Path | None = None) -> Path:
    return report.write(path, root)


def read_run_report(path: Path | None = None, root: Path | None = None) -> dict[str, Any]:
    """Read the last run report, or ``{}`` if there has never been a run."""
    target = path or config.last_run_path(root)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))
