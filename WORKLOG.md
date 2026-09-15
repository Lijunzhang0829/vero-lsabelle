# Worklog

## 2026-09-15

- Added the Isabelle proof-mode backend on `feat/isabelle-proof-backend`: fixed contracts, positive/negative slots, one repository-level Codex agent, artifact extraction, independent kernel-audited evaluation, and bounded recovery. See [usage and semantics](docs/isabelle.md).
- Reused the verified local sanitized Refine environment on machine B (287 contracts, 21 owners); implemented separate agent/checker users and regression tests for logical and process-isolation boundaries. The completed 30-minute pilot independently validated 39/287 contracts; 2 candidates failed module construction and 246 remained unfilled. Temporary credentials and the dedicated worker were removed. See [the pilot report](reports/refine-machine-b-20260915/README.md).
