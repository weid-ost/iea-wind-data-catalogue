"""Shared fixtures.

Every test that touches the filesystem works in a throwaway repository root
built by the ``repo`` fixture. Nothing in the suite writes into the real
``events/``, ``records/`` or ``state/``, and nothing touches the network — the
DOI resolver and every adapter take an injected client for exactly that reason.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REAL_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repository root with the real registers copied in."""
    for name in ("organizations.yaml", "groups.yaml", "sources.yaml"):
        shutil.copy(REAL_ROOT / name, tmp_path / name)
    for name in ("events", "records", "state", "cache"):
        (tmp_path / name).mkdir()
    return tmp_path


@pytest.fixture
def events_dir(repo: Path) -> Path:
    return repo / "events"


@pytest.fixture
def records_dir(repo: Path) -> Path:
    return repo / "records"


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """A DOI resolver stand-in. ``known`` maps a DOI to the agency that has it."""

    def __init__(self, known: dict[str, str] | None = None, raise_for: set[str] | None = None):
        self.known = known or {}
        self.raise_for = raise_for or set()
        self.calls: list[str] = []

    def get(self, url: str, **kwargs):  # noqa: ANN003
        self.calls.append(url)
        for prefix, agency in (
            ("https://api.datacite.org/dois/", "datacite"),
            ("https://api.crossref.org/works/", "crossref"),
        ):
            if url.startswith(prefix):
                doi = url[len(prefix):]
                if agency in self.raise_for:
                    raise ConnectionError(f"{agency} is down")
                if self.known.get(doi) == agency:
                    return FakeResponse(200, {"data": {"id": doi}})
                return FakeResponse(404)
        return FakeResponse(404)


@pytest.fixture
def fake_client() -> type[FakeClient]:
    return FakeClient
