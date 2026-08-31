"""GitHub adapter — **owner: Track D (github)**. STUB.

Source
    ``https://api.github.com`` over the orgs listed in ``sources.yaml``
    (``IEAWindTask37``, ``IEAWindSystems``, ``iea-task-43``, ...) plus topic
    search for code living in individuals' accounts (fixture ``gh-08``).
    **Authenticate with the Actions ``GITHUB_TOKEN``** for 5,000 requests/hour
    instead of 60 — it is already present in the workflow.

Source key (plan §4.1)
    No single trustworthy field exists, so it is a composite:
    ``default-branch SHA + latest release tag + hash(description, topics, licence)``.
    Build it with :func:`harvest.adapters.base.payload_hash` over exactly those
    parts and nothing else — including ``pushed_at`` or the star count would
    make every run a change event.

Identity
    A Zenodo DOI from the README badge if one resolves (``gh-02``), otherwise
    ``github|<owner>/<repo>``. The badge is a free join key between the code
    and the archived release — but ``resolve_or_drop`` applies to badges too:
    a stale badge pointing at a dead or version DOI is not trusted
    (``gh-03``).

Fixtures owned
    ``gh-01`` .. ``gh-10``.

Watch for
    * **Exclude forks by default** or you will catalogue fifty copies of a
      reference turbine (``gh-04``).
    * Archived repos are marked and retained, never deleted (``gh-05``).
    * ``license: null`` means "no licence stated" — display exactly that.
      Never infer, never default to something open (``gh-06``).
    * Renames and transfers: follow the redirect; the identity key must
      survive (``gh-07``).
    * Below a content threshold (empty, template, docs-only) a repo is noise
      and is excluded (``gh-10``).
    * One record per repo for v1; a monorepo's sub-artifacts are a documented
      limitation, not a bug (``gh-09``).
"""

from __future__ import annotations

from typing import Iterable

from harvest import DEFAULT_LIMIT
from harvest.adapters.base import Adapter, register
from harvest.models import MappedObservation, RawObservation

_TODO = "harvest.adapters.github is a stub — owner: Track D (github)"


@register
class GitHubAdapter(Adapter):
    source_name = "github"
    tier = 1
    source_key_semantics = "default-branch SHA + latest release tag + hash(description, topics, licence)"

    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        raise NotImplementedError(_TODO)

    def map(self, raw: RawObservation) -> MappedObservation:
        raise NotImplementedError(_TODO)
