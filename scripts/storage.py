#!/usr/bin/env python3
"""Where find-manual keeps its private data, plus the helpers every script shares.

Nothing here is ever written into the repository or the skill folder. All data lives
in a data folder (settings, catalog, vendor maps) and a cache folder (indexes, PDF
working copies, page images). Location, first match wins:

  1. FIND_MANUAL_HOME environment variable          (data = that folder, cache below it)
  2. "storage_dir" in config.json                     (same layout)
  3. the OS data and cache folders                    (see os_dirs)
  4. the project's .find-manual/ folder               (sandboxes with a read-only home)
  5. a temporary folder                               (this session only)

Layout:
  <data>/config.json  catalog.json  vendors/
  <cache>/index/<doc_id>.sqlite   pdf/<doc_id>.pdf   img/<doc_id>-p<page>.png

M3 adds the lock file, atomic writes, settings.py and cleanup.py on top of this.
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

APP = "find-manual"
SCHEMA_VERSION = 3   # index layout; build_index.py writes it, readers reject older files
ENV_HOME = "FIND_MANUAL_HOME"
SKILL_ROOT = Path(__file__).resolve().parent.parent


# ---- output -----------------------------------------------------------------
def emit(obj):
    """Every script prints exactly one JSON object."""
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def fail(error, message, fallback):
    """A handled error: still one JSON object, exit code 0, so the agent follows `fallback`."""
    emit({"error": error, "message": message, "fallback": fallback})
    sys.exit(0)


# ---- location ---------------------------------------------------------------
def os_dirs():
    """(data, cache) folders for this OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP, home / "Library" / "Caches" / APP
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / APP
        return base, base / "Cache"
    data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local" / "share"))) / APP
    cache = Path(os.environ.get("XDG_CACHE_HOME", str(home / ".cache"))) / APP
    return data, cache


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


_resolved = None


def resolve():
    """{"data": Path, "cache": Path, "source": "env|setting|os|project|temp"}; cached per process."""
    global _resolved
    if _resolved:
        return _resolved
    candidates = []
    env = os.environ.get(ENV_HOME)
    if env:
        h = Path(env).expanduser()
        candidates.append((h, h / "cache", "env"))
    os_data, os_cache = os_dirs()
    cfg = read_json(Path(env).expanduser() / "config.json") if env else {}
    cfg = cfg or read_json(os_data / "config.json")
    if cfg.get("storage_dir"):
        h = Path(cfg["storage_dir"]).expanduser()
        candidates.append((h, h / "cache", "setting"))
    candidates.append((os_data, os_cache, "os"))
    proj = Path.cwd() / f".{APP}"
    candidates.append((proj, proj / "cache", "project"))
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    tmp = Path(tempfile.gettempdir()) / f"{APP}-{uid}"
    candidates.append((tmp, tmp / "cache", "temp"))
    for data, cache, source in candidates:
        if _writable(data) and _writable(cache):
            _resolved = {"data": data, "cache": cache, "source": source}
            return _resolved
    fail("no_storage", "no writable folder for find-manual data",
         f"set {ENV_HOME} to a writable folder and run the command again")


def reset():
    """Forget the resolved location (tests change the environment between runs)."""
    global _resolved
    _resolved = None


def data_dir() -> Path:
    return resolve()["data"]


def cache_dir() -> Path:
    return resolve()["cache"]


def _sub(name) -> Path:
    p = cache_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def index_dir() -> Path:
    return _sub("index")


def pdf_dir() -> Path:
    return _sub("pdf")


def image_dir() -> Path:
    return _sub("img")


# ---- settings ---------------------------------------------------------------
def settings() -> dict:
    """defaults.json from the skill, overridden by config.json from the data folder."""
    s = read_json(SKILL_ROOT / "defaults.json")
    s.update(read_json(data_dir() / "config.json"))
    return s


# ---- documents --------------------------------------------------------------
def doc_id_for(path) -> str:
    """Content hash of a file: the same PDF gets the same id wherever it came from."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def index_path(doc_id) -> Path:
    return index_dir() / f"{doc_id}.sqlite"


def find_index(ref):
    """`ref` is a doc_id or a path to an index file. Returns the Path, or None."""
    p = Path(ref).expanduser()
    if p.suffix == ".sqlite" and p.exists():
        return p
    q = index_path(ref)
    return q if q.exists() else None


def require_index(ref) -> Path:
    p = find_index(ref)
    if p is None:
        fail("no_index", f"no index for {ref!r} in {index_dir()}",
             "build it with scripts/build_index.py <pdf>, or fetch the manual again with scripts/fetch.py get")
    return p


def open_index(ref):
    """(connection, meta, path) of an index; a handled error when it is missing, damaged or older."""
    import sqlite3
    path = require_index(ref)
    try:
        db = sqlite3.connect(str(path))
        meta = dict(db.execute("SELECT k, v FROM meta").fetchall())
    except sqlite3.Error as e:
        fail("bad_index", f"{path}: {e}", "rebuild it: scripts/build_index.py <pdf> --force")
    if meta.get("schema") != str(SCHEMA_VERSION):
        fail("stale_index", f"{path} has schema {meta.get('schema')}, current is {SCHEMA_VERSION}",
             f"rebuild it: scripts/build_index.py {meta.get('source_path', '<pdf>')} --force")
    return db, meta, path
