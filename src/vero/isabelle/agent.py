"""A replaceable Vero-style repository agent, using the existing Codex JSON protocol.

The caller must place this agent in an OS-isolated, sanitized worker. A cwd is
not a security boundary. The supervising process owns logs outside that worker's
writable sandbox. No per-contract planner is introduced here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from .benchmark import save
from .processes import kill_tree
from .response_usage import ResponseUsageCollector
from .telemetry import EventLog


class CodexAgent:
    name = "codex"

    def __init__(self, config, log_dir):
        self.config = dict(config)
        self.log_dir = Path(log_dir)

    def run(self, *, sandbox_dir, instruction_file, monitor=None):
        cfg = self.config
        metrics = EventLog.from_config(cfg)
        mode = cfg.get("sandbox_mode", "workspace-write")
        if mode == "danger-full-access" and not (
            cfg.get("external_isolation") == "docker-nonroot"
            and Path("/.dockerenv").exists()
            and cfg.get("command_prefix") == ["runuser", "-u", "agent", "--"]
        ):
            raise ValueError(
                "external mode requires the dedicated nonroot Docker worker"
            )
        self.log_dir.mkdir(parents=True, exist_ok=False)
        command = cfg.get("command_prefix", []) + [
            cfg["codex"],
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--sandbox",
            mode,
            "--add-dir",
            cfg["home"] + "/.isabelle",
            "-m",
            cfg["model"],
            "-c",
            'model_reasoning_effort="' + cfg.get("reasoning_effort", "medium") + '"',
            "-c",
            'web_search="disabled"',
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "remote_plugin",
            "--disable",
            "hooks",
            "-",
        ]
        if cfg.get("persistent_session") or metrics is not None:
            command.remove("--ephemeral")
        if cfg.get("resume_session"):
            if not cfg.get("persistent_session"):
                raise ValueError("session resume requires persistent_session")
            i = command.index("exec")
            command[i + 1 : i + 1] = ["resume", cfg.get("resume_thread_id") or "--last"]
            # Installed resume CLI does not accept exec's --sandbox/--add-dir.
            for flag in ("--sandbox", "--add-dir"):
                i = command.index(flag)
                del command[i : i + 2]
            command[-1:-1] = (
                ["--dangerously-bypass-approvals-and-sandbox"]
                if mode == "danger-full-access"
                else [
                    "-c",
                    'sandbox_mode="' + mode + '"',
                    "-c",
                    'sandbox_workspace_write.writable_roots=["'
                    + cfg["home"]
                    + '/.isabelle"]',
                ]
            )
        env = os.environ.copy()
        env.update(
            {
                "HOME": cfg["home"],
                "USER": cfg.get("user", "agent"),
                "CODEX_HOME": cfg["home"] + "/.codex",
            }
        )
        env.update(cfg.get("env", {}))
        for key in list(env):
            if key.startswith("CODEX_") and key != "CODEX_HOME":
                env.pop(key)
        save(
            self.log_dir / "invocation.json",
            {
                "command": command,
                "cwd": str(sandbox_dir),
                "persistent_session": bool(cfg.get("persistent_session")),
                "resume_session": bool(cfg.get("resume_session")),
            },
        )
        started = time.time()
        invocation_id = hashlib.sha256(str(self.log_dir.resolve()).encode()).hexdigest()
        collector = None
        prompt = Path(instruction_file).read_text()
        if metrics:
            metrics.emit(
                "invocation_started",
                event_id="invocation-start:" + invocation_id,
                timestamp=started,
                invocation_id=invocation_id,
                requested_model=cfg["model"],
                reasoning_effort=cfg.get("reasoning_effort", "medium"),
                role=cfg.get("role", "repository_agent"),
                agent_id=cfg.get("agent_id", "codex"),
                source=str(self.log_dir),
                instruction_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                accounting_scope=cfg.get("accounting_scope", {}),
                accounting_exclusive_home=bool(cfg.get("accounting_exclusive_home")),
                persistent_session=bool(cfg.get("persistent_session")),
                resume_session=bool(cfg.get("resume_session")),
            )
            collector = ResponseUsageCollector(
                metrics,
                codex_home=env["CODEX_HOME"],
                cli_events=self.log_dir / "agent_events.jsonl",
                accounting_scope=cfg.get("accounting_scope"),
                exclusive_home=cfg.get("accounting_exclusive_home", False),
                invocation_id=invocation_id,
                model=cfg["model"],
                reasoning_effort=cfg.get("reasoning_effort", "medium"),
                role=cfg.get("role", "repository_agent"),
            )
        save(
            self.log_dir / "state.json",
            {
                "status": "running",
                "started_at": started,
                "model": cfg["model"],
                "invocation_id": invocation_id,
            },
        )
        timeout = False
        stop_reason = None
        forced_kill = False
        proc = None
        failure = None
        accounting = None
        try:
            with (
                (self.log_dir / "agent_events.jsonl").open("w") as stdout,
                (self.log_dir / "agent.stderr").open("w") as stderr,
            ):
                registry = getattr(self, "process_registry", None)
                popen = registry.popen if registry is not None else subprocess.Popen
                proc = popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=sandbox_dir,
                    env=env,
                    text=True,
                    start_new_session=True,
                    **({"kind": "model"} if registry is not None else {}),
                )
                save(
                    self.log_dir / "state.json",
                    {
                        "status": "running",
                        "started_at": started,
                        "pid": proc.pid,
                        "process_start_ticks": Path("/proc/" + str(proc.pid) + "/stat")
                        .read_text()
                        .rsplit(")", 1)[1]
                        .split()[19],
                        "max_seconds": cfg["timeout_seconds"],
                        "model": cfg["model"],
                        "invocation_id": invocation_id,
                    },
                )
                while True:
                    try:
                        poll = cfg.get("monitor_poll_seconds", 1)
                        interval = (
                            poll
                            if monitor is not None or collector
                            else cfg["timeout_seconds"]
                        )
                        proc.communicate(prompt, timeout=interval)
                        break
                    except subprocess.TimeoutExpired:
                        prompt = None  # stdin was already delivered on the first call
                        if collector:
                            collector.poll()
                        now = time.time()
                        state = json.loads((self.log_dir / "state.json").read_text())
                        state["last_observed_at"] = now
                        save(self.log_dir / "state.json", state)
                        stop_reason = monitor() if monitor is not None else None
                        timeout = (
                            cfg["timeout_seconds"] is not None
                            and now - started >= cfg["timeout_seconds"]
                        )
                        if not stop_reason and not timeout:
                            continue
                        proc.terminate()
                        try:
                            proc.communicate(
                                timeout=cfg.get("shutdown_grace_seconds", 30)
                            )
                        except subprocess.TimeoutExpired:
                            forced_kill = True
                            kill_tree(proc.pid)
                            proc.wait()
                        break
        except BaseException as error:
            failure = error
            raise
        finally:
            # Interrupted callers must not leave an unaccounted child producing
            # proofs; persisted session records survive SIGKILL for later import.
            if proc is not None and proc.poll() is None:
                forced_kill = True
                kill_tree(proc.pid)
                proc.wait()
            status = (
                "interrupted"
                if failure or timeout or stop_reason
                else "completed"
                if proc is not None and proc.returncode == 0
                else "failed"
            )
            if collector:
                try:
                    accounting = collector.finish(status)
                except Exception as error:
                    metrics.emit(
                        "usage_missing",
                        event_id="collector-failure:" + invocation_id,
                        invocation_id=invocation_id,
                        usage=None,
                        reason="collector_failed",
                        error_type=type(error).__name__,
                    )
                    if failure is None:
                        raise
                finally:
                    metrics.emit(
                        "invocation_finished",
                        event_id="invocation-end:" + invocation_id,
                        invocation_id=invocation_id,
                        status=status,
                        returncode=proc.returncode if proc is not None else None,
                        elapsed_seconds=time.time() - started,
                        timed_out=timeout,
                        stop_reason=stop_reason,
                        forced_kill=forced_kill,
                        error_type=type(failure).__name__ if failure else None,
                        accounting=accounting,
                    )
            if failure:
                save(
                    self.log_dir / "state.json",
                    {
                        "status": "interrupted",
                        "started_at": started,
                        "finished_at": time.time(),
                        "invocation_id": invocation_id,
                        "error_type": type(failure).__name__,
                        "accounting": accounting,
                    },
                )
        usage, events, turns = {}, 0, 0
        for line in (self.log_dir / "agent_events.jsonl").read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events += 1
            if event.get("type") == "turn.completed":
                turns += 1
                for key, value in event.get("usage", {}).items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value
        result = {
            "ok": proc.returncode == 0 and not timeout,
            "returncode": proc.returncode,
            "timed_out": timeout,
            "stop_reason": stop_reason,
            "forced_kill": forced_kill,
            "elapsed_seconds": time.time() - started,
            "usage": usage,
            "num_turns": turns,
            "event_count": events,
            "total_cost_usd": None,
            "model": cfg["model"],
            "reasoning_effort": cfg.get("reasoning_effort", "medium"),
        }
        if accounting is not None:
            # Legacy usage remains a CLI summary for existing consumers. The
            # common event log and response_accounting are authoritative metrics.
            result["response_accounting"] = accounting
            result["invocation_id"] = invocation_id
        save(self.log_dir / "state.json", {"status": "finished", **result})
        return result
