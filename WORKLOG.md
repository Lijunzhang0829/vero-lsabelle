# Worklog

## 2026-09-16

- Completed checker-only post-hoc failure attribution: original FinaliseSlot failure reproduced; withdrawing only c0071 makes unchanged c0062/c0065 pass full module checking. All three historical c0162 candidates still fail in isolation at an unhandled state assertion; the first three original submissions had instead been blocked by sibling failures. Original 35/287 score preserved, no new proofs/model calls/credentials, diagnostic worker removed. See [attribution evidence](reports/refine-failure-attribution-20260916/README.md).

- Added separate artifact-driven Isabelle PLAN, native declaration preflight, actual generated-theory imports and dependency-aware evaluation. All 29 tests passed on machine B including real cross-theory proof reuse. Completed a fresh Refine experiment without earlier proof seeds: first PLAN exhausted 600 seconds at 207 contracts and was rejected; explicit continuation took 478.96 seconds and produced a validated 71-module, 152-import plan covering all 287 contracts. Generation took 1,800.01 seconds; independent evaluation proved 35, with 3 module build errors and 249 unfilled. This is below the prior fresh 39 result. Traces and per-contract records are archived; credentials and worker were removed. See [PLAN experiment](reports/refine-plan-machine-b-20260916/README.md).

## 2026-09-15

- Added per-contract diagnostic check snapshots, rendered-source attribution, interrupted submissions, supervisor archives and automatic post-evaluation ledgers. Backfilled all 287 contracts conservatively: 56 independently proved, 2 unresolved with explicit legacy slot evidence, 229 unresolved with incomplete per-contract history. See [current ledger](reports/refine-contract-ledger-20260915/README.md).

- Added configuration-bound initial proof slots for the user-requested network retry, preserving prior charged budgets and imported-proof provenance. Started `refine-002` from 39 independently verified proofs with a fresh 1,800-second allowance after a successful transport probe; final independent evaluation retained all 39 and added 17 (56/287, zero build errors). Temporary credentials and worker were removed; see [retry report](reports/refine-machine-b-network-retry-20260915/README.md). Closed the runner lock deterministically and added a seed/binding regression test.

- Added the Isabelle proof-mode backend on `feat/isabelle-proof-backend`: fixed contracts, positive/negative slots, one repository-level Codex agent, artifact extraction, independent kernel-audited evaluation, and bounded recovery. See [usage and semantics](docs/isabelle.md).
- Reused the verified local sanitized Refine environment on machine B (287 contracts, 21 owners); implemented separate agent/checker users and regression tests for logical and process-isolation boundaries. The completed 30-minute pilot independently validated 39/287 contracts; 2 candidates failed module construction and 246 remained unfilled. Temporary credentials and the dedicated worker were removed. See [the pilot report](reports/refine-machine-b-20260915/README.md).
