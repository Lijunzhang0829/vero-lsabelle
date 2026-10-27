"""Current A transactional batch kernel with VERO's frozen proposition audit.

The ML kernel and XML validator are exact source copies from A. The Python
adapter owns local process isolation, immutable import loading and resource
deadlines. A native GREEN is accepted only with both the exact transaction XML
and the existing trusted contract proposition audit. Declarations, complete
theories and session builds retain A's full-process certification path.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from .a_batch_audit import PROTOCOL, validate_native_audit
from .shared_native import (
    CERTIFICATION_FACT,
    CORE_DIR,
    TARGET_ID,
    LocalWorker,
    SharedNativeBackend,
    audit_markers,
    digest,
    owned_file,
    prefix_parts,
    source_request,
)

BACKEND = "a-shared-batch-v2"


def core_identity():
    manifest = json.loads((CORE_DIR / "a_batch_core.json").read_text())
    for name, entry in manifest["files"].items():
        if (
            hashlib.sha256((CORE_DIR / name).read_bytes()).hexdigest()
            != entry["sha256"]
        ):
            raise ValueError("shared Isabelle batch core changed: " + name)
    return digest(manifest)


class BatchTargetManifestError(ValueError):
    """An invalid trusted batch boundary must never fall back to acceptance."""


def target_manifest(directory, request):
    path = Path(directory) / "batch-targets.json"
    if not path.exists() and not path.is_symlink():
        return [request["target"]]
    if path.is_symlink():
        raise BatchTargetManifestError("batch target manifest must not be a symlink")
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise BatchTargetManifestError(
            "cannot read trusted batch target manifest"
        ) from error
    targets = value.get("target_ids") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or set(value) != {"target_ids"}
        or not isinstance(targets, list)
        or not targets
        or any(
            not isinstance(item, str) or not re.fullmatch(r"[ch]\d{4}", item)
            for item in targets
        )
        or len(set(targets)) != len(targets)
        or targets[0] != request["target"]
        or request["polarity"] != "prove"
    ):
        raise BatchTargetManifestError("invalid trusted batch target identities")
    return targets


class LocalBatchWorker(LocalWorker):
    """A's check/commit protocol over the existing isolated local launcher."""

    core_filename = "a_batch_core.ML"

    def protocol_files(self, config):
        return {
            "prefix": self.prefix,
            "prefix_facts": CERTIFICATION_FACT,
            "worker_id": self.name,
            "imports": "\n".join(self.imports),
            "theory_name": self.label,
            "command_timeout": str(config.get("command_timeout_seconds", 900)),
            "candidate_timeout": str(config.get("candidate_timeout_seconds", 120)),
        }

    def __init__(self, config, request, root, deadline, process_registry=None):
        self.pending = None
        super().__init__(config, request, root, deadline, process_registry)
        self.native_imports = (self.root / "imports").read_text().splitlines()
        self.native_label = (self.root / "theory_name").read_text()

    def wait(self, path, timeout=None):
        started = time.monotonic()
        limit = self.checker.timeout if timeout is None else timeout
        deadline = min(started + limit, self.checker.request_deadline)
        while not path.exists():
            if self.process.poll() is not None:
                raise RuntimeError((self.root / "worker.log").read_text()[-6000:])
            if time.monotonic() >= deadline:
                error = TimeoutError("batch worker timeout waiting for " + path.name)
                error.timeout_seconds = limit
                error.timeout_scope = (
                    "prefix-startup" if path.name == "ready" else "candidate-watchdog"
                )
                error.worker_log = (self.root / "worker.log").read_text()
                self.close()
                raise error
            time.sleep(0.05)

    def transaction(self, action, text="", names=(CERTIFICATION_FACT,)):
        self.index += 1
        stem = str(self.index)
        offset = (self.root / "worker.log").stat().st_size
        for suffix, value in (
            ("action", action),
            ("facts", "\n".join(names)),
            ("line", "1"),
        ):
            owned_file(self.root / (stem + "." + suffix), value, self.checker.config)
        temporary = owned_file(self.root / (stem + ".tmp"), text, self.checker.config)
        temporary.rename(self.root / (stem + ".request"))
        limit = (
            self.checker.config.get("candidate_timeout_seconds", 120) + 10
            if action == "check"
            else self.checker.timeout
        )
        self.wait(self.root / (stem + ".response"), timeout=limit)
        answer = (self.root / (stem + ".response")).read_text()
        with (self.root / "worker.log").open("rb") as stream:
            stream.seek(offset)
            log = stream.read().decode(errors="replace")
        evidence = {}
        if answer == "GREEN":
            content = (self.root / (stem + ".audit.xml")).read_text()
            evidence = {
                "audit_content": content,
                "audit_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "request_id": self.index,
                "worker": self.name,
            }
            validate_native_audit(
                evidence,
                prefix=self.prefix,
                delta=text,
                names=names,
                imports=self.native_imports,
                label=self.native_label,
            )
        return answer, log, evidence

    def check(self, body):
        started = time.monotonic()
        self.pending = None
        try:
            answer, log, audit = self.transaction("check", body)
            timed_out = answer.startswith("TIMEOUT\n")
            timeout_scope = "candidate-execute-and-audit" if timed_out else None
        except TimeoutError as error:
            answer, log, audit = "TIMEOUT\n" + str(error), error.worker_log, {}
            timed_out, timeout_scope = True, error.timeout_scope
        self.pending = self.prefix + body if answer == "GREEN" else None
        log_path = self.root / (str(self.index) + ".log")
        owned_file(log_path, log + "\n" + answer, self.checker.config)
        proof_state = self.root / (str(self.index) + ".proof-state.xml")
        return {
            **audit,
            "verdict": "green" if answer == "GREEN" else "red",
            "returncode": 0 if answer == "GREEN" else 124 if timed_out else 1,
            "diagnostic": answer,
            "command_log": str(log_path),
            "wall_ms": round((time.monotonic() - started) * 1000),
            "timed_out": timed_out,
            "timeout_scope": timeout_scope,
            "timeout_seconds": self.checker.config.get(
                "candidate_timeout_seconds", 120
            ),
            "native_proof_state_xml": proof_state.read_text()
            if proof_state.exists()
            else None,
        }

    def align(self, source, node_id, fact):
        label, _, prefix, _ = prefix_parts(source, node_id, self.imports)
        if label != self.label or not prefix.startswith(self.prefix):
            return False
        if prefix == self.prefix:
            return True
        # B may remove the prior target marker when publishing its certified
        # prefix. Recheck the exact committed delta with A's kernel before
        # promotion; no result from a failed/speculative candidate is imported.
        if prefix != self.pending:
            trial = self.check(prefix[len(self.prefix) :])
            if trial["verdict"] != "green":
                return False
            self.prefix_audited = list(
                dict.fromkeys(
                    [
                        *self.prefix_audited,
                        *audit_markers(Path(trial["command_log"]).read_text()),
                    ]
                )
            )
        answer, _, _ = self.transaction("commit")
        if answer != "COMMITTED":
            raise RuntimeError("native batch commit failed: " + answer)
        self.prefix, self.pending = prefix, None
        return True


class BatchNativeBackend(SharedNativeBackend):
    def __init__(self, config, cold_check):
        config = {
            "threads": 4,
            "timeout_seconds": 900,
            "command_timeout_seconds": 900,
            "candidate_timeout_seconds": 120,
            **config,
        }
        expected = config.get("shared_core_sha256")
        super().__init__(
            {k: v for k, v in config.items() if k != "shared_core_sha256"}, cold_check
        )
        self.config = dict(config)
        self.core_sha256 = core_identity()
        if expected is not None and expected != self.core_sha256:
            raise ValueError("frozen shared Isabelle batch core identity mismatch")

    def _metadata(self):
        return {
            "backend": BACKEND,
            "native_protocol": PROTOCOL,
            "shared_core_sha256": self.core_sha256,
            "command_timeout_seconds": self.config.get("command_timeout_seconds", 900),
            "candidate_timeout_seconds": self.config.get(
                "candidate_timeout_seconds", 120
            ),
            "source_qualifier": self.config.get("source_qualifier", "Generated_Refine"),
        }

    def build_session(
        self, directory, theories, session_name="Generated_Refine", timeout=None
    ):
        # Match A's native resource lease: release a resident proof worker
        # before the full session build consumes the native slot.
        self.close()
        return super().build_session(directory, theories, session_name, timeout)

    def check(self, directory, owner, timeout=None):
        started_at, started = time.time(), time.monotonic()
        remaining = self._resource_remaining()
        whole_cap = self.config.get("total_timeout_seconds")
        deadline = started + min(
            remaining, float("inf") if whole_cap is None else whole_cap
        )
        if timeout is not None:
            deadline = min(deadline, started + timeout)
        reason = None
        is_candidate = False
        try:
            request = source_request(directory, owner)
            is_candidate = True
            targets = target_manifest(directory, request)
            if request["prefix_depth"] != 0:
                # A's initial batch audit requires a theory boundary. Keep the
                # same native context by checking the entire body as one delta.
                # Imports remain warm; preceding proofs are re-executed.
                _, _, prefix, _ = prefix_parts(
                    request["synthetic"], TARGET_ID, request["imports"]
                )
                request["candidate"] = prefix + request["candidate"]
                request["synthetic"] = request["synthetic"].replace(
                    "\nbegin\n" + prefix, "\nbegin\n", 1
                )
                request["prefix_depth"] = 0
            reused = advanced = False
            worker = self.worker
            if worker is not None:
                worker.checker.request_deadline = deadline
                same_context = (
                    not worker.closed
                    and worker.binding == request["closure_sha256"]
                    and worker.label == owner
                    and worker.imports == tuple(request["imports"])
                )
                if same_context:
                    if request["gate"]:
                        gate = worker.check(request["gate"])
                        if gate["verdict"] != "green":
                            log = Path(gate["command_log"]).read_text()
                            if "repository command gate:" not in log:
                                raise RuntimeError(
                                    "native gate failed: " + gate["diagnostic"]
                                )
                            return {
                                **self._metadata(),
                                "status": "source_policy_violation",
                                "returncode": 1,
                                "log": log,
                                "audited": [],
                                "persistent": True,
                                "prefix_reused": True,
                                "elapsed_seconds": time.monotonic() - started,
                                "started_at": started_at,
                                "finished_at": time.time(),
                            }
                    previous = worker.prefix
                    reused = worker.align(
                        request["synthetic"], TARGET_ID, CERTIFICATION_FACT
                    )
                    advanced = reused and worker.prefix != previous
                if not reused:
                    self.close()
            if self.worker is None:
                self.worker = LocalBatchWorker(
                    self.config, request, self.root, deadline, self.process_registry
                )
            prefix_seconds = time.monotonic() - started
            frozen = owned_file(
                self.worker.root / (str(self.worker.index + 1) + ".full.thy"),
                request["source"],
                self.config,
            )
            result = self.worker.check(request["candidate"])
            log = Path(result["command_log"]).read_text()
            current_audited = audit_markers(log)
            target_audited = all(
                target + ":" + request["polarity"] in current_audited
                for target in targets
            )
            status = "ok" if result["verdict"] == "green" else "build_error"
            if result["timed_out"]:
                status = "timeout"
            if status == "ok" and not target_audited:
                status = "missing_audit"
            failure_kind = None
            if "Warning - Unable to increase stack - interrupting thread" in log:
                failure_kind, status = "stack_exhausted", "resource_exhausted"
            elif "exception Interrupt_Breakdown raised" in log:
                failure_kind, status = "native_interrupt", "infrastructure_error"
            return {
                **self._metadata(),
                **result,
                "status": status,
                "log": log,
                "failure_kind": failure_kind,
                "elapsed_seconds": time.monotonic() - started,
                "started_at": started_at,
                "finished_at": time.time(),
                "audited": list(
                    dict.fromkeys([*self.worker.prefix_audited, *current_audited])
                ),
                "target_id": request["target"],
                "target_ids": targets,
                "target_audit_verified": target_audited,
                "command": self.worker.command,
                "worker_id": self.worker.name,
                "persistent": True,
                "prefix_reused": reused,
                "prefix_advanced": advanced,
                "prefix_prepare_seconds": prefix_seconds,
                "dependency_loading_seconds": prefix_seconds,
                "owner_check_seconds": result["wall_ms"] / 1000,
                "source_sha256": digest(request["source"]),
                "import_closure_sha256": request["closure_sha256"],
                "worker_evidence": str(self.worker.root),
                "frozen_source": str(frozen),
                "timeout_phase": result["timeout_scope"],
                "timeout_limit": "candidate" if result["timed_out"] else None,
            }
        except BatchTargetManifestError as error:
            self.close()
            return {
                **self._metadata(),
                "status": "infrastructure_error",
                "returncode": 1,
                "log": str(error),
                "audited": [],
                "persistent": False,
                "failure_kind": "invalid_trusted_batch_manifest",
                "elapsed_seconds": time.monotonic() - started,
                "started_at": started_at,
                "finished_at": time.time(),
            }
        except TimeoutError as error:
            # Current A returns a startup timeout directly. Retrying the same
            # source cold would grant another allowance and hide startup cost.
            self.close()
            return {
                **self._metadata(),
                "status": "timeout",
                "returncode": 124,
                "log": getattr(error, "worker_log", "") + "\n" + str(error),
                "audited": [],
                "persistent": True,
                "timed_out": True,
                "timeout_phase": getattr(error, "timeout_scope", "prefix-startup"),
                "timeout_limit": "prefix-startup",
                "timeout_seconds": getattr(
                    error, "timeout_seconds", self.config.get("timeout_seconds", 900)
                ),
                "elapsed_seconds": time.monotonic() - started,
                "started_at": started_at,
                "finished_at": time.time(),
            }
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
            reason = str(error)
            # A discards only this worker; a later request may start a fresh
            # batch worker. Do not permanently degrade all remaining work.
            self.close()
        acceleration_seconds = time.monotonic() - started
        remaining = min(deadline - time.monotonic(), self._resource_remaining())
        if remaining <= 0:
            result = {
                "status": "timeout",
                "returncode": None,
                "log": reason,
                "audited": [],
                "timeout_phase": "persistent",
                "timeout_limit": "total_check",
            }
        else:
            # A's exceptional proof fallback has the *candidate* allowance,
            # including prefix loading. Statement/whole-theory checks use the
            # complete-check allowance. An explicit cap may only shorten it.
            full_budget = self.config.get(
                "candidate_timeout_seconds" if is_candidate else "timeout_seconds",
                120 if is_candidate else 900,
            )
            if timeout is not None:
                full_budget = min(full_budget, timeout)
            result = self.cold_check(
                directory, owner, timeout=min(full_budget, remaining)
            )
            if is_candidate and result.get("status") == "timeout":
                result = {
                    **result,
                    "timeout_phase": "cold-candidate-including-prefix",
                    "timeout_limit": "candidate",
                    "timeout_seconds": full_budget,
                }
        if is_candidate:
            verified = all(
                target + ":" + request["polarity"] in result.get("audited", [])
                for target in targets
            )
            result = {
                **result,
                "target_id": request["target"],
                "target_ids": targets,
                "target_audit_verified": verified,
            }
            if result.get("status") == "ok" and not verified:
                result["status"] = "missing_audit"
        if "repository command gate:" in result.get("log", ""):
            result = {**result, "status": "source_policy_violation"}
        return {
            **result,
            **self._metadata(),
            "persistent": False,
            "fallback_reason": reason,
            "persistent_disabled": self.persistent_disabled,
            "persistent_elapsed_seconds": acceleration_seconds,
            "elapsed_seconds": time.monotonic() - started,
            "started_at": started_at,
            "finished_at": time.time(),
        }
