#!/usr/bin/env python3
"""Where find-manual keeps its private data, plus the helpers every script shares.

Nothing here is ever written into the repository or the skill folder. All data lives
in a data folder (settings, catalog, pins, vendor maps) and a cache folder (indexes, PDF
working copies, page images). Location, first match wins:

  1. FIND_MANUAL_HOME environment variable          (data = that folder, cache below it)
  2. "storage_dir" in config.json                     (same layout)
  3. the OS data and cache folders                    (see os_dirs)
  4. the project's .find-manual/ folder               (sandboxes with a read-only home)
  5. a temporary folder                               (this session only)

config.json itself always sits in the first writable "base" folder of 1, 3, 4, 5: it is
what holds the storage_dir pointer, so it cannot move with it.

Layout:
  <data>/config.json  catalog.json  pins.json  vendors/<host>.json  .lock
  <cache>/index/<doc_id>.sqlite   pdf/<doc_id>.pdf   img/<doc_id>-p<page>.png

Writers (build_index, settings, setup, cleanup, fetch) take the lock and write
atomically (temp file + os.replace); readers (search, page) never lock. Every use of an
index or PDF bumps its mtime, which is what cleanup.py reads as "last used".
"""
import contextlib
import hashlib
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

APP = "find-manual"
SCHEMA_VERSION = 3   # index layout; build_index.py writes it, readers reject older files
ENV_HOME = "FIND_MANUAL_HOME"
SKILL_ROOT = Path(__file__).resolve().parent.parent

LOCK_TIMEOUT = 30.0        # seconds a writer waits for another tool before giving up
LOCK_STALE = 600.0         # a lock older than this whose process is gone is broken


# ---- output -----------------------------------------------------------------
def emit(obj):
    """Every script prints exactly one JSON object, as UTF-8 whatever the console's code
    page: a Windows pipe defaults to cp1252, which cannot encode ×, → or ≥. A stream that
    cannot be switched gets ASCII-escaped JSON instead, which reads back the same."""
    text = json.dumps(obj, indent=1, ensure_ascii=False)
    out = sys.stdout
    enc = (getattr(out, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    if enc and enc not in ("utf8", "cp65001") and hasattr(out, "reconfigure"):
        with contextlib.suppress(Exception):
            out.reconfigure(encoding="utf-8", errors="replace")
    try:
        print(text, file=out)
    except UnicodeEncodeError:
        print(json.dumps(obj, indent=1, ensure_ascii=True), file=out)


def pip_hint(package="pypdfium2"):
    """The install command for this very interpreter. `python3` is not on PATH on most
    Windows machines, and a package installed into another Python would not help."""
    exe = sys.executable or "python3"
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} -m pip install --user {package}"


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
        probe = p / f".write-test-{os.getpid()}"
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
    """Where everything lives; cached per process.

    {"base": Path (holds config.json), "config": Path, "data": Path, "cache": Path,
     "source": "env|setting|os|project|temp", "notice": str or ""}
    """
    global _resolved
    if _resolved:
        return _resolved
    os_data, os_cache = os_dirs()
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    tmp = Path(tempfile.gettempdir()) / f"{APP}-{uid}"
    proj = Path.cwd() / f".{APP}"
    bases = []
    env = os.environ.get(ENV_HOME)
    if env:
        h = Path(env).expanduser()
        bases.append((h, h, h / "cache", "env"))
    bases += [(os_data, os_data, os_cache, "os"),
              (proj, proj, proj / "cache", "project"),
              (tmp, tmp, tmp / "cache", "temp")]
    for base, data, cache, source in bases:
        if not (_writable(base) and _writable(data) and _writable(cache)):
            continue
        notice = ""
        cfg = read_json(base / "config.json")
        if cfg.get("storage_dir"):
            h = Path(cfg["storage_dir"]).expanduser()
            if _writable(h) and _writable(h / "cache"):
                data, cache, source = h, h / "cache", "setting"
            else:
                notice = f"storage_dir {h} is not writable; using {data} instead"
        if source == "project":
            notice = (f"the home folder is not writable, so data is kept in {data} (gitignored); "
                      f"allow writing to {os_data} to share it with other tools")
        elif source == "temp":
            notice = (f"no writable home or project folder, so data is kept in {data} for this "
                      f"session only; set {ENV_HOME} to a writable folder to keep it")
        _resolved = {"base": base, "config": base / "config.json", "data": data, "cache": cache,
                     "source": source, "notice": notice}
        return _resolved
    fail("no_storage", f"no writable folder for {APP} data",
         f"set {ENV_HOME} to a writable folder and run the command again")


def reset():
    """Forget the resolved location (tests change the environment between runs)."""
    global _resolved
    _resolved = None


def data_dir() -> Path:
    return resolve()["data"]


def cache_dir() -> Path:
    return resolve()["cache"]


def config_path() -> Path:
    return resolve()["config"]


def first_run() -> bool:
    """True until setup.py has written config.json."""
    return not config_path().exists()


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


def vendors_dir() -> Path:
    p = data_dir() / "vendors"
    p.mkdir(parents=True, exist_ok=True)
    return p


def catalog_path() -> Path:
    return data_dir() / "catalog.json"


def pins_path() -> Path:
    return data_dir() / "pins.json"


# ---- settings ---------------------------------------------------------------
def defaults() -> dict:
    return read_json(SKILL_ROOT / "defaults.json")


def settings() -> dict:
    """defaults.json from the skill, overridden by config.json from the data folder.

    One level of nesting is merged (retention.pdf_hours alone leaves the other
    retention keys at their defaults)."""
    s = defaults()
    for k, v in read_json(config_path()).items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k] = dict(s[k], **v)
        else:
            s[k] = v
    return s


# ---- safe writes ------------------------------------------------------------
REPLACE_TRIES = 8          # os.replace onto a file another program has open fails on Windows:
REPLACE_WAIT = 0.25        # retried this many times, this many seconds apart, before giving up


class InUse(OSError):
    """The target file is open in another program (Windows) or not writable."""


def replace_file(src, dst, tries=REPLACE_TRIES, wait=REPLACE_WAIT):
    """os.replace that waits out a reader. On Windows a file that another process has open
    (an index being searched, a PDF in a viewer) cannot be replaced or deleted, and the
    write lock does not cover readers; a few short retries outlast a search or a page read.
    Raises InUse when the target stays busy, so callers can report a handled error."""
    last = None
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError as e:
            last = e
            if attempt < tries - 1:
                time.sleep(wait)
    raise InUse(f"{dst} is open in another program or not writable ({last.strerror or last})") from last


def write_atomic(path, data):
    """Write bytes or text to `path` through a temp file in the same folder + os.replace,
    so a reader never sees a half-written file and a crash leaves the old one intact."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        if isinstance(data, str):
            tmp.write_text(data, encoding="utf-8")
        else:
            tmp.write_bytes(data)
        replace_file(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def write_json(path, obj):
    return write_atomic(path, json.dumps(obj, indent=1, ensure_ascii=False) + "\n")


class Locked(Exception):
    """Another find-manual process holds the write lock."""


def _pid_alive_windows(pid):
    """Windows has no signal 0: open the process for a query and read its exit code."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE, ERROR_INVALID_PARAMETER = 0x1000, 259, 87
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # no such process → invalid parameter; access denied → it exists, another user's
            return False if ctypes.get_last_error() == ERROR_INVALID_PARAMETER else None
        try:
            code = wintypes.DWORD()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE   # a finished process whose handle is still held
            return None
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return None


def _pid_alive(pid):
    """True or False when the OS can tell, None when it cannot (another user's process)."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True           # alive, another user
    except OSError:
        return None
    return True


def _lock_stale(path: Path) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False          # gone already
    if age > LOCK_STALE:
        return True
    pid = read_json(path).get("pid")
    if isinstance(pid, int) and pid != os.getpid() and _pid_alive(pid) is False:
        return True           # the holder died without unlinking
    return False


@contextlib.contextmanager
def lock(timeout=LOCK_TIMEOUT, busy="fail"):
    """Exclusive lock for writers (index build, settings, cleanup, downloads).

    Retries with a short random sleep until `timeout` seconds have passed. A stale lock
    (holder gone, or older than LOCK_STALE) is removed. On timeout, busy="fail" prints
    the handled error and exits; busy="raise" raises Locked so the caller can skip.
    """
    path = data_dir() / ".lock"
    deadline = time.monotonic() + timeout
    fd = None
    while fd is None:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _lock_stale(path):
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            if time.monotonic() >= deadline:
                msg = f"another {APP} process holds {path}"
                if busy == "raise":
                    raise Locked(msg)
                fail("locked", msg, "wait a few seconds and run the command again")
            time.sleep(0.05 + random.random() * 0.15)
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "time": time.time(),
                                 "tool": Path(sys.argv[0]).name}).encode())
        os.close(fd)
        yield path
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def mark_used(path):
    """Bump the mtime: cleanup.py keeps what was used recently."""
    with contextlib.suppress(OSError):
        os.utime(path, None)


# ---- pins -------------------------------------------------------------------
def pins() -> dict:
    """{doc_id: {"since": ISO date, "note": str}}; pinned manuals are never cleaned up."""
    p = read_json(pins_path())
    return p if isinstance(p, dict) else {}


def pin(doc_id, note=""):
    p = pins()
    p[doc_id] = {"since": time.strftime("%Y-%m-%d"), "note": note}
    write_json(pins_path(), p)
    return p[doc_id]


def unpin(doc_id) -> bool:
    p = pins()
    if doc_id not in p:
        return False
    del p[doc_id]
    write_json(pins_path(), p)
    return True


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


def pdf_path(doc_id) -> Path:
    return pdf_dir() / f"{doc_id}.pdf"


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
    mark_used(path)
    return db, meta, path
