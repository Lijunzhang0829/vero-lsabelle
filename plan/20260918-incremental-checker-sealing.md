# Incremental Isabelle verification and theory sealing

Continue on the already authorized feat/isabelle-proof-backend branch. Pause the active Sol experiment with STOP_REQUEST, preserve original frozen run and all charged attempts, then create an explicitly linked continuation with new frozen checker code.

Implement an opt-in incremental checker for controller submissions. Cache successful native checks by checker/environment binding, support implementation, rendered theory and transitive import contents. Check only changed modules and affected consumers; incomplete modules use a rendered prefix ending at their latest submitted proof, rather than checking later unfilled declarations. Cached results remain proof evidence, never arbitrary boolean shortcuts. Shared helper/import edits invalidate consumers. Failed/timeout checks are never reusable.

Once a module has all positive contracts, perform an additional full-theory check and publish a seal only when its imports are sealed; bind seals to exact source/import closure. Preserve final repository integration. Seal failures must not consume a target retry or silently discard already validated target proofs; expose separately and prevent repository completion. Maintain recovery/durable requests and native diagnostic attribution.

Validate cache reuse, helper/import invalidation, prefix truncation, failed checks, full-theory seal gating and recovery with meaningful tests, then real Isabelle fixtures and replay of an existing candidate on B. Resume with Sol medium, same contracts and counters; report measured speedup only from native evidence.
