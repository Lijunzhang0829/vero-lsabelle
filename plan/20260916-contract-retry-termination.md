# Per-contract termination for long Refine runs

Continue on the existing authorized feat/isabelle-proof-backend branch and preserve the existing unbounded-run edits. Apply the repo-level-proof-generation skill for frozen inputs and independent checking; retain Vero's repository agent rather than introducing File Planner/GRA.

- No total generation deadline or outer iteration cap in the new policy. Finite calls are resumable checkpoints only.
- Each fixed contract gets one initial native-checked candidate and at most five retries. Multiple candidates may be submitted together; successful contracts stop consuming attempts.
- A supervisor-owned controller checks candidates against accepted proofs in dependency order, isolates sibling failures, persists counters/evidence before responding, and retains accepted artifacts. Infrastructure/check timeout/interruption does not consume proof retries.
- Agent-side diagnostic ledgers cannot authorize acceptance or reset counters. Route the agent check tool through a local supervisor socket in this mode.
- Terminate on all positive proofs plus integration, or every remaining contract exhausting six completed attempts. Empty slots do not count as attempts. Operational errors/stops remain resumable, not successful termination.
- Test batching, isolation, exhausted limits, interruption recovery, persistence, and runner termination. Preserve historical experiment identities and scores. Update deployment/docs; no new model run is implied by changing the policy.
