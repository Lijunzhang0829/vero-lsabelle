"""Separate PLAN agent, deterministic acceptance, then repository generation."""

import fcntl
import os
import threading
import time
from pathlib import Path

from .agent import CodexAgent
from .benchmark import digest, read, render, save, validate
from .checker import Checker
from .planning import PLAN_INSTRUCTION, check_plan_file
from .run_metrics import TraceChecker, segment, setup_metrics
from .runner import _run as run_with_metrics
from .runner import run


def execute(benchmark_path, config_path, output, *, resume=False):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "pipeline.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _execute(benchmark_path, config_path, root, resume)


def _execute(benchmark_path, config_path, root, resume):
    source = validate(read(benchmark_path))
    cfg = read(config_path)
    binding = {
        "source_benchmark_id": source["benchmark_id"],
        "config": cfg,
        "implementation_id": digest(
            {
                p.name: p.read_text()
                for p in Path(__file__).parent.iterdir()
                if p.suffix in {".py", ".thy", ".ML", ".json"}
            }
        ),
    }
    if (root / "binding.json").exists():
        if not resume or read(root / "binding.json") != binding:
            raise ValueError(
                "existing pipeline requires --resume with unchanged configuration and implementation"
            )
    else:
        save(root / "binding.json", binding)
    telemetry = setup_metrics(root, cfg, source, "vero")
    with segment(telemetry, resume=resume) as lifecycle:
        result = _execute_stages(source, cfg, root, resume, telemetry)
        lifecycle["status"] = result["generation"]["status"]
        return result


def _planning_event(telemetry, kind, **fields):
    if telemetry is not None:
        return telemetry.emit(kind, phase="planning", **fields)
    return None


class _PlanningCheckEvents:
    def __init__(self, telemetry, timeout_seconds, timeout_source):
        self.telemetry = telemetry
        self.timeout_seconds = timeout_seconds
        self.timeout_source = timeout_source

    def emit(self, kind, **fields):
        return _planning_event(
            self.telemetry,
            kind,
            **{
                **fields,
                "check_kind": "declaration",
                "configured_timeout_seconds": self.timeout_seconds,
                "timeout_source": self.timeout_source,
            },
        )


def _recover_plan_usage(telemetry, cfg, stage):
    """Replay only this reserved PLAN invocation and its bound descendants.

    A killed caller does not imply an observed model end or complete usage.
    ResponseUsageCollector deduplicates by response identity and selects only
    threads bound by this invocation's trusted CLI events/parent metadata.
    """
    if telemetry is None:
        return
    from .response_usage import ResponseUsageCollector

    records = telemetry.events()
    agent_dir = (stage / "agent").resolve()
    starts = [
        row
        for row in records
        if row["kind"] == "invocation_started"
        and row.get("role") == "planner"
        and isinstance(row.get("source"), str)
        and Path(row["source"]).resolve() == agent_dir
    ]
    finished = {
        row["invocation_id"]: row
        for row in records
        if row["kind"] == "invocation_finished"
    }
    missing_invocations = {
        row.get("invocation_id") for row in records if row["kind"] == "usage_missing"
    }
    home = (
        cfg["agent"]
        .get("env", {})
        .get("CODEX_HOME", str(Path(cfg["agent"]["home"]) / ".codex"))
    )
    reports = []
    for invocation in starts:
        ident = invocation["invocation_id"]
        collector = ResponseUsageCollector(
            telemetry,
            codex_home=home,
            cli_events=agent_dir / "agent_events.jsonl",
            invocation_id=ident,
            model=invocation["requested_model"],
            reasoning_effort=invocation.get("reasoning_effort"),
            role="planner",
        )
        for record in records:
            if (
                record["kind"] == "invocation_thread"
                and record["invocation_id"] == ident
            ):
                collector.bind(
                    record["thread_id"], parent_thread_id=record.get("parent_thread_id")
                )
        collector.poll()
        unclosed = ident not in finished
        if unclosed:
            telemetry.emit(
                "usage_missing",
                event_id="unclosed-invocation:" + ident,
                invocation_id=ident,
                usage=None,
                reason="invocation_end_unobserved_unreported_cost_possible",
            )
        terminal = finished.get(ident, {})
        unknown = (
            unclosed
            or ident in missing_invocations
            or terminal.get("status") != "completed"
            or bool((terminal.get("accounting") or {}).get("unreported_usage_possible"))
            or not collector.records
            or any(
                row.get("usage") is None or row["usage"].get("total_tokens") is None
                for row in collector.records.values()
            )
        )
        reports.append(
            {
                "invocation_id": ident,
                "invocation_end_observed": not unclosed,
                **collector.summary(missing=unknown),
            }
        )
    save(stage / "recovered-accounting.json", {"invocations": reports})


def _execute_stages(source, cfg, root, resume, telemetry):
    stage = root / "planning"
    stage.mkdir(exist_ok=True)
    if cfg.get("initial_plan") is not None:
        _planning_event(
            telemetry,
            "planning_external_seed",
            event_id="planning-external-seed",
            plan_sha256=digest(cfg["initial_plan"]),
            previous_charged_plan_seconds=(cfg.get("planning_continuation") or {}).get(
                "previous_charged_plan_seconds"
            ),
        )
        if telemetry:
            telemetry.emit(
                "usage_missing",
                event_id="planning-external-seed-usage",
                usage=None,
                phase="planning",
                reason="external_plan_seed_prior_usage_not_in_this_ledger",
            )
    workspace = Path(cfg["sandbox"])
    workspace.mkdir(parents=True, exist_ok=True)
    plan_path = workspace / ".vero/plan.json"
    accepted = stage / "accepted.json"
    if not accepted.exists():
        if not (stage / "submitted.json").exists():
            render(source, workspace)
            (workspace / ".vero").mkdir(exist_ok=True)
            if cfg.get("initial_plan") is not None:
                save(plan_path, cfg["initial_plan"])
            elif plan_path.exists():
                raise ValueError("fresh PLAN workspace must not contain a prior plan")
            instruction = workspace / "PLAN_INSTRUCTION.md"
            instruction.write_text(
                PLAN_INSTRUCTION.format(
                    seconds=cfg.get("plan_seconds", 600),
                    benchmark_id=source["benchmark_id"],
                    count=len(source["contracts"]),
                )
            )
            if cfg.get("initial_plan") is not None:
                with instruction.open("a") as stream:
                    stream.write(
                        "\nAn incomplete plan from a previous charged planning call is present at .vero/plan.json. Inspect and continue it; retain useful decisions and finish exact coverage. Previous budget is recorded separately, not reset. Prioritize completing and validating the artifact.\n"
                    )
            if cfg.get("sandbox_uid") is not None:
                for p in [workspace, *workspace.rglob("*")]:
                    if p.is_symlink():
                        raise ValueError("symlink in planner workspace")
                    os.chown(p, cfg["sandbox_uid"], cfg["sandbox_uid"])
            save(
                stage / "submitted.json",
                {
                    "max_seconds": cfg.get("plan_seconds", 600),
                    "submitted_at": time.time(),
                },
            )
            _planning_event(
                telemetry,
                "planning_submitted",
                event_id="planning-submitted",
                timestamp=read(stage / "submitted.json")["submitted_at"],
                reserved_seconds=cfg.get("plan_seconds", 600),
                source_benchmark_id=source["benchmark_id"],
            )
            stop = threading.Event()

            def snapshot():
                previous = None
                while not stop.is_set():
                    try:
                        if (
                            plan_path.is_file()
                            and not plan_path.is_symlink()
                            and plan_path.stat().st_size <= 2_000_000
                        ):
                            content = plan_path.read_text()
                            identity = digest(content)
                            if identity != previous:
                                save(
                                    stage / "snapshots" / f"{time.time_ns()}.json",
                                    {
                                        "at": time.time(),
                                        "sha256": identity,
                                        "content": content,
                                    },
                                )
                                previous = identity
                    except (OSError, UnicodeError):
                        pass
                    stop.wait(2)

            watcher = threading.Thread(target=snapshot, daemon=True)
            watcher.start()
            try:
                result = CodexAgent(
                    {
                        **cfg["agent"],
                        **(
                            {"metrics": cfg["metrics"], "role": "planner"}
                            if telemetry
                            else {}
                        ),
                        "timeout_seconds": cfg.get("plan_seconds", 600),
                    },
                    stage / "agent",
                ).run(sandbox_dir=workspace, instruction_file=instruction)
                save(stage / "agent-result.json", result)
            finally:
                stop.set()
                watcher.join()
        else:
            state = (
                read(stage / "agent/state.json")
                if (stage / "agent/state.json").exists()
                else {}
            )
            if state.get("status") == "running" and state.get("pid"):
                proc = Path("/proc") / str(state["pid"]) / "stat"
                if proc.exists() and proc.read_text().rsplit(")", 1)[1].split()[
                    19
                ] == state.get("process_start_ticks"):
                    raise RuntimeError("PLAN agent still running")
            _planning_event(
                telemetry,
                "planning_submitted",
                event_id="planning-submitted",
                timestamp=read(stage / "submitted.json")["submitted_at"],
                reserved_seconds=read(stage / "submitted.json")["max_seconds"],
                source_benchmark_id=source["benchmark_id"],
            )
            _recover_plan_usage(telemetry, cfg, stage)
            if not (stage / "agent-result.json").exists():
                save(
                    stage / "agent-result.json",
                    state
                    if state.get("status") == "finished"
                    else {
                        "ok": False,
                        "recovered_interruption": True,
                        "elapsed_seconds": read(stage / "submitted.json")[
                            "max_seconds"
                        ],
                        "budget_charge": "full reserved PLAN allowance",
                    },
                )
        try:
            if plan_path.exists() and not plan_path.is_symlink():
                save(stage / "submitted-plan.json", read(plan_path))
            bench = check_plan_file(source, plan_path)
            save(stage / "plan.json", read(plan_path))
            render(bench, stage / "preflight/source")
            if "plan_preflight_seconds" in cfg:
                preflight_seconds = cfg["plan_preflight_seconds"]
                preflight_timeout_source = "plan_preflight_seconds"
            elif cfg["checker"].get("backend") == "a-shared-incremental-v1":
                preflight_seconds = cfg["checker"].get(
                    "initial_statement_timeout_seconds", 1800
                )
                preflight_timeout_source = "checker.initial_statement_timeout_seconds"
            else:
                preflight_seconds = 300
                preflight_timeout_source = "legacy_default"
            checker = TraceChecker(
                Checker(cfg["checker"]),
                _PlanningCheckEvents(
                    telemetry, preflight_seconds, preflight_timeout_source
                )
                if telemetry
                else None,
                stage / "preflight/native-logs",
            )
            try:
                result = checker.check(
                    stage / "preflight/source",
                    "Vero_All",
                    timeout=preflight_seconds,
                )
            finally:
                checker.close()
            save(
                stage / "preflight/result.json",
                {
                    **result,
                    "configured_timeout_seconds": preflight_seconds,
                    "timeout_source": preflight_timeout_source,
                },
            )
            if result["status"] != "ok":
                raise ValueError(
                    "PLAN declaration/import preflight failed; see preflight/result.json"
                )
            save(stage / "benchmark.json", bench)
            save(
                accepted,
                {
                    "plan_sha256": digest(read(plan_path)),
                    "benchmark_id": bench["benchmark_id"],
                    "source_benchmark_id": source["benchmark_id"],
                    "accepted_at": time.time(),
                    "preflight": "ok",
                },
            )
        except (ValueError, OSError, KeyError, TypeError) as exc:
            save(stage / "validation.json", {"accepted": False, "error": str(exc)})
            _planning_event(
                telemetry, "planning_validation_failed", error_type=type(exc).__name__
            )
            raise
    elif resume:
        _recover_plan_usage(telemetry, cfg, stage)
    bench = validate(read(stage / "benchmark.json"))
    if (
        digest(read(stage / "plan.json")) != read(accepted)["plan_sha256"]
        or bench["benchmark_id"] != read(accepted)["benchmark_id"]
    ):
        raise ValueError("accepted plan/benchmark changed")
    acceptance = read(accepted)
    save(stage / "validation.json", {"accepted": True, **acceptance})
    _planning_event(
        telemetry,
        "planning_accepted",
        event_id="planning-accepted:" + acceptance["plan_sha256"],
        timestamp=acceptance["accepted_at"],
        **acceptance,
    )
    # The prover may read but cannot replace the benchmark used by its check tool.
    public = Path(cfg.get("plan_public_benchmark", str(root / "public/benchmark.json")))
    public.parent.mkdir(parents=True, exist_ok=True)
    save(public, bench)
    public.chmod(0o444)
    save(workspace / ".vero/plan.json", read(stage / "plan.json"))
    generation = {
        **cfg,
        "agent": {
            **cfg["agent"],
            **({"role": "prover"} if telemetry else {}),
            "env": {**cfg["agent"].get("env", {}), "VERO_BENCHMARK": str(public)},
        },
    }
    save(root / "generation-config.json", generation)
    _planning_event(
        telemetry,
        "planning_generation_binding",
        event_id="planning-generation-binding",
        plan_sha256=acceptance["plan_sha256"],
        source_benchmark_id=source["benchmark_id"],
        benchmark_id=bench["benchmark_id"],
        config_sha256=digest(generation),
    )
    # The pipeline owns the original immutable manifest and complete lifecycle.
    # The internal runner retains all generation bindings/checks without trying
    # to replace that identity with the plan-derived benchmark/configuration.
    generation_runner = run_with_metrics if telemetry else run
    journal = generation_runner(
        stage / "benchmark.json",
        root / "generation-config.json",
        root / "generation",
        resume=(root / "generation/binding.json").exists(),
        **({"telemetry": telemetry} if telemetry else {}),
    )
    summary = {
        "planning": read(accepted),
        "plan_agent": read(stage / "agent-result.json"),
        "planning_continuation": cfg.get("planning_continuation"),
        "generation": journal,
    }
    save(root / "summary.json", summary)
    return summary
