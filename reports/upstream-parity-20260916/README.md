# Upstream Vero proof-mode constituent parity validation

Upstream commit: `0a7325df9e9e6dbc275c0ad483b3d1cbe38d9b09`, also remote HEAD when checked. The original Lean modules in this fork match the audited upstream files; see [source hashes](upstream-audit.json). The Isabelle changes adapt their helper slots, extraction/re-rendering, prompts, imports and continuation behavior. Full scope and remaining differences: [module-by-module audit](../../docs/upstream-parity.md).

## Validation

- **46 tests passed on machine B**, including all native Isabelle cases, in 29.515 seconds: [final log](backend-tests-final.txt).
- Native cases cover global/proof auxiliaries, conservative helper definitions, helper reuse in the same fixed context, cross-theory helper reuse through editable imports, full integration, rejection of helper `sorry` through transitive audit, and rejection of arbitrary axioms even with reserved helper names.
- Regression cases cover round trips, legacy slot identity, malformed/cyclic imports, auxiliary-only provider failure, full context snapshots, auxiliary error attribution, all-slot continuation, two-axis checkpoint budgeting, preserved workspace and mocked CLI session routing.
- **All 287 unchanged Refine contracts passed v2 declaration/import preflight** in 59.849 seconds: [native result](refine-preflight.json). This used the original sanitized Semantic_Base environment; empty proofs establish no contract.
- [Lint/format checks](lint.txt) passed. [Implementation hashes](implementation-hashes.json) identify tested backend and test files.
- Initial native testing exposed rejection of legitimate `definition` extensions by the old all-new-axioms guard. The fix consults Isabelle's native `Defs.dest_constdefs` registry for fresh reserved helper constants. The initial failed log is preserved alongside subsequent successful runs; the audit was not disabled.

No paid model call or new Refine generation experiment was run. Persistent model conversation carry-over is covered by command/workspace tests and deployed CLI flag inspection, not a model-backed experiment. Historical 35/287 remains the v1 result and must not be relabeled as v2 performance.

## New baseline and preserved history

`benchmarks/RefineIsabelleV2/benchmark.json` has identity `fca5759b4a0d7523f1c680187e22e8c6853217db633109a23434dd5538eabe9e`. Its contract array equals the original 287-contract array; the old benchmark and old results remain unchanged. The deployment CLI defaults to this directory and accepts an explicit old benchmark path for legacy replay.

The whole raw validation run is preserved on B at `/data/zljj/vero-runs/refine-parity-001/`, and in the parent worktree at `reports/vero-isabelle-upstream-parity-20260916/`. The worker had no network and no credentials. It was removed after verification; the main seL4 container remains running: [cleanup](cleanup.json), [deployment](deployment.json).
