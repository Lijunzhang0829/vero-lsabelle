"""Versioned experiment accounting, independent of proof-state authority.

Immutable event files are authoritative. The append-only JSONL is a convenience
projection: a killed writer may leave its last line incomplete, without losing
the already committed event file. This module never serializes a whole config,
prompt, environment, credential, or model response text.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def timestamp(value=None):
    if value is None:
        return time.time()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _encoded(value):
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def _immutable(path, value):
    """Atomic immutable installation; compare an existing value, never replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _encoded(value)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(name, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("immutable telemetry identity conflict: " + str(path))
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(name)


class EventLog:
    """Shared across agents/checkers; identities are idempotent under a lock."""

    def __init__(self, directory, manifest=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        if manifest is not None:
            _immutable(self.directory / "manifest.json", manifest)
        self.manifest = json.loads((self.directory / "manifest.json").read_text())
        if self.manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported metrics manifest version")
        self.run_id = self.manifest["run_id"]
        (self.directory / "events").mkdir(exist_ok=True)

    @classmethod
    def open(cls, directory):
        return cls(directory)

    @classmethod
    def from_config(cls, config):
        settings = config.get("metrics")
        if not settings:
            return None
        if isinstance(settings, str):
            return cls.open(settings)
        directory = settings["directory"]
        path = Path(directory) / "manifest.json"
        if path.exists():
            result = cls.open(directory)
            if settings.get("run_id", result.run_id) != result.run_id:
                raise ValueError("metrics run identity mismatch")
            for key, value in settings.get("manifest", {}).items():
                if result.manifest.get(key) != value:
                    raise ValueError("metrics manifest binding mismatch: " + key)
            return result
        manifest = {"schema_version": SCHEMA_VERSION, "run_id": settings["run_id"]}
        for key, value in settings.get("manifest", {}).items():
            if key in manifest and manifest[key] != value:
                raise ValueError("metrics manifest identity override")
            manifest[key] = value
        return cls(directory, manifest)

    def _path(self, event_id):
        key = hashlib.sha256(str(event_id).encode()).hexdigest()
        return self.directory / "events" / (key + ".json")

    def get(self, event_id):
        path = self._path(event_id)
        return json.loads(path.read_text()) if path.exists() else None

    def events(self):
        records = [
            json.loads(p.read_text())
            for p in (self.directory / "events").glob("*.json")
        ]
        for row in records:
            if (
                row.get("schema_version") != SCHEMA_VERSION
                or row.get("run_id") != self.run_id
            ):
                raise ValueError("metrics event binding mismatch")
        return sorted(
            records,
            key=lambda row: (row["timestamp"], row["recorded_at"], row["event_id"]),
        )

    def emit(self, kind, *, event_id=None, timestamp=None, **payload):
        reserved = {"schema_version", "run_id", "kind", "event_id", "recorded_at"}
        if reserved.intersection(payload):
            raise ValueError("reserved telemetry event field")
        event_id = event_id or str(uuid.uuid4())
        with (self.directory / ".events.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            old = self.get(event_id)
            if old:
                expected = {"kind": kind, **payload}
                actual = {
                    k: v for k, v in old.items() if k not in reserved | {"timestamp"}
                }
                actual["kind"] = old["kind"]
                if actual != expected or (
                    timestamp is not None
                    and old["timestamp"] != globals()["timestamp"](timestamp)
                ):
                    raise ValueError("conflicting telemetry event: " + str(event_id))
                return old
            row = {
                "schema_version": SCHEMA_VERSION,
                "run_id": self.run_id,
                "event_id": event_id,
                "kind": kind,
                "timestamp": globals()["timestamp"](timestamp),
                "recorded_at": time.time(),
                **payload,
            }
            _immutable(self._path(event_id), row)
            # A prior killed append can have an incomplete tail. A separator
            # preserves all existing bytes; readers use immutable event files.
            with (self.directory / "events.jsonl").open("ab+") as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell():
                    stream.seek(-1, os.SEEK_END)
                    if stream.read(1) != b"\n":
                        stream.write(b"\n")
                stream.write(_encoded(row))
                stream.flush()
                os.fsync(stream.fileno())
            return row

    @contextmanager
    def segment(self, *, segment_id=None, **fields):
        segment_id = segment_id or str(uuid.uuid4())
        self.emit(
            "lifecycle",
            event_id="segment-start:" + segment_id,
            action="start",
            segment_id=segment_id,
            **fields,
        )
        try:
            yield segment_id
        except BaseException as error:
            self.emit(
                "lifecycle",
                event_id="segment-end:" + segment_id,
                action="pause",
                segment_id=segment_id,
                reason=type(error).__name__,
                **fields,
            )
            raise
        else:
            self.emit(
                "lifecycle",
                event_id="segment-end:" + segment_id,
                action="stop",
                segment_id=segment_id,
                **fields,
            )


def initialize_run(directory, config, benchmark, method, implementation):
    """Freeze safe identities; caller supplies one explicit metrics directory."""
    settings = config.get("metrics", {})
    run_id = settings.get("run_id", config.get("run_id"))
    if not run_id:
        raise ValueError("metrics requires an explicit run_id")
    agent = config.get("agent", {})
    checker = config.get("checker", {})
    checker_identity = {
        key: value
        for key, value in checker.items()
        if key
        in {
            "backend",
            "shared_core_sha256",
            "source_qualifier",
            "session",
            "threads",
            "environment_identity",
            "build_extra_sessions",
            "incremental",
        }
        or "timeout" in key
    }
    checker_identity["env"] = {
        key: checker.get("env", {})[key]
        for key in ("L4V_ARCH", "ISABELLE_IDENTIFIER")
        if key in checker.get("env", {})
    }
    contracts = benchmark.get("contracts", [])
    ids = [c["id"] if isinstance(c, dict) else c for c in contracts]
    identity = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "method": method,
        "benchmark_id": benchmark.get("benchmark_id"),
        "contract_ids": ids,
        "model": agent.get("model"),
        "reasoning_effort": agent.get("reasoning_effort"),
        "implementation": implementation,
        "checker": checker_identity,
        "config_sha256": hashlib.sha256(_encoded(config)).hexdigest(),
        "token_accounting": "individual_reported_response_usage",
        "acceptance": "committed_original_contracts",
        **settings.get("manifest", {}),
    }
    return EventLog(directory, identity)


def normalize_usage(value):
    """Normalize a single response, never a cumulative token_count snapshot."""
    if not isinstance(value, dict):
        return None
    details = value.get("input_tokens_details") or {}
    output = value.get("output_tokens_details") or {}
    result = {
        "input_tokens": value.get("input_tokens"),
        "cached_input_tokens": value.get(
            "cached_input_tokens", details.get("cached_tokens")
        ),
        "cache_write_input_tokens": value.get(
            "cache_write_input_tokens", details.get("cache_write_tokens")
        ),
        "output_tokens": value.get("output_tokens"),
        "reasoning_output_tokens": value.get(
            "reasoning_output_tokens", output.get("reasoning_tokens")
        ),
    }
    for name, amount in result.items():
        if amount is not None and (
            not isinstance(amount, int) or isinstance(amount, bool) or amount < 0
        ):
            raise ValueError("invalid token usage: " + name)
    if result["input_tokens"] is not None and result["output_tokens"] is not None:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    else:
        result["total_tokens"] = None
    return result
