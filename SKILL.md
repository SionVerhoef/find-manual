---
name: find-manual
description: "Finds the right manual and the right page for a question about a product, then reads only that page and opens the vendor's own PDF at that spot. Use when the user asks what a parameter, setting, fault, trip or error code, wiring, procedure, rating or figure means in a manual, user guide, operating instructions, datasheet or documentation site, or asks where something is covered, which firmware a function needs, or to look it up in the manual. Works with PDFs online or on disk and with HTML documentation sites. Never answers from memory when the manual is available."
---

# find-manual

Find the manual, find the page, read only that page, open the vendor's PDF at that spot.
Answer only from what was read, with a citation and a confidence label.

Scripts live in `scripts/` next to this file and need Python 3.9+ with `pypdfium2`.
Every script prints one JSON object. A handled error comes back as
`{"error", "message", "fallback"}` with exit code 0: follow the `fallback`, do not stop.
Details that do not fit here are in `reference/`.

## First use

Run `scripts/env_check.py` at the start of a session. If it reports `first_run: true`,
run `scripts/setup.py` once: it checks the environment, reports only `problems` (each
with its fallback), gives three `lines` (defaults, data folder, cache folder) to show
the user, and on the first run one `optional` question to put to the user once: supplier
doc-portal links or a local manuals folder, or skip. Save their answer with
`scripts/settings.py add suppliers <url>` / `add manuals_dirs <path>`. It asks no
settings questions; in a headless run the defaults apply. Then continue with the
user's question. If either script returns a `notice` (data kept in the project or a
temp folder because the home folder is not writable), tell the user once.

## Settings and housekeeping

Settings change by asking, never by a questionnaire: "keep PDFs for a week" →
`scripts/settings.py set retention.pdf_hours 1w`; "stop asking so often" →
`scripts/settings.py set doubt_threshold 0.95`; `scripts/settings.py show` lists them.
`scripts/cleanup.py` removes only the skill's own stale files (PDF copies after 48 h,
indexes after 30 d, catalog entries after 90 d, more when the cache passes 500 MB)
and runs at the start of a lookup. "Keep this manual" → `scripts/cleanup.py pin <doc_id>`.

## Workflow for every lookup

1. **Parse the question.** Product/model, topic, IDs (parameters, fault/trip codes,
   figure or table numbers) and firmware if given.

2. **Find the manual.** `scripts/fetch.py resolve --model M [--doc-type T] [--firmware V]
   [--url U | --path P] [--vendor DOMAIN]` looks at the link or file from the user, the
   catalog of manuals used before, the vendor document maps and the local manuals folder,
   verifies every candidate from its own title page and front matter, and returns
   `candidates` best first, each with a `label`. It stops at the first **Verified** one.
   `"next": "web_search"` means nothing was found on this machine: search with your own
   web tool using the `queries` given, the vendor's own domain first, and run
   `resolve --model M --url U` for each PDF link, best first; the script downloads it,
   verifies it and labels it. Then `scripts/fetch.py get --url U | --path P` returns the
   `doc_id`, the private working copy and the index (cached from the second time on).
   The rules, the `--doc-type` words and the catalog are in `reference/verification.md`.
   - **Verified**: model and firmware range stated and matching → go on.
   - **Likely**: model matches, no firmware range stated → confirm with the user first
     (title, issue/version, link).
   - **Unsure**: close candidates, a partial match or a firmware mismatch → ask first.
   - `ask_firmware: true` → ask for the device's firmware. Pass `firmware_hint` on when
     given (where the manual says to read it), then run `resolve` again with `--firmware V`.
   - A `warning` that the manual is newer than the device's firmware → look for an older
     issue; if there is none, say that some functions may not exist on that device.

3. **Rewrite the question, every time.** Turn it into English search terms in the
   manual's own vocabulary: the manual's words, synonyms, and IDs in the manual's format
   (`Pr 02.011`, `OI.AC`, `Figure 5-3`). Raw questions get 60 % top-1; rewritten ones 90 %.
   If the first result is weak, try up to 2 more variants.

4. **Search.** `scripts/search.py <doc_id> "<terms>"` returns the top 3 with page, printed
   label, position, section path, score and snippet, plus `doubt`. If `doubt` is true
   (second score at least 0.85 × the first), show a pick-list (page, section, one line)
   and ask the user which one. Any doubt about manual, firmware or page → ask.
   Still weak after the variants? `scripts/search.py <doc_id> --outline` gives the compact
   table of contents; pick a section and search inside it with `--section ID`.

5. **Read.** `scripts/page.py context <doc_id> <page> <y>` gives the hit block and its
   neighbours (~300–650 tokens). Read the next page only if a table or procedure
   continues. Render `scripts/page.py image <doc_id> <page>` only for drawings when the
   text is not enough.

6. **Open the page once.** `scripts/page.py link <doc_id> <page> <y> [--pane-width PX]`
   returns the vendor's URL with `#page=N&zoom=Z,0,T&navpanes=0`. Open it in a **new
   tab**, or force a reload: changing only the fragment of an open tab does nothing.
   One load per lookup; each load downloads the PDF again. Which tool opens it is in
   `reference/hosts.md`; a local file opens as `file://`.

7. **Answer only from the text that was read.** Cite the manual, issue/version, printed
   page label and section, and give the confidence label. Warn on a firmware mismatch.
   Paraphrase; use only short quotes.

## Never

- answer from memory when the manual is available
- reload the PDF repeatedly in one lookup
- host, re-serve, copy into the repo or redistribute manual content: the working copy
  and index stay private on this machine, and the page always opens at the vendor's URL

## Fallbacks

The skill never fails silently: take the next route, say so in one line only when it
affects the user, and keep going. The routes are in `reference/fallbacks.md`.
