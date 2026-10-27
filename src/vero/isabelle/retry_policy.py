"""Supervisor-owned contract attempts; repository agents may submit whole modules.

The socket is only a submission interface. Counters and accepted proof artifacts
live outside the agent's writable sandbox and are committed before any reply.
"""

from __future__ import annotations

import json
import os
import re
import socket
import socketserver
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .benchmark import digest, read, save, validate_artifact
from .checker import evaluate, filled
from .planning import dependencies, module_order
from .run_metrics import MetricsOutbox
from .slots import context, schedule


class ContractRetries(MetricsOutbox):
    def __init__(self, bench, root, checker, max_retries=5, telemetry=None):
        self.telemetry = telemetry
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_contract_retries must be a nonnegative integer")
        self.bench, self.root, self.checker = bench, Path(root), checker
        self.incremental = None
        if getattr(checker, "config", {}).get("incremental", False):
            from .incremental import IncrementalChecks

            self.incremental = IncrementalChecks(
                self.root / "incremental-cache", checker
            )
        self.slots = schedule(bench)
        self.limit = max_retries + 1
        self.lock = threading.RLock()
        self.path = self.root / "state.json"
        identity = {"benchmark_id": bench["benchmark_id"], "max_attempts": self.limit}
        if self.path.exists():
            self.state = read(self.path)
            if self.state["identity"] != identity:
                raise ValueError("contract retry policy binding changed")
        else:
            self.state = {
                "identity": identity,
                "accepted_bodies": {},
                "contracts": {
                    c["id"]: {"attempts": 0, "proved": False, "history": []}
                    for c in bench["contracts"]
                },
                "next_check": 0,
            }
            self.persist()
        self.flush_metrics()
        # An interrupted check has no completed mathematical outcome. Preserve
        # its evidence, but do not invent a failure or consume retry credit.
        pending = self.state.get("pending")
        if pending is not None:
            directory = Path(pending["directory"])
            report = directory / "eval/report.json"
            if report.exists():
                trial = read(directory / "artifact.json")
                bodies = {k: v["body"] for k, v in trial["slots"].items() if v["found"]}
                self.complete_check(
                    pending["id"],
                    directory,
                    pending["candidate_sha256"],
                    bodies,
                    trial,
                    read(report),
                )
            else:
                self.state.pop("pending")
                self.state["recovered_interruption"] = True
                self.persist()

    def persist(self):
        save(self.path, self.state)
        self.flush_metrics()

    def artifact(self):
        return self.make_artifact(self.state["accepted_bodies"])

    def make_artifact(self, bodies):
        return {
            "schema_version": 1,
            "backend": "isabelle",
            "benchmark_id": self.bench["benchmark_id"],
            "slots": {
                k: {
                    "found": True,
                    "body": bodies.get(k, "oops" if kind == "proof" else ""),
                }
                for k, (_, kind) in self.slots.items()
            },
            "extras": [],
            "file_errors": {},
        }

    def progress(self):
        with self.lock:
            rows = {
                cid: {
                    "attempts": r["attempts"],
                    "retries_used": max(0, r["attempts"] - 1),
                    "remaining_attempts": 0
                    if r["proved"]
                    else self.limit - r["attempts"],
                    "status": "proved"
                    if r["proved"]
                    else "exhausted"
                    if r["attempts"] >= self.limit
                    else "pending",
                }
                for cid, r in self.state["contracts"].items()
            }
            return {
                "max_attempts": self.limit,
                "theory_seals": self.state.get("theory_seals", {}),
                "contracts": rows,
                "all_proved": all(r["status"] == "proved" for r in rows.values()),
                "all_finished": all(r["status"] != "pending" for r in rows.values()),
                "next_targets": [
                    cid
                    for cid, r in sorted(rows.items(), key=lambda x: x[1]["attempts"])
                    if r["status"] == "pending"
                ],
            }

    def submit(self, artifact, module=None, *, checkpoint=False, request_id=None):
        """Count completed target checks, never sibling checks or empty slots.

        Each target is evaluated with already accepted proofs, excluding every
        other new candidate. Batch order follows the effective theory DAG and
        declaration order. A successful trial must preserve all prior proofs.
        """
        with self.lock:
            validate_artifact(self.bench, artifact)
            incoming = {
                k: v["body"] for k, v in artifact["slots"].items() if v["found"]
            }
            try:
                effective = context(self.bench, incoming)
            except ValueError as exc:
                return {
                    "status": "invalid_context",
                    "error": str(exc),
                    "progress": self.progress(),
                }
            modules = module_order(effective)
            if module is not None and module not in modules:
                raise ValueError("unknown module")
            selected = set(modules) if module is None else {module}
            errors = {
                m: e
                for m, e in artifact.get("file_errors", {}).items()
                if m in selected
            }
            if errors:
                return {
                    "status": "extraction_error",
                    "errors": errors,
                    "progress": self.progress(),
                }
            outcomes = []
            for owner in modules:
                if owner not in selected:
                    continue
                for c in self.bench["contracts"]:
                    if c["module"] != owner:
                        continue
                    if (self.root.parent / "STOP_REQUEST.json").exists():
                        return {
                            "status": "operator_stopped",
                            "outcomes": outcomes,
                            "progress": self.progress(),
                        }
                    cid = c["id"]
                    row = self.state["contracts"][cid]
                    replay = next(
                        (
                            h
                            for h in row["history"]
                            if request_id is not None
                            and h.get("request_id") == request_id
                        ),
                        None,
                    )
                    if replay is not None:
                        outcomes.append(self.with_log(replay))
                        continue
                    candidates = {
                        k: incoming[k]
                        for k in (cid + ":prove", cid + ":disprove")
                        if k in incoming and filled(incoming[k])
                    }
                    if not candidates:
                        continue
                    if row["proved"] or row["attempts"] >= self.limit:
                        outcomes.append(
                            {
                                "id": cid,
                                "status": "already_proved"
                                if row["proved"]
                                else "exhausted",
                                "charged": False,
                            }
                        )
                        continue
                    bodies = dict(self.state["accepted_bodies"])
                    # Shared auxiliary declarations may be developed in imported
                    # files as well. Unsubmitted sibling proof_aux is excluded.
                    closure = dependencies(effective, owner) | {owner}
                    changes = {
                        k: body
                        for k, body in incoming.items()
                        if (
                            self.slots[k][0] in closure
                            and self.slots[k][1] in {"imports", "global_aux"}
                        )
                        or k in {cid + ":prove_aux", cid + ":disprove_aux"}
                    }
                    signature = digest({**changes, **candidates})
                    if checkpoint and row.get("last_candidate") == signature:
                        continue
                    bodies.update(changes)
                    bodies.update(candidates)
                    number = self.state["next_check"]
                    directory = self.root / f"check-{number:06d}"
                    self.state["next_check"] += 1
                    trial = self.make_artifact(bodies)
                    save(directory / "artifact.json", trial)
                    self.state["pending"] = {
                        "id": cid,
                        "directory": str(directory),
                        "candidate_sha256": signature,
                        "request_id": request_id,
                    }
                    self.record_metric(
                        "candidate_reserved",
                        event_id="candidate-reserved:" + str(directory),
                        node_id=cid,
                        contract_id=cid,
                        node_kind="contract",
                        request_id=request_id,
                        candidate_sha256=signature,
                        evidence_id=str(directory),
                        attempt=row["attempts"] + 1,
                    )
                    self.persist()
                    report = evaluate(
                        self.bench,
                        trial,
                        directory / "eval",
                        self.checker,
                        incremental=self.incremental,
                    )
                    outcome = self.complete_check(
                        cid, directory, signature, bodies, trial, report
                    )
                    outcomes.append(self.with_log(outcome))

            return {
                "status": "checked",
                "outcomes": outcomes,
                "progress": self.progress(),
            }

    def complete_check(self, cid, directory, signature, bodies, trial, report):
        row = self.state["contracts"][cid]
        owner = next(c["module"] for c in self.bench["contracts"] if c["id"] == cid)
        own = report["contracts"][cid]["status"]
        transient = any(
            (
                r["status"] == "infrastructure_error"
                or (
                    r["status"] == "timeout"
                    and r.get("timeout_limit") != "total_check"
                    and r.get("backend", getattr(self.checker, "config", {}).get("backend")) != "a-shared-incremental-v1"
                )
            )
            for r in report["modules"].values()
        )
        # A failed prerequisite is never a failure of this target.
        blocked = own == "blocked_dependency" or transient
        accepted = own == "proved" and all(
            report["contracts"][other]["status"] == "proved"
            for other, record in self.state["contracts"].items()
            if record["proved"]
        )
        # An auxiliary edit can invalidate an existing theorem before
        # reaching this target. Detect that via absence of target audit
        # and the earliest native error location, rather than charging
        # the newly submitted target for the existing theorem's error.
        if own == "build_error" and not blocked:
            from .attempts import finish

            save(
                directory / "submitted.json",
                {
                    "module": owner,
                    "slots": {cid + ":prove": trial["slots"][cid + ":prove"]},
                },
            )
            native = dict(report["modules"][owner])
            native["log"] = (directory / "eval" / (owner + ".log")).read_text()
            finish(
                directory,
                native,
                Path(native.get("source_directory", directory / "eval/source"))
                / (owner + ".thy"),
            )
            location = read(directory / "result.json").get("diagnostic_slot")
            blocked = location is not None and not location.startswith(cid + ":")
        if own == "proved" and not accepted:
            blocked = True
        charged = not blocked
        if charged:
            row["attempts"] += 1
            row["last_candidate"] = signature
        if accepted:
            if report.get("seals") is not None:
                self.state["theory_seals"] = report["seals"]
            row["proved"] = True
            self.state["accepted_bodies"] = bodies
        outcome = {
            "id": cid,
            "request_id": self.state["pending"].get("request_id"),
            "status": "proved"
            if accepted
            else "blocked_or_infrastructure"
            if blocked
            else own,
            "charged": charged,
            "attempt": row["attempts"],
            "candidate_sha256": signature,
            "evidence": str(directory),
        }
        self.record_outcome(outcome)
        row["history"].append(outcome)
        self.state.pop("pending")
        self.persist()
        return outcome

    def with_log(self, outcome):
        directory = Path(outcome["evidence"]) / "eval"
        report = read(directory / "report.json")
        owner = next(
            c["module"] for c in self.bench["contracts"] if c["id"] == outcome["id"]
        )
        logs = "\n".join(
            p.read_text()
            for p in directory.glob("*.log")
            if p.stem == owner
            or report["modules"].get(p.stem, {}).get("status") != "ok"
        )
        return {**outcome, "log": logs}

    def feedback(self, contract=None):
        with self.lock:
            if contract is not None and contract not in self.state["contracts"]:
                raise ValueError("unknown contract")
            return {
                "outcomes": [
                    self.with_log(row["history"][-1])
                    for cid, row in self.state["contracts"].items()
                    if row["history"] and (contract is None or cid == contract)
                ],
                "progress": self.progress(),
            }

    def dispatch(self, payload):
        """A durable request is committed before checking; response before delivery.

        The acknowledgement means the CLI printed/flushed a response, not that
        a model understood it. Missing receipts remain retrievable indefinitely.
        """
        with self.lock:
            command = payload["cmd"]
            if command == "progress":
                result = self.progress()
                result["unacknowledged_requests"] = [
                    p.parent.name
                    for p in sorted((self.root / "requests").glob("*/request.json"))
                    if not p.with_name("ack.json").exists()
                ]
                return result
            if command == "feedback":
                return self.feedback(payload.get("contract"))
            if command not in {"check", "result", "ack"}:
                raise ValueError("unknown controller command")
            rid = payload.get("request_id") or uuid.uuid4().hex
            if not isinstance(rid, str) or not re.fullmatch(
                r"[A-Za-z0-9_-]{1,80}", rid
            ):
                raise ValueError("invalid request ID")
            directory = self.root / "requests" / rid
            response = directory / "response.json"
            if command == "ack":
                if not response.exists():
                    raise ValueError("no completed response to acknowledge")
                save(
                    directory / "ack.json",
                    {
                        "at": time.time(),
                        "meaning": "CLI output flushed; not model comprehension",
                    },
                )
                return {"status": "acknowledged", "request_id": rid}
            request_path = directory / "request.json"
            if command == "check":
                frozen = {
                    "module": payload.get("module"),
                    "artifact": payload["artifact"],
                }
                if request_path.exists():
                    if read(request_path) != frozen:
                        raise ValueError("request ID reused with different input")
                else:
                    validate_artifact(self.bench, frozen["artifact"])
                    save(request_path, frozen)
            elif not request_path.exists():
                raise ValueError("unknown request ID")
            if response.exists():
                return read(response)
            # Recovery/result retrieval also finishes a partially checked batch.
            # Completed target outcomes are replayed, never charged a second time.
            frozen = read(request_path)
            result = self.submit(frozen["artifact"], frozen["module"], request_id=rid)
            result["request_id"] = rid
            save(response, result)
            return result

    @contextmanager
    def serve(self):
        controller = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    request = json.loads(self.rfile.readline())
                    result = controller.dispatch(request)
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    result = {"status": "controller_error", "error": str(exc)}
                try:
                    self.wfile.write((json.dumps(result) + "\n").encode())
                except BrokenPipeError:
                    pass

        with tempfile.TemporaryDirectory(prefix="vero-retries-") as tmp:
            os.chmod(tmp, 0o711)
            endpoint = str(Path(tmp) / "controller.sock")
            with socketserver.UnixStreamServer(endpoint, Handler) as server:
                os.chmod(endpoint, 0o666)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    yield endpoint
                finally:
                    server.shutdown()
                    thread.join()
                    if hasattr(self.checker, "close"):
                        self.checker.close()


def request(endpoint, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(endpoint)
        stream = client.makefile("rwb")
        stream.write((json.dumps(payload) + "\n").encode())
        stream.flush()
        return json.loads(stream.readline())
