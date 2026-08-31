"""Zenodo adapter — **owner: Track A (zenodo)**. STUB.

Fill this in against ``harvest/CONTRACT.md``. Nothing else in the repo needs to
change for this adapter to start producing records.

Source
    InvenioRDM REST API at ``https://zenodo.org/api/records``, filtered by the
    IEA Wind communities listed under ``sources.yaml -> sources.zenodo.communities``.

Source key (plan §4.1)
    The record **revision id** together with the version DOI. InvenioRDM
    increments the revision on any metadata edit. **Verify the field name
    against a live payload before relying on it** — the plan flags this as
    unconfirmed. Fall back to ``payload_hash(record["metadata"])`` if it is
    absent or looks unstable.

Identity
    The **concept DOI**, never the version DOI. This is the single most
    important decision in this adapter (fixture ``zen-02``): a record with
    three versions is ONE catalogue record whose versions are resources, never
    three records. Display the latest version's metadata under the concept
    identity and keep the first-seen date (``zen-03``).

Fixtures owned
    ``zen-01`` .. ``zen-12``. ``zen-07`` (HTML description with a ``<script>``)
    must round-trip through :func:`harvest.sanitize.sanitize_html`;
    ``zen-08`` (free-text or absent licence) through
    :func:`harvest.licenses.map_license`, flagged when unmapped; ``zen-10``
    (diacritics) through :func:`harvest.identity.slugify`.

Watch for
    * ``access_right`` of ``restricted`` / ``embargoed`` -> ``access_status``,
      never imply the files are downloadable (``zen-05``, ``zen-06``).
    * A record in two IEA Wind communities is harvested twice and must resolve
      to one identity with a unioned ``iea_task`` (``zen-11``).
    * Tombstoned records get a ``withdrawn`` event; they are never deleted
      (``zen-12``, ADR-0027).
"""

from __future__ import annotations

from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register
from harvest.models import MappedObservation, RawObservation

_TODO = "harvest.adapters.zenodo is a stub — owner: Track A (zenodo)"


@register
class ZenodoAdapter(Adapter):
    source_name = "zenodo"
    tier = 1
    source_key_semantics = "InvenioRDM record revision id + version DOI"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        raise NotImplementedError(_TODO)

    def map(self, raw: RawObservation) -> MappedObservation:
        raise NotImplementedError(_TODO)
