# Worklog

## 2026-09-15

- Added the Isabelle proof-mode backend on `feat/isabelle-proof-backend`: fixed contracts, positive/negative slots, one repository-level Codex agent, artifact extraction, independent kernel-audited evaluation, and bounded recovery. See [usage and semantics](docs/isabelle.md).
- Reused the verified local sanitized Refine environment on machine B (287 contracts, 21 owners); implemented separate agent/checker users and regression tests for logical and process-isolation boundaries. Pilot results are recorded separately when complete.
