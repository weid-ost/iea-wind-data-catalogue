"""``python -m harvest`` entry point. See :mod:`harvest.cli`."""

from __future__ import annotations

from harvest.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
