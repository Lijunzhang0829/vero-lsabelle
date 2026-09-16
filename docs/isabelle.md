# Isabelle proof mode

This backend ports Vero's repository-agent → artifact extraction → independent evaluation loop to Isabelle. It starts from fixed contract declarations, renders positive and negative proof slots, lets a repository-level Codex agent work on the whole repository, then evaluates the extracted bodies in newly rendered theories. It does not use the repository-planning/GRA pipeline. The original Lean commands and benchmark format remain available; Isabelle uses the separate `vero-isabelle` entry point (`PYTHONPATH=src python3 -m vero.isabelle` also works without installing the Lean dependencies).

## Frozen inputs and proof semantics

`prepare` consumes the sanitized environment's `contracts.jsonl`. Each contract keeps its exact proposition, attributes, and enclosing context; its binding becomes `cNNNN_prove` or `cNNNN_disprove`. Adjacent declarations with identical enclosing contexts share one context block. This avoids repeatedly interpreting `Arch` while preserving the declarations' assumptions. Legacy `run` retains independent owner modules. The new `plan-run` first runs a separate filesystem-capable PLAN agent, then freezes its grouping, contract order, module imports and execution order. Completed earlier slots and imported generated theorems can be reused; imports are real Isabelle theory imports.

Every contract has two slots. Fill exactly one, leaving the other `oops`. Positive proofs count toward Refine completion. A negative result proves the negation of the universally closed contract, including its own assumptions; it is reported separately. The trusted conversion reads **raw assumptions**, preserving nested meta-quantifiers instead of using their normalized theorem rules. Locale/context assumptions remain in the enclosing context. `includes` and `obtains` are not supported for negative contracts; neither occurs in this Refine input.

The evaluator extracts bodies only, ignores changes to frozen declarations, rejects malformed or duplicate markers and symlinks, and renders a fresh workspace. Proof slots allow ordinary Isar and proof methods, with conservative rejection of raw ML and outer declarations. Successful goals must emit the trusted command's audit record, have no `skip_proof` or oracle dependencies, and introduce no axioms beyond the fixed imported theory. A module must finish successfully before any of its completed goals receive credit. This deliberately makes unfinished candidates in a module affect that module's score. The complete task additionally requires an integrated `Vero_All` check and positive proofs for all contracts.

An empty `oops` preflight checks parsing and contexts only; it is **not** evidence of proving any contract. The copied parent heap is `Semantic_Base`, built from sanitized definitions and contract shells. Stock target heaps and original target proofs are excluded. Legal upstream facts remain available through the sanitized runtime and premise catalog.

## Commands

```bash
PYTHONPATH=src python3 -m vero.isabelle verify-environment --environment /path/to/portable-environment
PYTHONPATH=src python3 -m vero.isabelle prepare --contracts /path/to/contracts.jsonl --output benchmarks/MyIsabelle
PYTHONPATH=src python3 -m vero.isabelle render --benchmark benchmarks/MyIsabelle/benchmark.json --output /tmp/my-sandbox
PYTHONPATH=src python3 -m vero.isabelle run --benchmark /benchmark/benchmark.json --config /control/config.json --output /results/trial
```

Inside the worker, the agent can run `python3 /backend/tool.py search QUERY`, `check MODULE`, and `progress`. Progress reports filled slots, not verified proofs.

`run --resume` binds to the same benchmark, configuration, and implementation hashes. It records each submitted call and its wall-clock allowance before invocation. Recovery refuses to overwrite a live agent process, reuses a completed independent evaluation, and charges the full reserved allowance when actual interrupted usage is unknown. Agent failures are distinct from an exhausted proving budget. Monetary cost is `null` when the CLI does not supply it; token usage comes from completed-turn events and can be missing on interruption.

For an explicitly authorized continuation after an exhausted run, use a new run directory and budget. The optional `initial_proofs` configuration maps slot IDs (for example `c0001:prove`) to previously independently checked proof bodies. These bodies are frozen as part of the configuration binding; record their source artifact and evaluation in `continuation_provenance`. They are inputs to the next agent and must pass the fresh final evaluator again. This does not reset the prior run’s charged budget or make imported proofs count as newly generated results.

## Isolated deployment

`scripts/deploy_isabelle_worker.py --help` lists the deployment arguments. Use a dedicated Docker worker with sanitized runtime, parent heaps, benchmark, and backend code mounted read-only. The agent is UID 1001; the final Isabelle process is UID 1002. The supervising process owns the configuration and results. User settings, homes, and mutable proof sources are separated. The worker cannot mount the original Refine checkout or Docker socket. A working directory alone is not an isolation boundary. On machine B, unprivileged user namespaces prevent the nested Codex sandbox from starting. The dedicated nonroot Docker deployment therefore selects `danger-full-access` for the inner CLI and retains the outer container/user/mount controls; the adapter rejects this setting outside that deployment. This follows the [official CLI guidance for isolated runners](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

The deployment script accepts an explicitly authorized temporary authentication file. Stop/remove the worker and delete both its private source copy and `agent-home/.codex/auth.json` when the experiment ends. If using `--proxy`, it should refer to an authorized proxy bound to the worker host's loopback address; the worker uses host networking for that option. This deployment is for a cooperative coding agent, not a general hostile multi-tenant security service.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests/isabelle -v
VERO_TEST_ISABELLE=/path/to/isabelle PYTHONPATH=src python3 -m unittest discover -s tests/isabelle -v
ruff check src/vero/isabelle scripts/deploy_isabelle_worker.py tests/isabelle
ruff format --check src/vero/isabelle scripts/deploy_isabelle_worker.py tests/isabelle
```

The second command additionally checks real HOL proofs, counterexamples, vacuous assumptions, quantified assumptions of different types, and rejection of `sorry` even when Isabelle's `quick_and_dirty` option is enabled. `reference/IsabelleMini` is the Isabelle fixture; the Lean canonical example is unchanged.

## Per-contract diagnostic records

Each `tool.py check MODULE` writes `.vero-attempts/<unique-id>/submitted.json` before checking, including the frozen benchmark identity, every slot body in that module, and a candidate hash. It preserves the rendered `source.thy`, `checker.log`, and `result.json`. A process killed before completion leaves a submission without a result. The first diagnostic line is mapped to the relevant declaration/proof range; a module failure does not blame its siblings, and timeouts without locations remain unattributed.

These are agent-writable diagnostic records, not independent proof certificates. The supervisor archives regular files to `iter-NNN/attempts/` and writes `attempt-ledger.json` after independent evaluation, including recovery. A malformed diagnostic archive produces an explicit telemetry error without changing independent evaluation results. Repeated checks of an unchanged body remain distinguishable by body hash; they are not different candidate proofs. Changes never submitted to the check tool are not captured by this mechanism.

To build a readable ledger with one JSON file per contract:

```bash
PYTHONPATH=src python3 -m vero.isabelle.attempts --benchmark benchmarks/RefineIsabelle/benchmark.json --evaluation /results/trial/iter-000/eval/report.json --attempt-root /results/trial/iter-000/attempts --output /results/ledger
```

For old runs, repeat `--trace PATH` in chronological run order. Legacy marker mentions and module checks are evidence only, not precise attempt counts; absent history is reported as unknown. Use `contracts/cNNNN.json` for full evidence, latest candidate diagnostic, final status and candidate-withdrawal indicators. The first historical Refine ledger is in `reports/refine-contract-ledger-20260915/`.

## PLAN before generation

`python -m vero.isabelle plan-run --benchmark SOURCE --config CONFIG --output RUN` runs a separate planning agent before the generation agent. `plan_seconds` defaults to 600; `plan_preflight_seconds` defaults to 300. Generation retains its separate configured time allowance. The planning task incrementally writes `.vero/plan.json`; the supervisor saves changed content snapshots every two seconds (not every filesystem write), the complete emitted agent event stream, submitted budget, returned plan and validation results. A final response alone cannot complete PLAN.

Schema version 1 includes `source_benchmark_id`, module entries (`name`, ordered `contracts`, `imports`, `rationale`) and an `execution_order`. Exact-once coverage, known IDs, safe nonreserved module names, dependency topology and source identity are validated. Contract statements and contexts come exclusively from the source benchmark; the plan cannot replace them. This is an Isabelle adaptation of Vero's artifact-driven PLAN stage, not the original Lean translation schema. Shared new top-level helper declarations are not introduced by this version.

The accepted plan is compiled to a distinct benchmark identity. A fresh all-`oops` native Isabelle preflight validates declarations/imports, not proofs. Only after it passes does generation start. `.vero/plan.json` guides the repository agent; rendered imports and declaration order enforce the plan's available context. The agent is instructed to work providers before consumers, but its actual command order remains agent-controlled. The final evaluator follows topological module order. An imported theory build failure yields `blocked_dependency` for downstream pending candidates; an unfilled contract is never made available as a theorem.

In the dedicated worker, set `plan_public_benchmark` to `/run/vero-plan/benchmark.json`: the supervisor owns this file and its directory, while the generator's check tool reads it through `VERO_BENCHMARK`. Agent changes to the sandbox's plan/benchmark do not change the frozen checking context. Module checks include their import closure's current proof bodies and reconstruct all declarations afresh. Final independent evaluation still audits every credited theorem and performs integration only after all contracts are resolved.

`--resume` requires the same pipeline binding. It does not silently restart an interrupted PLAN agent or reset its allowance; if a usable plan remains on disk it can be validated, otherwise an explicit repair/rebudget decision is required. Accepted plan/benchmark identity is rechecked, and the existing generation runner handles its own interruption recovery. Planning validation failures are saved and stop generation.
