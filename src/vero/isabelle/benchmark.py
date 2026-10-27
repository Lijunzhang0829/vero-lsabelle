"""Frozen, content-addressed Isabelle contracts and deterministic proof slots."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

NAME = re.compile(r"^(lemma|theorem|corollary|proposition)\s+(\S+?)(?=\s|\[|:)")
MARKER = re.compile(
    r"^\(\* VERO (START|END) ([A-Za-z0-9_]+) (prove|disprove|prove_aux|disprove_aux|global_aux|imports) \*\)$",
    re.MULTILINE,
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    tmp.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def validate(bench):
    if bench.get("schema_version", 1) not in {1, 2}:
        raise ValueError("unsupported Isabelle slot schema")
    body = {k: v for k, v in bench.items() if k != "benchmark_id"}
    if bench.get("benchmark_id") != digest(body):
        raise ValueError("benchmark identity mismatch")
    ids = [c["id"] for c in bench["contracts"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate contract id")
    for c in bench["contracts"]:
        if not re.fullmatch(r"c[0-9]{4}", c["id"]) or not NAME.match(c["statement"]):
            raise ValueError("invalid contract declaration")
        if not re.fullmatch(r"Vero_[A-Za-z0-9_]+", c["module"]):
            raise ValueError("invalid module")
    return bench


def prepare(
    contracts_path,
    output,
    *,
    base_import="Semantic_Base.Semantic_Root",
    provenance=None,
):
    records = [
        json.loads(x)
        for x in Path(contracts_path).read_text().splitlines()
        if x.strip()
    ]
    contracts = []
    for i, item in enumerate(records, 1):
        statement = item["exact_statement"]
        if not NAME.match(statement):
            raise ValueError("unsupported declaration: " + item["contract_id"])
        contracts.append(
            {
                "id": f"c{i:04d}",
                "source_id": item["contract_id"],
                "name": item["name"],
                "statement": statement,
                "context": item.get("contract_owned_context", []),
                "module": "Vero_" + re.sub(r"[^A-Za-z0-9_]", "_", item["owner_theory"]),
            }
        )
    body = {
        "schema_version": 2,
        "backend": "isabelle",
        "mode": "proof",
        "base_import": base_import,
        "contracts": contracts,
        "provenance": provenance or {},
        "policy": {
            "unfilled": "oops",
            "reference_proofs_visible": False,
            "cross_module_proof_imports": True,
            "auxiliary_slots": True,
            "editable_generated_imports": True,
        },
    }
    bench = {**body, "benchmark_id": digest(body)}
    validate(bench)
    save(Path(output) / "benchmark.json", bench)
    return bench


def slot_key(c, polarity):
    return c["id"] + ":" + polarity


def declaration(c, polarity):
    # Only the binding is changed. Types, context, attributes and proposition stay frozen.
    command = "vero_prove" if polarity == "prove" else "vero_disprove"
    statement = NAME.sub(
        lambda m: command + ' "' + c["id"] + '" ' + c["id"] + "_" + polarity,
        c["statement"],
        count=1,
    )
    return statement


def render(bench, destination, bodies=None, *, support_path=None):
    from .slots import block, enabled

    validate(bench)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    bodies = bodies or {}
    modules = sorted({c["module"] for c in bench["contracts"]})
    support = Path(
        support_path or Path(__file__).with_name("Vero_Support.thy")
    ).read_text()
    support = support.replace("IMPORT_BASE", bench["base_import"])
    (destination / "Vero_Support.thy").write_text(support)
    for module in modules:
        dependencies = bench.get("planning", {}).get("imports", {}).get(module, [])
        imports = " ".join(["Vero_Support", *dependencies])
        extra = (
            "\n" + block(module + ":imports", bodies.get(module + ":imports", ""))
            if enabled(bench)
            else ""
        )
        chunks = [f"theory {module}\nimports {imports}{extra}\nbegin\n"]
        chunks.append(f'ML \\<open>writeln "VERO_THEORY_ENTER {module}"\\<close>')
        if enabled(bench):
            chunks.append(
                block(module + ":global_aux", bodies.get(module + ":global_aux", ""))
            )
        active_context = []
        for c in bench["contracts"]:
            if c["module"] != module:
                continue
            if c["context"] != active_context:
                chunks.extend("end" for _ in active_context)
                chunks.extend(c["context"])
                active_context = c["context"]
            for polarity in ["prove", "disprove"]:
                if enabled(bench):
                    key = slot_key(c, polarity) + "_aux"
                    chunks.append(block(key, bodies.get(key, "")))
                chunks.extend(
                    [
                        declaration(c, polarity),
                        f"(* VERO START {c['id']} {polarity} *)",
                        bodies.get(slot_key(c, polarity), "oops").strip(),
                        f"(* VERO END {c['id']} {polarity} *)",
                    ]
                )
        chunks.extend("end" for _ in active_context)
        chunks.append("end\n")
        (destination / (module + ".thy")).write_text("\n\n".join(chunks))
    (destination / "Vero_All.thy").write_text(
        "theory Vero_All\nimports\n"
        + "\n".join("  " + m for m in modules)
        + '\nbegin\nML \\<open>writeln "VERO_THEORY_ENTER Vero_All"\\<close>\nend\n'
    )
    save(destination / "benchmark.json", bench)
    return modules


def extract(bench, source):
    validate(bench)
    source = Path(source)
    from .slots import schedule

    expected = schedule(bench)
    bodies, errors, extras = {}, {}, []
    for module in sorted({c["module"] for c in bench["contracts"]}):
        path = source / (module + ".thy")
        if path.is_symlink():
            errors[module] = "symlink rejected"
            continue
        try:
            text = path.read_text()
        except OSError as exc:
            errors[module] = str(exc)
            continue
        markers = list(MARKER.finditer(text))
        local = {}
        try:
            if len(markers) % 2:
                raise ValueError("unpaired marker")
            for start, end in zip(markers[::2], markers[1::2]):
                key = start[2] + ":" + start[3]
                if (
                    start[1] != "START"
                    or end[1] != "END"
                    or start.groups()[1:] != end.groups()[1:]
                ):
                    raise ValueError("mismatched marker")
                if key in local:
                    raise ValueError("duplicate marker")
                if key not in expected:
                    extras.append(key)
                    continue
                owner = expected[key][0]
                if owner != module:
                    raise ValueError("slot moved to another module")
                local[key] = text[start.end() : end.start()].strip()
            bodies.update(local)
        except ValueError as exc:
            errors[module] = str(exc)
    return {
        "schema_version": 1,
        "backend": "isabelle",
        "benchmark_id": bench["benchmark_id"],
        "slots": {
            key: {"found": key in bodies, "body": bodies.get(key, "")}
            for key in sorted(expected)
        },
        "extras": extras,
        "file_errors": errors,
    }


def validate_artifact(bench, artifact):
    if artifact.get("benchmark_id") != bench["benchmark_id"]:
        raise ValueError("artifact belongs to a different benchmark")
    from .slots import schedule

    expected = schedule(bench)
    if set(artifact.get("slots", {})) != set(expected):
        raise ValueError("artifact slot inventory mismatch")
    for slot in artifact["slots"].values():
        if not isinstance(slot.get("body"), str) or not isinstance(
            slot.get("found"), bool
        ):
            raise TypeError("invalid artifact slot")
    return artifact
