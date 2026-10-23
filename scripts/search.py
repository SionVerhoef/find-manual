#!/usr/bin/env python3
"""Search an index built by build_index.py.

Usage: search.py <doc_id|index.sqlite> "<query>" [--k 3] [--section ID]
       search.py <doc_id|index.sqlite> --outline [--depth 2]

The query is what the agent sends after rewriting the question: English terms in the
manual's own vocabulary, synonyms, and IDs in the manual's format ("Pr 02.011", "OI.AC",
"Figure 5-3"). Prints one JSON object: route (exact or bm25), hits (page, label, y,
section, score, match, snippet; best block per page), and doubt — true when the second
score is at least doubt_threshold × the first, so the agent shows a pick-list instead
of opening a page. --outline prints the compact table of contents (ids usable with
--section to search inside one section).
"""
import argparse
import re
import sqlite3
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage  # noqa: E402
from patterns import CAPTION_QUERY_RE, PARAM_QUERY_RE, caption_key, param_key  # noqa: E402

STOP = set("""a an the of to in on for and or is are be how what which when where why does do i my
with from by at as it this that can set setting value default use using show find about vs""".split())
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.]*")
SNIPPET_CHARS = 240


def snippet_for(text, key=None):
    """Start the snippet at the line that holds the matched ID, so an exact hit shows the entry itself."""
    if key:
        kl = key.lower()
        variants = [kl, kl.replace("-", ".")]
        m = re.fullmatch(r"(\d{2})\.(\d{3})", kl)   # 05.014 may be printed as 5.14 or 5.014
        if m:
            variants += [f"{int(m.group(1))}.{int(m.group(2)):02d}", f"{int(m.group(1))}.{m.group(2)}"]
        low = text.lower()
        for v in variants:
            i = low.find(v)
            if i >= 0:
                text = text[low.rfind("\n", 0, i) + 1:]
                break
    return re.sub(r"\s+", " ", text)[:SNIPPET_CHARS]


class Index:
    def __init__(self, ref):
        self.db, self.meta, path = storage.open_index(ref)
        self.path = str(path)
        self.outline = self.db.execute(
            "SELECT id, level, title, page, end_page, parent FROM outline ORDER BY id").fetchall()
        # an index built before labels fell back to the physical number may hold ""
        self.labels = {pg: (lb or str(pg)) for pg, lb in self.db.execute("SELECT page, label FROM pages")}
        self._pages = {}

    def close(self):
        """Close the index explicitly: left to garbage collection, the connection keeps the
        file open on Windows, where an open index cannot be rebuilt or cleaned up."""
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- reading --------------------------------------------------------------
    def page_text(self, page):
        if page not in self._pages:
            z, = self.db.execute("SELECT z FROM pages WHERE page=?", (page,)).fetchone()
            self._pages[page] = zlib.decompress(z).decode()
        return self._pages[page]

    def block(self, bid):
        page, x, y, sec, kind, off, ln = self.db.execute(
            "SELECT page,x,y,sec,kind,off,len FROM blocks WHERE id=?", (bid,)).fetchone()
        return {"id": bid, "page": page, "x": x, "y": y, "sec": sec, "kind": kind,
                "text": self.page_text(page)[off:off + ln]}

    def section_path(self, sec):
        parts = []
        while sec is not None:
            _, _, title, _, _, parent = self.outline[sec]
            parts.append(title)
            sec = parent
        return " > ".join(reversed(parts))

    def subtree(self, sec):
        """Outline ids under (and including) sec; the outline is stored depth-first."""
        lvl = self.outline[sec][1]
        ids = [sec]
        for o in self.outline[sec + 1:]:
            if o[1] <= lvl:
                break
            ids.append(o[0])
        return ids

    def compact_outline(self, depth=2):
        return [{"id": i, "level": lvl, "title": title, "page": pg, "label": self.labels.get(pg, str(pg)),
                 "end_page": end}
                for i, lvl, title, pg, end, _ in self.outline if lvl < depth]

    # ---- routes -------------------------------------------------------------
    def exact(self, q):
        """IDs in the query that the index has anchored: parameters, captions, trip codes."""
        keys = []
        for m in PARAM_QUERY_RE.finditer(q):
            keys.append(("param", param_key(m.group(1), m.group(2))))
        for m in CAPTION_QUERY_RE.finditer(q):
            keys.append(("caption", caption_key(m.group(1), m.group(2))))
        for tok in TOKEN.findall(q):
            t = tok.lower().rstrip(".")
            if t not in STOP and self.db.execute(
                    "SELECT 1 FROM anchors WHERE kind='trip' AND key=? LIMIT 1", (t,)).fetchone():
                keys.append(("trip", t))
        hits = []
        for kind, key in keys:
            rows = self.db.execute(
                "SELECT block, is_def FROM anchors WHERE kind=? AND key=? ORDER BY is_def DESC, block",
                (kind, key)).fetchall()
            for bid, is_def in rows:
                hits.append((10.0 if is_def else 1.0, bid, f"{kind}:{key}"))
        return hits

    def bm25(self, q):
        # acronyms written in capitals are never stop words ("IT network", "ON delay")
        raw = re.findall(r"[A-Za-z0-9]+", q)
        terms = [t.lower() for t in raw
                 if len(t) > 1 and (t.lower() not in STOP or (t.isupper() and len(t) <= 4))]
        terms += self.placeholder_terms(terms)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        # blocks inside a section whose title matches get a boost
        sec_boost = {}
        for rowid, rank in self.db.execute(
                "SELECT rowid, bm25(ofts) FROM ofts WHERE ofts MATCH ? ORDER BY rank LIMIT 8", (match,)):
            for s in self.subtree(rowid):
                sec_boost[s] = max(sec_boost.get(s, 0), -rank)
        top_sec = max(sec_boost.values()) if sec_boost else 1
        rows = self.db.execute(
            "SELECT f.rowid, -bm25(fts), b.sec, b.kind, b.page FROM fts f JOIN blocks b ON b.id=f.rowid "
            "WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 200", (match,)).fetchall()
        hits = []
        for bid, score, sec, kind, page in rows:
            # coverage: how many distinct query terms the block contains
            text = self.block(bid)["text"].lower()
            cover = sum(1 for t in set(terms) if t[:5] in text) / len(set(terms))
            s = score * (0.4 + cover)
            if sec in sec_boost:
                s *= 1 + sec_boost[sec] / top_sec
            if kind == "heading":
                s *= 1.3
            hits.append((s, bid, "text"))
        return hits

    def placeholder_terms(self, terms):
        """Model numbers in the query also match x-placeholders in the manual (AX8108 -> ax81x8)."""
        if not hasattr(self, "_xvocab"):
            try:
                self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab USING fts5vocab(main, fts, row)")
                self._xvocab = [t for (t,) in self.db.execute(
                    "SELECT term FROM temp.vocab WHERE term GLOB '*[0-9]*x*' OR term GLOB '*x*[0-9]*'")]
            except sqlite3.Error:
                self._xvocab = []
        extra = []
        for t in terms:
            if not re.search(r"\d", t) or len(t) < 4:
                continue
            for v in self._xvocab:
                if len(v) == len(t) and re.fullmatch(v.replace("x", "[0-9a-z]"), t) and v not in terms:
                    extra.append(v)
        return extra

    # ---- public -------------------------------------------------------------
    def search(self, q, k=3, section=None, threshold=0.85):
        hits = self.exact(q)
        route = "exact" if hits else "bm25"
        if not hits:
            hits = self.bm25(q)
        allowed = set(self.subtree(section)) if section is not None else None
        best = {}  # best block per page
        for s, bid, why in hits:
            b = self.block(bid)
            if allowed is not None and b["sec"] not in allowed:
                continue
            p = b["page"]
            if p not in best or s > best[p][0]:
                best[p] = (s, b, why)
        ranked = sorted(best.values(), key=lambda r: -r[0])[:k]
        top = ranked[0][0] if ranked else 1
        out = []
        for s, b, why in ranked:
            out.append({
                "page": b["page"],
                "label": self.labels.get(b["page"], str(b["page"])),
                "y": b["y"],
                "x": b["x"],
                "section": self.section_path(b["sec"]) if b["sec"] is not None else "",
                "section_id": b["sec"],
                "score": round(s / top, 2),
                "match": why,
                "snippet": snippet_for(b["text"], why.split(":", 1)[1] if ":" in why else None),
            })
        doubt = len(ranked) >= 2 and ranked[1][0] >= threshold * top
        return {"route": route, "hits": out, "doubt": doubt}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("doc", help="doc_id, or a path to an index file")
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--section", type=int, help="outline id: search only inside this section")
    ap.add_argument("--outline", action="store_true", help="print the compact table of contents instead")
    ap.add_argument("--depth", type=int, default=2, help="outline levels to print with --outline")
    a = ap.parse_args()

    with Index(a.doc) as idx:
        doc_id = idx.meta.get("doc_id", "")
        if a.outline:
            storage.emit({"doc_id": doc_id, "sections": len(idx.outline), "outline": idx.compact_outline(a.depth)})
            return
        if not a.query.strip():
            storage.fail("no_query", "the query is empty",
                         "rewrite the user's question into search terms in the manual's own vocabulary and retry")
        if a.section is not None and not 0 <= a.section < len(idx.outline):
            storage.fail("no_section", f"section {a.section} is not in the outline", "run with --outline to see the ids")
        threshold = float(storage.settings().get("doubt_threshold", 0.85))
        res = idx.search(a.query, a.k, a.section, threshold)
        res.update(doc_id=doc_id, query=a.query, doubt_threshold=threshold)
        storage.emit(res)


if __name__ == "__main__":
    main()
