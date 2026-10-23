"""settings.py: show, get, set, unset, add, remove; typed values; config.json stays minimal."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import settings as cli  # noqa: E402
import storage  # noqa: E402


def handled_error(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        fn(*args)
    return json.loads(out.getvalue())


class Settings(unittest.TestCase):
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

    def config(self):
        return json.loads(storage.config_path().read_text())

    def test_get_reports_default_and_override(self):
        g = cli.get("retention.pdf_hours")
        self.assertEqual((g["value"], g["default"], g["overridden"]), (48, 48, False))
        cli.set_("retention.pdf_hours", "72")
        g = cli.get("retention.pdf_hours")
        self.assertEqual((g["value"], g["overridden"]), (72, True))

    def test_set_writes_only_the_override_and_keeps_siblings(self):
        r = cli.set_("retention.pdf_hours", "1w")
        self.assertEqual((r["value"], r["previous"]), (168, 48))
        self.assertEqual(self.config(), {"retention": {"pdf_hours": 168}})
        self.assertEqual(storage.settings()["retention"]["index_days"], 30)

    def test_durations_convert_to_the_key_unit(self):
        self.assertEqual(cli.parse_value("retention.pdf_hours", "2d"), 48)
        self.assertEqual(cli.parse_value("retention.index_days", "2w"), 14)
        self.assertEqual(cli.parse_value("retention.index_days", "36h"), 1.5)
        self.assertEqual(cli.parse_value("max_cache_mb", "2d"), "2d")   # not a retention key: a string
        self.assertEqual(cli.parse_value("storage_dir", "null"), None)
        self.assertEqual(cli.parse_value("suppliers", '["a"]'), ["a"])

    def test_values_are_json_typed_and_validated(self):
        self.assertEqual(cli.set_("doubt_threshold", "0.9")["value"], 0.9)
        self.assertEqual(cli.set_("max_cache_mb", "250")["value"], 250)
        self.assertEqual(handled_error(cli.set_, "doubt_threshold", "2")["error"], "bad_value")
        self.assertEqual(handled_error(cli.set_, "retention.pdf_hours", "soon")["error"], "bad_value")
        self.assertEqual(handled_error(cli.set_, "retention.pdf_hours", "-1")["error"], "bad_value")
        self.assertEqual(handled_error(cli.set_, "retention.index_days", "1.5")["error"], "bad_value")
        self.assertEqual(handled_error(cli.set_, "answer_language", "7")["error"], "bad_value")
        err = handled_error(cli.set_, "nope", "1")
        self.assertEqual(err["error"], "unknown_setting")
        self.assertIn("retention.pdf_hours", err["fallback"])

    def test_setting_the_default_removes_the_override(self):
        cli.set_("doubt_threshold", "0.9")
        cli.set_("doubt_threshold", "0.85")
        self.assertEqual(self.config(), {})

    def test_unset_restores_the_default_and_drops_empty_groups(self):
        cli.set_("retention.pdf_hours", "72")
        r = cli.unset("retention.pdf_hours")
        self.assertEqual(r["value"], 48)
        self.assertEqual(self.config(), {})

    def test_add_and_remove_on_list_keys(self):
        r = cli.add("suppliers", "https://a.example/docs")
        self.assertEqual(r["value"], ["https://a.example/docs"])
        cli.add("suppliers", "https://b.example/docs, https://a.example/docs")   # no duplicates
        self.assertEqual(storage.settings()["suppliers"], ["https://a.example/docs", "https://b.example/docs"])
        cli.add("suppliers", "https://a.example/docs", remove=True)
        self.assertEqual(storage.settings()["suppliers"], ["https://b.example/docs"])
        self.assertEqual(handled_error(cli.add, "suppliers", "ftp://x")["error"], "bad_value")
        self.assertEqual(handled_error(cli.add, "max_cache_mb", "1")["error"], "not_a_list")

    def test_manuals_dirs_expand_and_warn_when_missing(self):
        r = cli.add("manuals_dirs", self.tmp.name)
        self.assertEqual(r["value"], [self.tmp.name])
        self.assertNotIn("warnings", r)
        r = cli.add("manuals_dirs", "~/surely-not-a-folder-xyz")
        self.assertTrue(r["value"][1].startswith(str(Path.home())))
        self.assertTrue(any("not a folder" in w for w in r["warnings"]))

    def test_storage_dir_warns_that_data_is_not_moved(self):
        r = cli.set_("storage_dir", str(Path(self.tmp.name) / "elsewhere"))
        self.assertTrue(any("not moved" in w for w in r["warnings"]))
        self.assertEqual(cli.set_("storage_dir", "null")["value"], None)

    def test_show_lists_effective_settings_and_overrides(self):
        cli.set_("max_cache_mb", "100")
        s = cli.show()
        self.assertEqual(s["settings"]["max_cache_mb"], 100)
        self.assertEqual(s["overrides"], {"max_cache_mb": 100})
        self.assertEqual(s["storage"], "env")
        self.assertEqual(Path(s["config"]), storage.config_path())

    def test_cli_round_trip(self):
        import subprocess
        script = Path(__file__).resolve().parents[2] / "scripts" / "settings.py"
        out = subprocess.check_output([sys.executable, str(script), "set", "retention.pdf_hours", "3d"], encoding="utf-8")
        self.assertEqual(json.loads(out)["value"], 72)
        out = subprocess.check_output([sys.executable, str(script), "get", "retention.pdf_hours"], encoding="utf-8")
        self.assertEqual(json.loads(out)["value"], 72)


if __name__ == "__main__":
    unittest.main()
