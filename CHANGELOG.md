# Changelog

## Unreleased

- M3 storage, settings and housekeeping: `scripts/env_check.py` (python, PDF library,
  FTS5, where data lives, network, browser hint, `first_run`), `scripts/setup.py` (first
  run: problems only, three lines, one optional question, writes `config.json`),
  `scripts/settings.py` (show / get / set / unset / add / remove, JSON-typed values,
  durations like `1w`), `scripts/cleanup.py` (retention per kind, size cap with PDFs
  first, pins, catalog and vendor-map expiry, only its own files). `storage.py` gained the
  write lock with retry and stale-lock recovery, atomic writes, pins, `config.json` kept
  in the base folder when `storage_dir` moves the data, and the project-folder / temp
  fallback with a one-time notice. 55 new unit tests; CI unchanged.
- M2 core scripts from the prototype: `scripts/build_index.py` (doc_id, schema 3, caption
  keys normalised), `scripts/search.py` (doubt flag, page labels, `--outline`, `--section`),
  `scripts/page.py` (context, positioned link, page image), `scripts/storage.py` and
  `scripts/patterns.py`; `tests/` with `evaluate.py`, `run_baselines.py`, the 3 test sets
  and unit tests; CI runs the unit tests.
- M1 repository skeleton: layout, README (install, privacy, copyright), `defaults.json`,
  SKILL.md with the lookup workflow, `install.py` (copy to `~/.agents/skills`, link from
  `~/.claude/skills`), `reference/` (hosts, fallbacks, ID patterns).
- Repository scaffold: draft SKILL.md, contributor rules, CI frontmatter check.
