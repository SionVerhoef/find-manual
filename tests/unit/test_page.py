"""page.py context and the page labels, on a tiny generated PDF: the hit block is chosen by
y then x, the sibling columns of a table row come along, --find starts a long block at the
ID, the blocks above are added when the page ends soon after the hit, and a PDF without a
page-label tree gets the physical number as label. Nothing here is copied from a manual."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import storage  # noqa: E402

try:
    import pypdfium2  # noqa: F401
    HAVE_PDFIUM = True
except ImportError:
    HAVE_PDFIUM = False


def make_pdf(pages):
    """A minimal PDF; each page is a list of (x, y_from_bottom, [lines]) text blocks."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    body = [b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    for i, blocks in enumerate(pages):
        parts = []
        for x, y, lines in blocks:
            parts.append(f"BT /F1 12 Tf {x} {y} Td 16 TL " + " T* ".join(f"({esc(ln)}) Tj" for ln in lines) + " ET")
        content = " ".join(parts)
        body.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> "
                     f"/Contents {5 + 2 * i} 0 R >>").encode())
        body.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, b in enumerate(body, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + b + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(body) + 1}\n".encode() + b"0000000000 65535 f \n"
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# Page 1: a two-column table row (left and right blocks at the same height), then a long
# diagnostics-style block whose entry of interest sits in the middle. Page 2: a long
# block at the top and a short caption-like block near the bottom.
PAGE1 = [(72, 700, ["Cross section outer conductor", "1.5 square", "2.5 square"]),
         (330, 700, ["Minimum cross section grounding", "2 x 1.5 square", "2 x 2.5 square"]),
         (72, 560, ["Trip table", "ZZ.1 first entry text", "its recommended action",
                    "QQ.AC second entry text", "its recommended action", "YY.3 third entry text"])]
PAGE2 = [(72, 700, [f"Table body line {i} with words" for i in range(1, 9)]),   # below the header band
         (72, 500, ["Tab. 4-2: Connection points"])]


@unittest.skipUnless(HAVE_PDFIUM, "pypdfium2 not installed")
class Context(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build_index
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old = os.environ.get(storage.ENV_HOME)
        os.environ[storage.ENV_HOME] = cls.tmp.name
        storage.reset()
        pdf = Path(cls.tmp.name) / "tiny.pdf"
        pdf.write_bytes(make_pdf([PAGE1, PAGE2]))
        cls.index = Path(cls.tmp.name) / "tiny.sqlite"
        build_index.build(pdf, cls.index, "f" * 16)
        cls.db = sqlite3.connect(str(cls.index))

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        if cls.old is None:
            os.environ.pop(storage.ENV_HOME, None)
        else:
            os.environ[storage.ENV_HOME] = cls.old
        storage.reset()
        cls.tmp.cleanup()

    def block(self, page, needle):
        """(x, y) of the block on `page` whose text holds `needle`."""
        import zlib
        z, = self.db.execute("SELECT z FROM pages WHERE page=?", (page,)).fetchone()
        text = zlib.decompress(z).decode()
        for x, y, off, ln in self.db.execute("SELECT x, y, off, len FROM blocks WHERE page=?", (page,)):
            if needle in text[off:off + ln]:
                return x, y
        self.fail(f"no block with {needle!r} on page {page}")

    def test_no_label_tree_means_the_physical_number(self):
        from page import page_row
        from search import Index
        self.assertEqual(page_row(self.db, 2)[0], "2")
        self.assertEqual(Index(str(self.index)).labels[1], "1")

    def test_hit_by_y_then_x_and_the_sibling_column_comes_along(self):
        from page import context
        lx, ly = self.block(1, "outer conductor")
        rx, ry = self.block(1, "Minimum cross section")
        self.assertEqual(ly, ry)
        c = context(self.db, 1, ry, x=rx)
        self.assertEqual(c["y"], ry)
        self.assertTrue(c["text"].startswith("Cross section outer conductor"))   # left column first
        self.assertIn("Minimum cross section grounding", c["text"])
        self.assertLess(c["text"].index("Minimum cross section grounding"), c["text"].index("Trip table"))

    def test_find_starts_a_long_block_at_the_id(self):
        from page import context
        x, y = self.block(1, "QQ.AC")
        c = context(self.db, 1, y, x=x, find="qq.ac")
        self.assertTrue(c["text"].startswith("QQ.AC second entry text"), c["text"][:60])
        self.assertIn("YY.3 third entry text", c["text"])
        self.assertNotIn("ZZ.1", c["text"])

    def test_blocks_above_are_added_when_the_page_ends_after_the_hit(self):
        from page import context
        x, y = self.block(2, "Tab. 4-2")
        c = context(self.db, 2, y, x=x)
        self.assertEqual(c["y"], y)
        self.assertIn("Table body line 1", c["text"])
        self.assertTrue(c["text"].rstrip().endswith("Tab. 4-2: Connection points"))

    def test_search_hits_carry_x(self):
        from search import Index
        r = Index(str(self.index)).search("minimum cross section grounding")
        self.assertTrue(r["hits"])
        rx, ry = self.block(1, "Minimum cross section")
        self.assertEqual((r["hits"][0]["x"], r["hits"][0]["y"]), (rx, ry))
        self.assertEqual(r["hits"][0]["label"], "1")


if __name__ == "__main__":
    unittest.main()
