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

    def command(self, directory, theory):
        cfg = self.config
        argv = [
            cfg.get("isabelle", "/workspace/verification/isabelle/bin/isabelle"),
            "process",
            "-o",
            "threads=" + str(cfg.get("threads", 4)),
            "-l",
            cfg.get("session", "Semantic_Base"),
        ]
        for directory_arg in cfg.get("session_dirs", []):
            argv += ["-d", directory_arg]
        argv += ["-T", str(Path(directory) / theory)]
        return cfg.get("command_prefix", []) + argv

    def check(self, directory, theory, timeout=None):
        started = time.monotonic()
        command = self.command(directory, theory)
        env = os.environ.copy()
        env.update(self.config.get("env", {}))
        try:
            p = subprocess.Popen(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            try:
                log, _ = p.communicate(
                    timeout=timeout or self.config.get("timeout_seconds", 120)
                )
                rc = p.returncode
                status = "ok" if rc == 0 else "build_error"
            except subprocess.TimeoutExpired:
                kill_tree(p.pid)
                log, _ = p.communicate()
                rc, status = None, "timeout"
        except OSError as exc:
            rc, log, status = None, str(exc), "infrastructure_error"
        evidence = re.findall(
            r"^VERO_RESULT (c\d{4}) (prove|disprove) clean$", log, re.MULTILINE
        )
        return {
            "status": status,
            "returncode": rc,
            "elapsed_seconds": time.monotonic() - started,
            "audited": [cid + ":" + kind for cid, kind in evidence],
            "log": log,
            "command": command,
        }


def evaluate(bench, artifact, output, checker):
    validate_artifact(bench, artifact)
    output = Path(output)
    if output.exists():
        raise FileExistsError("evaluation must use a fresh directory")
    output.mkdir(parents=True)
    started = time.monotonic()
    statuses, bodies = {}, {}
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
    module_results = {}
    for module in sorted({c["module"] for c in bench["contracts"]}):
        pending = [
            c
            for c in bench["contracts"]
            if c["module"] == module and statuses[c["id"]]["status"] == "pending"
        ]
        if not pending:
            continue
        result = checker.check(output / "source", module)
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
    positive = sum(s["status"] == "proved" for s in statuses.values())
    negative = sum(s["status"] == "disproved" for s in statuses.values())
    complete = positive == len(statuses)
    integration = None
    if positive + negative == len(statuses):
        integration = checker.check(output / "source", "Vero_All")
        (output / "integration.log").write_text(integration["log"])
        complete = complete and integration["status"] == "ok"
        integration = {k: v for k, v in integration.items() if k != "log"}
    report = {
        "schema_version": 1,
        "benchmark_id": bench["benchmark_id"],
        "artifact_sha256": digest(artifact),
        "total_specs": len(statuses),
        "proved_specs": positive,
        "disproved_specs": negative,
        "resolved_specs": positive + negative,
        "complete": complete,
        "elapsed_seconds": time.monotonic() - started,
        "contracts": statuses,
        "modules": module_results,
        "integration": integration,
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
