"""IEA Wind Data Catalogue harvester.

The harvest is a pipeline of four deterministic stages:

    harvest  ->  events/     (append-only, append-on-change, source of truth)
    replay   ->  records/     (derived CKAN package dicts, regenerable)
    validate ->  CKAN-compat gate (plan §2.2, fixture x-08)
    report   ->  state/last-run.json (written every run, even a no-op)

Read ``harvest/CONTRACT.md`` before writing an adapter. It is the interface
document the parallel tracks build against.
"""

from __future__ import annotations

# ===========================================================================
# PROTOTYPE RECORD CAP
# ===========================================================================
# Every adapter's ``harvest()`` takes a ``limit`` and MUST honour it.  The
# default is FIVE.  This is a deliberate prototype cap so that a stray run
# cannot hammer an upstream API, blow through a rate limit, or commit three
# thousand records before anyone has looked at five.  Raise it consciously,
# per-run, with ``--limit`` or ``max_records:`` in ``sources.yaml``.
# ===========================================================================
DEFAULT_LIMIT = 5

#: HTTP User-Agent for every outbound request. Descriptive, with a contact
#: address, per the harvesting etiquette in CLAUDE.md.
USER_AGENT = (
    "iea-wind-data-catalogue/0.1 "
    "(+https://github.com/thclark/iea-wind-data-catalogue; tom@octue.com)"
)

__version__ = "0.1.0"

__all__ = ["DEFAULT_LIMIT", "USER_AGENT", "__version__"]
