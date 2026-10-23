"""storage.py: where data lives, the sandbox fallbacks, settings merge, stable document ids,
JSON output on any console, and the retried replace for files another program holds open."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.assertEqual(r["config"], Path(self.tmp.name) / "config.json")
        self.assertEqual(r["notice"], "")
        self.assertEqual(storage.index_path("abc"), Path(self.tmp.name) / "cache" / "index" / "abc.sqlite")
        self.assertTrue(storage.index_dir().is_dir())

    def test_first_run_until_config_exists(self):
        self.assertTrue(storage.first_run())
        storage.write_json(storage.config_path(), {})
        self.assertFalse(storage.first_run())

    def test_settings_merge_defaults_with_config_one_level_deep(self):
        self.assertEqual(storage.settings()["doubt_threshold"], 0.85)
        (Path(self.tmp.name) / "config.json").write_text('{"doubt_threshold": 0.9, "retention": {"pdf_hours": 1}}')
        s = storage.settings()
        self.assertEqual(s["doubt_threshold"], 0.9)
        self.assertEqual(s["answer_language"], "en")           # untouched default
        self.assertEqual(s["retention"]["pdf_hours"], 1)
        self.assertEqual(s["retention"]["index_days"], 30)     # sibling keeps its default

    def test_storage_dir_setting_moves_data_but_config_stays_in_the_base(self):
        target = Path(self.tmp.name) / "elsewhere"
        (Path(self.tmp.name) / "config.json").write_text(json.dumps({"storage_dir": str(target)}))
        storage.reset()
        r = storage.resolve()
        self.assertEqual(r["source"], "setting")
        self.assertEqual(r["data"], target)
        self.assertEqual(r["cache"], target / "cache")
        self.assertEqual(r["config"], Path(self.tmp.name) / "config.json")
        self.assertEqual(storage.settings()["storage_dir"], str(target))   # still read from the base

    def test_find_index_accepts_an_id_or_a_path(self):
        p = storage.index_path("abc")
        self.assertIsNone(storage.find_index("abc"))
        p.write_bytes(b"")
        self.assertEqual(storage.find_index("abc"), p)
        self.assertEqual(storage.find_index(str(p)), p)


class SandboxFallback(unittest.TestCase):
    """A sandbox with an unwritable home: data goes to the project's .find-manual/, then to a
    temp folder. The blocker is a regular file where a folder is needed, so mkdir(parents=True)
    fails on every OS; a read-only bit would stop neither Windows nor root."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()   # macOS: /var is a link to /private/var, and cwd() resolves it
        self.home = base / "home"
        self.project = base / "project"
        self.tempdir = base / "tmp"
        for d in (self.home, self.project, self.tempdir):
            d.mkdir()
        self.blocker = self.home / "blocked"   # a file: nothing can be created below it
        self.blocker.write_text("not a folder")
        self.saved_env = {k: os.environ.get(k) for k in
                          (storage.ENV_HOME, "HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "LOCALAPPDATA")}
        os.environ.pop(storage.ENV_HOME, None)
        os.environ["HOME"] = str(self.blocker)                                  # macOS: Path.home()
        os.environ["XDG_DATA_HOME"] = str(self.blocker / ".local" / "share")    # Linux
        os.environ["XDG_CACHE_HOME"] = str(self.blocker / ".cache")
        os.environ["LOCALAPPDATA"] = str(self.blocker / "AppData" / "Local")    # Windows
        self.saved_cwd = os.getcwd()
        os.chdir(self.project)
        self.saved_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.tempdir)
        storage.reset()

    def tearDown(self):
        tempfile.tempdir = self.saved_tempdir
        os.chdir(self.saved_cwd)
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        storage.reset()
        self.tmp.cleanup()

    def block_project(self):
        (self.project / f".{storage.APP}").write_text("not a folder")

    def block_temp(self):
        tempfile.tempdir = str(self.blocker)

    def test_read_only_home_falls_back_to_the_project_folder(self):
        r = storage.resolve()
        self.assertEqual(r["source"], "project")
        self.assertEqual(r["data"], self.project / ".find-manual")
        self.assertEqual(r["cache"], self.project / ".find-manual" / "cache")
        self.assertTrue((self.project / ".find-manual").is_dir())
        self.assertIn("home folder is not writable", r["notice"])
        self.assertEqual([p.name for p in self.home.iterdir()], ["blocked"])   # nothing was written to the home

    def test_unwritable_env_home_is_skipped(self):
        os.environ[storage.ENV_HOME] = str(self.blocker / "find-manual")
        storage.reset()
        self.assertEqual(storage.resolve()["source"], "project")

    def test_read_only_project_falls_back_to_a_temp_folder(self):
        self.block_project()
        r = storage.resolve()
        self.assertEqual(r["source"], "temp")
        self.assertEqual(r["data"].parent, self.tempdir)
        self.assertIn("this session only", r["notice"])

    def test_nothing_writable_is_a_handled_error(self):
        self.block_project()
        self.block_temp()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            storage.resolve()
        self.assertEqual(cm.exception.code, 0)
        err = json.loads(out.getvalue())
        self.assertEqual(err["error"], "no_storage")
        self.assertIn(storage.ENV_HOME, err["fallback"])

    def test_env_check_and_setup_report_the_sandbox_once(self):
        import env_check
        import setup
        r = env_check.check(network=False)
        self.assertEqual(r["storage"], "project")
        self.assertIn("notice", r)
        self.assertTrue(r["first_run"])
        s = setup.run(network=False)
        self.assertIn("notice", s)
        self.assertEqual(Path(s["config"]), self.project / ".find-manual" / "config.json")
        self.assertFalse(env_check.check(network=False)["first_run"])


class Emit(unittest.TestCase):
    """Every script prints one JSON object; a console that cannot show × or → must not break it."""
    LINE = "asks when the second hit scores 0.85 × the first → 2–3 ≥ ok"

    def test_non_ascii_survives_a_cp1252_console(self):
        buf = io.BytesIO()
        stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", write_through=True)
        with contextlib.redirect_stdout(stream):
            storage.emit({"line": self.LINE})
        stream.flush()
        self.assertEqual(json.loads(buf.getvalue().decode("utf-8"))["line"], self.LINE)
        stream.detach()

    def test_a_stream_that_cannot_switch_encodings_gets_ascii_json(self):
        class Plain:
            encoding = "ascii"

            def __init__(self):
                self.parts = []

            def write(self, s):
                s.encode("ascii")      # strict, like a bare ASCII console
                self.parts.append(s)

            def flush(self):
                pass
        out = Plain()
        with contextlib.redirect_stdout(out):
            storage.emit({"line": self.LINE})
        text = "".join(out.parts)
        self.assertTrue(text.isascii())
        self.assertEqual(json.loads(text)["line"], self.LINE)

    def test_a_string_buffer_is_left_alone(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            storage.emit({"line": self.LINE})
        self.assertEqual(json.loads(out.getvalue())["line"], self.LINE)

    def test_pip_hint_names_this_interpreter(self):
        hint = storage.pip_hint()
        self.assertTrue(hint.endswith("-m pip install --user pypdfium2"))
        self.assertIn(sys.executable, hint)
        with mock.patch.object(sys, "executable", r"C:\Program Files\Python313\python.exe"):
            self.assertTrue(storage.pip_hint().startswith('"C:\\Program Files\\Python313\\python.exe" -m pip'))


class ReplaceFile(unittest.TestCase):
    """os.replace onto a file another program holds open fails on Windows: retried, then InUse."""

    def test_retries_while_the_target_is_busy(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "src", Path(d) / "dst"
            src.write_text("new")
            dst.write_text("old")
            real, calls = os.replace, []

            def busy_twice(a, b):
                calls.append(1)
                if len(calls) <= 2:
                    raise PermissionError(13, "The process cannot access the file because it is being used by another process")
                real(a, b)
            with mock.patch.object(os, "replace", busy_twice), mock.patch.object(storage.time, "sleep") as sleep:
                storage.replace_file(src, dst)
            self.assertEqual(dst.read_text(), "new")
            self.assertEqual((len(calls), sleep.call_count), (3, 2))

    def test_gives_up_with_in_use_and_keeps_the_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "src", Path(d) / "dst"
            src.write_text("new")
            dst.write_text("old")
            with mock.patch.object(os, "replace", side_effect=PermissionError(13, "in use")) as rep, \
                    mock.patch.object(storage.time, "sleep"):
                with self.assertRaises(storage.InUse) as cm:
                    storage.replace_file(src, dst, tries=3)
            self.assertIsInstance(cm.exception, OSError)
            self.assertIn(str(dst), str(cm.exception))
            self.assertEqual(rep.call_count, 3)
            self.assertEqual(dst.read_text(), "old")

    def test_write_atomic_on_a_busy_target_keeps_the_old_file_and_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "catalog.json"
            storage.write_json(p, {"a": 1})
            with mock.patch.object(os, "replace", side_effect=PermissionError(13, "in use")), \
                    mock.patch.object(storage.time, "sleep"), self.assertRaises(storage.InUse):
                storage.write_json(p, {"a": 2})
            self.assertEqual(json.loads(p.read_text()), {"a": 1})
            self.assertEqual([x.name for x in Path(d).iterdir()], ["catalog.json"])


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
