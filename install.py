#!/usr/bin/env python3
"""Install the find-manual skill for the agent tools on this machine.

    python3 install.py              # copy to ~/.agents/skills/find-manual, link from ~/.claude/skills
    python3 install.py --link       # link ~/.agents/skills/find-manual to this clone (for development)
    python3 install.py --uninstall  # remove what this script created
    python3 install.py --home DIR   # use DIR instead of the real home folder (for tests)

~/.agents/skills is the open Agent Skills location read by Codex, Gemini CLI and Copilot.
~/.claude/skills is read by Claude Code, Copilot and Cursor; it gets a symlink to the
installed folder, or a second copy on Windows, where symlinks need extra privileges.
Nothing outside those two folders is touched, and nothing is written to this repository.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SKILL = "find-manual"
# What an installed skill needs. Tests, CI and contributor files stay in the repo.
SHIP = ["SKILL.md", "defaults.json", "LICENSE", "README.md", "scripts", "reference"]
MARKER = ".installed-from"  # written into a copied install, so uninstall only removes our own copy


def out(**kw):
    print(json.dumps(kw, indent=1))


def is_link(p: Path) -> bool:
    return p.is_symlink() or (os.name == "nt" and p.exists() and os.path.islink(p))


def remove(p: Path, force: bool):
    """Remove p if it is a link, or a copy this script made; refuse anything else unless forced."""
    if not p.exists() and not p.is_symlink():
        return "absent"
    if is_link(p):
        p.unlink()
        return "link removed"
    if p.is_dir():
        marker = p / MARKER
        if marker.exists() or force:
            shutil.rmtree(p)
            return "copy removed"
        raise SystemExit(f"{p} exists and was not created by install.py; "
                         f"move it away or rerun with --force")
    raise SystemExit(f"{p} exists and is a file; move it away")


def copy_tree(src: Path, dst: Path):
    dst.mkdir(parents=True)
    for name in SHIP:
        s = src / name
        if not s.exists():
            continue
        if s.is_dir():
            shutil.copytree(s, dst / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(s, dst / name)
    (dst / MARKER).write_text(str(src) + "\n", encoding="utf-8")


def link_or_copy(target: Path, source: Path):
    """Point target at source: a symlink where they work, a copy on Windows."""
    if os.name != "nt":
        target.symlink_to(source, target_is_directory=True)
        return "link"
    try:
        target.symlink_to(source, target_is_directory=True)
        return "link"
    except OSError:
        copy_tree(source, target)
        return "copy"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--link", action="store_true", help="link the agents folder to this clone instead of copying")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--force", action="store_true", help="replace an existing folder that this script did not create")
    ap.add_argument("--home", default=None, help="home folder to install under (default: your home)")
    a = ap.parse_args()

    source = Path(__file__).resolve().parent
    if not (source / "SKILL.md").exists():
        raise SystemExit("run install.py from the find-manual repository")
    home = Path(a.home).expanduser().resolve() if a.home else Path.home()
    agents = home / ".agents" / "skills" / SKILL
    claude = home / ".claude" / "skills" / SKILL

    if a.uninstall:
        out(action="uninstall", agents=f"{agents}: {remove(agents, a.force)}",
            claude=f"{claude}: {remove(claude, a.force)}")
        return

    agents_result = remove(agents, a.force)
    claude_result = remove(claude, a.force)
    agents.parent.mkdir(parents=True, exist_ok=True)
    claude.parent.mkdir(parents=True, exist_ok=True)

    if a.link:
        how = link_or_copy(agents, source)
        agents_how = f"{how} -> {source}"
    else:
        copy_tree(source, agents)
        agents_how = "copy"
    claude_how = link_or_copy(claude, agents)

    # Sanity: both locations must resolve to a folder with SKILL.md.
    ok = (agents / "SKILL.md").exists() and (claude / "SKILL.md").exists()
    out(action="install", skill=SKILL, agents_skills=str(agents), agents_how=agents_how,
        claude_skills=str(claude), claude_how=claude_how, ok=ok,
        note="Restart Claude Code (or start a new session) so it reads the new skill.")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
