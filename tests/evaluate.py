#!/usr/bin/env python3
"""Run one test set against an index: top-1 / top-3 hit rate per question type, and latency.

Usage: evaluate.py <doc_id|index.sqlite> <testset.json> [--field query|raw] [--verbose]
`query` is the agent's rewritten question (the baseline), `raw` the user's wording.
Exit code 1 when --field query scores below the set's declared baseline.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import storage  # noqa: E402
from search import Index  # noqa: E402


def run_set(index_path, testset, field="query", k=3, verbose=False, out=sys.stdout):
    idx = Index(index_path)
    data = json.loads(Path(testset).read_text(encoding="utf-8"))
    cases = data["cases"]
    top1 = top3 = 0
    times, by_type = [], {}
    for c in cases:
        t0 = time.perf_counter()
        res = idx.search(c[field], k)
        times.append((time.perf_counter() - t0) * 1000)
        pages = [h["page"] for h in res["hits"]]
        ok1 = bool(pages) and pages[0] in c["expected"]
        ok3 = any(p in c["expected"] for p in pages)
        top1 += ok1
        top3 += ok3
        t = by_type.setdefault(c["type"], [0, 0, 0])
        t[0] += ok1
        t[1] += ok3
        t[2] += 1
        mark = "OK " if ok1 else ("~3 " if ok3 else "MISS")
        if verbose or not ok1:
            print(f"  {mark} #{c['id']:>2} {res['route']:5} doubt={int(res['doubt'])} got {pages} "
                  f"want {c['expected']}  | {c[field]}", file=out)
    n = len(cases)
    times.sort()
    print(f"  [{field}] top-1 {top1}/{n} ({top1 / n:.0%})  top-3 {top3}/{n} ({top3 / n:.0%})  "
          f"median {times[n // 2]:.1f} ms  max {times[-1]:.1f} ms", file=out)
    for kind, (o1, o3, m) in by_type.items():
        print(f"    {kind:6} top-1 {o1}/{m}  top-3 {o3}/{m}", file=out)
    return {"testset": Path(testset).name, "manual": data.get("manual", ""), "field": field, "n": n,
            "top1": top1, "top3": top3, "median_ms": round(times[n // 2], 2), "max_ms": round(times[-1], 2),
            "by_type": by_type, "baseline": data.get("baseline")}


def below_baseline(summary):
    b = summary.get("baseline") or {}
    return summary["field"] == "query" and (summary["top1"] < b.get("top1", 0) or summary["top3"] < b.get("top3", 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("index", help="doc_id or index path")
    ap.add_argument("testset")
    ap.add_argument("--field", default="query", choices=["query", "raw"])
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    s = run_set(storage.require_index(a.index), a.testset, a.field, verbose=a.verbose)
    if below_baseline(s):
        print(f"BELOW BASELINE {s['baseline']}: top-1 {s['top1']}, top-3 {s['top3']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
