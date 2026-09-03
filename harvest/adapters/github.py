"""GitHub adapter — **owner: Track D (github)**.

Source
    ``https://api.github.com``. Three discovery paths, in this order:

    1. **Org enumeration** — ``GET /orgs/{login}/repos`` for every org in
       ``sources.yaml`` ``github.orgs``. All three configured logins were
       re-verified live on 2026-08-31: ``IEAWindSystems`` (12 public repos),
       ``IEA-Task-43`` (7) and ``IEAWindTask37`` (1). ``IEAWindTask52`` was
       verified too and added.
    2. **Explicit repositories** — ``github.repos``, fetched by path. This is
       the path that follows GitHub's rename/transfer 301 redirect, and it is
       also where code living in an individual's account goes (``gh-08``).
    3. **Topic search** — ``GET /search/repositories?q=topic:...`` for every
       topic in ``github.topics``. Reached only when 1 and 2 have not already
       filled the run's ``max_records``, which they usually do; it
       exists so the discovery surface is real rather than aspirational, and
       it is exercised offline in ``tests/test_github.py``.

    Unauthenticated the core API allows 60 requests/hour, which is enough at
    the five-record cap. ``$GITHUB_TOKEN`` (the Actions token, already present
    in the workflow) raises that to 5,000 and is used when set. A rate-limit
    403/429 is **not** a crash: it raises :class:`SourceUnreachable` and the
    source disables itself for the run.

Source key (plan §4.1, ADR-0026)
    A composite, because no single trustworthy field exists::

        "<default-branch head SHA>:<latest release tag>:<hash(description, topics, licence)>"

    ``:`` is illegal in a git ref name, so it is an unambiguous separator.
    ``pushed_at``, ``updated_at``, star and fork counts are **deliberately
    excluded** — including any of them would make every run a change event and
    turn append-on-change into append-always.

    *Known limitation.* ADR-0026 fixes the hashed fields at description,
    topics and licence, so archiving a repository that receives no further
    commits and cuts no further release does not by itself move the key. The
    archived flag lands with the next real change. Widening the hash is an
    ADR amendment, not an adapter decision.

Identity
    1. The **concept DOI behind the README's Zenodo badge**, when one
       resolves (``gh-02``). Badges usually carry a *version* DOI —
       ``IEAWindSystems/windIO``'s badge points at ``10.5281/zenodo.15191297``,
       which DataCite declares ``IsVersionOf 10.5281/zenodo.15191296`` — so the
       badge DOI is resolved, its ``IsVersionOf`` relation followed once, and
       the **concept** DOI becomes the identity. That is the same rule Zenodo
       follows (``zen-02``), which is exactly what makes the DOI a free join
       key between the code and the archived release.
    2. Otherwise ``github|<owner>/<repo>``.

    **Resolve-or-drop applies to badges too** (``gh-03``): a badge pointing at
    a DOI that no longer resolves is dropped and logged, and the repo falls
    back to rule 2. No identifier enters a record unresolved.

    **Renames and transfers** (``gh-07``). GitHub 301-redirects the old path,
    so the artifact is never lost — but the redirect resolves to the *new*
    ``full_name``, which would mint a second identity and a second URL. So the
    adapter looks the immutable numeric repository id up in the event log and
    yields the observation under its **first-seen** path. The identity key,
    the slug, the record filename and the citable URL all survive the rename;
    ``source.url`` and ``source.extra.github_full_name`` show the new name.

Exclusions
    * **Forks**, by default (``gh-04``) — otherwise you catalogue fifty copies
      of a reference turbine. ``exclude_forks: false`` turns it off.
    * **Below the content threshold** (``gh-10``): a template repo, a repo
      whose git size is under ``min_content_bytes``, or one with neither a
      description nor a README. ``IEAWindTask37/.github`` is the live example.
    * Archived repos are **marked and retained**, never excluded (``gh-05``):
      ``source.archived`` is true and the record materialises with
      ``extras.lifecycle_state: archived``.

Licensing
    ``license: null`` means *no licence stated*, and that is what the record
    says: ``license_id: notspecified`` with no ``license_raw`` (``gh-06``).
    Never inferred, never defaulted to something open. ``NOASSERTION`` — a
    licence GitHub detected but could not name — maps to ``notspecified`` with
    ``license_mapped: false``, so the run report flags it.

Granularity
    **One record per repository** (``gh-09``). A monorepo holding several
    distinct artifacts becomes one record. That is a documented v1 limitation,
    not a bug: splitting it needs a per-artifact identity the repository does
    not provide.

Payload envelope
    A GitHub "record" is four endpoints, so ``RawObservation.payload`` is an
    envelope. ``repository``, ``head_ref``, ``latest_release`` and ``readme``
    are the verbatim responses (``null`` where the endpoint 404s). ``_resolved``
    is the one adapter-derived block, and it exists because the contract
    requires DOI resolution to happen in ``harvest()`` while ``map()`` must
    stay pure — it is the only channel between the two.

Fixtures owned
    ``gh-01`` .. ``gh-10``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable, Iterator

from harvest import DEFAULT_MAX_RECORDS
from harvest import config as _config
from harvest.adapters.base import Adapter, SourceUnreachable, payload_hash, register
from harvest.doi import DoiDropLog, extract_dois, normalise_doi, resolve_or_drop
from harvest.http import HarvestClient, build_client
from harvest.identity import identity_key
from harvest.licenses import map_license
from harvest.models import (
    FieldProvenance,
    MappedObservation,
    RawObservation,
    SourceNamespace,
)
from harvest.sanitize import sanitize_html

__all__ = [
    "GitHubAdapter",
    "GITHUB_API",
    "BADGE_MARKER",
    "IMAGE_SUFFIXES",
    "readme_text",
    "badge_doi_candidates",
    "source_key_for",
    "content_bytes",
    "exclusion_reason",
    "concept_doi_of",
    "known_repository_paths",
]

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

#: The substring that scopes DOI extraction to an actual Zenodo badge. A
#: README may cite half a dozen unrelated DOIs; only the badge is a claim about
#: *this* repository.
BADGE_MARKER = "zenodo.org/badge"

#: A Zenodo badge's image URL is the DOI with an image extension glued on
#: (``.../10.5281/zenodo.15191297.svg``). Stripping it is not a second DOI
#: regex — it is one suffix removal before ``harvest.doi`` sees the candidate.
IMAGE_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".gif")

#: Markdown and reStructuredText wrap a badge in brackets and parentheses, and
#: the DOI character set legitimately contains all of them — so
#: ``[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.10944127.svg)](https://doi.org/10.5281/zenodo.10944127)``
#: matches as *one* long malformed DOI. Splitting the line on markup
#: punctuation first fixes that. It is a delimiter, not a second DOI regex:
#: ``harvest.doi.extract_dois`` still does all the matching.
_MARKUP_DELIMITERS = re.compile(r"[\s()\[\]<>\"'`|,]+")

_ACCEPT = "application/vnd.github+json"
_API_VERSION = "2022-11-28"

#: Repository names that are GitHub machinery rather than artifacts.
_META_REPO_NAMES = frozenset({".github", ".github-private"})


# ---------------------------------------------------------------------------
# Pure helpers — importable, testable offline, used by both harvest() and map()
# ---------------------------------------------------------------------------


def readme_text(payload: dict[str, Any]) -> str:
    """Decode the README out of the payload envelope. ``""`` when there is none."""
    readme = payload.get("readme") or {}
    content = readme.get("content")
    if not content:
        return ""
    encoding = (readme.get("encoding") or "base64").lower()
    if encoding != "base64":
        return str(content)
    try:
        return base64.b64decode(content).decode("utf-8", "replace")
    except Exception:  # a malformed README costs the badge, not the record
        log.info("could not decode README content (%s)", readme.get("path"))
        return ""


def _strip_image_suffix(doi: str) -> str:
    lowered = doi.lower()
    for suffix in IMAGE_SUFFIXES:
        if lowered.endswith(suffix):
            return doi[: -len(suffix)]
    return doi


def badge_doi_candidates(text: str) -> list[str]:
    """DOIs claimed by a Zenodo badge in ``text``, in order of appearance.

    Scoped to lines mentioning :data:`BADGE_MARKER` so that a DOI merely
    *cited* in the README — ``IEAWindSystems/windIO`` cites ``10.2172/1868328``
    — never becomes this repository's identity. Extraction itself is
    :func:`harvest.doi.extract_dois`; there is no second DOI regex in this
    module.
    """
    candidates: list[str] = []
    for line in (text or "").splitlines():
        if BADGE_MARKER not in line.lower():
            continue
        for token in _MARKUP_DELIMITERS.split(line):
            for doi in extract_dois(token):
                normalised = normalise_doi(_strip_image_suffix(doi))
                if normalised and normalised not in candidates:
                    candidates.append(normalised)
    return candidates


def concept_doi_of(datacite_payload: dict[str, Any] | None) -> str | None:
    """The concept DOI a version DOI's DataCite record declares, if any.

    Zenodo expresses it as ``relatedIdentifiers[].relationType == "IsVersionOf"``
    with ``relatedIdentifierType == "DOI"``.
    """
    if not isinstance(datacite_payload, dict):
        return None
    attributes = (datacite_payload.get("data") or {}).get("attributes") or {}
    for related in attributes.get("relatedIdentifiers") or []:
        if not isinstance(related, dict):
            continue
        if str(related.get("relationType", "")).lower() != "isversionof":
            continue
        if str(related.get("relatedIdentifierType", "")).upper() != "DOI":
            continue
        concept = normalise_doi(related.get("relatedIdentifier"))
        if concept:
            return concept
    return None


def source_key_for(payload: dict[str, Any]) -> str:
    """The composite change token (plan §4.1).

    ``<head SHA>:<latest release tag>:<hash(description, topics, licence)>``.
    Pure and deterministic: the offline fixture tests recompute it from the raw
    payload and compare it against the recorded expectation.
    """
    repository = payload.get("repository") or {}
    head_sha = ((payload.get("head_ref") or {}).get("object") or {}).get("sha") or ""
    tag = (payload.get("latest_release") or {}).get("tag_name") or ""
    digest = payload_hash(
        {
            "description": repository.get("description"),
            "topics": sorted(repository.get("topics") or []),
            "license": (repository.get("license") or {}).get("spdx_id"),
        }
    )
    return f"{head_sha}:{tag}:{digest}"


def content_bytes(repository: dict[str, Any]) -> int:
    """The repository's git size in bytes. GitHub reports ``size`` in KiB."""
    try:
        return int(repository.get("size") or 0) * 1024
    except (TypeError, ValueError):
        return 0


def exclusion_reason(
    payload: dict[str, Any],
    exclude_forks: bool = True,
    min_content_bytes: int = 2048,
) -> str | None:
    """Why this repository is noise, or ``None`` if it belongs in the catalogue.

    Forks (``gh-04``) and sub-threshold repos (``gh-10``) are excluded.
    Archived repos are **not** — they are marked and retained (``gh-05``).
    """
    repository = payload.get("repository") or {}
    if exclude_forks and repository.get("fork"):
        return "fork"
    if repository.get("is_template"):
        return "template repository"
    if str(repository.get("name") or "") in _META_REPO_NAMES:
        return "GitHub meta-repository, not an artifact"
    size = content_bytes(repository)
    if size < min_content_bytes:
        return f"below the content threshold ({size} < {min_content_bytes} bytes)"
    if not (repository.get("description") or "").strip() and not readme_text(payload):
        return "no description and no README"
    return None


def known_repository_paths(events_directory: Path | None = None) -> dict[str, str]:
    """``{github repository id: first-seen "owner/repo"}`` from the event log.

    The numeric repository id is immutable across renames and transfers, so
    this is what lets a renamed repository keep its identity key, its slug and
    its citable URL (``gh-07``). Read defensively: a damaged event file costs
    the rename-following, never the run.
    """
    directory = events_directory or _config.events_dir()
    paths: dict[str, str] = {}
    if not directory.exists():
        return paths
    for path in sorted(directory.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:  # pragma: no cover - defensive
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:  # pragma: no cover - defensive
                continue
            if event.get("source_system") != "github" or event.get("event_type") != "scraped":
                continue
            repo_id = ((event.get("source") or {}).get("extra") or {}).get("github_repo_id")
            source_id = event.get("source_id")
            if repo_id is None or not source_id:
                continue
            paths.setdefault(str(repo_id), str(source_id))  # first-seen wins
    return paths


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


@register
class GitHubAdapter(Adapter):
    source_name = "github"
    tier = 1
    source_key_semantics = (
        "default-branch SHA + latest release tag + hash(description, topics, licence)"
    )

    def __init__(
        self,
        config: Any = None,
        client: Any = None,
        doi_client: Any = None,
        events_directory: Path | None = None,
    ) -> None:
        super().__init__(config=config, client=client)
        self._doi_client = doi_client
        self._owned_client: HarvestClient | None = None
        self._owned_doi_client: Any = None
        self._events_directory = events_directory
        #: Populated during :meth:`harvest`; the run report reads it via the
        #: logged warnings, and the reconciler never sees an unresolved DOI.
        self.drop_log = DoiDropLog()

    # -- plumbing ----------------------------------------------------------

    def _api(self) -> str:
        return str(self.config.get("api") or GITHUB_API).rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": _ACCEPT, "X-GitHub-Api-Version": _API_VERSION}
        token_env = str(self.config.get("token_env") or "GITHUB_TOKEN")
        token = os.environ.get(token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _http(self) -> Any:
        if self.client is not None:
            return self.client
        if self._owned_client is None:
            self._owned_client = HarvestClient(
                min_interval=float(self.config.get("min_request_interval_seconds", 0.2) or 0.2)
            )
        return self._owned_client

    def _resolver(self) -> Any:
        if self._doi_client is not None:
            return self._doi_client
        if self._owned_doi_client is None:
            self._owned_doi_client = build_client()
        return self._owned_doi_client

    def close(self) -> None:
        for client in (self._owned_client, self._owned_doi_client):
            if client is None:
                continue
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                log.debug("closing a github client failed", exc_info=True)
        self._owned_client = None
        self._owned_doi_client = None

    def _get(self, url: str, allow_missing: bool = False) -> Any:
        """One GitHub GET. Rate limits and errors become ``SourceUnreachable``."""
        result = self._http().get(url, headers=self._headers())
        headers = {str(k).lower(): v for k, v in (result.headers or {}).items()}
        status = result.status_code

        if status in (403, 429):
            remaining = str(headers.get("x-ratelimit-remaining", "")).strip()
            if remaining == "0" or status == 429:
                reset = headers.get("x-ratelimit-reset", "unknown")
                raise SourceUnreachable(
                    f"GitHub rate limit exhausted (resets at epoch {reset}); "
                    f"set $GITHUB_TOKEN for 5,000 requests/hour"
                )
            raise SourceUnreachable(f"HTTP {status} from {url}")
        if status == 404 and allow_missing:
            return None
        if status == 304:
            # Conditional GET says nothing changed; there is no body to map.
            return None
        if not result.ok:
            raise SourceUnreachable(result.error or f"HTTP {status} from {url}")
        try:
            return result.json()
        except Exception as exc:
            raise SourceUnreachable(f"unparseable response from {url}: {exc}") from exc

    # -- discovery ---------------------------------------------------------

    def _candidate_paths(self) -> Iterator[tuple[str, list[str]]]:
        """``("owner/repo", [iea tasks])`` from orgs, explicit repos, then topics.

        Yields lazily so a run at the five-record cap never issues the search
        requests, and deduplicates by path so a repo reachable two ways is
        harvested once.
        """
        seen: set[str] = set()

        def offer(path: str, tasks: Iterable[str]) -> Iterator[tuple[str, list[str]]]:
            key = path.lower()
            if not path or key in seen:
                return
            seen.add(key)
            yield path, sorted({str(task) for task in tasks if task})

        for org in self.config.get("orgs") or []:
            login = str(org.get("login") or "").strip()
            if not login:
                continue
            listing = self._get(f"{self._api()}/orgs/{login}/repos?per_page=100&type=public"
                                f"&sort=full_name&direction=asc", allow_missing=True)
            if not listing:
                log.warning("github org %s returned no repositories", login)
                continue
            for repository in listing:
                yield from offer(str(repository.get("full_name") or ""),
                                 org.get("iea_task") or [])

        for entry in self.config.get("repos") or []:
            if isinstance(entry, str):
                yield from offer(entry, [])
            else:
                yield from offer(str(entry.get("path") or ""), entry.get("iea_task") or [])

        for topic in self.config.get("topics") or []:
            found = self._get(
                f"{self._api()}/search/repositories?q=topic:{topic}&per_page=25"
                f"&sort=updated&order=desc",
                allow_missing=True,
            )
            for repository in (found or {}).get("items", []):
                yield from offer(str(repository.get("full_name") or ""), [])

    # -- the two contract methods -----------------------------------------

    def harvest(self, max_records: int = DEFAULT_MAX_RECORDS) -> Iterable[RawObservation]:
        api = self._api()
        exclude_forks = bool(self.config.get("exclude_forks", True))
        min_content = int(self.config.get("min_content_bytes", 2048) or 0)
        known_paths = known_repository_paths(self._events_directory)
        yielded = 0

        for path, tasks in self._candidate_paths():
            # /repos/{owner}/{repo} follows the rename/transfer 301 (gh-07).
            repository = self._get(f"{api}/repos/{path}", allow_missing=True)
            if not repository:
                log.info("github repository %s is gone or private; skipping", path)
                continue

            branch = str(repository.get("default_branch") or "")
            full_name = str(repository.get("full_name") or path)
            envelope: dict[str, Any] = {
                "repository": repository,
                "head_ref": (
                    self._get(f"{api}/repos/{full_name}/git/ref/heads/{branch}",
                              allow_missing=True)
                    if branch
                    else None
                ),
                "latest_release": self._get(f"{api}/repos/{full_name}/releases/latest",
                                            allow_missing=True),
                "readme": self._get(f"{api}/repos/{full_name}/readme", allow_missing=True),
            }

            reason = exclusion_reason(envelope, exclude_forks, min_content)
            if reason is not None:
                log.info("excluding github repository %s: %s", full_name, reason)
                continue

            envelope["_resolved"] = self._resolve_badge(envelope, full_name)
            envelope["_resolved"]["iea_task"] = list(tasks)

            # gh-07: the first-seen path owns the identity for this repo id.
            repo_id = repository.get("id")
            source_id = known_paths.get(str(repo_id), full_name) if repo_id is not None else full_name
            if source_id != full_name:
                log.info(
                    "github repository %s was renamed from %s; keeping the original identity",
                    full_name, source_id,
                )

            yielded += 1
            yield RawObservation(
                source_system=self.source_name,
                source_id=source_id,
                source_key=source_key_for(envelope),
                url=repository.get("html_url"),
                payload=envelope,
            )
            if yielded >= max_records:
                return

    def _resolve_badge(self, envelope: dict[str, Any], context: str) -> dict[str, Any]:
        """Resolve the README's Zenodo badge, concept DOI first (gh-02, gh-03).

        Network work belongs here, never in :meth:`map`. The outcome — a
        resolved DOI or nothing, plus every drop — is written into the
        envelope's ``_resolved`` block, which is the only channel a pure
        ``map()`` can read it from.
        """
        outcome: dict[str, Any] = {"doi": None, "version_doi": None, "dropped_dois": []}
        candidates = badge_doi_candidates(readme_text(envelope))
        outcome["badge_candidates"] = candidates
        if not candidates:
            return outcome

        resolver = self._resolver()
        for candidate in candidates:
            resolution = resolve_or_drop(candidate, resolver, self.drop_log, context)
            if resolution is None:
                outcome["dropped_dois"].append(candidate)
                continue
            concept = concept_doi_of(resolution.payload)
            if concept and concept != resolution.doi:
                concept_resolution = resolve_or_drop(concept, resolver, self.drop_log, context)
                if concept_resolution is not None:
                    outcome["doi"] = concept_resolution.doi
                    outcome["version_doi"] = resolution.doi
                    return outcome
                outcome["dropped_dois"].append(concept)
            outcome["doi"] = resolution.doi
            return outcome
        return outcome

    def map(self, raw: RawObservation) -> MappedObservation:
        payload = raw.payload
        repository = payload.get("repository") or {}
        resolved = payload.get("_resolved") or {}
        release = payload.get("latest_release") or {}

        doi = normalise_doi(resolved.get("doi"))
        license_raw = (repository.get("license") or {}).get("spdx_id")
        license_id, _ = map_license(license_raw)

        html_url = repository.get("html_url")
        source_urls = [url for url in (html_url,) if url]

        resources: list[dict[str, Any]] = []
        if html_url:
            resources.append(
                {"url": html_url, "name": "Source repository", "format": "git"}
            )
        if release.get("html_url") and release.get("tag_name"):
            resources.append(
                {
                    "url": release["html_url"],
                    "name": f"Release {release['tag_name']}",
                    "format": "release",
                }
            )

        related: list[dict[str, str]] = []
        if doi and resolved.get("version_doi"):
            related.append(
                {
                    "relation": "HasVersion",
                    "identifier": str(resolved["version_doi"]),
                    "identifier_type": "DOI",
                }
            )

        archived = bool(repository.get("archived"))
        description = (repository.get("description") or "").strip()
        extra: dict[str, Any] = {
            "github_repo_id": repository.get("id"),
            "github_full_name": repository.get("full_name"),
            "github_owner": (repository.get("owner") or {}).get("login"),
            "github_default_branch": repository.get("default_branch"),
            "github_head_sha": ((payload.get("head_ref") or {}).get("object") or {}).get("sha"),
            "github_archived": archived,
            "github_fork": bool(repository.get("fork")),
            "github_size_kb": repository.get("size"),
        }
        if repository.get("language"):
            extra["github_language"] = repository["language"]
        if repository.get("homepage"):
            extra["github_homepage"] = repository["homepage"]
        if resolved.get("dropped_dois"):
            extra["github_dropped_badge_dois"] = list(resolved["dropped_dois"])
        extra = {key: value for key, value in extra.items() if value is not None}

        # LIFECYCLE_STATES already names "archived"; materialize maps this onto
        # extras.lifecycle_state. Retained and marked, never deleted (gh-05).
        archived_field = {"archived": True} if archived else {}

        source = SourceNamespace(
            title=str(repository.get("full_name") or raw.source_id),
            # An absent description stays absent: on a record page "no
            # description" and "an empty description" say different things.
            notes=sanitize_html(description) if description else None,
            doi=doi,
            url=html_url,
            source_urls=source_urls,
            publisher="GitHub",
            published_date=_iso_date(release.get("published_at") or repository.get("created_at")),
            version=release.get("tag_name"),
            license_raw=license_raw,
            license_id=license_id,
            resource_kind="software",
            access_status="open" if repository.get("private") is False else "unknown",
            keywords=list(repository.get("topics") or []),
            resources=resources,
            related_identifiers=related,
            iea_task=list(resolved.get("iea_task") or []),
            withdrawn=False,
            extra=extra,
            **archived_field,
        )

        provenance = {
            "title": FieldProvenance(extraction_method="api"),
            "license_id": FieldProvenance(extraction_method="pattern"),
            "resource_kind": FieldProvenance(extraction_method="pattern"),
            "published_date": FieldProvenance(extraction_method="api"),
        }
        if description:
            provenance["notes"] = FieldProvenance(extraction_method="api")
        if repository.get("topics"):
            provenance["keywords"] = FieldProvenance(extraction_method="api")
        if release.get("tag_name"):
            provenance["version"] = FieldProvenance(extraction_method="api")
        if doi:
            provenance["doi"] = FieldProvenance(extraction_method="pattern")
        if source.iea_task:
            provenance["iea_task"] = FieldProvenance(extraction_method="pattern")

        return MappedObservation(
            identity_key=identity_key(
                doi=doi, source_system=self.source_name, source_id=raw.source_id
            ),
            source_system=self.source_name,
            source_id=raw.source_id,
            source_key=raw.source_key,
            source=source,
            provenance=provenance,
            fetched_at=raw.fetched_at,
        )


def _iso_date(timestamp: str | None) -> str | None:
    """``2025-06-16T19:37:46Z`` -> ``2025-06-16``. Never fabricates a date."""
    if not timestamp:
        return None
    text = str(timestamp).strip()
    return text[:10] if len(text) >= 10 else None
