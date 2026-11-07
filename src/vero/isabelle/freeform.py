"""Plain Codex experiment using ordinary files and the shared native checker.

Only transport, independent checking and VERO-compatible accounting are imposed;
there is no model planner, proof graph, proof-slot renderer or search policy.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from .agent import CodexAgent
from .benchmark import NAME, digest, read, save, validate
from .checker import Checker, filled
from .freeform_sources import ContextError, render, snapshot
from .retry_policy import ContractRetries
from .run_metrics import (
    TraceChecker,
    checker_description,
    requires_build,
    segment,
    setup_metrics,
)


def support(bench, reference):
    return (
        Path(__file__)
        .with_name("Freeform_Support.thy")
        .read_text()
        .replace("IMPORT_BASE", bench["base_import"])
        .replace("REFERENCE_DIRECTORY", str(Path(reference).resolve()))
    )


def prepare_reference(bench, checker, root):
    """Elaborate in the parent context; no target theorem is proved or exported."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / "Freeform_Support.thy").write_text(support(bench, root))
    chunks = [
        "theory Frozen_Contracts imports Freeform_Support begin",
        'ML \\<open>writeln "VERO_THEORY_ENTER Frozen_Contracts"\\<close>',
    ]
    active_context = []
    for c in bench["contracts"]:
        if c["context"] != active_context:
            chunks.extend("end" for _ in active_context)
            chunks.extend(c["context"])
            active_context = c["context"]
        chunks.append(
            NAME.sub(
                lambda m, c=c: 'contract_reference "' + c["id"] + '" ' + c["id"],
                c["statement"],
                count=1,
            )
        )
        chunks.append("oops")
    chunks.extend("end" for _ in active_context)
    chunks.append("end")
    (root / "Frozen_Contracts.thy").write_text("\n\n".join(chunks) + "\n")
    # The trusted checker user writes only the reference exports, not the run ledger.
    if checker.config.get("command_prefix"):
        os.chmod(root, 0o777)
    reference_timeout = (
        checker.config.get("initial_statement_timeout_seconds", 1800)
        if requires_build(checker)
        else 600
    )
    result = checker.check(root, "Frozen_Contracts", timeout=reference_timeout)
    (root / "preflight.log").write_text(result["log"])
    hashes = {
        c["id"]: hashlib.sha256((root / (c["id"] + ".yxml")).read_bytes()).hexdigest()
        for c in bench["contracts"]
        if (root / (c["id"] + ".yxml")).exists()
    }
    if checker.config.get("command_prefix"):
        os.chmod(root, 0o755)
    if result["status"] != "ok" or len(hashes) != len(bench["contracts"]):
        save(root / "preflight.json", result)
        raise ValueError("frozen-contract elaboration failed: " + result["log"][-5000:])
    for term_file in root.glob("*.yxml"):
        term_file.chmod(0o444)
    save(
        root / "reference.json",
        {
            "benchmark_id": bench["benchmark_id"],
            "terms": hashes,
            "elapsed_seconds": result["elapsed_seconds"],
            "status": "ok",
        },
    )
    return hashes


class FreeformController(ContractRetries):
    """Reuse the existing socket transport and progress shape; freeze free sources."""

    budget_policy = "candidate-v2"
    snapshot = staticmethod(snapshot)

    def __init__(self, bench, root, checker, workspace, reference, telemetry=None):
        self.telemetry = telemetry
        self.bench, self.root, self.checker = bench, Path(root), checker
        self.workspace, self.reference = Path(workspace), Path(reference)
        self.limit, self.lock = 6, threading.RLock()
        self.path = self.root / "state.json"
        self.ids = {c["id"] for c in bench["contracts"]}
        refs = read(self.reference / "reference.json")
        if refs["benchmark_id"] != bench["benchmark_id"]:
            raise ValueError("frozen reference binding changed")
        for cid, expected in refs["terms"].items():
            if (
                hashlib.sha256(
                    (self.reference / (cid + ".yxml")).read_bytes()
                ).hexdigest()
                != expected
            ):
                raise ValueError("frozen reference changed")
        self.binding = {
            "benchmark_id": bench["benchmark_id"],
            "max_attempts": 6,
            "references": refs["terms"],
            "checker": checker.config,
            "budget_policy": self.budget_policy,
            "implementation": digest(
                {
                    p.name: p.read_text()
                    for p in Path(__file__).parent.iterdir()
                    if p.suffix in {".py", ".thy", ".md", ".ML", ".json"}
                }
            ),
        }
        if self.path.exists():
            self.state = read(self.path)
            if self.state["identity"] != self.binding:
                raise ValueError("freeform experiment binding changed")
        else:
            self.state = {
                "identity": self.binding,
                "contracts": {
                    cid: {"attempts": 0, "proved": False, "history": []}
                    for cid in sorted(self.ids)
                },
                "next_check": 0,
                "theory_seals": {},
            }
            self.persist()
        self.flush_metrics()
        pending = self.state.get("pending")
        if pending:
            directory = Path(pending["directory"])
            if (directory / "outcome.json").exists():
                self.commit(read(directory / "outcome.json"))
            else:
                outcome = {
                    "id": pending["id"],
                    "request_id": pending["request_id"],
                    "evidence": str(directory),
                    "attempt": pending["attempt"],
                    "charged": pending["charged"],
                    "budget_reserved": True,
                    "infrastructure_retry": pending["infrastructure_retry"],
                    "status": "infrastructure_error",
                    "recoverable": True,
                    "log": "Checker interrupted; the candidate slot remains reserved.",
                }
                save(directory / "outcome.json", outcome)
                self.commit(outcome)

    def progress(self):
        with self.lock:
            result = super().progress()
            retry_requests = []
            for cid, row in self.state["contracts"].items():
                last = row["history"][-1] if row["history"] else {}
                if (
                    not row["proved"]
                    and last.get("recoverable")
                    and last.get("infrastructure_retry", 0) < 2
                ):
                    retry_requests.append(last["request_id"])
                    result["contracts"][cid]["infrastructure_retry_request"] = last[
                        "request_id"
                    ]
                    if row["attempts"] >= self.limit:
                        result["contracts"][cid]["status"] = "recovery_pending"
            result["retry_requests"] = sorted(set(retry_requests))
            result["all_finished"] = (
                result["all_finished"]
                and not retry_requests
                and not self.state.get("pending")
            )
            result["budget_policy"] = self.budget_policy
            return result

    def feedback(self, contract=None):
        rows = self.state["contracts"]
        return {
            "outcomes": [
                self.with_log(r["history"][-1])
                for cid, r in rows.items()
                if r["history"] and (contract is None or cid == contract)
            ]
        }

    def with_log(self, outcome):
        p = Path(outcome["evidence"]) / "native.log"
        return {
            **outcome,
            "log": p.read_text()
            if p.exists()
            else outcome.get("log", outcome.get("error", "")),
        }

    def commit(self, outcome):
        row = self.state["contracts"][outcome["id"]]
        if not any(h["evidence"] == outcome["evidence"] for h in row["history"]):
            if outcome["charged"] and not outcome.get("budget_reserved"):
                row["attempts"] += 1
            if outcome["status"] == "proved":
                row["proved"] = True
                row["accepted_evidence"] = outcome["evidence"]
            self.record_outcome(outcome)
            row["history"].append(outcome)
        self.state.pop("pending", None)
        self.persist()

    def check_snapshot(self, frozen, cid, directory):
        if cid not in frozen["contracts"]:
            return {
                "status": "unfilled",
                "charged": False,
                "log": "No ordinary lemma/theorem named " + cid + " is present.",
            }
        declaration = frozen["contracts"][cid]
        original = frozen["modules"][declaration["module"]]["text"]
        if not filled(original[declaration["proof_start"] : declaration["end"]]):
            return {
                "status": "unfilled",
                "charged": False,
                "log": "Empty/oops candidate cannot prove the target.",
            }
        accepted = set(frozen["accepted_contracts"])
        source = directory / "source"
        contents = render(frozen, source, accepted, cid)
        (source / "Freeform_Support.thy").write_text(
            support(self.bench, self.reference)
        )
        owner = frozen["contracts"][cid]["module"]
        closure = set()

        def visit(name):
            if name not in closure:
                closure.add(name)
                for dep in frozen["modules"][name]["dependencies"]:
                    visit(dep)

        visit(owner)
        key = digest(
            {
                "binding": self.binding,
                "sources": {n: contents[n] for n in sorted(closure)},
            }
        )
        cache = self.root / "cache" / (key + ".json")
        if cache.exists():
            result = {**read(cache), "cache_hit": True, "elapsed_seconds": 0.0}
        else:
            result = {**self.checker.check(source, owner), "cache_hit": False}
        audited = cid + ":prove" in result.get("audited", [])
        status = "proved" if result["status"] == "ok" and audited else result["status"]
        if status == "ok":
            status = "missing_audit"
        # Every new candidate is budgeted before checking. Only the same frozen
        # candidate can be retried for infrastructure failure, under a small cap.
        recoverable = result.get("failure_kind") != "stack_exhausted" and (
            result["status"] == "infrastructure_error"
            or (
                result["status"] == "timeout"
                and result.get("timeout_phase") == "dependency_loading"
                and result.get("timeout_limit") != "total_check"
            )
        )
        if status == "proved":
            save(cache, result)
        return {
            **result,
            "status": status,
            "recoverable": recoverable,
            "source_sha256": key,
            "module": owner,
        }

    def submit_one(self, frozen, cid, rid, *, retry=False):
        row = self.state["contracts"][cid]
        prior = next(
            (h for h in reversed(row["history"]) if h.get("request_id") == rid), None
        )
        if prior:
            if not retry or row["proved"] or not prior.get("recoverable"):
                return self.with_log(prior)
            recovery = prior.get("infrastructure_retry", 0) + 1
            if recovery > 2:
                return {**self.with_log(prior), "recovery_limit_reached": True}
            attempt, charged = prior["attempt"], False
            frozen = read(Path(prior["evidence"]) / "snapshot.json")
        else:
            if row["proved"] or row["attempts"] >= 6:
                return {
                    "id": cid,
                    "status": "already_proved" if row["proved"] else "exhausted",
                    "charged": False,
                }
            attempt, charged, recovery = row["attempts"] + 1, True, 0
            frozen = {
                **frozen,
                "accepted_contracts": sorted(
                    c for c, r in self.state["contracts"].items() if r["proved"]
                ),
            }
        # Reserve durably before invoking native Isabelle; a crash cannot make a
        # generated candidate disappear from the six-candidate budget.
        number = self.state["next_check"]
        while True:
            directory = self.root / f"check-{number:06d}"
            try:
                directory.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                # Preserve orphan evidence from a pre-reservation crash. No
                # native check ran until pending was durably saved below.
                number += 1
        self.state["next_check"] = number + 1
        save(directory / "snapshot.json", frozen)
        if charged:
            row["attempts"] = attempt
        self.state["pending"] = {
            "id": cid,
            "directory": str(directory),
            "request_id": rid,
            "attempt": attempt,
            "charged": charged,
            "infrastructure_retry": recovery,
        }
        self.record_metric(
            "candidate_reserved",
            event_id="candidate-reserved:" + str(directory),
            node_id=cid,
            node_kind="helper" if cid.startswith("h") else "contract",
            request_id=rid,
            attempt=attempt,
            charged=charged,
            infrastructure_retry=recovery,
            evidence_id=str(directory),
            stage=row.get("stage"),
            cumulative_attempt=row.get("cumulative_attempts", attempt),
        )
        self.persist()
        if frozen.get("snapshot_error"):
            result = {
                "status": "invalid_context"
                if frozen.get("invalid_context")
                else "rejected",
                "charged": charged,
                "log": frozen["snapshot_error"],
            }
        else:
            result = self.check_snapshot(frozen, cid, directory)
        result["charged"] = charged
        (directory / "native.log").write_text(result.get("log", ""))
        save(directory / "native.json", result)
        outcome = {k: v for k, v in result.items() if k != "log"}
        outcome.update(
            {
                "id": cid,
                "request_id": rid,
                "evidence": str(directory),
                "attempt": attempt,
                "charged": charged,
                "budget_reserved": True,
                "infrastructure_retry": recovery,
            }
        )
        save(directory / "outcome.json", outcome)
        self.commit(outcome)
        if outcome["status"] == "proved":
            self.seal_snapshot(frozen, directory / "sealing")
        return self.with_log(outcome)

    def dispatch(self, payload):
        with self.lock:
            command = payload["cmd"]
            if command == "progress":
                result = self.progress()
                result["unacknowledged_requests"] = [
                    p.parent.name
                    for p in (self.root / "requests").glob("*/request.json")
                    if not p.with_name("ack.json").exists()
                ]
                return result
            if command == "feedback":
                return self.feedback(payload.get("contract"))
            rid = payload.get("request_id", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
                raise ValueError("invalid request ID")
            d = self.root / "requests" / rid
            if command == "ack":
                if not (d / "response.json").exists():
                    raise ValueError("no response")
                save(d / "ack.json", {"at": time.time()})
                return {"status": "acknowledged"}
            if command == "check":
                ids = payload["contracts"]
                if (
                    not ids
                    or len(set(ids)) != len(ids)
                    or any(cid not in self.ids for cid in ids)
                ):
                    raise ValueError("expected unique known contract IDs")
                try:
                    if payload.get("source_error"):
                        raise ValueError(payload["source_error"])
                    frozen = self.snapshot(
                        self.workspace,
                        self.ids,
                        self.bench["base_import"],
                        payload.get("files"),
                    )
                except (ValueError, OSError) as exc:
                    frozen = {
                        "snapshot_error": str(exc),
                        "invalid_context": isinstance(exc, ContextError),
                    }
                req = {"contracts": ids, "snapshot": frozen}
                if (d / "request.json").exists() and read(d / "request.json") != req:
                    raise ValueError(
                        "request ID reused with changed input; use result to recover"
                    )
                save(d / "request.json", req)
            elif command not in {"result", "retry"}:
                raise ValueError("unknown command")
            if (d / "response.json").exists() and command != "retry":
                return read(d / "response.json")
            req = read(d / "request.json")
            outcomes = []
            for cid in req["contracts"]:
                if (self.root.parent / "STOP_REQUEST.json").exists():
                    break
                outcomes.append(
                    self.submit_one(req["snapshot"], cid, rid, retry=command == "retry")
                )
            result = {
                "request_id": rid,
                "outcomes": outcomes,
                "progress": self.progress(),
            }
            save(d / "response.json", result)
            return result

    def seal_snapshot(self, frozen, directory):
        accepted = {
            cid for cid, row in self.state["contracts"].items() if row["proved"]
        }
        source = directory / "source"
        contents = render(frozen, source, accepted)
        (source / "Freeform_Support.thy").write_text(
            support(self.bench, self.reference)
        )
        seals = {}
        keys = {}
        for name in frozen["order"]:
            module = frozen["modules"][name]
            own = {c["id"] for c in module["contracts"]}
            keys[name] = digest(
                {
                    "binding": self.binding,
                    "source": contents[name],
                    "imports": {d: keys[d] for d in module["dependencies"]},
                }
            )
            if not own <= accepted:
                seals[name] = {"status": "incomplete", "key": keys[name]}
                continue
            if any(seals[d]["status"] != "sealed" for d in module["dependencies"]):
                seals[name] = {"status": "waiting_for_imports", "key": keys[name]}
                continue
            cache = self.root / "seals" / (keys[name] + ".json")
            if cache.exists():
                seals[name] = {**read(cache), "cache_hit": True}
                if seals[name].get("sealed_at") is not None:
                    self.record_metric(
                        "theory_sealed",
                        event_id="seal:" + keys[name],
                        timestamp=seals[name]["sealed_at"],
                        theory=name,
                        contract_ids=sorted(n for n in own if n.startswith("c")),
                        helper_ids=sorted(n for n in own if n.startswith("h")),
                        evidence_id=seals[name]["evidence"],
                        seal_key=keys[name],
                        build_required=requires_build(self.checker),
                    )
                continue
            result = self.checker.check(source, name)
            log = directory / (name + ".log")
            log.write_text(result["log"])
            valid = result["status"] == "ok" and {cid + ":prove" for cid in own} <= set(
                result.get("audited", [])
            )
            build = None
            if valid and requires_build(self.checker):
                build = self.checker.build_session(source, [name])
                (directory / (name + ".build.log")).write_text(build.get("log", ""))
                valid = build["status"] == "ok"
            seal = {
                "status": "sealed" if valid else "seal_failed",
                "key": keys[name],
                "evidence": str(log),
                "cache_hit": False,
                "native": {k: v for k, v in result.items() if k != "log"},
                "build": {k: v for k, v in build.items() if k != "log"}
                if build
                else None,
            }
            if valid:
                seal["sealed_at"] = time.time()
                self.record_metric(
                    "theory_sealed",
                    event_id="seal:" + keys[name],
                    timestamp=seal["sealed_at"],
                    theory=name,
                    contract_ids=sorted(n for n in own if n.startswith("c")),
                    helper_ids=sorted(n for n in own if n.startswith("h")),
                    evidence_id=str(log),
                    seal_key=keys[name],
                    build_required=requires_build(self.checker),
                )
                save(cache, seal)
            seals[name] = seal
        self.state["theory_seals"] = seals
        self.persist()
        save(directory / "seals.json", seals)
        return seals

    def integrate(self, frozen=None):
        if frozen is None:
            frozen = snapshot(self.workspace, self.ids, self.bench["base_import"])
        if set(frozen["contracts"]) != self.ids:
            return {
                "status": "incomplete_inventory",
                "missing": sorted(self.ids - set(frozen["contracts"])),
            }
        directory = self.root.parent / ("integration-" + str(time.time_ns()))
        source = directory / "source"
        render(frozen, source, self.ids)
        (source / "Freeform_Support.thy").write_text(
            support(self.bench, self.reference)
        )
        imports = " ".join(frozen["order"])
        (source / "Freeform_Integration.thy").write_text(
            "theory Freeform_Integration imports "
            + imports
            + ' begin\nML \\<open>writeln "VERO_THEORY_ENTER Freeform_Integration"\\<close>\nend\n'
        )
        result = self.checker.check(source, "Freeform_Integration")
        (directory / "native.log").write_text(result["log"])
        result["complete"] = result["status"] == "ok" and self.ids <= {
            x.split(":")[0] for x in result["audited"]
        }
        if result["complete"] and requires_build(self.checker):
            build = self.checker.build_session(
                source,
                [*frozen["order"], "Freeform_Integration"],
                timeout=self.checker.config.get(
                    "integration_build_timeout_seconds", 900
                ),
            )
            (directory / "build.log").write_text(build.get("log", ""))
            result["build"] = {k: v for k, v in build.items() if k != "log"}
            result["complete"] = build["status"] == "ok"
            if not result["complete"]:
                result["status"] = "integration_build_failed"
        save(directory / "report.json", result)
        self.state["integration"] = {
            "evidence": str(directory),
            **{k: v for k, v in result.items() if k != "log"},
        }
        self.record_metric(
            "repository_integration",
            event_id="integration:" + str(directory),
            complete=result["complete"],
            status=result["status"],
            evidence_id=str(directory),
            build_required=requires_build(self.checker),
        )
        self.persist()
        return self.state["integration"]


def instruction(bench, checker_config=None, agent_config=None):
    limits = checker_config or {}
    agent_identity = (
        f" ({agent_config['model']}, {agent_config['reasoning_effort']})"
        if agent_config is not None
        else ""
    )
    return f"""Prove all {len(bench["contracts"])} fixed contracts in contracts.json using Isabelle.
You are a general coding agent{agent_identity}. Organize the task, files, theories, imports,
helpers and proof search yourself. You may freely create, edit and delete files
within /sandbox, including subdirectories. A theory may contain one, many or all
contracts. There is no supplied PLAN, proof graph or fixed submission slot.

Write ordinary .thy sources importing {bench["base_import"]} or other theories
from this workspace. The filename must match the theory name; theory names use
letters, digits and underscores. For attribution, name each target theorem by
its fixed ID (lemma c0001: ...); use its fixed statement and original context
from contracts.json, replacing only the binding. The checker independently
certifies the resulting theorem against the original parent-context proposition.
Helpers may have any other names. Use normal Isar and native proof methods;
raw ML, new axioms, sorry, syntax hooks and custom executable methods are outside
the trusted checker's accepted language. There is no restriction to a helper slot.

Only the parent session's premises/definitions and this experiment's generated
content are available. The parent catalog is /public/premises.jsonl; its sanitized
sources are /semantic-base and /sel4-project/verification/l4v. There are no
historical generated proofs, previous plans, old experiment diagnostics or target
proof sources. Do not retrieve them via network, history or external tools.

Use the trusted check command for every proof experiment; do not run a second
Isabelle checker or construct a substitute evaluator, which would bypass counts:
  python3 /backend/freeform_tool.py check c0001 [c0002 ...]
  python3 /backend/freeform_tool.py progress
  python3 /backend/freeform_tool.py feedback c0001
  python3 /backend/freeform_tool.py search 'text' --limit 20
  python3 /backend/freeform_tool.py result REQUEST_ID
  python3 /backend/freeform_tool.py retry REQUEST_ID
The submission freezes current files. Multiple IDs are checked independently in
the order you give. Other pending contract proofs are removed from each native
check; unproved contracts never become premises. Generated helpers and accepted
contracts may be reused. Owner checks stop at the submitted target's completed
proof, closing its contexts; later unfinished targets do not invalidate it.

Each contract permits at most six submitted candidates: initial generation and
five adjustments. Every new check request reserves a slot BEFORE checking,
including repeated candidates, empty/rejected sources, helper failures, owner
proof timeouts and stack/resource exhaustion. Do not generate a seventh candidate.
A successful contract closes. Counters survive restarts and theory regrouping.
For a lost reply use result REQUEST_ID; do not make a fresh check request.
For recoverable infrastructure errors or dependency-loading timeouts, retry
REQUEST_ID checks exactly the saved sources, at most twice, without a new slot.
It cannot check edits or retry owner timeouts/resource exhaustion for free.
Changed candidates always require a new check request and consume another slot.
Check progress and feedback for authoritative outcomes.

{checker_description(limits)}
Full theory and repository integration are required to report repository success.
Keep accepted results available for final integration. Source bytes and diagnostic
records are preserved independently of editable files.

There is no total time limit. Continue until every contract has either succeeded
or exhausted the initial attempt plus five retries. Passive snapshots do not
interrupt you. A normal reply does not end the experiment. If infrastructure
prevents progress, report that accurately; it is not a mathematical failure.
"""


def validate_agent_config(agent_config):
    """Require an explicit model; preserve it verbatim rather than selecting a fallback."""
    model = agent_config.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("frozen experiment requires an explicit nonempty model")
    if agent_config.get("reasoning_effort") != "medium":
        raise ValueError("frozen experiment requires medium reasoning effort")


def run(
    bench_path,
    config_path,
    root,
    resume=False,
    *,
    controller_class=FreeformController,
    instruction_builder=instruction,
):
    cfg, bench = read(config_path), validate(read(bench_path))
    validate_agent_config(cfg["agent"])
    if cfg.get("max_contract_retries") != 5:
        raise ValueError("frozen experiment requires six attempts")
    telemetry = setup_metrics(
        root, cfg, bench, cfg.get("instruction_profile", "codex_freeform")
    )
    with segment(telemetry, resume=resume) as lifecycle:
        result = _run(
            bench_path,
            config_path,
            root,
            resume,
            controller_class=controller_class,
            instruction_builder=instruction,
            telemetry=telemetry,
        )
        lifecycle["status"] = result["status"]
        return result


def _run(
    bench_path,
    config_path,
    root,
    resume=False,
    *,
    controller_class=FreeformController,
    instruction_builder=instruction,
    telemetry=None,
):
    bench, cfg, root = validate(read(bench_path)), read(config_path), Path(root)
    validate_agent_config(cfg["agent"])
    if cfg.get("max_contract_retries") != 5:
        raise ValueError("frozen experiment requires six attempts")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        workspace = Path(cfg["sandbox"])
        workspace.mkdir(parents=True, exist_ok=True)
        binding = {
            "benchmark_id": bench["benchmark_id"],
            "config": cfg,
            "implementation": digest(
                {
                    p.name: p.read_text()
                    for p in Path(__file__).parent.iterdir()
                    if p.suffix in {".py", ".thy", ".md", ".ML", ".json"}
                }
            ),
        }
        if (root / "binding.json").exists():
            if not resume or read(root / "binding.json") != binding:
                raise ValueError("resume/binding mismatch")
        else:
            if any(workspace.iterdir()):
                raise ValueError(
                    "fresh freeform run requires an empty generated workspace"
                )
            save(root / "binding.json", binding)
            save(
                workspace / "contracts.json",
                {
                    "base_import": bench["base_import"],
                    "contracts": [
                        {k: v for k, v in c.items() if k != "module"}
                        for c in bench["contracts"]
                    ],
                },
            )
            (workspace / "INSTRUCTION.md").write_text(
                instruction_builder(bench, cfg["checker"], cfg["agent"])
            )
            if cfg.get("sandbox_uid") is not None:
                for p in [workspace, *workspace.iterdir()]:
                    os.chown(p, cfg["sandbox_uid"], cfg["sandbox_uid"])
        checker = TraceChecker(Checker(cfg["checker"]), telemetry, root / "native-logs")
        controller = controller_class(
            bench,
            root / "contract-retries",
            checker,
            workspace,
            cfg["reference_directory"],
            telemetry=telemetry,
        )
        journal_path = root / "iterations.json"
        journal = (
            read(journal_path)
            if journal_path.exists()
            else {"status": "running", "iterations": []}
        )
        with controller.serve() as endpoint:
            while not controller.progress()["all_finished"]:
                if (root / "STOP_REQUEST.json").exists():
                    journal["status"] = "operator_stopped"
                    break
                index = len(journal["iterations"])
                iteration = root / f"iter-{index:03d}"
                config = {
                    **cfg["agent"],
                    **({"metrics": cfg["metrics"]} if cfg.get("metrics") else {}),
                    "timeout_seconds": None,
                    "persistent_session": True,
                    "resume_session": bool(
                        index and journal["iterations"][-1]["agent"].get("ok")
                    ),
                    "env": {**cfg["agent"].get("env", {}), "FREEFORM_SOCKET": endpoint},
                }
                stop = threading.Event()

                def sample(stop=stop):
                    while not stop.is_set():
                        state = read(controller.path)
                        save(
                            root / "heartbeat.json",
                            {
                                "at": time.time(),
                                "status": "running",
                                "proved": sum(
                                    r["proved"] for r in state["contracts"].values()
                                ),
                                "attempts": sum(
                                    r.get("cumulative_attempts", r["attempts"])
                                    for r in state["contracts"].values()
                                ),
                                "exhausted": sum(
                                    not r["proved"] and r["attempts"] >= 6
                                    for r in state["contracts"].values()
                                ),
                                "pending": state.get("pending"),
                                "helper_count": sum(
                                    n.get("kind") == "helper"
                                    for n in state.get("helper_stages", {})
                                    .get("nodes", {})
                                    .values()
                                ),
                            },
                        )
                        stop.wait(cfg.get("sample_interval_seconds", 120))

                thread = threading.Thread(target=sample, daemon=True)
                thread.start()

                def monitor():
                    if (
                        root / "STOP_REQUEST.json"
                    ).exists() and not controller.state.get("pending"):
                        return "operator_stop"
                    if controller.progress()[
                        "all_finished"
                    ] and not controller.state.get("pending"):
                        return "contract_policy_finished"
                    return None

                try:
                    outcome = CodexAgent(config, iteration / "agent").run(
                        sandbox_dir=workspace,
                        instruction_file=workspace / "INSTRUCTION.md",
                        monitor=monitor,
                    )
                finally:
                    stop.set()
                    thread.join()
                journal["iterations"].append({"agent": outcome, "iteration": index})
                journal["status"] = "running"
                save(journal_path, journal)
                if outcome.get("stop_reason") == "operator_stop":
                    journal["status"] = "operator_stopped"
                    break
                if not outcome.get("ok") and not controller.progress()["all_finished"]:
                    journal["status"] = "agent_error"
                    break
            if controller.progress()["all_finished"]:
                if controller.progress()["all_proved"]:
                    integration = controller.integrate()
                    journal["status"] = (
                        "complete"
                        if integration.get("complete")
                        else "integration_error"
                    )
                else:
                    journal["status"] = "retries_exhausted"
            journal["progress"] = controller.progress()
            save(journal_path, journal)
            save(
                root / "heartbeat.json",
                {
                    "at": time.time(),
                    "status": journal["status"],
                    **controller.progress(),
                },
            )
        return journal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare-reference", "run"])
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare-reference":
        cfg = read(args.config)
        result = prepare_reference(
            validate(read(args.benchmark)), Checker(cfg["checker"]), args.output
        )
        print(json.dumps({"contracts": len(result), "status": "ok"}))
    else:
        print(json.dumps(run(args.benchmark, args.config, args.output, args.resume)))


if __name__ == "__main__":
    main()
