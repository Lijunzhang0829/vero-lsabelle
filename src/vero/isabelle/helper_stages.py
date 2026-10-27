"""Pure, durable accounting for agent-planned proof dependencies.

The native checker supplies canonical statement/context fingerprints and GREEN outcomes. This module does not elaborate propositions, decide whether a helper is mathematically useful, or perform I/O. Persist ``state`` atomically with the controller ledger before invoking the checker. A separate schema prevents accidental migration or reset of historical candidate-v2 experiments.
"""

import json
from copy import deepcopy


class StageError(ValueError):
    """A requested graph or budget transition violates the experiment policy."""


class HelperStages:
    """Six candidates per stage; one new stage per completed new-helper batch.

    Idempotent request and batch IDs are mandatory. Repeated ``finish`` calls return the same durable credit events; a controller must apply each event ID once, in the same transaction as persisting this state. Every native check invocation must either reserve a new candidate or be an explicitly bounded recovery of the identical pending candidate handled by the controller.
    """

    SCHEMA = "helper-stages-v1"

    def __init__(self, state=None, *, max_attempts=6):
        if max_attempts != 6:
            raise StageError("this experiment requires six candidates per stage")
        if state is None:
            state = {
                "schema": self.SCHEMA,
                "max_attempts": max_attempts,
                "nodes": {},
                "batches": {},
                "requests": {},
                "events": [],
            }
        if state.get("schema") != self.SCHEMA:
            raise StageError(
                "not a helper-stages ledger; historical state is immutable"
            )
        if state.get("max_attempts") != max_attempts:
            raise StageError("stage budget changed")
        for key, kind in (
            ("nodes", dict),
            ("batches", dict),
            ("requests", dict),
            ("events", list),
        ):
            if not isinstance(state.get(key), kind):
                raise StageError(f"invalid ledger field: {key}")
        self.state = state

    @staticmethod
    def _text(value, name):
        if not isinstance(value, str) or not value.strip():
            raise StageError(f"{name} must be a nonempty string")
        return value

    def get(self, node_id):
        try:
            return self.state["nodes"][node_id]
        except KeyError as exc:
            raise StageError(f"unknown node: {node_id}") from exc

    def _event(self, kind, **payload):
        event = {
            "event_id": f"event-{len(self.state['events']) + 1:08d}",
            "kind": kind,
            **payload,
        }
        self.state["events"].append(event)
        return event

    def _node(self, node_id, statement_fingerprint, context_fingerprint, kind):
        return {
            "node_id": self._text(node_id, "node ID"),
            "kind": kind,
            "statement_fingerprint": self._text(
                statement_fingerprint, "statement fingerprint"
            ),
            "context_fingerprint": self._text(
                context_fingerprint, "context fingerprint"
            ),
            "stage": 1,
            "stage_attempts": 0,
            "cumulative_attempts": 0,
            "green": False,
            "green_evidence": None,
            "dependencies": [],
            "requests": [],
            "pending_request": None,
            "origin_batch": None,
        }

    def register_contract(self, node_id, statement_fingerprint, context_fingerprint):
        node = self._node(
            node_id, statement_fingerprint, context_fingerprint, "contract"
        )
        existing = self.state["nodes"].get(node_id)
        if existing is not None:
            if any(
                existing[key] != node[key]
                for key in ("kind", "statement_fingerprint", "context_fingerprint")
            ):
                raise StageError("registered contract identity changed")
            return existing
        self.state["nodes"][node_id] = node
        self._event("contract_registered", node_id=node_id)
        return node

    def _would_cycle(self, parent_id, dependency_id):
        pending, seen = [dependency_id], set()
        while pending:
            current = pending.pop()
            if current == parent_id:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(self.get(current)["dependencies"])
        return False

    def _mutable_parent(self, node_id):
        node = self.get(node_id)
        if node["green"]:
            raise StageError("a GREEN node cannot acquire new dependencies")
        if node["pending_request"]:
            raise StageError(
                "finish the pending candidate before changing dependencies"
            )
        return node

    def add_dependencies(self, parent_id, dependency_ids):
        """Attach existing nodes without generating any stage credit."""
        parent = self._mutable_parent(parent_id)
        dependency_ids = list(dict.fromkeys(dependency_ids))
        for dependency_id in dependency_ids:
            self.get(dependency_id)
            if self._would_cycle(parent_id, dependency_id):
                raise StageError("proof dependencies must form a DAG")
        added = [
            node_id
            for node_id in dependency_ids
            if node_id not in parent["dependencies"]
        ]
        if added:
            parent["dependencies"].extend(added)
            self._event(
                "dependencies_registered", parent_id=parent_id, dependencies=added
            )
        return added

    def register_batch(self, parent_id, batch_id, helpers, *, rationale):
        """Register and attach a nonempty batch of genuinely new helper goals.

        Canonical statement fingerprints are name independent. Reusing an existing goal, even with a new helper ID or a different context, cannot buy another credit. Existing helpers may instead be attached through ``add_dependencies``. Mathematical relevance remains a recorded agent claim, not something this accounting layer can establish.
        """
        self._text(batch_id, "batch ID")
        self._text(rationale, "helper rationale")
        specs = [
            {
                "node_id": spec["node_id"],
                "statement_fingerprint": spec["statement_fingerprint"],
                "context_fingerprint": spec["context_fingerprint"],
            }
            for spec in helpers
        ]
        identity = {"parent_id": parent_id, "helpers": specs, "rationale": rationale}
        existing = self.state["batches"].get(batch_id)
        if existing is not None:
            if existing["identity"] != identity:
                raise StageError("batch ID was reused with different contents")
            return [self.get(spec["node_id"]) for spec in specs]
        parent = self._mutable_parent(parent_id)
        if not specs:
            raise StageError("an empty helper batch cannot grant a stage")
        if any(
            batch["parent_id"] == parent_id and batch["status"] == "pending"
            for batch in self.state["batches"].values()
        ):
            raise StageError(
                "complete or invalidate the existing required helper batch first"
            )
        seen_ids = set(self.state["nodes"])
        seen_goals = {
            node["statement_fingerprint"] for node in self.state["nodes"].values()
        }
        nodes = []
        for spec in specs:
            node = self._node(**spec, kind="helper")
            if node["node_id"] in seen_ids:
                raise StageError("stage-credit helpers must be newly registered")
            if node["statement_fingerprint"] in seen_goals:
                raise StageError(
                    "duplicate or renamed helper goal cannot grant stage credit"
                )
            seen_ids.add(node["node_id"])
            seen_goals.add(node["statement_fingerprint"])
            node["origin_batch"] = batch_id
            nodes.append(node)
        batch = {
            "batch_id": batch_id,
            "identity": identity,
            "parent_id": parent_id,
            "parent_context": parent["context_fingerprint"],
            "helper_contexts": {
                node["node_id"]: node["context_fingerprint"] for node in nodes
            },
            "status": "pending",
            "credit_event": None,
        }
        self.state["batches"][batch_id] = batch
        for node in nodes:
            self.state["nodes"][node["node_id"]] = node
            parent["dependencies"].append(node["node_id"])
        self._event(
            "helper_batch_registered",
            batch_id=batch_id,
            parent_id=parent_id,
            helpers=list(batch["helper_contexts"]),
        )
        return nodes

    def blockers(self, node_id):
        return [
            dependency
            for dependency in self.get(node_id)["dependencies"]
            if not self.get(dependency)["green"]
        ]

    def status(self, node_id):
        node = self.get(node_id)
        if node["green"]:
            return "green"
        if node["pending_request"]:
            return "checking"
        if self.blockers(node_id):
            return "blocked"
        if node["stage_attempts"] >= self.state["max_attempts"]:
            return "exhausted"
        return "ready"

    def can_check(self, node_id):
        return self.status(node_id) == "ready"

    def reserve(self, node_id, request_id, candidate_fingerprint, context_fingerprint):
        """Charge once before running native verification; interruption retains the charge."""
        self._text(request_id, "request ID")
        self._text(candidate_fingerprint, "candidate fingerprint")
        identity = {
            "node_id": node_id,
            "candidate_fingerprint": candidate_fingerprint,
            "context_fingerprint": context_fingerprint,
        }
        existing = self.state["requests"].get(request_id)
        if existing is not None:
            if existing["identity"] != identity:
                raise StageError(
                    "request ID was reused with a different candidate or context"
                )
            return existing
        node = self.get(node_id)
        if context_fingerprint != node["context_fingerprint"]:
            raise StageError("candidate context does not match the registered context")
        if not self.can_check(node_id):
            raise StageError(f"node is not checkable: {self.status(node_id)}")
        node["stage_attempts"] += 1
        node["cumulative_attempts"] += 1
        node["pending_request"] = request_id
        node["requests"].append(request_id)
        record = {
            "request_id": request_id,
            "identity": identity,
            "stage": node["stage"],
            "attempt": node["stage_attempts"],
            "cumulative_attempt": node["cumulative_attempts"],
            "outcome": None,
            "evidence": None,
            "result_identity": None,
            "credits": [],
            "invalidated": False,
            "dependency_snapshot": deepcopy(node["dependencies"]),
            "recoveries": [],
        }
        self.state["requests"][request_id] = record
        self._event(
            "candidate_reserved",
            request_id=request_id,
            node_id=node_id,
            stage=record["stage"],
            attempt=record["attempt"],
        )
        return record

    def _grant_ready_batches(self):
        credits = []
        for batch in self.state["batches"].values():
            if batch["status"] != "pending":
                continue
            parent = self.get(batch["parent_id"])
            if parent["context_fingerprint"] != batch["parent_context"]:
                continue
            if not all(
                self.get(helper)["green"]
                and self.get(helper)["context_fingerprint"] == context
                for helper, context in batch["helper_contexts"].items()
            ):
                continue
            previous_attempts = parent["stage_attempts"]
            parent["stage"] += 1
            parent["stage_attempts"] = 0
            credit = self._event(
                "stage_credit",
                batch_id=batch["batch_id"],
                parent_id=batch["parent_id"],
                stage=parent["stage"],
                previous_attempts=previous_attempts,
                cumulative_attempts=parent["cumulative_attempts"],
            )
            batch["status"] = "credited"
            batch["credit_event"] = credit["event_id"]
            credits.append(credit)
        return credits

    def finish(self, request_id, outcome, *, evidence=None, context_fingerprint=None):
        """Record an externally verified outcome and return stable credit events.

        Only ``green`` or ``proved`` denotes successful native certification. Every other outcome, including timeout, syntax errors and resource errors, consumes the already reserved slot. An invalidated candidate cannot become GREEN even if its old check later succeeds.
        """
        self._text(outcome, "outcome")
        try:
            evidence = json.loads(json.dumps(evidence, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise StageError("evidence must be JSON-compatible") from exc
        try:
            record = self.state["requests"][request_id]
        except KeyError as exc:
            raise StageError("an unreserved result cannot change the ledger") from exc
        if context_fingerprint is None:
            context_fingerprint = record["identity"]["context_fingerprint"]
        identity = {
            "outcome": outcome,
            "evidence": deepcopy(evidence),
            "context_fingerprint": context_fingerprint,
        }
        if record["result_identity"] is not None:
            if record["result_identity"] != identity:
                raise StageError("request already has a different completed result")
            return deepcopy(record["credits"])
        node = self.get(record["identity"]["node_id"])
        valid = (
            not record["invalidated"]
            and context_fingerprint
            == record["identity"]["context_fingerprint"]
            == node["context_fingerprint"]
            and not self.blockers(node["node_id"])
        )
        record["result_identity"] = identity
        record["outcome"] = outcome if valid else "stale_context"
        record["evidence"] = deepcopy(evidence)
        if node["pending_request"] == request_id:
            node["pending_request"] = None
        if valid and outcome in {"green", "proved"}:
            node["green"] = True
            node["green_evidence"] = {
                "request_id": request_id,
                "context_fingerprint": context_fingerprint,
                "evidence": deepcopy(evidence),
            }
        self._event(
            "candidate_finished",
            request_id=request_id,
            node_id=node["node_id"],
            outcome=record["outcome"],
        )
        record["credits"] = deepcopy(self._grant_ready_batches())
        return deepcopy(record["credits"])

    def recover_result(
        self, request_id, outcome, *, evidence=None, context_fingerprint=None
    ):
        """Record at most two recoveries of the identical infrastructure-failed candidate.

        Only an effective ``infrastructure_error`` outcome is recoverable. The controller must enforce the same candidate source and accepted-context snapshot before invoking native recovery; this layer also requires the original stage, dependencies and context, and prohibits recovery after a newer candidate. Owner deadlines, total deadlines, resource failures and ordinary proof errors must not be relabeled as infrastructure errors.

        Exact replay of the latest recovery result is idempotent, including its credit events. The initial completion remains in ``initial_result`` and ``result_identity``; ``recoveries`` holds every recovery, while ``outcome`` and ``evidence`` reflect the effective latest result. No candidate counters are increased or refunded.
        """
        self._text(outcome, "outcome")
        try:
            evidence = json.loads(json.dumps(evidence, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise StageError("evidence must be JSON-compatible") from exc
        try:
            record = self.state["requests"][request_id]
        except KeyError as exc:
            raise StageError("an unreserved result cannot be recovered") from exc
        if context_fingerprint is None:
            context_fingerprint = record["identity"]["context_fingerprint"]
        identity = {
            "outcome": outcome,
            "evidence": deepcopy(evidence),
            "context_fingerprint": context_fingerprint,
        }
        recoveries = record.get("recoveries", [])
        if recoveries and recoveries[-1]["result_identity"] == identity:
            return deepcopy(recoveries[-1]["credits"])
        if record["outcome"] != "infrastructure_error":
            raise StageError("only infrastructure_error candidates are recoverable")
        if len(recoveries) >= 2:
            raise StageError("infrastructure recovery limit reached")
        node = self.get(record["identity"]["node_id"])
        if (
            not node["requests"]
            or node["requests"][-1] != request_id
            or node["pending_request"]
        ):
            raise StageError("a newer or pending candidate prevents recovery")
        if record["stage"] != node["stage"]:
            raise StageError("candidate stage changed before recovery")
        if (
            record["invalidated"]
            or record["identity"]["context_fingerprint"] != node["context_fingerprint"]
        ):
            raise StageError("candidate context was invalidated before recovery")
        if record.get("dependency_snapshot", []) != node[
            "dependencies"
        ] or self.blockers(node["node_id"]):
            raise StageError("candidate dependencies changed before recovery")
        valid = context_fingerprint == record["identity"]["context_fingerprint"]
        if "initial_result" not in record:
            record["initial_result"] = {
                "outcome": record["outcome"],
                "evidence": deepcopy(record["evidence"]),
                "result_identity": deepcopy(record["result_identity"]),
                "credits": deepcopy(record["credits"]),
            }
        recovery = {
            "recovery": len(recoveries) + 1,
            "result_identity": identity,
            "outcome": outcome if valid else "stale_context",
            "evidence": deepcopy(evidence),
            "credits": [],
        }
        record["recoveries"] = recoveries
        recoveries.append(recovery)
        record["outcome"] = recovery["outcome"]
        record["evidence"] = deepcopy(evidence)
        if valid and outcome in {"green", "proved"}:
            node["green"] = True
            node["green_evidence"] = {
                "request_id": request_id,
                "recovery": recovery["recovery"],
                "context_fingerprint": context_fingerprint,
                "evidence": deepcopy(evidence),
            }
        self._event(
            "candidate_recovered",
            request_id=request_id,
            node_id=node["node_id"],
            recovery=recovery["recovery"],
            outcome=recovery["outcome"],
        )
        recovery["credits"] = deepcopy(self._grant_ready_batches())
        return deepcopy(recovery["credits"])

    def record_result(
        self,
        node_id,
        request_id,
        outcome,
        *,
        context_fingerprint,
        candidate_fingerprint,
        evidence=None,
    ):
        """Replay/import an already charged controller event; not a pre-check reservation substitute."""
        self.reserve(node_id, request_id, candidate_fingerprint, context_fingerprint)
        return self.finish(
            request_id,
            outcome,
            evidence=evidence,
            context_fingerprint=context_fingerprint,
        )

    def invalidate_context(self, node_id, context_fingerprint):
        """Invalidate dependent certificates without erasing attempts or granting new credit.

        Pending helper batches bound to an invalidated node are permanently invalidated. Restoring the old context is not an opportunity to earn the same helper credit again. Credited batch history remains immutable; invalidation never refunds candidate costs.
        """
        self._text(context_fingerprint, "context fingerprint")
        node = self.get(node_id)
        if node["context_fingerprint"] == context_fingerprint:
            return []
        node["context_fingerprint"] = context_fingerprint
        affected = {node_id}
        changed = True
        while changed:
            changed = False
            for current_id, current in self.state["nodes"].items():
                if current_id not in affected and affected.intersection(
                    current["dependencies"]
                ):
                    affected.add(current_id)
                    changed = True
        for current_id in sorted(affected):
            current = self.get(current_id)
            current["green"] = False
            current["green_evidence"] = None
            if current["requests"]:
                self.state["requests"][current["requests"][-1]]["invalidated"] = True
        for batch in self.state["batches"].values():
            if batch["status"] == "pending" and (
                batch["parent_id"] in affected
                or affected.intersection(batch["helper_contexts"])
            ):
                batch["status"] = "invalidated"
        self._event(
            "context_invalidated",
            node_id=node_id,
            context_fingerprint=context_fingerprint,
            affected=sorted(affected),
        )
        return sorted(affected)
