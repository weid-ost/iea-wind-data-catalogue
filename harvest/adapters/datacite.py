"""DataCite adapter — **owner: Track B (datacite)**. STUB.

Source
    ``https://api.datacite.org/dois`` with a query over IEA Wind clients,
    prefixes and affiliations listed in ``sources.yaml``.

Source key (plan §4.1)
    ``attributes.updated`` — it reflects client metadata pushes, which is what
    we want to notice.

Identity
    The DOI, lowercase-normalised. ``10.5281/ZENODO.123`` and
    ``10.5281/zenodo.123`` are one record, never two (fixture ``dc-05``) —
    use :func:`harvest.identity.identity_key`, which normalises for you.

Fixtures owned
    ``dc-01`` .. ``dc-09``.

Watch for
    * ``state`` must be ``findable``. A ``registered`` or draft DOI is skipped
      and logged — never publish a record for a DOI that does not resolve
      (``dc-04``).
    * Multiple titles: the primary title only; ``AlternativeTitle`` and
      ``TranslatedTitle`` go to ``source.extra`` (``dc-02``).
    * ``publisher`` is a string in schema <=4.4 and an object in 4.5+. Handle
      both (``dc-07``).
    * ``relatedIdentifiers`` feed version resolution and paper<->dataset
      linking. **Do not create records for the targets** (``dc-06``).
    * A rights string with no URI and no SPDX match is ``notspecified`` AND
      flagged, never silently dropped (``dc-09``).
"""

from __future__ import annotations

from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register
from harvest.models import MappedObservation, RawObservation

_TODO = "harvest.adapters.datacite is a stub — owner: Track B (datacite)"


@register
class DataCiteAdapter(Adapter):
    source_name = "datacite"
    tier = 1
    source_key_semantics = "attributes.updated"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        raise NotImplementedError(_TODO)

    def map(self, raw: RawObservation) -> MappedObservation:
        raise NotImplementedError(_TODO)
