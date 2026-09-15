# Isabelle proof mode

This backend ports Vero's repository-agent → artifact extraction → independent evaluation loop to Isabelle. It starts from fixed contract declarations, renders positive and negative proof slots, lets one Codex agent work on the whole repository, then evaluates the extracted bodies in newly rendered theories. It does not use the repository-planning/GRA pipeline. The original Lean commands and benchmark format remain available; Isabelle uses the separate `vero-isabelle` entry point (`PYTHONPATH=src python3 -m vero.isabelle` also works without installing the Lean dependencies).

## Frozen inputs and proof semantics

`prepare` consumes the sanitized environment's `contracts.jsonl`. Each contract keeps its exact proposition, attributes, and enclosing context; its binding becomes `cNNNN_prove` or `cNNNN_disprove`. Adjacent declarations with identical enclosing contexts share one context block. This avoids repeatedly interpreting `Arch` while preserving the declarations' assumptions. Owner modules are independent, and completed preceding slots in the same module are reusable facts. This first backend does not introduce cross-owner generated-proof dependencies.

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
