"""iea-wind.org task-site adapter — **owner: Track F (ieawind)**. Tier 3.

This is where most extraction bugs will live, and the only adapter that is
allowed anywhere near a model.

Source
    ``https://iea-wind.org`` task microsites and their publication pages,
    enumerated from ``sources.yaml -> sources.ieawind.task_pages``. **Never
    construct a task URL from a task number** — ``/task11/`` serves Task 65 and
    Task 25 lives at ``/task25-63/``. Fetch via
    :class:`harvest.http.HarvestClient` (robots respected — ``iea-wind.org``
    serves ``User-agent: * / Disallow:``, verified 2026-08-31 — conditional
    GETs, throttled), then reduce to main content with
    :func:`harvest.extract.main_text` before anything else looks at it
    (fixture ``iea-10``).

Source key (plan §4.1)
    The **normalised content hash** of the extracted main text
    (:func:`harvest.extract.content_hash`). Its input is byte-identical to the
    LLM cache key's first component, so an unchanged page is both a no-op event
    and a cache hit. A record cited on several pages keys on the hash of all
    its contributing pages' hashes, so it re-emits when any of them moves.

Identity
    Never the page. A task page is a *citation list*: sweep it for DOIs with
    :func:`harvest.doi.extract_dois`, resolve each with
    :func:`harvest.doi.resolve_or_drop`, and build records **from the
    resolver's metadata, not from the page** (``iea-01``). The page's lasting
    contribution is the ``iea_task`` attribution.

Order of operations — this is the boundary ADR-0024 draws
    1. Classify the page deterministically. A news post or event announcement
       never contributes records, however many DOIs it quotes (``iea-09``).
       Only a genuinely ambiguous page reaches :func:`harvest.extract.extract`,
       and if no model is available the page is queued and skipped — which
       fails safe, because "unclassified" means "no records".
    2. Regex the page for DOIs. Deterministic, and the only source of
       identifiers.
    3. Resolve every DOI. Non-resolving → dropped **and logged** (``iea-05``).
    4. Build the record from the DataCite or Crossref payload.

Fixtures owned
    ``iea-01`` .. ``iea-12``. The DOI edge cases (``iea-02`` trailing full
    stop, ``iea-03`` four prefix spellings, ``iea-04`` line-wrapped) are
    already handled by :mod:`harvest.doi` and tested there — use it, do not
    write a second regex.

Watch for
    * ``iea-06`` — the same DOI cited on two task pages is one record with both
      tasks. The union happens **inside one harvest run**: every configured
      task page is crawled before anything is yielded, and the observation
      carries the union. Ordering the crawl any other way would let the second
      page's scrape replace the first page's attribution, because ``source.*``
      is replaced wholesale per source system (ADR-0038).
    * ``iea-08`` — renumbered tasks (19 → 54, 34 → 59) appear beside their new
      numbers on the same page. Every task string goes through
      :func:`harvest.config.canonical_group`. Task numbers mentioned in prose
      are used only to *confirm or alias* the page's own task, never to
      attribute a record to a different one: a false task chip is worse than a
      missing one.
    * ``iea-10`` — the classifier's citation-table markers are split in two on
      purpose. The *headings* (``Publication Type``) match anywhere, because
      nobody writes them in prose. The *cell values* (``Journal paper``) match
      only inside a table cell or on a line of their own: the Task 43 overview
      says "our published journal papers, conference papers, posters and videos
      are listed here" and is a task overview, not a publication list. Widening
      the value markers back out re-breaks ``iea-10``.
    * ``iea-11`` — publication lists inside linked PDFs are out of scope for
      v1 and the gap is **recorded** in :attr:`IeaWindAdapter.notices`, not
      silently skipped.
    * ``iea-12`` — a 404 or redirected task page is recorded and skipped;
      existing records are untouched. Only when *every* page fails does the
      source declare itself unreachable.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

from harvest import DEFAULT_LIMIT
from harvest import config as _config
from harvest.adapters.base import Adapter, SourceUnreachable, payload_hash, register
from harvest.doi import DoiDropLog, extract_dois, normalise_doi, resolve_or_drop
from harvest.extract import (
    PROMPT_VERSION,
    RECORD_BEARING_PAGE_KINDS,
    cache_key,
    content_hash,
    extract as llm_extract,
    lookup_cache,
    main_text,
    pin_held,
    queue_pending,
    resolve_model,
)
from harvest.http import HarvestClient, build_client
from harvest.identity import identity_key, normalise_title
from harvest.licenses import map_license
from harvest.models import (
    Author,
    FieldProvenance,
    MappedObservation,
    RawObservation,
    SourceNamespace,
)
from harvest.sanitize import sanitize_html

__all__ = [
    "IeaWindAdapter",
    "PageClassification",
    "classify_page",
    "page_tasks",
    "map_datacite",
    "map_crossref",
]

log = logging.getLogger(__name__)

#: URL segments that mark a page as news or an announcement outright.
_NEWS_URL_RE = re.compile(
    r"/(news|newsletter|events?|webinars?|blog|press[-_]releases?|category|tag|author)(/|$)",
    re.IGNORECASE,
)

#: Slug shapes only a news post has: a sentence, in the URL, in the past tense.
_NEWS_SLUG_RE = re.compile(
    r"(^|/)[a-z0-9-]*"
    r"(has-published|now-available|out-now|joins?-the|kickoff|kick-off|is-new-|"
    r"-webinar|webinar-|-recording|announc|-award|save-the-date|call-for-|"
    r"registration-open|summary-report-has|newsletter)"
    r"[a-z0-9-]*/?$",
    re.IGNORECASE,
)

#: Link shapes worth following one level from a task page.
_PUBLICATION_LINK_RE = re.compile(
    r"(publication|publications|open-data|outputs?|reports?|deliverables|resources)",
    re.IGNORECASE,
)

#: A publication list announces itself with citation-table *headings*. These are
#: phrases nobody writes in running prose, so any occurrence is a signal.
_PUBLICATION_LIST_MARKERS = (
    "publication title",
    "publication type",
    "date of publication",
)

#: Citation-table *cell values*. Unlike the headings these do occur in prose —
#: the Task 43 overview says "Our published journal papers, conference papers,
#: posters and videos are listed here" and is emphatically not a publication
#: list (fixture ``iea-10``). So they only count inside a table cell or on a
#: line of their own, never mid-sentence.
_PUBLICATION_CELL_MARKERS = (
    "journal paper",
    "conference paper",
    "conference proceedings",
)

_TASK_MENTION_RE = re.compile(r"\btask[\s–—-]*(\d{1,3})\b", re.IGNORECASE)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.IGNORECASE)
_PDF_LINK_RE = re.compile(r"""href\s*=\s*["']([^"'#]+\.pdf)["']""", re.IGNORECASE)

#: Crossref title search: accept only a normalised-title identity match. A
#: near-match is flagged for a human, never accepted (fixture ``iea-07``).
TITLE_SEARCH_ENDPOINT = "https://api.crossref.org/works"


def _has_citation_table_markers(content: str) -> bool:
    """True when the text carries citation-table headings or type cells.

    The headings match anywhere. The cell values (``Journal paper``) match only
    where they *are* a cell — the whole of a pipe-delimited field, or the whole
    of a short standalone line — because they are also ordinary English.
    """
    lowered = (content or "").lower()
    if any(marker in lowered for marker in _PUBLICATION_LIST_MARKERS):
        return True
    for line in lowered.splitlines():
        cells = [cell.strip(" -*\t") for cell in line.split("|")] if "|" in line else [line.strip(" -*\t")]
        for cell in cells:
            if not cell or len(cell) > 60:
                continue
            for marker in _PUBLICATION_CELL_MARKERS:
                if cell == marker or cell == marker + "s" or cell.startswith(marker + ","):
                    return True
    return False


class PageClassification:
    """What a page is, how we decided, and how sure we are."""

    __slots__ = ("kind", "method", "confidence", "reason")

    def __init__(self, kind: str, method: str, confidence: float, reason: str = "") -> None:
        self.kind = kind
        self.method = method            # "pattern" | "llm" | "config"
        self.confidence = confidence
        self.reason = reason

    @property
    def record_bearing(self) -> bool:
        return self.kind in RECORD_BEARING_PAGE_KINDS

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PageClassification {self.kind} via {self.method} ({self.confidence})>"


def classify_page(
    url: str,
    content: str,
    trusted: bool = False,
) -> PageClassification | None:
    """Deterministic classification. ``None`` means "ambiguous — ask the model".

    ``trusted`` marks a page reached from ``sources.yaml`` (a configured task
    page, or one link down from it). Order matters: the news signals are
    checked **before** the trusted shortcut, because a task microsite happily
    links its own announcements under the same path prefix, and a page that is
    reachable is not thereby a publication list.
    """
    path = urlsplit(url).path or "/"
    if _NEWS_URL_RE.search(path):
        return PageClassification("news", "pattern", 1.0, "news/event URL segment")
    if _NEWS_SLUG_RE.search(path):
        return PageClassification("news", "pattern", 0.9, "announcement-shaped slug")

    if _has_citation_table_markers(content):
        return PageClassification(
            "publication-list", "pattern", 1.0, "citation-table headings present"
        )
    if trusted and _PUBLICATION_LINK_RE.search(path):
        return PageClassification("publication-list", "config", 0.9, "configured publications page")
    if trusted:
        return PageClassification("task-overview", "config", 1.0, "configured task page")

    # A page with no citation-table markers, no news markers and no place in
    # sources.yaml is exactly the case ADR-0024 lets a model classify.
    return None


def page_tasks(configured_task: str | None, content: str) -> list[str]:
    """The task attribution for one page, through the renumbering aliases.

    Task numbers written in prose are canonicalised and kept **only when they
    resolve to the page's own task** — that is the whole of fixture ``iea-08``
    (a Task 59 page that says "Task 34 (WREN)"). Attributing a record to a
    different task because its number was mentioned in a sentence is how a
    catalogue's task facet becomes untrustworthy, so it is not done.
    """
    if not configured_task:
        return []
    canonical = _config.canonical_group(configured_task)
    tasks = {canonical}
    for number in _TASK_MENTION_RE.findall(content or ""):
        mention = _config.canonical_group(f"task-{number}")
        if mention == canonical:
            tasks.add(mention)
    return sorted(tasks)


# ---------------------------------------------------------------------------
# Resolver payload -> source namespace. Pure; used by map().
# ---------------------------------------------------------------------------


def _first(values: Any, key: str | None = None) -> Any:
    for value in values or []:
        if key is None:
            return value
        if isinstance(value, dict) and value.get(key):
            return value[key]
    return None


def map_datacite(payload: dict[str, Any]) -> dict[str, Any]:
    """The bits of a DataCite ``/dois/<doi>`` response the catalogue keeps."""
    attributes = ((payload or {}).get("data") or {}).get("attributes") or {}

    authors: list[Author] = []
    for creator in attributes.get("creators") or []:
        name = str(creator.get("name") or "").strip()
        if not name:
            continue
        orcid = None
        for identifier in creator.get("nameIdentifiers") or []:
            if str(identifier.get("nameIdentifierScheme", "")).upper() == "ORCID":
                orcid = str(identifier.get("nameIdentifier") or "").strip() or None
        affiliation = creator.get("affiliation")
        if isinstance(affiliation, list):
            affiliation = _first(affiliation, "name") or (
                affiliation[0] if affiliation and isinstance(affiliation[0], str) else None
            )
        authors.append(
            Author(
                name=name,
                given=creator.get("givenName") or None,
                family=creator.get("familyName") or None,
                orcid=orcid,
                affiliation=str(affiliation) if affiliation else None,
            )
        )

    abstract = None
    for description in attributes.get("descriptions") or []:
        if str(description.get("descriptionType", "")).lower() == "abstract":
            abstract = description.get("description")
            break

    issued = None
    for date in attributes.get("dates") or []:
        if str(date.get("dateType", "")).lower() in ("issued", "available", "created"):
            issued = str(date.get("date") or "").strip() or None
            if str(date.get("dateType", "")).lower() == "issued":
                break
    if not issued and attributes.get("publicationYear"):
        issued = str(attributes["publicationYear"])

    rights = _first(attributes.get("rightsList"), "rightsIdentifier") or _first(
        attributes.get("rightsList"), "rights"
    )
    kinds = {
        "dataset": "dataset",
        "software": "software",
        "text": "publication",
        "journalarticle": "publication",
        "conferencepaper": "publication",
        "report": "report",
        "model": "model",
        "preprint": "publication",
    }
    general = str((attributes.get("types") or {}).get("resourceTypeGeneral") or "").lower()
    container = attributes.get("container")
    container_title = None
    if isinstance(container, dict):
        container_title = str(container.get("title") or "").strip() or None

    related = []
    for item in attributes.get("relatedIdentifiers") or []:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("relatedIdentifier") or "").strip()
        if not identifier:
            continue
        related.append(
            {
                "relation": str(item.get("relationType") or ""),
                "identifier": identifier,
                "identifier_type": str(item.get("relatedIdentifierType") or ""),
            }
        )

    return {
        "title": str(_first(attributes.get("titles"), "title") or "").strip() or None,
        "notes": sanitize_html(abstract),
        "authors": authors,
        "publisher": str(attributes.get("publisher") or "").strip() or None,
        "published_date": issued,
        "version": str(attributes.get("version") or "").strip() or None,
        "license_raw": str(rights).strip() if rights else None,
        "resource_kind": kinds.get(general, "other" if general else None),
        "container": container_title,
        "keywords": [
            str(subject.get("subject")).strip()
            for subject in attributes.get("subjects") or []
            if isinstance(subject, dict) and subject.get("subject")
        ],
        "related_identifiers": related,
        "url": str(attributes.get("url") or "").strip() or None,
    }


def map_crossref(payload: dict[str, Any]) -> dict[str, Any]:
    """The bits of a Crossref ``/works/<doi>`` response the catalogue keeps."""
    message = (payload or {}).get("message") or {}

    authors: list[Author] = []
    for person in message.get("author") or []:
        family = str(person.get("family") or "").strip()
        given = str(person.get("given") or "").strip()
        name = str(person.get("name") or "").strip()
        display = name or (f"{family}, {given}".strip(", ") if family else given)
        if not display:
            continue
        orcid = str(person.get("ORCID") or "").strip() or None
        if orcid:
            orcid = orcid.rsplit("/", 1)[-1]
        affiliation = _first(person.get("affiliation"), "name")
        authors.append(
            Author(
                name=display,
                given=given or None,
                family=family or None,
                orcid=orcid,
                affiliation=str(affiliation) if affiliation else None,
            )
        )

    # cr-02: never fabricate a month. A year-only Crossref date stays year-only.
    parts = ((message.get("issued") or {}).get("date-parts") or [[]])[0] or []
    published_date = "-".join(f"{int(p):02d}" if index else str(int(p))
                              for index, p in enumerate(parts) if p is not None) or None

    kinds = {
        "journal-article": "publication",
        "proceedings-article": "publication",
        "posted-content": "publication",
        "book-chapter": "publication",
        "report": "report",
        "dataset": "dataset",
        "component": "other",
        "monograph": "report",
    }
    license_raw = None
    for entry in message.get("license") or []:
        if isinstance(entry, dict) and entry.get("URL"):
            license_raw = str(entry["URL"])
            if str(entry.get("content-version")) == "vor":
                break

    return {
        "title": str(_first(message.get("title")) or "").strip() or None,
        "notes": sanitize_html(message.get("abstract")),
        "authors": authors,
        "publisher": str(message.get("publisher") or "").strip() or None,
        "published_date": published_date,
        "version": None,
        "license_raw": license_raw,
        "resource_kind": kinds.get(str(message.get("type") or ""), "publication"),
        "container": str(_first(message.get("container-title")) or "").strip() or None,
        "keywords": [str(s).strip() for s in message.get("subject") or [] if str(s).strip()],
        "related_identifiers": [],
        "url": str(message.get("URL") or "").strip() or None,
    }


_MAPPERS = {"datacite": map_datacite, "crossref": map_crossref}


@register
class IeaWindAdapter(Adapter):
    """The Tier-3 task-site adapter. Read the module docstring before editing."""

    source_name = "ieawind"
    tier = 3
    source_key_semantics = "normalised main-content hash (= the LLM cache key input)"

    def __init__(self, *args: Any, resolver: Any = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Every dropped DOI, for ``state/last-run.json -> dropped_dois`` (``iea-05``).
        self.drop_log = DoiDropLog()
        #: Coverage gaps and page failures, for the run report (``iea-11``, ``iea-12``).
        self.notices: list[dict] = []
        self._resolver = resolver
        self._owned: list[Any] = []

    # -- plumbing ----------------------------------------------------------
    def _http(self) -> Any:
        if self.client is None:
            self.client = HarvestClient()
            self._owned.append(self.client)
        return self.client

    def _resolver_client(self) -> Any:
        """A plain client for the DOI registries — API calls, not crawling."""
        if self._resolver is None:
            self._resolver = build_client()
            self._owned.append(self._resolver)
        return self._resolver

    def close(self) -> None:
        for client in self._owned:
            try:
                client.close()
            except Exception:  # pragma: no cover - defensive
                log.debug("closing %r failed", client, exc_info=True)
        self._owned.clear()

    def _note(self, kind: str, url: str, detail: str, **rest: Any) -> None:
        notice = {"type": kind, "source": self.source_name, "url": url, "detail": detail}
        notice.update(rest)
        self.notices.append(notice)
        log.warning("%s: %s (%s)", kind, url, detail)

    # -- harvest -----------------------------------------------------------
    def harvest(self, limit: int = DEFAULT_LIMIT) -> Iterable[RawObservation]:
        """Crawl the configured task pages and yield at most ``limit`` records.

        Nothing is yielded until every page has been read, because a DOI cited
        on two task pages must arrive as **one** observation carrying both
        (``iea-06``).
        """
        pages = list(self.config.get("task_pages") or [])
        if not pages:
            raise SourceUnreachable("sources.yaml declares no ieawind task_pages")

        max_followed = int(self.config.get("max_followed_links_per_page", 2))
        follow = bool(self.config.get("follow_publication_links", True))

        # doi -> {agency, resolution, tasks: set, pages: [page ref]}
        found: dict[str, dict[str, Any]] = {}
        seen_urls: set[str] = set()
        reachable_pages = 0

        for entry in pages:
            url = str(entry.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            read = self._read(url, entry.get("iea_task"), trusted=True)
            if read is None:
                continue
            reachable_pages += 1
            batch = [read] if read.get("record_bearing") else []
            if follow:
                for link in self._publication_links(read["html"], url)[:max_followed]:
                    if link in seen_urls:
                        continue
                    seen_urls.add(link)
                    # A discovered page is NOT trusted: it earns "publication
                    # list" from its own citation-table markers, or it goes to
                    # the model. This is the only path a model call is on.
                    sub = self._read(link, entry.get("iea_task"), trusted=False)
                    if sub is None:
                        continue
                    reachable_pages += 1
                    if sub.get("record_bearing"):
                        batch.append(sub)
            self._collect(batch, found, limit)
            # The cap is on records, not on pages: keep crawling only while
            # there is room, but never truncate mid-page — iea-06's union needs
            # every page that cites a DOI already held.
            if len(found) >= limit:
                break

        if reachable_pages == 0:
            # iea-12: every configured page failed. The source is unreachable;
            # run_adapter records it and existing records are untouched.
            raise SourceUnreachable(
                f"none of the {len(pages)} configured iea-wind.org task pages could be read"
            )

        for doi, gathered in list(found.items())[:limit]:
            hashes = sorted({page["content_hash"] for page in gathered["pages"]})
            yield RawObservation(
                source_system=self.source_name,
                source_id=doi,
                source_key=hashes[0] if len(hashes) == 1 else payload_hash(hashes),
                url=gathered["pages"][0]["url"],
                payload={
                    "doi": doi,
                    "agency": gathered["agency"],
                    "resolution": gathered["resolution"],
                    "iea_task": sorted(gathered["tasks"]),
                    "pages": gathered["pages"],
                    "identifier_source": gathered.get("identifier_source", "page-doi-sweep"),
                },
            )

    # -- one page ----------------------------------------------------------
    def _read(self, url: str, task: Any, trusted: bool) -> dict[str, Any] | None:
        """Fetch, reduce and classify one page.

        ``None`` means the page could not be read at all. A page that read fine
        but is not record-bearing comes back with ``record_bearing: False`` —
        it still counts as reachable, which is what keeps a task site made
        entirely of news posts from being reported as a dead source.
        """
        result = self._http().get(url)
        if not result.ok:
            # iea-12: a dead or redirected page is recorded, not fatal.
            self._note(
                "page_unreachable",
                url,
                result.error or f"HTTP {result.status_code}",
                iea_task=task,
            )
            return None
        if result.status_code == 304:
            return None  # conditional GET: unchanged, and we have no body to hash

        content = main_text(result.text)
        if not content:
            self._note("no_main_content", url, "trafilatura returned nothing", iea_task=task)
            return None

        page: dict[str, Any] = {
            "url": url,
            "iea_task": page_tasks(task if isinstance(task, str) else None, content),
            "content_hash": content_hash(content),
            "content": content,
            "html": result.text,
            "dois": [],
            "titles_without_doi": [],
            "record_bearing": False,
        }

        classification = classify_page(url, content, trusted=trusted)
        cached, _key = lookup_cache(content, PROMPT_VERSION, resolve_model(None), url=url)
        if pin_held(cached, content):
            # plan §4.3: the page moved beneath a human judgement. The pin
            # holds; the notice is what puts the decision back in front of a
            # human rather than reverting them silently.
            self._note(
                "pin_notice",
                url,
                "a pinned Tier-3 extraction is being served for a page whose content "
                "hash has changed; the pin holds until a human revisits it",
                iea_task=task,
                pin_source_key=cached.pin_source_key,
                content_hash=content_hash(content),
            )
        if classification is None:
            classification = self._classify_with_model(url, content, cached)
        page["classification"] = classification

        if not classification.record_bearing:
            # iea-09: a news post or an event announcement never becomes a
            # record, however many DOIs it quotes. The sweep does not even run.
            self._note(
                "page_not_record_bearing",
                url,
                f"classified {classification.kind} via {classification.method}"
                f" ({classification.reason})",
                iea_task=task,
                confidence=classification.confidence,
            )
            return page

        page["record_bearing"] = True
        page["dois"] = extract_dois(content)
        if cached is not None:
            # iea-07: citations the extraction found with no DOI attached. Read
            # from the committed cache only — an unambiguous publication list
            # never spends a model call.
            try:
                page["titles_without_doi"] = [
                    record.title for record in cached.page().records if not record.doi
                ]
            except Exception as exc:  # a malformed cache entry costs nothing
                log.warning("cache entry for %s is not a valid extraction: %s", url, exc)
        if not page["dois"]:
            self._record_pdf_gap(url, result.text, task)
        return page

    def _classify_with_model(
        self, url: str, content: str, cached: Any = None
    ) -> PageClassification:
        """Ambiguous page: ask the model, or queue it and treat it as not a list.

        ADR-0031: no model and no cache entry means ``None``, the page is
        queued, and the run carries on. Unclassified therefore means **no
        records**, which is the safe direction to fail in — a missing record is
        a gap, a wrong one is a defect.
        """
        result = cached if cached is not None else llm_extract(
            content, context={"url": url, "dois": extract_dois(content)}
        )
        if result is None:
            queue_pending(
                url,
                cache_key(content, PROMPT_VERSION, resolve_model(None)),
                "tier-3 page classification unavailable",
            )
            self._note("extraction_queued", url, "no cache entry and no model available")
            return PageClassification("other", "pattern", 0.0, "unclassified: queued")
        page = result.page()
        return PageClassification(
            page.page_kind,
            "llm",
            page.confidence,
            f"model {result.model} prompt {result.prompt_version}",
        )

    def _publication_links(self, html: str, base: str) -> list[str]:
        """Same-site links one level down that look like publication lists."""
        host = urlsplit(base).netloc
        links: list[str] = []
        for href in _HREF_RE.findall(html or ""):
            absolute = urljoin(base, href.strip())
            parts = urlsplit(absolute)
            if parts.scheme not in ("http", "https") or parts.netloc != host:
                continue
            if absolute.rstrip("/") == base.rstrip("/"):
                continue
            if not _PUBLICATION_LINK_RE.search(parts.path):
                continue
            if _NEWS_URL_RE.search(parts.path):
                continue
            if absolute not in links:
                links.append(absolute)
        return links

    def _record_pdf_gap(self, url: str, html: str, task: Any) -> None:
        """iea-11: a publication list that lives in a linked PDF is a recorded gap."""
        pdfs = [
            urljoin(url, href)
            for href in _PDF_LINK_RE.findall(html or "")
            if _PUBLICATION_LINK_RE.search(href) or "publi" in href.lower()
        ]
        if not pdfs:
            return
        self._note(
            "coverage_gap_pdf_only",
            url,
            "publication list appears to live in linked PDFs; PDF parsing is out of "
            "scope for v1 (fixture iea-11)",
            iea_task=task,
            pdfs=sorted(set(pdfs))[:10],
        )

    # -- DOIs -> records ---------------------------------------------------
    def _collect(
        self,
        pages: list[dict[str, Any]],
        found: dict[str, dict[str, Any]],
        limit: int,
    ) -> bool:
        """Resolve each page's DOIs and fold them into ``found``. Returns "full"."""
        client = self._resolver_client()
        for page in pages:
            for doi in page["dois"]:
                normalised = normalise_doi(doi)
                if not normalised:
                    continue
                if normalised in found:
                    # iea-06: the same DOI on a second task page adds its task,
                    # it does not replace the first.
                    found[normalised]["tasks"].update(page["iea_task"])
                    known = {ref["url"] for ref in found[normalised]["pages"]}
                    if page["url"] not in known:
                        found[normalised]["pages"].append(self._page_ref(page))
                    continue
                if len(found) >= limit:
                    continue
                # iea-05: resolve or drop. Never a record from an unresolved DOI.
                resolution = resolve_or_drop(
                    normalised, client, self.drop_log, context=page["url"]
                )
                if resolution is None:
                    continue
                found[normalised] = {
                    "agency": resolution.agency,
                    "resolution": resolution.payload,
                    "tasks": set(page["iea_task"]),
                    "pages": [self._page_ref(page)],
                }
            self._title_search(page, found, limit)
        return len(found) >= limit

    @staticmethod
    def _page_ref(page: dict[str, Any]) -> dict[str, Any]:
        return {
            "url": page["url"],
            "iea_task": page["iea_task"],
            "content_hash": page["content_hash"],
            "classified_as": page["classification"].kind,
            "classification_method": page["classification"].method,
        }

    def _title_search(
        self,
        page: dict[str, Any],
        found: dict[str, dict[str, Any]],
        limit: int,
    ) -> None:
        """iea-07: a citation with a title but no DOI, looked up in Crossref.

        Accepted **only** on a normalised-title identity match. Anything else
        is flagged in the run report for a human, never guessed into a record.
        The DOI Crossref returns is then put through ``resolve_or_drop`` like
        any other, because a search result is not a resolution.
        """
        candidates = page.get("titles_without_doi") or []
        client = self._resolver_client()
        for title in candidates:
            if len(found) >= limit:
                return
            try:
                response = client.get(
                    TITLE_SEARCH_ENDPOINT,
                    params={"query.bibliographic": title, "rows": 3, "mailto": "tom@octue.com"},
                    timeout=20.0,
                )
                items = (response.json().get("message") or {}).get("items") or []
            except Exception as exc:
                self._note("title_search_failed", page["url"], f"{type(exc).__name__}: {exc}")
                continue
            wanted = normalise_title(title)
            match = next(
                (
                    item
                    for item in items
                    if normalise_title(str((item.get("title") or [""])[0])) == wanted
                ),
                None,
            )
            if match is None:
                self._note(
                    "unresolved_citation",
                    page["url"],
                    "no high-confidence Crossref match for a citation with no DOI "
                    "(fixture iea-07); flagged rather than guessed",
                    title=title,
                )
                continue
            normalised = normalise_doi(match.get("DOI"))
            if not normalised or normalised in found:
                continue
            resolution = resolve_or_drop(normalised, client, self.drop_log, context=page["url"])
            if resolution is None:
                continue
            found[normalised] = {
                "agency": resolution.agency,
                "resolution": resolution.payload,
                "tasks": set(page["iea_task"]),
                "pages": [self._page_ref(page)],
                "identifier_source": "crossref-title-search",
            }

    # -- map ---------------------------------------------------------------
    def map(self, raw: RawObservation) -> MappedObservation:
        """Build the record from the **resolver payload**, never from the page.

        Pure: no network, no clock, no filesystem. Everything it needs was put
        on ``raw.payload`` by :meth:`harvest`.
        """
        payload = raw.payload
        doi = normalise_doi(payload.get("doi")) or str(payload.get("doi") or "")
        agency = str(payload.get("agency") or "")
        mapper = _MAPPERS.get(agency)
        if mapper is None:
            raise ValueError(f"ieawind: unknown resolver agency {agency!r} for {doi}")
        fields = mapper(payload.get("resolution") or {})

        pages = list(payload.get("pages") or [])
        page_urls = [str(page.get("url")) for page in pages if page.get("url")]
        landing = fields.get("url") or f"https://doi.org/{doi}"
        source_urls = [landing, *[u for u in page_urls if u != landing]]

        license_id, _mapped = map_license(fields.get("license_raw"))
        tasks = sorted({_config.canonical_group(t) for t in payload.get("iea_task") or [] if t})

        provenance = {
            "doi": FieldProvenance(extraction_method="pattern", source_system=self.source_name),
            "title": FieldProvenance(extraction_method="api"),
            "authors": FieldProvenance(extraction_method="api"),
            "notes": FieldProvenance(extraction_method="api"),
            "published_date": FieldProvenance(extraction_method="api"),
            "publisher": FieldProvenance(extraction_method="api"),
            "resource_kind": FieldProvenance(extraction_method="api"),
            "license_id": FieldProvenance(extraction_method="pattern"),
            "iea_task": FieldProvenance(
                extraction_method="pattern", source_system=self.source_name
            ),
        }

        return MappedObservation(
            identity_key=identity_key(doi=doi),
            source_system=self.source_name,
            source_id=doi,
            source_key=raw.source_key,
            fetched_at=raw.fetched_at,
            source=SourceNamespace(
                title=fields.get("title"),
                notes=fields.get("notes") or None,
                doi=doi,
                url=landing,
                source_urls=source_urls,
                authors=fields.get("authors") or [],
                publisher=fields.get("publisher"),
                published_date=fields.get("published_date"),
                version=fields.get("version"),
                license_raw=fields.get("license_raw"),
                license_id=license_id,
                resource_kind=fields.get("resource_kind"),
                container=fields.get("container"),
                keywords=fields.get("keywords") or [],
                related_identifiers=fields.get("related_identifiers") or [],
                iea_task=tasks,
                resources=[{"name": "DOI", "url": landing, "description": "Resolved landing page"}]
                if landing
                else [],
                extra={
                    "resolved_by": agency,
                    "cited_on": page_urls,
                    "identifier_source": payload.get("identifier_source", "page-doi-sweep"),
                },
            ),
            provenance=provenance,
        )
