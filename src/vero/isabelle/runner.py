"""Repository-level generation -> extract -> fresh evaluation, with frozen budgets."""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from .agent import CodexAgent
from .attempts import archive, summarize
from .benchmark import digest, extract, read, render, save, validate
from .checker import Checker, evaluate

INSTRUCTION = """# Isabelle Vero: prove the fixed repository specifications

Your task is to complete all {count} contracts in this sandbox. This is one
repository-level proof task. Decide which files/specifications to inspect and
which proofs to attempt; there is no separate planner or per-contract model.

Read benchmark.json for the exact contract inventory and source names. Editable
slots have `(* VERO START cNNNN prove/disprove *)` / `(* VERO END ... *)` markers.
Edit ONLY the bodies inside those markers. Keep every other byte and every marker
unchanged. Each specification has two slots: prove the fixed proposition, or prove
its logical negation. Exactly one slot may be filled; leave the other as `oops`.
The trusted `vero_disprove` command negates the full universally closed contract,
including its own assumptions. A false specification needs an actual proof of
negation; timeout or failure of automation is not a refutation.

Use Isar proofs, local `have`/`show` helpers, induction and supported proof methods.
Do not add axioms, sorry, raw ML, tactic, setup, or top-level declarations. The
checker audits each result's transitive kernel dependencies. Unfilled `oops`
slots do not publish theorems and cannot be used as facts. Existing proved slots
in the same module can be reused by their displayed `cNNNN_prove` names. Owner
modules are independent: no hidden original proof dependency graph is supplied.

Useful commands (run from this directory):
  python3 /backend/tool.py check Vero_Refine_KHeap_R
  python3 /backend/tool.py check SLOT_MODULE
  python3 /backend/tool.py search 'name_or_statement_substring' --limit 20
  python3 /backend/tool.py progress
The check tool rebuilds a temporary theory from frozen declarations and your
current slot bodies. Read its diagnostics and repair the proofs. The premise
catalog is /public/premises.jsonl. Sanitized semantic sources are at /semantic-base;
legal upstream sources are at /sel4-project/verification/l4v. Toolchain and parent
heaps are supplied separately. Do not seek original Refine proofs, historical
solutions or stock Refine heaps; none is an allowed source.

The final evaluator uses a fresh workspace and ignores changes outside proof
slots. Do not edit ROOT, Vero_Support.thy, benchmark.json or tool scripts. Do not
write a substitute evaluator or forge log messages. A successful tool process
is not proof completion: each filled slot needs a VERO_RESULT audit record.

Work through as many specifications as possible within {seconds} seconds. Spend
that budget on proof attempts and checker feedback, not on summaries or building
an alternative harness. Keep valid completed slots. Before stopping, restore any
unfinished candidate to `oops` and check changed modules. Report unresolved goals
honestly; only independently checked positive proofs complete the Refine task.
"""


def run(benchmark_path, config_path, run_dir, *, resume=False):
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run_locked(benchmark_path, config_path, root, resume=resume)


def next_position(journal, cfg):
    records = journal["iterations"]
    chunk_seconds = cfg.get("iteration_time_chunk_seconds")
    if chunk_seconds is None:
        position = (len(records), 0)
    elif not records:
        position = (0, 0)
    else:
        if chunk_seconds <= 0:
            raise ValueError("chunk seconds must be positive")
        last = records[-1]
        it = last.get("iteration", 0)
        used = sum(
            r["agent"]["elapsed_seconds"]
            for r in records
            if r.get("iteration", 0) == it
        )
        cap = cfg.get("iteration_max_time_seconds", chunk_seconds)
        if (
            not (
                last["agent"].get("timed_out", False)
                or last["agent"].get("recovered_interruption", False)
            )
            or used >= cap
        ):
            position = (it + 1, 0)
        else:
            position = (it, last.get("chunk", 0) + 1)
    return position if position[0] < cfg.get("max_iterations", 1) else None


def _run_locked(benchmark_path, config_path, root, *, resume):
    bench = validate(read(benchmark_path))
    cfg = read(config_path)
    if cfg.get("completion_metric", "positive") not in {"positive", "resolved"}:
        raise ValueError("completion_metric must be positive or resolved")
    if (
        cfg.get("iteration_time_chunk_seconds") is not None
        and cfg.get("iteration_max_time_seconds", cfg["iteration_time_chunk_seconds"])
        <= 0
    ):
        raise ValueError("iteration time limit must be positive")
    identity = {
        "benchmark_id": bench["benchmark_id"],
        "config": cfg,
        "implementation_id": digest(
            {
                p.name: p.read_text()
                for p in Path(__file__).parent.iterdir()
                if p.suffix in {".py", ".thy"}
            }
        ),
    }
    binding = root / "binding.json"
    if binding.exists():
        if read(binding) != identity:
            raise ValueError("run configuration changed")
        if not resume:
            raise FileExistsError("use --resume for an existing run")
    else:
        save(binding, identity)
    journal_path = root / "iterations.json"
    journal = (
        read(journal_path)
        if journal_path.exists()
        else {"iterations": [], "status": "running"}
    )
    if journal["status"] in {"complete", "budget_exhausted", "agent_error"}:
        return journal
    cumulative = sum(x["agent"]["elapsed_seconds"] for x in journal["iterations"])
    workspace = Path(cfg["sandbox"])
    while (position := next_position(journal, cfg)) is not None:
        index = len(journal["iterations"])
        chunk_seconds = cfg.get("iteration_time_chunk_seconds")
        if chunk_seconds is not None and chunk_seconds <= 0:
            raise ValueError("chunk seconds must be positive")
        remaining = cfg["total_agent_seconds"] - cumulative
        if remaining <= 0:
            break
        iteration = root / f"iter-{index:03d}"
        if iteration.exists():
            # A submitted call is charged once even if its supervisor died.
            if not resume:
                raise FileExistsError(str(iteration))
            recovered = recover_iteration(
                bench, cfg, root, iteration, workspace, journal
            )
            cumulative += recovered["agent"]["elapsed_seconds"]
            if recovered["complete"]:
                journal["status"] = "complete"
                save(journal_path, journal)
                return journal
            continue
        iteration.mkdir()
        seed = dict(cfg.get("initial_proofs", {}))
        from .slots import enabled, schedule

        allowed = set(schedule(bench))
        if set(seed) - allowed or any(
            not isinstance(body, str) for body in seed.values()
        ):
            raise ValueError("invalid initial proof slots")
        if cfg.get("resume_artifact", False) and journal["iterations"]:
            previous = read(root / journal["iterations"][-1]["artifact"])
            seed = {k: v["body"] for k, v in previous["slots"].items() if v["found"]}
        if not (chunk_seconds is not None and index > 0):
            render(bench, workspace, seed)
        feedback = ""
        if cfg.get("feedback", True) and journal["iterations"]:
            feedback = (
                "\n# Previous independent evaluation\n"
                + (root / journal["iterations"][-1]["report_md"]).read_text()
            )
        instruction = (
            INSTRUCTION.format(count=len(bench["contracts"]), seconds=int(remaining))
            + feedback
        )
        if "planning" in bench:
            instruction = instruction.replace(
                "there is no separate planner or per-contract model.",
                "a separately accepted PLAN is provided; there is no per-contract model.",
            )
            instruction = instruction.replace(
                "Owner\nmodules are independent: no hidden original proof dependency graph is supplied.",
                "The accepted PLAN supplies real theory imports. Imported completed generated facts may be referenced using their qualified theory names. Unfilled goals supply no facts.",
            )
            instruction += "\n# Accepted PLAN\nRead .vero/plan.json and follow its execution_order and contract order. Prioritize providers before consumers, retain successful providers, and check dependency chains. Only frozen imports are available; do not edit them.\n"
        if enabled(bench):
            instruction = instruction.replace(
                "Owner\nmodules are independent: no hidden original proof dependency graph is supplied.",
                "Additional generated-theory imports are available through imports slots; no hidden original proof dependency graph is supplied.",
            )
            instruction = instruction.replace(
                "Do not add axioms, sorry, raw ML, tactic, setup, or top-level declarations.",
                "Do not add axioms, sorry, raw ML, tactic or setup. File-level auxiliary declarations are permitted only in auxiliary slots.",
            )
            instruction += "\n# Upstream Vero auxiliary slots\nEach theory has an editable imports slot (additional generated theory names only) and a global_aux slot before contract contexts. Each prove/disprove target has a prove_aux/disprove_aux slot immediately before its declaration, inside its frozen context. These correspond to upstream imports/global_aux/proof_aux. Use auxiliary slots for named lemma/theorem/definition declarations with fresh vero_aux_ names; prove helpers fully before use. They are preserved in artifact extraction and independent evaluation. Do not alter contexts, notation, axioms or frozen declarations. Auxiliary theorems may be reused later in the same file or via generated-theory imports; oops provides no theorem. Additional imports must be acyclic; PLAN imports remain in place.\n"
            instruction = instruction.replace(
                "Only frozen imports are available; do not edit them.",
                "PLAN imports remain fixed; the imports slot may add other generated theories, provided the resulting graph is acyclic.",
            )
        instruction += "\n# Persistence and decomposition (adapted from upstream Vero)\nKeep working until the budget is exhausted or every target is resolved. Try at least two genuinely different approaches before setting a failed target aside. Search definitions, use induction, or prove a reusable helper rather than repeatedly applying the same tactic. Preserve proofs that already compile: revert only the newest change that broke a module. Prefer one target at a time and check after each nontrivial edit. Wind down before the deadline: finish or withdraw the current failing candidate, then check affected modules.\n"
        if feedback:
            (workspace / "FEEDBACK.md").write_text(feedback)
        (workspace / "INSTRUCTION.md").write_text(instruction)
        if cfg.get("sandbox_uid") is not None:
            uid = cfg["sandbox_uid"]
            for path in [workspace, *workspace.rglob("*")]:
                if path.is_symlink():
                    raise ValueError("symlink in sandbox")
                os.chown(path, uid, uid)
        allowance = min(cfg["agent"]["timeout_seconds"], remaining)
        if chunk_seconds is not None:
            iteration_used = sum(
                r["agent"]["elapsed_seconds"]
                for r in journal["iterations"]
                if r.get("iteration", 0) == position[0]
            )
            allowance = min(
                allowance,
                chunk_seconds,
                cfg.get("iteration_max_time_seconds", chunk_seconds) - iteration_used,
            )
            with (workspace / "INSTRUCTION.md").open("a") as stream:
                stream.write(
                    f"\nThis is checkpoint chunk {position[1]} of iteration {position[0]}, with {allowance:.1f} seconds. The same workspace and conversation resume at the next checkpoint. Preserve successful helpers, imports and proofs; finish or withdraw only the current broken candidate before the cutoff.\n"
                )
        agent_cfg = {
            **cfg["agent"],
            "timeout_seconds": allowance,
            "persistent_session": chunk_seconds is not None,
            "resume_session": chunk_seconds is not None and index > 0,
        }
        save(
            iteration / "submitted.json",
            {
                "max_seconds": agent_cfg["timeout_seconds"],
                "submitted_at": time.time(),
                "iteration": position[0],
                "chunk": position[1],
            },
        )
        agent = CodexAgent(agent_cfg, iteration / "agent")
        result = agent.run(
            sandbox_dir=workspace, instruction_file=workspace / "INSTRUCTION.md"
        )
        archive(workspace, iteration / "attempts")
        artifact = extract(bench, workspace)
        save(iteration / "artifact.json", artifact)
        report = evaluate(bench, artifact, iteration / "eval", Checker(cfg["checker"]))
        save(
            iteration / "attempt-ledger.json",
            summarize(bench, report, iteration / "attempts"),
        )
        cumulative += result["elapsed_seconds"]
        done = (
            report.get("all_specs_resolved", False)
            if cfg.get("completion_metric") == "resolved"
            else report["complete"]
        )
        journal["iterations"].append(
            {
                "index": index,
                "iteration": position[0],
                "chunk": position[1],
                "agent": result,
                "proved_specs": report["proved_specs"],
                "disproved_specs": report["disproved_specs"],
                "artifact": str((iteration / "artifact.json").relative_to(root)),
                "report_md": str((iteration / "eval/report.md").relative_to(root)),
                "complete": done,
            }
        )
        journal["best_index"] = max(
            journal["iterations"],
            key=lambda r: (r["proved_specs"] + r["disproved_specs"], -r["index"]),
        )["index"]
        journal["elapsed_agent_seconds"] = cumulative
        journal["status"] = "complete" if done else "running"
        save(journal_path, journal)
        if not result["ok"] and not result.get("timed_out", False):
            journal["status"] = "agent_error"
            break
        if done:
            break
    if journal["status"] not in {"complete", "agent_error"}:
        journal["status"] = "budget_exhausted"
    save(journal_path, journal)
    return journal


def recover_iteration(bench, cfg, root, iteration, workspace, journal):
    state_path = iteration / "agent/state.json"
    state = read(state_path) if state_path.exists() else {}
    if state.get("status") == "running" and state.get("pid"):
        proc = Path("/proc") / str(state["pid"]) / "stat"
        if proc.exists() and proc.read_text().rsplit(")", 1)[1].split()[
            19
        ] == state.get("process_start_ticks"):
            raise RuntimeError(
                "agent process is still running; cannot recover over an active call"
            )
    if state.get("status") == "finished":
        result = {k: v for k, v in state.items() if k != "status"}
    else:
        submitted = read(iteration / "submitted.json")
        result = {
            "ok": False,
            "recovered_interruption": True,
            "elapsed_seconds": submitted["max_seconds"],
            "usage": {},
            "total_cost_usd": None,
            "budget_charge": "full reserved call allowance; actual usage unknown",
        }
    archive(workspace, iteration / "attempts")
    artifact_path = iteration / "artifact.json"
    artifact = (
        read(artifact_path) if artifact_path.exists() else extract(bench, workspace)
    )
    save(artifact_path, artifact)
    report_path = iteration / "eval/report.json"
    if report_path.exists():
        report = read(report_path)
        if report["artifact_sha256"] != digest(artifact):
            raise ValueError("recovery artifact mismatch")
    else:
        evaluation = iteration / "eval"
        if evaluation.exists():
            evaluation.rename(iteration / ("eval-interrupted-" + str(time.time_ns())))
        report = evaluate(bench, artifact, evaluation, Checker(cfg["checker"]))
    save(
        iteration / "attempt-ledger.json",
        summarize(bench, report, iteration / "attempts"),
    )
    submitted_position = read(iteration / "submitted.json")
    rec = {
        "iteration": submitted_position.get("iteration", len(journal["iterations"])),
        "chunk": submitted_position.get("chunk", 0),
        "index": len(journal["iterations"]),
        "agent": result,
        "proved_specs": report["proved_specs"],
        "disproved_specs": report["disproved_specs"],
        "artifact": str(artifact_path.relative_to(root)),
        "report_md": str((iteration / "eval/report.md").relative_to(root)),
        "complete": report.get("all_specs_resolved", False)
        if cfg.get("completion_metric") == "resolved"
        else report["complete"],
        "recovered": True,
    }
    journal["iterations"].append(rec)
    journal["best_index"] = max(
        journal["iterations"],
        key=lambda r: (r["proved_specs"] + r["disproved_specs"], -r["index"]),
    )["index"]
    journal["elapsed_agent_seconds"] = sum(
        x["agent"]["elapsed_seconds"] for x in journal["iterations"]
    )
    save(root / "iterations.json", journal)
    return rec
