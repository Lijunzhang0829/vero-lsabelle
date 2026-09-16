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
                if p.suffix in {".py", ".thy"}
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
    stage = root / "planning"
    stage.mkdir(exist_ok=True)
    workspace = Path(cfg["sandbox"])
    workspace.mkdir(parents=True, exist_ok=True)
    plan_path = workspace / ".vero/plan.json"
    accepted = stage / "accepted.json"
    if not accepted.exists():
        if not (stage / "submitted.json").exists():
            render(source, workspace)
            (workspace / ".vero").mkdir(exist_ok=True)
            if plan_path.exists():
                raise ValueError("fresh PLAN workspace must not contain a prior plan")
            instruction = workspace / "PLAN_INSTRUCTION.md"
            instruction.write_text(
                PLAN_INSTRUCTION.format(
                    seconds=cfg.get("plan_seconds", 600),
                    benchmark_id=source["benchmark_id"],
                    count=len(source["contracts"]),
                )
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
                    {**cfg["agent"], "timeout_seconds": cfg.get("plan_seconds", 600)},
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
            bench = check_plan_file(source, plan_path)
            save(stage / "plan.json", read(plan_path))
            render(bench, stage / "preflight/source")
            result = Checker(cfg["checker"]).check(
                stage / "preflight/source",
                "Vero_All",
                timeout=cfg.get("plan_preflight_seconds", 300),
            )
            save(stage / "preflight/result.json", result)
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
            raise
    bench = validate(read(stage / "benchmark.json"))
    if (
        digest(read(stage / "plan.json")) != read(accepted)["plan_sha256"]
        or bench["benchmark_id"] != read(accepted)["benchmark_id"]
    ):
        raise ValueError("accepted plan/benchmark changed")
    save(stage / "validation.json", {"accepted": True, **read(accepted)})
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
            "env": {**cfg["agent"].get("env", {}), "VERO_BENCHMARK": str(public)},
        },
    }
    save(root / "generation-config.json", generation)
    journal = run(
        stage / "benchmark.json",
        root / "generation-config.json",
        root / "generation",
        resume=(root / "generation/binding.json").exists(),
    )
    summary = {
        "planning": read(accepted),
        "plan_agent": read(stage / "agent-result.json"),
        "generation": journal,
    }
    save(root / "summary.json", summary)
    return summary
