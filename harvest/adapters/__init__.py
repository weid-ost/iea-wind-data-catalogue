"""Source adapters. One module per source, one class per module.

Read ``harvest/CONTRACT.md`` before adding one. The short version:

    from harvest.adapters.base import Adapter, register

    @register
    class ZenodoAdapter(Adapter):
        source_name = "zenodo"
        tier = 1
        source_key_semantics = "InvenioRDM record revision id"

        def harvest(self, max_records=DEFAULT_MAX_RECORDS): ...   # -> Iterable[RawObservation]
        def map(self, raw): ...                       # -> MappedObservation

Adapters are discovered by name from ``sources.yaml``; the module name, the
file name and ``source_name`` must all match the key in that file.
"""

from harvest.adapters.base import (  # noqa: F401
    ADAPTERS,
    Adapter,
    AdapterError,
    SourceConfig,
    SourceResult,
    available_adapters,
    get_adapter,
    load_adapters,
    register,
    run_adapter,
)

__all__ = [
    "ADAPTERS",
    "Adapter",
    "AdapterError",
    "SourceConfig",
    "SourceResult",
    "available_adapters",
    "get_adapter",
    "load_adapters",
    "register",
    "run_adapter",
]
