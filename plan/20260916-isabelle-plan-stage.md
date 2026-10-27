# Isabelle PLAN stage and fresh Refine experiment

Implement a separate filesystem-capable PLAN agent that incrementally writes `.vero/plan.json` from the fixed sanitized contracts. Its output chooses theory grouping, ordered contract membership, acyclic imports and an execution order, with rationales. The validator requires exact-once coverage and forbids changing propositions/contexts. This adapts Vero's artifact-driven planning to Isabelle, without translating already formalized specs or exposing human proofs.

Compile the accepted plan to a content-addressed benchmark; check declarations with the real Isabelle environment before generation. Preserve the plan, model trace, submissions, validation and implementation identities. Expose a root-owned planned benchmark to the generator's checker. Render actual theory imports and support imported generated facts in checking and independent evaluation. Separate planner-suggested dependencies from actual kernel acceptance. A failed dependency must not be mislabeled as a local proof failure.

Run on B with fresh proof slots, the same 287 contracts and gpt-6-astra medium: PLAN at most 600 seconds, generation 1,800 seconds. Retain prior 56 proofs as comparison results only, not seeds. Report planning cost separately and do not infer causal improvement from one run. Archive per-contract attempts and clean temporary authorization material and worker afterwards.

## Completed outcome

First 600-second PLAN incomplete and rejected; explicit continuation used 478.96 seconds with prior time retained. Accepted: 71 theories, 152 imports, all 287 contracts. Fresh generation: 1,800.01 seconds; independent evaluation: 35 proved, 3 module build errors, 249 unfilled. [Report and cleanup evidence](../reports/refine-plan-machine-b-20260916/README.md). No performance improvement is claimed.
