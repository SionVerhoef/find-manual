#!/usr/bin/env python3
"""Check the environment find-manual runs in. No arguments; prints one JSON object.

Reports python, pdf_lib (pypdfium2, or the pypdf / pdftotext fallback that is present),
sqlite (FTS5), where data lives (data_dir, cache_dir, config, storage source, writable),
network, a browser hint, cache size, and first_run (true until setup.py has written
config.json; SKILL.md runs setup.py then). Problems come with the fallback to take, as
in reference/fallbacks.md. When data lives in the project folder or a temp folder, the
notice says so: tell the user once.

--no-network skips the connectivity probe (a TCP connect to example.com:443; no request
is sent).
"""
import argparse
import os
import platform
import shutil
import socket
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage  # noqa: E402

MIN_PYTHON = (3, 9)
PROBE_HOST = ("example.com", 443)


def _version(package):
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return "unknown"


def pdf_library():
    """The PDF library in use: pypdfium2, else the fallback that is present, else None."""
    try:
        import pypdfium2  # noqa: F401
        return {"name": "pypdfium2", "version": _version("pypdfium2")}
    except ImportError:
        pass
    try:
        import pypdf  # noqa: F401
        return {"name": "pypdf", "version": _version("pypdf")}
    except ImportError:
        pass
    exe = shutil.which("pdftotext")
    if exe:
        return {"name": "pdftotext", "version": exe}
    return None


def fts5_available():
    try:
        db = sqlite3.connect(":memory:")
        db.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        db.close()
        return True
    except sqlite3.Error:
        return False


def network_available(timeout=2.0):
    try:
        with socket.create_connection(PROBE_HOST, timeout=timeout):
            return True
    except OSError:
        return False


# (executable that must be present, the command to run) per platform. On Windows `start`
# is a cmd built-in whose first quoted argument is a window title, so the empty "" is what
# makes it open the URL; `cmd /c` makes the same line work from PowerShell and from cmd.
OPEN_COMMANDS = {"darwin": ("open", "open <url>"),
                 "win": ("cmd", 'cmd /c start "" "<url>"'),
                 "linux": ("xdg-open", "xdg-open <url>")}


def browser_hint(platform=None):
    """How to open a link in the system browser when the session has no browser tool."""
    platform = platform or ("win" if os.name == "nt" else sys.platform)
    key = "darwin" if platform == "darwin" else "win" if platform.startswith("win") else "linux"
    exe, command = OPEN_COMMANDS[key]
    present = shutil.which(exe) is not None
    return {"open_command": command if present else None,
            "hint": ("open the link with the agent's browser tool in a NEW tab; without one, run "
                     f"`{command}`; without any browser, show scripts/page.py image and the plain link")}


def folder_mb(path):
    total = 0
    for p in Path(path).rglob("*"):
        if p.is_file() and not p.is_symlink():
            total += p.stat().st_size
    return round(total / 1e6, 2)


def check(network=True):
    problems = []
    py_ok = sys.version_info >= MIN_PYTHON
    if not py_ok:
        problems.append({"problem": f"Python {platform.python_version()} is older than 3.9",
                         "fallback": "read a few pages with the agent's own web fetch and PDF tools, without an index"})
    lib = pdf_library()
    if lib is None:
        problems.append({"problem": "no PDF library (pypdfium2, pypdf or pdftotext)",
                         "fallback": f"run: {storage.pip_hint()} — then retry"})
    elif lib["name"] != "pypdfium2":
        problems.append({"problem": f"pypdfium2 is not installed; only {lib['name']} is present",
                         "fallback": f"run: {storage.pip_hint()} — build_index.py and page images need it"})
    fts5 = fts5_available()
    if not fts5:
        problems.append({"problem": "this Python's SQLite has no FTS5",
                         "fallback": "use a python.org or Homebrew Python 3.9+, or read pages with the agent's own PDF tool"})

    loc = storage.resolve()
    writable = True   # resolve() only returns folders it could write to
    net = network_available() if network else None
    if net is False:
        problems.append({"problem": "no network",
                         "fallback": "use a local manuals folder, a cached index, or a PDF the user attaches"})
    s = storage.settings()
    cache_mb = folder_mb(loc["cache"])
    report = {
        "python": {"version": platform.python_version(), "ok": py_ok, "executable": sys.executable},
        "pdf_lib": lib,
        "sqlite": {"version": sqlite3.sqlite_version, "fts5": fts5},
        "data_dir": str(loc["data"]),
        "cache_dir": str(loc["cache"]),
        "config": str(loc["config"]),
        "storage": loc["source"],
        "writable": writable,
        "first_run": storage.first_run(),
        "network": net,
        "browser": browser_hint(),
        "cache_mb": cache_mb,
        "max_cache_mb": s.get("max_cache_mb"),
        "ok": not problems,
        "problems": problems,
    }
    if loc["notice"]:
        report["notice"] = loc["notice"]
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-network", action="store_true", help="skip the connectivity probe")
    a = ap.parse_args()
    storage.emit(check(network=not a.no_network))


if __name__ == "__main__":
    main()
