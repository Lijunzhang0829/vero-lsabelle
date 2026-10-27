"""Fresh resource-v2 run and explicit same-run continuation.

This launcher never routes through the six-attempt runner. Deployment must still
pass the independent common-tool conformance gate before model invocation.
"""

import argparse
import fcntl
import json
import os
import time
import uuid
from pathlib import Path

from .agent import CodexAgent
from .benchmark import digest, read, save, validate
from .checker import Checker
from .freeform import validate_agent_config
from .processes import kill_tree
from .resource_budget import ResourceBudget
from .resource_checker import ResourceChecker
from .resource_controller import ResourceController
from .resource_metrics import export_resource_metrics
from .resource_processes import ResourceProcessRegistry
from .resource_watchdog import start_watchdog, stop_watchdog
from .response_usage import reconcile_usage
from .run_metrics import TraceChecker, implementation_identity, segment, setup_metrics

PROFILE = "codex_repository_resource_v2"


def validate_config(cfg, *, require_conformance=True):
    if cfg.get("instruction_profile") != PROFILE:
        raise ValueError("resource-v2 profile required")
    if "max_contract_retries" in cfg:
        raise ValueError("legacy retry gate is not supported")
    validate_agent_config(cfg["agent"])
    policy = cfg["budget_policy"]
    if (
        policy.get("kind") != "global_resources"
        or policy.get("token_limit_mode") != "unlimited"
        or policy.get("total_reported_tokens") is not None
    ):
        raise ValueError("explicit unlimited token resource policy required")
    if policy.get("wall_clock_seconds") != 86400:
        raise ValueError("initial wall-clock allowance is 86400 seconds")
    if (
        any(
            policy.get(k) is not None
            for k in ("max_attempts_per_contract", "max_attempts_per_helper")
        )
        or policy.get("helper_green_resets_budget") is not False
    ):
        raise ValueError("no public per-node caps/helper credits in resource mode")
    if policy.get("deadline_action") != "pause_preserve_artifacts":
        raise ValueError("deadline must retain artifacts")
    for key in (
        "allowed",
        "requires_user_authorization",
        "same_run_and_session",
        "preserve_verified_proofs_helpers_plan_and_ledgers",
        "preserve_cumulative_costs_and_time",
        "new_stage_budget_requires_explicit_binding",
        "first_stage_cutoff_report_immutable",
        "downtime_recorded_separately",
    ):
        if policy.get("continuation", {}).get(key) is not True:
            raise ValueError("continuation invariant: " + key)
    expected = {
        "backend": "a-shared-batch-v2",
        "threads": 4,
        "timeout_seconds": 900,
        "total_timeout_seconds": None,
        "command_timeout_seconds": 900,
        "candidate_timeout_seconds": 120,
        "initial_statement_timeout_seconds": 900,
        "helper_statement_timeout_seconds": 900,
        "integration_build_timeout_seconds": 900,
    }
    for key, value in expected.items():
        if cfg.get("checker", {}).get(key) != value:
            raise ValueError("checker profile mismatch: " + key)
    if cfg.get("agent", {}).get("timeout_seconds") is not None:
        raise ValueError("global stage controls generation time")
    if not cfg.get("metrics", {}).get("directory"):
        raise ValueError("common accounting required")
    if not cfg.get("run_id"):
        raise ValueError("fresh run identity required")
    if require_conformance:
        resources = cfg.get("resources", {})
        if (
            resources.get("model_concurrency") != 1
            or resources.get("checker_concurrency") != 1
        ):
            raise ValueError(
                "this implementation requires serial model/checker concurrency"
            )
        if (
            type(resources.get("memory_limit_bytes")) is not int
            or resources["memory_limit_bytes"] <= 0
            or resources.get("cpu_allocation") is None
        ):
            raise ValueError("hardware limits must be bound")
        from .resource_protocol import validate_protocol_binding

        validate_protocol_binding(cfg["verification"])
    return {
        "profile": PROFILE,
        "runtime_supported": True,
        "launch_ready": require_conformance,
    }


def instruction(bench, phase, cfg):
    inventory = json.dumps(
        {
            "base_import": bench["base_import"],
            "contracts": [
                {k: v for k, v in c.items() if k != "module"}
                for c in bench["contracts"]
            ],
        },
        ensure_ascii=False,
    )
    common = """You are a repository-level Isabelle proof agent. Only the frozen parent premises and this run's own artifacts are visible. Choose theory grouping, helper design, search, batch editing and feedback use yourself. Call python3 -m vero.isabelle.resource_tool for every prover operation. Never call Isabelle or its ML runtime directly. No per-target attempt limit exists. Preserve all original propositions, context and attributes. A helper never proves its consumer automatically.\n"""
    if phase == "planning":
        return (
            common
            + """This is the planning phase. Do not generate proofs. Assign every contract once in theory_plan.json and submit `plan theory_plan.json`. Schema: {"theories":[{"name":"T","path":"T.thy","imports":["BASE"],"contracts":["c0001"],"helpers":[]}]}. Use the actual frozen base import below. A helper entry is {"id":"h0001","statement_hash":"SHA256 of exact extracted statement_text stripped of outer whitespace","dependencies":[]}. No proof tools are available until the trusted plan is accepted and the runner enters proving. Once accepted, return immediately.\nFrozen contracts:\n"""
            + inventory
        )
    return (
        common
        + """The trusted plan has been accepted. Read authoritative progress. Create all planned .thy declaration stubs (oops means unproved). `statement [IDs]` elaborates selected declarations (omit IDs for all), requiring sealed generated imports; `check c0001 c0002` checks the selected nodes in their real source order, including batch proofs; `check-theory T` submits the whole planned theory. Missing/unproved goals cannot be premises. Generated imports must be sealed before use. Once all active nodes in a theory are GREEN the controller attempts full seal + session build; inspect failures and repair. `seal T` retries that operation. `integrate` checks/builds the repository after all theories seal.\nUse `revise revision.json` with {plan,reason,expected_version} for regrouping/helper changes. Changing source conservatively invalidates the changed theory and transitive importing theories, so recheck all affected proofs before publishing new seals. Original statements remain audited against immutable native references. `scope scope.json` records target_ids/theory_ids/phase as declared work scope, not an exact causal token allocation. `feedback [ID]` gives detailed failures. Native submissions may return queued/running with a request_id and poll_after_seconds; wait that interval and use `result REQUEST_ID` until completed, then read its result. Keep only one outstanding result query per request. A busy reply with accepted=false means no work was submitted. A transport_timeout with acceptance_unknown requires querying the same request ID, not generating a duplicate submission. Changed input needs a fresh request ID. `search words` is catalog retrieval only; `query c0001 TrueI` performs a metered native fact availability query in that target context. `relate relation.json` with {"helper_id":"h0001","consumer_ids":["c0001"]} records intended helper consumers without claiming actual citation.\nThe first resource stage lasts86400 elapsed seconds including recovery; tokens are unlimited but fully logged. At expiry the controller pauses and retains all artifacts. Do not restart work to bypass the deadline. Future authorized continuation uses the same run/session and cumulative costs.\nFrozen contracts:\n"""
        + inventory
    )


def account_budget(budget, telemetry):
    for event in telemetry.events():
        if event["kind"] == "response_usage":
            usage = event.get("usage") or {}
            budget.record_usage(str(event["response_id"]), usage.get("total_tokens"))
        elif event["kind"] == "usage_missing":
            budget.record_usage("missing:" + event["event_id"], None)


def stop_orphan_invocations(root, journal):
    """Only terminate PID/start-ticks pairs recorded by this trusted run."""
    for row in journal["iterations"]:
        if row.get("status") != "started":
            continue
        path = root / f"iter-{row['index']:04d}" / "agent" / "state.json"
        state = read(path) if path.exists() else {}
        pid, ticks = state.get("pid"), state.get("process_start_ticks")
        if pid is not None and ticks is not None:
            try:
                current = (
                    Path("/proc/" + str(pid) + "/stat")
                    .read_text()
                    .rsplit(")", 1)[1]
                    .split()[19]
                )
            except FileNotFoundError:
                current = None
            if current == str(ticks):
                kill_tree(pid)
        row.update(
            status="recovered_interruption",
            recovered_at=time.time(),
            previous_invocation_end_unobserved=True,
        )
    save(root / "iterations.json", journal)


def root_thread(telemetry, root):
    ids = {
        e["thread_id"]
        for e in telemetry.events()
        if e["kind"] == "invocation_thread" and not e.get("parent_thread_id")
    }
    binding = root / "session-binding.json"
    if binding.exists():
        bound = read(binding)["thread_id"]
        if ids and ids != {bound}:
            raise ValueError("run root session identity changed")
        return bound
    if len(ids) > 1:
        raise ValueError(
            "multiple root sessions cannot be an independent same-session run"
        )
    if ids:
        bound = next(iter(ids))
        save(binding, {"thread_id": bound})
        return bound
    return None


def export_results(root, controller, telemetry, budget):
    targets = [
        {
            "target_id": n,
            "target_kind": "contract" if n in controller.contract_ids else "helper",
        }
        for n in controller.state["contracts"]
    ]
    save(
        root / "resource-metrics.json",
        export_resource_metrics(telemetry, targets=targets),
    )
    budget.status()  # atomically commit any elapsed deadline before exporting
    ledger = read(budget.path)
    for stage in ledger["stages"]:
        if stage["state"] == "active":
            continue
        stamp = stage.get("ended_at", stage["deadline_at"])
        report = {
            "stage": stage,
            "metrics": export_resource_metrics(
                telemetry, targets=targets, cutoff=stamp
            ),
        }
        cutoff = root / ("stage-" + str(stage["stage_id"]) + "-cutoff.json")
        if not cutoff.exists():
            save(cutoff, report)
        elif read(cutoff) != report:
            # Recovered provider records improve knowledge at the SAME cutoff;
            # preserve the original receipt, never mix in later-stage work.
            corrected = root / (cutoff.stem + "-reconciled-" + digest(report) + ".json")
            if not corrected.exists():
                save(corrected, report)
        save(root / ("stage-" + str(stage["stage_id"]) + "-cutoff-latest.json"), report)


def run(
    benchmark, config, root, *, resume=False, authorization_id=None, stage_seconds=None
):
    cfg = read(config)
    validate_config(cfg)
    bench = validate(read(benchmark))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = {
            "benchmark_id": bench["benchmark_id"],
            "config": cfg,
            "implementation": implementation_identity(),
        }
        workspace = Path(cfg["sandbox"])
        workspace.mkdir(parents=True, exist_ok=True)
        if (root / "binding.json").exists():
            if not resume or read(root / "binding.json") != binding:
                raise ValueError(
                    "same-run continuation requires identical frozen binding"
                )
        else:
            if resume or any(workspace.iterdir()):
                raise ValueError("fresh run requires new identity and empty workspace")
            codex_home = Path(cfg["agent"]["home"]) / ".codex"
            if any(
                p
                for name in ("sessions", "archived_sessions")
                for p in (codex_home / name).rglob("*.jsonl")
            ):
                raise ValueError("fresh run requires a new Codex session home")
            save(root / "binding.json", binding)
            save(
                workspace / "contracts.json",
                {"base_import": bench["base_import"], "contracts": bench["contracts"]},
            )
        telemetry = setup_metrics(root, cfg, bench, PROFILE)
        budget = ResourceBudget(root / "resource-budget.json", cfg["run_id"])
        if not budget.path.exists():
            budget.initialize(
                wall_clock_seconds=86400,
                token_limit=None,
                token_limit_mode="unlimited",
                request_id="initial-stage",
            )
        journal = (
            read(root / "iterations.json")
            if (root / "iterations.json").exists()
            else {"iterations": [], "status": "running"}
        )
        if resume:
            stop_orphan_invocations(root, journal)
            reconcile_usage(telemetry, codex_home=Path(cfg["agent"]["home"]) / ".codex")
            account_budget(budget, telemetry)
        native = TraceChecker(
            ResourceChecker(Checker(cfg["checker"]), budget),
            telemetry,
            root / "native-logs",
        )
        controller = ResourceController(
            bench,
            root / "resource-controller",
            native,
            workspace,
            cfg["reference_directory"],
            budget=budget,
            telemetry=telemetry,
            enforce_workspace=True,
        )
        budget.status()  # commit expiry before reconstructing all prior cutoffs
        export_results(root, controller, telemetry, budget)
        if authorization_id:
            if not resume or stage_seconds is None:
                raise ValueError(
                    "continuation requires resume, explicit authorization and new stage seconds"
                )
            budget.continue_stage(
                wall_clock_seconds=stage_seconds,
                authorization_id=authorization_id,
                request_id="continue:" + authorization_id,
            )
            stop_request = root / "STOP_REQUEST.json"
            if stop_request.exists():
                archive = root / "stop-requests" / (digest(authorization_id) + ".json")
                archive.parent.mkdir(exist_ok=True)
                if archive.exists():
                    raise ValueError("continuation stop-request archive already exists")
                stop_request.replace(archive)
        if budget.status()["state"] != "active":
            journal.update(status="resource_paused", progress=controller.progress())
            save(root / "iterations.json", journal)
            native.close()
            return journal
        epoch = uuid.uuid4().hex
        registry_options = {}
        if cfg["agent"].get("external_isolation") == "docker-nonroot":
            container_id = cfg["resources"].get("isolated_container_id")
            if not container_id:
                raise ValueError(
                    "dedicated worker identity must be bound before generation"
                )
            registry_options = {
                "confined_uids": (1001, 1002),
                "isolated_container_id": container_id,
            }
        registry = ResourceProcessRegistry(
            root / "processes" / (epoch + ".json"), cfg["run_id"], **registry_options
        )
        # Attach callbacks as runtime state, never mutate the frozen checker config.
        actual_checker = native.native.checker
        actual_checker.process_registry = registry
        actual_checker._shared.process_registry = registry
        controller.cancel_callback = registry.cleanup
        guardian_directory = root / "watchdogs" / epoch
        guardian = start_watchdog(registry, budget.path, guardian_directory)
        try:
            with (
                segment(telemetry, resume=resume) as lifecycle,
                controller.serve() as endpoint,
            ):
                try:
                    while budget.status()["state"] == "active":
                        if controller.state.get("integration", {}).get("complete"):
                            journal["status"] = "complete"
                            break
                        phase = (
                            "proving"
                            if controller.plans.snapshot()["version"]
                            else "planning"
                        )
                        controller.phase = phase
                        index = len(journal["iterations"])
                        prompt = workspace / "INSTRUCTION.md"
                        prompt.write_text(instruction(bench, phase, cfg))
                        uid = cfg.get("sandbox_uid")
                        if uid is not None:
                            for p in (workspace, prompt, workspace / "contracts.json"):
                                os.chown(p, uid, uid)
                        if budget.status()["state"] != "active":
                            journal["status"] = "resource_paused"
                            break
                        thread_id = root_thread(telemetry, root)
                        if index and thread_id is None:
                            raise ValueError(
                                "cannot resume without recorded root session identity"
                            )
                        agent_config = {
                            **cfg["agent"],
                            # Fresh resource runs own this entire session home;
                            # auxiliary CLI roots must not escape cost accounting.
                            "accounting_exclusive_home": True,
                            "metrics": cfg["metrics"],
                            "role": "repository_" + phase,
                            "timeout_seconds": None,
                            "persistent_session": True,
                            "resume_session": bool(thread_id),
                            "resume_thread_id": thread_id,
                            "shutdown_grace_seconds": 2,
                            "accounting_scope": {
                                "target_ids": [],
                                "scope_kind": "repository",
                                "theory_ids": [],
                                "phase": phase,
                                "attribution_basis": "whole_repository_invocation",
                                "confidence": "declared_not_causal",
                            },
                            "env": {
                                **cfg["agent"].get("env", {}),
                                "FREEFORM_SOCKET": endpoint,
                                "FREEFORM_WORKSPACE": str(workspace),
                            },
                        }
                        # Persist invocation reservation before spawning, so a crash
                        # never reuses an agent log directory or silently starts fresh.
                        iteration = {
                            "index": index,
                            "phase": phase,
                            "status": "started",
                        }
                        # Publish current state before launching a recovered
                        # invocation; retain its earlier failure in history.
                        if journal.get("status") != "running":
                            journal.setdefault("status_history", []).append(
                                {"status": journal.get("status"), "at": time.time()}
                            )
                        journal.update(status="running", progress=controller.progress())
                        journal["iterations"].append(iteration)
                        save(root / "iterations.json", journal)

                        def monitor(phase=phase):
                            if budget.status()["state"] != "active":
                                controller.request_cancel("resource_deadline")
                                return "resource_deadline"
                            if guardian.poll() is not None:
                                budget.pause(
                                    reason="guardian_failed",
                                    request_id="guardian-failed:" + epoch,
                                )
                                registry.cleanup("guardian_failed")
                                return "guardian_failed"
                            if (root / "STOP_REQUEST.json").exists():
                                budget.pause(
                                    reason="operator_stop",
                                    request_id="operator:" + str(index),
                                )
                                controller.request_cancel("operator_cancelled")
                                return "operator_stop"
                            if (
                                phase == "planning"
                                and controller.plans.snapshot()["version"]
                            ):
                                return "plan_accepted"
                            if controller.state.get("integration", {}).get("complete"):
                                return "repository_complete"
                            return None

                        agent = CodexAgent(
                            agent_config, root / f"iter-{index:04d}" / "agent"
                        )
                        agent.process_registry = registry
                        outcome = agent.run(
                            sandbox_dir=workspace,
                            instruction_file=prompt,
                            monitor=monitor,
                        )
                        iteration.update(status="finished", agent=outcome)
                        save(root / "iterations.json", journal)
                        account_budget(budget, telemetry)
                        root_thread(telemetry, root)
                        if outcome.get("stop_reason") == "operator_stop":
                            budget.pause(
                                reason="operator_stop",
                                request_id="operator:" + str(index),
                            )
                            journal["status"] = "paused"
                            break
                        if budget.status()["state"] != "active":
                            journal["status"] = "resource_paused"
                            break
                        if not outcome.get("ok") and outcome.get("stop_reason") not in {
                            "plan_accepted",
                            "repository_complete",
                        }:
                            journal["status"] = "agent_error"
                            break
                    else:
                        journal["status"] = "resource_paused"
                except BaseException:
                    journal["status"] = "interrupted"
                    raise
                finally:
                    account_budget(budget, telemetry)
                    journal["progress"] = controller.progress()
                    if journal["status"] == "complete" and not journal["progress"].get(
                        "all_finished"
                    ):
                        journal["status"] = "integration_invalidated"
                    save(root / "iterations.json", journal)
                    lifecycle["status"] = journal["status"]
        finally:
            try:
                stop_watchdog(guardian, guardian_directory)
            finally:
                export_results(root, controller, telemetry, budget)
        return journal


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["validate-config", "run"])
    p.add_argument("--config", required=True)
    p.add_argument("--benchmark")
    p.add_argument("--output")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--authorization-id")
    p.add_argument("--stage-seconds", type=float)
    a = p.parse_args()
    if a.command == "validate-config":
        result = validate_config(read(a.config))
    else:
        if not a.benchmark or not a.output:
            p.error("run requires benchmark and output")
        result = run(
            a.benchmark,
            a.config,
            a.output,
            resume=a.resume,
            authorization_id=a.authorization_id,
            stage_seconds=a.stage_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if a.command == "run" and result.get("status") not in {
        "complete",
        "paused",
        "resource_paused",
    }:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
