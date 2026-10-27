#!/usr/bin/env python3
"""Summarize a completed Isabelle trial without inferring unreported token usage."""

import argparse
import collections
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    benchmark = read(args.benchmark)
    run = Path(args.run)
    journal = read(run / "iterations.json")
    rows = []
    for record in journal["iterations"]:
        iteration = run / Path(record["artifact"]).parent
        report = read(iteration / "eval/report.json")
        if report["benchmark_id"] != benchmark["benchmark_id"]:
            raise ValueError("benchmark mismatch")
        module_counts = {}
        for c in benchmark["contracts"]:
            counts = module_counts.setdefault(c["module"], collections.Counter())
            counts["total"] += 1
            counts[report["contracts"][c["id"]]["status"]] += 1
        commands = collections.Counter()
        errors = []
        for line in (iteration / "agent/agent_events.jsonl").read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item", {})
            if (
                event.get("type") == "item.completed"
                and item.get("type") == "command_execution"
            ):
                commands["completed"] += 1
                commands["succeeded" if item.get("exit_code") == 0 else "failed"] += 1
                if "/backend/tool.py check " in item.get("command", ""):
                    commands["shell_commands_mentioning_check_tool"] += 1
            if event.get("type") in {"error", "turn.failed"}:
                errors.append(event.get("message", event.get("error", "unknown")))
        rows.append(
            {
                "iteration": record["index"],
                "agent": record["agent"],
                "proved": report["proved_specs"],
                "disproved": report["disproved_specs"],
                "total": report["total_specs"],
                "complete": report["complete"],
                "evaluation_seconds": report["elapsed_seconds"],
                "modules": module_counts,
                "shell_commands": commands,
                "cli_errors": errors,
            }
        )
    summary = {
        "benchmark_id": benchmark["benchmark_id"],
        "status": journal["status"],
        "iterations": rows,
    }
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    text = [
        "# Isabelle Refine trial",
        "",
        "Benchmark: `" + benchmark["benchmark_id"] + "`.",
        "",
        "Run status: `" + journal["status"] + "`.",
        "",
    ]
    for row in rows:
        agent = row["agent"]
        text += [
            f"## Iteration {row['iteration']}",
            "",
            f"Positive proofs: **{row['proved']}/{row['total']}**. Negative proofs: {row['disproved']}. Entire task complete: {row['complete']}.",
            "",
            f"Agent wall time: {agent['elapsed_seconds']:.2f}s. Independent evaluation: {row['evaluation_seconds']:.2f}s. Model: `{agent.get('model', 'unknown')}`. Agent OK: {agent['ok']}. Timeout: {agent.get('timed_out', 'unknown')}.",
            "",
            "Reported usage: `"
            + json.dumps(agent.get("usage", {}))
            + "`. Missing usage is unknown, not zero. Dollar cost is unknown unless explicitly reported.",
            "",
            "| Module | Contracts | Proved | Disproved | Other statuses |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for module, counts in sorted(row["modules"].items()):
            other = (
                ", ".join(
                    f"{key}: {value}"
                    for key, value in sorted(counts.items())
                    if key not in {"total", "proved", "disproved"}
                )
                or "none"
            )
            text.append(
                f"| {module} | {counts['total']} | {counts.get('proved', 0)} | {counts.get('disproved', 0)} | {other} |"
            )
        text += [
            "",
            "Shell telemetry: `" + json.dumps(row["shell_commands"]) + "`.",
            "",
            "CLI errors: `" + json.dumps(row["cli_errors"]) + "`.",
            "",
        ]
    text += [
        "This is a bounded pilot on one model and one input snapshot. It is not a statistical comparison with another system. Negative proofs do not count as Refine completion. Module errors can invalidate otherwise completed slots within that module.",
        "",
    ]
    (output / "summary.md").write_text("\n".join(text))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "iterations": [
                    {k: v for k, v in r.items() if k not in {"modules", "cli_errors"}}
                    for r in rows
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
