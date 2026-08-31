"""OSTI adapter — **owner: Track E (osti)**. STUB.

Source
    OSTI's public API (``https://www.osti.gov/api/v1/records``, with
    ``https://www.osti.gov/api/v1/records/`` for a single record) filtered to
    DOE wind-energy programme output. Endpoints and query terms live in
    ``sources.yaml``.

Source key (plan §4.1)
    OSTI's metadata-updated field where it is provided; otherwise
    :func:`harvest.adapters.base.payload_hash` over the normalised record.

Identity
    The DOI when present, otherwise ``osti|<osti_id>`` (fixture ``osti-02``).

Fixtures owned
    ``osti-01`` .. ``osti-05``.

Watch for
    * OSTI deposits frequently duplicate a journal article or a Zenodo record
      because deposit is mandated. That is a **merge**, with OSTI contributing
      an additional ``source_url`` — not a second record (``osti-03``).
    * Metadata-only entries with no public full text get an honest
      ``access_status``; never imply a download (``osti-04``).
    * The report number is often the only stable human-facing identifier and
      must reach the record page (``osti-05``).
"""

from __future__ import annotations

from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register
from harvest.models import MappedObservation, RawObservation

_TODO = "harvest.adapters.osti is a stub — owner: Track E (osti)"


@register
class OstiAdapter(Adapter):
    source_name = "osti"
    tier = 1
    source_key_semantics = "metadata-updated field if provided, else payload hash"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        raise NotImplementedError(_TODO)

    def map(self, raw: RawObservation) -> MappedObservation:
        raise NotImplementedError(_TODO)
