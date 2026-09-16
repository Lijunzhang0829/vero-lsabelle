"""Artifact-driven Isabelle PLAN: frozen contracts, editable organization only."""

import re
from pathlib import Path

from .benchmark import digest, validate


def compile_plan(source, plan):
    validate(source)
    if (
        plan.get("version") != 1
        or plan.get("source_benchmark_id") != source["benchmark_id"]
    ):
        raise ValueError("plan version/source benchmark mismatch")
    modules = plan.get("modules")
    if not isinstance(modules, list) or not modules:
        raise ValueError("nonempty modules required")
    names = [m["name"] for m in modules]
    if len(set(names)) != len(names) or any(
        not re.fullmatch(r"Vero_[A-Za-z0-9_]+", n) or n in {"Vero_All", "Vero_Support"}
        for n in names
    ):
        raise ValueError("invalid/duplicate/reserved theory name")
    order = plan.get("execution_order")
    if (
        not isinstance(order, list)
        or len(order) != len(names)
        or set(order) != set(names)
    ):
        raise ValueError("execution_order must contain every module exactly once")
    positions = {n: i for i, n in enumerate(order)}
    inventory = {c["id"]: c for c in source["contracts"]}
    assigned = []
    imports = {}
    for m in modules:
        ids = m.get("contracts")
        deps = m.get("imports")
        if not isinstance(ids, list) or not ids or any(i not in inventory for i in ids):
            raise ValueError("module must contain known contract IDs")
        if (
            not isinstance(deps, list)
            or len(deps) != len(set(deps))
            or any(
                d not in positions or positions[d] >= positions[m["name"]] for d in deps
            )
        ):
            raise ValueError(
                "imports must be acyclic and precede consumer in execution_order"
            )
        if not isinstance(m.get("rationale"), str) or not m["rationale"].strip():
            raise ValueError("module rationale required")
        assigned.extend(ids)
        imports[m["name"]] = deps
    if len(assigned) != len(inventory) or set(assigned) != set(inventory):
        raise ValueError("each contract must occur exactly once")
    by_name = {m["name"]: m for m in modules}
    body = {k: v for k, v in source.items() if k != "benchmark_id"}
    body["contracts"] = [
        {**inventory[cid], "module": name}
        for name in order
        for cid in by_name[name]["contracts"]
    ]
    body["planning"] = {
        "source_benchmark_id": source["benchmark_id"],
        "plan_sha256": digest(plan),
        "execution_order": order,
        "imports": imports,
    }
    body["policy"] = {**source["policy"], "cross_module_proof_imports": True}
    return validate({**body, "benchmark_id": digest(body)})


def module_order(bench):
    return bench.get("planning", {}).get(
        "execution_order", sorted({c["module"] for c in bench["contracts"]})
    )


def dependencies(bench, module):
    result = set()

    def visit(name):
        for dependency in bench.get("planning", {}).get("imports", {}).get(name, []):
            if dependency not in result:
                result.add(dependency)
                visit(dependency)

    visit(module)
    return result


PLAN_INSTRUCTION = """# Isabelle Vero PLAN

You are the planning agent for a fixed-contract Isabelle proof-generation task.
Read benchmark.json: every contract proposition and context is immutable. You may
read sanitized definitions at /semantic-base and /sel4-project/verification/l4v,
and query /public/premises.jsonl. Original human Refine proofs are forbidden.
No proof bodies from earlier trials are provided. Do not search for them.

Your deliverable is .vero/plan.json. You have {seconds} seconds. Write a JSON
scaffold early; add one complete module at a time and keep it parseable. This is
a file-writing task, not a request to describe a plan in your final response.

Schema:
{{"version":1,"source_benchmark_id":"{benchmark_id}",
  "strategy":"global rationale and approach",
  "modules":[{{"name":"Vero_Foundation","contracts":["c0001"],
               "imports":[],"rationale":"why these contracts belong together"}},
             {{"name":"Vero_Consumer","contracts":["c0002"],
               "imports":["Vero_Foundation"],"rationale":"which generated facts it can reuse"}}],
  "execution_order":["Vero_Foundation","Vero_Consumer"]}}

Cover all {count} contract IDs exactly once. Module names must start Vero_ and
contain only letters, digits, underscores. Vero_All and Vero_Support are reserved.
Imports must reference other planned modules and form a DAG. Providers precede
consumers in execution_order. Contracts within each module are emitted in your
listed order; earlier completed facts can be referenced as cNNNN_prove, imported
facts can also be qualified as Vero_Module.cNNNN_prove. Each contract retains its
own original context. An oops slot never supplies a fact. Planned dependencies
are search guidance, not assumptions or proofs. Avoid unnecessarily broad import
chains: one broken imported theory can block consumers.

Choose grouping/order based on definitions and reusable correspondence results,
not just alphabetical original owner grouping. Explain each module's role and
likely reusable prerequisites in its rationale. Do not change specs, create new
specifications, import external theories, or write proof bodies in the plan.
For slot-schema v2 benchmarks, generation may introduce file-level helper lemmas
and definitions in global_aux/proof_aux slots, or local helpers inside a proof.
Suggest helper strategies in rationales where useful, but do not add helper IDs
to the fixed contract inventory. Helpers are not independently scored or scheduled
nodes. Legacy schema v1 benchmarks support only proof-local helpers.

Run `python3 /backend/tool.py validate-plan .vero/plan.json` for structural feedback.
Partial plans will fail exact coverage until all IDs are assigned. Read the errors
and repair the file. End only with a valid complete plan. The supervisor will
independently validate coverage, identity, names and topology, render native
Isabelle imports and check all declarations before accepting the plan.
"""


def check_plan_file(source, path):
    from .benchmark import read

    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError("invalid plan file")
    return compile_plan(source, read(path))
