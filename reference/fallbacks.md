# Fallbacks

The skill never fails silently. Take the next route, tell the user in one line only when
it affects them, and keep going. Scripts return a handled error as
`{"error": "...", "message": "...", "fallback": "..."}` with exit code 0; the `fallback`
names the next route below.

| Problem | Fallback |
| --- | --- |
| Python missing or older than 3.9 | Use the agent's own web fetch and PDF reading on a few pages, with no index. Say so once. |
| PDF library missing | `pip install --user pypdfium2` → `pypdf` → `pdftotext` if already installed. `env_check.py` reports which one is in use. |
| Home folder not writable (sandbox) | Project `.find-manual/` (gitignored) → temp folder for this session only. `env_check.py` and `setup.py` return a `notice` saying which and how to allow the home folder: tell the user once. |
| No network | `fetch.py` returns `no_network`: use the local manuals folder (`settings.py add manuals_dirs`), the cached index (a cached URL is served without its daily check), or a PDF the user attaches (`fetch.py get --path`). |
| Download blocked (403, bot check, a web page instead of the PDF) | `fetch.py` returns `download_blocked` or `not_a_pdf`: fetch the link with the agent's own web fetch tool and save the PDF → ask the user to download it and give the path → `fetch.py get --path <file>`. |
| No browser tool | OS `open` / `xdg-open` / `cmd /c start "" "<url>"` (Windows; `env_check.py` gives the command) → page image inline (`page.py image`) → plain link. |
| PDF has no bookmarks | Headings detected from font size and the printed contents pages (done at build time). |
| Scanned PDF (no text layer) | Page image to the agent; OCR only with the user's permission. |
| Index corrupt or schema outdated | Rebuild (`build_index.py`), automatically. |
| Cache above `max_cache_mb` or files unused past retention | `cleanup.py` removes PDF copies first, then indexes, oldest first; pinned manuals stay. A missing working copy is fetched again. |
| Two tools at once | Writers (index build, settings, cleanup, downloads) take `<data>/.lock`, retry for 30 s and break a stale lock; readers never wait. All writes land atomically. A `locked` error means retry in a few seconds. |
| Cloud session | Works normally, starts empty each session. |
| Manual newer than the device's firmware | `fetch.py` labels it Unsure with a `warning`: look for an older issue in the vendor archive (`resolve --url` on each); if there is none, use it and warn that some functions may not exist on that device. |
| Firmware unknown | `resolve` returns `ask_firmware: true`: ask. Pass on `firmware_hint` (the parameter or page where the manual says the version is shown), then run `resolve` again with `--firmware V`. |
| Nothing found on this machine | `resolve` returns `next: web_search` with queries: search with the agent's own web tool, the vendor's own domain first, and run `resolve --model M --url U` per PDF link so the script verifies and labels it. |
| The link or file is not that model's manual | `model_match: none`, label Unsure, and the lookup goes on to the other routes: tell the user and follow `next`. |
| Weak search results | Rewrite the query again (up to 2 more variants) → pick a section from the compact outline (`search.py --outline`) and search inside it. |
| The search finds only the HTML edition of the manual | The PDF usually sits on the vendor's download host: fetch that page for its PDF link, or try the vendor's download centre; `resolve --url` verifies whatever is tried. |
| Two copies of the same manual (another host, mirror or file) | `resolve` keeps one (best label, the `--vendor` host, newest date, most recently used) and lists the others under `duplicates`: one manual, no question to the user. |
| The hit is a long table block, or a caption below its table | `page.py context … --find <ID>` starts at the ID; read the neighbouring page when the table continues there. |
| The browser pane answers with a download instead of a page | Do not retry the URL there. Next opener, each once: Claude in Chrome (new tab) → system browser (`open` / `xdg-open` / `cmd /c start "" "<url>"`) → `page.py image` inline with the plain link. Order and tests in `reference/hosts.md`. |
| A local file opens without its fragment | State the printed page label and section in the answer. |
