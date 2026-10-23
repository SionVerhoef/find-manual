"""page.py link: the fragment that opens the vendor's PDF at the hit."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from page import DEFAULT_PANE_WIDTH, build_link, write_png  # noqa: E402

URL = "https://vendor.example/manual.pdf"
A4 = (595.0, 842.0)


class Link(unittest.TestCase):
    def test_chromium_counts_top_down_and_fits_the_pane(self):
        url = build_link(URL, 158, 300, *A4, pane_width=800)
        self.assertEqual(url, f"{URL}#page=158&zoom=134,0,276&navpanes=0")  # 800/595 = 134 %, 300 - 24

    def test_firefox_counts_from_the_bottom(self):
        url = build_link(URL, 158, 300, *A4, pane_width=800, viewer="firefox")
        self.assertEqual(url, f"{URL}#page=158&zoom=134,0,566&navpanes=0")  # 842 - 276

    def test_hit_near_the_top_clamps_to_zero(self):
        self.assertIn("&zoom=134,0,0&", build_link(URL, 1, 10, *A4, pane_width=800))

    def test_default_pane_width_and_margin(self):
        self.assertEqual(build_link(URL, 2, 100, *A4),
                         build_link(URL, 2, 100, *A4, pane_width=DEFAULT_PANE_WIDTH, margin=24))

    def test_hidden_pane_width_zero_uses_the_default(self):
        # a hidden browser pane reports innerWidth 0
        self.assertEqual(build_link(URL, 2, 100, *A4, pane_width=0), build_link(URL, 2, 100, *A4))

    def test_local_file_urls_keep_the_fragment(self):
        url = build_link("file:///Users/me/manuals/c200.pdf", 7, 50, *A4)
        self.assertTrue(url.startswith("file:///Users/me/manuals/c200.pdf#page=7&zoom="))

    def test_landscape_page_zooms_out(self):
        self.assertIn("&zoom=95,", build_link(URL, 1, 100, 842.0, 595.0, pane_width=800))


class Png(unittest.TestCase):
    def test_writes_a_valid_png_header(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.png"
            write_png(p, 2, 2, [b"\xff\x00\x00\x00\xff\x00", b"\x00\x00\xff\xff\xff\xff"])
            data = p.read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertIn(b"IHDR", data)
            self.assertTrue(data.endswith(b"IEND\xaeB`\x82"))


if __name__ == "__main__":
    unittest.main()
