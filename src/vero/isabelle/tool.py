#!/usr/bin/env python3
"""Agent-visible read/search/check tools. The final evaluator is separate."""

import argparse
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, os.environ.get("VERO_SRC", "/vero-src"))
from vero.isabelle.attempts import begin, finish
from vero.isabelle.benchmark import extract, read, render, save
from vero.isabelle.checker import Checker, filled
from vero.isabelle.planning import check_plan_file, dependencies
from vero.isabelle.slots import context, schedule, slot_hygiene


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("module")
    ck.add_argument("--request-id")
    result_parser = sub.add_parser("result")
    result_parser.add_argument("request_id")
    feedback_parser = sub.add_parser("feedback")
    feedback_parser.add_argument("contract", nargs="?")
    plan = sub.add_parser("validate-plan")
    plan.add_argument("path")
    se = sub.add_parser("search")
    se.add_argument("query")
    se.add_argument("--limit", type=int, default=20)
    se.add_argument("--offset", type=int, default=0)
    sub.add_parser("progress")
    a = p.parse_args()
    bench = read(os.environ.get("VERO_BENCHMARK", "/benchmark/benchmark.json"))
    if a.cmd == "validate-plan":
        try:
            compiled = check_plan_file(bench, a.path)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "contracts": len(compiled["contracts"]),
                        "planning": compiled["planning"],
                    }
                )
            )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            print(json.dumps({"valid": False, "error": str(exc)}))
            raise SystemExit(1) from exc
        return
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
    endpoint = os.environ.get("VERO_RETRY_SOCKET")
    if endpoint and a.cmd in {"progress", "check", "result", "feedback"}:
        from vero.isabelle.retry_policy import request

        payload = {"cmd": a.cmd}
        if a.cmd == "check":
            rid = a.request_id or uuid.uuid4().hex
            payload.update(
                artifact=extract(bench, Path.cwd()), module=a.module, request_id=rid
            )
            # Local filenames use a generated name; an untrusted explicit ID
            # never becomes a filesystem path on the client.
            save(Path.cwd() / ".vero-requests" / (uuid.uuid4().hex + ".json"), payload)
            print("VERO_REQUEST_ID " + rid, file=sys.stderr, flush=True)
        elif a.cmd == "result":
            payload["request_id"] = a.request_id
        elif a.cmd == "feedback":
            payload["contract"] = a.contract
        result = request(endpoint, payload)
        print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
        if result.get("request_id"):
            request(endpoint, {"cmd": "ack", "request_id": result["request_id"]})
        failed = result.get("status") in {
            "controller_error",
            "extraction_error",
            "invalid_context",
        } or any(
            r["status"] not in {"proved", "already_proved"}
            for r in result.get("outcomes", [])
        )
        raise SystemExit(1 if failed else 0)
    if a.cmd in {"result", "feedback"}:
        raise SystemExit("durable feedback requires the supervisor controller")
    artifact = extract(bench, Path.cwd())
    if a.cmd == "progress":
        print(
            json.dumps(
                {
                    "total_specs": len(bench["contracts"]),
                    "filled_slots": sum(
                        s["found"] and filled(s["body"])
                        for key, s in artifact["slots"].items()
                        if schedule(bench)[key][1] == "proof"
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
    attempt = begin(Path.cwd() / ".vero-attempts", bench, a.module, artifact)
    all_bodies = {k: v["body"] for k, v in artifact["slots"].items() if v["found"]}
    try:
        effective = context(bench, all_bodies)
    except ValueError as exc:
        finish(attempt, {"status": "rejected", "log": str(exc), "audited": []}, None)
        raise SystemExit(str(exc)) from exc
    closure = dependencies(effective, a.module) | {a.module}
    errors = {m: e for m, e in artifact.get("file_errors", {}).items() if m in closure}
    if errors:
        finish(
            attempt,
            {"status": "extraction_error", "log": str(errors), "audited": []},
            None,
        )
        raise SystemExit(str(errors))
    bodies = {}
    for key, (module, kind) in schedule(bench).items():
        if module not in closure:
            continue
        slot = artifact["slots"][key]
        if slot["found"] and filled(slot["body"]):
            error = slot_hygiene(kind, slot["body"])
            if error:
                finish(
                    attempt,
                    {"status": "rejected", "log": key + ": " + error, "audited": []},
                    None,
                )
                raise SystemExit(key + ": " + error)
            bodies[key] = slot["body"]
    with tempfile.TemporaryDirectory(prefix="vero-check-") as tmp:
        render(bench, tmp, bodies)
        source = Path(tmp) / (a.module + ".thy")
        (attempt / "source.thy").write_text(source.read_text())
        result = Checker(read("/benchmark/checker.json")).check(tmp, a.module)
        finish(attempt, result, source)
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
