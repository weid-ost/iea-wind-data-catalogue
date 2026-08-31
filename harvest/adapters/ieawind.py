"""iea-wind.org task-site adapter — **owner: Track F (ieawind)**. Tier 3. STUB.

This is where most extraction bugs will live, and the only adapter that is
allowed anywhere near a model.

Source
    ``https://iea-wind.org/task<NN>/`` task microsites and their publication
    pages, enumerated from ``sources.yaml -> sources.ieawind.tasks``. Fetch via
    :class:`harvest.http.HarvestClient` (robots respected, conditional GETs),
    then reduce to main content with ``trafilatura`` before anything else looks
    at it (fixture ``iea-10``).

Source key (plan §4.1)
    The **normalised content hash** of the extracted main text. This is
    deliberately the same value as the LLM cache key input, so an unchanged
    page is both a no-op event and a cache hit.

Identity
    Never the page. A task page is a *citation list*: sweep it for DOIs with
    :func:`harvest.doi.extract_dois`, resolve each with
    :func:`harvest.doi.resolve_or_drop`, and build records **from the
    resolver's metadata, not from the page** (``iea-01``). The page's only
    lasting contribution is the ``iea_task`` attribution, which is a
    ``local``-namespace annotation.

Order of operations — this is the boundary ADR-0024 draws
    1. Regex the page for DOIs, GitHub URLs and ORCIDs. Deterministic.
    2. Resolve every DOI. Non-resolving -> dropped **and logged** (``iea-05``).
    3. Only then, for pages with no usable identifier, call
       :func:`harvest.extract.extract` — and pass it the identifier list as
       context so the model *assigns* identifiers rather than transcribing
       them. No identifier a model produced is ever accepted.

Fixtures owned
    ``iea-01`` .. ``iea-12``. The DOI edge cases (``iea-02`` trailing full
    stop, ``iea-03`` four prefix spellings, ``iea-04`` line-wrapped) are
    already handled by :mod:`harvest.doi` and tested there — use it, do not
    write a second regex.

Watch for
    * A news post or event announcement must **not** become a record
      (``iea-09``). Classification, and a false positive here is visible.
    * Renumbered tasks: 19 -> 54 and 34 -> 59 appear on the same page.
      Resolve through :func:`harvest.config.canonical_group` (``iea-08``).
    * The same DOI cited on two task pages is one record with both tasks
      (``iea-06``).
    * A 404 or redirected task page marks the source unreachable and leaves
      existing records untouched (``iea-12``).
    * Publication lists inside linked PDFs are **out of scope for v1** and the
      gap is recorded explicitly, not silently (``iea-11``).
"""

from __future__ import annotations

from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register
from harvest.models import MappedObservation, RawObservation

_TODO = "harvest.adapters.ieawind is a stub — owner: Track F (ieawind, Tier 3)"


@register
class IeaWindAdapter(Adapter):
    source_name = "ieawind"
    tier = 3
    source_key_semantics = "normalised main-content hash (= the LLM cache key input)"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        raise NotImplementedError(_TODO)

    def map(self, raw: RawObservation) -> MappedObservation:
        raise NotImplementedError(_TODO)
