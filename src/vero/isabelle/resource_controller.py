"""Repository resource protocol: no target-attempt policy or tactic scheduler.

Only this trusted controller publishes plans, native acceptance and seals. The
legacy six-attempt controller remains unchanged and cannot load these ledgers.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import socketserver
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import repository_sources as sources
from .benchmark import digest, read, save
from .checker import filled
from .freeform import support
from .freeform_sources import render, tokens
from .repository import MeasuredChecker, RepositoryController
from .resource_budget import ResourceBudgetStopped
from .resource_plan import ResourcePlanLedger
from .run_metrics import MetricsOutbox, implementation_identity


class ResourceCancelled(RuntimeError):
    """An operator/supervisor cancellation, never a mathematical failure."""


class _CancellableChecker:
    def __init__(self, controller, native):
        self.controller, self.native, self.config = controller, native, native.config

    def _call(self, operation, *args, **kwargs):
        self.controller._require_work_allowed()
        result = getattr(self.native, operation)(*args, **kwargs)
        if self.controller._cancelled.is_set():
            result = {
                **result,
                "native_status": result.get("native_status", result.get("status")),
                "status": self.controller._cancel_status(),
                "cancel_reason": self.controller._cancel_reason,
            }
        return result

    def check(self, *args, **kwargs):
        return self._call("check", *args, **kwargs)

    def build_session(self, *args, **kwargs):
        return self._call("build_session", *args, **kwargs)

    def close(self):
        if hasattr(self.native, "close"):
            self.native.close()


class ResourceController(MetricsOutbox):
    snapshot = staticmethod(sources.snapshot)
    _capture_helper = RepositoryController._capture_helper

    def __init__(
        self,
        bench,
        root,
        checker,
        workspace,
        reference,
        *,
        budget,
        telemetry=None,
        enforce_workspace=False,
    ):
        self.bench, self.root = bench, Path(root)
        self.workspace = Path(workspace)
        self.enforce_workspace = enforce_workspace
        self.phase = "auto"
        self.root.mkdir(parents=True, exist_ok=True)
        self.reference = self.root / "references"
        self.reference.mkdir(exist_ok=True)
        refs = read(Path(reference) / "reference.json")
        if refs["benchmark_id"] != bench["benchmark_id"]:
            raise ValueError("reference benchmark mismatch")
        self.contract_ids = {c["id"] for c in bench["contracts"]}
        if set(refs["terms"]) != self.contract_ids:
            raise ValueError("reference inventory mismatch")
        for cid, expected in refs["terms"].items():
            p = Path(reference) / (cid + ".yxml")
            if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
                raise ValueError("reference bytes changed: " + cid)
            shutil.copyfile(p, self.reference / p.name)
        self.budget, self.telemetry, self.lock = budget, telemetry, threading.RLock()
        self._status_lock = threading.RLock()
        self._submission_lock = threading.RLock()
        self._active_request = None
        self._live_requests = {}
        self._workers = set()
        self._published_state = {}
        self._published_plan = {}
        self._cancelled = threading.Event()
        self._cancel_reason = None
        self.cancel_callback = None
        self._cancel_worker = None
        self.checker = MeasuredChecker(
            _CancellableChecker(self, checker), self.root / "native-calls"
        )
        self.binding = {
            "profile": "codex_repository_resource_v2",
            "benchmark_id": bench["benchmark_id"],
            "references": refs["terms"],
            "checker": checker.config,
            "implementation": implementation_identity(),
        }
        self.path = self.root / "state.json"
        self.state = (
            read(self.path)
            if self.path.exists()
            else {
                "identity": self.binding,
                "contracts": {
                    cid: {"attempts": 0, "proved": False, "history": []}
                    for cid in sorted(self.contract_ids)
                },
                "helper_references": {},
                "requests": {},
                "theory_seals": {},
                "next_check": 0,
            }
        )
        if self.state["identity"] != self.binding:
            raise ValueError("resource run binding changed; never migrate an old run")
        # A socket receipt is durable even if the process dies before its worker
        # obtains the serialized execution lock.
        for receipt_path in (self.root / "requests").glob("*/receipt.json"):
            receipt = read(receipt_path)
            rid = receipt["request_id"]
            if rid != receipt_path.parent.name:
                raise ValueError("request receipt identity mismatch")
            self.state["requests"].setdefault(
                rid,
                {
                    "signature": receipt["signature"],
                    "started_at": receipt["started_at"],
                    "status": "interrupted_request",
                },
            )
        self.plans = ResourcePlanLedger(
            self.root / "plans.json",
            self.contract_ids,
            external_imports=(bench["base_import"],),
        )
        self.persist()

    def persist(self):
        save(self.path, self.state)
        self.flush_metrics()
        # Only the serialized writer publishes these immutable read views.
        with self._status_lock:
            self._published_state = copy.deepcopy(self.state)
            self._published_plan = self.plans.snapshot()
            self._published_at = time.time()

    def _cancel_status(self):
        return (
            "resource_deadline"
            if self._cancel_reason == "resource_deadline"
            else "operator_cancelled"
        )

    def _require_work_allowed(self):
        if self._cancelled.is_set():
            raise ResourceCancelled(self._cancel_reason or "operator_cancelled")
        self.budget.require_work_allowed()

    def request_cancel(self, reason="operator_cancelled"):
        """Latch before cleaning processes; no state/native lock is needed."""
        with self._submission_lock:
            if self._cancelled.is_set():
                return
            self._cancel_reason = reason
            self._cancelled.set()
            if self.cancel_callback is not None:
                self._cancel_worker = threading.Thread(
                    target=self.cancel_callback, args=(reason,), daemon=True
                )
                self._cancel_worker.start()

    def _request_view(self, rid):
        with self._submission_lock:
            live = copy.deepcopy(self._live_requests.get(rid))
        with self._status_lock:
            saved = copy.deepcopy(self._published_state.get("requests", {}).get(rid))
        if live and live.get("status") in {"queued", "running"}:
            live["elapsed_seconds"] = max(0, time.time() - live["started_at"])
            live["poll_after_seconds"] = 2
            return live
        if saved:
            if "result" not in saved:
                saved.update(
                    status="interrupted_request",
                    request_id=rid,
                    new_request_required=True,
                )
            else:
                saved.setdefault("status", "completed")
                saved.setdefault("request_id", rid)
            return saved
        return live or {"status": "unknown_request", "request_id": rid}

    def _progress_view(self, *, refreshed=False):
        with self._status_lock:
            state, plan = (
                copy.deepcopy(self._published_state),
                copy.deepcopy(self._published_plan),
            )
            published_at = self._published_at
        rows = {
            n: {
                "attempts": r["attempts"],
                "remaining_attempts": None,
                "status": "proved" if n in plan.get("green", {}) else "pending",
            }
            for n, r in state.get("contracts", {}).items()
        }
        with self._submission_lock:
            active = self._active_request
        return {
            "profile": self.binding["profile"],
            "contracts": {n: r for n, r in rows.items() if n in self.contract_ids},
            "helpers": {n: r for n, r in rows.items() if n not in self.contract_ids},
            "plan": plan,
            "budget": self.budget.status(),
            "all_proved": bool(rows)
            and all(r["status"] == "proved" for r in rows.values()),
            "all_finished": bool(state.get("integration", {}).get("complete")),
            "integration": state.get("integration"),
            "max_attempts": None,
            "snapshot_at": published_at,
            "workspace_refreshed": refreshed,
            "active_request": self._request_view(active) if active else None,
            "cancel_requested": self._cancelled.is_set(),
        }

    def event(self, kind, **fields):
        self.record_metric(
            kind, event_id="resource:" + digest({"kind": kind, **fields}), **fields
        )
        self.persist()

    def _plan(self):
        # Public ledger API returns the accepted plan; normalize in one place.
        return self.plans.require_plan()["plan"]

    def _theories(self):
        p = self._plan()
        return {x["name"]: x for x in p["theories"]}

    def _inventory(self):
        return {
            n: t["name"]
            for t in self._theories().values()
            for n in [*t["contracts"], *(h["id"] for h in t.get("helpers", []))]
        }

    def _sync(self):
        snapshot = self.plans.snapshot()
        for nid, row in self.state["contracts"].items():
            row["proved"] = nid in snapshot["green"]
        self.state["theory_seals"] = snapshot["seals"]
        # The plan journal is authoritative. Export it with an atomic state/outbox
        # cursor, recovering events even if a crash happened after native acceptance.
        cursor = self.state.get("plan_event_cursor", 0)
        for event in snapshot["events"]:
            if event["sequence"] <= cursor:
                continue
            common = {
                "event_id": "resource-plan:" + str(event["sequence"]),
                "timestamp": event["at"],
                "plan_version": event["plan_version"],
            }
            kind = event["kind"]
            if kind == "validity_invalidated":
                self.state.pop("integration", None)
                self.record_metric(
                    "targets_invalidated",
                    **common,
                    target_ids=sorted(event["previous_green"]),
                    theory_ids=event["theories"],
                    reason=event["reason"],
                    previous_seals=event["previous_seals"],
                )
            elif kind == "node_green":
                record = event["record"]
                self.record_metric(
                    "target_green",
                    **common,
                    target_id=event["node_id"],
                    target_kind="contract"
                    if event["node_id"] in self.contract_ids
                    else "helper",
                    theory_id=record["theory"],
                    evidence=record["evidence"],
                    version=record["source_hash"],
                )
            elif kind == "theory_sealed":
                record = event["record"]
                # Read the inventory from the plan active at this journal event,
                # not a later regrouping's target ownership.
                accepted = next(
                    e["plan"]
                    for e in reversed(snapshot["events"][: event["sequence"]])
                    if e["kind"] == "plan_accepted"
                )
                theory = next(
                    t for t in accepted["theories"] if t["name"] == event["theory"]
                )
                ids = theory["contracts"] + [h["id"] for h in theory["helpers"]]
                self.record_metric(
                    "theory_sealed",
                    **common,
                    theory_id=event["theory"],
                    target_ids=ids,
                    evidence=record["evidence"],
                    version=record["source_hash"],
                )
            elif kind == "plan_accepted":
                self.record_metric(
                    "plan_accepted",
                    **common,
                    plan=event["plan"],
                    plan_hash=event["plan_hash"],
                    reason=event["reason"],
                )
                for theory in event["plan"]["theories"]:
                    for nid in theory["contracts"] + [
                        h["id"] for h in theory["helpers"]
                    ]:
                        self.state["contracts"].setdefault(
                            nid, {"attempts": 0, "proved": False, "history": []}
                        )
                        self.record_metric(
                            "target_registered",
                            event_id=common["event_id"] + ":" + nid,
                            timestamp=event["at"],
                            target_id=nid,
                            target_kind="contract"
                            if nid in self.contract_ids
                            else "helper",
                            theory_id=theory["name"],
                            version=event["plan_version"],
                        )
                    for helper in theory["helpers"]:
                        self.record_metric(
                            "helper_relation",
                            event_id=common["event_id"] + ":relation:" + helper["id"],
                            timestamp=event["at"],
                            helper_id=helper["id"],
                            consumer_ids=[],
                            dependency_ids=helper["dependencies"],
                            relation_basis="declared_plan_dependencies",
                        )
            self.state["plan_event_cursor"] = event["sequence"]
        self.persist()
        return snapshot

    def _refresh_workspace(self):
        state = self.plans.snapshot()
        if not self.enforce_workspace or state["plan"] is None:
            return
        try:
            frozen = self._frozen()
        except (ValueError, KeyError, OSError) as exc:
            if state["green"] or state["seals"]:
                self.plans.invalidate(
                    list(state["inventory"]["theories"]),
                    "workspace is not a valid planned snapshot: " + str(exc),
                )
        else:
            self._observe(frozen)

    def progress(self):
        # Native execution may own the writer lock for minutes. A read never
        # waits for it, and explicitly labels the last published snapshot.
        if not self.lock.acquire(blocking=False):
            return self._progress_view()
        try:
            self._refresh_workspace()
            self._sync()
            return self._progress_view(refreshed=True)
        finally:
            self.lock.release()

    def _frozen(self, files=None):
        theories = self._theories()
        inventory = self._inventory()
        if self.enforce_workspace and files is not None:
            actual = self.snapshot(
                self.workspace, set(inventory), self.bench["base_import"]
            )
            actual_files = {m["path"]: m["text"] for m in actual["modules"].values()}
            if files != actual_files:
                raise ValueError("submitted snapshot must equal the actual workspace")
        frozen = self.snapshot(
            self.workspace, set(inventory), self.bench["base_import"], files
        )
        if set(frozen["modules"]) != set(theories) or set(frozen["contracts"]) != set(
            inventory
        ):
            raise ValueError(
                "sources must contain the complete accepted theory/node inventory"
            )
        for name, mod in frozen["modules"].items():
            t = theories[name]
            if mod["path"] != t["path"] or set(mod["imports"]) != set(t["imports"]):
                raise ValueError(
                    "source path/imports differ from accepted plan: " + name
                )
            if any(inventory[x["id"]] != name for x in mod["contracts"]):
                raise ValueError("source node assigned to wrong theory")
        return frozen

    def _observe(self, frozen):
        self.plans.observe_sources(
            {
                n: hashlib.sha256(m["text"].encode()).hexdigest()
                for n, m in frozen["modules"].items()
            }
        )
        return self._sync()

    def _capture_declarations(self, frozen, ids, directory):
        """Elaborate one theory's pending statements in one native request."""
        owner = frozen["contracts"][ids[0]]["module"]
        if any(frozen["contracts"][nid]["module"] != owner for nid in ids):
            raise ValueError("declaration batch must belong to one theory")
        files = {m["path"]: m["text"] for m in frozen["modules"].values()}
        module = frozen["modules"][owner]
        last = max(ids, key=lambda nid: frozen["contracts"][nid]["start"])
        green = {nid for nid, row in self.state["contracts"].items() if row["proved"]}
        text = module["text"]
        for declaration in reversed(module["contracts"]):
            # Preserve already certified preceding facts while capturing their
            # declarations. No submitted, unproved candidate enters the context.
            if declaration["id"] in ids and (
                declaration["id"] not in green or declaration["id"] == last
            ):
                text = (
                    text[: declaration["proof_start"]]
                    + "oops\n"
                    + text[declaration["end"] :]
                )
        files[module["path"]] = text
        clean = self.snapshot(
            self.workspace, set(frozen["contracts"]), self.bench["base_import"], files
        )
        source = self._source(clean, directory, [], last)
        terms = directory / "terms"
        if self.checker.config.get("command_prefix"):
            terms.chmod(0o777)
        body_path = source / (owner + ".thy")
        body = body_path.read_text().replace(
            '\nML \\<open>writeln "FREEFORM_TARGET_BEGIN ' + last + '"\\<close>\n',
            "",
        )
        for nid in ids:
            # Never mistake copied reference bytes for newly elaborated output.
            (terms / (nid + ".yxml")).unlink(missing_ok=True)
            start, end = self._declaration_span(body, nid)
            body = body[:start] + 'contract_reference "' + nid + '" ' + nid + body[end:]
            audit = (
                '\nlocal_setup \\<open>Freeform_Reference.audit "'
                + nid
                + '" "'
                + nid
                + '"\\<close>\n'
            )
            body = body.replace(audit, "")
        body_path.write_text(body)
        initial = any(nid in self.contract_ids for nid in ids)
        timeout = self.checker.config.get(
            "initial_statement_timeout_seconds"
            if initial
            else "helper_statement_timeout_seconds",
            900,
        )
        result = self.checker.check(source, owner, timeout=timeout)
        save(directory / "native.json", result)
        if result["status"] != "ok" or any(
            not (terms / (nid + ".yxml")).is_file() for nid in ids
        ):
            raise ValueError(
                "declaration batch did not elaborate: "
                + str(result.get("log", ""))[-6000:]
            )
        return {nid: (terms / (nid + ".yxml")).read_bytes() for nid in ids}

    @staticmethod
    def _declaration_span(body, node_id):
        """Locate the unique executable declaration, excluding comments/quotes."""
        lexemes = tokens(body)
        spans = [
            (command.start, binding.end)
            for command, binding in zip(lexemes, lexemes[1:])
            if not command.quoted
            and command.value in sources.THEOREMS
            and not binding.quoted
            and binding.value == node_id
        ]
        if len(spans) != 1:
            raise ValueError("expected exactly one target declaration: " + node_id)
        return spans[0]

    @staticmethod
    def _binding_prefix(statement):
        # Preserve binding attributes independently of proposition equivalence.
        # The trusted benchmark and generated declaration differ only in binding
        # name/command; quoted colons are part of statements, never delimiters.
        ts = tokens(statement)[2:]
        end = next(
            (
                i
                for i, token in enumerate(ts)
                if not token.quoted and token.value == ":"
            ),
            len(ts),
        )
        return [(token.value, token.quoted) for token in ts[:end]]

    def _register(self, frozen, directory, ids=None):
        requested = list(frozen["contracts"]) if ids is None else ids
        if (
            not isinstance(requested, list)
            or not requested
            or len(requested) != len(set(requested))
            or not set(requested) <= set(frozen["contracts"])
        ):
            raise ValueError("expected unique planned statement IDs")
        # Admit the complete request before starting any costly elaboration.
        for name in {frozen["contracts"][nid]["module"] for nid in requested}:
            self._require_imports(frozen, name)
        helpers = {h["id"]: h for t in self._theories().values() for h in t["helpers"]}
        pending = {}
        green = {nid for nid, row in self.state["contracts"].items() if row["proved"]}
        originals = {c["id"]: c for c in self.bench["contracts"]}
        for nid in frozen["contracts"]:
            if nid in originals and self._binding_prefix(
                sources.statement_text(frozen, nid)
            ) != self._binding_prefix(originals[nid]["statement"]):
                raise ValueError("original contract binding attributes changed: " + nid)
            statement_hash = hashlib.sha256(
                sources.statement_text(frozen, nid).strip().encode()
            ).hexdigest()
            if nid in helpers and helpers[nid]["statement_hash"] != statement_hash:
                raise ValueError("helper statement differs from accepted plan: " + nid)
            if nid not in requested:
                continue
            signature = sources.declaration_context_signature(frozen, nid, green)
            old = self.state.setdefault("declarations", {}).get(nid)
            if not old or old["signature"] != signature:
                pending[nid] = signature
        for name in frozen["order"]:
            ids = [
                d["id"]
                for d in frozen["modules"][name]["contracts"]
                if d["id"] in pending
            ]
            if not ids:
                continue
            self._require_work_allowed()
            terms = self._capture_declarations(
                frozen, ids, directory / "helper-registrations" / name
            )
            for nid, term in terms.items():
                term_hash = hashlib.sha256(term).hexdigest()
                if (
                    nid in self.contract_ids
                    and term_hash != self.binding["references"][nid]
                ):
                    raise ValueError(
                        "original contract proposition/context changed: " + nid
                    )
                if nid not in self.contract_ids:
                    (self.reference / (nid + ".yxml")).write_bytes(term)
                    self.state["helper_references"][nid] = term_hash
                self.state["declarations"][nid] = {
                    "signature": pending[nid],
                    "term_sha256": term_hash,
                }
                self.event(
                    "target_registered",
                    target_id=nid,
                    target_kind="contract" if nid in self.contract_ids else "helper",
                    theory_id=name,
                    version=pending[nid],
                    evidence_id=str(
                        directory / "helper-registrations" / name / "native.json"
                    ),
                )

    def _source(self, frozen, directory, selected, target=None):
        directory.mkdir(parents=True, exist_ok=True)
        terms = directory / "terms"
        terms.mkdir(exist_ok=True)
        for p in self.reference.glob("*.yxml"):
            shutil.copyfile(p, terms / p.name)
        source = directory / "source"
        green = {n for n, r in self.state["contracts"].items() if r["proved"]}
        render(frozen, source, green | set(selected), target)
        (source / "Freeform_Support.thy").write_text(support(self.bench, terms))
        return source

    def _require_imports(self, frozen, theory):
        snap = self.plans.snapshot()
        for dep in frozen["modules"][theory]["dependencies"]:
            if not snap.get("seals", {}).get(dep):
                raise ValueError("generated import must be sealed: " + dep)

    def _publish_if_allowed(self, operation, *args, **kwargs):
        # Cancellation and acceptance commit share this short admission lock.
        with self._submission_lock:
            if not self._acceptance_allowed():
                return False
            operation(*args, **kwargs)
            return True

    def _acceptance_allowed(self):
        try:
            self._require_work_allowed()
        except (ResourceBudgetStopped, ResourceCancelled):
            return False
        return True

    def check(self, frozen, ids, directory):
        if (
            not ids
            or len(ids) != len(set(ids))
            or not set(ids) <= set(frozen["contracts"])
        ):
            raise ValueError("expected unique planned target IDs")
        self._observe(frozen)
        for nid in ids:
            self._require_imports(frozen, frozen["contracts"][nid]["module"])
            if not filled(sources.proof_text(frozen, nid)):
                raise ValueError("empty candidate: " + nid)
        self._register(frozen, directory, ids)
        outcomes = []
        for name in frozen["order"]:
            selected = [
                d["id"] for d in frozen["modules"][name]["contracts"] if d["id"] in ids
            ]
            if not selected:
                continue
            self._require_work_allowed()
            self._require_imports(frozen, name)
            checked_context = self.plans.context(name)
            for nid in selected:
                if not filled(sources.proof_text(frozen, nid)):
                    raise ValueError("empty candidate: " + nid)
                self.state["contracts"][nid]["attempts"] += 1
            self.persist()  # reserve all submitted work before native execution
            d = directory / name
            source = self._source(frozen, d, selected, selected[-1])
            if len(selected) > 1:
                # One candidate transaction starts at the first selected proof,
                # not at its final sibling. No speculative proof enters the
                # committed prefix or escapes the batch execution deadline.
                path = source / (name + ".thy")
                marker = (
                    '\nML \\<open>writeln "FREEFORM_TARGET_BEGIN '
                    + selected[-1]
                    + '"\\<close>\n'
                )
                body = path.read_text().replace(marker, "")
                marker = (
                    'ML \\<open>writeln "FREEFORM_TARGET_BEGIN '
                    + selected[0]
                    + '"\\<close>\n'
                )
                start, _ = self._declaration_span(body, selected[0])
                body = body[:start] + marker + body[start:]
                path.write_text(body)
                save(source / "batch-targets.json", {"target_ids": selected})
            result = self.checker.check(source, name)
            save(d / "native.json", result)
            within_budget = self._acceptance_allowed()
            audited = set(result.get("audited", []))
            for nid in selected:
                valid = (
                    within_budget
                    and result["status"] == "ok"
                    and nid + ":prove" in audited
                )
                evidence = str(d / "native.json")
                item = {
                    "id": nid,
                    "native_status": result["status"],
                    "status": (
                        self._cancel_status()
                        if self._cancelled.is_set()
                        else "resource_deadline"
                    )
                    if not within_budget
                    else "proved"
                    if valid
                    else result["status"]
                    if result["status"] != "ok"
                    else "missing_audit",
                    "evidence": evidence,
                    "native_feedback": self._diagnostic(
                        result, max(0, len(result.get("log", "")) - 12000), 12000
                    ),
                    "has_full_feedback": True,
                    "attempt": self.state["contracts"][nid]["attempts"],
                }
                if valid:
                    # Batch native evidence is retained for every target, but
                    # publication is admitted separately at its commit time.
                    within_budget = within_budget and self._acceptance_allowed()
                    if within_budget:
                        within_budget = self._publish_if_allowed(
                            self.plans.mark_green,
                            nid,
                            {"evidence_id": evidence},
                            expected_context=checked_context,
                        )
                    if not within_budget:
                        item["status"] = (
                            self._cancel_status()
                            if self._cancelled.is_set()
                            else "resource_deadline"
                        )
                self.state["contracts"][nid]["history"].append(item)
                self.event(
                    "candidate_finished",
                    target_id=nid,
                    node_id=nid,
                    status=item["status"],
                    evidence_id=evidence,
                    attempt=item["attempt"],
                )
                outcomes.append(item)
            self._sync()
            if not within_budget:
                break
            if all(
                self.state["contracts"][d["id"]]["proved"]
                for d in frozen["modules"][name]["contracts"]
            ):
                self.seal(frozen, name, directory / "sealing" / name)
        return {"outcomes": outcomes, "progress": self.progress()}

    def seal(self, frozen, name, directory):
        self._require_work_allowed()
        self._observe(frozen)
        self._require_imports(frozen, name)
        checked_context = self.plans.require_sealable(name)
        source = self._source(frozen, directory, [])
        result = self.checker.check(source, name)
        save(directory / "native.json", result)
        ids = [d["id"] for d in frozen["modules"][name]["contracts"]]
        valid = result["status"] == "ok" and {n + ":prove" for n in ids} <= set(
            result.get("audited", [])
        )
        within_budget = self._acceptance_allowed()
        build = (
            self.checker.build_session(source, [name])
            if valid and within_budget
            else None
        )
        if build is not None:
            save(directory / "build.json", build)
        within_budget = within_budget and self._acceptance_allowed()
        evidence = {
            "native": str(directory / "native.json"),
            "build": str(directory / "build.json"),
        }
        if within_budget and valid and build is not None and build["status"] == "ok":
            within_budget = self._publish_if_allowed(
                self.plans.mark_sealed, name, evidence, expected_context=checked_context
            )
            if within_budget:
                self._sync()
                return {"status": "sealed", "evidence": evidence}
        status = (
            "seal_failed"
            if within_budget
            else self._cancel_status()
            if self._cancelled.is_set()
            else "resource_deadline"
        )
        self.event(
            "seal_finished",
            theory_id=name,
            target_ids=ids,
            status=status,
            evidence=evidence,
            native_status=result["status"],
        )
        return {"status": status, "native": result, "build": build}

    def integrate(self, frozen, directory):
        self._observe(frozen)
        if not all(
            self.plans.snapshot().get("seals", {}).get(n) for n in frozen["order"]
        ):
            raise ValueError("all planned theories must be sealed before integration")
        self._require_work_allowed()
        source = self._source(frozen, directory, [])
        name = "Freeform_Integration"
        (source / (name + ".thy")).write_text(
            "theory " + name + " imports " + " ".join(frozen["order"]) + " begin\nend\n"
        )
        result = self.checker.check(source, name)
        save(directory / "native.json", result)
        complete = result["status"] == "ok" and {
            n + ":prove" for n in frozen["contracts"]
        } <= set(result.get("audited", []))
        within_budget = self._acceptance_allowed()
        build = (
            self.checker.build_session(
                source,
                [*frozen["order"], name],
                timeout=self.checker.config.get(
                    "integration_build_timeout_seconds", 900
                ),
            )
            if complete and within_budget
            else None
        )
        if build is not None:
            save(directory / "build.json", build)
        within_budget = within_budget and self._acceptance_allowed()
        workspace_current = True
        if self.enforce_workspace:
            # The checked copy is immutable, but the agent may edit its live
            # workspace while native checking/building is running. Retain that
            # old native result without advertising current repository success.
            self._refresh_workspace()
            current = self._sync()
            expected = {
                n: hashlib.sha256(m["text"].encode()).hexdigest()
                for n, m in frozen["modules"].items()
            }
            workspace_current = current["sources"] == expected and all(
                name in current["seals"] for name in frozen["order"]
            )
        with self._submission_lock:
            within_budget = within_budget and self._acceptance_allowed()
            self.state["integration"] = {
                "complete": bool(
                    within_budget
                    and workspace_current
                    and complete
                    and build is not None
                    and build["status"] == "ok"
                ),
                "status": "snapshot_changed"
                if not workspace_current
                else "checked"
                if within_budget
                else "resource_deadline",
                "evidence": str(directory),
                "native": result,
                "build": build,
            }
        if self.state["integration"]["complete"]:
            self.event(
                "repository_integrated",
                target_ids=sorted(frozen["contracts"]),
                evidence=str(directory),
            )
        self.persist()
        return self.state["integration"]

    def query(self, frozen, target_id, facts, directory):
        """Ask Isabelle for named facts in the target's legal current prefix."""
        if target_id not in frozen["contracts"]:
            raise ValueError("query target must be a planned node")
        fact_name = re.compile(
            r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z"
        )
        if (
            not isinstance(facts, list)
            or not facts
            or len(facts) > 100
            or any(
                not isinstance(fact, str) or not fact_name.fullmatch(fact)
                for fact in facts
            )
        ):
            raise ValueError("query requires 1..100 Isabelle fact names")
        self._require_work_allowed()
        self._observe(frozen)
        declaration = frozen["contracts"][target_id]
        owner = declaration["module"]
        self._require_imports(frozen, owner)
        files = {m["path"]: m["text"] for m in frozen["modules"].values()}
        module = frozen["modules"][owner]
        text = module["text"]
        files[module["path"]] = (
            text[: declaration["proof_start"]] + "oops\n" + text[declaration["end"] :]
        )
        clean = self.snapshot(
            self.workspace, set(frozen["contracts"]), self.bench["base_import"], files
        )
        source = self._source(clean, directory, [], target_id)
        path = source / (owner + ".thy")
        body = path.read_text()
        audit = (
            '\nlocal_setup \\<open>Freeform_Reference.audit "'
            + target_id
            + '" "'
            + target_id
            + '"\\<close>\n'
        )
        body = body.replace(audit, "")
        # A fact query elaborates an oops shell. It is not a candidate proof
        # transaction and cannot require/produce a GREEN theorem audit.
        body = re.sub(
            r'ML \\<open>writeln "FREEFORM_TARGET_BEGIN [^"\n]+"\\<close>\n?',
            "",
            body,
        )
        start, _ = self._declaration_span(body, target_id)
        body = body[:start] + "thm " + " ".join(facts) + "\n" + body[start:]
        path.write_text(body)
        result = self.checker.check(
            source, owner, timeout=self.checker.config.get("timeout_seconds", 300)
        )
        save(directory / "native.json", result)
        self.event(
            "fact_query_finished",
            target_id=target_id,
            theory_id=owner,
            facts=facts,
            status=result["status"],
            evidence_id=str(directory / "native.json"),
        )
        return {
            "status": result["status"],
            "facts": facts,
            "log": result.get("log", ""),
            "evidence": str(directory / "native.json"),
        }

    @staticmethod
    def _diagnostic(native, offset, limit):
        log = native.get("log", "")
        end = min(len(log), offset + limit)
        metadata = {
            key: native[key]
            for key in (
                "status",
                "failure_kind",
                "timeout_phase",
                "timeout_limit",
                "elapsed_seconds",
                "returncode",
                "started_at",
                "finished_at",
                "owner_started",
                "persistent",
            )
            if key in native
        }
        return {
            **metadata,
            "log": log[offset:end],
            "offset": offset,
            "total_chars": len(log),
            "next_offset": end if end < len(log) else None,
            "truncated": offset > 0 or end < len(log),
        }

    def feedback(self, payload, *, state=None):
        state = self.state if state is None else state
        offset, limit = payload.get("offset", 0), payload.get("limit", 12000)
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 65536
        ):
            raise ValueError("feedback offset must be nonnegative and limit 1..65536")
        target = payload.get("target_id")
        if target is not None and (
            not isinstance(target, str) or target not in state["contracts"]
        ):
            raise ValueError("feedback requires a registered target ID")
        reply = {}
        for nid, row in state["contracts"].items():
            if target is not None and nid != target:
                continue
            reply[nid] = []
            for outcome in row["history"][-1:]:
                # Never accept a caller-selected path. Only trusted ledger
                # evidence for this target may cross the private checker boundary.
                path = Path(outcome["evidence"]).resolve()
                if (
                    not path.is_relative_to(self.root.resolve())
                    or path.name != "native.json"
                ):
                    raise ValueError("invalid trusted native evidence path")
                if path.is_file():
                    diagnostic = self._diagnostic(read(path), offset, limit)
                else:
                    diagnostic = {
                        "status": "evidence_unavailable",
                        "log": "",
                        "offset": offset,
                        "total_chars": None,
                        "next_offset": None,
                        "truncated": False,
                    }
                reply[nid].append({**outcome, "native_feedback": diagnostic})
        return reply

    def _dispatch_serial(self, payload):
        with self.lock:
            command = payload["cmd"]
            if self.phase == "planning" and command not in {
                "plan",
                "progress",
                "feedback",
                "result",
            }:
                return {
                    "status": "rejected",
                    "error": "trusted planning phase has not finished",
                }
            if command == "progress":
                return self.progress()
            if command == "feedback":
                return self.feedback(payload)
            rid = payload.get("request_id", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
                raise ValueError("request ID required")
            old = self.state["requests"].get(rid)
            if command == "result":
                return old or {"status": "unknown_request"}
            signature = digest(payload)
            if old:
                if old["signature"] != signature:
                    raise ValueError("request ID reused with different input")
                if "result" in old:
                    return old["result"]
                return {
                    "status": "interrupted_request",
                    "request_id": rid,
                    "new_request_required": True,
                }
            self._require_work_allowed()
            self.state["requests"][rid] = {
                "signature": signature,
                "started_at": time.time(),
            }
            self.persist()
            directory = self.root / "requests" / rid
            directory.mkdir(parents=True, exist_ok=True)
            save(directory / "request.json", payload)
            try:
                if command in {"plan", "revise"}:
                    if command == "plan" and self.plans.snapshot().get("version", 0):
                        raise ValueError("initial plan already accepted; use revise")
                    if command == "revise" and (
                        "expected_version" not in payload or not payload.get("reason")
                    ):
                        raise ValueError(
                            "revision requires expected_version and reason"
                        )
                    result = self.plans.accept_plan(
                        payload["plan"],
                        reason=payload.get("reason", "agent plan"),
                        expected_version=payload.get("expected_version"),
                    )
                    for nid in self._inventory():
                        self.state["contracts"].setdefault(
                            nid, {"attempts": 0, "proved": False, "history": []}
                        )
                    self._sync()
                elif command == "relate":
                    nodes = self.plans.require_plan()["inventory"]["nodes"]
                    helper_id = payload.get("helper_id")
                    consumers = payload.get("consumer_ids")
                    if (
                        not isinstance(helper_id, str)
                        or nodes.get(helper_id, {}).get("kind") != "helper"
                    ):
                        raise ValueError("relation requires a registered helper ID")
                    if (
                        not isinstance(consumers, list)
                        or any(
                            not isinstance(n, str) or n not in nodes or n == helper_id
                            for n in consumers
                        )
                        or len(set(consumers)) != len(consumers)
                    ):
                        raise ValueError(
                            "relation requires unique planned consumer IDs"
                        )
                    self.event(
                        "helper_relation",
                        helper_id=helper_id,
                        consumer_ids=consumers,
                        relation_basis="agent_declared_dependency",
                        actual_reference_confirmed=False,
                        request_id=rid,
                    )
                    result = {
                        "status": "relation_recorded",
                        "helper_id": helper_id,
                        "consumer_ids": consumers,
                        "actual_reference_confirmed": False,
                    }
                elif command == "scope":
                    inventory = self._inventory()
                    if not set(payload.get("target_ids", [])) <= set(inventory):
                        raise ValueError("unknown scope target")
                    self.event(
                        "work_scope",
                        target_ids=payload.get("target_ids", []),
                        theory_ids=payload.get("theory_ids", []),
                        phase=payload.get("phase", "proof"),
                        attribution_basis="agent_declared_work_scope",
                        confidence="declared_not_causal",
                        request_id=rid,
                    )
                    result = {
                        "status": "scope_recorded",
                        "response_attribution": "unattributed_until_explicit_response_binding",
                    }
                else:
                    frozen = self._frozen(payload.get("files"))
                    save(directory / "snapshot.json", frozen)
                    if command == "check":
                        result = self.check(frozen, payload["target_ids"], directory)
                    elif command == "check-theory":
                        result = self.check(
                            frozen,
                            [
                                d["id"]
                                for d in frozen["modules"][payload["theory"]][
                                    "contracts"
                                ]
                            ],
                            directory,
                        )
                    elif command == "statement":
                        self._observe(frozen)
                        self._register(frozen, directory, ids=payload.get("target_ids"))
                        result = {"status": "statements_valid"}
                    elif command == "query":
                        result = self.query(
                            frozen, payload["target_id"], payload["facts"], directory
                        )
                    elif command == "seal":
                        result = self.seal(frozen, payload["theory"], directory)
                    elif command in {"integrate", "finish"}:
                        result = self.integrate(frozen, directory)
                    else:
                        raise ValueError("unknown command")
            except (ValueError, RuntimeError, KeyError) as exc:
                result = {"status": "rejected", "error": str(exc)}
            if self._cancelled.is_set():
                result = {
                    "status": self._cancel_status(),
                    "reason": self._cancel_reason,
                    "partial_result": result,
                }
            self.state["requests"][rid].update(
                result=result,
                status=("cancelled" if self._cancelled.is_set() else "completed"),
                finished_at=time.time(),
            )
            self.persist()
            return result

    def _dispatch_reserved(self, payload):
        rid = payload["request_id"]
        with self._submission_lock:
            self._live_requests[rid]["status"] = "running"
        try:
            result = self._dispatch_serial(payload)
            return result
        except BaseException as exc:
            with self._submission_lock:
                self._live_requests[rid].update(
                    status="interrupted_request",
                    error_type=type(exc).__name__,
                    new_request_required=True,
                )
            raise
        finally:
            with self._submission_lock:
                self._live_requests[rid].setdefault("finished_at", time.time())
                if self._live_requests[rid]["status"] == "running":
                    self._live_requests[rid]["status"] = (
                        "cancelled" if self._cancelled.is_set() else "completed"
                    )
                self._active_request = None

    def dispatch(self, payload, *, background=False):
        command = payload["cmd"]
        if self.phase == "planning" and command not in {
            "plan",
            "progress",
            "feedback",
            "result",
        }:
            return {
                "status": "rejected",
                "error": "trusted planning phase has not finished",
            }
        if command == "progress":
            return self.progress()
        rid = payload.get("request_id", "")
        if command == "feedback":
            with self._status_lock:
                state = copy.deepcopy(self._published_state)
            return self.feedback(payload, state=state)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
            raise ValueError("request ID required")
        if command == "result":
            return self._request_view(rid)
        signature = digest(payload)
        with self._submission_lock:
            view = self._request_view(rid)
            if view.get("status") != "unknown_request":
                if view.get("signature") != signature:
                    raise ValueError("request ID reused with different input")
                return view.get("result", view)
            if self._active_request:
                return {
                    "status": "busy",
                    "request_id": rid,
                    "accepted": False,
                    "active_request_id": self._active_request,
                    "poll_after_seconds": 2,
                }
            self._require_work_allowed()
            self._active_request = rid
            self._live_requests[rid] = {
                "status": "queued",
                "request_id": rid,
                "signature": signature,
                "command": command,
                "target_ids": payload.get("target_ids", []),
                "theory": payload.get("theory"),
                "started_at": time.time(),
            }
            worker = None
            try:
                directory = self.root / "requests" / rid
                directory.mkdir(parents=True, exist_ok=True)
                save(directory / "request.json", payload)
                save(directory / "receipt.json", self._live_requests[rid])
                if background:

                    def run():
                        try:
                            self._dispatch_reserved(payload)
                        except BaseException:
                            pass  # Durable reservation is reported as interrupted.
                        finally:
                            with self._submission_lock:
                                self._workers.discard(threading.current_thread())

                    worker = threading.Thread(target=run, daemon=True)
                    self._workers.add(worker)
                    worker.start()
            except BaseException as exc:
                # No native worker owns this reservation yet. Preserve durable
                # receipts while releasing the slot after disk/thread failure.
                self._active_request = None
                self._live_requests[rid].update(
                    status="interrupted_request",
                    accepted=False,
                    error_type=type(exc).__name__,
                    finished_at=time.time(),
                    new_request_required=True,
                )
                if worker is not None:
                    self._workers.discard(worker)
                raise
        if worker is None:
            return self._dispatch_reserved(payload)
        # Fast operations still return their completed reply. Native work gets a
        # recoverable receipt; the socket handler never waits for a whole check.
        worker.join(timeout=0.05)
        return self._request_view(rid)

    @contextmanager
    def serve(self):
        controller = self
        self.progress()

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(10)
                try:
                    line = self.rfile.readline(32_000_001)
                    if len(line) > 32_000_000:
                        raise ValueError("oversized controller request")
                    result = controller.dispatch(json.loads(line), background=True)
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    result = {"status": "controller_error", "error": str(exc)}
                try:
                    self.wfile.write((json.dumps(result) + "\n").encode())
                except OSError:
                    pass

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            block_on_close = False
            request_queue_size = 128

        with tempfile.TemporaryDirectory(prefix="vero-resource-") as tmp:
            os.chmod(tmp, 0o711)
            endpoint = str(Path(tmp) / "controller.sock")
            with Server(endpoint, Handler) as server:
                os.chmod(endpoint, 0o666)
                thread = threading.Thread(
                    target=server.serve_forever,
                    kwargs={"poll_interval": 0.05},
                    daemon=True,
                )
                thread.start()
                try:
                    yield endpoint
                finally:
                    with self._submission_lock:
                        workers = list(self._workers)
                    if workers:
                        self.request_cancel("service_shutdown")
                    server.shutdown()
                    thread.join(timeout=2)
                    if self._cancel_worker is not None:
                        self._cancel_worker.join(timeout=15)
                    for worker in workers:
                        worker.join(timeout=5)
                    if any(worker.is_alive() for worker in workers):
                        raise RuntimeError(
                            "native request did not stop after cancellation"
                        )
                    self.checker.close()
