---
name: find-manual
description: "Finds the right manual and the right page for a question about a product, then reads only that page and opens the vendor's own PDF at that spot. Use when the user asks what a parameter, setting, fault, trip or error code, wiring, procedure, rating or figure means in a manual, user guide, operating instructions, datasheet or documentation site, or asks where something is covered, which firmware a function needs, or to look it up in the manual. Works with PDFs online or on disk and with HTML documentation sites. Never answers from memory when the manual is available."
---

# find-manual

Find the manual, find the page, read only that page, open the vendor's PDF at that spot.
Answer only from what was read, with a citation and a confidence label.

> **In development (M1–M2 built).** `env_check.py`, `setup.py`, `fetch.py` and `settings.py`
> do not exist yet. Until they do: skip "First use" and step 2, take the PDF path or URL from
> the user, run `scripts/build_index.py <pdf> [--url U]`, and continue from step 3 with the
> `doc_id` it prints.

Scripts live in `scripts/` next to this file and need Python 3.9+ with `pypdfium2`.
Every script prints one JSON object. A handled error comes back as
`{"error", "message", "fallback"}` with exit code 0: follow the `fallback`, do not stop.
Details that do not fit here are in `reference/`.

## First use

If `scripts/env_check.py` reports `first_run: true`, run `scripts/setup.py` once.
It checks the environment, reports only problems, shows the defaults and where data
lives (three lines), and offers once to take supplier doc-portal links or a local
manuals folder. It asks no settings questions. Then continue with the user's question.
Settings change by asking ("keep PDFs for a week" → `scripts/settings.py set ...`).

## Workflow for every lookup

1. **Parse the question.** Product/model, topic, IDs (parameters, fault/trip codes,
   figure or table numbers) and firmware if given.

2. **Find the manual.** `scripts/fetch.py resolve --model M [--doc-type T] [--url U | --path P]`
   returns candidates with a confidence label. Lookup order: link or file from the user →
   catalog of manuals used before → vendor document maps → local manuals folder → web
   search, vendor's own domain first. Then `scripts/fetch.py get --url U | --path P`
   downloads a private working copy and builds the index.
   - **Verified**: model and firmware range stated and matching → go on.
   - **Likely**: model matches, no firmware range stated → confirm with the user first
     (title, issue/version, link).
   - **Unsure**: close candidates or a firmware mismatch → ask first.
   - Firmware unknown → ask. If the manual says where to read the version (for example
     a parameter that shows the software version), tell the user.

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
