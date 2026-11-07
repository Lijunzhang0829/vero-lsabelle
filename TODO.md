# TODO

- 2026-11-07: Snapshot display adjustment complete and tested; use progress.json/progress.md for current versus historical-best counts. Development-only change; bind a new release for future experiments, preserving historical frozen code. See [plan](plan/20261106-progress-display.md).

- 2026-11-06: Contextual refutation and resolved-mode recovery implemented on `feat/contextual-refutation-robustness`. Independent candidate `contextual-refutation-20261106-003` is validated on machine B (125-case native/fault base, 113 final runtime cases); new experiments must freeze this implementation rather than historical `db0d68f`. Preserve historical runs and recheck inherited refutations. See [semantics and deployment](docs/contextual-refutation.md).

- 2026-09-27: New `codex-resource-sol-002` is running on B with frozen r4, gpt-5.6-sol/medium,24h/unlimited tokens, one checker/four threads. User excludes A slot concurrency; fresh A/B28-case native conformance passed.21-theory plan accepted; monitor proof/seal progress, serial declaration costs and reported/unknown usage. Old Sol001 remains paused and preserved. See [launch](reports/codex-resource-sol-002/README.md).

- 2026-09-27 repair: scoped declaration cache, current A audited batch backend, asynchronous status and native cancellation are implemented and validated separately. Formal `codex-resource-sol-001` remains paused at 2/287 GREEN/one seal with original r3 hashes unchanged. New r4 is staging only: freeze/disclose A=2 versus B=1 native slots and acquire current protocol receipts before a fresh launch or explicitly marked continuation. See [repair](reports/resource-check-repair-20260927/README.md).

- Codex resource-v2 runtime, planning/sealing, process watchdog and experimental accounting are implemented. Frozen-code native/deployment acceptance passed; the separately bound B config selects `codex-resource-v2-20260926-r2`. Formal run `codex-resource-sol-001` subsequently launched on validated r3 and resumed after reauthentication on 2026-09-27; keep historical run bindings unchanged. See [recovery](reports/codex-resource-sol-001/recovery-20260927.md). See [engineering acceptance](reports/codex-resource-v2-implementation-20260926/README.md).

- VERO v2 implementation is tracked in the [VERO-specific TODO](experiments/resource-fairness-v2/VERO-TODO.md), published on B at `/data/zljj/vero-requirements/VERO-TODO.md`. Ten work items include code entry points, dependencies and acceptance; no runtime changes or experiment launch in this documentation task.

- Shared A native core and full VERO/Codex accounting are deployed to B `current-shared`; final regression288/288 and original statement preflight287/287 passed. Current Codex experiment remains paused. New performance runs require a fresh run bound to this release; do not rewrite or resume a historical binding with new code. See [delivery](reports/shared-harness-metrics-20260926/README.md).




- PLAN experiment completed; measure dependency reuse and repeat comparable trials before claiming a performance gain. See [results](reports/refine-plan-machine-b-20260916/README.md).

- Remaining comparison limitations: passive sampled evaluation, self mode, upstream transient-retry/cost-budget protocol and additional agent adapters. Persistent checkpoint session carry-over still needs a model-backed validation when the next experiment is run. See [parity audit](docs/upstream-parity.md).

- Failure attribution confirms module collateral loss and an unhandled state assertion; optional next comparison: focused c0162 repair feedback versus targeted legal premises under equal budgets. See [evidence](reports/refine-failure-attribution-20260916/README.md).

- Implement audited token/cost budget enforcement before resource-matched long-run comparisons; current manual stop is checkpoint-granular and usage can be missing.

- Validate model-driven long-run coverage under the new six-attempt contract policy; pending targets are never counted as attempted merely because a checkpoint ended. See [policy](plan/20260916-contract-retry-termination.md).

- Historical `refine-phase-sol-001` completed its charged-attempt policy:82 accepted,205 exhausted. Preserve its mixed-model lineage and original metrics; it is not a fresh comparison baseline.

- Measure dependency-loading costs after the phase-timeout repair; successful evidence caching still does not retain live Isabelle imports. See [phase repair](reports/checker-phases-20260920/README.md).

- The Codex gpt-5.6-sol / medium repository experiment on B (`codex-repository-sol-001`) is explicitly paused at 74/287 contracts after a prior model usage-limit stop. Preserve its frozen backend, results and credentials; resume only after a later user instruction. Historical launch details: see [run record](reports/codex-repository-sol-machine-b-20260923/README.md). Keep historical runs/results unchanged. Reuse the verified [constraint project](experiments/codex-repository-v3/README.md) and theory-planning/sealing prompt, with exact model identity and fresh native preflight.

- Sol experiment resumed on 2026-09-24 and was explicitly paused on 2026-09-26. Ask the user before deleting either authentication copy; this engineering task does not authorize an experiment restart.

- Common VERO/Codex response-usage, acceptance, helper, check/build and lifecycle accounting is implemented. Before a three-method comparison, add the corresponding event adapter for A’s high-level multiagent roles; do not treat historical proxy curves as exact transaction logs. See [shared harness and metrics](reports/shared-harness-metrics-20260926/README.md).

- 2026-09-27: Formal resource run paused by user at 2/287 GREEN, one seal. Long-check investigation found repository-wide declaration invalidation, forced cold checks, blocking status queries and current A/B policy/backend drift. See [audit](reports/codex-resource-sol-001/pause-check-audit-20260927.md). Keep paused pending repair/authorization.

- [ ] Sol 002 failure audit: enforce source/network isolation, fix coarse GREEN invalidation and large-state persistence/polling, test actual A/B frontend parity; preserve contaminated run and start a clean session only after acceptance. See `reports/codex-resource-sol-002/failure-audit-20260928/README.md`.
