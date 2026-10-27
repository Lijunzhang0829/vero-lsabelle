"""Agent-owned repository proof generation with independently verified stages.

This module supplies accounting and acceptance constraints, never a proof planner
or a tactic-selection policy. Original candidate-v2 ledgers are not migrated.
"""

import argparse
import copy
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from . import repository_sources as sources
from .benchmark import digest, read, save
from .freeform import (
    FreeformController,
    prepare_reference,
    support,
    validate_agent_config,
)
from .freeform import run as freeform_run
from .freeform_sources import render, tokens
from .helper_stages import HelperStages
from .repository_gate import install_gate
from .run_metrics import checker_description, requires_build


class MeasuredChecker:
    """Retain every native invocation, including registration/seals/integration."""

    def __init__(self, checker, directory):
        self.native = checker
        self.config = checker.config
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def check(self, source, owner, **kwargs):
        number = len(list(self.directory.glob("*.json")))
        path = self.directory / f"native-{number:06d}.json"
        source = Path(source)
        kind = (
            "declaration"
            if "helper-registrations" in source.parts
            else "seal"
            if "sealing" in source.parts
            else "integration"
            if any(p.startswith("integration-") for p in source.parts)
            else "candidate"
        )
        record = {
            "kind": kind,
            "owner": owner,
            "source": str(source),
            "started_at": time.time(),
            "status": "started",
        }
        save(path, record)
        install_gate(source)
        result = self.native.check(source, owner, **kwargs)
        if "repository command gate:" in result.get("log", ""):
            result = {
                **result,
                "status": "source_policy_violation",
                "failure_kind": "unsupported_or_malformed_command",
            }
        save(
            path,
            {
                **record,
                **{k: v for k, v in result.items() if k != "log"},
                "finished_at": time.time(),
            },
        )
        return result

    def build_session(self, source, theories, **kwargs):
        install_gate(Path(source))
        return self.native.build_session(source, theories, **kwargs)

    def close(self):
        if hasattr(self.native, "close"):
            self.native.close()


class RepositoryController(FreeformController):
    budget_policy = "repository-helper-stages-v3"
    snapshot = staticmethod(sources.snapshot)

    @property
    def policy(self):
        return HelperStages(self.state["helper_stages"])

    def __init__(self, bench, root, checker, workspace, reference, telemetry=None):
        self.contract_ids = {c["id"] for c in bench["contracts"]}
        super().__init__(
            bench,
            root,
            MeasuredChecker(checker, Path(root) / "native-calls"),
            workspace,
            reference,
            telemetry=telemetry,
        )
        if "helper_stages" not in self.state:
            policy = HelperStages()
            for contract in bench["contracts"]:
                cid = contract["id"]
                policy.register_contract(
                    cid,
                    self.binding["references"][cid],
                    digest(
                        {"base": bench["base_import"], "context": contract["context"]}
                    ),
                )
            self.state.update(
                helper_stages=policy.state,
                helper_references={},
                registered_signatures={},
                semantic_signatures={},
                finalized=False,
            )
            self.persist()
        self.ids = set(self.state["contracts"])
        for hid, expected in self.state["helper_references"].items():
            if (
                hashlib.sha256(
                    (self.reference / (hid + ".yxml")).read_bytes()
                ).hexdigest()
                != expected
            ):
                raise ValueError("registered helper reference changed: " + hid)
        self._sync()
        self.persist()

    def persist(self):
        if "helper_stages" in self.state:
            self._sync()
        super().persist()

    def _sync(self):
        for nid, node in self.policy.state["nodes"].items():
            row = self.state["contracts"][nid]
            row["attempts"] = node["stage_attempts"]
            row["cumulative_attempts"] = node["cumulative_attempts"]
            row["stage"] = node["stage"]
            row["proved"] = node["green"]

    def progress(self):
        if "helper_stages" not in self.state:
            return super().progress()
        result = super().progress()
        rows = result["contracts"]
        for nid, row in rows.items():
            node = self.policy.get(nid)
            status = self.policy.status(nid)
            if row["status"] != "recovery_pending":
                row["status"] = "proved" if status == "green" else status
            row.update(
                stage=node["stage"],
                cumulative_attempts=node["cumulative_attempts"],
                kind=node["kind"],
                blockers=self.policy.blockers(nid),
            )
        result["nodes"] = rows
        result["contracts"] = {
            nid: row for nid, row in rows.items() if nid in self.contract_ids
        }
        result["helpers"] = {
            nid: row for nid, row in rows.items() if nid not in self.contract_ids
        }
        result["next_targets"] = [
            nid for nid, row in rows.items() if row["status"] == "ready"
        ]
        result["all_proved"] = all(row["status"] == "proved" for row in rows.values())
        result["can_finalize"] = (
            not any(
                row["status"] in {"ready", "checking", "recovery_pending"}
                for row in rows.values()
            )
            and not result["retry_requests"]
        )
        result["all_finished"] = result["all_proved"] or self.state["finalized"]
        result["stage_credits"] = [
            e for e in self.policy.state["events"] if e["kind"] == "stage_credit"
        ]
        result["cumulative_attempts"] = sum(
            n["cumulative_attempts"] for n in self.policy.state["nodes"].values()
        )
        costs = {}
        for path in (self.root / "native-calls").glob("*.json"):
            call = read(path)
            cost = costs.setdefault(
                call["kind"], {"calls": 0, "elapsed_seconds": 0.0, "unfinished": 0}
            )
            cost["calls"] += 1
            cost["elapsed_seconds"] += call.get("elapsed_seconds", 0.0)
            cost["unfinished"] += call["status"] == "started"
        result["native_costs"] = costs
        return result

    @staticmethod
    def _statement_signature(frozen, nid):
        return digest(
            {
                "statement": [
                    (t.value, t.quoted)
                    for t in tokens(sources.statement_text(frozen, nid))
                ],
                "context": frozen["node_contexts"][nid],
            }
        )

    def _validate(self, frozen, *, saved_context=False):
        accepted_ids = (
            set(frozen["accepted_contracts"])
            if saved_context
            else {nid for nid, row in self.state["contracts"].items() if row["proved"]}
        )
        accepted = {
            nid: read(
                Path(self.state["contracts"][nid]["accepted_evidence"])
                / "snapshot.json"
            )
            for nid in accepted_ids
        }
        sources.validate_accepted(frozen, accepted)
        sources.validate_semantics(
            frozen.get("policy_semantics", {})
            if saved_context
            else self.state["semantic_signatures"],
            sources.semantic_signatures(frozen),
        )
        signatures = (
            frozen.get("policy_signatures", {})
            if saved_context
            else self.state["registered_signatures"]
        )
        for nid, signature in signatures.items():
            if (
                nid not in frozen["contracts"]
                or self._statement_signature(frozen, nid) != signature
            ):
                raise ValueError(
                    "registered statement/context changed or removed: " + nid
                )

    def check_snapshot(self, frozen, cid, directory):
        try:
            self._validate(frozen, saved_context=True)
        except (ValueError, KeyError) as exc:
            return {"status": "invalid_context", "log": str(exc), "recoverable": False}
        # Freeze definitions even on a failed proof; neither proof edits nor
        # regrouping may quietly replace the meaning of an existing goal.
        self.state["semantic_signatures"].update(sources.semantic_signatures(frozen))
        self.persist()
        return super().check_snapshot(frozen, cid, directory)

    def submit_one(self, frozen, cid, rid, *, retry=False):
        row = self.state["contracts"][cid]
        prior = next(
            (h for h in reversed(row["history"]) if h.get("request_id") == rid), None
        )
        key = rid + ":" + cid
        if prior:
            if retry and prior.get("recoverable"):
                latest = row["history"][-1]
                record = self.policy.state["requests"][key]
                if (
                    latest["request_id"] != rid
                    or record["stage"] != self.policy.get(cid)["stage"]
                    or record.get("dependency_snapshot", [])
                    != self.policy.get(cid)["dependencies"]
                    or record.get("invalidated")
                ):
                    return {
                        **self.with_log(prior),
                        "recovery_rejected": "candidate/stage superseded",
                    }
        else:
            if self.state["finalized"]:
                return {"id": cid, "status": "finalized", "charged": False}
            if not self.policy.can_check(cid):
                return {
                    "id": cid,
                    "status": self.policy.status(cid),
                    "charged": False,
                    "blockers": self.policy.blockers(cid),
                }
            # This reservation is persisted together with FreeformController's
            # pending native-check record before the subprocess starts.
            frozen = {
                **frozen,
                "policy_semantics": copy.deepcopy(self.state["semantic_signatures"]),
                "policy_signatures": copy.deepcopy(self.state["registered_signatures"]),
            }
            self.policy.reserve(
                cid, key, digest(frozen), self.policy.get(cid)["context_fingerprint"]
            )
        try:
            return super().submit_one(frozen, cid, rid, retry=retry)
        except Exception:
            # Roll back an in-memory reservation if a filesystem operation
            # failed before persist; committed pending checks stay charged.
            self.state = read(self.path)
            raise

    def commit(self, outcome):
        row = self.state["contracts"][outcome["id"]]
        if not any(h["evidence"] == outcome["evidence"] for h in row["history"]):
            key = outcome["request_id"] + ":" + outcome["id"]
            status = (
                "infrastructure_error"
                if outcome.get("recoverable")
                else outcome["status"]
            )
            if outcome.get("infrastructure_retry", 0):
                credits = self.policy.recover_result(
                    key, status, evidence=outcome["evidence"]
                )
            else:
                credits = self.policy.finish(key, status, evidence=outcome["evidence"])
            outcome["stage"] = self.policy.state["requests"][key]["stage"]
            outcome["cumulative_attempt"] = self.policy.state["requests"][key][
                "cumulative_attempt"
            ]
            outcome["stage_credits"] = credits
            self.record_outcome(outcome)
            row["history"].append(outcome)
            if outcome["status"] == "proved":
                row["accepted_evidence"] = outcome["evidence"]
        self.state.pop("pending", None)
        self._sync()
        self.persist()

    def _capture_helper(self, frozen, hid, directory):
        """Elaborate only the helper statement; never execute its candidate proof."""
        declaration = frozen["contracts"][hid]
        module = frozen["modules"][declaration["module"]]
        files = {m["path"]: m["text"] for m in frozen["modules"].values()}
        text = module["text"]
        files[module["path"]] = (
            text[: declaration["proof_start"]] + "oops\n" + text[declaration["end"] :]
        )
        clean = self.snapshot(
            self.workspace, set(frozen["contracts"]), self.bench["base_import"], files
        )
        source = directory / "source"
        terms = directory / "terms"
        terms.mkdir(parents=True, exist_ok=True)
        # Accepted prefixes reference existing immutable statements during capture.
        reference_ids = set(self.binding["references"]) | set(
            self.state["helper_references"]
        )
        for term in self.reference.glob("*.yxml"):
            if term.stem in reference_ids:
                shutil.copyfile(term, terms / term.name)
        if self.checker.config.get("command_prefix"):
            terms.chmod(0o777)
        accepted = {
            nid for nid, row in self.state["contracts"].items() if row["proved"]
        }
        render(clean, source, accepted, hid)
        owner = declaration["module"]
        p = source / (owner + ".thy")
        body = p.read_text()
        body = re.sub(
            r"\b(?:lemma|theorem|corollary|proposition)\s+" + hid + r"(?=\s|\[|:)",
            'contract_reference "' + hid + '" ' + hid,
            body,
            count=1,
        )
        audit = (
            '\nlocal_setup \\<open>Freeform_Reference.audit "'
            + hid
            + '" "'
            + hid
            + '"\\<close>\n'
        )
        body = body.replace(audit, "")
        p.write_text(body)
        (source / "Freeform_Support.thy").write_text(support(self.bench, terms))
        limits = (
            {
                "timeout": self.checker.config.get(
                    "helper_statement_timeout_seconds", 900
                )
            }
            if requires_build(self.checker)
            else {}
        )
        result = self.checker.check(source, owner, **limits)
        save(directory / "native.json", {k: v for k, v in result.items() if k != "log"})
        (directory / "native.log").write_text(result.get("log", ""))
        if result["status"] != "ok" or not (terms / (hid + ".yxml")).is_file():
            raise ValueError(
                "helper declaration did not elaborate: "
                + hid
                + "\n"
                + result.get("log", "")[-6000:]
            )
        term = (terms / (hid + ".yxml")).read_bytes()
        return term

    def register_helpers(self, payload):
        rid = payload["request_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
            raise ValueError("invalid batch ID")
        directory = self.root / "helper-registrations" / rid
        if (directory / "request.json").exists():
            if read(directory / "request.json") != payload:
                raise ValueError("batch ID reused with changed contents")
            if (directory / "response.json").exists():
                return read(directory / "response.json")
        if self.state["finalized"]:
            raise ValueError("experiment is finalized")
        parent, helpers = payload["parent"], payload["helpers"]
        if (
            not helpers
            or len(helpers) != len(set(helpers))
            or any(not re.fullmatch(r"h[0-9]{4}", h) for h in helpers)
        ):
            raise ValueError("helpers must be unique hNNNN IDs")
        existing = self.policy.state["batches"].get(rid)
        if existing:
            result = {
                "request_id": rid,
                "registered": helpers,
                "progress": self.progress(),
            }
            save(directory / "response.json", result)
            return result
        if self.policy.get(parent)["green"]:
            raise ValueError("GREEN parent cannot acquire helper credit")
        if set(helpers) & self.ids:
            raise ValueError("credit requires newly registered helpers")
        if any(
            h.get("recoverable") and h.get("infrastructure_retry", 0) < 2
            for h in self.state["contracts"][parent]["history"][-1:]
        ):
            raise ValueError("complete infrastructure recovery before graph revision")
        frozen = self.snapshot(
            self.workspace,
            self.ids | set(helpers),
            self.bench["base_import"],
            payload.get("files"),
        )
        self._validate(frozen)
        if not {parent, *helpers} <= set(frozen["contracts"]):
            raise ValueError(
                "parent and all helpers must be declared in the submitted sources"
            )
        save(directory / "request.json", payload)
        save(directory / "snapshot.json", frozen)
        terms, specs = {}, []
        for hid in helpers:
            term = self._capture_helper(frozen, hid, directory / ("helper-" + hid))
            terms[hid] = term
            specs.append(
                {
                    "node_id": hid,
                    "statement_fingerprint": hashlib.sha256(term).hexdigest(),
                    "context_fingerprint": digest(
                        {
                            "base": self.binding,
                            "context": frozen["node_contexts"][hid],
                            "semantics": frozen["semantic_signatures"],
                        }
                    ),
                }
            )
        # Pure validation runs on a copy; a rejected batch cannot partially add nodes.
        policy = HelperStages(copy.deepcopy(self.policy.state))
        policy.register_batch(parent, rid, specs, rationale=payload["rationale"])
        for hid, term in terms.items():
            p = self.reference / (hid + ".yxml")
            temporary = p.with_suffix(".yxml.tmp")
            temporary.write_bytes(term)
            temporary.chmod(0o444)
            temporary.replace(p)
            self.state["helper_references"][hid] = hashlib.sha256(term).hexdigest()
            self.state["contracts"][hid] = {
                "attempts": 0,
                "proved": False,
                "history": [],
            }
        self.state["helper_stages"] = policy.state
        for nid in [parent, *helpers]:
            self.state["registered_signatures"][nid] = self._statement_signature(
                frozen, nid
            )
        self.state["semantic_signatures"].update(frozen["semantic_signatures"])
        self.ids.update(helpers)
        for hid in helpers:
            self.record_metric(
                "helper_registered",
                event_id="helper-registered:" + hid,
                node_id=hid,
                node_kind="helper",
                parent_id=parent,
                batch_id=rid,
                statement_sha256=self.state["helper_references"][hid],
                evidence_id=str(directory),
            )
        self._sync()
        self.persist()
        result = {"request_id": rid, "registered": helpers, "progress": self.progress()}
        save(directory / "response.json", result)
        return result

    def dispatch(self, payload):
        with self.lock:
            command = payload["cmd"]
            if command == "result":
                rid = payload.get("request_id", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
                    raise ValueError("invalid request ID")
                registration = self.root / "helper-registrations" / rid
                if (registration / "request.json").exists():
                    if (registration / "response.json").exists():
                        return read(registration / "response.json")
                    try:
                        return self.register_helpers(
                            read(registration / "request.json")
                        )
                    except Exception:
                        self.state = read(self.path)
                        self.ids = set(self.state["contracts"])
                        raise
            if command == "helpers":
                try:
                    return self.register_helpers(payload)
                except Exception:
                    self.state = read(self.path)
                    self.ids = set(self.state["contracts"])
                    raise
            if command == "depend":
                if self.state["finalized"]:
                    raise ValueError("experiment is finalized")
                parent = payload["parent"]
                last = self.state["contracts"][parent]["history"][-1:]
                if any(
                    h.get("recoverable") and h.get("infrastructure_retry", 0) < 2
                    for h in last
                ):
                    raise ValueError(
                        "complete infrastructure recovery before graph revision"
                    )
                added = self.policy.add_dependencies(
                    payload["parent"], payload["dependencies"]
                )
                self.persist()
                return {
                    "added": added,
                    "stage_credit": False,
                    "progress": self.progress(),
                }
            if command == "finish":
                progress = self.progress()
                if not progress["can_finalize"]:
                    raise ValueError("ready nodes or infrastructure recovery remain")
                self.state["finalized"] = True
                self.persist()
                return self.progress()
            return super().dispatch(payload)

    def seal_snapshot(self, frozen, directory):
        try:
            self._validate(frozen)
        except ValueError as exc:
            result = {"status": "seal_deferred", "reason": str(exc)}
            save(directory / "seals.json", result)
            return result
        return super().seal_snapshot(frozen, directory)

    def integrate(self):
        frozen = self.snapshot(self.workspace, self.ids, self.bench["base_import"])
        self._validate(frozen)
        if not all(row["proved"] for row in self.state["contracts"].values()):
            return {"complete": False, "status": "unproved_nodes"}
        return super().integrate(frozen=frozen)


def instruction(bench, checker_config, agent_config):
    validate_agent_config(agent_config)
    return (
        Path(__file__)
        .with_name("REPOSITORY_TASK.md")
        .read_text()
        .replace("{{CONTRACT_COUNT}}", str(len(bench["contracts"])))
        .replace("{{BASE_IMPORT}}", bench["base_import"])
        .replace("{{AGENT_MODEL}}", agent_config["model"])
        .replace("{{REASONING_EFFORT}}", agent_config["reasoning_effort"])
        .replace("{{CHECKER_DESCRIPTION}}", checker_description(checker_config))
        .replace(
            "{{CHECKER_POLICY}}",
            json.dumps(
                {
                    k: checker_config.get(k)
                    for k in (
                        "backend",
                        "shared_core_sha256",
                        "command_timeout_seconds",
                        "initial_statement_timeout_seconds",
                        "helper_statement_timeout_seconds",
                        "integration_build_timeout_seconds",
                        "timeout_seconds",
                        "total_timeout_seconds",
                        "dependency_timeout_seconds",
                        "threads",
                    )
                },
                sort_keys=True,
            ),
        )
    )


def validate_config(config):
    if config.get("instruction_profile") != "codex_repository":
        raise ValueError("new repository experiment requires its own profile/identity")
    shared = config["checker"].get("backend") == "a-shared-incremental-v1"
    expected = {
        "timeout_seconds": 300,
        "total_timeout_seconds": 300,
        "dependency_timeout_seconds": 600,
        "threads": 4,
    }
    if shared:
        expected = {
            "timeout_seconds": 300,
            "total_timeout_seconds": None,
            "command_timeout_seconds": 60,
            "initial_statement_timeout_seconds": 1800,
            "helper_statement_timeout_seconds": 900,
            "integration_build_timeout_seconds": 900,
            "threads": 8,
        }
        if not config["checker"].get("shared_backend_root"):
            raise ValueError("shared checker needs an isolated worker root")
    if any(config["checker"].get(key) != value for key, value in expected.items()):
        raise ValueError(
            "checker policy must match the frozen backend profile (shared A: initial1800/helper900/integration-build900/request300/command60/threads8; legacy: total300/loading600)"
        )
    validate_agent_config(config["agent"])
    if config.get("max_contract_retries") != 5:
        raise ValueError("requires six candidates per stage")
    if (
        config.get("total_agent_seconds") is not None
        or config["agent"].get("timeout_seconds") is not None
    ):
        raise ValueError(
            "passive supervision cannot impose a generation checkpoint kill"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["prepare-reference", "validate-config", "run"]
    )
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    config = read(args.config)
    validate_config(config)
    if args.command == "validate-config":
        print(json.dumps({"status": "valid", "profile": config["instruction_profile"]}))
        return
    if not args.output:
        parser.error("--output is required")
    if args.command == "prepare-reference":
        from .checker import Checker

        result = prepare_reference(
            read(args.benchmark), Checker(config["checker"]), args.output
        )
        print(json.dumps({"status": "ok", "contracts": len(result)}))
    else:
        print(
            json.dumps(
                freeform_run(
                    args.benchmark,
                    args.config,
                    args.output,
                    args.resume,
                    controller_class=RepositoryController,
                    instruction_builder=instruction,
                )
            )
        )


if __name__ == "__main__":
    main()
