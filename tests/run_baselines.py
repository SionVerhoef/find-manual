#!/usr/bin/env python3
"""Regression gate for indexing and ranking: build every test manual and run its test set.

Usage: run_baselines.py [--manuals DIR] [--verbose]
Needs the PDFs named in tests/testset_*.json under DIR (default <repo>/manuals, which is
gitignored; each set's "source" says where to get the file). Always rebuilds, so the
build time is measured. Exit 1 when any set is below its baseline, a build takes 5 s or
more, or an index is 1 MB or more per 200 pages (the M2 "done when" from the design doc).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from evaluate import below_baseline, run_set  # noqa: E402

MAX_BUILD_S = 5.0
MAX_MB_PER_200P = 1.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manuals", default=str(ROOT / "manuals"))
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    manuals = Path(a.manuals).expanduser()
    # Build into a scratch folder unless the caller set one: the real cache must not get test
    # indexes, and a test manual that is also catalogued would lose its stored vendor URL.
    env = dict(os.environ)
    env.setdefault("FIND_MANUAL_HOME", tempfile.mkdtemp(prefix="find-manual-baselines-"))

    rows, failures, skipped = [], [], []
    for ts in sorted((ROOT / "tests").glob("testset_*.json")):
        data = json.loads(ts.read_text(encoding="utf-8"))
        pdf = manuals / data["file"]
        if not pdf.exists():
            skipped.append(f"{ts.name}: {pdf} missing — source: {data['source']}")
            continue
        print(f"== {data['manual']}")
        r = json.loads(subprocess.check_output(
            [sys.executable, str(ROOT / "scripts" / "build_index.py"), str(pdf), "--force"], text=True, env=env))
        if "error" in r:
            failures.append(f"{ts.name}: build failed: {r['message']}")
            continue
        q = run_set(r["index"], ts, "query", verbose=a.verbose)
        raw = run_set(r["index"], ts, "raw")
        mb = r["size"] / 1e6
        per200 = mb / r["pages"] * 200
        rows.append((data["manual"], r["pages"], r["seconds"], mb, per200, q, raw))
        if below_baseline(q):
            failures.append(f"{ts.name}: below baseline {q['baseline']} (top-1 {q['top1']}, top-3 {q['top3']})")
        if r["seconds"] >= MAX_BUILD_S:
            failures.append(f"{ts.name}: build took {r['seconds']} s (limit {MAX_BUILD_S})")
        if per200 >= MAX_MB_PER_200P:
            failures.append(f"{ts.name}: index is {per200:.2f} MB per 200 pages (limit {MAX_MB_PER_200P})")

    print()
    print(f"{'Manual':<44} {'Pages':>5} {'Build s':>7} {'MB':>5} {'MB/200p':>7} {'Top-1':>6} {'Top-3':>6} {'Raw t1':>6}")
    t1 = t3 = n = rt1 = 0
    for manual, pages, secs, mb, per200, q, raw in rows:
        print(f"{manual[:44]:<44} {pages:>5} {secs:>7.2f} {mb:>5.2f} {per200:>7.2f} "
              f"{q['top1']:>3}/{q['n']:<2} {q['top3']:>3}/{q['n']:<2} {raw['top1']:>3}/{raw['n']:<2}")
        t1 += q["top1"]; t3 += q["top3"]; n += q["n"]; rt1 += raw["top1"]
    if n:
        print(f"{'Total':<44} {'':>5} {'':>7} {'':>5} {'':>7} {t1:>3}/{n:<2} {t3:>3}/{n:<2} {rt1:>3}/{n:<2}"
              f"   ({t1 / n:.0%} / {t3 / n:.0%} / {rt1 / n:.0%})")
    for s in skipped:
        print("SKIP", s)
    for f in failures:
        print("FAIL", f)
    if not rows:
        print("no test manuals found; nothing checked")
        sys.exit(1)
    print("RESULT", "FAIL" if failures else "PASS", f"(baselines kept, build < {MAX_BUILD_S:g} s, index < {MAX_MB_PER_200P:g} MB per 200 pages)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
