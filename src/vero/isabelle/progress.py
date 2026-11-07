"""Rebuildable checkpoint presentation; never an acceptance or scheduling ledger."""
from __future__ import annotations

import json
import warnings
from pathlib import Path


def project(root, journal):
    root = Path(root)
    snapshots = []
    elapsed = 0.0
    for record in journal.get("iterations", []):
        elapsed += record.get("agent", {}).get("elapsed_seconds", 0)
        report_path = Path(record["report_md"]).with_suffix(".json")
        try:
            report = json.loads((root / report_path).read_text())
        except (OSError, ValueError):
            report = {}
        proved = record["proved_specs"]
        refuted = record["disproved_specs"]
        modules = {k: v.get("status") for k, v in report.get("modules", {}).items()}
        snapshots.append({
            "index": record["index"], "snapshot_id": f"iter-{record['index']:03d}",
            "artifact": record["artifact"],
            "artifact_sha256": report.get("artifact_sha256"),
            "report": str(report_path), "report_available": bool(report),
            "proved": proved, "refuted": refuted, "resolved": proved + refuted,
            "total": report.get("total_specs"), "agent_seconds": elapsed,
            "module_status": modules,
            "module_check_failed": any(v != "ok" for v in modules.values()) if modules else None,
            "integration": report.get("integration"), "seals": report.get("seals"),
        })
    # Select ONE existing artifact. Never independently maximize polarities or
    # combine target identities from incompatible historical proof contexts.
    best = max(snapshots, key=lambda s: (
        s["resolved"], (s.get("integration") or {}).get("status") == "ok", -s["index"]
    ), default=None)
    current = snapshots[-1] if snapshots else None
    regression = bool(current and best and current["resolved"] < best["resolved"])
    return {
        "schema": "checkpoint-progress-v1", "run_status": journal.get("status"),
        "basis": "independently evaluated checkpoint; not a permanent per-contract GREEN ledger",
        "best_selection": "resolved count, integration success, earliest checkpoint; display only",
        "current_snapshot": current, "best_snapshot": best,
        "headline": {
            "basis": "best coherent evaluated checkpoint, not union of historical targets",
            "snapshot_id": best["snapshot_id"] if best else None,
            "proved": best["proved"] if best else None,
            "refuted": best["refuted"] if best else None,
            "resolved": best["resolved"] if best else None,
        },
        "final_integration": (
            {"snapshot_id": current["snapshot_id"], "result": current["integration"]}
            if current and journal.get("status") == "complete" else None
        ),
        "current_below_best": regression,
        "regression_note": (
            "Current snapshot has fewer validated results; this does not establish refutation or loss of historical proofs."
            if regression else None
        ),
        "snapshots": snapshots,
    }


def markdown(view):
    """Keep snapshot occupancy separate from historical best in human reports."""
    lines = ["# Checkpoint progress", "", f"Run status: {view['run_status']}", "",
             "Counts describe one evaluated artifact; they are not a permanent per-contract GREEN ledger.", "",
             "| Snapshot | Proved | Refuted | Resolved | Module check failed | Integration | Seals |",
             "|---|---:|---:|---:|---|---|---|"]
    for label, key in [("Current", "current_snapshot"), ("Historical best", "best_snapshot")]:
        row = view[key]
        if row:
            integration = (row["integration"] or {}).get("status", "not checked")
            seals = "not enabled / unavailable" if row["seals"] is None else "see progress.json"
            lines.append(f"| {label}: {row['snapshot_id']} | {row['proved']} | {row['refuted']} | {row['resolved']} | {row['module_check_failed']} | {integration} | {seals} |")
    if view["regression_note"]:
        lines.extend(["", view["regression_note"]])
    lines.extend(["", "Raw per-checkpoint counts and source identities remain in progress.json.", ""])
    return "\n".join(lines)


def export(root, journal):
    """A sidecar failure cannot alter proof acceptance, completion or recovery."""
    from .benchmark import save
    try:
        view = project(root, journal)
        save(Path(root) / "progress.json", view)
        (Path(root) / "progress.md").write_text(markdown(view))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        warnings.warn(f"Progress display export unavailable: {exc}", RuntimeWarning)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    state = json.loads((args.run_directory / "iterations.json").read_text())
    print(json.dumps(project(args.run_directory, state), indent=2))
