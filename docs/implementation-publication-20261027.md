# Isabelle implementation publication, 2026-10-27

This publication separates implementation, deployment/analysis scripts, and optimization records into three commits based on the locally recorded upstream main revision `0a7325df9e9e6dbc275c0ad483b3d1cbe38d9b09`. It snapshots the current local source; it is not a reconstruction of the latest machine-B CRefine release.

The implementation includes repository generation and proposition auditing, incremental checks and theory sealing, bounded transport recovery, durable resource budgets, process supervision, resource protocol receipts, and response-level accounting. Scripts provide worker deployment, supervision and metric exports. Existing dated plans and the worklog record the motivations and historical acceptance scope; their historical run claims are not new validation results. References to experiment artifacts may be unavailable because artifacts are excluded from this publication.

Local test additions, experiment configuration directories, reports, logs, heaps and credentials are excluded from these commits. Tests already present in the upstream base are inherited unchanged.

## Validation and limits

- Source patch whitespace checks passed.
- The existing non-native unittest invocation attempted 280 tests, with 15 skips and 10 module-import errors due to unavailable pytest; the remaining tests passed. Ruff was unavailable. This is incomplete validation, not a full-suite pass.
- The separate parent harness incident replay reported 17 cases and 19 passing replays. This exercises the parent harness together with selected VERO code, not this publication branch in isolation; some replays check historical observations rather than demonstrating a repaired live system.
- A local seeded budget-invariant test passed 20 seeds and 2,000 generated operations, plus wrong-run and corrupt-ledger controls. It is intentionally excluded from publication at the user's request.
- Native Isabelle, Docker deployment and model-backed experiments were not rerun for this publication.

Source origin: https://github.com/sunblaze-ucb/vero . The destination repository is https://github.com/Lijunzhang0829/vero-lsabelle ; GitHub currently identifies it as an independent repository rather than a fork.
