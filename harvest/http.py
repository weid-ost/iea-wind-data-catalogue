"""HTTP etiquette: one client, one User-Agent, robots respected.

Every outbound request in this project goes through here. The rules are not
optional — this catalogue harvests other people's servers on a schedule and
must be a good citizen about it:

* a **descriptive User-Agent with a contact address**, so an operator who
  dislikes what we are doing can reach a human rather than a firewall rule;
* **robots.txt is respected**, cached per host for the run;
* **conditional GETs** — ETag and Last-Modified are stored per URL in the
  fetch state, so an unchanged page costs a 304 and no body;
* **metadata and links only.** The catalogue never mirrors a file. If you find
  yourself writing bytes from a resource URL to disk, stop.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from harvest import USER_AGENT

__all__ = [
    "USER_AGENT",
    "DEFAULT_TIMEOUT",
    "FetchResult",
    "RobotsCache",
    "HarvestClient",
    "build_client",
]

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


def build_client(timeout: float = DEFAULT_TIMEOUT, **kwargs: Any) -> httpx.Client:
    """An ``httpx.Client`` with the project's headers and redirect policy."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
        **kwargs.pop("headers", {}),
    }
    return httpx.Client(headers=headers, timeout=timeout, follow_redirects=True, **kwargs)


@dataclass
class FetchResult:
    """The outcome of one conditional GET."""

    url: str
    status_code: int | None
    changed: bool
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and self.status_code < 400

    def json(self) -> Any:
        import json as _json

        return _json.loads(self.text)


class RobotsCache:
    """Per-host ``robots.txt``, fetched once per run.

    A host we cannot reach robots.txt for is treated as **allowed** — the
    convention when robots.txt is absent — but a host that serves a robots.txt
    disallowing us is obeyed without argument.
    """

    def __init__(self, client: httpx.Client | None = None, user_agent: str = USER_AGENT) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _parser(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host in self._parsers:
            return self._parsers[host]
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        parser: urllib.robotparser.RobotFileParser | None = None
        try:
            client = self._client or build_client()
            response = client.get(robots_url, timeout=10.0)
            if response.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(response.text.splitlines())
        except Exception as exc:
            log.info("robots.txt unavailable for %s (%s); assuming allowed", host, exc)
        self._parsers[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parser = self._parser(url)
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)


class HarvestClient:
    """A polite HTTP client: robots-aware, rate-limited, conditional.

    ``fetch_state`` is a mutable ``{url: {"etag": ..., "last_modified": ...}}``
    map that an adapter may persist between runs to make its GETs conditional.
    Nothing here writes to disk.
    """

    def __init__(
        self,
        client: httpx.Client | None = None,
        robots: RobotsCache | None = None,
        min_interval: float = 0.2,
        respect_robots: bool = True,
        fetch_state: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._client = client or build_client()
        self._robots = robots if robots is not None else RobotsCache(self._client)
        self._min_interval = min_interval
        self._respect_robots = respect_robots
        self._last_request = 0.0
        self.fetch_state: dict[str, dict[str, str]] = fetch_state if fetch_state is not None else {}

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str, **kwargs: Any) -> FetchResult:
        """Conditional, robots-respecting GET. Never raises on transport error."""
        if self._respect_robots and not self._robots.allowed(url):
            log.warning("robots.txt disallows %s; skipping", url)
            return FetchResult(url=url, status_code=None, changed=False,
                               error="disallowed-by-robots")

        headers = dict(kwargs.pop("headers", {}))
        cached = self.fetch_state.get(url, {})
        if cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = cached["last_modified"]

        self._throttle()
        try:
            response = self._client.get(url, headers=headers, **kwargs)
        except Exception as exc:
            log.warning("fetch failed for %s: %s", url, exc)
            return FetchResult(url=url, status_code=None, changed=False, error=str(exc))

        state: dict[str, str] = {}
        if response.headers.get("ETag"):
            state["etag"] = response.headers["ETag"]
        if response.headers.get("Last-Modified"):
            state["last_modified"] = response.headers["Last-Modified"]
        if state:
            self.fetch_state[url] = state

        if response.status_code == 304:
            return FetchResult(url=url, status_code=304, changed=False,
                               headers=dict(response.headers))
        return FetchResult(
            url=url,
            status_code=response.status_code,
            changed=True,
            text=response.text,
            headers=dict(response.headers),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HarvestClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
