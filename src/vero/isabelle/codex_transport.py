#!/usr/bin/env python3
"""Experiment SSE transport; never generates or resubmits proof candidates."""

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

REAL = "/opt/vero/codex"
THREAD = Path("/home/agent/.codex/experiment-thread")
IDLE = 180
RETRIES = 5
TRANSIENT = (
    "stream disconnected",
    "connection",
    "tls handshake",
    "timed out",
    "timeout",
    "model is at capacity",
    "temporarily unavailable",
    "error sending request",
    "failed to send request",
    "502",
    "503",
    "429",
)
OVERRIDES = [
    'model_provider="experiment_sse"',
    'model_providers.experiment_sse.name="Experiment SSE"',
    'model_providers.experiment_sse.base_url="https://chatgpt.com/backend-api/codex"',
    'model_providers.experiment_sse.wire_api="responses"',
    "model_providers.experiment_sse.requires_openai_auth=true",
    "model_providers.experiment_sse.supports_websockets=false",
    "model_providers.experiment_sse.stream_idle_timeout_ms=180000",
    "model_providers.experiment_sse.stream_max_retries=5",
    "model_providers.experiment_sse.request_max_retries=4",
]


def command(args, thread):
    args = list(args)
    # The trusted runner places its bound session immediately after `resume`.
    # Canonicalize it before adding recovery flags, rather than appending a
    # second positional ID from the transport checkpoint.
    if "resume" in args:
        position = args.index("resume") + 1
        if position < len(args) and not args[position].startswith("-"):
            explicit = args.pop(position)
            if thread and explicit != thread:
                raise ValueError(
                    "explicit resume session differs from transport checkpoint"
                )
            thread = explicit
    if thread:
        if "resume" not in args:
            args.insert(args.index("exec") + 1, "resume")
        if "--last" in args:
            args.remove("--last")
        for flag in ("--sandbox", "--add-dir"):
            if flag in args:
                i = args.index(flag)
                del args[i : i + 2]
        if "--dangerously-bypass-approvals-and-sandbox" not in args:
            args.insert(-1, "--dangerously-bypass-approvals-and-sandbox")
        args.insert(-1, thread)
    for override in OVERRIDES:
        args[-1:-1] = ["-c", override]
    return [REAL, *args]


def stop(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def run_once(cmd, prompt, idle=IDLE):
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
        active, thread, transient = set(), None, False
        last_event = time.monotonic()
        buffers = {proc.stdout: b"", proc.stderr: b""}
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ)
            sel.register(proc.stderr, selectors.EVENT_READ)
            while sel.get_map():
                for key, _ in sel.select(timeout=min(1, idle)):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    sink = (
                        sys.stdout.buffer
                        if key.fileobj is proc.stdout
                        else sys.stderr.buffer
                    )
                    sink.write(data)
                    sink.flush()
                    buffers[key.fileobj] += data
                    while b"\n" in buffers[key.fileobj]:
                        line, buffers[key.fileobj] = buffers[key.fileobj].split(
                            b"\n", 1
                        )
                        if key.fileobj is proc.stderr:
                            transient |= any(
                                x in line.decode(errors="replace").lower()
                                for x in TRANSIENT
                            )
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        last_event = time.monotonic()
                        typ = event.get("type")
                        if typ == "thread.started":
                            thread = event["thread_id"]
                        item = event.get("item", {})
                        if typ == "item.started" and item.get("type") in (
                            "command_execution",
                            "mcp_tool_call",
                        ):
                            active.add(item["id"])
                        if typ == "item.completed":
                            active.discard(item.get("id"))
                        if (
                            typ in ("error", "turn.failed")
                            or item.get("type") == "error"
                        ):
                            transient |= any(
                                x in line.decode(errors="replace").lower()
                                for x in TRANSIENT
                            )
                # Native SSE timer measures actual stream inactivity. This outer
                # guard catches a CLI stuck after reporting a transport error;
                # ordinary long reasoning and outstanding tools are exempt.
                if transient and not active and time.monotonic() - last_event >= idle:
                    print(
                        "transport guard: no events after communication error; resuming same thread",
                        file=sys.stderr,
                        flush=True,
                    )
                    stop(proc)
                    return 75, thread, True
            return proc.wait(), thread, transient
    finally:
        stop(proc)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            pipe.close()


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    prompt = sys.stdin.buffer.read()
    thread = THREAD.read_text().strip() if THREAD.exists() else None
    args = sys.argv[1:]
    for attempt in range(RETRIES + 1):
        cmd = command(args, thread)
        print(
            json.dumps(
                {
                    "transport": "sse",
                    "stream_idle_seconds": IDLE,
                    "recovery": attempt,
                    "thread": thread,
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        rc, observed, transient = run_once(cmd, prompt)
        thread = observed or thread
        if thread:
            THREAD.parent.mkdir(parents=True, exist_ok=True)
            temporary = THREAD.with_suffix(".tmp")
            temporary.write_text(thread)
            temporary.replace(THREAD)
        if rc == 0 or not transient or attempt == RETRIES:
            return rc
        if not thread:
            return rc  # No known session: never silently start fresh.
        time.sleep(5)
    return 1


if __name__ == "__main__":
    sys.exit(main())
