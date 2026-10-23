"""install.py: a copy under ~/.agents/skills, a link (a second copy on Windows without the
symlink privilege) under ~/.claude/skills, and an uninstall that removes only what it made.
Runs against a scratch home (--home), so nothing here touches the real one."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "install.py"
SHIPPED = ("SKILL.md", "defaults.json", "LICENSE", "README.md", "scripts/search.py", "reference/hosts.md")


def run(*args, check=True):
    p = subprocess.run([sys.executable, str(INSTALL), *args], capture_output=True, encoding="utf-8")
    if check and p.returncode != 0:
        raise AssertionError(f"install.py {' '.join(args)} failed:\n{p.stdout}\n{p.stderr}")
    return p


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.agents = self.home / ".agents" / "skills" / "find-manual"
        self.claude = self.home / ".claude" / "skills" / "find-manual"

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *args):
        return json.loads(run("--home", str(self.home), *args).stdout)

    def gone(self, p):
        return not p.exists() and not p.is_symlink()

    def test_install_then_uninstall_leaves_nothing_of_its_own(self):
        r = self.install()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["agents_how"], "copy")
        self.assertIn(r["claude_how"], ("link", "copy"))          # copy: Windows without the privilege
        for root in (self.agents, self.claude):
            for name in SHIPPED:
                self.assertTrue((root / name).is_file(), f"{root / name}")
        self.assertTrue((self.agents / ".installed-from").is_file())
        self.assertFalse((self.agents / "tests").exists())          # tests, CI and contributor files stay in the repo
        self.assertFalse((self.agents / ".github").exists())

        r = self.install("--uninstall")
        self.assertEqual(r["action"], "uninstall")
        self.assertTrue(self.gone(self.agents) and self.gone(self.claude))
        self.assertTrue((self.home / ".agents" / "skills").is_dir())   # only the skill's own folders go
        self.assertTrue((self.home / ".claude" / "skills").is_dir())
        self.assertTrue((REPO / "SKILL.md").is_file())                # and never the clone

    def test_reinstall_replaces_its_own_copy(self):
        self.install()
        (self.agents / "stale.txt").write_text("left over from an older version")
        r = self.install()
        self.assertTrue(r["ok"], r)
        self.assertFalse((self.agents / "stale.txt").exists())
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_refuses_a_folder_it_did_not_create_unless_forced(self):
        self.agents.mkdir(parents=True)
        (self.agents / "SKILL.md").write_text("someone else's skill")
        p = run("--home", str(self.home), check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not created by install.py", p.stderr)
        self.assertEqual((self.agents / "SKILL.md").read_text(), "someone else's skill")
        r = self.install("--force")
        self.assertTrue(r["ok"], r)
        self.assertNotEqual((self.agents / "SKILL.md").read_text(), "someone else's skill")

    def test_link_mode_points_at_the_clone_and_uninstall_leaves_the_clone(self):
        r = self.install("--link")
        self.assertTrue(r["ok"], r)
        if r["agents_how"].startswith("link"):
            self.assertTrue(self.agents.is_symlink())
            self.assertEqual(self.agents.resolve(), REPO)
        else:
            self.assertEqual(r["agents_how"], "copy")               # Windows without the privilege
        r = self.install("--uninstall")
        self.assertTrue(self.gone(self.agents) and self.gone(self.claude))
        self.assertTrue((REPO / "SKILL.md").is_file() and (REPO / "scripts" / "search.py").is_file())


if __name__ == "__main__":
    unittest.main()
