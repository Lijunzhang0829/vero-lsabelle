#!/usr/bin/env python3
"""Agent-visible read/search/check tools. The final evaluator is separate."""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.environ.get("VERO_SRC", "/vero-src"))
from vero.isabelle.benchmark import extract, read, render
from vero.isabelle.checker import Checker, filled, hygiene


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("module")
    se = sub.add_parser("search")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--offset", type=int, default=0)
    sub.add_parser("progress")
    a = p.parse_args()
    bench = read(os.environ.get("VERO_BENCHMARK", "/benchmark/benchmark.json"))
    if a.cmd == "search":
        found = 0
        printed = 0
        with open("/public/premises.jsonl") as f:
            for line in f:
                x = json.loads(line)
                if (
                    a.query.lower()
                    in (x["canonical_name"] + " " + x["exact_statement"]).lower()
                ):
                    if found >= a.offset and printed < min(a.limit, 100):
                        print(
                            json.dumps(
                                {
                                    "name": x["canonical_name"],
                                    "statement": x["exact_statement"],
                                },
                                ensure_ascii=False,
                            )
                        )
                        printed += 1
                    found += 1
        print(json.dumps({"matches": found, "returned": printed}))
        return
    artifact = extract(bench, Path.cwd())
    if a.cmd == "progress":
        print(
            json.dumps(
                {
                    "total_specs": len(bench["contracts"]),
                    "filled_slots": sum(
                        s["found"] and filled(s["body"])
                        for s in artifact["slots"].values()
                    ),
                    "errors": artifact["file_errors"],
                },
                indent=2,
            )
        )
        return
    modules = {c["module"] for c in bench["contracts"]}
    if a.module not in modules:
        raise SystemExit("unknown module")
    bodies = {}
    for c in bench["contracts"]:
        if c["module"] != a.module:
            continue
        for polarity in ["prove", "disprove"]:
            key = c["id"] + ":" + polarity
            slot = artifact["slots"][key]
            if slot["found"] and filled(slot["body"]):
                error = hygiene(slot["body"])
                if error:
                    raise SystemExit(key + ": " + error)
                bodies[key] = slot["body"]
    with tempfile.TemporaryDirectory(prefix="vero-check-") as tmp:
        render(bench, tmp, bodies)
        result = Checker(read("/benchmark/checker.json")).check(tmp, a.module)
        print(result["log"])
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in {"log", "command"}},
                indent=2,
            )
        )
        raise SystemExit(0 if result["status"] == "ok" else 1)


if __name__ == "__main__":
    main()
