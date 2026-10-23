# Finding and verifying the manual

What `scripts/fetch.py` does and returns. SKILL.md holds the short form.

## Lookup order

`resolve --model M` tries, in this order, and stops at the first **Verified** candidate:

1. The link or file the user gave (`--url`, `--path`).
2. The catalog of manuals used before (`<data>/catalog.json`).
3. Vendor document maps (`<data>/vendors/*.json`, one row per document: title, models,
   doc_type, language, issue, date, firmware_range, url). The best rows are downloaded
   and verified like any other candidate. `vendor_map.py` builds them (M6); until then the
   folder is usually empty.
4. The local manuals folders (`manuals_dirs` setting): by file name first, then by the
   title page of each PDF.
5. Hand-over to the agent's web search: `"next": "web_search"` with ready-made `queries`
   (model + document type + "pdf"; a `site:<vendor>` query first when `--vendor DOMAIN`
   is given). Search with the vendor's own domain first and run
   `resolve --model M --url U` for each PDF link, best first. The script has no search API
   of its own: it verifies what the agent finds.

Otherwise every candidate comes back, best first (label, then how well the model matched,
then the document type, then the newest issue or date).

**Copies.** Two candidates with the same title, issue and page count are one manual found
twice (another host, a mirror, a local copy). `resolve` keeps one — the best label, then the
`--vendor` host, then the newest date, then the most recently used — and lists the others
under its `duplicates` (source, url or path, date, doc_id, label). Copies never count as
close candidates. A different issue or page count is a different document and stays apart.

**Only the HTML edition found.** Many vendors publish the manual as web pages and keep the
PDF on a separate download host that search engines rank low or not at all. Fetch the HTML
edition's page for its PDF link, or try the vendor's download centre; `resolve --url`
verifies whatever is tried from the document itself, so a wrong guess costs nothing.

## Verification

Evidence comes from the document itself, never from a listing or a file name alone: the
title page (the first page with text) and the front matter (the first six pages, which is
where the revision history and firmware statements live).

| Check | Read from | Rule |
| --- | --- | --- |
| Model / product | Title page; the PDF metadata title only as a weaker fallback (it is often stale) | Every token of the model must appear. A token with digits also matches an `x` placeholder of the same length (`AX8108` ~ `AX81x8`), a family name with trailing zeros (`AX8108` ~ `AX8000`), or by its base number when only a variant suffix is missing (`HCS01.1E-W0054` ~ `HCS01`): those count as **family** matches and cap the label at Likely. Only part of the name → **partial** → Unsure. |
| Document type | Title page | Detected from generic phrases (table below). Must fit `--doc-type` when given; `user-guide` and `operating-instructions` fit each other; an undetected type fits anything. |
| Issue / version | Title page, else the next pages | `Issue`, `Issue Number`, `Edition`, `Version`, `Revision`, `Rev.` followed by a number. The title page wins over the PDF metadata. |
| Date | Title page (first date), else the newest date in the front matter (the last row of a revision history) | `YYYY-MM-DD`, `YYYY-MM`, `Month YYYY`, `MM/YYYY`, `DD.MM.YYYY`, years 1990–2099. |
| Firmware range | Front matter | `firmware/software (version) V1.10 and later`, `from firmware V1.10`, `firmware 2.0 to 2.5`, `applies to firmware 1.4` (a single version counts as "from"). A parameter's value range such as `00.00.00 to 99.99.99` is ignored. |
| Language | Front matter | Function-word counts: `en`, `de`, `fr`, `es`, `it`, `nl` or `unknown`. Must be one of `manual_languages` (`--lang` overrides for one call); `unknown` passes. |

Versions are compared numerically part by part (`01.07.01` = `1.7.1`, `2.0` = `2.0.0`).

## Labels

| Label | When | Agent |
| --- | --- | --- |
| **Verified** | Model matches and a stated firmware range covers `--firmware` | Answers |
| **Likely** | Model matches and no firmware range is stated, or the device firmware is unknown; also every family or metadata-only match | Confirms with the user first (title, issue, link) |
| **Unsure** | Partial model match, document type or language does not fit, firmware outside the range, or close candidates (two of the same label and match) | Asks first |

- `ask_firmware: true` whenever `--firmware` was not given and there is a candidate.
  `firmware_hint` names the parameter whose name is the software/firmware version (or the
  sentence that says where it is displayed), with its page, when the manual has one.
- Manual newer than the device (`--firmware` below the stated range): label Unsure, plus a
  `warning` to look for an older issue; if there is none, some functions may not exist on
  that device.
- `evidence` lists the page and the short wording each check rested on (the title line
  with the model, the issue line, the firmware statement). Citations can name it.

## Document types (`--doc-type`)

| Value | Title-page phrases | Questions that usually need it |
| --- | --- | --- |
| `user-guide` | user guide, user manual, owner's manual, operating manual, programming guide, reference guide; a bare "manual" or "guide" | parameters, settings, faults and trips, procedures, keypad, menus |
| `parameter-reference` | parameter reference / guide / description / list | a parameter's full definition when the user guide has only a summary |
| `installation` | installation / mounting / assembly guide or instructions, hardware manual, commissioning guide | wiring, terminals, mounting, clearances, cable sizes |
| `operating-instructions` | operating instructions, operation manual, instruction manual, instructions for use | the same as user-guide; many vendors use this name |
| `datasheet` | data sheet, technical data, product data | ratings, dimensions, weights, derating |
| `release-notes` | release notes, firmware release / history, version history, changelog | which firmware adds or changes a function |
| `other` | anything else | accepted when nothing else fits |

Without `--doc-type` every type is accepted and `user-guide` ranks first.

## Catalog

`<data>/catalog.json` is `{"manuals": {doc_id: {...}}}`, links and short evidence only,
never page text: `title`, `url` (clean) or `path`, `version`, `date`, `models` (the model
names it was found for), `doc_type`, `language`, `firmware_range`, `label`, `evidence`,
`pages`, `etag`, `last_modified`, `size`, `checked_at`, `added`, `last_used` (ISO
`YYYY-MM-DDTHH:MM:SS`). `resolve` reads it first and bumps `last_used`; `resolve --url`,
the vendor-map step and `get` write it. `cleanup.py` drops entries unused for
`retention.catalog_days`; pinned manuals stay.

## Working copy and freshness

- `get --url` strips tracking parameters (`utm_*`, `gclid`, `fbclid`, `_gl`, `mc_cid`,
  `mc_eid`, `ref`, ...) and the fragment, downloads with a plain User-Agent to a temp file
  under the write lock, hashes it, keeps it as `<cache>/pdf/<doc_id>.pdf`, builds or reuses
  the index with the clean URL stored in it (what `page.py link` opens), and writes the
  catalog. `get --path` uses the user's file in place and never copies it.
- A cached URL is checked at most once a day with a HEAD request (ETag, Last-Modified, else
  size). A changed file is downloaded again and gets a new `doc_id` and index; the old
  ones are left to `cleanup.py`. `--force` downloads again regardless. Offline, the cached
  copy is used without the check.
- A working copy that `cleanup.py` removed while the index remained is reported with a
  `note`; search and reading still work, and `get --force` fetches the file again when a
  page image is needed.
