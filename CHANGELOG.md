# Changelog

## Unreleased

- M2 core scripts from the prototype: `scripts/build_index.py` (doc_id, schema 3, caption
  keys normalised), `scripts/search.py` (doubt flag, page labels, `--outline`, `--section`),
  `scripts/page.py` (context, positioned link, page image), `scripts/storage.py` and
  `scripts/patterns.py`; `tests/` with `evaluate.py`, `run_baselines.py`, the 3 test sets
  and unit tests; CI runs the unit tests.
- M1 repository skeleton: layout, README (install, privacy, copyright), `defaults.json`,
  SKILL.md with the lookup workflow, `install.py` (copy to `~/.agents/skills`, link from
  `~/.claude/skills`), `reference/` (hosts, fallbacks, ID patterns).
- Repository scaffold: draft SKILL.md, contributor rules, CI frontmatter check.
