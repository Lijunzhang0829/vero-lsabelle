# Worklog

## 2026-09-15

- Added configuration-bound initial proof slots for the user-requested network retry, preserving prior charged budgets and imported-proof provenance. Started `refine-002` from 39 independently verified proofs with a fresh 1,800-second allowance after a successful transport probe; final independent evaluation retained all 39 and added 17 (56/287, zero build errors). Temporary credentials and worker were removed; see [retry report](reports/refine-machine-b-network-retry-20260915/README.md). Closed the runner lock deterministically and added a seed/binding regression test.

- Added the Isabelle proof-mode backend on `feat/isabelle-proof-backend`: fixed contracts, positive/negative slots, one repository-level Codex agent, artifact extraction, independent kernel-audited evaluation, and bounded recovery. See [usage and semantics](docs/isabelle.md).
- Reused the verified local sanitized Refine environment on machine B (287 contracts, 21 owners); implemented separate agent/checker users and regression tests for logical and process-isolation boundaries. The completed 30-minute pilot independently validated 39/287 contracts; 2 candidates failed module construction and 246 remained unfilled. Temporary credentials and the dedicated worker were removed. See [the pilot report](reports/refine-machine-b-20260915/README.md).
