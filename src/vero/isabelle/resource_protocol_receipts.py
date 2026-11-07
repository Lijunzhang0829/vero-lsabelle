"""Assemble paired receipts only from complete, source-bound native test runs."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .resource_protocol import (
    COMMON_FILES,
    OPERATIONS,
    PROTOCOL_ID,
    binding_identity,
    digest,
    file_hash,
)


def _read_run(directory, manifest):
    directory = Path(directory)
    receipt = json.loads((directory / "native-receipt.json").read_text())
    if receipt.get("returncode") != 0 or receipt.get("model_calls") != 0:
        raise ValueError("native run failed or unexpected model work")
    tests = ET.parse(directory / "native.xml").getroot().findall(".//testcase")
    if not tests or any(
        test.find(tag) is not None
        for test in tests
        for tag in ("failure", "error", "skipped")
    ):
        raise ValueError("native tests incomplete, failed, or skipped")
    required_tests = {
        "test_statement_query_node_seal_integration_and_duplicate",
        "test_batch_native_certification",
        "test_native_and_policy_negative_cases",
        "test_native_build_failure",
        "test_actual_worker_interrupt_and_cold_fallback",
        "test_native_command_timeout",
        "test_warm_persistent_timeout_kills_worker_and_does_not_fallback",
        "test_cold_check_has_global_limit_not_fresh_complete_check_budget",
        "test_build_cancels_real_process_at_global_deadline",
    }
    if not required_tests <= {test.attrib.get("name") for test in tests}:
        raise ValueError("required native conformance tests were not executed")
    expected = manifest["common_facade"]["files"]
    for filename in COMMON_FILES:
        for phase in ("sources_before", "sources_after"):
            if receipt.get(phase, {}).get(filename) != expected[filename]:
                raise ValueError("native source binding differs: " + filename)
    cases = {}
    for path in sorted((directory / "evidence").glob("*/protocol-cases.json")):
        for case, row in json.loads(path.read_text()).items():
            # The full regression may include a separately reported native helper
            # control; retain its raw evidence but do not turn it into tool cases.
            if case.startswith("helper:"):
                continue
            if case in cases:
                raise ValueError("duplicate case evidence: " + case)
            references = path.parent / "reference" / "reference.json"
            row["reference_terms"] = json.loads(references.read_text())["terms"]
            row["fixture_directory"] = str(path.parent)
            if row.get("passed") is not True or row.get("native_fixture") is not True:
                raise ValueError("case not certified by native assertions")
            cases[case] = row
    deadline = list(
        (directory / "evidence").glob(
            "test_warm_persistent_timeout*/resource-test-result.json"
        )
    )
    if len(deadline) != 1:
        raise ValueError("one unambiguous actual persistent deadline control required")
    result = json.loads(deadline[0].read_text())
    if (
        result.get("status") != "timeout"
        or result.get("timeout_limit") != "global_resource_stage"
    ):
        raise ValueError("global deadline native result invalid")
    cases["recovery:global_deadline"] = {
        "passed": True,
        "native_fixture": True,
        "result": result,
        "input_files": {
            p.name: p.read_text()
            for p in (deadline[0].parent / "workspace").glob("*.thy")
        },
        "reference_terms": json.loads(
            (deadline[0].parent / "reference/reference.json").read_text()
        )["terms"],
        "fixture_directory": str(deadline[0].parent),
    }
    required = {
        operation + ":" + case
        for operation, spec in OPERATIONS.items()
        for case in spec["cases"]
    }
    if set(cases) != required:
        raise ValueError(
            "native case inventory differs: " + str(sorted(required - set(cases)))
        )
    return receipt, cases


def _normalized(row, resource_hash):
    result = row["result"]
    outcomes = result.get("outcomes", [])
    status = result.get("status")
    if status is None:
        status = (
            "complete"
            if result.get("complete")
            else "incomplete"
            if "complete" in result
            else [r["status"] for r in outcomes]
        )
    certified = sorted(
        r.get("id", r.get("contract_id", r.get("node_id", r.get("contract"))))
        for r in outcomes
        if r.get("status") == "proved"
    )
    return {
        "verdict": status,
        "certified_target_ids": certified,
        "context_sha256": digest(row["input_files"]),
        "proposition_sha256": digest(row["reference_terms"]),
        "error_class": result.get("failure_kind", result.get("timeout_limit")),
        "resource_policy_sha256": resource_hash,
    }


def assemble_receipts(manifest, *, a_run, b_run, output_directory):
    """Return a copied manifest with actual comparisons and activation receipts.

    Activation denotes real use of the common facade by these native controls.
    It does not claim the pre-existing A multi-agent pipeline was reconfigured.
    """
    source = {"A": _read_run(a_run, manifest), "B": _read_run(b_run, manifest)}
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    result = json.loads(json.dumps(manifest))
    binding = binding_identity(manifest)

    def put(name, data):
        path = output / name
        encoded = json.dumps(data, indent=2) + "\n"
        if path.exists() and path.read_text() != encoded:
            raise ValueError("refusing to rewrite immutable conformance evidence")
        path.write_text(encoded)
        return {"path": name, "sha256": file_hash(path)}

    for machine, (run, _) in source.items():
        put(machine + "-run.json", run)
        entry = "src/vero/isabelle/resource_controller.py"
        result["activations"][machine] = put(
            machine + "-activation.json",
            {
                "machine": machine,
                "binding_sha256": binding,
                "adapter": PROTOCOL_ID,
                "status": "active",
                "entrypoint": entry,
                "entrypoint_sha256": manifest["common_facade"]["files"][entry],
                "activation_basis": "real_native_common_facade_controls",
                "existing_a_pipeline_reconfigured": False,
            },
        )
    for case in source["A"][1]:
        rows = {machine: source[machine][1][case] for machine in ("A", "B")}
        normalized = {
            machine: _normalized(row, manifest["resource_policy_sha256"])
            for machine, row in rows.items()
        }
        if normalized["A"] != normalized["B"]:
            raise ValueError("A/B native results differ: " + case)
        name = case.replace(":", "-")
        raw = {
            machine: put(name + "-" + machine + "-raw.json", row)
            for machine, row in rows.items()
        }
        result["conformance"][case] = put(
            name + ".json",
            {
                "binding_sha256": binding,
                "case": case,
                "native": True,
                "passed": True,
                "input_sha256": digest(
                    {
                        "case": case,
                        "context": normalized["A"]["context_sha256"],
                        "propositions": normalized["A"]["proposition_sha256"],
                    }
                ),
                "results": normalized,
                "raw_evidence": raw,
            },
        )
    # Validation, not this builder, determines readiness against current sources.
    return result
