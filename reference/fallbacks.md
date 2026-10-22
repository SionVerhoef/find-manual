# Fallbacks

The skill never fails silently. Take the next route, tell the user in one line only when
it affects them, and keep going. Scripts return a handled error as
`{"error": "...", "message": "...", "fallback": "..."}` with exit code 0; the `fallback`
names the next route below.

| Problem | Fallback |
| --- | --- |
| Python missing or older than 3.9 | Use the agent's own web fetch and PDF reading on a few pages, with no index. Say so once. |
| PDF library missing | `pip install --user pypdfium2` → `pypdf` → `pdftotext` if already installed. `env_check.py` reports which one is in use. |
| Home folder not writable (sandbox) | Project `.find-manual/` (gitignored) → temp folder for this session only; tell the user once and how to allow the home folder. |
| No network | Local manuals folder, cached index, or a PDF the user attaches. |
| Download blocked (403, bot check) | The agent's web fetch tool → ask the user to download the PDF and give the path. |
| No browser tool | OS `open` / `xdg-open` / `start` → page image inline (`page.py image`) → plain link. |
| PDF has no bookmarks | Headings detected from font size and the printed contents pages (done at build time). |
| Scanned PDF (no text layer) | Page image to the agent; OCR only with the user's permission. |
| Index corrupt or schema outdated | Rebuild (`build_index.py`), automatically. |
| Two tools at once | Lock file with retry, atomic writes. |
| Cloud session | Works normally, starts empty each session. |
| Manual newer than the device's firmware | Look for an older issue in the vendor archive; if none, warn that some functions may not exist on that device. |
| Firmware unknown | Ask. If the manual says where to read the version, pass that on. |
| Weak search results | Rewrite the query again (up to 2 more variants) → pick a section from the compact outline (`search.py --outline`) and search inside it. |
