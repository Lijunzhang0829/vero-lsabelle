"""Transactional plan and validity ledger for the resource-v2 controller.

The controller, not the agent, owns this file and supplies native check evidence.
This module validates inventories and version bindings; it does not certify proofs.
An atomic state includes the complete event journal so crash recovery cannot lose
an accepted mutation between a state write and an event append.
"""

import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path, PurePosixPath

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
HELPER = re.compile(r"h[0-9]{4}\Z")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def validate_plan(plan, contract_ids, external_imports=()):
    """Return normalized inventory, rejecting omissions, aliases and cyclic imports."""
    if not isinstance(plan, dict) or set(plan) != {"theories"}:
        raise ValueError("plan must contain only theories")
    entries = plan["theories"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("theories must be a nonempty list")
    theories, nodes, paths = {}, {}, set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "name",
            "path",
            "imports",
            "contracts",
            "helpers",
        }:
            raise ValueError("theory requires name, path, imports, contracts, helpers")
        name, path = entry["name"], entry["path"]
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError("invalid theory name")
        if (
            name in {"Freeform_Support", "Freeform_Reference", "Freeform_Integration"}
            or name in external_imports
        ):
            raise ValueError("reserved or parent theory name")
        if not isinstance(path, str):
            raise ValueError("invalid theory path")  # noqa: TRY004 - protocol rejection
        parsed = PurePosixPath(path)
        if (
            parsed.is_absolute()
            or ".." in parsed.parts
            or "\\" in path
            or str(parsed) != path
            or parsed.name != name + ".thy"
        ):
            raise ValueError("invalid theory path")
        if name in theories or path in paths:
            raise ValueError("duplicate theory or path")
        for key in ("imports", "contracts", "helpers"):
            if not isinstance(entry[key], list):
                raise ValueError(key + " must be a list")  # noqa: TRY004 - protocol rejection
        imports = entry["imports"]
        if any(not isinstance(x, str) for x in imports) or len(set(imports)) != len(
            imports
        ):
            raise ValueError("invalid/duplicate imports")
        paths.add(path)
        theories[name] = copy.deepcopy(entry)
        for node in entry["contracts"]:
            if not isinstance(node, str) or node not in contract_ids or node in nodes:
                raise ValueError("unknown/duplicate contract")
            nodes[node] = {"kind": "contract", "theory": name}
        for helper in entry["helpers"]:
            if not isinstance(helper, dict) or set(helper) != {
                "id",
                "statement_hash",
                "dependencies",
            }:
                raise ValueError("invalid helper record")
            node = helper["id"]
            if (
                not isinstance(node, str)
                or not HELPER.fullmatch(node)
                or node in nodes
                or node in contract_ids
            ):
                raise ValueError("invalid/duplicate helper ID")
            if not isinstance(helper["statement_hash"], str) or not re.fullmatch(
                r"[0-9a-f]{64}", helper["statement_hash"]
            ):
                raise ValueError("helper statement_hash must be SHA256")
            deps = helper["dependencies"]
            if (
                not isinstance(deps, list)
                or any(not isinstance(x, str) for x in deps)
                or len(deps) != len(set(deps))
            ):
                raise ValueError("invalid helper dependencies")
            nodes[node] = {"kind": "helper", "theory": name, **copy.deepcopy(helper)}
    if {n for n, v in nodes.items() if v["kind"] == "contract"} != set(contract_ids):
        raise ValueError("plan must cover every original contract exactly once")
    active, done = set(), set()

    def visit(name):
        if name in active:
            raise ValueError("cyclic theory imports")
        if name in done:
            return
        active.add(name)
        for dep in theories[name]["imports"]:
            if dep in theories:
                visit(dep)
            elif dep not in external_imports:
                raise ValueError("unapproved external import: " + dep)
        active.remove(name)
        done.add(name)

    for name in theories:
        visit(name)
    for node, info in nodes.items():
        if any(dep not in nodes or dep == node for dep in info.get("dependencies", [])):
            raise ValueError("unknown/self helper dependency")
    visiting, visited = set(), set()

    def visit_node(node):
        if node in visiting:
            raise ValueError("cyclic helper dependencies")
        if node in visited:
            return
        visiting.add(node)
        for dep in nodes[node].get("dependencies", []):
            visit_node(dep)
        visiting.remove(node)
        visited.add(node)

    for node in nodes:
        visit_node(node)
    return {"theories": theories, "nodes": nodes}


class ResourcePlanLedger:
    def __init__(self, path, contract_ids, external_imports=()):
        self.path = Path(path)
        self.contract_ids = frozenset(contract_ids)
        self.external_imports = frozenset(external_imports)
        if not self.contract_ids:
            raise ValueError("contract inventory cannot be empty")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _empty(self):
        return {
            "schema": "resource-plan-v2",
            "contract_ids": sorted(self.contract_ids),
            "external_imports": sorted(self.external_imports),
            "version": 0,
            "plan": None,
            "inventory": {"theories": {}, "nodes": {}},
            "sources": {},
            "green": {},
            "seals": {},
            "events": [],
        }

    def _read(self):
        state = (
            json.loads(self.path.read_text()) if self.path.exists() else self._empty()
        )
        if (
            state["contract_ids"] != sorted(self.contract_ids)
            or state["external_imports"] != sorted(self.external_imports)
            or state["schema"] != "resource-plan-v2"
        ):
            raise ValueError("frozen plan ledger identity mismatch")
        return state

    def snapshot(self):
        return self._read()

    def _transaction(self, operation):
        with self.path.with_suffix(self.path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self._read()
            operation(state)
            fd, name = tempfile.mkstemp(
                prefix=self.path.name + ".", dir=self.path.parent
            )
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(state, stream, sort_keys=True, indent=2)
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
            return copy.deepcopy(state)

    @staticmethod
    def _event(state, kind, **data):
        state["events"].append(
            {
                "sequence": len(state["events"]) + 1,
                "kind": kind,
                "at": time.time(),
                "plan_version": state["version"],
                **data,
            }
        )

    @staticmethod
    def _require(state):
        if state["plan"] is None:
            raise ValueError("accepted theory plan required before proving")

    def require_plan(self):
        state = self.snapshot()
        self._require(state)
        return state

    def _invalidate(self, state, theories, reason, old_inventory=None):
        affected = set(theories)
        inventories = [state["inventory"], old_inventory or state["inventory"]]
        while True:
            before = set(affected)
            for inventory in inventories:
                for name, info in inventory["theories"].items():
                    if set(info["imports"]) & affected:
                        affected.add(name)
                for info in inventory["nodes"].values():
                    if any(
                        inventory["nodes"][d]["theory"] in affected
                        for d in info.get("dependencies", [])
                        if d in inventory["nodes"]
                    ):
                        affected.add(info["theory"])
            if affected == before:
                break
        invalid_green = {
            n: v for n, v in state["green"].items() if v["theory"] in affected
        }
        invalid_seals = {n: v for n, v in state["seals"].items() if n in affected}
        state["green"] = {
            n: v for n, v in state["green"].items() if n not in invalid_green
        }
        state["seals"] = {
            n: v for n, v in state["seals"].items() if n not in invalid_seals
        }
        self._event(
            state,
            "validity_invalidated",
            theories=sorted(affected),
            reason=reason,
            previous_green=invalid_green,
            previous_seals=invalid_seals,
        )

    def accept_plan(self, plan, reason, expected_version=None):
        inventory = validate_plan(plan, self.contract_ids, self.external_imports)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("plan revision reason is required")

        def apply(state):
            if expected_version is not None and expected_version != state["version"]:
                raise ValueError("stale plan revision")
            old = state["inventory"]
            if not set(old["nodes"]) <= set(inventory["nodes"]):
                raise ValueError("registered nodes cannot silently disappear")
            changed = {
                n
                for n in set(old["theories"]) | set(inventory["theories"])
                if old["theories"].get(n) != inventory["theories"].get(n)
            }
            previous = state["plan"]
            state["version"] += 1
            state["plan"] = copy.deepcopy(plan)
            state["inventory"] = inventory
            self._invalidate(state, changed, reason, old)
            state["sources"] = {
                n: h
                for n, h in state["sources"].items()
                if n in inventory["theories"] and n not in changed
            }
            self._event(
                state,
                "plan_accepted",
                reason=reason,
                plan=plan,
                previous_plan=previous,
                plan_hash=_digest(plan),
            )

        return self._transaction(apply)

    def invalidate(self, theories, reason):
        def apply(state):
            self._require(state)
            if not set(theories) <= set(state["inventory"]["theories"]):
                raise ValueError("unknown theory")
            self._invalidate(state, theories, reason)

        return self._transaction(apply)

    def observe_sources(self, hashes):
        """Bind all theory source hashes once before a batch; changes invalidate dependents."""

        def apply(state):
            self._require(state)
            if set(hashes) != set(state["inventory"]["theories"]):
                raise ValueError("source inventory must match accepted theory plan")
            if any(
                not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                for h in hashes.values()
            ):
                raise ValueError("source hashes must be SHA256")
            changed = {n for n, h in hashes.items() if state["sources"].get(n) != h}
            if changed:
                self._invalidate(state, changed, "source changed")
                state["sources"] = dict(hashes)
                self._event(state, "sources_observed", hashes=hashes)

        return self._transaction(apply)

    def _context(self, state, theory):
        if theory not in state["sources"]:
            raise ValueError("theory source must be observed before proving")
        imports = state["inventory"]["theories"][theory]["imports"]
        dependencies = {}
        for dep in imports:
            if dep in state["inventory"]["theories"]:
                if dep not in state["seals"]:
                    raise ValueError("generated import must be sealed: " + dep)
                dependencies[dep] = _digest(state["seals"][dep])
        return {
            "theory": theory,
            "source_hash": state["sources"][theory],
            "imports": dependencies,
            "inventory_hash": _digest(state["inventory"]["theories"][theory]),
        }

    def mark_green(self, node_id, evidence, expected_context=None):
        def apply(state):
            self._require(state)
            node = state["inventory"]["nodes"].get(node_id)
            if node is None:
                raise ValueError("unregistered node")
            context = self._context(state, node["theory"])
            if expected_context is not None and expected_context != context:
                raise ValueError("proof context changed during check")
            if not isinstance(evidence, dict) or not evidence:
                raise ValueError("native proof evidence required")
            record = {
                **context,
                "plan_version": state["version"],
                "evidence": copy.deepcopy(evidence),
            }
            state["green"][node_id] = record
            self._event(state, "node_green", node_id=node_id, record=record)

        return self._transaction(apply)

    def context(self, theory):
        state = self.require_plan()
        return self._context(state, theory)

    def _sealable(self, state, theory):
        self._require(state)
        if theory not in state["inventory"]["theories"]:
            raise ValueError("unknown theory")
        context = self._context(state, theory)
        required = {
            n for n, v in state["inventory"]["nodes"].items() if v["theory"] == theory
        }
        if not required <= set(state["green"]):
            raise ValueError("complete planned contract/helper inventory must be GREEN")
        for node in required:
            if any(state["green"][node].get(k) != v for k, v in context.items()):
                raise ValueError("stale GREEN context")
        return context

    def require_sealable(self, theory):
        return self._sealable(self.snapshot(), theory)

    def mark_sealed(self, theory, evidence, expected_context=None):
        def apply(state):
            context = self._sealable(state, theory)
            if expected_context is not None and expected_context != context:
                raise ValueError("seal context changed during check")
            if not isinstance(evidence, dict) or not evidence:
                raise ValueError("native audit and build evidence required")
            record = {
                **context,
                "plan_version": state["version"],
                "evidence": copy.deepcopy(evidence),
            }
            if state["seals"].get(theory) == record:
                return
            # Resealing the same bytes with different evidence must not leave old import bindings valid.
            dependents = [
                n
                for n, info in state["inventory"]["theories"].items()
                if theory in info["imports"]
            ]
            if dependents:
                self._invalidate(state, dependents, "import seal changed")
            state["seals"][theory] = record
            self._event(state, "theory_sealed", theory=theory, record=record)

        return self._transaction(apply)
