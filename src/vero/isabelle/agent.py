"""A replaceable Vero-style repository agent, using the existing Codex JSON protocol.

The caller must place this agent in an OS-isolated, sanitized worker. A cwd is
not a security boundary. The supervising process owns logs outside that worker's
writable sandbox. No per-contract planner is introduced here.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .benchmark import save
from .processes import kill_tree


class CodexAgent:
    name = "codex"

    def __init__(self, config, log_dir):
        self.config = dict(config)
        self.log_dir = Path(log_dir)

    def run(self, *, sandbox_dir, instruction_file):
        cfg = self.config
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
        started = time.time()
        save(
            self.log_dir / "state.json",
            {"status": "running", "started_at": started, "model": cfg["model"]},
        )
        timeout = False
        with (
            (self.log_dir / "agent_events.jsonl").open("w") as stdout,
            (self.log_dir / "agent.stderr").open("w") as stderr,
        ):
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                cwd=sandbox_dir,
                env=env,
                text=True,
                start_new_session=True,
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
                },
            )
            try:
                proc.communicate(
                    Path(instruction_file).read_text(), timeout=cfg["timeout_seconds"]
                )
            except subprocess.TimeoutExpired:
                timeout = True
                kill_tree(proc.pid)
                proc.wait()
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
            "elapsed_seconds": time.time() - started,
            "usage": usage,
            "num_turns": turns,
            "event_count": events,
            "total_cost_usd": None,
            "model": cfg["model"],
            "reasoning_effort": cfg.get("reasoning_effort", "medium"),
        }
        save(self.log_dir / "state.json", {"status": "finished", **result})
        return result
