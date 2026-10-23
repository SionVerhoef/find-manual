"""build_index.py main: the index lands atomically under the lock, and an index that another
program holds open (Windows) is a handled error, not a crash. No PDF library is needed here:
the build itself is faked, so this runs on every CI runner."""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_index  # noqa: E402
import storage  # noqa: E402

BUSY = PermissionError(13, "The process cannot access the file because it is being used by another process")


def fake_build(pdf, tmp, doc_id, url=""):
    Path(tmp).write_bytes(b"index")
    return {"doc_id": doc_id, "pages": 1, "blocks": 1, "text_pages": 1, "sections": 0,
            "boilerplate_words": 0, "seconds": 0.0}


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get(storage.ENV_HOME)
        os.environ[storage.ENV_HOME] = self.tmp.name
        storage.reset()
        self.pdf = Path(self.tmp.name) / "manual.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 not really")

    def tearDown(self):
        if self.old is None:
            os.environ.pop(storage.ENV_HOME, None)
        else:
            os.environ[storage.ENV_HOME] = self.old
        storage.reset()
        self.tmp.cleanup()

    def run_main(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["build_index.py", str(self.pdf), *args]), \
                mock.patch.object(build_index, "pdfium", types.SimpleNamespace(PdfiumError=RuntimeError)), \
                mock.patch.object(build_index, "build", fake_build), \
                mock.patch.object(storage.time, "sleep"), \
                contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            build_index.main()
        return json.loads(out.getvalue())

    def temp_files(self):
        return [p.name for p in storage.index_dir().iterdir() if p.name.endswith(".tmp")]

    def test_the_index_lands_whole_and_no_temp_file_stays(self):
        r = self.run_main()
        self.assertFalse(r["cached"])
        self.assertEqual(Path(r["index"]).read_bytes(), b"index")
        self.assertEqual(Path(r["index"]), storage.index_path(storage.doc_id_for(self.pdf)))
        self.assertEqual(self.temp_files(), [])
        self.assertFalse((storage.data_dir() / ".lock").exists())

    def test_a_reader_that_finishes_in_time_does_not_fail_the_build(self):
        real, calls = os.replace, []

        def busy_twice(a, b):
            calls.append(1)
            if len(calls) <= 2:
                raise BUSY
            real(a, b)
        with mock.patch.object(os, "replace", busy_twice):
            r = self.run_main()
        self.assertFalse(r["cached"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(Path(r["index"]).read_bytes(), b"index")

    def test_an_index_held_open_elsewhere_is_a_handled_error(self):
        with mock.patch.object(os, "replace", side_effect=BUSY) as rep:
            r = self.run_main("--force")
        self.assertEqual(r["error"], "index_in_use")
        self.assertIn("run the command again", r["fallback"])
        self.assertEqual(rep.call_count, storage.REPLACE_TRIES)
        self.assertEqual(self.temp_files(), [])                       # the half-built index is gone
        self.assertFalse((storage.data_dir() / ".lock").exists())    # and the lock released


class ReadMeta(unittest.TestCase):
    """read_meta closes its connection on every path: left open, the file stays busy on Windows."""

    def connections(self):
        real, opened = sqlite3.connect, []

        def connect(*a, **k):
            db = real(*a, **k)
            opened.append(db)
            return db
        return mock.patch.object(sqlite3, "connect", connect), opened

    def closed(self, db):
        with self.assertRaises(sqlite3.ProgrammingError):
            db.execute("SELECT 1")

    def test_a_foreign_file_reads_as_empty_and_is_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.sqlite"
            p.write_bytes(b"not an index")
            patch, opened = self.connections()
            with patch:
                self.assertEqual(build_index.read_meta(p), {})
            self.assertEqual(len(opened), 1)
            self.closed(opened[0])
            self.assertEqual(build_index.read_meta(Path(d) / "missing.sqlite"), {})

    def test_a_real_index_is_read_and_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.sqlite"
            db = sqlite3.connect(str(p))
            db.executescript("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT); INSERT INTO meta VALUES('schema', '3');")
            db.commit()
            db.close()
            patch, opened = self.connections()
            with patch:
                self.assertEqual(build_index.read_meta(p), {"schema": "3"})
            self.closed(opened[0])


if __name__ == "__main__":
    unittest.main()
