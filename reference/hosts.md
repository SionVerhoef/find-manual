# Opening the page per host

Choose by the tools available in the session, not by product name. Always open the
vendor's own URL (or the local `file://` path); never a copy served by the skill.

| Environment | Opens in | How |
| --- | --- | --- |
| Claude Code desktop (first target) | Browser pane, then Claude in Chrome, then the system browser, then the page image | In that order; see *Opener order* below |
| Claude app with a browser | Built-in browser or Claude in Chrome | Same order |
| Codex app | In-app browser (public pages only) | Browser tool: new tab |
| CLIs (Claude Code, Codex, Gemini, Copilot), Cursor | System browser | `open` (macOS) / `xdg-open` (Linux) / `start` (Windows) with the link |
| No browser available | Inline | `page.py image` → show the PNG, plus the plain link |

## Opener order (Claude Code desktop and the Claude app)

Try each opener once, in this order, and stop at the first that displays the page. Say
which one was used in one line only when it is not the pane, because the user then has
to look elsewhere.

1. **Built-in browser pane** (`mcp__Claude_Browser__*`, or the `mcp__remote-devices__…`
   variant): open the link in a **new tab** (`preview_start` with the URL). Done when the
   PDF displays. When the pane answers "responded with a file download instead of a page"
   (a save dialog) or the tool is missing, do not retry that URL there: next opener.
2. **Claude in Chrome** (`mcp__claude-in-chrome__*`): `tabs_create_mcp`, then `navigate`
   with the link. Chrome's viewer honours page, zoom, position and `navpanes` (tested 23 Oct
   2026: the C200 guide opened at page 158, 134 %, with the hit in view; a vendor redirect
   kept the fragment). Leave the tab open for the user. Extension not connected, or the
   site declined: next opener.
3. **System browser**: `open` (macOS) / `xdg-open` (Linux) / `start` (Windows) with the
   link. Displays the PDF at the fragment, outside the app.
4. **Page image inline**: `page.py image <doc_id> <page>`, shown with the plain link.

`--pane-width`: pass the pane's `innerWidth` when it reads above 0. For Chrome or the
system browser keep the 800 px default: fitting a full window (~1500 px) gives ~250 %.

## The link

`page.py link <doc_id> <page> <y> [--pane-width PX]` returns

```
<vendor URL>#page=N&zoom=Z,0,T&navpanes=0
```

- `N`: physical page number (1-based), not the printed label.
- `Z`: zoom percent fitted so the page fills the pane: `pane width / page width in points × 100`.
  Pass `--pane-width` with the pane's `innerWidth` when it can be read; otherwise 800 px
  is assumed.
- `T`: the hit's top position minus a small margin, in PDF points counted from the top of
  the page (Chromium). `page.py link --viewer firefox` converts to bottom-up for viewers
  that follow the PDF spec. `T` is the top of the hit *block*: inside a long table block
  the ID itself can sit lower in the view.
- `navpanes=0`: hides the thumbnail sidebar, which otherwise takes half of a narrow pane.
- A viewer that ignores the extra parts still honours `#page=N`.
- The URL is the one stored with the index (`build_index.py --url`, set by `fetch.py`);
  `--url` overrides it. A local PDF without a URL opens as `file://`.

## Rules (tested in the Claude browser pane, Chromium viewer)

- Fragments only take effect on a **full page load**. Changing just the `#…` part of an
  open tab (navigate, or opening the same URL again) does nothing. Open a new tab, or
  force a reload after setting the URL.
- Each load downloads the PDF again (10–20 s for a 10 MB manual). Open the page **once
  per lookup**, after the answer is known.
- `#search=word` is ignored in Chromium, so there is no highlighting in v1; precise
  positioning replaces it. Firefox honours `#search` as a bonus.
- `view=FitH` is not used: its zoom depends on the pane size during loading.
- Local files open directly as `file://…#page=N…`. If the fragment is lost, state the
  page number in the answer.
- Documentation websites: open `url#anchor` (or `#:~:text=…` for a text fragment) the
  same way, in a new tab.

## Claude Code desktop, tested 23 Oct 2026 (app build 3.1623)

- `innerWidth` reads 0 while the pane is hidden and the real width once it is shown (458 px
  here); pass `--pane-width` only when it is above 0 (`page.py link` falls back to the
  default for 0).
- This build's pane answers every PDF URL, hidden or shown, vendor or a 13 KB test file, with
  "responded with a file download instead of a page" and a save dialog, although the
  servers send `application/pdf` without a `Content-Disposition` header; the 21 Oct test on
  an earlier build displayed the same files. Try the link once; when it is refused, do not
  retry it there: go on with the *Opener order* above (Claude in Chrome displayed the same
  link at the hit the same day).
- A local file opens in a tab of its own as a static snapshot: `#page=` is ignored and the
  page tools cannot act on that tab. State the page number in the answer.
- The pane shows a page as a background page while hidden; `show_pane` only works when the
  session is open in a window.
