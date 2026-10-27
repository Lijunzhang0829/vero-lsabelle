# Resource controller liveness and cancellation repair

Continue on the user-approved feat/codex-autonomous-baseline branch. Preserve the paused B run and frozen releases.

- Give resource-v2 a threaded, bounded-lifetime socket service and asynchronous native request receipts while keeping direct dispatch synchronous for native protocol tests.
- Publish immutable request/progress snapshots independently of the single native writer lock; report queued/running/completed/interrupted/cancelled states and retain deduplication.
- Bound client socket wait, propagate supervisor cancellation to registered native descendants, reject late native acceptance and prevent subsequent work after cancellation.
- Test active request queries, duplicate requests, disconnect/recovery, cancellation and service shutdown; support selective statement IDs coordinated with declaration repair.
