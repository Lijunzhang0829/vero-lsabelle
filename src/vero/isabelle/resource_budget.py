"""Durable run-wide resource stages; callers must cancel work at remaining_seconds().

This ledger is controller-owned, never agent writable. It has no target-attempt
counter. Explicit continuation preserves run identity, costs and prior stages.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


class ResourceBudgetError(RuntimeError):
    pass


class ResourceBudgetStopped(ResourceBudgetError):
    pass


class ResourceBudget:
    def __init__(
        self, path: str | Path, run_id: str, *, clock: Callable[[], float] = time.time
    ):
        if not run_id:
            raise ValueError("run_id is required")
        self.path = Path(path)
        self.run_id = run_id
        self.clock = clock

    @contextmanager
    def _transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(self.path.suffix + ".lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.path.exists():
                data = json.loads(self.path.read_text())
                if data.get("run_id") != self.run_id or data.get("schema_version") != 1:
                    raise ResourceBudgetError("budget ledger identity/schema mismatch")
            else:
                data = {}
            before = copy.deepcopy(data)
            yield data
            if data != before:
                fd, name = tempfile.mkstemp(
                    prefix=self.path.name + ".", dir=self.path.parent
                )
                try:
                    with os.fdopen(fd, "w") as stream:
                        json.dump(data, stream, sort_keys=True, allow_nan=False)
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, self.path)
                    directory = os.open(self.path.parent, os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)

    def _now(self, data):
        now = float(self.clock())
        if not math.isfinite(now):
            raise ResourceBudgetError("invalid clock")
        # Backward clock adjustments cannot grant additional allowance.
        now = max(now, data.get("last_observed_at", now))
        data["last_observed_at"] = now
        return now

    @staticmethod
    def _duration(value):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("wall_clock_seconds must be positive and finite")
        return float(value)

    @staticmethod
    def _event(data, kind, now, **fields):
        data["events"].append(
            {"sequence": len(data["events"]) + 1, "kind": kind, "at": now, **fields}
        )

    @staticmethod
    def _request(data, request_id, payload):
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id is required")
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        previous = data.setdefault("requests", {}).get(request_id)
        if previous is not None:
            if previous != digest:
                raise ResourceBudgetError("request_id reused with different parameters")
            return False
        data["requests"][request_id] = digest
        return True

    @staticmethod
    def _require_initialized(data):
        if not data.get("stages"):
            raise ResourceBudgetError("budget not initialized")

    def _refresh(self, data, now):
        self._require_initialized(data)
        stage = data["stages"][-1]
        if stage["state"] == "active" and now >= stage["deadline_at"]:
            stage.update(
                state="paused", ended_at=stage["deadline_at"], reason="wall_clock_limit"
            )
            self._event(
                data,
                "stage_paused",
                now,
                stage_id=stage["stage_id"],
                reason="wall_clock_limit",
                effective_at=stage["deadline_at"],
            )
        limit = data["token_limit"]
        if stage["state"] == "active" and limit is not None:
            total = sum(v for v in data["responses"].values() if v is not None)
            if total >= limit or any(v is None for v in data["responses"].values()):
                reason = "unknown_usage" if total < limit else "token_limit"
                stage.update(state="paused", ended_at=now, reason=reason)
                self._event(
                    data, "stage_paused", now, stage_id=stage["stage_id"], reason=reason
                )

    def _snapshot(self, data, now):
        stage = data["stages"][-1]
        unknown = sum(v is None for v in data["responses"].values())
        return {
            "run_id": self.run_id,
            "stage_id": stage["stage_id"],
            "state": stage["state"],
            "reason": stage.get("reason"),
            "deadline_at": stage["deadline_at"],
            "remaining_seconds": max(0.0, stage["deadline_at"] - now)
            if stage["state"] == "active"
            else 0.0,
            "reported_tokens": sum(
                v for v in data["responses"].values() if v is not None
            ),
            "unknown_usage_responses": unknown,
            "usage_complete": unknown == 0,
            "token_limit": data["token_limit"],
            "token_limit_mode": data["token_limit_mode"],
            "stage_count": len(data["stages"]),
            "cumulative_stage_wall_seconds": sum(
                max(
                    0.0, min(now, s.get("ended_at", s["deadline_at"])) - s["started_at"]
                )
                for s in data["stages"]
            ),
            "elapsed_since_run_start_seconds": max(
                0.0, now - data["stages"][0]["started_at"]
            ),
        }

    def initialize(
        self,
        *,
        wall_clock_seconds=86400,
        token_limit=None,
        token_limit_mode="unlimited",
        request_id,
    ):
        duration = self._duration(wall_clock_seconds)
        if token_limit_mode not in ("unlimited", "finite"):
            raise ValueError("token_limit_mode must explicitly be unlimited or finite")
        if token_limit_mode == "unlimited" and token_limit is not None:
            raise ValueError("unlimited token policy requires null limit")
        if token_limit_mode == "finite" and (
            isinstance(token_limit, bool)
            or not isinstance(token_limit, int)
            or token_limit <= 0
        ):
            raise ValueError("finite token policy requires positive integer limit")
        payload = {
            "operation": "initialize",
            "duration": duration,
            "token_limit": token_limit,
            "token_limit_mode": token_limit_mode,
        }
        with self._transaction() as data:
            if data:
                if request_id not in data.get("requests", {}):
                    raise ResourceBudgetError(
                        "existing run must use explicit continuation"
                    )
                self._request(data, request_id, payload)
            else:
                data.update(
                    schema_version=1,
                    run_id=self.run_id,
                    events=[],
                    requests={},
                    responses={},
                    stages=[],
                    token_limit=token_limit,
                    token_limit_mode=token_limit_mode,
                )
                self._request(data, request_id, payload)
                now = self._now(data)
                self._new_stage(data, now, duration, "initial", request_id)
            now = self._now(data)
            self._refresh(data, now)
            return self._snapshot(data, now)

    def _new_stage(self, data, now, duration, authorization_id, request_id):
        stage_id = len(data["stages"]) + 1
        data["stages"].append(
            {
                "stage_id": stage_id,
                "started_at": now,
                "deadline_at": now + duration,
                "wall_clock_seconds": duration,
                "state": "active",
                "authorization_id": authorization_id,
                "request_id": request_id,
            }
        )
        self._event(
            data,
            "stage_started",
            now,
            stage_id=stage_id,
            authorization_id=authorization_id,
            request_id=request_id,
        )

    def status(self):
        with self._transaction() as data:
            now = self._now(data)
            self._refresh(data, now)
            return self._snapshot(data, now)

    def remaining_seconds(self):
        return self.status()["remaining_seconds"]

    def require_work_allowed(self):
        status = self.status()
        if status["state"] != "active":
            raise ResourceBudgetStopped(str(status["reason"]))
        return status

    def pause(self, *, reason="user_pause", request_id):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("pause reason required")
        with self._transaction() as data:
            now = self._now(data)
            self._refresh(data, now)
            if self._request(
                data, request_id, {"operation": "pause", "reason": reason}
            ):
                stage = data["stages"][-1]
                if stage["state"] == "active":
                    stage.update(state="paused", ended_at=now, reason=reason)
                    self._event(
                        data,
                        "stage_paused",
                        now,
                        stage_id=stage["stage_id"],
                        reason=reason,
                        request_id=request_id,
                    )
            return self._snapshot(data, now)

    def continue_stage(self, *, wall_clock_seconds, authorization_id, request_id):
        duration = self._duration(wall_clock_seconds)
        if not isinstance(authorization_id, str) or not authorization_id.strip():
            raise ValueError("explicit continuation authorization required")
        with self._transaction() as data:
            now = self._now(data)
            self._refresh(data, now)
            payload = {
                "operation": "continue",
                "duration": duration,
                "authorization_id": authorization_id,
            }
            if self._request(data, request_id, payload):
                if data["stages"][-1]["state"] == "active":
                    raise ResourceBudgetError("cannot extend an active stage")
                if any(
                    s["authorization_id"] == authorization_id for s in data["stages"]
                ):
                    raise ResourceBudgetError(
                        "continuation authorization already consumed"
                    )
                self._new_stage(data, now, duration, authorization_id, request_id)
                self._refresh(data, now)
            return self._snapshot(data, now)

    def record_usage(self, response_id: str, reported_tokens: int | None):
        """Record late/inflight usage even while paused; reconcile unknown once."""
        if not isinstance(response_id, str) or not response_id.strip():
            raise ValueError("response_id required")
        if reported_tokens is not None and (
            isinstance(reported_tokens, bool)
            or not isinstance(reported_tokens, int)
            or reported_tokens < 0
        ):
            raise ValueError("reported_tokens must be null or a nonnegative integer")
        with self._transaction() as data:
            now = self._now(data)
            self._refresh(data, now)
            responses = data["responses"]
            if response_id in responses and responses[response_id] is not None:
                if (
                    reported_tokens is not None
                    and responses[response_id] != reported_tokens
                ):
                    raise ResourceBudgetError("conflicting response usage")
            elif response_id not in responses or reported_tokens is not None:
                responses[response_id] = reported_tokens
                self._event(
                    data,
                    "response_usage",
                    now,
                    response_id=response_id,
                    reported_tokens=reported_tokens,
                )
            self._refresh(data, now)
            return self._snapshot(data, now)
