# `data/` — the catalogue itself

Everything the harvest reads or writes, and nothing else. The code that produces
it lives in `harvest/`; the code that renders it lives in `site/`; the registers
that configure it (`sources.yaml`, `organizations.yaml`, `groups.yaml`) are
hand-edited configuration and stay at the repository root.

Resolved in exactly one place — `harvest.config` — so that nothing else in the
tree hardcodes a data path.

| Directory | What it is |
|---|---|
| [`events/`](events/) | **The source of truth.** Append-only JSONL, one file per identity, ordered by observation time (ADR-0037). Never edited, never reordered, never deleted. |
| [`records/`](records/) | Derived CKAN `package` dicts, one JSON per record (ADR-0021). Regenerable — `make materialize` rebuilds them byte-for-byte from `events/`. Committed anyway: it is what the site globs and what CKAN receives on promotion day. |
| [`annotations/`](annotations/) | Curatorial intent as YAML, replayed into `annotated` events. Additive only; source metadata is never edited (ADR-0038). |
| [`cache/`](cache/) | The committed LLM extraction cache, keyed by `sha256(content + prompt_version + model_id)` (ADR-0025). What makes an AI-assisted harvest reproducible. |
| [`state/`](state/) | `last-run.json` (the run report, and the cron heartbeat written on every run — ADR-0029), `link-check.json`, `merge-proposals.json`, `pending-extraction.json` (ADR-0031). |
| [`fixtures/`](fixtures/) | The test and gallery fixture set; `fixtures-catalogue.md` is the specification. |

Two rules govern this whole subtree:

1. **`events/` is the truth; `records/` is derived.** Never edit a record file —
   the next replay overwrites it. Correct a record by writing an annotation
   (`docs/runbooks/correct-a-record.md`).
2. **Withdrawn records are kept, never deleted** (ADR-0027). A tombstone is
   information; a missing file is a mystery.
