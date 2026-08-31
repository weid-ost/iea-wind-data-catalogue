"""Crossref adapter — **owner: Track C (crossref)**. STUB.

Source
    ``https://api.crossref.org/works`` and
    ``https://api.crossref.org/journals/<issn>/works``. *Wind Energy Science*
    (ISSN 2366-7451) is the anchor journal; the rest are configured in
    ``sources.yaml``. Send ``mailto=`` in the query or the ``User-Agent`` to
    land in Crossref's polite pool — the project User-Agent already carries it.

Source key (plan §4.1)
    ``deposited`` — **not** ``indexed``, which churns without any content
    change and would produce a no-op event every week.

Identity
    The DOI.

Fixtures owned
    ``cr-01`` .. ``cr-07``.

Watch for
    * ``date-parts`` may be year-only. Emit a year-precision date; **never
      fabricate a month or day** (``cr-02``).
    * ``posted-content`` preprints paired with a published article: prefer the
      published version and link the preprint rather than listing it
      separately (``cr-04``).
    * Titles contain ``<i>``, ``&amp;`` and LaTeX. They must render correctly
      in HTML *and* in JSON-LD (``cr-05``) — sanitise, do not strip blindly.
    * Author entries may have no ``given`` name, or be a collaboration
      (``cr-06``). :class:`harvest.models.Author` requires only ``name``.
    * ``update-to`` retraction notices become a prominent flag (``cr-07``).
"""

from __future__ import annotations

from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register
from harvest.models import MappedObservation, RawObservation

_TODO = "harvest.adapters.crossref is a stub — owner: Track C (crossref)"


@register
class CrossrefAdapter(Adapter):
    source_name = "crossref"
    tier = 1
    source_key_semantics = "deposited (never indexed)"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        raise NotImplementedError(_TODO)

    def map(self, raw: RawObservation) -> MappedObservation:
        raise NotImplementedError(_TODO)
