"""Passive snapshots: observe generation without modifying or stopping it."""

import threading
import time
from pathlib import Path

from .benchmark import extract, read, save


class PassiveSampler:
    def __init__(self, bench, workspace, output, interval, controller):
        if interval <= 0:
            raise ValueError("sample interval must be positive")
        self.bench, self.workspace = bench, Path(workspace)
        self.output, self.interval = Path(output), interval
        self.controller = controller
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def snapshot(self):
        directory = self.output / str(time.time_ns())
        try:
            candidate = extract(self.bench, self.workspace)
            save(directory / "candidate-artifact.json", candidate)
            # The atomic supervisor journal provides a consistent accepted state
            # without waiting for the controller lock or a long native check.
            state = read(self.controller.path)
            save(
                directory / "accepted-artifact.json",
                self.controller.make_artifact(state["accepted_bodies"]),
            )
            save(
                directory / "progress.json",
                {
                    "at": time.time(),
                    "proved": sum(r["proved"] for r in state["contracts"].values()),
                    "attempts": sum(r["attempts"] for r in state["contracts"].values()),
                    "pending_check": state.get("pending"),
                    "candidate_snapshot_is_authoritative": False,
                },
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            save(
                directory / "sampling-error.json",
                {"at": time.time(), "error": str(exc)},
            )

    def loop(self):
        while not self.stop_event.wait(self.interval):
            self.snapshot()

    def __enter__(self):
        self.snapshot()
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop_event.set()
        self.thread.join()
        self.snapshot()


class RunMonitor:
    """Only operator stop or terminal proof policy can request agent shutdown."""

    def __init__(self, root, controller, terminal_grace=10):
        self.root, self.controller = Path(root), controller
        self.terminal_since = None
        self.terminal_grace = terminal_grace

    def __call__(self):
        state = read(self.controller.path)
        if state.get("pending"):
            return None
        if (self.root / "STOP_REQUEST.json").exists():
            return "operator_stop"
        done = all(
            r["proved"] or r["attempts"] >= self.controller.limit
            for r in state["contracts"].values()
        )
        if done:
            if self.terminal_since is None:
                self.terminal_since = time.monotonic()
            if time.monotonic() - self.terminal_since >= self.terminal_grace:
                return "contract_policy_finished"
        else:
            self.terminal_since = None
        return None


def grade_samples(bench, samples, output, checker):
    """Optional post-hoc native grading; never interrupts a running generator."""
    from .checker import evaluate

    output = Path(output)
    if output.exists():
        raise FileExistsError("sample evaluation requires a fresh directory")
    output.mkdir(parents=True)
    rows = []
    for path in sorted(Path(samples).glob("*/candidate-artifact.json")):
        report = evaluate(bench, read(path), output / path.parent.name, checker)
        rows.append(
            {
                "sample": str(path.parent),
                "proved_specs": report["proved_specs"],
                "complete": report["complete"],
                "report": str(output / path.parent.name / "report.json"),
            }
        )
    save(output / "summary.json", {"samples": rows})
    return {"samples": rows}
