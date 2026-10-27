# Continuous generation and durable feedback

User explicitly requested stopping the active experiment, completing the harness, then restarting from scratch. Continue on the authorized feat/isabelle-proof-backend branch; preserve old bindings and evidence.

- Stop refine-contract-retries-002 at its operator checkpoint, archive status and verify credential cleanup.
- Port the upstream passive-sampling design: one uninterrupted Codex process, periodic candidate/accepted-state snapshots, no process termination for sampling. Normal agent replies may continue in the same session; genuine interruptions start a clean conversation with persisted state and diagnostics rather than replaying a dangling tool call.
- Add durable request IDs and reply caching to the trusted checker service. Replay interrupted batch requests without double-charging completed candidates. Provide result retrieval, explicit transport receipts, and per-target diagnostic retrieval.
- Preserve six completed attempts per fixed contract and native integration acceptance. Operator stop/terminal policy can gracefully stop the process with a bounded grace period; hard kill is a logged last resort, never a sampling operation.
- Test a tool that spans multiple sampling intervals, lost response retrieval, partial-batch recovery, normal return and interrupted session selection, terminal stop and native proof checking. Run real model startup and verify a native check spans a passive sampling point.
- Freeze a new code snapshot and launch refine-continuous-001 with the same 287 contracts and accepted 71-theory PLAN, no proof seeds, no total time limit. Disclose protocol/version changes and preserve original result history.
