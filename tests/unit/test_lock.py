"""storage.py: the write lock (two tools at once), atomic writes and pins."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import storage  # noqa: E402


class Isolated(unittest.TestCase):
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


class Lock(Isolated):
    def test_lock_is_exclusive_and_released_on_exit(self):
        with storage.lock() as path:
            self.assertTrue(path.exists())
            self.assertEqual(json.loads(path.read_text())["pid"], os.getpid())
            with self.assertRaises(storage.Locked):
                with storage.lock(timeout=0.2, busy="raise"):
                    pass
        self.assertFalse(path.exists())
        with storage.lock(timeout=0.2, busy="raise"):   # free again
            pass

    def test_busy_fail_mode_is_a_handled_error(self):
        out = io.StringIO()
        with storage.lock(), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            with storage.lock(timeout=0.2):
                pass
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(json.loads(out.getvalue())["error"], "locked")

    def test_lock_of_a_dead_process_is_broken(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        path = storage.data_dir() / ".lock"
        path.write_text(json.dumps({"pid": dead.pid, "time": time.time()}))
        t0 = time.monotonic()
        with storage.lock(timeout=5, busy="raise"):
            pass
        self.assertLess(time.monotonic() - t0, 2)

    def test_process_liveness_is_told_on_this_os(self):
        """POSIX asks with signal 0; Windows opens the process and reads its exit code."""
        self.assertTrue(storage._pid_alive(os.getpid()))
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.assertIs(storage._pid_alive(dead.pid), False)
        self.assertIsNone(storage._pid_alive(None))
        self.assertIsNone(storage._pid_alive(0))

    def test_old_lock_is_broken_even_if_the_pid_lives(self):
        path = storage.data_dir() / ".lock"
        path.write_text(json.dumps({"pid": os.getpid(), "time": 0}))
        old = time.time() - storage.LOCK_STALE - 5
        os.utime(path, (old, old))
        with storage.lock(timeout=1, busy="raise"):
            pass

    def test_a_second_process_waits_for_the_first(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, sys.argv[1]); import storage\n"
             "with storage.lock():\n    print('held', flush=True); time.sleep(0.8)",
             str(SCRIPTS)],
            stdout=subprocess.PIPE, text=True, env=dict(os.environ))
        self.assertEqual(holder.stdout.readline().strip(), "held")
        t0 = time.monotonic()
        with storage.lock(timeout=10, busy="raise"):
            waited = time.monotonic() - t0
        holder.wait()
        holder.stdout.close()
        self.assertGreater(waited, 0.3)


class AtomicWrite(Isolated):
    def test_replaces_the_file_whole_and_leaves_no_temp_file(self):
        p = storage.data_dir() / "catalog.json"
        storage.write_json(p, {"a": 1})
        storage.write_json(p, {"a": 2})
        self.assertEqual(json.loads(p.read_text()), {"a": 2})
        self.assertEqual([x.name for x in storage.data_dir().iterdir() if x.name.endswith(".tmp")], [])

    def test_a_failed_write_keeps_the_old_file(self):
        p = storage.data_dir() / "catalog.json"
        storage.write_json(p, {"a": 1})

        class Boom(Exception):
            pass

        class Bad:
            def __bytes__(self):
                raise Boom()
        with self.assertRaises((Boom, TypeError)):
            storage.write_atomic(p, Bad())
        self.assertEqual(json.loads(p.read_text()), {"a": 1})
        self.assertEqual([x.name for x in storage.data_dir().iterdir() if x.name.endswith(".tmp")], [])

    def test_creates_parent_folders(self):
        p = storage.data_dir() / "vendors" / "deep" / "x.json"
        storage.write_json(p, [1])
        self.assertEqual(json.loads(p.read_text()), [1])


class Pins(Isolated):
    def test_pin_unpin_round_trip(self):
        self.assertEqual(storage.pins(), {})
        entry = storage.pin("0123456789abcdef", "keep for the site visit")
        self.assertEqual(entry["note"], "keep for the site visit")
        self.assertIn("0123456789abcdef", storage.pins())
        self.assertTrue(storage.unpin("0123456789abcdef"))
        self.assertFalse(storage.unpin("0123456789abcdef"))
        self.assertEqual(storage.pins(), {})

    def test_damaged_pins_file_reads_as_empty(self):
        storage.pins_path().write_text("[not a dict]")
        self.assertEqual(storage.pins(), {})


class MarkUsed(Isolated):
    def test_bumps_the_mtime(self):
        p = storage.data_dir() / "f"
        p.write_text("x")
        os.utime(p, (1_000_000, 1_000_000))
        storage.mark_used(p)
        self.assertGreater(p.stat().st_mtime, time.time() - 60)
        storage.mark_used(storage.data_dir() / "missing")   # no error


if __name__ == "__main__":
    unittest.main()
