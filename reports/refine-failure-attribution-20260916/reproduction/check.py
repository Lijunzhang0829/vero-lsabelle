import hashlib
import json
from pathlib import Path

from vero.isabelle.benchmark import digest, read, render, save
from vero.isabelle.checker import Checker, filled, hygiene

root = Path("/original/pipeline-02")
g = root / "generation/iter-000"
out = Path("/results")
b = read(root / "planning/benchmark.json")
a = read(g / "artifact.json")
cfg = read(root / "generation-config.json")["checker"]
checker = Checker(cfg)
base = {k: v["body"] for k, v in a["slots"].items() if v["found"] and filled(v["body"])}
save(
    out / "binding.json",
    {
        "benchmark_id": b["benchmark_id"],
        "original_artifact_sha256": digest(a),
        "checker": cfg,
        "model_calls": 0,
        "intervention": "post-hoc checker-only; no new proof bodies",
        "implementation": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path("/vero-src/vero/isabelle").glob("*.py")
        },
    },
)
cases = [("finalise-original", "Vero_FinaliseSlot", base, {"changed_slots": []})]
isolated = dict(base)
isolated["c0071:prove"] = "oops"
cases.append(
    (
        "finalise-without-c0071",
        "Vero_FinaliseSlot",
        isolated,
        {
            "changed_slots": ["c0071:prove"],
            "operation": "replace only this candidate with oops; all other bodies unchanged",
        },
    )
)
seen = set()
for p in sorted((g / "attempts").glob("*/submitted.json")):
    s = read(p)
    v = s["slots"].get("c0162:prove")
    if not v or not filled(v["body"]) or digest(v["body"]) in seen:
        continue
    seen.add(digest(v["body"]))
    bodies = {
        k: x["body"] for k, x in s["slots"].items() if x["found"] and filled(x["body"])
    }
    # Isolate target while retaining exact frozen declarations/contexts/imports.
    bodies = {k: v["body"] for k in ["c0162:prove"]}
    cases.append(
        (
            f"c0162-candidate-{len(seen)}",
            "Vero_StateFields",
            bodies,
            {
                "original_submission": p.parent.name,
                "candidate_sha256": digest(v["body"]),
                "operation": "retain target body exactly; all other slots oops; module has no planned imports",
            },
        )
    )
summary = []
for name, module, bodies, intervention in cases:
    d = out / name
    d.mkdir()
    assert all(hygiene(v) is None for v in bodies.values() if filled(v))
    render(b, d / "source", bodies)
    save(
        d / "manifest.json",
        {
            "module": module,
            "bodies": bodies,
            "intervention": intervention,
            "source_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (d / "source").glob("*.thy")
            },
        },
    )
    result = checker.check(d / "source", module)
    save(d / "result.json", result)
    (d / "checker.log").write_text(result["log"])
    summary.append(
        {
            "case": name,
            **{k: result[k] for k in ["status", "audited", "elapsed_seconds"]},
        }
    )
    save(out / "progress.json", summary)
    print(json.dumps(summary[-1]), flush=True)
save(out / "summary.json", summary)
