# Changelog

## Unreleased

- M4 `scripts/fetch.py`: `resolve` finds the manual for a model (user link or file, catalog,
  vendor document maps, local manuals folders) and verifies each candidate from the
  document itself (title page and front matter: model with placeholders and family names,
  doc type, issue and date, firmware range against `--firmware`, language) with the
  Verified / Likely / Unsure label, evidence, `ask_firmware` and the manual's own hint on
  where to read the device's software version; nothing found → `next: web_search` with
  queries, the agent searches and calls `resolve --url` per link. `get` downloads a private
  working copy with a plain User-Agent (a 403, a bot check or a web page instead of the
  PDF, and no network are handled errors with their fallback), hashes it, builds or reuses
  the index with the clean URL, and writes the catalog; tracking parameters are stripped
  from every URL; a cached URL gets one HEAD check a day and a changed file a new doc_id.
  `reference/verification.md` holds the rules; 45 unit tests against a tiny generated PDF
  and a fake HTTP server, no network.
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
