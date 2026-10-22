# find-manual

An agent skill that finds the right manual and the right page for your question, reads
only that page, and opens the vendor's own PDF at that spot next to the chat.

Ask "what does trip OI.AC mean on my Commander C200?" and the agent finds the user guide
for that model and firmware, finds the page, answers from that page with a citation, and
opens the PDF at the hit. A 200-page manual costs ~300–650 tokens per answer instead of
the whole document in context.

**Status: in development.** The index and search core is validated (3 vendors, 42 test
questions, 90 % top-1, 100 % top-3), and storage, settings, first-run setup and cleanup
are in; finding and downloading the manual (`fetch.py`) is being built. See
[CHANGELOG.md](CHANGELOG.md).

## What it does

1. Finds the manual for the model and firmware (link or file from you, manuals used
   before, the vendor's document portal, a local folder, or a web search on the vendor's
   own site), and verifies it from the document's own title page and revision history.
2. Builds a small private search index of the PDF (SQLite, ~0.5–1 MB per 200 pages, a
   few seconds).
3. Searches for the page, reads the hit and its neighbours, and answers only from that
   text with the manual, issue, printed page and section cited and a confidence label
   (Verified / Likely / Unsure). Any doubt about manual, firmware or page → it asks you.
4. Opens the vendor's PDF in a browser pane at that page and position.

Vendor neutral: it ships no manuals, no indexes and no vendor-specific code. Sources are
text PDFs (online or local) and HTML documentation sites. English answers and English
manuals by default.

## Install

Needs Python 3.9+ and [`pypdfium2`](https://github.com/pypdfium2-team/pypdfium2)
(`pip install pypdfium2`).

```bash
git clone https://github.com/SionVerhoef/find-manual.git
cd find-manual
python3 install.py
```

This copies the skill to `~/.agents/skills/find-manual` (the open Agent Skills folder,
read by Codex, Gemini CLI and GitHub Copilot) and links it from `~/.claude/skills/`
(Claude Code, Copilot, Cursor). On Windows the link is a second copy. Start a new agent
session afterwards. `python3 install.py --uninstall` removes both.

First target is Claude Code desktop, where the page opens in the browser pane. Other
tools open the system browser or show the page image inline; see
[reference/hosts.md](reference/hosts.md).

## Privacy and storage

Everything stays on your machine. The skill keeps, per OS data and cache folders
(`~/Library/Application Support/find-manual` and `~/Library/Caches/find-manual` on macOS,
`~/.local/share/find-manual` and `~/.cache/find-manual` on Linux, `%LOCALAPPDATA%\find-manual`
on Windows):

| What | Kept until unused for |
| --- | --- |
| Settings, catalog of manuals used, vendor document maps (links only) | settings: always; others: 90 days |
| Search index per manual revision | 30 days |
| Private working copy of a PDF | 48 hours |

`FIND_MANUAL_HOME` or the `storage_dir` setting moves all of it; in a sandbox with a
read-only home folder it falls back to the project's `.find-manual/` (gitignored), then
to a temp folder for the session, and says so once. Only the skill's own `cleanup.py`
deletes anything, only its own files, and pinned manuals (`cleanup.py pin <doc_id>`)
stay. Above 500 MB of cache, PDF copies go first, then indexes, oldest first. Settings
are changed by asking the agent ("keep PDFs for a week"), which runs
`scripts/settings.py set retention.pdf_hours 1w`; `settings.py show` lists them.
Nothing is sent anywhere except the requests needed to find and download a manual
(`env_check.py` probes connectivity with a TCP connect only).

## Copyright

Manuals belong to their vendors. The skill never hosts, re-serves or redistributes a
manual: it always opens the vendor's original URL, and the working copy and index are
private to your machine. This repository contains no manuals, no indexes and no manual
text, and its test sets only name the manuals they need.

## Development

See [CLAUDE.md](CLAUDE.md) for the rules on editing this repository (no manuals in the
repo, vendor-neutral fixes only). `python3 tests/run_baselines.py` builds the index of
every test manual you keep in `manuals/` (gitignored; each `tests/testset_*.json` says
where to get its PDF) and runs the test sets; `tests/evaluate.py` runs one set. Every
change to indexing or ranking must keep the baselines (top-1 ≥ 38/42, top-3 42/42).
`python3 -m unittest discover -s tests/unit` runs the unit tests (storage, lock, atomic
writes, settings, cleanup, env check, link building, patterns), which need no PDFs; set
`FIND_MANUAL_HOME` to a scratch folder to keep them off your real data, as CI does.

## License

MIT — see [LICENSE](LICENSE). Only permissive dependencies: `pypdfium2` (Apache-2.0 /
BSD-3), SQLite FTS5 (public domain), optional `pypdf` (BSD-3) as fallback.
