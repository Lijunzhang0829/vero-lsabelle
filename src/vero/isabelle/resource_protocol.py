"""Freeze and validate a common A/B tool protocol without assuming parity.

The existing A graph runner is a reference, not an automatic consumer of the new
facade. Deploying a matching native core on B cannot satisfy this gate alone.
Conformance receipts must be produced outside the agent-writable workspace by
native tests. This validator checks their bindings and results, not their origin;
receipt directory integrity remains the supervising deployment's responsibility.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PROTOCOL_ID = "resource-proof-tools-v2"
A_REFERENCE_FILES = (
    "regen/execution/isabelle_node_checker.py",
    "regen/execution/persistent_checker.py",
    "regen/execution/persistent_checker.ML",
    "regen/execution/batch_checker.py",
    "regen/execution/batch_checker.ML",
    "regen/execution/batch_audit.py",
    "regen/execution/declaration_cache.py",
    "regen/execution/fact_context.py",
    "regen/execution/symbol_resolution.py",
)
COMMON_FILES = (
    "src/vero/isabelle/a_batch_core.ML",
    "src/vero/isabelle/a_batch_core.json",
    "src/vero/isabelle/a_batch_native.py",
    "src/vero/isabelle/a_batch_audit.py",
    "tests/isabelle/test_a_batch_native.py",
    "tests/isabelle/test_resource_liveness.py",
    "tests/isabelle/test_resource_cancel_real.py",
    "tests/isabelle/test_resource_declarations.py",
    "tests/isabelle/test_resource_declarations_real.py",
    "src/vero/isabelle/resource_controller.py",
    "src/vero/isabelle/resource_checker.py",
    "src/vero/isabelle/resource_processes.py",
    "src/vero/isabelle/resource_watchdog.py",
    "tests/isabelle/test_resource_protocol_real.py",
    "tests/isabelle/test_resource_checker_real.py",
    "tests/isabelle/test_shared_native_real.py",
    "src/vero/isabelle/resource_runtime.py",
    "src/vero/isabelle/resource_tool.py",
    "src/vero/isabelle/resource_protocol.py",
    "src/vero/isabelle/resource_protocol_receipts.py",
    "src/vero/isabelle/repository_tool.py",
    "src/vero/isabelle/metrics.py",
    "src/vero/isabelle/helper_stages.py",
    "src/vero/isabelle/a_shared_core.json",
    "src/vero/isabelle/resource_metrics.py",
    "src/vero/isabelle/response_usage.py",
    "src/vero/isabelle/agent.py",
    "src/vero/isabelle/codex_transport.py",
    "src/vero/isabelle/telemetry.py",
    "src/vero/isabelle/run_metrics.py",
    "src/vero/isabelle/repository_gate.py",
    "src/vero/isabelle/Freeform_Support.thy",
    "src/vero/isabelle/shared_native.py",
    "src/vero/isabelle/resource_budget.py",
    "src/vero/isabelle/resource_plan.py",
    "src/vero/isabelle/checker.py",
    "src/vero/isabelle/repository.py",
    "src/vero/isabelle/repository_sources.py",
    "src/vero/isabelle/freeform.py",
    "src/vero/isabelle/freeform_sources.py",
    "src/vero/isabelle/benchmark.py",
    "src/vero/isabelle/processes.py",
    "src/vero/isabelle/retry_policy.py",
    "src/vero/isabelle/a_persistent_core.py",
    "src/vero/isabelle/a_incremental_core.ML",
)
# A method may retain graph scheduling, but each Isabelle operation must dispatch
# through this shared surface or demonstrate equivalent native receipt semantics.
OPERATIONS = {
    "statement": {
        "a_reference": "IsabelleChecker.check_graph / statements_only / oops",
        "semantics": "Elaborate fixed declaration in legal context without exporting an unproved fact.",
        "cases": ["valid", "ill_typed", "unproved_fact_unavailable"],
    },
    "proof": {
        "a_reference": "IsabelleChecker.check_node / fact_context.visible_ids",
        "semantics": "Kernel-check candidate with legal committed facts and frozen proposition audit.",
        "cases": [
            "valid",
            "invalid",
            "proposition_drift",
            "illegal_context",
            "oracle_rejected",
        ],
    },
    "batch": {
        "a_reference": "IsabelleChecker.check_complete + audit_complete_theory",
        "semantics": "Audit all declared targets in one snapshot, without granting unproved siblings.",
        "cases": ["valid", "partial_failure", "node_batch_agreement"],
    },
    "query": {
        "a_reference": "IsabelleChecker.resolve_symbols / symbol_resolution.resolve_symbols",
        "semantics": "Native metered query sees the same legal context; text inventory is not native certification.",
        "cases": ["known_fact", "unknown_fact", "context_visibility"],
    },
    "seal": {
        "a_reference": "IsabelleChecker.check_complete / audit_complete_theory / build_generated_session",
        "semantics": "Complete inventory, source, proposition and import versions bound to full audit/build evidence.",
        "cases": [
            "complete",
            "missing_target",
            "source_drift",
            "import_drift",
            "build_failure",
        ],
    },
    "integration": {
        "a_reference": "IsabelleChecker.build_generated_session",
        "semantics": "Build the complete accepted repository with no stale seal certification.",
        "cases": ["complete", "missing_seal", "build_failure"],
    },
    "recovery": {
        "a_reference": "persistent_checker IncrementalWorker + high-level controller",
        "semantics": "Meter deadlines, classify interruption, recover idempotently, preserve diagnostics and cost.",
        "cases": [
            "timeout",
            "interrupt",
            "cold_fallback",
            "cache_context_change",
            "duplicate_request",
            "global_deadline",
        ],
    },
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _files(root, paths):
    return {name: file_hash(Path(root) / name) for name in paths}


def freeze_protocol(a_reference_root, facade_root, *, resource_policy):
    """Create an explicitly unvalidated manifest; never confer launch permission."""
    return {
        "schema_version": 1,
        "protocol_id": PROTOCOL_ID,
        "a_reference": {
            "root": str(Path(a_reference_root).resolve()),
            "files": _files(a_reference_root, A_REFERENCE_FILES),
        },
        "common_facade": {"files": _files(facade_root, COMMON_FILES)},
        "operations": OPERATIONS,
        "resource_policy": resource_policy,
        "resource_policy_sha256": digest(resource_policy),
        "activations": {},
        "conformance": {},
        "status": "unvalidated",
        "launch_ready": False,
        "existing_a_runner_already_uses_facade": False,
    }


def binding_identity(manifest):
    """Stable identity excludes validation receipts to avoid circular hashes."""
    return digest(
        {
            key: manifest[key]
            for key in (
                "schema_version",
                "protocol_id",
                "a_reference",
                "common_facade",
                "operations",
                "resource_policy",
                "resource_policy_sha256",
            )
        }
    )


def _artifact(root, entry):
    path = (Path(root) / entry["path"]).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("evidence path escapes trusted receipt directory")
    if file_hash(path) != entry["sha256"]:
        raise ValueError("evidence hash mismatch")
    return json.loads(path.read_text())


def validate_protocol(
    manifest,
    *,
    a_reference_root,
    a_facade_root,
    b_facade_root,
    evidence_root=None,
    require_ready=False,
):
    """Fail closed on drift, missing native comparisons or absent A activation.

    Native normalized result fields deliberately exclude machine-local paths and
    timings; they must include verdict, certified target IDs, context/proposition
    identities, error class and resource policy. Concrete test runners own the
    operation-specific normalization and retain full raw evidence beside it.
    """
    errors = []
    if (
        manifest.get("schema_version") != 1
        or manifest.get("protocol_id") != PROTOCOL_ID
    ):
        errors.append("unsupported protocol identity")
    if manifest.get("operations") != OPERATIONS:
        errors.append("incomplete or altered operation semantics")
    policy = manifest.get("resource_policy", {})
    required_policy = {
        "threads",
        "phase_timeouts",
        "context_policy",
        "batch_policy",
        "cache_policy",
        "fallback_policy",
        "cancellation_policy",
        "concurrency",
    }
    if not required_policy <= set(policy):
        errors.append("resource policy not fully frozen")

    def positive(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        )

    if not isinstance(policy.get("threads"), int) or not positive(
        policy.get("threads")
    ):
        errors.append("threads must be a positive integer")
    for field in ("phase_timeouts", "concurrency"):
        values = policy.get(field)
        if (
            not isinstance(values, dict)
            or not values
            or not all(positive(v) for v in values.values())
        ):
            errors.append(field + " must freeze positive limits")
    for field in (
        "context_policy",
        "batch_policy",
        "cache_policy",
        "fallback_policy",
        "cancellation_policy",
    ):
        if not isinstance(policy.get(field), str) or not policy[field].strip():
            errors.append(field + " must freeze explicit semantics")
    if manifest.get("resource_policy_sha256") != digest(policy):
        errors.append("resource policy hash mismatch")
    for label, root, field, expected in (
        ("A reference", a_reference_root, "a_reference", A_REFERENCE_FILES),
        ("A facade", a_facade_root, "common_facade", COMMON_FILES),
        ("B facade", b_facade_root, "common_facade", COMMON_FILES),
    ):
        pinned = manifest.get(field, {}).get("files", {})
        if set(pinned) != set(expected):
            errors.append(label + " source inventory incomplete")
        for path in expected:
            try:
                actual = file_hash(Path(root) / path)
            except OSError:
                actual = None
            if actual is None or pinned.get(path) != actual:
                errors.append(label + " source drift: " + path)
    # Equality of the original native execution core is a separate necessary
    # condition, not a substitute for frontend tool conformance.
    for origin, copied in (
        ("batch_checker.ML", "a_batch_core.ML"),
        ("batch_audit.py", "a_batch_audit.py"),
    ):
        a = (
            manifest.get("a_reference", {})
            .get("files", {})
            .get("regen/execution/" + origin)
        )
        b = (
            manifest.get("common_facade", {})
            .get("files", {})
            .get("src/vero/isabelle/" + copied)
        )
        if not a or a != b:
            errors.append("native core differs: " + origin)
    try:
        binding = binding_identity(manifest)
    except KeyError:
        binding = None
        errors.append("missing protocol binding field")
    if evidence_root is None:
        errors.append("trusted native evidence directory missing")
    else:
        for machine in ("A", "B"):
            entry = manifest.get("activations", {}).get(machine)
            try:
                receipt = _artifact(evidence_root, entry) if entry else {}
                if (
                    receipt.get("machine") != machine
                    or receipt.get("binding_sha256") != binding
                    or receipt.get("adapter") != PROTOCOL_ID
                    or receipt.get("status") != "active"
                    or not receipt.get("entrypoint")
                    or not receipt.get("entrypoint_sha256")
                ):
                    raise ValueError("missing or mismatched adapter activation binding")
                entrypoint = Path(receipt["entrypoint"])
                root = Path(
                    a_facade_root if machine == "A" else b_facade_root
                ).resolve()
                if entrypoint.is_absolute() or not (
                    root / entrypoint
                ).resolve().is_relative_to(root):
                    raise ValueError("adapter entrypoint outside frozen facade")
                if file_hash(root / entrypoint) != receipt["entrypoint_sha256"]:
                    raise ValueError("adapter activation entrypoint drift")
            except (ValueError, OSError, KeyError, TypeError) as exc:
                errors.append(machine + " activation: " + str(exc))
        for operation, spec in OPERATIONS.items():
            for case in spec["cases"]:
                key = operation + ":" + case
                entry = manifest.get("conformance", {}).get(key)
                try:
                    receipt = _artifact(evidence_root, entry) if entry else {}
                    if (
                        receipt.get("binding_sha256") != binding
                        or receipt.get("case") != key
                        or receipt.get("native") is not True
                        or receipt.get("passed") is not True
                        or not receipt.get("input_sha256")
                    ):
                        raise ValueError("native conformance receipt missing or failed")
                    results = receipt.get("results", {})
                    required = {
                        "verdict",
                        "certified_target_ids",
                        "context_sha256",
                        "proposition_sha256",
                        "error_class",
                        "resource_policy_sha256",
                    }
                    if not required <= set(results.get("A", {})) or results.get(
                        "A"
                    ) != results.get("B"):
                        raise ValueError(
                            "A/B normalized native results differ or are incomplete"
                        )
                    if results["A"]["resource_policy_sha256"] != manifest.get(
                        "resource_policy_sha256"
                    ):
                        raise ValueError("native comparison resource policy differs")
                    for machine in ("A", "B"):
                        _artifact(evidence_root, receipt["raw_evidence"][machine])
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    errors.append(key + ": " + str(exc))
    result = {
        "protocol_id": PROTOCOL_ID,
        "binding_sha256": binding,
        "launch_ready": not errors,
        "errors": errors,
        "existing_a_runner_parity_assumed": False,
    }
    if require_ready and errors:
        raise ValueError("tool protocol gate rejected: " + "; ".join(errors))
    return result


def validate_installed_facade(manifest):
    """Bind certification to the package actually executing this gate.

    A caller-selected reference snapshot is not evidence about running code.
    Resolve only from this module's own installed path: both repo/src/vero and
    the deployed /vero-src/vero layout have the same package-relative mapping.
    Native test sources remain verified in the immutable receipt snapshots;
    every pinned src/ file must additionally match the actual imported package.
    """
    return validate_facade_source_root(manifest, Path(__file__).resolve().parents[2])


def validate_facade_source_root(manifest, package_root):
    """Verify a deployment source tree before mounting it; not a runtime override.

    The runtime entry always obtains package_root from its own __file__ through
    validate_installed_facade. Only the trusted deployer supplies a prospective
    tree here so mismatches are rejected before creating directories/containers.
    """
    package_root = Path(package_root).resolve()
    expected = manifest.get("common_facade", {}).get("files", {})
    required = [name for name in COMMON_FILES if name.startswith("src/")]
    if any(name not in expected for name in required):
        raise ValueError("installed facade source inventory incomplete")
    for name in required:
        installed = package_root / Path(name).relative_to("src")
        try:
            actual = file_hash(installed)
        except OSError as exc:
            raise ValueError("installed facade source missing: " + name) from exc
        if actual != expected[name]:
            raise ValueError("installed facade source drift: " + name)
    return {"package_root": str(package_root), "verified_files": required}


def validate_protocol_binding(verification):
    """Runtime adapter for the frozen launch configuration.

    Remote reference sources may be a byte-identical, read-only verification
    snapshot mounted under local roots. Roots are not source identity: all bytes
    must still match the manifest. Configuration cannot replace native receipts.
    """
    path = verification.get("tool_protocol_manifest")
    if not isinstance(path, str) or not path:
        raise ValueError("tool protocol manifest path missing")
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text())
    if verification.get("tool_protocol_manifest_sha256") != file_hash(manifest_path):
        raise ValueError("tool protocol manifest file binding missing or changed")
    if verification.get("reference_code_identity") != digest(
        manifest.get("a_reference", {}).get("files", {})
    ):
        raise ValueError("A reference code identity differs from protocol manifest")
    roots = verification.get("protocol_source_roots", {})
    needed = {"a_reference", "a_facade", "b_facade", "evidence"}
    if not needed <= set(roots) or any(not roots[key] for key in needed):
        raise ValueError("protocol source/evidence verification roots missing")
    validate_installed_facade(manifest)
    return validate_protocol(
        manifest,
        a_reference_root=roots["a_reference"],
        a_facade_root=roots["a_facade"],
        b_facade_root=roots["b_facade"],
        evidence_root=roots["evidence"],
        require_ready=True,
    )
