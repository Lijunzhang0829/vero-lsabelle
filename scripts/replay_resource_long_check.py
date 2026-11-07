"""Replay the preserved slow request in an isolated root, without model calls.

Original evidence, proof text and credentials are never modified. The admission control first tests missing seals. The full replay then natively
re-certifies the original Bits_R prerequisite before checking the four targets.
"""

import argparse
import json
import time
from pathlib import Path

from vero.isabelle.benchmark import read, save
from vero.isabelle.checker import Checker
from vero.isabelle.resource_budget import ResourceBudget
from vero.isabelle.resource_checker import ResourceChecker
from vero.isabelle.resource_controller import ResourceController


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checker-config", type=Path, required=True)
    parser.add_argument("--restore-legal-prefix", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    bench = read(args.fixture / "benchmark.json")
    payload = read(args.fixture / "request.json")
    plan = read(args.fixture / "plans.json")["plan"]
    workspace = args.output / "workspace"
    workspace.mkdir()
    for name, content in payload["files"].items():
        path = workspace / name
        if path.resolve().parent != workspace.resolve():
            raise ValueError("fixture has unexpected source path")
        path.write_text(content)
    config = read(args.checker_config)
    checker = Checker(config)
    calls = []
    original_check = checker.check

    def observed_check(*positional, **kwargs):
        calls.append({"directory": str(positional[0]), "theory": positional[1]})
        return original_check(*positional, **kwargs)

    checker.check = observed_check
    budget = ResourceBudget(args.output / "budget.json", args.output.name)
    budget.initialize(wall_clock_seconds=3600, request_id="validation-only")
    resource_checker = ResourceChecker(checker, budget)
    try:
        controller = ResourceController(
            bench,
            args.output / "controller",
            resource_checker,
            workspace,
            args.fixture / "reference",
            budget=budget,
            enforce_workspace=True,
        )
        accepted = controller.dispatch(
            {"cmd": "plan", "request_id": "plan", "plan": plan}
        )
        if accepted.get("version") != 1:
            raise AssertionError(accepted)
        preparation = None
        if args.restore_legal_prefix:
            preparation = controller.dispatch(
                {
                    "cmd": "check",
                    "request_id": "revalidate-bits-prefix",
                    "target_ids": ["c0031", "c0032"],
                    "files": payload["files"],
                }
            )
            save(args.output / "prefix-revalidation.json", preparation)
            assert "Vero_Refine_Bits_R" in controller.plans.snapshot()["seals"], (
                preparation
            )
            calls.clear()
        started = time.monotonic()
        result = controller.dispatch(payload)
        elapsed = time.monotonic() - started
        receipt = {
            "scenario": "original-21-theory-four-target-request",
            "model_calls": 0,
            "source_files": len(payload["files"]),
            "target_ids": payload["target_ids"],
            "elapsed_seconds": elapsed,
            "native_calls": calls,
            "result": result,
            "original_artifacts_unchanged": True,
            "prior_prefix_natively_revalidated": preparation is not None,
        }
        save(args.output / "receipt.json", receipt)
        if args.restore_legal_prefix:
            declarations = [
                c for c in calls if "helper-registrations" in c["directory"]
            ]
            assert declarations, "legitimate request was not elaborated"
            assert {c["theory"] for c in declarations} == {"Vero_Refine_KHeap_R"}, (
                declarations
            )
            assert len(declarations) == 1, declarations
            assert len(result.get("outcomes", [])) == len(payload["target_ids"]), result
        else:
            assert result.get("status") == "rejected", result
            assert "sealed" in result.get("error", ""), result
            assert not calls, "illegal imports reached the native checker"
            assert elapsed < 10, "preflight did not reject promptly"

        print(json.dumps({k: v for k, v in receipt.items() if k != "result"}, indent=2))
        print(
            "outcome:",
            result.get("error", [x.get("status") for x in result.get("outcomes", [])]),
        )
    finally:
        resource_checker.close()


if __name__ == "__main__":
    main()
