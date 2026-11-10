# Contextual refutation for new repository benchmarks

The slot-based VERO runner and the current binomial Codex baseline share a trusted Isabelle checker. False contracts must be resolved by a checked proof of their closed negation. A failed positive attempt, timeout, model explanation or successful CLI exit is not a refutation. This document does not claim support in the older freeform/resource-v2 Codex frontend, which has a separate positive-only protocol.

New benchmarks prepared by `prepare` always use `owned-context-outcomes-v1`; there is no scope-selection CLI argument. The scope is a frozen semantic version, not a guess about whether the target is true. Ordinary runs of these benchmarks default to resolving contracts by checked positive or negative outcomes. A user can still explicitly select the historical positive-only completion metric when that is the intended experiment.

`vero_refute_capture` records owned environment E before parsing the fixed statement, then separates statement assumptions A and parameters x from that environment at Isabelle's native context boundary. It mechanically constructs E implies not (ALL x. A x implies Q x), exports it, and starts the negative proof outside the local context. All input assumptions remain inside the negated target. Concurrent modules retain separate captures in immutable theory data. The native audit rejects skip proofs, oracles, unauthorized axioms and residual hypotheses, and binds the exact negative term. Its scope receipt includes native ASTs for E, A, the local target and both obligations.

Reports retain negative polarity and add result_scope plus separate contextual/strict negative totals. An owned-context negative certificate does not establish environment satisfiability, fixed global constants or an actual deployment, and is never a positive premise. Trusted preparation must audit environment evidence separately. Unsupported native statement forms fail closed.

Historical benchmarks without the new policy marker select `Vero_Support_Strict_Global.thy`, whose original global-negation implementation is preserved byte-for-byte. Their benchmark identities, results and frozen deployments are not migrated. The shared VERO/official-Codex rules describe the default and this historical boundary; the seven-service interface and agent-controlled strategy are unchanged.

For a fresh Isabelle2026 benchmark:

```bash
PYTHONPATH=src python3 -m vero.isabelle prepare --contracts contracts.jsonl --output benchmark --base-import PROJECT_INPUT --isabelle-api 2026
```

The default API is `legacy`, suitable for machine B's existing Isabelle toolchain. The selected 2026 API is part of the content-addressed benchmark policy; the adaptation only changes Specification and Outer_Syntax signatures. The checker process transport must also match the installation: Isabelle2026 uses `process_theories`, whereas the older checker transport uses `process`. C dataset preparation already supplies a transport adapter; selecting the API alone does not replace that adapter.

Set `completion_metric` to `resolved` in the run configuration to accept proved or disproved contracts. Both finite-budget checkpoints and contract-retry/continuous modes use this metric. Positive-only mode remains available for previously proved benchmarks. Full integration and any configured theory seals/builds must pass before repository completion. Best-checkpoint ranking uses the same metric in ordinary execution and interruption recovery.

Accepted negative results preserve their polarity, artifact and attempt count across controller restarts and lost replies. Replaying a completed request does not charge again. Changing an accepted negative to a positive candidate cannot overwrite it through a retry submission. Metrics distinguish `contracts_green`, `contracts_disproved` and `contracts_resolved`; token costs retain their existing response accounting and missing-cost semantics.

Persistent session continuation binds an explicit thread ID from this run's durable result or unambiguous `thread.started` event. The adapter never issues `resume --last`. If the binding is missing, the runner keeps the workspace/accepted checkpoint and starts a fresh session with a recovery notice; a direct resume request without a bound ID fails before starting a process. Torn event tails are ignored and multiple different thread identities are treated as ambiguous.

## Validation

```bash
PYTHONPATH=src:tests/isabelle python3 -m unittest test_contextual_refutation.PolicyTests
VERO_TEST_ISABELLE=/path/to/legacy/isabelle PYTHONPATH=src:tests/isabelle python3 -m unittest test_contextual_refutation test_kernel
VERO_TEST_ISABELLE2026=/path/to/Isabelle2026/bin/isabelle PYTHONPATH=src:tests/isabelle python3 -m unittest test_contextual_refutation.NativeContextTests
```

These are generic HOL and fault-injection fixtures, not benchmark answers or fresh model generation. They cover false/true statements, inconsistent and satisfiable outer assumptions, nested contexts, locales, definitions, type sorts, parallel imports, incremental seals, exact-once recovery and separate positive/negative metrics. They do not establish a 48-hour stability guarantee or that a counterexample to a formal model is also a bug in the upstream Python/C implementation.

## Frozen runs and deployment

Freeze the actual source tree and hash every runtime file before a new run; record whether it is a committed tree or working-tree snapshot. Mount tests only in controller validation, never in the generator. Use the new release for both slot-based baselines, with the same benchmark and semantic environment. Preserve each method's disclosed agent strategy and budget.

Do not patch an existing frozen run or resume its journal under changed implementation hashes. Previous nonzero negative results need independent replay under corrected semantics before inheritance. Inherited proofs/refutations remain separate from newly generated results. Historical preparation scripts that explicitly archive `db0d68f` still select that historical verifier and must not be treated as a deployment of this release.

Authoritative run status comes from `iterations.json`, per-iteration native reports and process/exit evidence. A deployment launch marker or an Up container alone is not evidence that generation is still active.
