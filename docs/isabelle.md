# Isabelle proof mode

This backend ports Vero's repository-agent → artifact extraction → independent evaluation loop to Isabelle. It starts from fixed contract declarations, renders positive and negative proof slots, lets a repository-level Codex agent work on the whole repository, then evaluates the extracted bodies in newly rendered theories. It does not use the repository-planning/GRA pipeline. The original Lean commands and benchmark format remain available; Isabelle uses the separate `vero-isabelle` entry point (`PYTHONPATH=src python3 -m vero.isabelle` also works without installing the Lean dependencies).

New deployments default to the A-shared native backend, pinned Python/ML cores, explicit eight threads, 300-second requests and 60-second persistent ML commands, isolated full session builds, and response-level experiment accounting. See [the common profile and exports](../experiments/shared-native-v1/README.md). Frozen historical runs keep their original backend/configuration.

## Frozen inputs and proof semantics

`prepare` consumes the sanitized environment's `contracts.jsonl`. Each contract keeps its exact proposition, attributes, and enclosing context; its binding becomes `cNNNN_prove` or `cNNNN_disprove`. Adjacent declarations with identical enclosing contexts share one context block. This avoids repeatedly interpreting `Arch` while preserving the declarations' assumptions. Legacy `run` retains independent owner modules. The new `plan-run` first runs a separate filesystem-capable PLAN agent, then freezes its grouping, contract order, module imports and execution order. Completed earlier slots and imported generated theorems can be reused; imports are real Isabelle theory imports.

Every contract has two slots. Fill exactly one, leaving the other `oops`. Positive proofs count toward Refine completion. A negative result proves the negation of the universally closed contract, including its own assumptions; it is reported separately. The trusted conversion reads **raw assumptions**, preserving nested meta-quantifiers instead of using their normalized theorem rules. Locale/context assumptions remain in the enclosing context. `includes` and `obtains` are not supported for negative contracts; neither occurs in this Refine input.

The evaluator extracts bodies only, ignores changes to frozen declarations, rejects malformed or duplicate markers and symlinks, and renders a fresh workspace. Proof slots allow ordinary Isar and proof methods, with conservative rejection of raw ML and outer declarations. Successful goals must emit the trusted command's audit record, have no `skip_proof` or oracle dependencies, and introduce no arbitrary axioms beyond the fixed imported theory. Version 2 additionally permits conservative definitions with fresh `vero_aux_` names, checked against Isabelle's native definition registry. A module must finish successfully before any of its completed goals receive credit. This deliberately makes unfinished candidates in a module affect that module's score. The complete task additionally requires an integrated `Vero_All` check and positive proofs for all contracts.

An empty `oops` preflight checks parsing and contexts only; it is **not** evidence of proving any contract. The copied parent heap is `Semantic_Base`, built from sanitized definitions and contract shells. Stock target heaps and original target proofs are excluded. Legal upstream facts remain available through the sanitized runtime and premise catalog.

## Commands

```bash
PYTHONPATH=src python3 -m vero.isabelle verify-environment --environment /path/to/portable-environment
PYTHONPATH=src python3 -m vero.isabelle prepare --contracts /path/to/contracts.jsonl --output benchmarks/MyIsabelle
PYTHONPATH=src python3 -m vero.isabelle render --benchmark benchmarks/MyIsabelle/benchmark.json --output /tmp/my-sandbox
PYTHONPATH=src python3 -m vero.isabelle run --benchmark /benchmark/benchmark.json --config /control/config.json --output /results/trial
```

Inside the worker, the agent can run `python3 /backend/tool.py search QUERY`, `check MODULE`, and `progress`. Progress reports filled slots, not verified proofs.

`run --resume` binds to the same benchmark, configuration, and implementation hashes. It records each submitted call and its wall-clock allowance before invocation. Recovery refuses to overwrite a live agent process, reuses a completed independent evaluation, and charges the full reserved allowance when actual interrupted usage is unknown. Agent failures are distinct from an exhausted proving budget. Monetary cost is `null` when the CLI does not supply it. With metrics enabled, individual persisted response records provide token accounting, including failed/interrupted invocations and discoverable child sessions; missing reports remain unknown. Legacy completed-turn usage is diagnostic and must not be added to response totals.

For an explicitly authorized continuation after an exhausted run, use a new run directory and budget. The optional `initial_proofs` configuration maps slot IDs (for example `c0001:prove`) to previously independently checked proof bodies. These bodies are frozen as part of the configuration binding; record their source artifact and evaluation in `continuation_provenance`. They are inputs to the next agent and must pass the fresh final evaluator again. This does not reset the prior run’s charged budget or make imported proofs count as newly generated results.

## Isolated deployment

`scripts/deploy_isabelle_worker.py --help` lists the deployment arguments. Use a dedicated Docker worker with sanitized runtime, parent heaps, benchmark, and backend code mounted read-only. The agent is UID 1001; the final Isabelle process is UID 1002. The supervising process owns the configuration and results. User settings, homes, and mutable proof sources are separated. The worker cannot mount the original Refine checkout or Docker socket. A working directory alone is not an isolation boundary. On machine B, unprivileged user namespaces prevent the nested Codex sandbox from starting. The dedicated nonroot Docker deployment therefore selects `danger-full-access` for the inner CLI and retains the outer container/user/mount controls; the adapter rejects this setting outside that deployment. This follows the [official CLI guidance for isolated runners](https://learn.chatgpt.com/docs/developer-commands?surface=cli).

The deployment script accepts an explicitly authorized temporary authentication file. Credential retention and container cleanup follow the user’s explicit instructions. The current machine B experiment and both authentication copies are retained; do not delete them or resume the experiment without the corresponding user instruction. If using `--proxy`, it should refer to an authorized proxy bound to the worker host's loopback address; the worker uses host networking for that option. This deployment is for a cooperative coding agent, not a general hostile multi-tenant security service.

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

Schema version 1 includes `source_benchmark_id`, module entries (`name`, ordered `contracts`, `imports`, `rationale`) and an `execution_order`. Exact-once coverage, known IDs, safe nonreserved module names, dependency topology and source identity are validated. Contract statements and contexts come exclusively from the source benchmark; the plan cannot replace them. This is an Isabelle adaptation of Vero's artifact-driven PLAN stage, not the original Lean translation schema. PLAN does not enumerate helper proof nodes. With slot schema v2, generation can introduce file-level helpers through auxiliary slots; slot schema v1 retains the original restriction.

The accepted plan is compiled to a distinct benchmark identity. A fresh all-`oops` native Isabelle preflight validates declarations/imports, not proofs. Only after it passes does generation start. `.vero/plan.json` guides the repository agent; rendered imports and declaration order enforce the plan's available context. The agent is instructed to work providers before consumers, but its actual command order remains agent-controlled. The final evaluator follows topological module order. An imported theory build failure yields `blocked_dependency` for downstream pending candidates; an unfilled contract is never made available as a theorem.

In the dedicated worker, set `plan_public_benchmark` to `/run/vero-plan/benchmark.json`: the supervisor owns this file and its directory, while the generator's check tool reads it through `VERO_BENCHMARK`. Agent changes to the sandbox's plan/benchmark do not change the frozen checking context. Module checks include their import closure's current proof bodies and reconstruct all declarations afresh. Final independent evaluation still audits every credited theorem and performs integration only after all contracts are resolved.

`--resume` requires the same pipeline binding. It does not silently restart an interrupted PLAN agent or reset its allowance; if a usable plan remains on disk it can be validated, otherwise an explicit repair/rebudget decision is required. Accepted plan/benchmark identity is rechecked, and the existing generation runner handles its own interruption recovery. Planning validation failures are saved and stop generation.

An explicitly authorized new planning continuation may supply `initial_plan` and `planning_continuation` in a new bound pipeline configuration. The incomplete plan is an input, not an accepted plan; exact coverage and native preflight are still required. Previous charged planning time is retained in provenance and reported in addition to the new call.

Completed machine-B PLAN experiment: [results and traces](../reports/refine-plan-machine-b-20260916/README.md). Historical intermediate attempts from that run retained only the checked module. New attempts also preserve the complete extracted artifact, including imports and dependency/helper bodies, with a hash; this does not retroactively repair old snapshots.


## Upstream proof-mode compatibility (slot schema v2)

See [the constituent audit](upstream-parity.md). New `prepare` outputs schema v2. Historical v1 artifacts remain readable and their identities are unchanged. Upgrade into a new path with `python -m vero.isabelle upgrade-slots --benchmark OLD --output NEW`; never overwrite a historical run binding. The 287-contract upgrade is `benchmarks/RefineIsabelleV2/benchmark.json`, with identical contract entries. Deployment now defaults to that directory; `--benchmark` can select the old directory explicitly.

Each module has `imports` and `global_aux` slots; each polarity has a `prove_aux` or `disprove_aux` slot before its frozen declaration. These port upstream `imports`, `global_aux` and target-associated `proof_aux`. Helpers are ordinary declarations, not independently scored or scheduled nodes. Use `lemma`, `theorem` or `definition` with fresh `vero_aux_` binding names. `global_aux` is outside contract contexts; target-associated auxiliary slots are inside the exact enclosing context. Fully prove a helper before using it; a malformed or failed helper causes a module build failure. No arbitrary axioms, unfinished auxiliary proofs, new contexts/locales, notation changes or raw ML are permitted. This conservative grammar is an explicit Isabelle adaptation.

Imports slots contain additional generated theory names separated by whitespace. The entire effective graph is checked for unknown names/self references/cycles and evaluated in topological order. Fixed PLAN edges remain; additional edges are extracted and replayed from the artifact. Legal upstream facts remain supplied through the frozen base environment; arbitrary external theory imports are not enabled.

Example inside a `global_aux` marker pair:

```isabelle
lemma vero_aux_shared: "True" by simp
```

Later target bodies may use `by (rule vero_aux_shared)`; an importing module may use `by (rule Vero_Provider.vero_aux_shared)`. Auxiliary bodies survive fresh evaluation and `resume_artifact=true` iterations. The check tool records the full artifact before invocation, and identifies auxiliary-slot errors separately from target errors.

`max_iterations` counts total outer calls when chunking is disabled (upstream `iterate.max` counts retries, so add one). `resume_artifact=false` matches upstream's independent-iteration default; `true` seeds every slot type from the previous artifact. `feedback=true` writes `FEEDBACK.md` and includes the independent report in the next prompt. `best_index` selects the highest resolved count, breaking ties toward earlier records; it does not replace the complete journal or Refine-positive counts.

For persistent session chunks, set `iteration_time_chunk_seconds` and `iteration_max_time_seconds`, plus the overall `total_agent_seconds`. The workspace is not re-rendered between chunks. The first call persists its session; subsequent calls use `exec resume --last` in the same sandbox/CODEX_HOME. Each checkpoint is extracted and independently evaluated. A timed-out chunk continues within its outer iteration while allowance remains; a normal early exit advances the outer iteration. Recovery retains iteration/chunk positions and charges interrupted calls. Normal single-shot calls remain ephemeral. Session routing is covered by mocked CLI/workspace tests and installed CLI flag inspection; no paid model session-resumption experiment was run for this change.

Reports expose `passed_specs`/`resolved_specs` (positive or negative, as upstream proof mode) and `proved_specs` (Refine positive proofs). `all_specs_resolved` requires integrated acceptance of all polarities; `complete` continues to require all-positive Refine completion. Set `completion_metric="resolved"` to use upstream proof-mode stopping, or retain the default `"positive"` for Refine. Neither setting changes theorem checks.

## Legacy chunked runs and comparison stops

Explicit legacy chunked configurations use `total_agent_seconds: null`, `max_iterations: null`, and `iteration_max_time_seconds: null`. Their finite 600-second calls interrupt the agent and then resume it; this mode is retained for historical replay. New seL4 deployments use continuous sampling below. The workspace, session and independent evaluation feedback carry forward, including after a normal agent reply. Explicit positive `--agent-seconds` still supports bounded experiments. Existing bound runs must retain their configuration; use a new run directory for this policy.

Positive completion requires all fixed contracts to pass and the integrated `Vero_All` check to succeed. An agent reply, negative proof, checker timeout or infrastructure error is not positive completion. Individual checker timeouts remain finite.

To stop gracefully, the operator writes valid JSON such as `{"reason":"predeclared comparison budget reached"}` to `<generation-run-dir>/STOP_REQUEST.json`. The runner finishes the current finite call and independent evaluation, then records `status: stopped` and the request before another call starts. Allowance includes the remaining checkpoint and evaluation latency. This is an external stop, not a success claim. Retain the request in the experiment archive; remove the marker before explicitly resuming with the same bindings and `--resume`. If completion is verified first, completion takes precedence.

For fair comparisons, predeclare a shared model-token or monetary budget and separately report planner/generator usage, cached input, output and available reasoning usage, checker CPU/wall time, retries and infrastructure failures. One agent call can edit many proofs; call counts and theory-check counts are not equivalent units of proof effort. Token/cost budget enforcement is not yet implemented by this runner; missing usage must remain unknown. External stops are observed only at checkpoints and any budget overshoot must be reported. Plateau-based stopping may supplement a fixed resource budget but must not count as success or replace a predeclared common comparison endpoint.

## Contract retry termination (seL4 default)

The default unbounded deployment also sets `max_contract_retries: 5`: **one initial completed candidate check plus at most five retries per fixed contract**, six attempts total. All total/outer-iteration caps must be null in this mode. Multiple contracts may be generated and submitted in one agent call. The retry policy is supervisor-enforced, not inferred from model replies or agent-writable diagnostic files.

`tool.py check MODULE` submits that module's candidates to a supervisor socket. The controller checks each candidate against the accepted artifact, in declaration order, excluding other unverified candidates. Checkpoints submit any remaining candidates in theory dependency order. Shared auxiliary declarations and imports remain editable subject to native verification; a successful trial must preserve every previously accepted positive proof. Accepted bodies are retained, failed trials are archived, and a normal-return checkpoint restores the accepted artifact. A cutoff/interruption preserves unsubmitted work in the workspace for continuation and does not automatically submit an unfinished proof. This is an intentional change to the earlier module-level checking protocol and must be recorded when comparing experiments. It incurs additional native checking work.

- Successful contracts are frozen and do not consume more attempts. Each deliberately resubmitted failed candidate consumes another completed attempt, even if its text is unchanged. Rechecking the same candidate automatically at a checkpoint does not double-charge it.
- Empty/`oops` targets do not count as attempted. Malformed extraction/import context, failed prerequisites, infrastructure errors and checker timeouts do not consume mathematical retries. The agent receives pending targets and diagnostics and must continue; these events never make an unattempted contract exhausted.
- A candidate containing a broken helper is not automatically a proof of the parent. Helpers remain Vero auxiliary declarations, not separately scheduled fixed contracts with independent retry budgets.
- The authoritative `<run>/contract-retries/state.json` records per-contract counts, accepted bodies and evidence history. Every native trial has its artifact, fresh source, report and logs. Recovery commits a completed native result exactly once; incomplete checks retain evidence without inventing a failed proof.
- `CONTRACT_RETRIES.json` is a public checkpoint projection; `tool.py progress` returns live authoritative state. `CHECKPOINT_CANDIDATES.json` and `CONTRACT_RETRY_FEEDBACK.json` preserve withdrawn candidates and diagnostics for subsequent repair.
- `complete` requires all positive proofs and successful `Vero_All` integration. `retries_exhausted` means every contract is either proved or has consumed all six attempts, with at least one exhausted failure. Untouched targets prevent this termination. An integration/agent error or operator stop is an operational pause, not successful completion or retry exhaustion; explicit resume preserves the counters.

Legacy 600-second calls remain resumable checkpoints, not a 30-minute deadline; the new default instead leaves the agent uninterrupted. A nonresponsive generator or persistent infrastructure fault can still prevent progress; the controller must not fabricate attempts to manufacture termination. For a deliberately time-bounded comparison, explicit `--agent-seconds` selects the historical budget mode instead; old experiment bindings are never changed in place.

## Continuous sampling and durable checker feedback (2026-09-17 default)

New unbounded seL4 deployments set `sample_interval_seconds: 120`, `iteration_time_chunk_seconds: null`, and `agent.timeout_seconds: null`. The existing contract retry policy remains active. One Codex process keeps running across sample boundaries. Normal agent replies may be continued with the same session; a genuine error/interruption starts a fresh conversation using preserved files, counts and diagnostics, avoiding reuse of a dangling tool-call history.

The passive sampler writes `iter-*/samples/<timestamp>/candidate-artifact.json`, `accepted-artifact.json` and `progress.json`. It never terminates the agent, modifies source, submits proof attempts, or treats raw candidate snapshots as accepted proofs. Candidate extraction may overlap an edit; its errors remain visible. Accepted snapshots come from the atomically committed controller journal. Optional post-hoc native grading is available via `python3 -m vero.isabelle evaluate-samples --benchmark ... --samples ... --checker ... --output ...` using a new output directory.

Checks now have durable request IDs. `tool.py check MODULE` prints `VERO_REQUEST_ID <id>` on stderr before waiting, and returns a single JSON object on stdout. The trusted supervisor stores the request before execution and response before delivery. Reusing an ID with different inputs is rejected. A repeated or recovered request replays completed target outcomes without spending more attempts, including a partially completed batch.

- `tool.py result REQUEST_ID`: retrieve the saved response or finish the interrupted remainder of that request.
- `tool.py feedback cNNNN`: retrieve the target's latest full native diagnostic; omit the ID for all latest diagnostics.
- `tool.py progress`: live contract state and unacknowledged request IDs.
- `RECOVERED_FEEDBACK.json`: latest trusted diagnostics made available at a new agent invocation.

The CLI acknowledges after flushing the JSON response. This records transport receipt only, not proof that the model understood it. Responses remain retrievable after acknowledgement. No synthetic tool-return records are inserted into old Codex session files.

The run monitor requests shutdown only for an operator stop or when all contracts are proved/exhausted; it waits for a pending trusted check to finish. The CLI receives terminate and a 30-second cleanup grace period. A process-tree hard kill is a logged fallback for an unresponsive shutdown, never a sampling mechanism. User stops do not submit unfinished edits even if the CLI exits with status zero. Positive completion still requires native integration.

## Incremental checks and complete-theory seals

The seL4 deployment script enables incremental checks by default (`--full-checks` retains legacy replay). Set `checker.incremental: true` for manually configured contract-retry experiments. The supervisor stores reusable native evidence in `contract-retries/incremental-cache`; the agent cannot write this directory. Cache identities include the checker configuration (including a frozen `environment_identity`), the checker implementation and support theory, the rendered owner source, and every transitive generated import. Changed helper declarations, proofs or imports invalidate the affected consumers; unrelated modules reuse their prior evidence. Failures, timeouts and missing theorem audits are not cached as success.

An incomplete module is checked through the last submitted proof in its native theory context, closing its contexts at that point. Later unfilled declarations are not part of that prefix check. Imported generated theories are still loaded natively: this implementation avoids repeated independent checks of unchanged modules, but is not a persistent Isabelle process or a cache of loaded ML theory states. Consequently a changed target still pays its import-loading cost.

A successful prefix is not a theory seal. After every contract in a theory is positively proved and its imports are sealed, the checker verifies the full rendered theory and all expected audit markers, then records a content-bound seal. `progress.theory_seals` distinguishes `incomplete`, `waiting_for_imports`, `seal_failed` and `sealed`. A seal failure is separate from target retry accounting; it prevents repository completion and is retried on the next evaluation. Cached seals become inapplicable when source or transitive imports change. Final `Vero_All` integration is still required for repository completion.

Operator stops are honored between targets in a batch. Pending interrupted checks retain evidence and do not consume an attempt without a completed outcome. Migration from an older experiment uses a separate continuation directory, preserves counters and source history, and warms the new native cache against accepted proofs before model generation resumes.

### Separate dependency-loading deadline

`checker.dependency_timeout_seconds` opts into separate native-process phases. The trusted renderer emits `VERO_THEORY_ENTER <owner>` immediately after the theory header. Startup and transitive imports use the dependency budget; the exact owner's marker starts `timeout_seconds` afresh. This owner budget includes global helpers and the accepted prefix, not just the new contract. Reports include `dependency_loading_seconds`, `owner_theory_seconds`, `owner_started`, and `timeout_phase`. An absent marker cannot produce a successful check. Explicit `check(timeout=...)` keeps the legacy whole-process deadline.

The September 20 continuation uses 600 seconds for dependency loading and 120 seconds for the owner theory, without a total experiment deadline. Evidence caches still reuse only successful complete prefix checks; they do not retain live processes, loaded imports or progress from a timeout. This prevents imports consuming the owner budget but does not eliminate repeated import costs. Contract retry accounting is unchanged.

## Freeform proposition auditing

Freeform checking exports a named candidate theorem and reconstructs the frozen contract proposition. The audit first joins the candidate's proof dependencies: reading a theorem proposition alone does not certify a still-running Isabelle proof. Premise discharge handles non-atomic assumptions and reflexive HOL equalities, including equality premises introduced by correspondence attributes. It retains the checks for undischarged hypotheses, skipped proofs, oracles and added axioms. Acceptance requires both the trusted audit marker and successful completion of the native check.

Audit support changes participate in the implementation binding and invalidate old cache identities. Historical candidates can be rechecked in separate immutable-context replay outputs; do not overwrite the original experiment ledger or treat corrected offline outcomes as a fresh model run. See [the audit repair validation](../reports/audit-fix-retest-20260923/README.md).

## Comparable native checking deadlines

New worker deployments default to a 300-second total native check cap, matching the local repo-level whole-proof node check. Startup, imports and owner proof checking share that deadline. Phase telemetry remains available: `timeout_phase` identifies loading versus owner checking, while `timeout_limit=total_check` identifies expiry of the shared budget. `--check-seconds` controls the owner cap and `--check-total-seconds` controls the total cap; zero explicitly disables the latter for historical phase-only policies. The effective deadline is the earliest configured limit, so the 600-second dependency cap adds no time to a 300-second total cap.

A target check that exhausts the total deadline consumes a candidate attempt in both VERO ContractRetries and freeform candidate-v2. It is not eligible for free same-candidate infrastructure retries. Legacy configurations without a total cap retain their previous timeout accounting. Changing a run configuration requires a new binding; preserve historical counters and scores. See [the configuration comparison and replay](../reports/timeout-parity-20260923/README.md).

## Autonomous repository experiment with helper stages

The `codex_repository` profile uses ordinary theory files and basic check/feedback/search tools while the Codex agent owns planning, grouping and proof search. Every contract/helper has six candidates per stage. A newly registered required helper batch must all become GREEN before its parent obtains one fresh stage, with cumulative costs retained. The new controller freezes accepted proofs, audits native command spans, rejects unregistered standalone proofs, and keeps retries bound to the original candidate/context. It does not migrate old candidate-v2 ledgers.

See the [constraint verification project](../experiments/codex-repository-v3/README.md) for the complete rules, executable tests, configuration and future B deployment workflow. The current user instruction is engineering and validation only: no new model run has been launched.
