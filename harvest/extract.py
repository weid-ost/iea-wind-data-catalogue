"""Tier-3 LLM extraction — **owner: Track H (extraction)**. STUB.

The interface below is fixed; the implementation is not written. Everything
here is constrained by three ADRs, and none of them is negotiable.

ADR-0035 — **no vendor SDK.** GitHub Models is OpenAI-compatible, so inference
is one ``httpx`` POST to ``{endpoint}/chat/completions`` with a JSON body. In
CI the credential is the built-in ``GITHUB_TOKEN`` with
``permissions: models: read`` — no PAT, no API key, no repo secret, no vendor
account (ADR-0030). Locally, ``$HARVEST_LLM_ENDPOINT`` / ``$HARVEST_LLM_TOKEN``
/ ``$HARVEST_LLM_MODEL`` point the same function at whatever key the operator
personally has.

ADR-0025 — **the cache is committed.** Key is
``sha256(content + prompt_version + model_id)``; the entry is written to
``cache/<key>.json`` and committed. A rebuild replays the cache rather than
re-inferring, which is the only reason "rebuild from the repo" and "AI
harvester" are not in direct conflict. Cache entries are byte-stable JSON.

ADR-0031 — **the harvest never fails because the LLM is unavailable.** Key
expired, rate limited, provider outage, no token at all: :func:`extract`
returns ``None``, the page is appended to ``state/pending-extraction.json``,
and the run continues and succeeds (fixture ``x-07``). Someone drains the
queue later with ``make extract`` on their own machine.

Three further rules:

* **Structured output only.** JSON-schema-constrained response, validated on
  receipt with pydantic. Not free-text parsing.
* **Extraction, not generation.** Titles, dates and abstracts are *copied*
  from the page, never composed or summarised. This is what makes two model
  lineages an auditable footnote rather than a quality problem.
* **Identifiers never come from the model.** DOIs are regexed out
  deterministically and passed in as context; the model *assigns* them to
  records. Every one is then resolved by :func:`harvest.doi.resolve_or_drop`.
  Every field the model produced carries
  ``FieldProvenance(extraction_method="llm", model=..., prompt_version=...,
  confidence=...)`` and renders with a visible badge (ADR-0028).

``MAX_EXTRACTIONS`` caps calls per run so a site redesign that invalidates
three thousand cache entries drains over weeks rather than arriving as one
surprise bill. The remaining backlog is reported in ``state/last-run.json``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "PROMPT_VERSION",
    "MAX_EXTRACTIONS",
    "ExtractionResult",
    "cache_key",
    "cache_path",
    "read_cache",
    "write_cache",
    "queue_pending",
    "read_pending",
    "extract",
    "drain_pending",
]

#: GitHub Models. OpenAI-compatible; the path appended is ``/chat/completions``.
DEFAULT_ENDPOINT = "https://models.github.ai/inference"

#: Small and fast by default; escalate only on low confidence or a schema failure.
DEFAULT_MODEL = "openai/gpt-4o-mini"

#: Bump on ANY prompt change. It is part of the cache key, so bumping it
#: invalidates the cache deliberately and visibly.
PROMPT_VERSION = "v1"

#: Hard cap on model calls per run (plan §3.4, cost control).
MAX_EXTRACTIONS = 200


@dataclass
class ExtractionResult:
    """One page's extraction, as stored in ``cache/<key>.json``."""

    key: str
    model: str
    prompt_version: str
    content_sha256: str
    extracted_at: str
    fields: dict[str, Any] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)
    pinned: bool = False           # §4.3: a human corrected this; it holds
    pin_source_key: str | None = None


def cache_key(content: str, prompt_version: str = PROMPT_VERSION,
              model_id: str = DEFAULT_MODEL) -> str:
    """``sha256(content + prompt_version + model_id)`` — ADR-0025.

    Implemented here (not a stub) because the source key for Tier-3 pages is
    derived from the same normalised content, and both adapters and the
    reconciler need it to agree.
    """
    digest = hashlib.sha256()
    digest.update(content.encode("utf-8"))
    digest.update(b"\x1f")
    digest.update(prompt_version.encode("utf-8"))
    digest.update(b"\x1f")
    digest.update(model_id.encode("utf-8"))
    return digest.hexdigest()


def cache_path(key: str, cache_directory: Path | None = None) -> Path:
    from harvest import config

    return (cache_directory or config.cache_dir()) / f"{key}.json"


def read_cache(key: str, cache_directory: Path | None = None) -> ExtractionResult | None:
    """Read a committed cache entry. Returns ``None`` on a miss."""
    raise NotImplementedError("harvest.extract.read_cache — owner: Track H (extraction)")


def write_cache(result: ExtractionResult, cache_directory: Path | None = None) -> Path:
    """Write a cache entry as byte-stable JSON, ready to commit."""
    raise NotImplementedError("harvest.extract.write_cache — owner: Track H (extraction)")


def queue_pending(url: str, key: str, reason: str,
                  state_directory: Path | None = None) -> None:
    """Append a cache miss to ``state/pending-extraction.json`` (fixture ``x-07``).

    The queue is a list of ``{url, cache_key, reason, queued_at}``, deduped on
    ``cache_key``. Its length is reported next to the freshness banner.
    """
    raise NotImplementedError("harvest.extract.queue_pending — owner: Track H (extraction)")


def read_pending(state_directory: Path | None = None) -> list[dict]:
    """The pending-extraction queue, or ``[]`` when the file is absent."""
    raise NotImplementedError("harvest.extract.read_pending — owner: Track H (extraction)")


def extract(
    content: str,
    prompt_version: str = PROMPT_VERSION,
    model: str = DEFAULT_MODEL,
    endpoint: str = DEFAULT_ENDPOINT,
    token: str | None = None,
    schema: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    cache_directory: Path | None = None,
) -> ExtractionResult | None:
    """Extract structured metadata from cleaned page text.

    ``content`` MUST already be main-content text from ``trafilatura`` passed
    through :func:`harvest.sanitize.html_to_text` — never raw HTML. ``context``
    carries the deterministically-regexed identifiers (DOIs, GitHub URLs,
    ORCIDs) that the model is asked to *assign*.

    Returns the cached entry on a hit, a fresh :class:`ExtractionResult` on a
    successful call, and **``None``** on any miss the model cannot serve —
    no token, rate limit, outage, schema-validation failure. ``None`` is not an
    error: the caller queues the page and carries on.

    This function must never raise for an LLM-side reason.
    """
    raise NotImplementedError("harvest.extract.extract — owner: Track H (extraction)")


def drain_pending(
    limit: int = MAX_EXTRACTIONS,
    state_directory: Path | None = None,
    cache_directory: Path | None = None,
) -> int:
    """``make extract``: run the queued pages through :func:`extract`.

    Human-operated by design (plan §3.4). Returns the number of entries
    resolved; the rest stay queued.
    """
    raise NotImplementedError("harvest.extract.drain_pending — owner: Track H (extraction)")
