#!/usr/bin/env python3
"""Summarise results/*.jsonl into the tables the README quotes."""
import json, statistics, sys
from collections import defaultdict
from pathlib import Path

ORDER = ["runc-default", "runc-hardened", "runsc-default", "runsc-hardened",
         "runc-tmpfs", "runsc-tmpfs", "runsc-cpus1"]


def load(d):
    rows = []
    for p in sorted(Path(d).glob("*.jsonl")):
        rows += [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return rows


def main(d="results"):
    rows = load(d)
    configs = [c for c in ORDER if any(r["config"] == c for r in rows)]
    configs += sorted({r["config"] for r in rows} - set(configs))

    # What each configuration let the code do.
    probes = defaultdict(dict)
    for r in rows:
        if r["kind"] in ("probe", "abuse"):
            if r["probe"] == "kernel":
                cell = r["note"]
            elif r["allowed"]:
                cell = "allowed"
            else:
                cell = f"blocked ({r.get('errno') or r.get('note', '')})" if r.get("errno") else "blocked"
            probes[r["probe"]][r["config"]] = cell
    print("| probe | " + " | ".join(configs) + " |")
    print("|---|" + "---|" * len(configs))
    for p, cells in probes.items():
        print(f"| {p} | " + " | ".join(cells.get(c, "") for c in configs) + " |")
    print()

    # What each configuration cost, median of the repetitions, with the ratio
    # to the default runtime so the overhead reads directly.
    cost = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["kind"] == "cost":
            cost[r["workload"]][r["config"]].append(r["seconds"])
    base = "runc-default"
    print("| workload | " + " | ".join(configs) + " |")
    print("|---|" + "---|" * len(configs))
    for w, by in cost.items():
        b = statistics.median(by[base]) if by.get(base) else None
        cells = []
        for c in configs:
            xs = by.get(c, [])
            if not xs:
                cells.append("")
                continue
            m = statistics.median(xs)
            ratio = f" ({m / b:.2f}x)" if b and c != base else ""
            cells.append(f"{m * 1000:.0f} ms{ratio} [{min(xs)*1000:.0f}-{max(xs)*1000:.0f}]")
        print(f"| {w} | " + " | ".join(cells) + " |")
    n = {len(v) for by in cost.values() for v in by.values()}
    print(f"\nrepetitions per cell: {sorted(n)}")


if __name__ == "__main__":
    main(*sys.argv[1:])
