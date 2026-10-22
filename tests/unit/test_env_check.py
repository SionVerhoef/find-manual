"""env_check.py and setup.py: the first-run flow, without network."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import env_check  # noqa: E402
import setup  # noqa: E402
import storage  # noqa: E402


class EnvCheck(unittest.TestCase):
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

    def test_reports_every_field_skill_md_relies_on(self):
        r = env_check.check(network=False)
        for key in ("python", "pdf_lib", "sqlite", "data_dir", "cache_dir", "config", "storage",
                    "writable", "first_run", "network", "browser", "cache_mb", "max_cache_mb", "ok", "problems"):
            self.assertIn(key, r)
        self.assertTrue(r["python"]["ok"])
        self.assertTrue(r["sqlite"]["fts5"])
        self.assertEqual(r["storage"], "env")
        self.assertTrue(r["first_run"])
        self.assertIsNone(r["network"])          # skipped, not failed
        self.assertNotIn("notice", r)            # a normal location needs no notice
        self.assertIn("NEW tab", r["browser"]["hint"])

    def test_cli_prints_one_json_object(self):
        out = subprocess.check_output([sys.executable, str(SCRIPTS / "env_check.py"), "--no-network"], text=True)
        self.assertEqual(json.loads(out)["data_dir"], self.tmp.name)


class Setup(EnvCheck):
    def test_first_run_writes_config_and_asks_the_optional_question_once(self):
        r = setup.run(network=False)
        self.assertTrue(r["first_run"])
        self.assertEqual(len(r["lines"]), 3)
        self.assertIn("48 h", r["lines"][0])
        self.assertIn(self.tmp.name, r["lines"][1])
        self.assertEqual(r["optional"], setup.OPTIONAL)
        self.assertTrue(storage.config_path().exists())
        self.assertEqual(json.loads(storage.config_path().read_text()), {})
        self.assertFalse(env_check.check(network=False)["first_run"])
        again = setup.run(network=False)
        self.assertFalse(again["first_run"])
        self.assertNotIn("optional", again)

    def test_suppliers_and_manuals_dirs_are_saved_and_checked(self):
        folder = Path(self.tmp.name) / "manuals"
        folder.mkdir()
        r = setup.run(suppliers=["https://docs.vendor.example", "ftp://no"],
                      manuals_dirs=[str(folder), str(folder / "missing")], network=False)
        self.assertEqual(r["added"], {"suppliers": ["https://docs.vendor.example"], "manuals_dirs": [str(folder)]})
        self.assertEqual(len(r["problems"]), 2)
        self.assertFalse(r["ok"])
        self.assertNotIn("optional", r)
        s = storage.settings()
        self.assertEqual(s["suppliers"], ["https://docs.vendor.example"])
        self.assertEqual(s["manuals_dirs"], [str(folder)])
        setup.run(suppliers=["https://docs.vendor.example"], network=False)   # no duplicate
        self.assertEqual(storage.settings()["suppliers"], ["https://docs.vendor.example"])

    def test_setup_keeps_existing_overrides(self):
        storage.write_json(storage.config_path(), {"doubt_threshold": 0.9})
        setup.run(network=False)
        self.assertEqual(storage.settings()["doubt_threshold"], 0.9)

    def test_cli(self):
        out = subprocess.check_output([sys.executable, str(SCRIPTS / "setup.py"), "--no-network",
                                       "--supplier", "https://docs.vendor.example"], text=True)
        self.assertEqual(json.loads(out)["added"]["suppliers"], ["https://docs.vendor.example"])


if __name__ == "__main__":
    unittest.main()
