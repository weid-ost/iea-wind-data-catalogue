"""Tier-3 LLM extraction — **owner: Track H (extraction)**.

The interface below is fixed; everything here is constrained by three ADRs, and
none of them is negotiable.

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

  This module enforces the rule itself rather than trusting the caller: any
  DOI in the response that is not in ``context["dois"]`` is **discarded before
  the result is cached**, so a hallucinated identifier cannot even reach the
  resolver, let alone a record.

``MAX_EXTRACTIONS`` caps calls per run so a site redesign that invalidates
three thousand cache entries drains over weeks rather than arriving as one
surprise bill. The remaining backlog is reported in ``state/last-run.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harvest.models import ACCESS_STATUSES, RESOURCE_KINDS, utcnow

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "PROMPT_VERSION",
    "MAX_EXTRACTIONS",
    "SYSTEM_PROMPT",
    "EXTRACTION_SCHEMA",
    "PAGE_KINDS",
    "RECORD_BEARING_PAGE_KINDS",
    "ExtractedRecord",
    "PageExtraction",
    "ExtractionResult",
    "ExtractionStats",
    "STATS",
    "reset_stats",
    "BACKFILL_MODELS",
    "cache_key",
    "cache_lineage",
    "cache_path",
    "lookup_cache",
    "read_cache",
    "write_cache",
    "content_hash",
    "find_pin",
    "pin_held",
    "queue_pending",
    "read_pending",
    "write_pending",
    "extract",
    "drain_pending",
    "main_text",
    "resolve_endpoint",
    "resolve_model",
    "resolve_token",
    "GITHUB_ENDPOINT_HOSTS",
    "max_extractions",
]

log = logging.getLogger(__name__)

#: GitHub Models. OpenAI-compatible; the path appended is ``/chat/completions``.
DEFAULT_ENDPOINT = "https://models.github.ai/inference"

#: Small and fast by default; escalate only on low confidence or a schema failure.
DEFAULT_MODEL = "openai/gpt-4o-mini"

#: Bump on ANY prompt change. It is part of the cache key, so bumping it
#: invalidates the cache deliberately and visibly.
PROMPT_VERSION = "v1"

#: Hard cap on model calls per run (plan §3.4, cost control).
MAX_EXTRACTIONS = 200

#: The **other** model lineages present in the committed cache (ADR-0030 §4:
#: "accept the two model lineages and design the variance away"). The expensive
#: first pass is run once, locally, on whatever key the operator has; its cache
#: entries are committed and keyed on *that* model id. A later run under
#: :data:`DEFAULT_MODEL` must still replay them byte-identically, so the lookup
#: falls back through this list before it decides it has a miss.
#:
#: ``claude-fable-5`` is the backfill lineage that seeded ``cache/`` — see
#: ``docs/runbooks/drain-the-pending-extraction-queue.md`` §5. Override with
#: ``$HARVEST_LLM_CACHE_LINEAGE`` (comma-separated) if you seed another.
BACKFILL_MODELS: tuple[str, ...] = ("claude-fable-5",)

#: HTTP timeout for one inference call. Short on purpose: a slow provider is
#: indistinguishable from an unavailable one, and both mean "queue it".
REQUEST_TIMEOUT = 60.0

#: What a Tier-3 page can be. ``page_kind`` is the classification that keeps a
#: news post out of the catalogue (fixture ``iea-09``).
PAGE_KINDS = (
    "publication-list",
    "task-overview",
    "news",
    "event",
    "person",
    "other",
)

#: Only these page kinds may contribute records. A news post or an event
#: announcement never does, however many DOIs it happens to quote.
RECORD_BEARING_PAGE_KINDS = frozenset({"publication-list", "task-overview"})


# ---------------------------------------------------------------------------
# The prompt and its schema
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a metadata extractor for a research-data catalogue. You LOCATE and \
CLASSIFY text that is already on the page. You never compose, summarise, \
translate or invent anything.

Rules, in order of importance:

1. NEVER produce an identifier. Every DOI you emit must be copied character for \
character from the KNOWN_DOIS list supplied in the user message. If a citation \
has no DOI in that list, emit null for its doi. A DOI you construct, correct or \
complete is a defect.
2. Copy titles verbatim from the page, including punctuation and capitalisation. \
Do not translate, expand abbreviations, or tidy them.
3. Classify the page. A news post, an event or webinar announcement, a person's \
profile or a meeting report is NOT a publication list, even when it quotes DOIs. \
Set page_kind accordingly and return an empty records array for those.
4. Emit a record only for a work the page presents as a publication, dataset, \
report, model or piece of software in a list of outputs. Do not emit a record \
for the page itself, for the task, or for a linked website.
5. published_date is copied from the page. If the page states only a year, emit \
the year. Never fabricate a month or a day.
6. confidence is your own honest estimate in [0, 1]. Use a low value when the \
page is ambiguous; a low confidence is useful, a wrong high confidence is not.

Return JSON conforming exactly to the supplied schema. No prose, no markdown.\
"""

#: The JSON schema the response is constrained to, and the shape
#: :class:`PageExtraction` validates on receipt. Written by hand and kept in
#: the repository on purpose (ADR-0035): no SDK helper generates it.
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page_kind", "is_record_bearing", "confidence", "records"],
    "properties": {
        "page_kind": {"type": "string", "enum": list(PAGE_KINDS)},
        "is_record_bearing": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "notes": {"type": ["string", "null"]},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "doi", "resource_kind", "confidence"],
                "properties": {
                    "title": {"type": "string", "minLength": 1},
                    "doi": {"type": ["string", "null"]},
                    "resource_kind": {"type": "string", "enum": list(RESOURCE_KINDS)},
                    "access_status": {
                        "type": ["string", "null"],
                        "enum": [*ACCESS_STATUSES, None],
                    },
                    "container": {"type": ["string", "null"]},
                    "published_date": {"type": ["string", "null"]},
                    "authors": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
            },
        },
    },
}


class ExtractedRecord(BaseModel):
    """One work the model located on the page. Never a source of identifiers."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    doi: str | None = None
    resource_kind: Literal[RESOURCE_KINDS] = "other"  # type: ignore[valid-type]
    access_status: str | None = None
    container: str | None = None
    published_date: str | None = None
    authors: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PageExtraction(BaseModel):
    """The whole response for one page, validated on receipt."""

    model_config = ConfigDict(extra="forbid")

    page_kind: Literal[PAGE_KINDS] = "other"  # type: ignore[valid-type]
    is_record_bearing: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: str | None = None
    records: list[ExtractedRecord] = Field(default_factory=list)


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
    #: The page the pin was made for. A cache entry is keyed on content, so a
    #: redesigned page mints a new key and would lose the pin silently. The URL
    #: is the only stable handle the page has, so a pin records it and
    #: :func:`find_pin` looks the pin up by it (plan §4.3). Unset on ordinary
    #: entries, which are found by content and need no handle.
    pin_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "content_sha256": self.content_sha256,
            "extracted_at": self.extracted_at,
            "fields": self.fields,
            "confidence": self.confidence,
            "pinned": self.pinned,
            "pin_source_key": self.pin_source_key,
            "pin_url": self.pin_url,
        }

    def page(self) -> PageExtraction:
        """``fields`` re-validated as a :class:`PageExtraction`."""
        return PageExtraction.model_validate(self.fields)


@dataclass
class ExtractionStats:
    """Cache hits, misses and calls for one process. Fed to the run report."""

    hits: int = 0
    misses: int = 0
    calls: int = 0
    failures: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 4) if total else 0.0


#: Process-wide counters. ``state/last-run.json`` reports these.
STATS = ExtractionStats()


def reset_stats() -> None:
    """Zero the counters. Tests and long-lived processes call this."""
    STATS.hits = STATS.misses = STATS.calls = STATS.failures = 0


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def resolve_endpoint(endpoint: str | None = None) -> str:
    """The OpenAI-compatible base URL. ``/chat/completions`` is appended later."""
    return (
        endpoint
        or _env("HARVEST_LLM_ENDPOINT", "OPENAI_BASE_URL", "OPENAI_API_BASE")
        or DEFAULT_ENDPOINT
    ).rstrip("/")


def resolve_model(model: str | None = None) -> str:
    """The model id. Part of the cache key, so a change is a visible lineage split."""
    return model or _env("HARVEST_LLM_MODEL", "MODEL_ID", "OPENAI_MODEL") or DEFAULT_MODEL


#: Hosts that are GitHub Models. ``GITHUB_TOKEN`` is only ever sent to one of
#: these; ``OPENAI_API_KEY`` is never sent to one.
GITHUB_ENDPOINT_HOSTS = (".github.ai", ".github.com", "github.ai", "github.com")


def _is_github_endpoint(endpoint: str) -> bool:
    from urllib.parse import urlsplit

    host = (urlsplit(endpoint).hostname or "").lower()
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in GITHUB_ENDPOINT_HOSTS)


def resolve_token(token: str | None = None, endpoint: str | None = None) -> str | None:
    """The credential for **this endpoint**, or ``None``.

    ``None`` is the normal, supported state: CI without ``models: read``, a
    laptop with no key, a fork. It is not an error (ADR-0031).

    **A credential is only ever sent to the provider it belongs to.** The token
    and the endpoint are resolved as one unit, because they used to be resolved
    from two disjoint families of environment variables: a machine with the
    OpenAI SDK installed (``OPENAI_API_KEY`` set, no ``OPENAI_BASE_URL``) sent
    the operator's OpenAI secret as a Bearer token to
    ``https://models.github.ai`` on every ordinary harvest — a live credential
    leak to a third party (product-e2e-01). So:

    * an explicit argument, or ``HARVEST_LLM_TOKEN``, is this harvester's own
      credential and goes wherever the harvester is pointed;
    * ``OPENAI_API_KEY`` is used **only** when an endpoint was explicitly
      configured for it (``HARVEST_LLM_ENDPOINT`` / ``OPENAI_BASE_URL`` /
      ``OPENAI_API_BASE``) and that endpoint is not GitHub's;
    * ``GITHUB_TOKEN`` is used **only** when the endpoint is GitHub's — the
      mirror image, so a stray ``OPENAI_BASE_URL`` cannot walk a repository
      token to a third-party inference host either.

    A credential that is refused is logged once, at INFO, and the page queues.
    """
    if token:
        return token
    explicit = _env("HARVEST_LLM_TOKEN")
    if explicit:
        return explicit

    base = resolve_endpoint(endpoint)
    configured_endpoint = _env("HARVEST_LLM_ENDPOINT", "OPENAI_BASE_URL", "OPENAI_API_BASE")
    github = _is_github_endpoint(base)

    openai_key = _env("OPENAI_API_KEY")
    if openai_key:
        if configured_endpoint and not github:
            return openai_key
        log.info(
            "OPENAI_API_KEY is set but the inference endpoint is %s, which it was not "
            "issued for; not sending it. Set HARVEST_LLM_ENDPOINT (or OPENAI_BASE_URL) "
            "to the endpoint that key belongs to, or set HARVEST_LLM_TOKEN.",
            base,
        )

    github_token = _env("GITHUB_TOKEN")
    if github_token:
        if github:
            return github_token
        log.info(
            "GITHUB_TOKEN is set but the inference endpoint is %s, which is not GitHub; "
            "not sending it.",
            base,
        )
    return None


def max_extractions() -> int:
    """:data:`MAX_EXTRACTIONS`, overridable with ``$HARVEST_MAX_EXTRACTIONS``."""
    raw = _env("HARVEST_MAX_EXTRACTIONS")
    if raw is None:
        return MAX_EXTRACTIONS
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("HARVEST_MAX_EXTRACTIONS=%r is not an integer; using %s", raw, MAX_EXTRACTIONS)
        return MAX_EXTRACTIONS


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------


def main_text(html: str | None) -> str:
    """Reduce a fetched page to the only shape a model may see (fixture ``iea-10``).

    ``trafilatura`` for main content, then
    :func:`harvest.sanitize.html_to_text`. Nav, cookie banners, footers,
    ``<script>`` and every attribute are gone before the model, the DOI sweep
    or the content hash sees a single byte. This is simultaneously the token
    budget, the accuracy control and the prompt-injection boundary, and it is
    defined **here** so that the adapter, the source key and
    :func:`drain_pending` cannot disagree about what "the content" is.
    """
    if not html:
        return ""
    import trafilatura

    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_links=True,
        include_tables=True,
        favor_recall=True,
    )
    from harvest.sanitize import html_to_text

    return html_to_text(extracted or html).strip()


def content_hash(content: str) -> str:
    """The Tier-3 source key: 16 hex of ``sha256`` over the normalised content.

    The *input* is byte-identical to :func:`cache_key`'s first component, which
    is what ADR-0025 means by "one hash, two purposes". The digests differ only
    because the cache key also folds in the prompt version and the model id —
    bumping either must invalidate the cache without inventing a source change.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The cache (ADR-0025)
# ---------------------------------------------------------------------------


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


def cache_lineage(model_id: str) -> tuple[str, ...]:
    """``model_id`` first, then every other lineage present in the cache."""
    raw = _env("HARVEST_LLM_CACHE_LINEAGE")
    others = tuple(part.strip() for part in raw.split(",") if part.strip()) if raw else BACKFILL_MODELS
    return (model_id, *(other for other in others if other != model_id))


#: ``{directory: (signature, {pin_url: key})}``. Built once per process and
#: rebuilt when the cache directory changes, because a pin lookup happens on
#: every content miss and reading a few thousand committed entries each time
#: would make a cold run quadratic for the sake of a handful of hand-made pins.
_PIN_INDEX: dict[str, tuple[tuple[int, int], dict[str, str]]] = {}


def _pin_index(directory: Path) -> dict[str, str]:
    """``{pin_url: cache key}`` for one cache directory."""
    try:
        signature = (directory.stat().st_mtime_ns, len(list(directory.glob("*.json"))))
    except OSError:
        return {}
    cached = _PIN_INDEX.get(str(directory))
    if cached is not None and cached[0] == signature:
        return cached[1]

    index: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pin_url = payload.get("pin_url")
        if payload.get("pinned") and pin_url:
            index[str(pin_url)] = path.stem
    _PIN_INDEX[str(directory)] = (signature, index)
    return index


def find_pin(url: str, cache_directory: Path | None = None) -> ExtractionResult | None:
    """The pinned entry for ``url``, whatever the page says today (plan §4.3).

    A pin is a human outranking a model's guess about *our own* output, so it
    must survive the page being rewritten — otherwise a site redesign silently
    reverts every correction anybody ever made. Cache entries are keyed on
    content and a rewritten page mints a new key, so a pin is found by the one
    thing that does not change: the URL it was made for.
    """
    from harvest import config

    directory = cache_directory or config.cache_dir()
    if not url or not directory.exists():
        return None
    key = _pin_index(directory).get(url)
    return read_cache(key, directory) if key else None


def pin_held(result: "ExtractionResult | None", content: str) -> bool:
    """True when a pin is being served for content it was **not** made against.

    That is the moment plan §4.3 says a notice must fire: the page moved
    beneath a human judgement, the pin holds anyway, and a human decides.
    """
    if result is None or not result.pinned:
        return False
    return bool(result.pin_source_key) and result.pin_source_key != content_hash(content)


def lookup_cache(
    content: str,
    prompt_version: str = PROMPT_VERSION,
    model_id: str = DEFAULT_MODEL,
    cache_directory: Path | None = None,
    url: str | None = None,
) -> tuple["ExtractionResult | None", str]:
    """``(hit_or_None, primary_key)``, trying every lineage in :func:`cache_lineage`.

    The primary key is always the one for ``model_id``: that is what a fresh
    extraction would be written under, and what a queue entry records.

    When ``url`` is supplied and the content misses, a **pin** made for that URL
    is served instead (plan §4.3). Ask :func:`pin_held` whether the pin was made
    against different content, and raise the notice if it was.
    """
    primary = cache_key(content, prompt_version, model_id)
    for candidate in cache_lineage(model_id):
        key = primary if candidate == model_id else cache_key(content, prompt_version, candidate)
        hit = read_cache(key, cache_directory)
        if hit is not None:
            return hit, primary
    if url:
        pin = find_pin(url, cache_directory)
        if pin is not None:
            log.info("serving the pinned extraction for %s; the page has moved beneath it", url)
            return pin, primary
    return None, primary


def cache_path(key: str, cache_directory: Path | str | None = None) -> Path:
    from harvest import config

    # `Path(...)`, not the bare argument: `extract()` is documented as never
    # raising, and a `str` cache directory used to blow up with a TypeError on
    # the first line of the cache lookup — before any of the defensive
    # try/except blocks could catch it (product-e2e-08).
    directory = Path(cache_directory) if cache_directory is not None else config.cache_dir()
    return directory / f"{key}.json"


def read_cache(key: str, cache_directory: Path | None = None) -> ExtractionResult | None:
    """Read a committed cache entry. Returns ``None`` on a miss.

    A corrupt entry is a miss, not an exception: the committed cache is a
    convenience, never a dependency.
    """
    path = cache_path(key, cache_directory)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ExtractionResult(
            key=str(payload.get("key") or key),
            model=str(payload["model"]),
            prompt_version=str(payload["prompt_version"]),
            content_sha256=str(payload.get("content_sha256", "")),
            extracted_at=str(payload.get("extracted_at", "")),
            fields=dict(payload.get("fields") or {}),
            confidence={str(k): float(v) for k, v in (payload.get("confidence") or {}).items()},
            pinned=bool(payload.get("pinned", False)),
            pin_source_key=payload.get("pin_source_key") or None,
            pin_url=payload.get("pin_url") or None,
        )
    except Exception as exc:
        log.warning("ignoring unreadable cache entry %s: %s", path.name, exc)
        return None


def write_cache(result: ExtractionResult, cache_directory: Path | None = None) -> Path:
    """Write a cache entry as byte-stable JSON, ready to commit."""
    path = cache_path(result.key, cache_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        result.as_dict(), indent=2, sort_keys=True, ensure_ascii=False, separators=(",", ": ")
    ) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != payload:
        path.write_text(payload, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The pending queue (ADR-0031, fixture x-07)
# ---------------------------------------------------------------------------


def read_pending(state_directory: Path | None = None) -> list[dict]:
    """The pending-extraction queue, or ``[]`` when the file is absent."""
    from harvest import config

    path = (
        (state_directory / "pending-extraction.json")
        if state_directory is not None
        else config.pending_extraction_path()
    )
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("pending-extraction queue is unreadable (%s); treating as empty", exc)
        return []
    if isinstance(payload, dict):  # tolerate {"pending": [...]}
        payload = payload.get("pending", [])
    return [entry for entry in payload if isinstance(entry, dict)]


def write_pending(entries: list[dict], state_directory: Path | None = None) -> Path:
    """Overwrite the queue. Order is preserved: the queue is FIFO."""
    from harvest import config

    path = (
        (state_directory / "pending-extraction.json")
        if state_directory is not None
        else config.pending_extraction_path()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        entries, indent=2, sort_keys=True, ensure_ascii=False, separators=(",", ": ")
    ) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != payload:
        path.write_text(payload, encoding="utf-8")
    return path


def queue_pending(url: str, key: str, reason: str,
                  state_directory: Path | None = None) -> None:
    """Append a cache miss to ``state/pending-extraction.json`` (fixture ``x-07``).

    The queue is a list of ``{url, cache_key, reason, queued_at}``, deduped on
    ``cache_key``. Its length is reported next to the freshness banner.

    This never raises. A queue that cannot be written costs one page, not the
    run — which is the whole point of ADR-0031.
    """
    try:
        entries = read_pending(state_directory)
        for entry in entries:
            if entry.get("cache_key") == key:
                entry["reason"] = reason  # newest reason wins; queued_at is the first sighting
                write_pending(entries, state_directory)
                return
        entries.append(
            {"url": url, "cache_key": key, "reason": reason, "queued_at": utcnow()}
        )
        write_pending(entries, state_directory)
    except Exception as exc:  # pragma: no cover - defensive; the run must survive
        log.warning("could not queue %s for extraction: %s", url, exc)


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def _user_message(content: str, context: dict[str, Any] | None) -> str:
    context = dict(context or {})
    dois = [str(d) for d in (context.pop("dois", None) or [])]
    url = context.pop("url", None)
    parts = [
        "KNOWN_DOIS (the ONLY DOIs you may emit; copy them character for character):",
        json.dumps(dois, ensure_ascii=False),
    ]
    if url:
        parts += ["", f"PAGE_URL: {url}"]
    if context:
        parts += ["", "CONTEXT:", json.dumps(context, sort_keys=True, ensure_ascii=False)]
    parts += ["", "PAGE_TEXT (main content only, markup already stripped):", content]
    return "\n".join(parts)


def _drop_invented_dois(page: PageExtraction, allowed: list[str]) -> PageExtraction:
    """Blank any DOI the model produced that was not in the supplied context.

    ADR-0024 rule 2, enforced rather than assumed. The record survives with a
    null DOI (the deterministic sweep or the reconciler may still identify it);
    the invented string does not survive at all.
    """
    permitted = {str(d).strip().lower() for d in allowed}
    for record in page.records:
        if record.doi is None:
            continue
        if str(record.doi).strip().lower() not in permitted:
            log.warning(
                "discarding model-produced DOI %r for %r: not in the supplied context",
                record.doi,
                record.title,
            )
            record.doi = None
    return page


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
    through :func:`harvest.sanitize.html_to_text` — never raw HTML. Use
    :func:`main_text`. ``context`` carries the deterministically-regexed
    identifiers (``context["dois"]``) that the model is asked to *assign*.

    Returns the cached entry on a hit, a fresh :class:`ExtractionResult` on a
    successful call, and **``None``** on any miss the model cannot serve —
    no token, rate limit, outage, schema-validation failure. ``None`` is not an
    error: the caller queues the page and carries on.

    This function must never raise for an LLM-side reason.
    """
    model_id = resolve_model(model if model != DEFAULT_MODEL else None)
    url = str((context or {}).get("url") or "") or None

    # The cache is consulted FIRST, always, and a hit costs no call at all. A
    # pin for this URL counts as a hit even when the page has been rewritten:
    # a human's correction outranks a fresh guess (plan §4.3).
    cached, key = lookup_cache(content, prompt_version, model_id, cache_directory, url=url)
    if cached is not None:
        STATS.hits += 1
        return cached
    STATS.misses += 1

    base = resolve_endpoint(endpoint if endpoint != DEFAULT_ENDPOINT else None)
    credential = resolve_token(token, endpoint=base)
    if not credential:
        log.info("no LLM credential available; queueing instead of extracting")
        return None

    cap = max_extractions()
    if STATS.calls >= cap:
        log.warning("MAX_EXTRACTIONS (%s) reached this run; queueing the rest", cap)
        return None

    body = {
        "model": model_id,
        # Temperature at the minimum for stability. Determinism is NOT
        # guaranteed even so — the cache, not the temperature, is what makes a
        # rebuild reproducible (ADR-0025).
        "temperature": 0.0,
        "top_p": 1.0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(content, context)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "iea_wind_page_extraction",
                "strict": True,
                "schema": schema or EXTRACTION_SCHEMA,
            },
        },
    }

    STATS.calls += 1
    try:
        import httpx

        from harvest import USER_AGENT

        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post(
                f"{base}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
            )
        if response.status_code != 200:
            STATS.failures += 1
            log.warning(
                "inference returned HTTP %s; queueing the page (%s)",
                response.status_code,
                (response.text or "")[:200],
            )
            return None
        message = response.json()["choices"][0]["message"]["content"]
        page = PageExtraction.model_validate(json.loads(message))
    except ValidationError as exc:
        STATS.failures += 1
        log.warning("inference response failed schema validation; queueing the page: %s", exc)
        return None
    except Exception as exc:  # transport, JSON, rate limit, provider outage
        STATS.failures += 1
        log.warning("inference unavailable (%s: %s); queueing the page", type(exc).__name__, exc)
        return None

    page = _drop_invented_dois(page, list((context or {}).get("dois") or []))
    result = ExtractionResult(
        key=key,
        model=model_id,
        prompt_version=prompt_version,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        extracted_at=utcnow(),
        fields=page.model_dump(mode="json"),
        confidence={
            "page_kind": page.confidence,
            "is_record_bearing": page.confidence,
            "records": min([r.confidence for r in page.records], default=page.confidence),
        },
    )
    write_cache(result, cache_directory)
    return result


# ---------------------------------------------------------------------------
# The drain (``make extract``)
# ---------------------------------------------------------------------------


def drain_pending(
    limit: int = MAX_EXTRACTIONS,
    state_directory: Path | None = None,
    cache_directory: Path | None = None,
    client: Any = None,
) -> int:
    """``make extract``: run the queued pages through :func:`extract`.

    Human-operated by design (plan §3.4). Returns the number of entries
    resolved; the rest stay queued.

    Each entry is re-fetched and re-reduced through :func:`main_text`, which is
    the same function the adapter used, so the recomputed cache key matches the
    queued one whenever the page has not changed. When the page *has* changed
    the new key is cached and the stale entry is dropped from the queue —
    re-extracting a page nobody will ask for again is the wrong kind of thrift.
    """
    from harvest.http import HarvestClient

    entries = read_pending(state_directory)
    if not entries:
        return 0

    budget = min(int(limit), max_extractions())
    owned = client is None
    http = client or HarvestClient()
    resolved: list[dict] = []
    try:
        for entry in entries:
            if len(resolved) >= budget:
                break
            url = str(entry.get("url") or "")
            if not url:
                resolved.append(entry)  # nothing to fetch; do not keep it forever
                continue
            result = http.get(url)
            if not result.ok:
                log.warning("cannot re-fetch %s (%s); leaving it queued", url, result.error
                            or result.status_code)
                continue
            content = main_text(result.text)
            if not content:
                log.warning("no main content at %s; leaving it queued", url)
                continue
            outcome = extract(
                content,
                context={"url": url, "dois": _sweep(content)},
                cache_directory=cache_directory,
            )
            if outcome is None:
                continue
            resolved.append(entry)
    finally:
        if owned:
            http.close()

    if resolved:
        keys = {entry.get("cache_key") for entry in resolved}
        write_pending(
            [entry for entry in entries if entry.get("cache_key") not in keys], state_directory
        )
    return len(resolved)


def _sweep(content: str) -> list[str]:
    from harvest.doi import extract_dois

    return extract_dois(content)
