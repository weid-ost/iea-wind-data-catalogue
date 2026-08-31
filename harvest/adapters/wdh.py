"""Wind Data Hub adapter — **owner: Track G (wdh)**. STUB.

Source
    ``https://wdh.energy.gov`` (formerly A2e / ``a2e.energy.gov``). Whether the
    listing endpoint is reachable without a token is **unresolved** — the plan
    calls it Spike 4. Design for it being unavailable.

Source key (plan §4.1)
    A dataset-updated field if the API provides one, else
    :func:`harvest.adapters.base.payload_hash` over the normalised entry.

Identity
    DOI where present, else ``wdh|<project>/<dataset>`` (fixture ``wdh-02``).

Fixtures owned
    ``wdh-01`` .. ``wdh-07``.

Watch for
    * ``wdh-07`` is the adapter-degradation fixture for the whole project. If
      the listing endpoint demands a token we do not have, the adapter
      **disables itself cleanly**: raise
      :class:`harvest.adapters.base.SourceUnreachable`, which
      :func:`harvest.adapters.base.run_adapter` turns into an unreachable-source
      line in ``state/last-run.json``. Existing records are untouched, the run
      succeeds, the other six sources finish. Never raise anything else out of
      ``harvest()``.
    * **Never enumerate files.** A WDH dataset can hold tens of thousands
      (``wdh-03``). Catalogue the dataset; link to it.
    * Ongoing collections have a null end date. Do not fabricate one
      (``wdh-04``).
    * Legacy ``a2e.energy.gov`` URLs from citations are followed and
      canonicalised to ``wdh.energy.gov`` (``wdh-05``).
    * Download requiring an account is stated plainly as
      ``access_status: registration-required`` (``wdh-06``).
"""

from __future__ import annotations

from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register
from harvest.models import MappedObservation, RawObservation

_TODO = "harvest.adapters.wdh is a stub — owner: Track G (wdh)"


@register
class WindDataHubAdapter(Adapter):
    source_name = "wdh"
    tier = 2
    source_key_semantics = "dataset-updated field if provided, else payload hash"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        raise NotImplementedError(_TODO)

    def map(self, raw: RawObservation) -> MappedObservation:
        raise NotImplementedError(_TODO)
