"""Diagnostic attempt snapshots; never an authority for proof acceptance."""

import argparse
import collections
import json
import re
import time
import uuid
from pathlib import Path

from .benchmark import digest, read, save
from .checker import filled


def begin(root, bench, module, artifact):
    path = Path(root) / (str(time.time_ns()) + "-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    from .slots import schedule

    slot_schedule = schedule(bench)
    slots = {
        k: v
        for k, v in artifact["slots"].items()
        if slot_schedule[k] == (module, "proof")
    }
    save(
        path / "submitted.json",
        {
            "benchmark_id": bench["benchmark_id"],
            "module": module,
            "started_at": time.time(),
            "slots": slots,
            "candidate_sha256": digest(slots),
            "file_errors": artifact.get("file_errors", {}),
            "context_artifact": artifact,
            "context_artifact_sha256": digest(artifact),
            "authority": "agent-side diagnostic only",
        },
    )
    return path


def finish(path, result, source):
    path = Path(path)
    submitted = read(path / "submitted.json")
    module = submitted["module"]
    log = result.get("log", "")
    (path / "checker.log").write_text(log)
    # The first diagnostic location is authoritative only for this rendered snapshot.
    match = re.search(r'line (\d+) of "[^"\n]*/' + re.escape(module) + r'\.thy"', log)
    location = None
    auxiliary_location = None
    if source is not None:
        text = Path(source).read_text()
        (path / "source.thy").write_text(text)
        if match:
            line = int(match[1])
            from .benchmark import MARKER

            markers = list(MARKER.finditer(text))
            for start, end in zip(markers[::2], markers[1::2]):
                lo = text.count("\n", 0, start.start()) + 1
                hi = text.count("\n", 0, end.end()) + 1
                if lo <= line <= hi and start[3] not in {"prove", "disprove"}:
                    auxiliary_location = start[2] + ":" + start[3]
            declarations = list(
                re.finditer(
                    r'^vero_(?:prove|disprove)\s+"c\d{4}"\s+(c\d{4})_(prove|disprove)\b',
                    text,
                    re.MULTILINE,
                )
            )
            for start in declarations:
                lo = text.count("\n", 0, start.start()) + 1
                marker = f"(* VERO END {start[1]} {start[2]} *)"
                end = text.find(marker, start.end())
                hi = text.count("\n", 0, end) + 2 if end >= 0 else lo
                if lo <= line < hi:
                    location = start[1] + ":" + start[2]
    outcomes = {}
    for key, slot in submitted["slots"].items():
        if not slot["found"] or not filled(slot["body"]):
            outcomes[key] = "no_candidate"
        elif result["status"] == "ok" and key in result.get("audited", []):
            outcomes[key] = "check_passed_provisional"
        elif key == location:
            outcomes[key] = "diagnostic_at_this_contract"
        elif result["status"] == "ok":
            outcomes[key] = "missing_audit"
        else:
            outcomes[key] = "module_check_incomplete_unattributed"
    save(
        path / "result.json",
        {
            **result,
            "ended_at": time.time(),
            "diagnostic_slot": location,
            "diagnostic_auxiliary_slot": auxiliary_location,
            "outcomes": outcomes,
        },
    )


def ledger(bench, evaluation, traces=(), attempt_roots=()):
    rows = {
        c["id"]: {
            "id": c["id"],
            "name": c["name"],
            "module": c["module"],
            "final_status": evaluation["contracts"][c["id"]]["status"],
            "attempts": [],
            "legacy_evidence": [],
        }
        for c in bench["contracts"]
    }
    if evaluation["benchmark_id"] != bench["benchmark_id"]:
        raise ValueError("benchmark mismatch")
    module_events = collections.defaultdict(list)
    for trace in traces:
        for number, line in enumerate(Path(trace).read_text().splitlines(), 1):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item", {})
            if (
                event.get("type") != "item.completed"
                or item.get("type") != "command_execution"
            ):
                continue
            command = item.get("command", "")
            evidence = {
                "trace": str(trace),
                "line": number,
                "exit_code": item.get("exit_code"),
                "command": command,
                "output": item.get("aggregated_output", ""),
            }
            # A marker in a command is evidence of targeting, not proof the edit/check happened.
            for cid in (
                set(re.findall(r"VERO (?:START|END) (c\d{4})", command)) & rows.keys()
            ):
                rows[cid]["legacy_evidence"].append(evidence)
            for module in set(
                re.findall(r"/backend/tool.py check (Vero_\w+)", command)
            ):
                module_events[module].append(
                    {
                        "trace": str(trace),
                        "line": number,
                        "exit_code": item.get("exit_code"),
                    }
                )
    for root in attempt_roots:
        for path in sorted(Path(root).glob("*/submitted.json")):
            submitted = read(path)
            if submitted["benchmark_id"] != bench["benchmark_id"]:
                raise ValueError("attempt benchmark mismatch")
            result_path = path.with_name("result.json")
            result = read(result_path) if result_path.exists() else None
            for key, slot in submitted["slots"].items():
                if not slot["found"] or not filled(slot["body"]):
                    continue
                rows[key.split(":")[0]]["attempts"].append(
                    {
                        "path": str(path.parent),
                        "slot": key,
                        "body_sha256": digest(slot["body"]),
                        "submitted_at": submitted["started_at"],
                        "diagnostic": result.get("log", "")
                        if result
                        else "No completion record.",
                        "outcome": result["outcomes"][key]
                        if result
                        else "submitted_no_result",
                        "diagnostic_slot": result.get("diagnostic_slot")
                        if result
                        else None,
                    }
                )
    for row in rows.values():
        row["attempts"].sort(key=lambda a: a["submitted_at"])
        row["module_checks_observed"] = module_events[row["module"]]
        row["last_attempt"] = row["attempts"][-1] if row["attempts"] else None
        row["candidate_absent_at_final"] = row["final_status"] == "unfilled"
        row["withdrawn_since_observed_candidate"] = (
            bool(row["attempts"]) and row["candidate_absent_at_final"]
        )
        row["last_legacy_target_evidence"] = (
            row["legacy_evidence"][-1] if row["legacy_evidence"] else None
        )
        if row["final_status"] in ("proved", "disproved"):
            row["current_state"] = "independently_" + row["final_status"]
        elif row["attempts"]:
            row["current_state"] = "unresolved_with_attempt_records"
        elif row["legacy_evidence"]:
            row["current_state"] = "unresolved_with_legacy_target_evidence"
        else:
            row["current_state"] = "unresolved_attempt_history_unknown"
    return {
        "benchmark_id": bench["benchmark_id"],
        "evaluation_sha256": digest(evaluation),
        "contracts": list(rows.values()),
        "counts": dict(collections.Counter(r["current_state"] for r in rows.values())),
        "limitations": "Legacy command mentions are not attempt counts. Module failures are not contract failures. Agent telemetry is untrusted; final_status comes only from the independent report.",
    }


def summarize(bench, evaluation, root):
    """Broken agent diagnostics must not invalidate an independent evaluation."""
    try:
        return ledger(bench, evaluation, attempt_roots=[root])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = ledger(bench, evaluation)
        result["telemetry_error"] = str(exc)
        return result


def archive(workspace, destination):
    """Copy regular diagnostic files only, never follow agent-created symlinks."""
    import shutil

    source = Path(workspace) / ".vero-attempts"
    destination = Path(destination)
    if not source.is_dir() or source.is_symlink():
        return
    for record in source.iterdir():
        if record.is_symlink() or not record.is_dir():
            continue
        for name in ("submitted.json", "source.thy", "checker.log", "result.json"):
            path = record / name
            if path.is_file() and not path.is_symlink():
                target = destination / record.name / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--benchmark", required=True)
    p.add_argument("--evaluation", required=True)
    p.add_argument("--trace", action="append", default=[])
    p.add_argument("--attempt-root", action="append", default=[])
    p.add_argument("--output", required=True)
    args = p.parse_args()
    result = ledger(
        read(args.benchmark), read(args.evaluation), args.trace, args.attempt_root
    )
    root = Path(args.output)
    save(root / "ledger.json", result)
    lines = [
        "# Per-contract attempt ledger",
        "",
        result["limitations"],
        "",
        "| ID | Contract | Final | History |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {r['id']} | {r['name']} | {r['final_status']} | {r['current_state']} |"
        for r in result["contracts"]
    ]
    (root / "ledger.md").write_text("\n".join(lines) + "\n")
    for row in result["contracts"]:
        save(root / "contracts" / (row["id"] + ".json"), row)
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
