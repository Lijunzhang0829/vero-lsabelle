#!/usr/bin/env python3
"""Append-only run recording for the shared baseline harness.

Preliminary incremental recording: API usage and progress events are appended to
agent-writable JSONL files in the task workspace.  This is observability only;
it never certifies a proof and never changes checker authority.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _workspace() -> Path:
    return Path(os.environ.get("VERO_TASK_WORKSPACE", Path.cwd()))


def api_usage_path() -> Path:
    return Path(os.environ.get("VERO_API_USAGE", _workspace() / ".vero-api-usage.jsonl"))


def progress_events_path() -> Path:
    return Path(os.environ.get("VERO_PROGRESS_EVENTS", _workspace() / ".vero-progress-events.jsonl"))


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def record_api_usage(action: str, **fields: Any) -> None:
    _append(api_usage_path(), {"at": time.time(), "action": action, **fields})


def record_progress_event(event: str, **fields: Any) -> None:
    _append(progress_events_path(), {"at": time.time(), "event": event, **fields})
