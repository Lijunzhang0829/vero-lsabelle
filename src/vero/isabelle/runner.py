"""Repository-level generation -> extract -> fresh evaluation, with frozen budgets."""

from __future__ import annotations

import fcntl
import os
import time
from contextlib import ExitStack, nullcontext
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
    from .run_metrics import segment, setup_metrics

    cfg, bench = read(config_path), validate(read(benchmark_path))
    telemetry = setup_metrics(run_dir, cfg, bench, "vero")
    with segment(telemetry, resume=resume) as lifecycle:
        result = _run(
            benchmark_path, config_path, run_dir, resume=resume, telemetry=telemetry
        )
        lifecycle["status"] = result["status"]
        return result


def _run(benchmark_path, config_path, run_dir, *, resume=False, telemetry=None):
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a") as lock, ExitStack() as stack:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run_locked(
            benchmark_path,
            config_path,
            root,
            resume=resume,
            stack=stack,
            telemetry=telemetry,
        )


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
        if not (
            last["agent"].get("timed_out", False)
            or last["agent"].get("recovered_interruption", False)
        ) or (cap is not None and used >= cap):
            position = (it + 1, 0)
        else:
            position = (it, last.get("chunk", 0) + 1)
    limit = cfg.get("max_iterations", 1)
    return position if limit is None or position[0] < limit else None


def _run_locked(benchmark_path, config_path, root, *, resume, stack, telemetry=None):
    from .run_metrics import TraceChecker

    def make_checker(config):
        checker = TraceChecker(Checker(config), telemetry, root / "native-logs")
        stack.callback(checker.close)
        return checker

    bench = validate(read(benchmark_path))
    cfg = read(config_path)
    if "max_contract_retries" in cfg:
        for key in (
            "total_agent_seconds",
            "max_iterations",
            "iteration_max_time_seconds",
        ):
            cfg.setdefault(key, None)
    continuous = cfg.get("sample_interval_seconds") is not None
    if continuous:
        if cfg["sample_interval_seconds"] <= 0 or "max_contract_retries" not in cfg:
            raise ValueError(
                "continuous sampling requires positive interval and contract policy"
            )
        if (
            cfg.get("iteration_time_chunk_seconds") is not None
            or cfg["agent"]["timeout_seconds"] is not None
        ):
            raise ValueError(
                "continuous mode must not interrupt the agent for time limits"
            )
    if cfg.get("completion_metric", "positive") not in {"positive", "resolved"}:
        raise ValueError("completion_metric must be positive or resolved")
    iteration_cap = cfg.get(
        "iteration_max_time_seconds", cfg.get("iteration_time_chunk_seconds")
    )
    if iteration_cap is not None and iteration_cap <= 0:
        raise ValueError("iteration time limit must be positive")
    if (
        cfg.get("total_agent_seconds") is None
        and cfg.get("iteration_time_chunk_seconds") is None
        and not continuous
    ):
        raise ValueError("unbounded total time requires finite checkpoint chunks")
    if (
        cfg["agent"]["timeout_seconds"] is not None
        and cfg["agent"]["timeout_seconds"] <= 0
    ):
        raise ValueError("agent timeout must be positive")
    retry_mode = "max_contract_retries" in cfg
    if retry_mode:
        if any(
            cfg.get(k) is not None
            for k in (
                "total_agent_seconds",
                "max_iterations",
                "iteration_max_time_seconds",
            )
        ):
            raise ValueError(
                "contract retry termination requires no total/iteration limits"
            )
        if not cfg.get("iteration_time_chunk_seconds") and not continuous:
            raise ValueError(
                "contract retry termination requires persistent checkpoints"
            )
        if cfg.get("completion_metric", "positive") != "positive":
            raise ValueError("contract retry termination requires positive proofs")
    identity = {
        "benchmark_id": bench["benchmark_id"],
        "config": cfg,
        "implementation_id": digest(
            {
                p.name: p.read_text()
                for p in Path(__file__).parent.iterdir()
                if p.suffix in {".py", ".thy", ".ML", ".json"}
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
    if journal["status"] in {"complete", "budget_exhausted", "retries_exhausted"} or (
        journal["status"] == "agent_error" and not retry_mode
    ):
        return journal
    controller = None
    endpoint = None
    if retry_mode:
        from .retry_policy import ContractRetries

        controller = ContractRetries(
            bench,
            root / "contract-retries",
            make_checker(cfg["checker"]),
            cfg["max_contract_retries"],
            telemetry=telemetry,
        )
        endpoint = stack.enter_context(controller.serve())
        journal["status"] = "running"
    cumulative = sum(x["agent"]["elapsed_seconds"] for x in journal["iterations"])
    workspace = Path(cfg["sandbox"])
    while (position := next_position(journal, cfg)) is not None:
        # Operator-owned run directory; stop only between evaluated checkpoints.
        stop = root / "STOP_REQUEST.json"
        if stop.exists():
            journal["status"] = "stopped"
            journal["stop_request"] = read(stop)
            save(journal_path, journal)
            return journal
        index = len(journal["iterations"])
        chunk_seconds = cfg.get("iteration_time_chunk_seconds")
        if chunk_seconds is not None and chunk_seconds <= 0:
            raise ValueError("chunk seconds must be positive")
        total_limit = cfg.get("total_agent_seconds")
        remaining = float("inf") if total_limit is None else total_limit - cumulative
        if remaining <= 0:
            break
        iteration = root / f"iter-{index:03d}"
        if iteration.exists():
            # A submitted call is charged once even if its supervisor died.
            if not resume:
                raise FileExistsError(str(iteration))
            recovered = recover_iteration(
                bench, cfg, root, iteration, workspace, journal, controller=controller
            )
            cumulative += recovered["agent"]["elapsed_seconds"]
            if recovered["complete"]:
                journal["status"] = "complete"
                save(journal_path, journal)
                return journal
            if controller and controller.progress()["all_finished"]:
                journal["status"] = (
                    "integration_error"
                    if controller.progress()["all_proved"]
                    else "retries_exhausted"
                )
                save(journal_path, journal)
                return journal
            continue
        if controller and controller.progress()["all_finished"]:
            report = evaluate(
                bench,
                controller.artifact(),
                root / f"terminal-eval-{time.time_ns()}",
                make_checker(cfg["checker"]),
                **(
                    {"incremental": controller.incremental}
                    if controller.incremental
                    else {}
                ),
            )
            journal["status"] = (
                "complete"
                if report["complete"]
                else "integration_error"
                if controller.progress()["all_proved"]
                else "retries_exhausted"
            )
            save(journal_path, journal)
            return journal
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
        if not ((chunk_seconds is not None or continuous) and index > 0):
            render(bench, workspace, seed)
        feedback = ""
        if cfg.get("feedback", True) and journal["iterations"]:
            feedback = (
                "\n# Previous independent evaluation\n"
                + (root / journal["iterations"][-1]["report_md"]).read_text()
            )
        instruction = (
            INSTRUCTION.format(
                count=len(bench["contracts"]),
                seconds=int(remaining) if total_limit is not None else "unlimited",
            )
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
        if total_limit is None:
            instruction = instruction.replace(
                "Work through as many specifications as possible within unlimited seconds.",
                "There is no total time limit. Continue until independent evaluation confirms all fixed contracts have positive proofs and repository integration succeeds.",
            )
            instruction += "\nAn agent reply or checkpoint cutoff does not end this task. Unresolved targets must remain recorded with blockers and next approaches. Checkpoints preserve work and resume the task.\n"
        if cfg.get("checker", {}).get("dependency_timeout_seconds") is not None:
            instruction += "\n# Checker timing\nDependency loading has a separate budget from the owner-theory check. Reports expose dependency_loading_seconds, owner_theory_seconds and timeout_phase. A dependency_loading timeout is not evidence that the target proof failed. The cache stores successful complete prefix evidence only: timed-out checks do not preserve a live Isabelle process or partial import progress. Do not repeat an unchanged timed-out candidate in the hope of warming that cache; inspect diagnostics and pursue other targets or record the infrastructure blocker. Owner-theory timing includes preceding accepted proofs and helpers in that theory.\n"
        if cfg['checker'].get('backend') == 'a-shared-incremental-v1':
            from .run_metrics import checker_description
            instruction += '\n# Shared Isabelle checker\n' + checker_description(cfg['checker']) + '\nProof-check timeouts consume an attempt. Transport/infrastructure interruption remains distinct from a failed proof.\n'
        if controller:
            progress = controller.progress()
            save(workspace / "CONTRACT_RETRIES.json", progress)
            instruction += (
                "\n# Authoritative contract retry policy\n"
                "This section overrides budget/wind-down instructions above. There is no total time or iteration limit. "
                f"Each fixed contract has one initial attempt plus at most {cfg['max_contract_retries']} retries. "
                "Read CONTRACT_RETRIES.json and use tool.py progress for live authoritative counts. "
                "Generate multiple proofs per call if useful; tool.py check MODULE submits candidates to the supervisor. "
                "Every completed isolated target check consumes one attempt, including unchanged failed candidates explicitly resubmitted. "
                "Infrastructure errors, checkpoint interruptions, prerequisite blockage and repeated successful proofs consume none. "
                "Empty slots do not count. All pending contracts must receive candidates; prioritize never-attempted targets and useful providers. "
                "Do not repeatedly submit an unchanged failed candidate; use the target-specific diagnostic to change the proof. "
                "Do not edit or retry exhausted or proved targets. Preserve accepted helpers. "
                "Successful candidates are retained independently; other unverified sibling proofs are excluded from each target check. "
                "Submit providers before consumers. Failed candidate snapshots are in CHECKPOINT_CANDIDATES.json after a checkpoint; "
                "read CONTRACT_RETRY_FEEDBACK.json for the last checkpoint diagnostics. "
                "A normal reply or checkpoint does not finish the task. Stop only when all positive proofs pass integration, "
                "or every remaining contract exhausts its attempt allowance.\n"
            )
        if continuous:
            save(workspace / "RECOVERED_FEEDBACK.json", controller.feedback())
            instruction += (
                "\n# Continuous execution and durable feedback\n"
                "There is no per-call deadline. Passive snapshots do not stop tools or the agent. "
                "Ignore earlier wind-down/deadline instructions. Continue until the contract policy is finished. "
                "tool.py check MODULE prints VERO_REQUEST_ID to stderr before waiting. "
                "If a return is lost, use tool.py result REQUEST_ID to retrieve the original result without another proof attempt. "
                "tool.py progress lists unacknowledged requests. tool.py feedback cNNNN returns the latest native diagnostics. "
                "After an actual process interruption, start from preserved files, CONTRACT_RETRIES.json and RECOVERED_FEEDBACK.json; "
                "recover pending request results before issuing new checks. A transport receipt does not assert you read the feedback.\n"
            )
        if feedback:
            (workspace / "FEEDBACK.md").write_text(feedback)
        (workspace / "INSTRUCTION.md").write_text(instruction)
        if cfg.get("sandbox_uid") is not None:
            uid = cfg["sandbox_uid"]
            for path in [workspace, *workspace.rglob("*")]:
                if path.is_symlink():
                    raise ValueError("symlink in sandbox")
                os.chown(path, uid, uid)
        allowance = (
            None if continuous else min(cfg["agent"]["timeout_seconds"], remaining)
        )
        if chunk_seconds is not None:
            iteration_used = sum(
                r["agent"]["elapsed_seconds"]
                for r in journal["iterations"]
                if r.get("iteration", 0) == position[0]
            )
            allowance = min(
                allowance,
                chunk_seconds,
                (
                    float("inf")
                    if iteration_cap is None
                    else iteration_cap - iteration_used
                ),
            )
            with (workspace / "INSTRUCTION.md").open("a") as stream:
                stream.write(
                    f"\nThis is checkpoint chunk {position[1]} of iteration {position[0]}, with {allowance:.1f} seconds. The same workspace and conversation resume at the next checkpoint. Preserve successful helpers, imports and proofs; finish or withdraw only the current broken candidate before the cutoff.\n"
                )
        agent_cfg = {
            **cfg["agent"],
            **({"metrics": cfg["metrics"]} if cfg.get("metrics") else {}),
            "timeout_seconds": allowance,
            "persistent_session": chunk_seconds is not None or continuous,
            "resume_session": (chunk_seconds is not None and index > 0)
            or (
                continuous
                and index > 0
                and journal["iterations"][-1]["agent"].get("ok", False)
                and not journal["iterations"][-1]["agent"].get("stop_reason")
            ),
        }
        if controller and controller.incremental:
            instruction_note = "\n# Incremental checking\nChecks reuse unchanged native evidence and validate changed proof prefixes. Complete theories receive a full check and seal only after their imports are sealed. Inspect theory_seals in progress; a proved contract is not itself a sealed theory. Do not edit accepted helpers unnecessarily: changes invalidate affected consumers.\n"
            with (workspace / "INSTRUCTION.md").open("a") as stream:
                stream.write(instruction_note)
        if endpoint:
            agent_cfg["env"] = {
                **agent_cfg.get("env", {}),
                "VERO_RETRY_SOCKET": endpoint,
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
        sampler = nullcontext()
        agent_arguments = {}
        if continuous:
            from .sampling import PassiveSampler, RunMonitor

            sampler = PassiveSampler(
                bench,
                workspace,
                iteration / "samples",
                cfg["sample_interval_seconds"],
                controller,
            )
            agent_arguments["monitor"] = RunMonitor(root, controller)
        journal["status"] = "running"
        save(journal_path, journal)
        with sampler:
            result = agent.run(
                sandbox_dir=workspace,
                instruction_file=workspace / "INSTRUCTION.md",
                **agent_arguments,
            )
        archive(workspace, iteration / "attempts")
        artifact = extract(bench, workspace)
        if controller:
            artifact = checkpoint_candidates(
                controller,
                artifact,
                workspace,
                iteration,
                interrupted=result.get("timed_out", False)
                or not result.get("ok", False)
                or bool(result.get("stop_reason")),
            )
        save(iteration / "artifact.json", artifact)
        report = evaluate(
            bench,
            artifact,
            iteration / "eval",
            make_checker(cfg["checker"]),
            **(
                {"incremental": controller.incremental}
                if controller and controller.incremental
                else {}
            ),
        )
        if controller and report.get("seals") is not None:
            controller.state["theory_seals"] = report["seals"]
            controller.persist()
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
        if done:
            break
        if controller and controller.progress()["all_finished"]:
            journal["status"] = (
                "integration_error"
                if controller.progress()["all_proved"]
                else "retries_exhausted"
            )
            break
        if result.get("stop_reason") == "operator_stop":
            journal["status"] = "stopped"
            journal["stop_request"] = read(root / "STOP_REQUEST.json")
            break
        if not result["ok"] and not result.get("timed_out", False):
            journal["status"] = "agent_error"
            break
    if journal["status"] not in {
        "complete",
        "agent_error",
        "retries_exhausted",
        "integration_error",
        "stopped",
    }:
        journal["status"] = "budget_exhausted"
    save(journal_path, journal)
    return journal


def checkpoint_candidates(
    controller, artifact, workspace, iteration, *, interrupted=False
):
    save(iteration / "candidate-artifact.json", artifact)
    save(workspace / "CHECKPOINT_CANDIDATES.json", artifact)
    result = (
        {
            "status": "checkpoint_interrupted",
            "progress": controller.progress(),
            "note": "Unsubmitted edits remain in the workspace; no attempt charged for interruption.",
        }
        if interrupted
        else controller.submit(artifact, checkpoint=True)
    )
    save(iteration / "contract-retry-feedback.json", result)
    save(workspace / "CONTRACT_RETRY_FEEDBACK.json", result)
    accepted = controller.artifact()
    if not interrupted:
        render(controller.bench, workspace, controller.state["accepted_bodies"])
    return accepted


def recover_iteration(bench, cfg, root, iteration, workspace, journal, controller=None):
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
            "elapsed_seconds": submitted["max_seconds"]
            if submitted["max_seconds"] is not None
            else max(
                0,
                state.get("last_observed_at", submitted["submitted_at"])
                - submitted["submitted_at"],
            ),
            "usage": {},
            "total_cost_usd": None,
            "budget_charge": "full reserved call allowance; actual usage unknown"
            if submitted["max_seconds"] is not None
            else "last observed elapsed time; unobserved tail and token usage unknown",
        }
    archive(workspace, iteration / "attempts")
    artifact_path = iteration / "artifact.json"
    artifact = (
        read(artifact_path) if artifact_path.exists() else extract(bench, workspace)
    )
    if controller and not artifact_path.exists():
        artifact = checkpoint_candidates(
            controller,
            artifact,
            workspace,
            iteration,
            interrupted=result.get("timed_out", False)
            or not result.get("ok", False)
            or bool(result.get("stop_reason")),
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
        report = evaluate(
            bench,
            artifact,
            evaluation,
            Checker(cfg["checker"]),
            **(
                {"incremental": controller.incremental}
                if controller and controller.incremental
                else {}
            ),
        )
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
