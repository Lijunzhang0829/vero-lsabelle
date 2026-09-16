# Post-hoc failure attribution for PLAN run

Continue the existing feat/isabelle-proof-backend work. Preserve the original 35/287 result and source artifacts. Run a checker-only, network-disabled diagnostic container on machine B using the same sanitized runtime, Isabelle, parent heaps and checker settings, with no model credentials.

Replay the final Vero_FinaliseSlot unchanged, then replace only c0071:prove with oops and recheck, retaining all other proof bodies, statements, contexts and imports. Check whether c0062 and c0065 survive full module acceptance. Analyze all four c0162 submissions, distinguish sibling failures from actual target diagnostics, compare exact candidate bodies and connect the original command/search outputs to subsequent edits. Recheck each distinct c0162 candidate in a declared isolated context if useful; do not manufacture new proofs or silently add premises.

Save intervention manifests, input/source hashes, checker logs, audits, timeline and attribution confidence in a separate report. Do not change production generation or evaluation semantics. Remove only the diagnostic worker when done; preserve the main container. Record limitations on historical dependency snapshots and unavailable model-internal context.

Completed: five native checks, unchanged proof recovery for c0062/c0065, three isolated c0162 failures, timeline/context audit and confidence-qualified report. Dedicated worker removed; original result unchanged. See [report](../reports/refine-failure-attribution-20260916/README.md).
