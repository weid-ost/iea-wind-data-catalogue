# IEA Wind Data Catalogue.
#
# Everything Python goes through `uv run` so it uses the pinned interpreter in
# .python-version and the pinned lockfile — never whatever `python` happens to
# be on PATH (ADR-0034). CI uses `uv sync --frozen`; `make sync` here uses
# --frozen too, so a stale lockfile fails loudly instead of being rewritten.

.DEFAULT_GOAL := help
.PHONY: help sync harvest materialize validate annotations dedupe linkcheck test extract \
        build-tokens site gates clean

# Per-source record cap for `make harvest`. Blank = the harvester's default.
#   make harvest MAX_RECORDS=200
MAX_RECORDS ?=

UV ?= uv
NPM ?= npm

help:  ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

sync:  ## install the pinned Python environment (uv sync --frozen)
	$(UV) sync --frozen --dev

harvest:  ## harvest every enabled source (MAX_RECORDS=N overrides the default cap) and materialize
	$(UV) run python -m harvest run $(if $(MAX_RECORDS),--max-records $(MAX_RECORDS))

materialize:  ## replay events/ into records/ (derived; safe to delete and rebuild)
	$(UV) run python -m harvest materialize

validate:  ## CKAN-compat gate over records/ — exits non-zero listing every violation
	$(UV) run python -m harvest validate

annotations:  ## replay annotations/*.yaml into annotated events (idempotent)
	$(UV) run python -m harvest annotations

dedupe:  ## find cross-source duplicates; propose merges (add APPLY=1 to record them)
	$(UV) run python -m harvest dedupe $(if $(APPLY),--apply,)

linkcheck:  ## check every record's outbound links; dead links are reported, never deleted
	$(UV) run python -m harvest linkcheck

test:  ## run the test suite
	$(UV) run pytest

extract:  ## drain state/pending-extraction.json through the LLM (human-operated)
	$(UV) run python -m harvest extract

build-tokens:  ## regenerate the palette and re-verify WCAG contrast
	$(UV) run python design/gen.py

site:  ## build the static site and the Pagefind index
	cd site && $(NPM) ci && $(NPM) run build

gates:  ## everything CI enforces: tests, CKAN compat, tokens, a11y
	$(MAKE) test
	$(MAKE) validate
	$(MAKE) build-tokens
	cd site && $(NPM) run gates

clean:  ## remove derived artifacts (NEVER touches events/, which is the truth)
	rm -rf records/*.json .pytest_cache site/dist site/.astro
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
