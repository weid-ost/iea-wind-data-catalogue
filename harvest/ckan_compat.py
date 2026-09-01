"""The ``validate-ckan-compat`` gate (plan §2.2, fixture ``x-08``).

The trap this exists to prevent: a static site renders records CKAN would
reject, nobody notices for two years, and promotion day discovers three
thousand invalid records at once. So every record is held to what CKAN's API
would actually accept, on every run and in CI:

* ``name`` — unique, lowercase, ``a-z 0-9 - _``, 2-100 chars
* ``tags`` — ``a-z A-Z 0-9 - _ .``, 2-100 chars each
* ``license_id`` — present in :data:`harvest.licenses.LICENSE_REGISTER`
* ``extras`` — **string values only**, unique non-empty keys
* ``owner_org`` / ``groups`` — must exist in ``organizations.yaml`` /
  ``groups.yaml``, which are canonical data in the repo
* ``state`` — one of CKAN's own lifecycle values
* ``resources`` — every resource needs a ``url``

The validator collects **every** violation rather than raising on the first,
because the useful output is the full list. ``python -m harvest validate``
exits non-zero and prints them all.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from harvest import config
from harvest.licenses import LICENSE_IDS
from harvest.models import CKAN_STATES, MAX_COLLECTION_ITEMS, MAX_TEXT_LENGTH
from harvest.urls import ALLOWED_URL_SCHEMES, is_safe_url

__all__ = [
    "NAME_RE",
    "TAG_RE",
    "Violation",
    "tagify",
    "validate_package",
    "validate_records",
    "format_violations",
    "main",
]

#: CKAN ``package.name``: lowercase alphanumerics, ``-`` and ``_``, 2-100 chars.
NAME_RE = re.compile(r"^[a-z0-9_-]{2,100}$")

#: CKAN tag: alphanumerics, ``-``, ``_`` and ``.``, 2-100 chars. Spaces are
#: allowed by some CKAN configurations; the plan forbids them, so we do too.
TAG_RE = re.compile(r"^[A-Za-z0-9._-]{2,100}$")

_TAG_STRIP_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Violation:
    """One reason CKAN would refuse a record."""

    record: str          # the record name, or the file path for unparseable files
    field: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        location = f"{self.path}: " if self.path else ""
        return f"{location}{self.record}: {self.field}: {self.message}"


def tagify(text: str) -> str:
    """Coerce arbitrary keyword text into a CKAN-legal tag, or ``""``.

    Diacritics transliterate (``Søren`` becomes ``soren``) and the result is
    lowercased, because tags are facet values and a facet list that contains
    both ``Lidar`` and ``lidar`` is a bug. Everything else collapses to ``-``.
    Returns ``""`` when nothing legal is left or the result is shorter than
    CKAN's two-character minimum, and the caller drops it — a tag is a
    convenience, never data.
    """
    folded = unicodedata.normalize("NFKD", str(text))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = (
        folded.replace("ø", "o").replace("Ø", "O")
        .replace("æ", "ae").replace("Æ", "Ae")
        .replace("ß", "ss")
        .replace("ł", "l").replace("Ł", "L")
        .replace("đ", "d").replace("Đ", "D")
    )
    folded = folded.encode("ascii", "ignore").decode("ascii").lower()
    folded = _TAG_STRIP_RE.sub("-", folded).strip("-._")
    folded = re.sub(r"-{2,}", "-", folded)
    if len(folded) > 100:
        folded = folded[:100].strip("-._")
    return folded if len(folded) >= 2 else ""


def validate_package(
    package: dict[str, Any],
    known_orgs: set[str] | None = None,
    known_groups: set[str] | None = None,
    path: str | None = None,
) -> list[Violation]:
    """Every way ``package`` would fail CKAN, as a list."""
    violations: list[Violation] = []
    name = package.get("name")
    record = str(name) if isinstance(name, str) and name else (path or "<unnamed>")

    def fail(field: str, message: str) -> None:
        violations.append(Violation(record=record, field=field, message=message, path=path))

    # --- name ----------------------------------------------------------------
    if not isinstance(name, str) or not name:
        fail("name", "missing")
    elif not NAME_RE.match(name):
        fail(
            "name",
            f"{name!r} is not a legal CKAN slug "
            "(lowercase a-z, 0-9, '-' and '_' only, 2-100 chars)",
        )

    # --- title ---------------------------------------------------------------
    title = package.get("title")
    if not isinstance(title, str) or not title.strip():
        fail("title", "missing or empty")

    # --- notes ---------------------------------------------------------------
    notes = package.get("notes")
    if notes is not None and not isinstance(notes, str):
        fail("notes", "must be a string")
    elif isinstance(notes, str) and len(notes) > MAX_TEXT_LENGTH:
        # The namespaces truncate on the way in; this refuses a record that got
        # past them anyway. Without it, one upstream description inflates a
        # record file, an HTML page and a Pagefind entry without limit, and all
        # three are committed on every change (scrape-07).
        fail("notes", f"{len(notes)} characters, over the {MAX_TEXT_LENGTH}-character cap")

    # --- licence -------------------------------------------------------------
    license_id = package.get("license_id")
    if not isinstance(license_id, str) or not license_id:
        fail("license_id", "missing (use 'notspecified', never an empty string)")
    elif license_id not in LICENSE_IDS:
        fail("license_id", f"{license_id!r} is not in the licence register")

    # --- tags ----------------------------------------------------------------
    tags = package.get("tags", [])
    if not isinstance(tags, list):
        fail("tags", "must be a list")
    elif len(tags) > MAX_COLLECTION_ITEMS:
        fail("tags", f"{len(tags)} tags, over the {MAX_COLLECTION_ITEMS}-item cap")
    else:
        seen_tags: set[str] = set()
        for index, tag in enumerate(tags):
            if not isinstance(tag, dict) or "name" not in tag:
                fail(f"tags[{index}]", "must be an object with a 'name'")
                continue
            tag_name = tag["name"]
            if not isinstance(tag_name, str) or not TAG_RE.match(tag_name):
                fail(
                    f"tags[{index}]",
                    f"{tag_name!r} is not a legal CKAN tag "
                    "(A-Z a-z 0-9 '-' '_' '.', 2-100 chars)",
                )
            elif tag_name in seen_tags:
                fail(f"tags[{index}]", f"duplicate tag {tag_name!r}")
            else:
                seen_tags.add(tag_name)

    # --- extras --------------------------------------------------------------
    extras = package.get("extras", [])
    if not isinstance(extras, list):
        fail("extras", "must be a list")
    else:
        seen_keys: set[str] = set()
        for index, extra in enumerate(extras):
            if not isinstance(extra, dict) or "key" not in extra or "value" not in extra:
                fail(f"extras[{index}]", "must be an object with 'key' and 'value'")
                continue
            key, value = extra["key"], extra["value"]
            if not isinstance(key, str) or not key.strip():
                fail(f"extras[{index}]", "key must be a non-empty string")
            elif key in seen_keys:
                fail(f"extras[{index}]", f"duplicate extras key {key!r}")
            else:
                seen_keys.add(key)
            if not isinstance(value, str):
                fail(
                    f"extras[{key!r}]",
                    f"value must be a string, got {type(value).__name__} "
                    "(encode lists and objects as JSON strings)",
                )

    # --- url and resources ---------------------------------------------------
    # Defence in depth (scrape-03, eventlog-06). The namespaces filter URL
    # schemes on the way in; this refuses a record that reached records/ with
    # an unlinkable one anyway — a hand-edited file, a merge, a future adapter
    # that builds a package dict directly. Escaping an href does not disarm
    # `javascript:`, so the gate says no rather than the site rendering it.
    url = package.get("url")
    if url is not None and not is_safe_url(url):
        fail("url", f"{url!r} is not an allowed URL scheme "
                    f"(allowed: {', '.join(sorted(ALLOWED_URL_SCHEMES))})")

    resources = package.get("resources", [])
    if not isinstance(resources, list):
        fail("resources", "must be a list")
    elif len(resources) > MAX_COLLECTION_ITEMS:
        fail("resources", f"{len(resources)} resources, over the "
                          f"{MAX_COLLECTION_ITEMS}-item cap")
    else:
        for index, resource in enumerate(resources):
            if not isinstance(resource, dict):
                fail(f"resources[{index}]", "must be an object")
            elif not resource.get("url"):
                fail(f"resources[{index}]", "resource has no url")
            elif not is_safe_url(resource["url"]):
                fail(
                    f"resources[{index}]",
                    f"{resource['url']!r} is not an allowed URL scheme "
                    f"(allowed: {', '.join(sorted(ALLOWED_URL_SCHEMES))})",
                )

    # --- org and groups ------------------------------------------------------
    # REQUIRED, not optional. CKAN's `package_create` refuses a dataset with no
    # owning organisation on a default install, so "POSTable to CKAN with no
    # transformation" (ADR-0021) is only true if every record carries one — and
    # ADR-0023's institution facet has nothing to count otherwise
    # (product-e2e-02). `harvest.institutions.infer_owner_org` always returns a
    # register entry, including a real `unattributed` entry for the records it
    # honestly cannot attribute, so there is no legitimate reason to be here
    # without one.
    owner_org = package.get("owner_org")
    if owner_org is None:
        fail("owner_org", "missing — CKAN refuses a dataset with no owning organisation")
    elif not isinstance(owner_org, str) or not owner_org:
        fail("owner_org", "must be a non-empty string")
    elif known_orgs is not None and owner_org not in known_orgs:
        fail("owner_org", f"{owner_org!r} is not in organizations.yaml")

    groups = package.get("groups", [])
    if not isinstance(groups, list):
        fail("groups", "must be a list")
    else:
        for index, group in enumerate(groups):
            if not isinstance(group, dict) or not group.get("name"):
                fail(f"groups[{index}]", "must be an object with a 'name'")
                continue
            group_name = group["name"]
            if known_groups is not None and group_name not in known_groups:
                fail(f"groups[{index}]", f"{group_name!r} is not in groups.yaml")

    # --- state ---------------------------------------------------------------
    state = package.get("state", "active")
    if state not in CKAN_STATES:
        fail("state", f"{state!r} is not one of {CKAN_STATES}")

    return violations


def validate_records(
    records_directory: Path | None = None,
    root: Path | None = None,
    known_orgs: set[str] | None = None,
    known_groups: set[str] | None = None,
) -> list[Violation]:
    """Validate every ``records/*.json``, including cross-record uniqueness."""
    records_directory = records_directory or config.records_dir(root)
    known_orgs = known_orgs if known_orgs is not None else config.organization_names(root)
    known_groups = known_groups if known_groups is not None else config.group_names(root)

    violations: list[Violation] = []
    names: dict[str, str] = {}
    if not records_directory.exists():
        return violations

    for path in sorted(records_directory.glob("*.json")):
        try:
            package = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            violations.append(
                Violation(record=path.name, field="<file>", message=f"invalid JSON: {exc}",
                          path=str(path))
            )
            continue
        if not isinstance(package, dict):
            violations.append(
                Violation(record=path.name, field="<file>",
                          message="top level must be a CKAN package object", path=str(path))
            )
            continue

        violations.extend(
            validate_package(package, known_orgs, known_groups, path=str(path))
        )

        name = package.get("name")
        if isinstance(name, str) and name:
            if name != path.stem:
                violations.append(
                    Violation(record=name, field="name",
                              message=f"does not match filename stem {path.stem!r}",
                              path=str(path))
                )
            if name in names:
                violations.append(
                    Violation(record=name, field="name",
                              message=f"duplicate name, also in {names[name]}", path=str(path))
                )
            else:
                names[name] = str(path)

    return violations


def format_violations(violations: Iterable[Violation]) -> str:
    return "\n".join(f"  - {violation}" for violation in violations)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Exits non-zero listing every violation."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m harvest validate",
        description="Validate records/*.json against CKAN's package_create rules.",
    )
    parser.add_argument("--records", type=Path, default=None, help="records directory")
    parser.add_argument("--root", type=Path, default=None, help="repository root")
    args = parser.parse_args(argv)

    records_directory = args.records or config.records_dir(args.root)
    violations = validate_records(records_directory, root=args.root)
    count = len(list(records_directory.glob("*.json"))) if records_directory.exists() else 0

    if violations:
        print(f"validate-ckan-compat: FAIL — {len(violations)} violation(s) "
              f"across {count} record(s)", file=sys.stderr)
        print(format_violations(violations), file=sys.stderr)
        return 1
    print(f"validate-ckan-compat: OK — {count} record(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
