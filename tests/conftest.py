"""Shared fixtures.

Every test that touches the filesystem works in a throwaway repository root
built by the ``repo`` fixture. Nothing in the suite writes into the real
``events/``, ``records/`` or ``state/``, and nothing touches the network — the
DOI resolver and every adapter take an injected client for exactly that reason.
"""

from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import httpx
import pytest

REAL_ROOT = Path(__file__).resolve().parent.parent

#: The seven sources in ``sources.yaml``, in track order.
SEVEN_SOURCES = ("zenodo", "datacite", "crossref", "github", "osti", "ieawind", "wdh")


def stub_sources() -> list[str]:
    """The sources whose adapter is still the foundation stub.

    A stub module carries **two** markers: the module attribute ``_TODO`` and
    the word ``STUB.`` in its docstring. A track that implements its adapter
    removes both. Either one is enough to recognise a stub here, because the
    five adapter tracks each keyed off a different one and the two Tier-3
    tracks have not landed yet — accepting both keeps this correct however the
    remaining tracks are written.

    Tests about stub behaviour iterate over this rather than over all seven, so
    that they keep testing what they mean as adapters land — and so that no
    test in this suite accidentally calls a live API.
    """
    stubs = []
    for name in SEVEN_SOURCES:
        module = importlib.import_module(f"harvest.adapters.{name}")
        if hasattr(module, "_TODO") or "STUB" in (module.__doc__ or ""):
            stubs.append(name)
    return stubs


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the "nothing touches the network" rule enforceable, not aspirational.

    Once a track ships a real adapter, ``python -m harvest run`` in the CLI
    tests would otherwise reach out to a live API. Blocking happens at the
    **real transport**, which is deliberate on two counts:

    * ``httpx.MockTransport`` still works, so an adapter test can inject a fake
      upstream and exercise the whole client stack offline;
    * an adapter that reaches for the network anyway sees a transport error,
      which :class:`harvest.http.HarvestClient` turns into an unreachable
      source — the degradation path every adapter must already handle, rather
      than a flaky, impolite test that depends on somebody else's uptime.

    Adapters under test get their client injected (``Adapter(client=...)``).
    """

    def blocked(self, request: httpx.Request, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError(
            f"network access is blocked in tests (attempted {request.url}); "
            "inject an httpx.MockTransport instead"
        )

    async def blocked_async(self, request: httpx.Request, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError(
            f"network access is blocked in tests (attempted {request.url}); "
            "inject an httpx.MockTransport instead"
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)


#: Every credential the harvester can pick up from the environment. Cleared for
#: every test, so that a developer with a real ``GITHUB_TOKEN`` exported (or a
#: CI job that has one) never gets a different suite from everyone else. The
#: Tier-3 tests in particular assert what happens when there is *no* key.
_CREDENTIAL_ENV = (
    "HARVEST_LLM_TOKEN",
    "HARVEST_LLM_ENDPOINT",
    "HARVEST_LLM_MODEL",
    "HARVEST_LLM_CACHE_LINEAGE",
    "HARVEST_MAX_EXTRACTIONS",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_MODEL",
    "MODEL_ID",
    "GITHUB_TOKEN",
    "WDH_API_TOKEN",
    "HARVEST_ROOT",
)


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test sees a real credential, whatever the developer has exported.

    The companion to :func:`no_network`: that one stops the suite reaching a
    live API, this one stops it *authenticating* to one, and stops a stray
    ``HARVEST_LLM_*`` in the shell turning the deterministic Tier-3 assertions
    into a live inference call (ADR-0031).
    """
    for name in _CREDENTIAL_ENV:
        monkeypatch.delenv(name, raising=False)


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
