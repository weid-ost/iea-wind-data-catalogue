"""URL scheme safety — one allow-list, applied everywhere a URL enters a record.

Upstream metadata is registrant-controlled: a DataCite ``attributes.url``, a
Zenodo file link, an OSTI full-text href and a curator's ``local.links[].url``
are all strings somebody else chose. The site renders them into ``href``
attributes, and escaping an attribute does **not** disarm ``javascript:`` or
``data:text/html``. So the scheme is checked here, once, and the check is
applied at three depths:

1. **On the way in** — :class:`~harvest.models.SourceNamespace` and
   :class:`~harvest.models.LocalNamespace` validate ``url``, ``source_urls``,
   ``resources[].url``, ``related_identifiers[].url`` and ``links[].url``, so
   no adapter can forget and no annotation can smuggle one past.
2. **On merge** — :func:`harvest.dedupe.apply_merge` re-filters what it copies
   from a secondary record onto a primary.
3. **At the gate** — ``validate-ckan-compat`` re-asserts it on ``url`` and
   ``resources[].url``, so a hand-edited ``records/*.json`` is refused too.

Dropping an unsafe URL is not an edit of source metadata (ADR-0038): the
verbatim value stays in the event log, exactly as a ``<script>`` tag stripped
by :mod:`harvest.sanitize` does. What is refused is *linking* to it.

Obfuscation is handled the way browsers handle it. WHATWG URL parsing strips
leading and trailing C0 controls and spaces, and removes tab/CR/LF from
anywhere in the input, *before* the scheme is read — so ``"\\x01javascript:"``
and ``"java\\tscript:"`` are both ``javascript:`` to a browser, and both are
refused here.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

__all__ = [
    "ALLOWED_URL_SCHEMES",
    "clean_url",
    "is_safe_url",
    "safe_url",
    "safe_urls",
    "safe_resources",
    "safe_links",
    "safe_related_identifiers",
]

#: The only schemes a catalogued link may use. ``mailto`` because a contact
#: address is a legitimate "where to ask for access" link on a restricted
#: record; ``ftp``/``ftps`` because a few national data archives still serve
#: bulk downloads that way.
ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto", "ftp", "ftps"})

#: C0 controls, space and DEL — everything a browser strips or ignores.
_C0_AND_SPACE = r"\x00-\x20\x7f"
_STRIP_RE = re.compile(rf"^[{_C0_AND_SPACE}]+|[{_C0_AND_SPACE}]+$")
_TAB_NEWLINE_RE = re.compile(r"[\t\r\n]")
_ALL_BLANKS_RE = re.compile(rf"[{_C0_AND_SPACE}]+")
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):", re.IGNORECASE)


def clean_url(value: Any) -> str:
    """The URL as a browser would see it: NUL/tab/CR/LF removed, C0 trimmed.

    Cleaning happens before the scheme test *and* on the value that is kept,
    so what the gate approves is what the page links to.
    """
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    text = _TAB_NEWLINE_RE.sub("", text)
    return _STRIP_RE.sub("", text)


def is_safe_url(
    value: Any,
    *,
    allow_relative: bool = False,
    schemes: Iterable[str] | None = None,
) -> bool:
    """Is ``value`` a URL this catalogue is willing to render as a link?

    >>> is_safe_url("https://example.org/x")
    True
    >>> is_safe_url("javascript:alert(1)")
    False
    >>> is_safe_url("\\x01javascript:alert(1)")
    False
    >>> is_safe_url("data:text/html,<script>alert(1)</script>")
    False
    """
    return safe_url(value, allow_relative=allow_relative, schemes=schemes) is not None


def safe_url(
    value: Any,
    *,
    allow_relative: bool = False,
    schemes: Iterable[str] | None = None,
) -> str | None:
    """The cleaned URL if its scheme is allowed, else ``None``.

    ``allow_relative`` permits a scheme-less URL (used only by
    :mod:`harvest.sanitize`, where a relative href inside a description is
    resolved against the page). A protocol-relative ``//host/path`` is always
    refused: its scheme is whatever the *current* page used, so it cannot be
    checked here.
    """
    allowed = frozenset(s.lower() for s in (schemes if schemes is not None else ALLOWED_URL_SCHEMES))
    cleaned = clean_url(value)
    if not cleaned:
        return None
    # The probe is what a browser resolves: every C0 control and space removed,
    # anywhere in the string, so "java\x0bscript:" cannot hide behind one.
    probe = _ALL_BLANKS_RE.sub("", cleaned)
    match = _SCHEME_RE.match(probe)
    if match:
        return cleaned if match.group(1).lower() in allowed else None
    if probe.startswith("//"):
        return None
    return cleaned if allow_relative else None


def safe_urls(values: Any, **kwargs: Any) -> list[str]:
    """Filter a list of URLs, preserving order and dropping the unsafe ones."""
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for value in values:
        cleaned = safe_url(value, **kwargs)
        if cleaned is not None and cleaned not in out:
            out.append(cleaned)
    return out


def _safe_mapping(entry: Any, key: str = "url", *, required: bool = True) -> dict | None:
    if not isinstance(entry, Mapping):
        return None
    if key not in entry or entry.get(key) in (None, ""):
        return None if required else dict(entry)
    cleaned = safe_url(entry.get(key))
    if cleaned is None:
        return None
    return {**dict(entry), key: cleaned}


def safe_resources(resources: Any) -> list[dict]:
    """Drop every resource whose ``url`` is not linkable. A resource *is* a link."""
    if not isinstance(resources, (list, tuple)):
        return []
    return [entry for entry in (_safe_mapping(r) for r in resources) if entry is not None]


def safe_links(links: Any) -> list[dict]:
    """Same, for ``local.links`` and any ``[{url, label}]`` collection."""
    return safe_resources(links)


def safe_related_identifiers(entries: Any) -> list[dict]:
    """Related identifiers keep their identifier; only an unsafe ``url`` is dropped.

    A related identifier is primarily a DOI or a handle. Some sources attach a
    resolved URL to it; that URL is a link and is filtered, but losing it must
    not lose the identifier itself.
    """
    if not isinstance(entries, (list, tuple)):
        return []
    out: list[dict] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        item = dict(entry)
        if item.get("url") is not None:
            cleaned = safe_url(item.get("url"))
            if cleaned is None:
                item.pop("url")
            else:
                item["url"] = cleaned
        out.append(item)
    return out
