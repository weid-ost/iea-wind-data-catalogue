"""``python -m harvest`` — run, materialize, validate, extract, report.

    uv run python -m harvest run [--source X] [--limit N]   # DEFAULT LIMIT: 5
    uv run python -m harvest materialize
    uv run python -m harvest validate
    uv run python -m harvest annotations [--dry-run]
    uv run python -m harvest dedupe [--apply] [--threshold R]
    uv run python -m harvest linkcheck [--limit N]
    uv run python -m harvest extract [--limit N]
    uv run python -m harvest report

``run`` does the whole pipeline: harvest every enabled source, append events on
change only, replay ``annotations/`` into ``annotated`` events, replay
everything into ``records/``, validate, and write ``state/last-run.json`` —
**always**, including when a source failed, because that file is the cron
keepalive (plan §3.3).

``materialize`` and ``run`` both replay ``annotations/`` first, idempotently, so
a curator writes one YAML file and runs one command
([[correct-a-record]] §2). ``dedupe`` and ``linkcheck`` are separate verbs
because one writes merge decisions and the other talks to seven upstreams;
neither belongs in an unattended weekly run without being asked for.
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
    run.add_argument("--no-annotations", action="store_true",
                     help="skip replaying annotations/ into annotated events")
    run.add_argument("--linkcheck", action="store_true",
                     help="check every record's outbound links and report the dead ones")

    materialize = sub.add_parser("materialize", help="replay events/ into records/")
    materialize.add_argument("--no-prune", action="store_true",
                             help="keep record files that have no backing events")
    materialize.add_argument("--no-annotations", action="store_true",
                             help="skip replaying annotations/ into annotated events")

    sub.add_parser("validate", help="run the CKAN-compat gate over records/")

    annotations = sub.add_parser(
        "annotations", help="replay annotations/*.yaml into annotated events (idempotent)"
    )
    annotations.add_argument("--dry-run", action="store_true",
                             help="say what would be appended, append nothing")

    dedupe = sub.add_parser(
        "dedupe", help="find cross-source duplicates; propose or apply merges"
    )
    dedupe.add_argument("--apply", action="store_true",
                        help="append the merge events for automatic candidates "
                             "(fuzzy matches are never applied)")
    dedupe.add_argument("--threshold", type=float, default=None,
                        help="fuzzy title-similarity threshold (default: 0.90)")

    linkcheck = sub.add_parser("linkcheck", help="check every record's outbound links")
    linkcheck.add_argument("--limit", type=int, default=None,
                           help="max records to check this pass")

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
        # Optional adapter reporting surfaces (Tier 3 uses both): a DOI drop log
        # so `dropped_dois` is never silent (ADR-0024 rule 3), and coverage
        # notices so a recorded gap reaches the curator's monthly read.
        drop_log = getattr(adapter, "drop_log", None)
        if drop_log is not None and len(drop_log):
            report.dropped_dois.extend(drop_log.as_notices())
        report.add_notices(getattr(adapter, "notices", []) or [])

    # Tier-3 accounting (ADR-0025, ADR-0031). Written even when no Tier-3
    # source ran, because the site renders the backlog unconditionally.
    from harvest import extract as extraction

    report.cache_hits = extraction.STATS.hits
    report.cache_misses = extraction.STATS.misses
    report.pending_extraction = len(
        extraction.read_pending(config.state_dir(root) if root else None)
    )

    if not args.no_annotations:
        from harvest.annotations import apply_annotations, check_pins

        annotation_outcome = apply_annotations(root=root, events_dir=config.events_dir(root))
        report.add_notices(annotation_outcome.as_notices())
        report.add_notices(check_pins(root=root, events_dir=config.events_dir(root)))
        log.info("%s", annotation_outcome.summary())

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

    if args.linkcheck:
        from harvest.linkcheck import check_records, write_link_report

        link_report = check_records(root=root, limit=args.limit)
        write_link_report(link_report, root=root)
        report.add_notices(link_report.as_notices())
        log.info("%s", link_report.summary())

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
    if not args.no_annotations:
        from harvest.annotations import apply_annotations, check_pins

        annotation_outcome = apply_annotations(
            root=args.root, events_dir=config.events_dir(args.root)
        )
        print(annotation_outcome.summary())
        for notice in check_pins(root=args.root, events_dir=config.events_dir(args.root)):
            print(f"  pin_notice: {notice['identity_key']}: {notice['reason']}")
        for message in annotation_outcome.errors:
            print(f"  - {message}", file=sys.stderr)

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


def cmd_annotations(args: argparse.Namespace) -> int:
    from harvest.annotations import apply_annotations, check_pins

    outcome = apply_annotations(
        root=args.root, events_dir=config.events_dir(args.root), dry_run=args.dry_run
    )
    prefix = "would apply" if args.dry_run else "applied"
    print(f"annotations: {prefix} {len(outcome.applied)}, "
          f"{len(outcome.skipped)} already present, "
          f"{len(outcome.pending)} waiting for their record")
    for annotation in outcome.applied:
        print(f"  + {annotation.identity_key}: {sorted(annotation.local)}")
    for annotation in outcome.pending:
        print(f"  … {annotation.identity_key}: waits for the record to be harvested")
    for notice in check_pins(
        root=args.root, events_dir=config.events_dir(args.root), dry_run=args.dry_run
    ):
        print(f"  pin_notice: {notice['identity_key']}: {notice['reason']}")
    for message in outcome.errors:
        print(f"  - {message}", file=sys.stderr)
    return 1 if outcome.errors else 0


def cmd_dedupe(args: argparse.Namespace) -> int:
    from harvest.dedupe import DEFAULT_FUZZY_THRESHOLD, dedupe, write_proposals

    threshold = args.threshold if args.threshold is not None else DEFAULT_FUZZY_THRESHOLD
    result = dedupe(root=args.root, apply=args.apply, threshold=threshold)
    path = write_proposals(result, root=args.root)
    print(result.summary())
    for candidate in result.applied:
        print(f"  merged  {candidate.secondary} -> {candidate.primary}  ({candidate.kind})")
    for candidate in result.merges:
        if candidate not in result.applied and not args.apply:
            print(f"  would merge {candidate.secondary} -> {candidate.primary} "
                  f"({candidate.kind}); re-run with --apply")
    for candidate in result.proposals:
        print(f"  PROPOSAL {candidate.secondary} ~ {candidate.primary} "
              f"({candidate.confidence:.2f}) — review by hand, never auto-merged")
    print(f"wrote {path}")
    for message in result.errors:
        print(f"  - {message}", file=sys.stderr)
    return 1 if result.errors else 0


def cmd_linkcheck(args: argparse.Namespace) -> int:
    from harvest.linkcheck import check_records, write_link_report

    report = check_records(root=args.root, limit=args.limit)
    path = write_link_report(report, root=args.root)
    print(report.summary())
    for name, urls in report.dead_by_record().items():
        for url in urls:
            status = report.checked[url]
            print(f"  DEAD {name}: {url} "
                  f"({status.status_code or status.error}) — record retained")
    print(f"wrote {path}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from harvest import extract as extraction

    limit = args.limit if args.limit is not None else extraction.MAX_EXTRACTIONS
    state_directory = config.state_dir(args.root) if args.root else None
    cache_directory = config.cache_dir(args.root) if args.root else None
    resolved = extraction.drain_pending(
        limit=limit, state_directory=state_directory, cache_directory=cache_directory
    )
    print(f"extract: resolved {resolved} pending extraction(s)")
    remaining = len(extraction.read_pending(state_directory))
    if remaining:
        print(f"extract: {remaining} still queued (see state/pending-extraction.json)")
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
    "annotations": cmd_annotations,
    "dedupe": cmd_dedupe,
    "linkcheck": cmd_linkcheck,
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
