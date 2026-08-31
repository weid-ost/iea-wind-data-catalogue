"""Cross-source identity merging — the reconciler (plan §4, track I).

**Most deduplication in this catalogue never reaches this module**, and that is
by design. When Zenodo, DataCite, GitHub and an iea-wind.org citation all
describe one DOI, all four adapters derive the *same* identity key (the DOI), so
all four write to one ``events/<slug>.jsonl`` and
:func:`harvest.events.resolve` composes them into one record with four
``source_urls`` — fixture ``x-01``, no reconciliation code involved.

What is left for this module is the harder half: artifacts whose identity keys
**differ** but which are the same thing.

| Case | Join key | Verdict |
|---|---|---|
| a repository record and a DOI record for one artifact (``osti-03``) | shared DOI on a non-DOI identity | automatic |
| Zenodo software ↔ GitHub repo (``zen-04``, ``gh-02``) | the Zenodo DOI badge in the README, carried as a related identifier | automatic |
| preprint ↔ published article (``cr-04``) | ``IsPreprintOf`` / ``IsPreviousVersionOf`` | automatic; the published version wins |
| same work in Zenodo *and* an institutional repository (``dc-08``) | fuzzy title + first-author surname + year | **proposed only, never applied** |

### What "merge" means here, given an append-only log

Identity keys are file names and citable URLs; they are not rewritten, and
nothing is ever deleted (ADR-0027). A merge is therefore expressed as two
``annotated`` events, exactly the vocabulary [[correct-a-record]] gives a
curator:

* the **primary** identity gains the secondary's ``local.source_urls`` and a
  ``local.links`` entry pointing at it, so one record carries every way in;
* the **secondary** identity gains ``local.suppressed: true`` — retained,
  citable, and out of the listings — plus a link back.

Both events carry ``actor: "reconcile"`` and a note naming the join key, so the
decision is auditable in the log forever, and reversible by appending the
opposite annotation. Merges are applied only by an explicit
``python -m harvest dedupe --apply``.

### Fuzzy matches are proposals, never merges

A fuzzy match is a guess about the world, and a wrong merge hides a real record
behind a suppression flag. So the fuzzy pass emits **proposals**: they land in
``state/merge-proposals.json`` and in the run report's ``notices``, which is the
short list a curator reads monthly. ``--apply`` does not apply them; a human
turns one into a merge by writing the annotation, or into a rejection by doing
nothing. Buckets are keyed on (first-author surname, year), so an artifact with
neither is never fuzzily matched at all — not even proposed.
"""

from __future__ import annotations

import difflib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from harvest import config
from harvest.doi import normalise_doi
from harvest.events import DEFAULT_SOURCE_PRECEDENCE, annotate, iter_identity_keys, resolve
from harvest.identity import identity_kind, normalise_author, normalise_title
from harvest.models import ResolvedRecord

__all__ = [
    "SAMENESS_RELATIONS",
    "PREPRINT_RELATIONS",
    "SUPERSEDED_BY_RELATIONS",
    "SUPERSEDES_RELATIONS",
    "DEFAULT_FUZZY_THRESHOLD",
    "MergeCandidate",
    "DedupeResult",
    "load_resolved",
    "find_candidates",
    "is_merged",
    "apply_merge",
    "dedupe",
    "proposals_path",
    "write_proposals",
    "read_proposals",
    "candidates_as_notices",
]

log = logging.getLogger(__name__)

#: Relations that assert *this is the same artifact by another route*. Kept
#: deliberately short: ``IsSupplementTo`` and ``References`` do not mean
#: sameness, and a loose list here silently hides records.
SAMENESS_RELATIONS = frozenset(
    {
        "isidenticalto",
        "isvariantformof",
        "isoriginalformof",
        "issourceof",
        "isderivedfrom",
        "iscompiledby",
        "compiles",
    }
)

#: "I am the earlier/lesser one of the pair" — the *target* becomes the primary.
#: ``cr-04``: a preprint declaring ``IsPreprintOf`` the published article loses
#: to it, and is linked from it rather than listed separately.
SUPERSEDED_BY_RELATIONS = frozenset(
    {"ispreprintof", "ispreviousversionof", "isobsoletedby", "issupersededby"}
)

#: The inverse, declared from the surviving side.
SUPERSEDES_RELATIONS = frozenset({"haspreprint", "isnewversionof", "obsoletes"})

#: Either direction of a preprint/version pair.
PREPRINT_RELATIONS = SUPERSEDED_BY_RELATIONS | SUPERSEDES_RELATIONS

#: ``difflib`` ratio over normalised titles. 0.90 keeps "…, 2021" vs "…, 2022"
#: apart while tolerating punctuation and subtitle drift.
DEFAULT_FUZZY_THRESHOLD = 0.90

#: Which identity rule produced a key, best first. A DOI identity outranks a
#: source identity outranks a fragile hash, because that is the order in which
#: the key survives an upstream metadata edit.
_KIND_RANK = {"doi": 0, "source": 1, "fragile": 2}


@dataclass(frozen=True)
class MergeCandidate:
    """One proposed or decided identification of two identities as one artifact."""

    primary: str
    secondary: str
    kind: str            # shared-doi | related-identifier | preprint-pair | fuzzy-title
    confidence: float
    evidence: str
    automatic: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "secondary": self.secondary,
            "kind": self.kind,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence,
            "automatic": self.automatic,
        }

    def as_notice(self) -> dict[str, Any]:
        return {
            "type": "merge" if self.automatic else "merge_proposal",
            "identity_key": self.primary,
            **self.as_dict(),
        }

    @property
    def note(self) -> str:
        return f"{self.kind}: {self.evidence}"


@dataclass
class DedupeResult:
    """The outcome of one reconciliation pass."""

    merges: list[MergeCandidate] = field(default_factory=list)
    proposals: list[MergeCandidate] = field(default_factory=list)
    applied: list[MergeCandidate] = field(default_factory=list)
    already_merged: list[MergeCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_notices(self) -> list[dict]:
        notices = [candidate.as_notice() for candidate in self.applied]
        notices.extend(candidate.as_notice() for candidate in self.proposals)
        notices.extend({"type": "dedupe_error", "message": message} for message in self.errors)
        return notices

    def summary(self) -> str:
        return (
            f"dedupe: {len(self.applied)} merge(s) applied, "
            f"{len(self.already_merged)} already merged, "
            f"{len(self.proposals)} proposal(s) for review"
        )


# ---------------------------------------------------------------------------
# Loading the world
# ---------------------------------------------------------------------------


def load_resolved(
    events_dir: Path | None = None,
    root: Path | None = None,
) -> dict[str, ResolvedRecord]:
    """Resolve every identity in the event log. Offline; no records/ needed."""
    directory = events_dir if events_dir is not None else config.events_dir(root)
    return {
        key: resolve(key, events_dir=directory)
        for key in sorted(iter_identity_keys(directory))
    }


def _precedence(root: Path | None = None) -> dict[str, int]:
    declared = {
        name: int(cfg["precedence"])
        for name, cfg in config.load_sources(root).items()
        if isinstance(cfg, dict) and cfg.get("precedence") is not None
    }
    return declared or dict(DEFAULT_SOURCE_PRECEDENCE)


def _rank(record: ResolvedRecord, precedence: Mapping[str, int]) -> tuple:
    """Sort key for "which of these two is the primary". Lower wins."""
    best_source = min(
        (precedence.get(system, 500) for system in record.source_systems),
        default=500,
    )
    return (
        _KIND_RANK.get(identity_kind(record.identity_key), 3),
        best_source,
        -len(record.source_systems),
        record.identity_key,
    )


def _order(
    left: ResolvedRecord,
    right: ResolvedRecord,
    precedence: Mapping[str, int],
) -> tuple[ResolvedRecord, ResolvedRecord]:
    return (
        (left, right)
        if _rank(left, precedence) <= _rank(right, precedence)
        else (right, left)
    )


def _dois(record: ResolvedRecord) -> set[str]:
    """Every DOI this record asserts *for itself* — its identity and its own field."""
    found: set[str] = set()
    if identity_kind(record.identity_key) == "doi":
        found.add(record.identity_key)
    normalised = normalise_doi(record.effective.get("doi"))
    if normalised:
        found.add(normalised)
    return found


def _related(record: ResolvedRecord) -> list[dict]:
    out: list[dict] = []
    for item in record.effective.get("related_identifiers") or []:
        if isinstance(item, dict):
            out.append(item)
    return out


def _relation(item: Mapping[str, Any]) -> str:
    return str(item.get("relation") or "").replace("-", "").replace("_", "").lower()


def _identifier_targets(item: Mapping[str, Any]) -> set[str]:
    """The identity keys an entry in ``related_identifiers`` could point at."""
    raw = str(item.get("identifier") or "").strip()
    if not raw:
        return set()
    targets: set[str] = set()
    normalised = normalise_doi(raw)
    if normalised:
        targets.add(normalised)
    # A GitHub URL, which is how a Zenodo software record points at its repo.
    lowered = raw.lower().rstrip("/")
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if lowered.startswith(prefix):
            targets.add(f"github|{raw.rstrip('/')[len(prefix):]}")
            targets.add(f"github|{lowered[len(prefix):]}")
    return targets


def _index_by_alias(records: Mapping[str, ResolvedRecord]) -> dict[str, str]:
    """``{alias -> identity key}`` for every DOI and lowercased key in the log."""
    index: dict[str, str] = {}
    for key, record in records.items():
        index.setdefault(key, key)
        index.setdefault(key.lower(), key)
        for doi in _dois(record):
            index.setdefault(doi, key)
    return index


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _shared_doi_candidates(
    records: Mapping[str, ResolvedRecord],
    precedence: Mapping[str, int],
) -> list[MergeCandidate]:
    """Two identities asserting one DOI (``osti-03``: a mandated duplicate)."""
    by_doi: dict[str, list[str]] = {}
    for key, record in records.items():
        for doi in _dois(record):
            by_doi.setdefault(doi, []).append(key)

    candidates: list[MergeCandidate] = []
    for doi, keys in sorted(by_doi.items()):
        if len(keys) < 2:
            continue
        ordered = sorted(keys, key=lambda k: _rank(records[k], precedence))
        primary = ordered[0]
        for secondary in ordered[1:]:
            candidates.append(
                MergeCandidate(
                    primary=primary,
                    secondary=secondary,
                    kind="shared-doi",
                    confidence=1.0,
                    evidence=f"both identities assert DOI {doi}",
                    automatic=True,
                )
            )
    return candidates


def _relation_candidates(
    records: Mapping[str, ResolvedRecord],
    precedence: Mapping[str, int],
) -> list[MergeCandidate]:
    """Declared sameness and preprint pairs.

    This is the free join key of ``gh-02``: the GitHub adapter lifts the Zenodo
    DOI out of the README badge and records it as a related identifier, and the
    Zenodo software record of ``zen-04`` points back at the repository. Either
    direction produces the same merge.
    """
    alias = _index_by_alias(records)
    candidates: list[MergeCandidate] = []

    for key, record in sorted(records.items()):
        for item in _related(record):
            relation = _relation(item)
            if relation not in SAMENESS_RELATIONS and relation not in PREPRINT_RELATIONS:
                continue
            for target in sorted(_identifier_targets(item)):
                other = alias.get(target)
                if not other or other == key:
                    continue
                left, right = records[key], records[other]
                if relation in PREPRINT_RELATIONS:
                    # The one declaring "I am the preprint of X" loses to X.
                    primary, secondary = (
                        (other, key) if relation in SUPERSEDED_BY_RELATIONS else (key, other)
                    )
                    candidates.append(
                        MergeCandidate(
                            primary=primary,
                            secondary=secondary,
                            kind="preprint-pair",
                            confidence=0.99,
                            evidence=(
                                f"{key} declares {item.get('relation')} "
                                f"{item.get('identifier')}; the published version is listed, "
                                "the preprint is linked from it"
                            ),
                            automatic=True,
                        )
                    )
                    continue
                first, second = _order(left, right, precedence)
                candidates.append(
                    MergeCandidate(
                        primary=first.identity_key,
                        secondary=second.identity_key,
                        kind="related-identifier",
                        confidence=0.95,
                        evidence=(
                            f"{key} declares {item.get('relation')} {item.get('identifier')}"
                        ),
                        automatic=True,
                    )
                )
    return candidates


def _first_author(record: ResolvedRecord) -> str:
    authors = record.effective.get("authors") or []
    for author in authors:
        if isinstance(author, Mapping):
            name = author.get("name") or author.get("family") or ""
        else:
            name = str(author)
        if name:
            return normalise_author(str(name))
    return ""


def _year(record: ResolvedRecord) -> str:
    published = str(record.effective.get("published_date") or "")
    return published[:4] if len(published) >= 4 and published[:4].isdigit() else ""


def _fuzzy_candidates(
    records: Mapping[str, ResolvedRecord],
    precedence: Mapping[str, int],
    threshold: float,
    already: set[frozenset[str]],
) -> list[MergeCandidate]:
    """Title + first author + year, bucketed so the pass stays cheap (``dc-08``)."""
    buckets: dict[tuple[str, str], list[str]] = {}
    for key, record in records.items():
        author, year = _first_author(record), _year(record)
        if not author or not year:
            continue  # too little evidence to guess with; never proposed
        buckets.setdefault((author, year), []).append(key)

    candidates: list[MergeCandidate] = []
    for (author, year), keys in sorted(buckets.items()):
        if len(keys) < 2:
            continue
        for index, key in enumerate(sorted(keys)):
            for other in sorted(keys)[index + 1:]:
                if frozenset({key, other}) in already:
                    continue
                left, right = records[key], records[other]
                if _dois(left) & _dois(right):
                    continue  # a shared DOI is not a guess; handled above
                title_a = normalise_title(str(left.effective.get("title") or ""))
                title_b = normalise_title(str(right.effective.get("title") or ""))
                if not title_a or not title_b:
                    continue
                ratio = difflib.SequenceMatcher(None, title_a, title_b).ratio()
                if ratio < threshold:
                    continue
                first, second = _order(left, right, precedence)
                candidates.append(
                    MergeCandidate(
                        primary=first.identity_key,
                        secondary=second.identity_key,
                        kind="fuzzy-title",
                        confidence=ratio,
                        evidence=(
                            f"title similarity {ratio:.2f} with the same first author "
                            f"({author}) and year ({year}); no shared DOI — REVIEW REQUIRED"
                        ),
                        automatic=False,
                    )
                )
    return candidates


def find_candidates(
    records: Mapping[str, ResolvedRecord],
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
    root: Path | None = None,
    precedence: Mapping[str, int] | None = None,
) -> list[MergeCandidate]:
    """Every merge candidate, automatic ones first, deterministically ordered."""
    ranks = precedence if precedence is not None else _precedence(root)

    automatic = _shared_doi_candidates(records, ranks) + _relation_candidates(records, ranks)

    # De-duplicate the candidates themselves: one pair, one decision.
    seen: set[frozenset[str]] = set()
    unique: list[MergeCandidate] = []
    for candidate in sorted(
        automatic, key=lambda c: (-c.confidence, c.kind, c.primary, c.secondary)
    ):
        pair = frozenset({candidate.primary, candidate.secondary})
        if pair in seen:
            continue
        seen.add(pair)
        unique.append(candidate)

    proposals = _fuzzy_candidates(records, ranks, threshold, seen)
    return sorted(
        unique + proposals,
        key=lambda c: (not c.automatic, c.kind, c.primary, c.secondary),
    )


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def _merge_links(record: ResolvedRecord, label: str) -> list[dict]:
    url = record.effective.get("url")
    if not url:
        urls = record.effective.get("source_urls") or []
        url = urls[0] if urls else None
    if not url:
        return []
    return [{"url": str(url), "label": label}]


def is_merged(
    candidate: MergeCandidate,
    records: Mapping[str, ResolvedRecord],
) -> bool:
    """Has this merge already been recorded? Keeps ``--apply`` idempotent."""
    secondary = records.get(candidate.secondary)
    if secondary is None:
        return False
    return bool(secondary.local.get("suppressed")) and (
        secondary.local.get("merged_into") == candidate.primary
    )


def apply_merge(
    candidate: MergeCandidate,
    records: Mapping[str, ResolvedRecord],
    events_dir: Path | None = None,
    root: Path | None = None,
    observed_at: str | None = None,
) -> None:
    """Record one merge as two ``annotated`` events. Never deletes anything.

    ``observed_at`` defaults to now — a merge is a decision taken at a moment,
    and the log says when. Tests and fixtures pass a fixed value so that a
    replay is reproducible byte for byte.
    """
    if not candidate.automatic:
        raise ValueError(
            f"refusing to apply a {candidate.kind} candidate: fuzzy matches are "
            "proposals for review, never merges (dc-08)"
        )
    directory = events_dir if events_dir is not None else config.events_dir(root)
    primary = records[candidate.primary]
    secondary = records[candidate.secondary]

    secondary_urls = list(secondary.effective.get("source_urls") or [])
    if secondary.effective.get("url") and secondary.effective["url"] not in secondary_urls:
        secondary_urls.insert(0, str(secondary.effective["url"]))

    annotate(
        candidate.primary,
        {
            "source_urls": secondary_urls,
            "links": _merge_links(
                secondary, f"Also catalogued as {candidate.secondary}"
            ),
            # Not a declared set-valued field, so union it here rather than
            # letting a second merge replace the first one's record of itself.
            "merged_from": sorted(
                {*(primary.local.get("merged_from") or []), candidate.secondary}
            ),
        },
        actor="reconcile",
        note=f"merge ({candidate.kind}): absorbed {candidate.secondary} — {candidate.evidence}",
        events_dir=directory,
        observed_at=observed_at,
    )
    annotate(
        candidate.secondary,
        {
            "suppressed": True,
            "merged_into": candidate.primary,
            "links": _merge_links(primary, f"Catalogued as {candidate.primary}"),
        },
        actor="reconcile",
        note=(
            f"merge ({candidate.kind}): superseded by {candidate.primary} — retained and "
            "citable, removed from listings (ADR-0027)"
        ),
        events_dir=directory,
        observed_at=observed_at,
    )


def dedupe(
    events_dir: Path | None = None,
    root: Path | None = None,
    apply: bool = False,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
    observed_at: str | None = None,
) -> DedupeResult:
    """One reconciliation pass. Never raises; every failure becomes an error line."""
    result = DedupeResult()
    directory = events_dir if events_dir is not None else config.events_dir(root)
    records = load_resolved(directory, root)

    for candidate in find_candidates(records, threshold=threshold, root=root):
        if not candidate.automatic:
            result.proposals.append(candidate)
            continue
        result.merges.append(candidate)
        if is_merged(candidate, records):
            result.already_merged.append(candidate)
            continue
        if not apply:
            continue
        try:
            apply_merge(
                candidate, records, events_dir=directory, root=root, observed_at=observed_at
            )
        except (ValueError, KeyError) as exc:
            result.errors.append(f"{candidate.primary} <- {candidate.secondary}: {exc}")
            continue
        result.applied.append(candidate)
        records = load_resolved(directory, root)  # a merge changes later decisions

    log.info("%s", result.summary())
    return result


# ---------------------------------------------------------------------------
# The proposal file
# ---------------------------------------------------------------------------


def proposals_path(root: Path | None = None) -> Path:
    """``state/merge-proposals.json`` — the curator's review queue."""
    return config.state_dir(root) / "merge-proposals.json"


def write_proposals(
    result: DedupeResult,
    path: Path | None = None,
    root: Path | None = None,
) -> Path:
    """Write the review queue deterministically, so a no-change pass is a no-diff."""
    target = path or proposals_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": (
            "Fuzzy matches proposed by `python -m harvest dedupe`. Nothing here has been "
            "applied. Confirm one by writing the merge annotation (see "
            "docs/runbooks/correct-a-record.md); reject one by leaving it alone."
        ),
        "proposals": [candidate.as_dict() for candidate in result.proposals],
        "merges": [candidate.as_dict() for candidate in result.merges],
    }
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False,
                   separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )
    return target


def read_proposals(path: Path | None = None, root: Path | None = None) -> dict[str, Any]:
    target = path or proposals_path(root)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def candidates_as_notices(candidates: Iterable[MergeCandidate]) -> list[dict]:
    return [candidate.as_notice() for candidate in candidates]
