"""``python -m harvest`` — run, materialize, validate, extract, report.

    uv run python -m harvest run [--source X] [--limit N]   # DEFAULT LIMIT: 5
    uv run python -m harvest materialize
    uv run python -m harvest validate
    uv run python -m harvest extract [--limit N]
    uv run python -m harvest report

``run`` does the whole pipeline: harvest every enabled source, append events on
change only, replay into ``records/``, validate, and write
``state/last-run.json`` — **always**, including when a source failed, because
that file is the cron keepalive (plan §3.3).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from harvest import DEFAULT_LIMIT, __version__, config
from harvest.adapters.base import (
    ADAPTERS,
    SourceConfig,
    get_adapter,
    load_adapters,
    run_adapter,
)
from harvest.ckan_compat import format_violations, validate_records
from harvest.materialize import materialize_all
from harvest.runreport import RunReport

__all__ = ["main", "build_parser"]

log = logging.getLogger("harvest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harvest",
        description="IEA Wind Data Catalogue harvester.",
    )
    parser.add_argument("--version", action="version", version=f"harvest {__version__}")
    parser.add_argument("--root", type=Path, default=None,
                        help="repository root (default: the package's parent)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="harvest, materialize, validate, report")
    run.add_argument("--source", action="append", default=None,
                     help="limit to one source (repeatable); default: all enabled")
    run.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=(
            f"max records per source. DEFAULT: {DEFAULT_LIMIT}. "
            "This is a deliberate prototype cap — raise it consciously."
        ),
    )
    run.add_argument("--dry-run", action="store_true",
                     help="harvest and report, but append no events")
    run.add_argument("--no-materialize", action="store_true",
                     help="skip the replay into records/")

    materialize = sub.add_parser("materialize", help="replay events/ into records/")
    materialize.add_argument("--no-prune", action="store_true",
                             help="keep record files that have no backing events")

    sub.add_parser("validate", help="run the CKAN-compat gate over records/")

    extract = sub.add_parser("extract", help="drain state/pending-extraction.json (Tier 3)")
    extract.add_argument("--limit", type=int, default=None, help="max extractions this pass")

    sub.add_parser("report", help="print the last run report")
    sub.add_parser("sources", help="list configured sources and their adapters")
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    root: Path | None = args.root
    report = RunReport(limit=args.limit)
    sources = config.load_sources(root)
    if not sources:
        log.warning("sources.yaml declares no sources; nothing to harvest")

    wanted = args.source or list(sources)
    load_adapters(list(sources), root)

    for name in wanted:
        source_config = SourceConfig.from_mapping(name, sources.get(name))
        limit = min(args.limit, source_config.max_records or args.limit)
        try:
            adapter_class = get_adapter(name)
        except Exception as exc:
            log.error("source %s has no adapter: %s", name, exc)
            report.add_source(name, {"enabled": source_config.enabled, "reachable": False,
                                     "implemented": False, "seen": 0, "changed": 0,
                                     "skipped_unchanged": 0, "errors": [str(exc)]})
            continue
        adapter = adapter_class(config=source_config)
        result = run_adapter(
            adapter,
            limit=limit,
            events_dir=config.events_dir(root),
            dry_run=args.dry_run,
        )
        report.add_source(name, result)

    if not args.no_materialize:
        outcome = materialize_all(root=root)
        report.records_total = outcome.total
        report.records_written = len(outcome.written)
        report.records_unchanged = len(outcome.unchanged)
        report.records_pruned = len(outcome.pruned)
        report.add_notices(outcome.notices)
        report.unmapped_licenses = outcome.unmapped_licenses
        report.validation_violations = [str(v) for v in outcome.violations]
        report.ok = outcome.ok

    report.finished_at = None  # stamped at write time
    path = report.write(root=root)
    log.info("wrote %s", path)

    if report.validation_violations:
        print("validate-ckan-compat: FAIL", file=sys.stderr)
        for violation in report.validation_violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    return 0


def cmd_materialize(args: argparse.Namespace) -> int:
    outcome = materialize_all(root=args.root, prune=not args.no_prune)
    print(
        f"materialize: {outcome.total} record(s) "
        f"({len(outcome.written)} written, {len(outcome.unchanged)} unchanged, "
        f"{len(outcome.pruned)} pruned)"
    )
    if outcome.violations:
        print("validate-ckan-compat: FAIL", file=sys.stderr)
        print(format_violations(outcome.violations), file=sys.stderr)
        return 1
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    records_directory = config.records_dir(args.root)
    violations = validate_records(records_directory, root=args.root)
    count = len(list(records_directory.glob("*.json"))) if records_directory.exists() else 0
    if violations:
        print(
            f"validate-ckan-compat: FAIL — {len(violations)} violation(s) "
            f"across {count} record(s)",
            file=sys.stderr,
        )
        print(format_violations(violations), file=sys.stderr)
        return 1
    print(f"validate-ckan-compat: OK — {count} record(s)")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from harvest import extract as extraction

    limit = args.limit if args.limit is not None else extraction.MAX_EXTRACTIONS
    try:
        resolved = extraction.drain_pending(limit=limit)
    except NotImplementedError as exc:
        print(f"extract: {exc}", file=sys.stderr)
        return 2
    print(f"extract: resolved {resolved} pending extraction(s)")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from harvest.runreport import read_run_report

    report = read_run_report(root=args.root)
    if not report:
        print("no run report yet — run `python -m harvest run`", file=sys.stderr)
        return 1
    import json

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    sources = config.load_sources(args.root)
    load_adapters(list(sources), args.root)
    for name in sorted(sources):
        source_config = SourceConfig.from_mapping(name, sources[name])
        adapter = ADAPTERS.get(name)
        state = "enabled" if source_config.enabled else "disabled"
        adapter_name = f"{adapter.__module__}.{adapter.__name__}" if adapter else "(no adapter)"
        print(
            f"{name:<10} tier {source_config.tier}  {state:<8} "
            f"max {source_config.max_records:<3} {adapter_name}"
        )
    return 0


_COMMANDS = {
    "run": cmd_run,
    "materialize": cmd_materialize,
    "validate": cmd_validate,
    "extract": cmd_extract,
    "report": cmd_report,
    "sources": cmd_sources,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
