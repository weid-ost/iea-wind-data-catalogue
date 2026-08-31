"""Link rot — the failure mode a catalogue exists to fight.

Every materialised record carries outbound URLs: its landing page, one
``source_url`` per contributing system, and its resource links. This module
checks them with the same etiquette every other request in the project uses
(:mod:`harvest.http`: descriptive User-Agent, robots.txt, throttling), and puts
the result where a human will see it.

**Nothing here deletes, withdraws or edits a record.** A 404 on a task page
means the *page* moved, not that the artifact stopped existing — plan §4.4 and
[[adr-0027-withdrawn-records-are-retained]] are explicit that disappearance is a
``withdrawn`` event raised by an adapter that looked at the artifact, never an
inference drawn from an HTTP status by a link checker. Fixture ``iea-12`` is the
shape of it: *existing records retained; source marked unreachable*.

**Why the result is not written into ``records/``.** Records are byte-stable by
contract (``harvest/CONTRACT.md`` §7): a run in which nothing changed produces
no diff. HTTP status is the least stable thing in the system — one flaky 503
would rewrite a record, and a weekly run would churn ``records/`` forever for no
change in what any source said. So link status lives in
``state/link-check.json`` (keyed by record name, for the site to read) and in
the run report's ``notices``, which is the curator's short monthly list.

``check_records`` takes an injected client, which is why the whole test suite
runs offline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

from harvest import config
from harvest.models import utcnow

__all__ = [
    "LinkStatus",
    "LinkCheckReport",
    "urls_for_record",
    "check_url",
    "check_urls",
    "check_records",
    "write_link_report",
    "read_link_report",
    "link_report_path",
]

log = logging.getLogger(__name__)

#: Statuses that mean "gone", as opposed to "we were rate-limited today".
_GONE = (404, 410)


@dataclass(frozen=True)
class LinkStatus:
    """The outcome of checking one URL."""

    url: str
    status_code: int | None = None
    ok: bool = False
    method: str = "GET"
    error: str | None = None
    checked_at: str = ""

    @property
    def gone(self) -> bool:
        """A definite 404/410, as opposed to a transient failure."""
        return self.status_code in _GONE

    @property
    def skipped(self) -> bool:
        return self.error == "disallowed-by-robots"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": self.url, "ok": self.ok, "method": self.method}
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.error:
            payload["error"] = self.error
        if self.checked_at:
            payload["checked_at"] = self.checked_at
        return payload


@dataclass
class LinkCheckReport:
    """Every link checked, and which records the broken ones belong to."""

    checked: dict[str, LinkStatus] = field(default_factory=dict)
    records: dict[str, list[str]] = field(default_factory=dict)   # record -> its urls
    started_at: str = field(default_factory=utcnow)
    finished_at: str | None = None

    # -- views -------------------------------------------------------------
    @property
    def dead(self) -> list[LinkStatus]:
        return sorted(
            (s for s in self.checked.values() if not s.ok and not s.skipped),
            key=lambda s: s.url,
        )

    @property
    def dead_urls(self) -> set[str]:
        return {status.url for status in self.dead}

    def dead_by_record(self) -> dict[str, list[str]]:
        dead = self.dead_urls
        return {
            name: sorted(url for url in urls if url in dead)
            for name, urls in sorted(self.records.items())
            if any(url in dead for url in urls)
        }

    def unreachable_hosts(self) -> list[str]:
        """Hosts every one of whose checked URLs failed — a source, not a page."""
        by_host: dict[str, list[LinkStatus]] = {}
        for status in self.checked.values():
            if status.skipped:
                continue
            by_host.setdefault(urlsplit(status.url).netloc, []).append(status)
        return sorted(
            host
            for host, statuses in by_host.items()
            if host and all(not status.ok for status in statuses)
        )

    def as_notices(self) -> list[dict]:
        """Dead links, shaped for ``state/last-run.json`` → ``notices``."""
        by_record = self.dead_by_record()
        notices: list[dict] = []
        for name, urls in by_record.items():
            for url in urls:
                status = self.checked[url]
                notices.append(
                    {
                        "type": "dead_link",
                        "record": name,
                        "url": url,
                        "status_code": status.status_code,
                        "error": status.error,
                        "gone": status.gone,
                        "action": "record retained; source link unreachable (ADR-0027)",
                    }
                )
        for host in self.unreachable_hosts():
            notices.append(
                {
                    "type": "source_unreachable",
                    "host": host,
                    "action": "every checked link on this host failed; records retained",
                }
            )
        return notices

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at or utcnow(),
            "checked": len(self.checked),
            "dead": [status.as_dict() for status in self.dead],
            "dead_by_record": self.dead_by_record(),
            "unreachable_hosts": self.unreachable_hosts(),
            "records": {name: sorted(urls) for name, urls in sorted(self.records.items())},
        }

    def summary(self) -> str:
        return (
            f"linkcheck: {len(self.checked)} url(s) checked, {len(self.dead)} dead, "
            f"{len(self.dead_by_record())} record(s) affected"
        )


# ---------------------------------------------------------------------------
# Which URLs a record exposes
# ---------------------------------------------------------------------------


def urls_for_record(package: dict[str, Any]) -> list[str]:
    """Every outbound URL on a record: landing page, source URLs, resources.

    Order-preserving and de-duplicated, so a record whose ``url`` is also its
    only ``source_url`` costs one request, not two.
    """
    urls: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            if value not in urls:
                urls.append(value)

    add(package.get("url"))
    extras = {
        extra.get("key"): extra.get("value")
        for extra in package.get("extras", [])
        if isinstance(extra, dict)
    }
    for key in ("source_url", "source_urls", "local_links"):
        raw = extras.get(key)
        if not raw:
            continue
        if key == "source_url":
            add(raw)
            continue
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for item in decoded if isinstance(decoded, list) else []:
            add(item.get("url") if isinstance(item, dict) else item)

    for resource in package.get("resources", []):
        if isinstance(resource, dict):
            add(resource.get("url"))
    return urls


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def check_url(url: str, client: Any) -> LinkStatus:
    """Check one URL. HEAD where the client offers it, GET otherwise.

    Never raises: a transport error is a result, not an exception, exactly as in
    :class:`harvest.http.HarvestClient`.
    """
    method = "GET"
    result: Any
    if hasattr(client, "head"):
        method = "HEAD"
        try:
            result = client.head(url)
        except Exception as exc:  # pragma: no cover - defensive
            return LinkStatus(url=url, error=str(exc), method=method, checked_at=utcnow())
        status = getattr(result, "status_code", None)
        # Plenty of servers refuse HEAD outright; fall back rather than call it dead.
        if status in (403, 405, 501) or status is None:
            method = "GET"
        else:
            return LinkStatus(
                url=url,
                status_code=status,
                ok=bool(status) and status < 400,
                method="HEAD",
                error=getattr(result, "error", None),
                checked_at=utcnow(),
            )

    try:
        result = client.get(url)
    except Exception as exc:
        return LinkStatus(url=url, error=str(exc), method=method, checked_at=utcnow())

    status = getattr(result, "status_code", None)
    error = getattr(result, "error", None)
    ok = getattr(result, "ok", None)
    if ok is None:
        ok = error is None and status is not None and status < 400
    return LinkStatus(
        url=url,
        status_code=status,
        ok=bool(ok),
        method=method,
        error=error,
        checked_at=utcnow(),
    )


def check_urls(urls: Iterable[str], client: Any) -> dict[str, LinkStatus]:
    """Check many URLs, once each, in order."""
    results: dict[str, LinkStatus] = {}
    for url in urls:
        if url in results:
            continue
        results[url] = check_url(url, client)
    return results


def check_records(
    records_directory: Path | None = None,
    client: Any = None,
    root: Path | None = None,
    limit: int | None = None,
    records: Sequence[dict[str, Any]] | None = None,
) -> LinkCheckReport:
    """Check every URL on every materialised record.

    ``limit`` caps the number of *records* examined, mirroring the prototype cap
    on the harvest itself: a link check that hammers seven upstreams is no more
    welcome than a harvest that does.
    """
    report = LinkCheckReport()
    if records is None:
        directory = records_directory or config.records_dir(root)
        records = []
        if directory.exists():
            for path in sorted(directory.glob("*.json")):
                try:
                    package = json.loads(path.read_text(encoding="utf-8"))
                except ValueError as exc:
                    log.error("unreadable record %s: %s", path.name, exc)
                    continue
                if isinstance(package, dict):
                    records.append(package)

    if client is None:  # pragma: no cover - the network path
        from harvest.http import HarvestClient

        client = HarvestClient()

    for index, package in enumerate(records):
        if limit is not None and index >= limit:
            log.info("linkcheck stopped at the record limit of %s", limit)
            break
        name = str(package.get("name") or f"record-{index}")
        urls = urls_for_record(package)
        report.records[name] = urls
        for url, status in check_urls(urls, client).items():
            report.checked.setdefault(url, status)

    report.finished_at = utcnow()
    log.info("%s", report.summary())
    return report


# ---------------------------------------------------------------------------
# The state file
# ---------------------------------------------------------------------------


def link_report_path(root: Path | None = None) -> Path:
    """``state/link-check.json``."""
    return config.state_dir(root) / "link-check.json"


def write_link_report(
    report: LinkCheckReport,
    path: Path | None = None,
    root: Path | None = None,
) -> Path:
    target = path or link_report_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True, ensure_ascii=False,
                   separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )
    return target


def read_link_report(path: Path | None = None, root: Path | None = None) -> dict[str, Any]:
    target = path or link_report_path(root)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))
