"""Stream native output with phase deadlines and an optional shared total cap."""

import os
import selectors
import subprocess
import time

from .processes import kill_tree


def communicate_phases(
    process, theory, *, load_seconds, owner_seconds, total_seconds=None, started_at=None
):
    started = time.monotonic() if started_at is None else started_at
    owner_started = None
    phase_deadline = started + load_seconds
    total_deadline = float("inf") if total_seconds is None else started + total_seconds
    deadline = min(phase_deadline, total_deadline)
    chunks = []
    partial = b""
    marker = ("VERO_THEORY_ENTER " + theory).encode()
    timeout_phase = None
    timeout_limit = None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timeout_phase = (
                    "dependency_loading" if owner_started is None else "owner_theory"
                )
                timeout_limit = "total_check" if total_deadline <= phase_deadline else timeout_phase
                kill_tree(process.pid)
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                chunks.append(chunk)
                partial += chunk
                lines = partial.split(b"\n")
                partial = lines.pop()
                if owner_started is None and any(
                    line.strip() == marker for line in lines
                ):
                    owner_started = time.monotonic()
                    phase_deadline = owner_started + owner_seconds
                    deadline = min(phase_deadline, total_deadline)
        # A child may close stdout without exiting. Keep the active deadline.
        if timeout_phase is None:
            try:
                process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timeout_phase = (
                    "dependency_loading" if owner_started is None else "owner_theory"
                )
                timeout_limit = "total_check" if total_deadline <= phase_deadline else timeout_phase
                kill_tree(process.pid)
        remainder, _ = process.communicate()
        if remainder:
            chunks.append(remainder)
    ended = time.monotonic()
    return b"".join(chunks).decode("utf-8", errors="replace"), {
        "timeout_phase": timeout_phase,
        "timeout_limit": timeout_limit,
        "total_timeout_seconds": total_seconds,
        "dependency_loading_seconds": (owner_started or ended) - started,
        "owner_theory_seconds": None
        if owner_started is None
        else ended - owner_started,
        "owner_started": owner_started is not None,
        "dependency_timeout_seconds": load_seconds,
        "owner_timeout_seconds": owner_seconds,
    }
