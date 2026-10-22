#!/usr/bin/env python3
"""First-time setup: check the environment, say where data lives, save config.json.

Usage: setup.py [--supplier URL]... [--manuals-dir PATH]... [--no-network]

Runs once on first use (env_check.py reports first_run: true) and again whenever the
user asks to run setup. It asks no settings questions: it reports only problems, gives
three lines (the defaults, the data folder, the cache folder) and, on the first run,
one optional question the agent may put to the user: supplier doc-portal links or a
local manuals folder. They can also be added later (settings.py add suppliers <url>,
settings.py add manuals_dirs <path>). Headless runs: the defaults apply, nothing is
asked. config.json is saved (empty when nothing was given) so first_run turns false.
Prints one JSON object.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env_check  # noqa: E402
import settings as settings_cli  # noqa: E402
import storage  # noqa: E402

OPTIONAL = "Paste supplier doc-portal links or a local manuals folder, or skip."
LATER = "scripts/settings.py add suppliers <url>  |  scripts/settings.py add manuals_dirs <path>"


def lines(loc, s):
    ret = s.get("retention", {})
    return [
        (f"Defaults: {s.get('answer_language', 'en')} answers; keeps PDF copies {ret.get('pdf_hours')} h, "
         f"indexes {ret.get('index_days')} d, the catalog {ret.get('catalog_days')} d; cache up to "
         f"{s.get('max_cache_mb')} MB; asks when the second hit scores {s.get('doubt_threshold')} × the first."),
        f"Data (settings, catalog, vendor maps): {loc['data']}",
        f"Cache (indexes, PDF copies, page images): {loc['cache']}",
    ]


def run(suppliers=(), manuals_dirs=(), network=True):
    was_first = storage.first_run()
    env = env_check.check(network=network)
    loc = storage.resolve()
    problems = list(env["problems"])
    added = {"suppliers": [], "manuals_dirs": []}
    cfg = storage.read_json(storage.config_path())
    for url in suppliers:
        if not url.startswith(("http://", "https://")):
            problems.append({"problem": f"supplier link {url!r} is not http(s)", "fallback": "paste the portal's full link"})
            continue
        cur = cfg.get("suppliers", s_default("suppliers"))
        if url not in cur:
            cfg["suppliers"] = cur + [url]
            added["suppliers"].append(url)
    for d in manuals_dirs:
        p = Path(d).expanduser()
        if not p.is_dir():
            problems.append({"problem": f"{d} is not a folder", "fallback": "check the path, or skip"})
            continue
        cur = cfg.get("manuals_dirs", s_default("manuals_dirs"))
        if str(p) not in cur:
            cfg["manuals_dirs"] = cur + [str(p)]
            added["manuals_dirs"].append(str(p))
    with storage.lock():
        storage.write_json(storage.config_path(), cfg)
    out = {"first_run": was_first, "ok": not problems, "problems": problems,
           "lines": lines(loc, storage.settings()), "config": str(storage.config_path()),
           "storage": loc["source"], "added": added, "later": LATER}
    if loc["notice"]:
        out["notice"] = loc["notice"]
    if was_first and not (added["suppliers"] or added["manuals_dirs"]):
        out["optional"] = OPTIONAL
    return out


def s_default(key):
    return list(settings_cli.get_path(storage.defaults(), key) or [])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--supplier", action="append", default=[], metavar="URL", help="a vendor doc-portal link")
    ap.add_argument("--manuals-dir", action="append", default=[], metavar="PATH", help="a local folder of manuals")
    ap.add_argument("--no-network", action="store_true")
    a = ap.parse_args()
    storage.emit(run(a.supplier, a.manuals_dir, network=not a.no_network))


if __name__ == "__main__":
    main()
