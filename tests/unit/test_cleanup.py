"""cleanup.py: retention, the size cap, pins, and that only the skill's own files go."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import cleanup  # noqa: E402
import storage  # noqa: E402

H, D = 3600, 86400
A, B, C, E = "a" * 16, "b" * 16, "c" * 16, "e" * 16


class Cleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get(storage.ENV_HOME)
        os.environ[storage.ENV_HOME] = self.tmp.name
        storage.reset()
        self.now = time.time()

    def tearDown(self):
        if self.old is None:
            os.environ.pop(storage.ENV_HOME, None)
        else:
            os.environ[storage.ENV_HOME] = self.old
        storage.reset()
        self.tmp.cleanup()

    def make(self, folder, name, age_s, size=1024):
        p = folder / name
        p.write_bytes(b"x" * size)
        os.utime(p, (self.now - age_s, self.now - age_s))
        return p

    def config(self, **kw):
        storage.write_json(storage.config_path(), kw)

    def removed(self, result):
        return sorted((r["kind"], r["name"]) for r in result["removed"])

    # ---- retention ------------------------------------------------------------
    def test_retention_per_kind(self):
        old_pdf = self.make(storage.pdf_dir(), f"{A}.pdf", 49 * H)
        new_pdf = self.make(storage.pdf_dir(), f"{B}.pdf", 47 * H)
        old_img = self.make(storage.image_dir(), f"{A}-p12.png", 49 * H)
        old_idx = self.make(storage.index_dir(), f"{A}.sqlite", 31 * D)
        new_idx = self.make(storage.index_dir(), f"{B}.sqlite", 29 * D)
        r = cleanup.run(now=self.now)
        self.assertEqual(self.removed(r), [("img", old_img.name), ("index", old_idx.name), ("pdf", old_pdf.name)])
        self.assertFalse(old_pdf.exists() or old_img.exists() or old_idx.exists())
        self.assertTrue(new_pdf.exists() and new_idx.exists())
        self.assertEqual(r["remaining"]["pdf"]["count"], 1)
        self.assertEqual(r["remaining"]["index"]["count"], 1)
        self.assertIn("unused for 2 d", r["removed"][0]["why"] + r["removed"][1]["why"] + r["removed"][2]["why"])

    def test_retention_follows_the_settings(self):
        self.config(retention={"pdf_hours": 168})
        pdf = self.make(storage.pdf_dir(), f"{A}.pdf", 100 * H)
        cleanup.run(now=self.now)
        self.assertTrue(pdf.exists())
        self.config(retention={"pdf_hours": 1})
        cleanup.run(now=self.now)
        self.assertFalse(pdf.exists())

    def test_a_use_resets_the_clock(self):
        idx = self.make(storage.index_dir(), f"{A}.sqlite", 40 * D)
        storage.mark_used(idx)
        cleanup.run(now=self.now)
        self.assertTrue(idx.exists())

    def test_dry_run_deletes_nothing(self):
        pdf = self.make(storage.pdf_dir(), f"{A}.pdf", 100 * H)
        r = cleanup.run(dry_run=True, now=self.now)
        self.assertEqual(self.removed(r), [("pdf", pdf.name)])
        self.assertTrue(pdf.exists())
        self.assertTrue(r["dry_run"])

    # ---- size cap -------------------------------------------------------------
    def test_size_cap_removes_pdfs_first_then_indexes_oldest_first(self):
        self.config(max_cache_mb=0.0025)                      # 2.5 KB; four 1 KB files
        pdf_old = self.make(storage.pdf_dir(), f"{A}.pdf", 10 * H)
        pdf_new = self.make(storage.pdf_dir(), f"{B}.pdf", 1 * H)
        idx_old = self.make(storage.index_dir(), f"{C}.sqlite", 10 * D)
        idx_new = self.make(storage.index_dir(), f"{E}.sqlite", 1 * D)
        r = cleanup.run(now=self.now)
        self.assertEqual([x["name"] for x in r["removed"]], [pdf_old.name, pdf_new.name])
        self.assertTrue(idx_old.exists() and idx_new.exists())
        self.assertLessEqual(r["remaining"]["cache_mb"], 0.0025)
        self.assertIn("above", r["removed"][0]["why"])

        self.config(max_cache_mb=0.0015)                      # now one index has to go too: the oldest
        r = cleanup.run(now=self.now)
        self.assertEqual([x["name"] for x in r["removed"]], [idx_old.name])
        self.assertTrue(idx_new.exists())

    def test_no_cap_when_zero(self):
        self.config(max_cache_mb=0)
        pdf = self.make(storage.pdf_dir(), f"{A}.pdf", 1 * H, size=50_000)
        cleanup.run(now=self.now)
        self.assertTrue(pdf.exists())

    # ---- pins -----------------------------------------------------------------
    def test_pinned_manuals_survive_retention_and_the_cap(self):
        storage.pin(A, "site visit")
        pdf = self.make(storage.pdf_dir(), f"{A}.pdf", 1000 * H)
        idx = self.make(storage.index_dir(), f"{A}.sqlite", 1000 * D)
        img = self.make(storage.image_dir(), f"{A}-p3.png", 1000 * H)
        other = self.make(storage.pdf_dir(), f"{B}.pdf", 1 * H)
        self.config(max_cache_mb=0.001)
        r = cleanup.run(now=self.now)
        self.assertTrue(pdf.exists() and idx.exists() and img.exists())
        self.assertFalse(other.exists())
        self.assertEqual(r["pinned"], [A])
        storage.unpin(A)
        cleanup.run(now=self.now)
        self.assertFalse(pdf.exists() or idx.exists())

    # ---- catalog and vendor maps -----------------------------------------------
    def test_catalog_entries_and_vendor_maps_expire(self):
        iso = lambda age: time.strftime(cleanup.ISO, time.localtime(self.now - age))  # noqa: E731
        storage.write_json(storage.catalog_path(), {"manuals": {
            A: {"title": "old", "last_used": iso(91 * D)},
            B: {"title": "recent", "last_used": iso(10 * D)},
            C: {"title": "pinned old", "last_used": iso(400 * D)},
            E: {"title": "no date"}}})
        storage.pin(C)
        old_map = self.make(storage.vendors_dir(), "old.example.json", 91 * D)
        new_map = self.make(storage.vendors_dir(), "new.example.json", 1 * D)
        r = cleanup.run(now=self.now)
        kept = json.loads(storage.catalog_path().read_text())["manuals"]
        self.assertEqual(sorted(kept), sorted([B, C, E]))
        self.assertFalse(old_map.exists())
        self.assertTrue(new_map.exists())
        self.assertEqual(r["remaining"]["catalog_entries"], 3)
        self.assertEqual(r["remaining"]["vendor_maps"], 1)
        self.assertIn(("catalog", "old"), self.removed(r))

    # ---- only its own files ----------------------------------------------------
    def test_foreign_files_are_never_touched(self):
        strangers = [self.make(storage.index_dir(), "notes.txt", 1000 * D),
                     self.make(storage.index_dir(), "manual.sqlite", 1000 * D),        # not a doc_id name
                     self.make(storage.pdf_dir(), "readme.pdf", 1000 * D),
                     self.make(storage.image_dir(), "photo.png", 1000 * D),
                     self.make(storage.data_dir(), "config.json", 1000 * D),
                     self.make(storage.vendors_dir(), "notes.md", 1000 * D)]
        sub = storage.index_dir() / "sub"
        sub.mkdir()
        self.make(sub, f"{A}.sqlite", 1000 * D)
        link = storage.pdf_dir() / f"{B}.pdf"
        target = Path(self.tmp.name) / "precious.pdf"
        target.write_bytes(b"keep me")
        link.symlink_to(target)
        os.utime(target, (self.now - 1000 * D, self.now - 1000 * D))
        r = cleanup.run(now=self.now)
        self.assertEqual(r["removed"], [])
        for p in strangers:
            self.assertTrue(p.exists(), p)
        self.assertTrue(target.exists() and link.is_symlink())

    def test_leftover_temp_files_go_after_an_hour(self):
        fresh = self.make(storage.index_dir(), f".{A}.sqlite.123.tmp", 10 * 60)
        stale = self.make(storage.index_dir(), f".{B}.sqlite.456.tmp", 2 * H)
        r = cleanup.run(now=self.now)
        self.assertEqual(self.removed(r), [("tmp", stale.name)])
        self.assertTrue(fresh.exists())

    def test_skips_when_another_tool_holds_the_lock(self):
        pdf = self.make(storage.pdf_dir(), f"{A}.pdf", 1000 * H)
        with storage.lock():
            r = cleanup.run(now=self.now, lock_timeout=0.2)
        self.assertIn("skipped", r)
        self.assertTrue(pdf.exists())

    def test_cli_pin_and_run(self):
        import subprocess
        script = Path(__file__).resolve().parents[2] / "scripts" / "cleanup.py"
        out = json.loads(subprocess.check_output([sys.executable, str(script), "pin", A, "--note", "n"], text=True))
        self.assertEqual(out["pins"], [A])
        out = json.loads(subprocess.check_output([sys.executable, str(script)], text=True))
        self.assertEqual(out["pinned"], [A])
        out = json.loads(subprocess.check_output([sys.executable, str(script), "status"], text=True))
        self.assertTrue(out["dry_run"])


if __name__ == "__main__":
    unittest.main()
