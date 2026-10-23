#!/usr/bin/env python3
"""Build the search index of a PDF manual: one SQLite file per document revision.

Usage: build_index.py <pdf> [--out PATH] [--url URL] [--force]
Prints one JSON object: doc_id, pages, blocks, seconds, size, index, cached.

The unit is a block (heading, paragraph, list item or table row) stored with its page
and position. Page text is stored once (zlib); blocks point into it by offset. Running
headers and footers are removed, contents pages are stored but not searched, and IDs
(parameters, trip codes, captions, firmware notes) are anchored for exact lookup.
All patterns are generic: see reference/id-patterns.md before adding one.

Index schema: see SCHEMA below. Bump SCHEMA_VERSION on any change; search.py and
page.py rebuild on a mismatch.
"""
import argparse
import os
import re
import sqlite3
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage  # noqa: E402
from patterns import (CAPTION_RE, CONTENTS_TITLE_RE, DIAG_CHAPTER_RE, FIRMWARE_RE,  # noqa: E402
                      PARAM_DEF_RE, PARAM_REF_RE, SECTION_NUMBER_RE, TRIP_SENTENCE_RE,
                      TRIP_TOKEN_RE, VERSION_RE, caption_key, param_key)

try:
    import pypdfium2 as pdfium
except ImportError:  # reported as a handled error in main()
    pdfium = None

SCHEMA_VERSION = storage.SCHEMA_VERSION
SEP = "\n"
WORD = re.compile(r"[a-z]+")
HYPHENS = ("-", "‐", "­", "￾", "‑", "\x02")   # \x02: a hyphen glyph some fonts expose as a control character

# Running headers and footers: words that recur on more than BOILER_PAGE_SHARE of the
# pages inside the top and bottom BAND of the page height. A line in that band whose
# words are at least BOILER_WORD_SHARE recurring is dropped, as are bare page numbers.
BAND = 0.12
BOILER_PAGE_SHARE = 0.3
BOILER_WORD_SHARE = 0.8

SCHEMA = """
PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA page_size=4096;
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE outline(id INTEGER PRIMARY KEY, level INT, title TEXT, page INT, y REAL, end_page INT, parent INT);
CREATE TABLE pages(page INTEGER PRIMARY KEY, label TEXT, width REAL, height REAL, kind TEXT, z BLOB);
CREATE TABLE blocks(id INTEGER PRIMARY KEY, page INT, x INT, y INT, sec INT, kind TEXT, off INT, len INT);
CREATE VIRTUAL TABLE fts USING fts5(body, content='', tokenize='porter unicode61');
CREATE VIRTUAL TABLE ofts USING fts5(title, content='', tokenize='porter unicode61');
CREATE TABLE anchors(key TEXT, kind TEXT, block INT, is_def INT);
"""


def page_lines(page):
    """Lines [(left, top, right, text)] merged from text fragments; top in PDF units (bottom-up)."""
    tp = page.get_textpage()
    frags = []
    for i in range(tp.count_rects()):
        l, b, r, t = tp.get_rect(i)
        txt = tp.get_text_bounded(l, b, r, t)
        if txt.strip():
            frags.append([l, t, r, txt])
    frags.sort(key=lambda f: (-round(f[1]), f[0]))
    lines = []
    for f in frags:
        if lines:
            L = lines[-1]
            gap = f[0] - L[2]
            # same baseline and a small gap: one line. A bare section number joins its
            # heading text even across a wide gap.
            if abs(L[1] - f[1]) < 2.5 and (0 <= gap < 12 or (0 <= gap < 80 and SECTION_NUMBER_RE.match(L[3].strip()))):
                if gap >= 12:
                    L[3] = L[3].rstrip() + " "
                L[2] = f[2]
                L[3] = L[3] + f[3]  # fragments carry their own spaces
                continue
        lines.append(list(f))
    return [(l, t, r, re.sub(r"\s+", " ", x).strip()) for l, t, r, x in lines]


def join_lines(lines):
    """Join block lines; undo end-of-line hyphenation (word-<newline>continuation)."""
    out = ""
    for ln in lines:
        ln = ln.replace("￾", "­")
        if out.endswith(HYPHENS) and ln[:1].islower():
            out = out[:-1] + ln
        elif out:
            out += SEP + ln
        else:
            out = ln
    return out.replace("­", "")


def build(pdf_path, out_path, doc_id, url=""):
    """Index `pdf_path` into the SQLite file `out_path`; returns the build stats. The PDF and
    the database are closed explicitly, whatever happens: a handle left to garbage collection
    keeps the file open on Windows, where an open file cannot be replaced or removed."""
    doc = pdfium.PdfDocument(str(pdf_path))
    handles = {}
    try:
        return _build(doc, pdf_path, out_path, doc_id, url, handles)
    finally:
        if handles.get("db") is not None:
            handles["db"].close()
        doc.close()


def _build(doc, pdf_path, out_path, doc_id, url, handles):
    t0 = time.time()
    n = len(doc)
    pages = []
    for i in range(n):
        w, h = doc[i].get_size()
        pages.append((w, h, page_lines(doc[i])))

    # 1. boilerplate
    band_words = Counter()
    for w, h, lines in pages:
        seen = set()
        for l, t, r, x in lines:
            if t > h * (1 - BAND) or t < h * BAND:
                seen.update(WORD.findall(x.lower()))
        band_words.update(seen)
    common = {k for k, c in band_words.items() if c > n * BOILER_PAGE_SHARE}

    def is_boiler(ln, h):
        l, t, r, x = ln
        if not (t > h * (1 - BAND) or t < h * BAND):
            return False
        words = WORD.findall(x.lower())
        if not words:
            return True  # bare page numbers in the band
        return sum(wd in common for wd in words) / len(words) >= BOILER_WORD_SHARE

    # 2. outline from bookmarks: section ranges and heading positions
    outline = []
    for bm in doc.get_toc():
        dest = bm.get_dest()
        if not dest:
            continue
        pg0 = dest.get_index()
        if pg0 is None:
            continue
        view = dest.get_view()
        y = view[1][1] if view and len(view[1]) > 1 else None
        outline.append((bm.level, bm.get_title().strip(), pg0, y))
    contents_pages = set()
    for i, (lvl, title, pg0, y) in enumerate(outline):
        if CONTENTS_TITLE_RE.match(re.sub(r"^[\d.]+\s*", "", title)):
            nxt = next((o[2] for o in outline[i + 1:] if o[0] <= lvl), len(pages))
            contents_pages.update(range(pg0, max(pg0 + 1, nxt)))
    heading_y = {}
    for o in outline:
        heading_y.setdefault(o[2], []).append(o[3])

    db = handles["db"] = sqlite3.connect(str(out_path))
    db.executescript(SCHEMA)

    parents = {}
    for i, (lvl, title, pg, y) in enumerate(outline):
        end = n - 1
        for lvl2, _, pg2, _ in outline[i + 1:]:
            if lvl2 <= lvl:
                end = pg2
                break
        parents[lvl] = i
        parent = parents.get(lvl - 1)
        db.execute("INSERT INTO outline VALUES(?,?,?,?,?,?,?)", (i, lvl, title, pg + 1, y, end + 1, parent))
        db.execute("INSERT INTO ofts(rowid,title) VALUES(?,?)", (i, title))

    def chapter_title(i):
        while outline[i][0] > 0:
            i = max(j for j in range(i) if outline[j][0] < outline[i][0])
        return outline[i][1]

    def section_at(pg, y):
        best = None
        for i, (lvl, title, opg, oy) in enumerate(outline):
            if opg < pg or (opg == pg and (oy is None or oy >= y - 2)):
                best = i
            elif opg > pg:
                break
        return best

    nblocks = 0
    text_pages = 0
    for pg, (w, h, lines) in enumerate(pages):
        lines = [ln for ln in lines if not is_boiler(ln, h)]
        toc_like = (sum(1 for ln in lines if re.search(r"(\.{3,}|\s)\d{1,3}$", ln[3])) > max(8, len(lines) * 0.5)
                    or sum(1 for ln in lines if re.search(r"\.{5,}", ln[3])) >= 6
                    or pg in contents_pages)
        kind_page = "contents" if toc_like else "body"
        try:
            label = doc.get_page_label(pg) or str(pg + 1)   # no page-label tree: the physical number
        except Exception:
            label = str(pg + 1)

        mid = w / 2

        def col(ln):
            l, t, r, _ = ln
            if r <= mid + 15:
                return 0
            if l >= mid - 15:
                return 1
            return 0
        lines.sort(key=lambda ln: (col(ln), -ln[1], ln[0]))

        blocks, cur = [], None
        hy = heading_y.get(pg, [])
        for ln in lines:
            l, t, r, x = ln
            c = col(ln)
            is_head = any(oy is not None and abs(oy - t) < 4 for oy in hy)
            # records and headings keep their following lines across larger gaps, so a
            # heading never ends up as a block without its paragraph
            gap_limit = 30 if cur and (cur["kind"] == "record" or (cur["kind"] == "heading" and len(cur["lines"]) < 3)) else 16
            new = (cur is None or c != cur["col"] or cur["last_t"] - t > gap_limit
                   or PARAM_DEF_RE.match(x) or CAPTION_RE.match(x) or is_head)
            if new:
                if cur:
                    blocks.append(cur)
                kind = ("heading" if is_head else "record" if PARAM_DEF_RE.match(x)
                        else "caption" if CAPTION_RE.match(x) else "text")
                cur = {"x": l, "y": t, "col": c, "last_t": t, "lines": [x], "kind": kind}
            else:
                cur["lines"].append(x)
                cur["last_t"] = t
        if cur:
            blocks.append(cur)

        page_text, rows = "", []
        for b in blocks:
            text = join_lines(b["lines"])
            off = len(page_text)
            page_text += text + SEP
            rows.append((b, text, off, len(text)))
        db.execute("INSERT INTO pages VALUES(?,?,?,?,?,?)",
                   (pg + 1, label, w, h, kind_page, zlib.compress(page_text.encode(), 9)))
        if rows and kind_page == "body":
            text_pages += 1

        for b, text, off, ln in rows:
            kind = "contents" if kind_page == "contents" else b["kind"]
            sec = section_at(pg, b["y"])
            bid = db.execute("INSERT INTO blocks(page,x,y,sec,kind,off,len) VALUES(?,?,?,?,?,?,?)",
                             (pg + 1, round(b["x"]), round(h - b["y"]), sec, kind, off, ln)).lastrowid
            nblocks += 1
            if kind != "contents":
                db.execute("INSERT INTO fts(rowid, body) VALUES(?,?)", (bid, text))
            first = b["lines"][0]
            m = PARAM_DEF_RE.match(first)
            if m and kind == "record":
                db.execute("INSERT INTO anchors VALUES(?,?,?,1)", (param_key(m.group(1), m.group(2)), "param", bid))
            refs = set()
            for m in PARAM_REF_RE.finditer(text):
                g = [x for x in m.groups() if x]
                refs.add(param_key(g[0], g[1]))
            for k in refs:
                db.execute("INSERT INTO anchors VALUES(?,?,?,0)", (k, "param", bid))
            m = CAPTION_RE.match(first)
            if m:
                db.execute("INSERT INTO anchors VALUES(?,?,?,1)", (caption_key(m.group(1), m.group(2)), "caption", bid))
            keys = set()
            if kind == "text" and sec is not None and DIAG_CHAPTER_RE.search(chapter_title(sec)):
                keys |= {ln.strip().lower() for ln in b["lines"] if TRIP_TOKEN_RE.match(ln.strip())}
            keys |= {m.group(1).lower() for m in TRIP_SENTENCE_RE.finditer(text)}
            for k in keys:
                db.execute("INSERT INTO anchors VALUES(?,?,?,1)", (k, "trip", bid))
            if FIRMWARE_RE.search(text):
                db.execute("INSERT INTO anchors VALUES(?,?,?,0)", ("firmware", "firmware", bid))

    db.execute("CREATE INDEX anchors_key ON anchors(key)")
    meta = doc.get_metadata_dict()
    p1 = SEP.join(x for *_, x in pages[0][2]) if pages else ""
    issue = VERSION_RE.search(p1)
    for k, v in {"schema": SCHEMA_VERSION, "doc_id": doc_id, "pages": n, "blocks": nblocks,
                 "title_meta": meta.get("Title", ""), "title_page": p1[:300],
                 "issue": f"{issue.group(1)} {issue.group(2)}" if issue else "",
                 "source_path": str(Path(pdf_path).resolve()), "source_url": url or "",
                 "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "build_seconds": round(time.time() - t0, 2)}.items():
        db.execute("INSERT INTO meta VALUES(?,?)", (k, str(v)))
    db.commit()
    db.execute("INSERT INTO fts(fts) VALUES('optimize')")
    db.commit()
    db.execute("VACUUM")
    db.close()
    return {"doc_id": doc_id, "pages": n, "blocks": nblocks, "text_pages": text_pages,
            "sections": len(outline), "boilerplate_words": len(common), "seconds": round(time.time() - t0, 2)}


def read_meta(path):
    """The meta table of an index, or {} for a missing, damaged or foreign file."""
    db = None
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return dict(db.execute("SELECT k, v FROM meta").fetchall())
    except sqlite3.Error:
        return {}
    finally:
        if db is not None:
            db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", help="index path (default: the cache folder, by doc_id)")
    ap.add_argument("--url", default="", help="the vendor URL this PDF came from; page.py link uses it")
    ap.add_argument("--force", action="store_true", help="rebuild even if a current index exists")
    a = ap.parse_args()

    pdf = Path(a.pdf).expanduser()
    if not pdf.is_file():
        storage.fail("no_file", f"{pdf} does not exist",
                     "check the path, or fetch the manual with scripts/fetch.py get")
    if pdfium is None:
        storage.fail("no_pdf_lib", "pypdfium2 is not installed",
                     f"run: {storage.pip_hint()} — then retry; see reference/fallbacks.md")
    doc_id = storage.doc_id_for(pdf)
    out = Path(a.out).expanduser() if a.out else storage.index_path(doc_id)

    if out.exists() and not a.force:
        meta = read_meta(out)
        if meta.get("schema") == str(SCHEMA_VERSION) and meta.get("doc_id") == doc_id:
            storage.emit({"doc_id": doc_id, "index": str(out), "cached": True, "pages": int(meta["pages"]),
                          "blocks": int(meta.get("blocks", 0)), "seconds": 0, "size": out.stat().st_size})
            return
        # stale schema or a different document under this name: rebuild below

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    with storage.lock():   # one writer at a time; the build lands atomically
        if tmp.exists():
            tmp.unlink()
        try:
            stats = build(pdf, tmp, doc_id, url=a.url)
        except pdfium.PdfiumError as e:
            if tmp.exists():
                tmp.unlink()
            storage.fail("bad_pdf", f"{pdf.name}: {e}",
                         "the file is not a readable PDF (encrypted or damaged): download it again or ask the user for another copy")
        try:
            storage.replace_file(tmp, out)   # retried: on Windows an index another process is reading cannot be replaced
        except storage.InUse as e:
            if tmp.exists():
                tmp.unlink()
            storage.fail("index_in_use", str(e),
                         "the index is in use by another program; wait a moment and run the command again")
    result = dict(stats, index=str(out), cached=False, size=out.stat().st_size,
                  storage=storage.resolve()["source"] if not a.out else "explicit")
    if stats["text_pages"] == 0:
        result["warning"] = "no text layer (scanned PDF?)"
        result["fallback"] = "show the page image with scripts/page.py image; OCR only with the user's permission"
    storage.emit(result)


if __name__ == "__main__":
    main()
