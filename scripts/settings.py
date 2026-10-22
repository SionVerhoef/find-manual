#!/usr/bin/env python3
"""Show and change find-manual settings. Prints one JSON object.

Usage: settings.py show
       settings.py get <key>
       settings.py set <key> <value>
       settings.py unset <key>                 (back to the default)
       settings.py add <key> <value>           (list keys: manuals_dirs, suppliers, manual_languages)
       settings.py remove <key> <value>

Keys are those of defaults.json; nested keys use dots (retention.pdf_hours). Values are
JSON (168, 0.9, true, null, ["a","b"]); a value that is not valid JSON is taken as a
string. Retention keys also accept durations in hours, days or weeks (48h, 7d, 2w),
converted to the key's unit. Overrides are written to config.json in the data folder,
atomically and under the write lock; everything else keeps its default.

"keep PDFs for a week"  →  settings.py set retention.pdf_hours 1w
"stop asking so often"  →  settings.py set doubt_threshold 0.95
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import storage  # noqa: E402

LIST_KEYS = {"manuals_dirs", "suppliers", "manual_languages"}
DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(h|d|w|hours?|days?|weeks?)\s*$", re.I)
HOURS = {"h": 1, "d": 24, "w": 168}


def flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def get_path(d, key, default=None):
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(d, key, value):
    parts = key.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def del_path(d, key):
    parts = key.split(".")
    cur = d
    for part in parts[:-1]:
        if part not in cur:
            return
        cur = cur[part]
    cur.pop(parts[-1], None)
    # drop an emptied group so config.json stays minimal
    if len(parts) > 1 and not get_path(d, ".".join(parts[:-1])):
        del_path(d, ".".join(parts[:-1]))


def parse_value(key, raw):
    """JSON if it parses; a duration for retention keys; else the raw string."""
    if isinstance(raw, str):
        m = DURATION_RE.match(raw)
        if m and (key.endswith("_hours") or key.endswith("_days")):
            n, unit = float(m.group(1)), m.group(2)[0].lower()
            hours = n * HOURS[unit]
            value = hours if key.endswith("_hours") else hours / 24
            return int(value) if float(value).is_integer() else value
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def validate(key, value, default):
    """Returns (value, warnings) or raises ValueError with the reason."""
    warnings = []
    if key in LIST_KEYS:
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError(f"{key} must be a list of strings")
    if key == "storage_dir":
        if value is not None and not isinstance(value, str):
            raise ValueError("storage_dir must be a path or null")
        if value:
            warnings.append("storage_dir takes effect on the next command; existing data is not moved")
    elif isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
    elif isinstance(default, int) and not isinstance(default, bool):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ValueError(f"{key} must be a whole number")
        value = int(value)
        if value < 0:
            raise ValueError(f"{key} must be 0 or more")
    elif isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number")
        value = float(value)
        if key == "doubt_threshold" and not 0 < value <= 1:
            raise ValueError("doubt_threshold must be above 0 and at most 1")
        if value < 0:
            raise ValueError(f"{key} must be 0 or more")
    elif isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError(f"{key} must be text")
    if key == "manuals_dirs":
        for d in value:
            if not Path(d).expanduser().is_dir():
                warnings.append(f"{d} is not a folder (yet)")
    if key == "suppliers":
        for u in value:
            if not u.startswith(("http://", "https://")):
                raise ValueError(f"supplier links must start with http:// or https://, got {u!r}")
    return value, warnings


def require_key(key):
    defaults = flatten(storage.defaults())
    if key not in defaults:
        storage.fail("unknown_setting", f"{key!r} is not a setting",
                     "the keys are: " + ", ".join(sorted(defaults)))
    return defaults[key]


def write_config(cfg):
    with storage.lock():
        storage.write_json(storage.config_path(), cfg)


def show():
    loc = storage.resolve()
    return {"config": str(loc["config"]), "storage": loc["source"], "data_dir": str(loc["data"]),
            "cache_dir": str(loc["cache"]), "settings": storage.settings(),
            "overrides": storage.read_json(loc["config"])}


def get(key):
    default = require_key(key)
    cfg = storage.read_json(storage.config_path())
    overridden = get_path(cfg, key, None) is not None or (key.split(".")[0] in cfg and get_path(cfg, key, "_") != "_")
    return {"key": key, "value": get_path(storage.settings(), key), "default": default, "overridden": overridden}


def set_(key, raw):
    default = require_key(key)
    value = parse_value(key, raw)
    try:
        value, warnings = validate(key, value, default)
    except ValueError as e:
        storage.fail("bad_value", str(e), f"the default is {json.dumps(default)}; see settings.py --help")
    previous = get_path(storage.settings(), key)
    cfg = storage.read_json(storage.config_path())
    if value == default:
        del_path(cfg, key)
    else:
        set_path(cfg, key, value)
    write_config(cfg)
    out = {"key": key, "value": value, "previous": previous, "config": str(storage.config_path())}
    if warnings:
        out["warnings"] = warnings
    return out


def unset(key):
    default = require_key(key)
    cfg = storage.read_json(storage.config_path())
    del_path(cfg, key)
    write_config(cfg)
    return {"key": key, "value": default, "config": str(storage.config_path())}


def add(key, raw, remove=False):
    if key not in LIST_KEYS:
        storage.fail("not_a_list", f"{key!r} is not a list setting",
                     "use `set` for it; add/remove work on " + ", ".join(sorted(LIST_KEYS)))
    current = list(get_path(storage.settings(), key) or [])
    items = raw if isinstance(raw, list) else [v.strip() for v in str(raw).split(",") if v.strip()]
    if key == "manuals_dirs":
        items = [str(Path(v).expanduser()) for v in items]
    if remove:
        new = [v for v in current if v not in items]
    else:
        new = current + [v for v in items if v not in current]
    return set_(key, new)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    for name in ("get", "unset"):
        sub.add_parser(name).add_argument("key")
    for name in ("set", "add", "remove"):
        p = sub.add_parser(name)
        p.add_argument("key")
        p.add_argument("value")
    a = ap.parse_args()
    if a.cmd == "show":
        storage.emit(show())
    elif a.cmd == "get":
        storage.emit(get(a.key))
    elif a.cmd == "set":
        storage.emit(set_(a.key, a.value))
    elif a.cmd == "unset":
        storage.emit(unset(a.key))
    elif a.cmd == "add":
        storage.emit(add(a.key, a.value))
    elif a.cmd == "remove":
        storage.emit(add(a.key, a.value, remove=True))


if __name__ == "__main__":
    main()
