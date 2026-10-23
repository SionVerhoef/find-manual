# Opening the page per host

Choose by the tools available in the session, not by product name. Always open the
vendor's own URL (or the local `file://` path); never a copy served by the skill.

| Environment | Opens in | How |
| --- | --- | --- |
| Claude Code desktop (first target) | Browser pane | Browser tool: open the link in a **new tab** |
| Claude app with a browser | Built-in browser or Claude in Chrome | Browser tool: new tab |
| Codex app | In-app browser (public pages only) | Browser tool: new tab |
| CLIs (Claude Code, Codex, Gemini, Copilot), Cursor | System browser | `open` (macOS) / `xdg-open` (Linux) / `start` (Windows) with the link |
| No browser available | Inline | `page.py image` → show the PNG, plus the plain link |

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
  that follow the PDF spec.
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

## Claude Code desktop, tested 23 Oct 2026 in a session without a window

- `innerWidth` reads 0 while the pane is hidden; pass `--pane-width` only when it is above 0
  (`page.py link` falls back to the default for 0). A screenshot of the pane measured 560 px.
- With the pane hidden, every PDF URL was refused ("responded with a file download instead
  of a page", a save dialog) although the servers answered `application/pdf` without a
  `Content-Disposition` header; the 21 Oct test with a visible pane displayed the same
  files. Do not retry a refused URL: show `page.py image` with the plain link and `open`
  the link in the system browser.
- A local file opens in a tab of its own as a static snapshot: `#page=` is ignored and the
  page tools cannot act on that tab. State the page number in the answer.
- The pane shows a page as a background page while hidden; `show_pane` only works when the
  session is open in a window.
