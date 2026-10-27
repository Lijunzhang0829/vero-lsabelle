"""Independent resource guardian survives supervisor SIGKILL; no model access."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .resource_processes import ResourceProcessRegistry


def watch(registry, budget_path, control_dir, *, poll_seconds=0.1):
    control_dir = Path(control_dir)
    control_dir.mkdir(parents=True, exist_ok=True)
    (control_dir / "ready").write_text(str(os.getpid()))
    reason = "unknown"
    try:
        while True:
            if (control_dir / "stop").exists():
                reason = "supervisor_cleanup"
                break
            if not registry.owner_alive():
                reason = "supervisor_disappeared"
                break
            try:
                data = json.loads(Path(budget_path).read_text())
                if data["run_id"] != registry.run_id:
                    raise ValueError("guardian budget identity mismatch")
                stage = data["stages"][-1]
                if stage["state"] != "active" or time.time() >= stage["deadline_at"]:
                    reason = "resource_stage_stopped"
                    break
            except (OSError, ValueError, KeyError, IndexError):
                reason = "budget_unreadable"
                break
            time.sleep(poll_seconds)
    finally:
        outcomes = registry.cleanup(reason)
        (control_dir / "result.json").write_text(
            json.dumps(
                {"reason": reason, "at": time.time(), "outcomes": outcomes}, indent=2
            )
            + "\n"
        )
    return reason


def start_watchdog(registry, budget_path, control_dir):
    registry.bind_budget(budget_path)
    control_dir = Path(control_dir).resolve()
    control_dir.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = package_root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    command = [
        sys.executable,
        "-m",
        "vero.isabelle.resource_watchdog",
        "--registry",
        str(registry.path),
        "--run-id",
        registry.run_id,
        "--owner-key",
        registry.owner_key,
        "--budget",
        str(Path(budget_path).resolve()),
        "--control-dir",
        str(control_dir),
    ]
    with (control_dir / "guardian.log").open("w") as log:
        process = subprocess.Popen(
            command,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    end = time.monotonic() + 10
    while not (control_dir / "ready").exists():
        if process.poll() is not None or time.monotonic() >= end:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("resource guardian did not become ready")
        time.sleep(0.02)
    return process


def stop_watchdog(process, control_dir):
    (Path(control_dir) / "stop").touch()
    process.wait(timeout=15)


def main():
    parser = argparse.ArgumentParser()
    for field in ("registry", "run-id", "owner-key", "budget", "control-dir"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    registry = ResourceProcessRegistry(
        args.registry, args.run_id, owner_key=args.owner_key
    )
    watch(registry, args.budget, args.control_dir)


if __name__ == "__main__":
    main()
