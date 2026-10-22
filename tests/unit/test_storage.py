"""storage.py: where data lives, the sandbox fallbacks, settings merge, stable document ids."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import storage  # noqa: E402

ROOT_USER = hasattr(os, "geteuid") and os.geteuid() == 0   # root ignores read-only bits


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


@unittest.skipIf(ROOT_USER, "read-only folders do not stop root")
class SandboxFallback(unittest.TestCase):
    """A sandbox with a read-only home: data goes to the project's .find-manual/, then to a temp folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()   # macOS: /var is a link to /private/var, and cwd() resolves it
        self.home = base / "home"
        self.project = base / "project"
        self.tempdir = base / "tmp"
        for d in (self.home, self.project, self.tempdir):
            d.mkdir()
        self.saved_env = {k: os.environ.get(k) for k in
                          (storage.ENV_HOME, "HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "LOCALAPPDATA")}
        os.environ.pop(storage.ENV_HOME, None)
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_DATA_HOME"] = str(self.home / ".local" / "share")
        os.environ["XDG_CACHE_HOME"] = str(self.home / ".cache")
        os.environ["LOCALAPPDATA"] = str(self.home / "AppData" / "Local")
        self.saved_cwd = os.getcwd()
        os.chdir(self.project)
        self.saved_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.tempdir)
        self.home.chmod(stat.S_IRUSR | stat.S_IXUSR)   # read-only home
        storage.reset()

    def tearDown(self):
        for d in (self.home, self.project, self.tempdir):
            with contextlib.suppress(OSError):
                d.chmod(stat.S_IRWXU)
        tempfile.tempdir = self.saved_tempdir
        os.chdir(self.saved_cwd)
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        storage.reset()
        self.tmp.cleanup()

    def test_read_only_home_falls_back_to_the_project_folder(self):
        r = storage.resolve()
        self.assertEqual(r["source"], "project")
        self.assertEqual(r["data"], self.project / ".find-manual")
        self.assertEqual(r["cache"], self.project / ".find-manual" / "cache")
        self.assertTrue((self.project / ".find-manual").is_dir())
        self.assertIn("home folder is not writable", r["notice"])
        self.assertFalse(list(self.home.iterdir()))           # nothing was written to the home

    def test_unwritable_env_home_is_skipped(self):
        os.environ[storage.ENV_HOME] = str(self.home / "find-manual")
        storage.reset()
        self.assertEqual(storage.resolve()["source"], "project")

    def test_read_only_project_falls_back_to_a_temp_folder(self):
        self.project.chmod(stat.S_IRUSR | stat.S_IXUSR)
        r = storage.resolve()
        self.assertEqual(r["source"], "temp")
        self.assertEqual(r["data"].parent, self.tempdir)
        self.assertIn("this session only", r["notice"])

    def test_nothing_writable_is_a_handled_error(self):
        self.project.chmod(stat.S_IRUSR | stat.S_IXUSR)
        self.tempdir.chmod(stat.S_IRUSR | stat.S_IXUSR)
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
