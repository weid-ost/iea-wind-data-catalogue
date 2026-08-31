"""Wind Data Hub adapter — **owner: Track G (wdh)**. Tier 2.

Source
    ``https://wdh.energy.gov`` (formerly A2e / ``a2e.energy.gov``).

Spike 4 — answered, live, 2026-08-31
    **The listing endpoint is walled and the adapter therefore disables itself
    by default** (fixture ``wdh-07``). What was found:

    * ``GET https://wdh.energy.gov/api/info`` is public and advertises
      ``apiUrl: https://70d76sxu18.execute-api.us-west-2.amazonaws.com/prod``.
      That gateway answers ``403 {"message":"Missing Authentication Token"}``
      to an unauthenticated request. It is the documented API and it needs a
      token we do not have and cannot obtain without an account.
    * The site's own search endpoints — ``/api/datasets/_search`` and
      ``/api/projects/_search`` — are an Elasticsearch proxy behind the React
      SPA. They **reject ``GET`` with 404**, and answer ``POST`` with
      ``419 Page Expired`` unless the request carries a Laravel CSRF token and
      the matching session cookie. They are a browser-session surface, not a
      machine-readable API, and the CSRF protection is a deliberate signal that
      unattended clients are not the intended consumer.

    So :meth:`WindDataHubAdapter.harvest` raises
    :class:`~harvest.adapters.base.SourceUnreachable`, which
    :func:`~harvest.adapters.base.run_adapter` turns into one
    ``unreachable_sources`` line in ``state/last-run.json``. Existing records
    are untouched, the run succeeds, every other source finishes. That is the
    whole of ADR-0031 in one adapter, and it is the **primary tested path**.

    If a token ever appears, set ``$WDH_API_TOKEN`` (or point
    ``api_token_env`` in ``sources.yaml`` at another variable) and the adapter
    queries the gateway instead. Until then nothing is guessed and nothing is
    impersonated.

Source key (plan §4.1)
    ``lastUpdated`` where the dataset carries one, else
    :func:`~harvest.adapters.base.payload_hash` over the meaningful subset of
    the entry. Never over the whole hit — ``_index`` carries a rebuild date
    (``production-datasets_20260831_030003``) that would churn every night and
    turn append-on-change into append-always.

Identity
    ``doiName`` when present (all WDH DOIs seen resolve against DataCite under
    the ``10.21947`` prefix), else ``wdh|<project>/<dataset>`` (``wdh-02``).

Fixtures owned
    ``wdh-01`` .. ``wdh-07``.

Semantics held here
    * ``wdh-01`` — a dataset is a ``dataset``; instrument, temporal and spatial
      coverage are carried in ``source.extra``.
    * ``wdh-02`` — no DOI ⇒ ``wdh|<project>/<dataset>``.
    * ``wdh-03`` — **files are never enumerated.** ``wfip3/nant.ld.z01.00``
      holds 1,758,855 of them. One resource: the dataset page. The counts live
      in ``extra`` as numbers, not as a list.
    * ``wdh-04`` — an open-ended collection keeps ``temporal_coverage_end:
      null``. Never the ingest date, never "present".
    * ``wdh-05`` — legacy ``a2e.energy.gov`` URLs are canonicalised to
      ``wdh.energy.gov`` by string rewrite. ``map()`` is pure, so the redirect
      is *known* rather than followed here.
    * ``wdh-06`` — ``accessLevel: "restricted public"`` is stated plainly as
      ``access_status: registration-required``. Never as ``open``.
"""

from __future__ import annotations

import os
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, SourceUnreachable, payload_hash, register
from harvest.doi import normalise_doi
from harvest.http import HarvestClient
from harvest.identity import identity_key
from harvest.licenses import map_license
from harvest.models import (
    Author,
    FieldProvenance,
    MappedObservation,
    RawObservation,
    SourceNamespace,
)
from harvest.sanitize import sanitize_html

__all__ = ["WindDataHubAdapter", "canonicalise_wdh_url", "landing_url", "access_status_for"]

#: The public site. Everything user-facing lives under here.
BASE_URL = "https://wdh.energy.gov"

#: Hosts that 301 to :data:`BASE_URL` (verified live 2026-08-31, fixture ``wdh-05``).
LEGACY_HOSTS = ("a2e.energy.gov", "www.a2e.energy.gov")

#: The reason string the run report shows when the source disables itself.
AUTH_WALL_REASON = (
    "Wind Data Hub listing is not machine-readable without credentials (Spike 4, "
    "verified 2026-08-31, re-verified 2026-09-01): /api/info advertises an AWS "
    "API Gateway that answers "
    "403 'Missing Authentication Token', and the site's /api/datasets/_search "
    "proxy rejects GET with 404 and unauthenticated POST with 419 (CSRF). Set "
    "$WDH_API_TOKEN if a credential is ever issued; the adapter is disabling "
    "itself for this run and existing records are untouched."
)

#: WDH states no licence anywhere in the dataset record. An absent licence is
#: ``notspecified`` with ``mapped=True`` — nothing went wrong, the source simply
#: said nothing. Never infer an open licence from a ``.gov`` domain.
LICENSE_RAW = None


def canonicalise_wdh_url(url: str | None) -> str | None:
    """Rewrite a legacy ``a2e.energy.gov`` URL onto ``wdh.energy.gov`` (``wdh-05``).

    Pure string work: the redirect is verified and documented, so ``map()``
    does not need the network to know where the page went.
    """
    if not url:
        return None
    parts = urlsplit(str(url).strip())
    if not parts.netloc:
        return str(url).strip()
    host = parts.netloc.lower()
    port = ""
    if ":" in host:
        host, _, port = host.partition(":")
    if host in LEGACY_HOSTS:
        return urlunsplit(("https", "wdh.energy.gov", parts.path, parts.query, parts.fragment))
    if port in ("80", "443"):
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    return urlunsplit(parts)


def landing_url(identifier: str) -> str:
    """The dataset page: ``https://wdh.energy.gov/ds/<project>/<dataset>``."""
    return f"{BASE_URL}/ds/{str(identifier).strip('/')}"


def access_status_for(access_level: Any, access_restriction: Any) -> str:
    """Map WDH's two access fields onto the catalogue vocabulary (``wdh-06``).

    ``"restricted public"`` means "anyone may have it, once they have an
    account and the project has approved them". The honest word for that is
    ``registration-required``; it is never ``open``.
    """
    level = str(access_level or "").strip().lower()
    restriction = str(access_restriction or "").strip().lower()
    if level == "public" and restriction in ("", "none"):
        return "open"
    if level in ("restricted public", "restricted-public"):
        return "registration-required"
    if level in ("restricted", "private", "internal"):
        return "restricted"
    if restriction not in ("", "none"):
        return "registration-required"
    return "unknown"


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _contacts(entries: Any) -> list[Author]:
    """``contactPoint`` entries as authors. ``fn`` is the only field we trust."""
    authors: list[Author] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("fn"))
        if not name:
            continue
        authors.append(
            Author(
                name=name,
                affiliation=_text(entry.get("hasOrg")),
                **({"role": entry["hasRole"]} if _text(entry.get("hasRole")) else {}),
            )
        )
    return authors


def _keywords(source: dict[str, Any]) -> list[str]:
    """Instrument, data level and the project's own keyword list — verbatim."""
    words: list[str] = []
    for candidate in (source.get("instrument"), source.get("dataLevel")):
        text = _text(candidate)
        if text and text not in words:
            words.append(text)
    for measurement in source.get("measurements") or []:
        text = _text(measurement)
        if text and text not in words:
            words.append(text)
    project = source.get("project")
    if isinstance(project, dict):
        for keyword in project.get("keywords") or []:
            text = _text(keyword)
            if text and text not in words:
                words.append(text)
    return words


@register
class WindDataHubAdapter(Adapter):
    """Wind Data Hub. Self-disabling by default — see the module docstring."""

    source_name = "wdh"
    tier = 2
    source_key_semantics = "lastUpdated if provided, else payload hash of the meaningful subset"

    # -- harvest -----------------------------------------------------------
    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        """Yield at most ``limit`` datasets, or disable the source cleanly.

        The default path is the second one, and it is the tested one
        (``wdh-07``). ``harvest()`` raises **only**
        :class:`~harvest.adapters.base.SourceUnreachable`.
        """
        token_env = self.config.get("api_token_env", "WDH_API_TOKEN")
        token = os.environ.get(str(token_env), "").strip()
        if not token:
            raise SourceUnreachable(AUTH_WALL_REASON)

        api = str(self.config.get("api") or f"{BASE_URL}/api").rstrip("/")
        client = self.client or HarvestClient()
        self._client = client if self.client is None else None
        result = client.get(
            f"{api}/datasets",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if not result.ok:
            raise SourceUnreachable(
                result.error or f"HTTP {result.status_code} from {api}/datasets"
            )
        try:
            hits = result.json()["hits"]["hits"]
        except Exception as exc:  # upstream changed its shape
            raise SourceUnreachable(f"unexpected listing shape from {api}/datasets: {exc}")

        for hit in hits[:limit]:
            source = hit.get("_source") or {}
            identifier = str(source.get("identifier") or hit.get("_id") or "")
            if not identifier:
                continue
            yield RawObservation(
                source_system=self.source_name,
                source_id=identifier,
                source_key=self.source_key_for(hit),
                url=landing_url(identifier),
                payload=hit,  # VERBATIM
            )

    def close(self) -> None:
        client = getattr(self, "_client", None)
        if client is not None:
            client.close()
            self._client = None

    # -- the change token --------------------------------------------------
    @staticmethod
    def source_key_for(hit: dict[str, Any]) -> str:
        """``lastUpdated``, else a hash of the fields that mean something.

        Deliberately **not** ``payload_hash(hit)``: ``_index`` carries the
        nightly reindex date and ``_score`` moves with the query, so hashing
        the whole hit would write an event every night for every dataset.
        """
        source = hit.get("_source") or {}
        updated = _text(source.get("lastUpdated"))
        if updated:
            return updated
        summary = source.get("dapFileSummary") or {}
        return payload_hash(
            {
                "identifier": source.get("identifier"),
                "title": source.get("title"),
                "doiName": source.get("doiName"),
                "description": source.get("description"),
                "accessLevel": source.get("accessLevel"),
                "accessRestriction": source.get("accessRestriction"),
                "file_count": summary.get("count"),
                "begins": summary.get("begins"),
                "ends": summary.get("ends"),
            }
        )

    # -- map ---------------------------------------------------------------
    def map(self, raw: RawObservation) -> MappedObservation:
        """Interpret one ES hit. Pure: no network, no clock, no filesystem."""
        hit = raw.payload
        source = hit.get("_source") or hit
        identifier = str(source.get("identifier") or raw.source_id)
        project = source.get("project") if isinstance(source.get("project"), dict) else {}

        doi = normalise_doi(source.get("doiName"))
        url = canonicalise_wdh_url(raw.url) or landing_url(identifier)

        summary = source.get("dapFileSummary") or {}
        # wdh-04: an absent end is null. Never the ingest date, never "present".
        coverage_start = _text(summary.get("begins"))
        coverage_end = _text(summary.get("ends"))

        access_status = access_status_for(
            source.get("accessLevel"), source.get("accessRestriction")
        )
        license_id, _mapped = map_license(LICENSE_RAW)

        # wdh-03: ONE resource — the dataset page. A WDH dataset can hold
        # 1.75 million files; the catalogue links to the dataset and stops.
        resources = [
            {
                "name": _text(source.get("title")) or identifier,
                "url": url,
                "description": (
                    "Dataset page at the Wind Data Hub. Individual files are listed and "
                    "downloaded there; this catalogue links to datasets and never "
                    "enumerates or mirrors their files."
                ),
            }
        ]
        for reference in source.get("references") or []:
            if not isinstance(reference, dict):
                continue
            reference_url = canonicalise_wdh_url(reference.get("referenceURL"))
            if reference_url:
                resources.append(
                    {
                        "name": _text(reference.get("referenceTitle")) or reference_url,
                        "url": reference_url,
                        "format": _text((reference.get("custom") or {}).get("fileExtension")),
                        "description": "Supporting document published alongside the dataset.",
                    }
                )

        extra: dict[str, Any] = {
            "wdh_identifier": identifier,
            "wdh_project": _text(source.get("projectName")) or _text(project.get("identifier")),
            "wdh_project_title": _text(project.get("title")),
            "wdh_instrument": _text(source.get("instrument")),
            "wdh_data_level": _text(source.get("dataLevel")),
            "wdh_access_level": _text(source.get("accessLevel")),
            "wdh_access_restriction": _text(source.get("accessRestriction")),
            # Explicit nulls: "we looked and the source states none" (wdh-04).
            "temporal_coverage_start": coverage_start,
            "temporal_coverage_end": coverage_end,
            "spatial_coverage": source.get("spatial") or None,
            # wdh-03: counts, never a file list.
            "wdh_file_count": summary.get("count"),
            "wdh_total_bytes": summary.get("size"),
            "wdh_file_types": sorted({str(t) for t in (summary.get("types") or [])}) or None,
            "wdh_files_enumerated": False,
        }

        provenance = {
            "title": FieldProvenance(extraction_method="api"),
            "notes": FieldProvenance(extraction_method="api"),
            "authors": FieldProvenance(extraction_method="api"),
            "resource_kind": FieldProvenance(extraction_method="pattern"),
            "access_status": FieldProvenance(extraction_method="pattern"),
            "license_id": FieldProvenance(extraction_method="pattern"),
        }
        if doi:
            provenance["doi"] = FieldProvenance(extraction_method="api")

        return MappedObservation(
            identity_key=identity_key(
                doi=doi, source_system=self.source_name, source_id=identifier
            ),
            source_system=self.source_name,
            source_id=identifier,
            source_key=raw.source_key,
            fetched_at=raw.fetched_at,
            source=SourceNamespace(
                title=_text(source.get("title")) or identifier,
                notes=sanitize_html(_text(source.get("description"))),
                doi=doi,
                url=url,
                source_urls=[url],
                authors=_contacts(source.get("contactPoint")),
                publisher="Wind Data Hub",
                license_raw=LICENSE_RAW,
                license_id=license_id,
                resource_kind="dataset",
                access_status=access_status,
                container=_text(project.get("title")),
                keywords=_keywords(source),
                resources=resources,
                extra=extra,
            ),
            provenance=provenance,
        )
