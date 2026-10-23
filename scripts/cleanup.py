#!/usr/bin/env python3
"""Remove find-manual's own stale files. Nothing else is ever deleted. Prints one JSON object.

Usage: cleanup.py [run] [--dry-run]
       cleanup.py status
       cleanup.py pin <doc_id> [--note TEXT]
       cleanup.py unpin <doc_id>
       cleanup.py pins

Runs at the start of a lookup (fetch.py get calls it before downloading). Retention
comes from the settings: PDF working copies and page images unused for pdf_hours,
indexes unused for index_days, catalog entries unused for catalog_days, vendor maps
older than vendor_map_days. Above max_cache_mb, PDFs go first, then indexes, oldest
first. Pinned manuals stay whatever their age or size. "Unused" is the file's mtime,
which every search and read bumps. Only files in the skill's own layout are touched:
index/<doc_id>.sqlite, pdf/<doc_id>.pdf, img/<doc_id>-p<N>.png, vendors/*.json, entries
of catalog.json, and leftover temp files older than an hour. A file that cannot be
deleted because another program has it open (Windows) is listed under `skipped` and
tried again next run; nothing crashes. `locked` means another find-manual tool held the
write lock and this run did nothing.
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage  # noqa: E402

DOC = r"[0-9a-f]{16}"
PATTERNS = {"index": re.compile(rf"^({DOC})\.sqlite$"),
            "pdf": re.compile(rf"^({DOC})\.pdf$"),
            "img": re.compile(rf"^({DOC})-p\d+\.png$")}
TMP_RE = re.compile(r"\.tmp$")
TMP_AGE_S = 3600
ISO = "%Y-%m-%dT%H:%M:%S"


def human(seconds):
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


def scan(now):
    """Every file of the skill's own layout in the cache: kind, doc_id, path, mb, unused_s."""
    dirs = {"index": storage.index_dir(), "pdf": storage.pdf_dir(), "img": storage.image_dir()}
    items = []
    for kind, folder in dirs.items():
        for p in folder.iterdir():
            if p.is_symlink() or not p.is_file():
                continue
            m = PATTERNS[kind].match(p.name)
            if not m:
                continue
            st = p.stat()
            items.append({"kind": kind, "doc_id": m.group(1), "path": p, "mb": st.st_size / 1e6,
                          "unused_s": now - st.st_mtime})
    return items


def stale_tmp(now):
    out = []
    for folder in (storage.index_dir(), storage.pdf_dir(), storage.image_dir(), storage.data_dir()):
        for p in folder.iterdir():
            if p.is_file() and not p.is_symlink() and TMP_RE.search(p.name) and now - p.stat().st_mtime > TMP_AGE_S:
                out.append(p)
    return out


def parse_iso(s):
    try:
        return time.mktime(time.strptime(s, ISO))
    except (TypeError, ValueError):
        return None


def run(dry_run=False, now=None, lock_timeout=10):
    now = now or time.time()
    s = storage.settings()
    ret = s.get("retention", {})
    cap_mb = float(s.get("max_cache_mb") or 0)
    pinned = storage.pins()
    removed, skipped, freed = [], [], 0.0

    def remove(path, kind, doc_id=None, why=""):
        """True when the file is gone (or would be, in a dry run). A file another program
        has open cannot be deleted on Windows: it is skipped, reported, and tried next run."""
        nonlocal freed
        try:
            mb = path.stat().st_size / 1e6
        except OSError:
            mb = 0
        if not dry_run:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except PermissionError as e:
                skipped.append({"kind": kind, "doc_id": doc_id, "name": path.name, "mb": round(mb, 2),
                                "why": f"in use by another program or not writable ({e.strerror or e})"})
                return False
        freed += mb
        removed.append({"kind": kind, "doc_id": doc_id, "name": path.name, "mb": round(mb, 2), "why": why})
        return True

    try:
        with storage.lock(timeout=lock_timeout, busy="raise"):
            items = scan(now)
            limits = {"pdf": float(ret.get("pdf_hours", 48)) * 3600,
                      "img": float(ret.get("pdf_hours", 48)) * 3600,
                      "index": float(ret.get("index_days", 30)) * 86400}
            keep = []
            # 1. retention
            for it in items:
                if it["doc_id"] in pinned:
                    keep.append(it)
                elif it["unused_s"] > limits[it["kind"]]:
                    if not remove(it["path"], it["kind"], it["doc_id"], f"unused for {human(it['unused_s'])}"):
                        keep.append(it)
                else:
                    keep.append(it)
            # 2. size cap: PDFs and images first, then indexes; oldest-used first; pinned stay
            total = sum(it["mb"] for it in keep)
            if cap_mb and total > cap_mb:
                order = sorted(keep, key=lambda it: (0 if it["kind"] in ("pdf", "img") else 1, -it["unused_s"]))
                for it in order:
                    if total <= cap_mb:
                        break
                    if it["doc_id"] in pinned:
                        continue
                    if remove(it["path"], it["kind"], it["doc_id"], f"cache above {cap_mb:g} MB"):
                        keep.remove(it)
                        total -= it["mb"]
            # 3. catalog entries
            cat = storage.read_json(storage.catalog_path())
            manuals = cat.get("manuals", {}) if isinstance(cat, dict) else {}
            cat_limit = float(ret.get("catalog_days", 90)) * 86400
            dropped = []
            for doc_id, entry in list(manuals.items()):
                if doc_id in pinned or not isinstance(entry, dict):
                    continue
                t = parse_iso(entry.get("last_used") or entry.get("added"))
                if t is not None and now - t > cat_limit:
                    dropped.append(doc_id)
                    removed.append({"kind": "catalog", "doc_id": doc_id, "name": entry.get("title", ""),
                                    "mb": 0, "why": f"unused for {human(now - t)}"})
            if dropped and not dry_run:
                for d in dropped:
                    del manuals[d]
                cat["manuals"] = manuals
                storage.write_json(storage.catalog_path(), cat)
            # 4. vendor maps
            vlimit = float(ret.get("vendor_map_days", 90)) * 86400
            vmaps = 0
            for p in storage.vendors_dir().glob("*.json"):
                if p.is_symlink() or not p.is_file():
                    continue
                age = now - p.stat().st_mtime
                if age > vlimit:
                    remove(p, "vendor_map", None, f"older than {human(vlimit)}")
                else:
                    vmaps += 1
            # 5. leftover temp files
            for p in stale_tmp(now):
                remove(p, "tmp", None, "leftover temp file")
    except storage.Locked as e:
        return {"locked": str(e), "removed": [], "skipped": [], "freed_mb": 0}

    remaining = {}
    for kind in ("pdf", "index", "img"):
        ks = [it for it in keep if it["kind"] == kind]
        remaining[kind] = {"count": len(ks), "mb": round(sum(it["mb"] for it in ks), 2)}
    remaining["cache_mb"] = round(sum(it["mb"] for it in keep), 2)
    remaining["max_cache_mb"] = cap_mb
    remaining["catalog_entries"] = len(manuals) - (len(dropped) if dry_run else 0)
    remaining["vendor_maps"] = vmaps
    return {"dry_run": dry_run, "removed": removed, "skipped": skipped, "freed_mb": round(freed, 2),
            "remaining": remaining, "pinned": sorted(pinned),
            "retention": {"pdf_hours": ret.get("pdf_hours"), "index_days": ret.get("index_days"),
                          "catalog_days": ret.get("catalog_days"), "vendor_map_days": ret.get("vendor_map_days")}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("pin")
    p.add_argument("doc_id")
    p.add_argument("--note", default="")
    sub.add_parser("unpin").add_argument("doc_id")
    sub.add_parser("pins")
    ap.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    cmd = a.cmd or "run"
    if cmd == "run":
        storage.emit(run(dry_run=a.dry_run))
    elif cmd == "status":
        out = run(dry_run=True)
        out["note"] = "status only; nothing was deleted. `removed` lists what the next run would remove"
        storage.emit(out)
    elif cmd == "pin":
        with storage.lock():
            entry = storage.pin(a.doc_id, a.note)
        storage.emit({"pinned": a.doc_id, **entry, "pins": sorted(storage.pins())})
    elif cmd == "unpin":
        with storage.lock():
            ok = storage.unpin(a.doc_id)
        storage.emit({"unpinned": a.doc_id if ok else None, "was_pinned": ok, "pins": sorted(storage.pins())})
    elif cmd == "pins":
        storage.emit({"pins": storage.pins()})


if __name__ == "__main__":
    main()
