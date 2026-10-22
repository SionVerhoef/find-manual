#!/usr/bin/env python3
"""Read, link and render one page of an indexed manual.

Usage: page.py context <doc_id> <page> <y> [--max-chars 1600]
       page.py link    <doc_id> <page> <y> [--pane-width PX] [--url URL] [--viewer chromium|firefox] [--margin PT]
       page.py image   <doc_id> <page> [--scale 1.5]

<page> is the physical page number (1-based) and <y> the hit's top position in PDF
points from the top of the page, both as search.py returned them. `context` is the
read package for the agent: the hit block and the blocks after it on the same page,
up to --max-chars. `link` returns the vendor's own URL (or the local file) with
#page=N&zoom=Z,0,T&navpanes=0; open it in a NEW tab or force a reload, because a
fragment-only change is ignored (see reference/hosts.md). `image` renders the page
to a PNG in the private cache, for drawings when the text is not enough.
"""
import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage  # noqa: E402

DEFAULT_PANE_WIDTH = 800   # CSS px: the Claude browser pane, when its innerWidth cannot be read
DEFAULT_MARGIN = 24        # PDF points kept visible above the hit


# ---- shared -----------------------------------------------------------------
def open_index(ref):
    db, meta, _ = storage.open_index(ref)
    return db, meta


def page_row(db, page):
    row = db.execute("SELECT label, width, height, z FROM pages WHERE page=?", (page,)).fetchone()
    if row is None:
        storage.fail("no_page", f"page {page} is not in the index",
                     "use the physical page number from the search hit (1-based)")
    label, w, h, z = row
    return label, w, h, zlib.decompress(z).decode()


def section_path(db, sec):
    parts = []
    while sec is not None:
        row = db.execute("SELECT title, parent FROM outline WHERE id=?", (sec,)).fetchone()
        if row is None:
            break
        parts.append(row[0])
        sec = row[1]
    return " > ".join(reversed(parts))


# ---- context ----------------------------------------------------------------
def context(db, page, y, max_chars=1600):
    label, w, h, text = page_row(db, page)
    rows = db.execute("SELECT id, y, sec, off, len FROM blocks WHERE page=? AND kind!='contents' ORDER BY id",
                      (page,)).fetchall()
    if not rows:
        return {"page": page, "label": label, "y": y, "section": "", "text": "", "chars": 0}
    start = min(range(len(rows)), key=lambda i: abs(rows[i][1] - y))  # the hit block
    out = []
    for _, _, _, off, ln in rows[start:]:
        out.append(text[off:off + ln])
        if sum(len(x) for x in out) > max_chars:
            break
    body = "\n".join(out)[:max_chars]
    return {"page": page, "label": label, "y": rows[start][1], "section": section_path(db, rows[start][2]),
            "text": body, "chars": len(body)}


# ---- link -------------------------------------------------------------------
def build_link(url, page, y, page_width, page_height, pane_width=DEFAULT_PANE_WIDTH,
               viewer="chromium", margin=DEFAULT_MARGIN):
    """Vendor URL + fragment that opens page N, fitted to the pane, scrolled to the hit.

    Z (percent) fits the page width to the pane. T is the hit's top minus the margin:
    counted from the top of the page in Chromium, from the bottom in the PDF spec and
    Firefox. navpanes=0 hides the thumbnail sidebar. A viewer that ignores the extra
    parts still honours #page=N.
    """
    zoom = max(10, round(pane_width / page_width * 100)) if page_width else 100
    top = max(0.0, float(y) - margin)
    if viewer == "firefox":
        top = max(0.0, float(page_height) - top)
    return f"{url}#page={int(page)}&zoom={zoom},0,{int(round(top))}&navpanes=0"


def source_url(meta, override=None):
    if override:
        return override
    if meta.get("source_url"):
        return meta["source_url"]
    src = meta.get("source_path", "")
    if src and Path(src).exists():
        return Path(src).resolve().as_uri()
    storage.fail("no_url", "the index has no source URL and the PDF working copy is gone",
                 "pass --url with the vendor's link, or fetch the manual again with scripts/fetch.py get")


# ---- image ------------------------------------------------------------------
def write_png(path, width, height, rows_rgb):
    """Minimal PNG writer (8-bit RGB), so page images need no library beyond pypdfium2."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + r for r in rows_rgb)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def image(meta, page, scale=1.5):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        storage.fail("no_pdf_lib", "pypdfium2 is not installed",
                     "run: python3 -m pip install --user pypdfium2 — then retry; see reference/fallbacks.md")
    src = meta.get("source_path", "")
    if not src or not Path(src).exists():
        storage.fail("no_pdf", "the PDF working copy is gone",
                     "fetch the manual again with scripts/fetch.py get, then retry")
    if Path(src).resolve().parent == storage.pdf_dir().resolve():
        storage.mark_used(src)   # a private working copy: keep it from cleanup a while longer
    pdf = pdfium.PdfDocument(src)
    if not 1 <= page <= len(pdf):
        storage.fail("no_page", f"page {page} is not in the PDF ({len(pdf)} pages)",
                     "use the physical page number from the search hit (1-based)")
    bitmap = pdf[page - 1].render(scale=scale, rev_byteorder=True)  # RGB(A/x) byte order
    n, stride, w, h = bitmap.n_channels, bitmap.stride, bitmap.width, bitmap.height
    buf = bytes(bitmap.buffer)
    rows = []
    for i in range(h):
        row = bytearray(buf[i * stride:i * stride + w * n])
        if n == 4:
            del row[3::4]  # drop alpha / padding
        rows.append(bytes(row))
    out = storage.image_dir() / f"{meta.get('doc_id', 'doc')}-p{page}.png"
    write_png(out, w, h, rows)
    return {"png": str(out), "page": page, "width": w, "height": h, "scale": scale}


# ---- cli --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("context")
    c.add_argument("doc"); c.add_argument("page", type=int); c.add_argument("y", type=float)
    c.add_argument("--max-chars", type=int, default=1600)
    l = sub.add_parser("link")
    l.add_argument("doc"); l.add_argument("page", type=int); l.add_argument("y", type=float)
    l.add_argument("--pane-width", type=float, default=DEFAULT_PANE_WIDTH, help="innerWidth of the pane in px")
    l.add_argument("--url", help="vendor URL to use instead of the one stored with the index")
    l.add_argument("--viewer", choices=["chromium", "firefox"], default="chromium")
    l.add_argument("--margin", type=float, default=DEFAULT_MARGIN)
    i = sub.add_parser("image")
    i.add_argument("doc"); i.add_argument("page", type=int)
    i.add_argument("--scale", type=float, default=1.5)
    a = ap.parse_args()

    db, meta = open_index(a.doc)
    doc_id = meta.get("doc_id", "")
    if a.cmd == "context":
        storage.emit(dict(doc_id=doc_id, **context(db, a.page, a.y, a.max_chars)))
    elif a.cmd == "link":
        label, w, h, _ = page_row(db, a.page)
        url = build_link(source_url(meta, a.url), a.page, a.y, w, h, a.pane_width, a.viewer, a.margin)
        storage.emit({"doc_id": doc_id, "url": url, "page": a.page, "label": label, "viewer": a.viewer,
                      "note": "open in a new tab or force a reload; a fragment-only change is ignored"})
    elif a.cmd == "image":
        label, *_ = page_row(db, a.page)
        storage.emit(dict(doc_id=doc_id, label=label, **image(meta, a.page, a.scale)))


if __name__ == "__main__":
    main()
