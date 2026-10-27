"""Apply the persisted global stage deadline to the existing shared checker.

This is an adapter, not a replacement checker or independent proof entry point.
Controller calls remain serialized, and the shared backend re-reads remaining
allowance before cold fallback/build so recovery never gains a fresh day.
"""

from __future__ import annotations

import threading

from .resource_budget import ResourceBudget


class ResourceChecker:
    def __init__(self, checker, budget: ResourceBudget):
        if getattr(checker, "_shared", None) is None:
            raise ValueError("resource-v2 requires the shared native checker")
        self.checker = checker
        self.config = checker.config
        self.budget = budget
        self._lock = threading.RLock()
        checker._shared.resource_remaining_seconds = budget.remaining_seconds

    def check(self, directory, theory, timeout=None):
        with self._lock:
            self.budget.require_work_allowed()
            result = self.checker.check(directory, theory, timeout)
            return self._annotate(result)

    def build_session(
        self, directory, theories, session_name="Generated_Refine", timeout=None
    ):
        with self._lock:
            self.budget.require_work_allowed()
            result = self.checker.build_session(
                directory, theories, session_name, timeout
            )
            return self._annotate(result)

    def _annotate(self, result):
        status = self.budget.status()
        result = {
            **result,
            "resource_stage_id": status["stage_id"],
            "resource_deadline_at": status["deadline_at"],
            "resource_budget_state": status["state"],
        }
        if status["state"] != "active":
            # Native evidence is retained, but post-deadline completion must not
            # be accepted as an in-budget result by the controller.
            result["native_status"] = result.get("status")
            if status["reason"] in {"operator_stop", "operator_cancelled"}:
                result["status"] = "operator_cancelled"
            else:
                result["status"] = "timeout"
                result["timeout_limit"] = "global_resource_stage"
            result["resource_budget_reason"] = status["reason"]
        return result

    def close(self):
        with self._lock:
            self.checker.close()
