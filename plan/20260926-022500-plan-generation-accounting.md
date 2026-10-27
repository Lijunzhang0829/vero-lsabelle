# PLAN and generation accounting

Use the already authorized feat/codex-autonomous-baseline branch. No model calls or historical experiment changes.

Freeze one manifest from the original pipeline configuration and contract benchmark before PLAN begins. Record planning and generation in the same lifecycle and usage ledger, with explicit planner/prover roles. Reuse the existing generation implementation with the already initialized EventLog so the derived plan benchmark/configuration cannot conflict with the immutable root identity. Preserve the legacy no-metrics path.

Bind accepted plan/derived benchmark/config hashes as events and classify the planning declaration preflight separately. Resume conserves the original reserved PLAN allowance, replays only usage belonging to the bound planning invocation/thread, and never invents an invocation end or zero cost for missing usage. Failed planning retains its cost events. Imported plan seeds disclose unavailable prior usage.

Validate with fake model/checker tests: shared PLAN+generation cost and manifest, role separation, failed-plan recovery without a second model call, interrupted bound-thread usage recovery without importing another experiment, and manifest/binding tamper rejection.
