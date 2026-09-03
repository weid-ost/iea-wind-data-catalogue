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
# RECORD CAP
# ===========================================================================
# Every adapter's ``harvest(max_records)`` MUST honour it. This is the default;
# ``harvest run --max-records N`` (what CI passes from the workflow's
# ``max_records`` input) overrides it for one run.
# ===========================================================================
DEFAULT_MAX_RECORDS = 50

#: HTTP User-Agent for every outbound request. Descriptive, with a contact
#: address, per the harvesting etiquette in CLAUDE.md.
USER_AGENT = (
    "iea-wind-data-catalogue/0.1 "
    "(+https://github.com/weid-ost/iea-wind-data-catalogue; tom@octue.com)"
)

__version__ = "0.1.0"

__all__ = ["DEFAULT_MAX_RECORDS", "USER_AGENT", "__version__"]
