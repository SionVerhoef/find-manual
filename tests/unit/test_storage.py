"""storage.py: where data lives, and stable document ids."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import storage  # noqa: E402


class Location(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get(storage.ENV_HOME)
        os.environ[storage.ENV_HOME] = self.tmp.name
        storage.reset()

    def tearDown(self):
        if self.old is None:
            os.environ.pop(storage.ENV_HOME, None)
        else:
            os.environ[storage.ENV_HOME] = self.old
        storage.reset()
        self.tmp.cleanup()

    def test_env_var_wins_and_cache_sits_below_it(self):
        r = storage.resolve()
        self.assertEqual(r["source"], "env")
        self.assertEqual(r["data"], Path(self.tmp.name))
        self.assertEqual(r["cache"], Path(self.tmp.name) / "cache")
        self.assertEqual(storage.index_path("abc"), Path(self.tmp.name) / "cache" / "index" / "abc.sqlite")
        self.assertTrue(storage.index_dir().is_dir())

    def test_settings_merge_defaults_with_config(self):
        self.assertEqual(storage.settings()["doubt_threshold"], 0.85)
        (Path(self.tmp.name) / "config.json").write_text('{"doubt_threshold": 0.9, "pdf_hours": 1}')
        s = storage.settings()
        self.assertEqual(s["doubt_threshold"], 0.9)
        self.assertEqual(s["answer_language"], "en")  # untouched default

    def test_find_index_accepts_an_id_or_a_path(self):
        p = storage.index_path("abc")
        self.assertIsNone(storage.find_index("abc"))
        p.write_bytes(b"")
        self.assertEqual(storage.find_index("abc"), p)
        self.assertEqual(storage.find_index(str(p)), p)


class DocId(unittest.TestCase):
    def test_same_bytes_same_id_wherever_the_file_is(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.pdf", Path(d) / "sub"
            b.mkdir()
            b = b / "other-name.pdf"
            a.write_bytes(b"%PDF-1.4 test")
            b.write_bytes(b"%PDF-1.4 test")
            self.assertEqual(storage.doc_id_for(a), storage.doc_id_for(b))
            self.assertEqual(len(storage.doc_id_for(a)), 16)
            b.write_bytes(b"%PDF-1.4 other")
            self.assertNotEqual(storage.doc_id_for(a), storage.doc_id_for(b))


if __name__ == "__main__":
    unittest.main()
