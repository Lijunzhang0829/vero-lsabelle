"""Fresh-process checking; only immutable templates reach the proof checker."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from .benchmark import digest, render, save, validate_artifact
from .processes import kill_tree

FORBIDDEN = re.compile(
    r"\b(?:sorry|axiomatization|axioms|oracle|ML\w*|SML\w*|setup|local_setup|method_setup|attribute_setup|simproc_setup|tactic|use|external_file|code_printing|code_reserved|code_identifier|export_code|export_generated_files|generate_file|compile_generated_files|SML_export|declare|lemmas|bundle|unbundle|include|notation|no_notation|syntax|no_syntax|translations|no_translations|judgment|typedecl|typedef|datatype|codatatype|fun|function|primrec|partial_function|class|instantiation|instance|lemma|theorem|corollary|proposition|definition|abbreviation|end|context|theory|locale|interpretation|sublocale|overloading)\b"
)


def strip_comments(text):
    out, i, depth = [], 0, 0
    while i < len(text):
        if text[i : i + 2] == "(*":
            depth += 1
            i += 2
        elif text[i : i + 2] == "*)":
            if not depth:
                raise ValueError("unmatched comment close")
            depth -= 1
            i += 2
        elif depth:
            i += 1
        else:
            out.append(text[i])
            i += 1
    if depth:
        raise ValueError("unclosed comment")
    return "".join(out)


def filled(body):
    try:
        return strip_comments(body).strip() not in {"", "oops"}
    except ValueError:
        return True


def hygiene(body):
    try:
        text = strip_comments(body)
    except ValueError as exc:
        return str(exc)
    # Conservative by design: executable embedded ML is outside the proof-slot language.
    bad = FORBIDDEN.search(text)
    if bad:
        return "forbidden proof token: " + bad[0]
    if re.search(r"\boops\b", text):
        return "oops mixed with a filled proof"
    if not re.match(r"^\s*(?:by|proof|apply|using|unfolding)\b", text):
        return "proof must start with by/proof/apply/using/unfolding"
    return None


class Checker:
    def __init__(self, config):
        self.config = dict(config)
        self._shared = None
        self.process_registry = None
        backend = self.config.get("backend")
        if backend is not None and backend not in {"fresh-process-v1", "a-shared-incremental-v1", "a-shared-batch-v2"}:
            raise ValueError("unknown Isabelle backend: " + str(backend))
        if backend == "a-shared-incremental-v1":
            from .shared_native import SharedNativeBackend
            self._shared = SharedNativeBackend(self.config, self._cold_check)
        elif backend == "a-shared-batch-v2":
            from .a_batch_native import BatchNativeBackend
            self._shared = BatchNativeBackend(self.config, self._cold_check)

    def close(self):
        if self._shared is not None:
            self._shared.close()

    def build_session(self, directory, theories, session_name="Generated_Refine", timeout=None):
        if self._shared is None:
            raise ValueError("session builds require the explicitly configured shared backend")
        return self._shared.build_session(directory, theories, session_name, timeout)

    def command(self, directory, theory):
        cfg = self.config
        argv = [
            cfg.get("isabelle", "/workspace/verification/isabelle/bin/isabelle"),
            "process",
            "-o",
            "threads=" + str(cfg.get("threads", 8 if cfg.get("backend") == "a-shared-incremental-v1" else 4)),
            "-l",
            cfg.get("session", "Semantic_Base"),
        ]
        for directory_arg in cfg.get("session_dirs", []):
            argv += ["-d", directory_arg]
        if cfg.get("backend") in {"a-shared-incremental-v1", "a-shared-batch-v2"}:
            from .shared_native import ml_string
            qualifier = cfg.get("source_qualifier", "Generated_Refine")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", qualifier):
                raise ValueError("invalid shared source qualifier")
            call = "(ignore (Thy_Info.use_theories (Options.default ()) "
            call += ml_string(qualifier) + " [(" + ml_string(Path(directory).resolve() / theory)
            call += ", Position.none)]); OS.Process.exit OS.Process.success) handle exn => "
            call += "(writeln (Runtime.exn_message exn); OS.Process.exit OS.Process.failure)"
            argv += ["-e", call]
        else:
            argv += ["-T", str(Path(directory) / theory)]
        return cfg.get("command_prefix", []) + argv

    def check(self, directory, theory, timeout=None):
        if self._shared is not None:
            return self._shared.check(directory, theory, timeout)
        return self._cold_check(directory, theory, timeout)

    def _cold_check(self, directory, theory, timeout=None):
        started = time.monotonic()
        command = self.command(directory, theory)
        env = os.environ.copy()
        env.update(self.config.get("env", {}))
        phases = {}
        phased = self.config.get("dependency_timeout_seconds") is not None and timeout is None
        try:
            launch = subprocess.Popen if self.process_registry is None else self.process_registry.popen
            registration = {} if self.process_registry is None else {"kind": "prover"}
            p = launch(
                command,
                text=not phased,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
                **registration,
            )
            if phased:
                from .phase_process import communicate_phases

                log, phases = communicate_phases(
                    p, theory,
                    load_seconds=self.config["dependency_timeout_seconds"],
                    owner_seconds=self.config.get("timeout_seconds", 120),
                    total_seconds=self.config.get("total_timeout_seconds"),
                    started_at=started,
                )
                rc = p.returncode
                status = "timeout" if phases["timeout_phase"] else ("ok" if rc == 0 else "build_error")
                if status == "timeout":
                    log += "\nVERO_TIMEOUT phase=" + phases["timeout_phase"] + "\n"
                elif status == "ok" and not phases["owner_started"]:
                    status = "infrastructure_error"
                    log += "\nMissing trusted owner-theory entry marker\n"
            else:
                budget = timeout or self.config.get("timeout_seconds", 120)
                total = self.config.get("total_timeout_seconds") if timeout is None else None
                if total is not None:
                    budget = min(budget, total)
                    phases = {"total_timeout_seconds": total, "timeout_limit": None}
                try:
                    log, _ = p.communicate(
                        timeout=max(0.001, budget - (time.monotonic() - started))
                    )
                    rc = p.returncode
                    status = "ok" if rc == 0 else "build_error"
                except subprocess.TimeoutExpired:
                    kill_tree(p.pid)
                    log, _ = p.communicate()
                    rc, status = None, "timeout"
                    if total is not None:
                        phases["timeout_limit"] = "total_check" if total <= budget else "whole_process"
        except OSError as exc:
            rc, log, status = None, str(exc), "infrastructure_error"
        failure_kind = None
        if "Warning - Unable to increase stack - interrupting thread" in log:
            failure_kind = "stack_exhausted"
            if status != "timeout":
                status = "resource_exhausted"
        elif "exception Interrupt_Breakdown raised" in log:
            failure_kind = "native_interrupt"
            if status != "timeout":
                status = "infrastructure_error"
        evidence = re.findall(
            r"^VERO_RESULT ([ch]\d{4}) (prove|disprove) clean$", log, re.MULTILINE
        )
        return {
            "status": status,
            "returncode": rc,
            "failure_kind": failure_kind,
            "elapsed_seconds": time.monotonic() - started,
            "audited": [cid + ":" + kind for cid, kind in evidence],
            "log": log,
            "command": command,
            **phases,
        }


def evaluate(bench, artifact, output, checker, *, incremental=None):
    validate_artifact(bench, artifact)
    output = Path(output)
    if output.exists():
        raise FileExistsError("evaluation must use a fresh directory")
    output.mkdir(parents=True)
    started = time.monotonic()
    from .slots import context, schedule, slot_hygiene

    statuses, bodies = {}, {}
    slot_schedule = schedule(bench)
    auxiliary_errors = {}
    for key, (module, kind) in slot_schedule.items():
        slot = artifact["slots"][key]
        if kind == "proof" or not slot["found"] or not slot["body"].strip():
            continue
        error = slot_hygiene(kind, slot["body"])
        if error:
            auxiliary_errors[module] = key + ": " + error
        else:
            bodies[key] = slot["body"]
    try:
        effective = context(bench, bodies)
        context_error = None
    except ValueError as exc:
        effective, context_error = bench, str(exc)
    for c in bench["contracts"]:
        entries = [
            (p, artifact["slots"][c["id"] + ":" + p]) for p in ["prove", "disprove"]
        ]
        selected = [(p, s) for p, s in entries if s["found"] and filled(s["body"])]
        if c["module"] in artifact.get("file_errors", {}):
            statuses[c["id"]] = {"status": "extraction_error"}
        elif len(selected) > 1:
            statuses[c["id"]] = {"status": "overfilled"}
        elif not selected:
            statuses[c["id"]] = {"status": "unfilled"}
        else:
            polarity, slot = selected[0]
            error = hygiene(slot["body"])
            if error:
                statuses[c["id"]] = {"status": "rejected", "error": error}
            else:
                statuses[c["id"]] = {"status": "pending", "polarity": polarity}
                bodies[c["id"] + ":" + polarity] = slot["body"]
    render(bench, output / "source", bodies)
    from .planning import dependencies, module_order

    module_results = {}
    for module in module_order(effective):
        pending = [
            c
            for c in bench["contracts"]
            if c["module"] == module and statuses[c["id"]]["status"] == "pending"
        ]
        has_aux = any(
            owner == module and kind != "proof" and key in bodies
            for key, (owner, kind) in slot_schedule.items()
        )
        if (
            not pending
            and not has_aux
            and module not in auxiliary_errors
            and module not in artifact.get("file_errors", {})
        ):
            continue
        blocked = sorted(
            d
            for d in dependencies(effective, module)
            if module_results.get(d, {}).get("status") not in {None, "ok"}
        )
        if (
            context_error
            or module in auxiliary_errors
            or module in artifact.get("file_errors", {})
        ):
            result = {
                "status": "rejected",
                "audited": [],
                "log": context_error
                or auxiliary_errors.get(module)
                or artifact["file_errors"][module],
            }
        elif blocked:
            result = {
                "status": "blocked_dependency",
                "audited": [],
                "log": "Blocked by failed imported theories: " + ", ".join(blocked),
                "blocked_by": blocked,
            }
        else:
            result = (
                incremental.check(output / "source", effective, module, pending, statuses)
                if incremental is not None
                else checker.check(output / "source", module)
            )
        (output / (module + ".log")).write_text(result["log"])
        module_results[module] = {k: v for k, v in result.items() if k != "log"}
        for c in pending:
            rec = statuses[c["id"]]
            key = c["id"] + ":" + rec["polarity"]
            if result["status"] == "ok" and key in result["audited"]:
                rec["status"] = "proved" if rec["polarity"] == "prove" else "disproved"
            else:
                rec["status"] = (
                    result["status"] if result["status"] != "ok" else "missing_theorem"
                )
    seals = (incremental.seal_complete(output / "source", effective, statuses, output)
             if incremental is not None else None)
    positive = sum(s["status"] == "proved" for s in statuses.values())
    negative = sum(s["status"] == "disproved" for s in statuses.values())
    complete = False
    integration = None
    if (
        positive + negative == len(statuses)
        and not context_error
        and not auxiliary_errors
        and all(r["status"] == "ok" for r in module_results.values())
        and (seals is None or all(s["status"] == "sealed" for s in seals.values()))
    ):
        integration = checker.check(output / "source", "Vero_All")
        (output / "integration.log").write_text(integration["log"])
        if integration["status"] == "ok" and getattr(checker, "config", {}).get("backend") in {"a-shared-incremental-v1", "a-shared-batch-v2"}:
            build = checker.build_session(
                output / "source", ["Vero_All"],
                timeout=checker.config.get("integration_build_timeout_seconds", 900),
            )
            (output / "integration-build.log").write_text(build["log"])
            integration["build"] = {k: v for k, v in build.items() if k != "log"}
            if build["status"] != "ok":
                integration["status"] = "integration_build_failed"
        complete = positive == len(statuses) and integration["status"] == "ok"
        integration = {k: v for k, v in integration.items() if k != "log"}
    report = {
        "schema_version": 1,
        "benchmark_id": bench["benchmark_id"],
        "artifact_sha256": digest(artifact),
        "total_specs": len(statuses),
        "proved_specs": positive,
        "disproved_specs": negative,
        "resolved_specs": positive + negative,
        "passed_specs": positive + negative,
        "all_specs_resolved": positive + negative == len(statuses)
        and integration is not None
        and integration["status"] == "ok",
        "complete": complete,
        "elapsed_seconds": time.monotonic() - started,
        "contracts": statuses,
        "modules": module_results,
        "integration": integration,
        "verification_mode": "incremental" if incremental is not None else "full",
        "seals": seals,
        "context_error": context_error,
        "auxiliary_errors": auxiliary_errors,
        "effective_imports": effective.get("planning", {}).get("imports", {}),
    }
    save(output / "report.json", report)
    (output / "report.md").write_text(
        "# Isabelle Vero evaluation\n\n"
        + f"Proved: {positive}/{len(statuses)}; disproved: {negative}; complete: {complete}.\n\n"
        + "\n".join(
            f"- {c['id']} ({c['name']}): {statuses[c['id']]['status']}"
            for c in bench["contracts"]
        )
        + "\n"
    )
    return report
