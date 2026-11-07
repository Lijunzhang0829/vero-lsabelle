# Independent Codex autonomous Refine baseline

User authorized branch `feat/codex-autonomous-baseline` and selected autonomous Codex organization, reusing only fixed benchmark data, checker and attempt accounting. This is a fresh run on machine B with `gpt-6-astra`, reasoning effort `medium`, no imported generated proofs and six completed mathematical attempts per contract (initial plus five retries).

The previous VERO continuation ended with 82/287 proved, 205 exhausted and 1,422 charged attempts. Preserve its run and all existing uncommitted changes. It is mixed Astra/Sol history, not a fresh model baseline.

## Implementation

- Add an explicit autonomous instruction profile to the existing execution transport. Do not supply VERO PLAN, strategy rationales, fixed proof-search order, persistence/decomposition recipes or an external proof planner. The single Codex session chooses search, proof order, helper use and generated imports.
- Mechanically render one immutable contract per initial theory. This provides arbitrary acyclic dependencies without a supervisor-chosen grouping/order. Wrappers and proof/helper/import slots are the trusted checker interface, not model-generated planning guidance. Record this structural difference when comparing results; this is not unrestricted arbitrary-source Isabelle editing.
- Freeze the exact same 287 statement/context/source identities. Reuse sanitized Semantic_Base, upstream premise data, kernel auditing, incremental complete-prefix certificates, full-theory seals and final integration. No old generated proof or historical target proof is visible.
- Keep the six-attempt controller unchanged, including no charges for infrastructure errors, interrupted checks or blocked dependencies, and no reset after conversation recovery. Require positive proofs; report exhausted targets explicitly.
- Use dependency loading 600 seconds, owner theory checking 120 seconds, four Isabelle threads, no total generation deadline and passive 120-second sampling. Avoid strategy changes during generation.
- Add deployment parameters for model/profile/dependency timeout, and freeze implementation/configuration/inventory hashes in a separately named run. Use existing Docker agent/checker separation and authenticated transport; remove run-specific temporary credentials and worker on termination.

## Validation and reporting

Before adaptation, the local suite ran 87 tests, passing with 11 native-Isabelle skips. Add tests for statement preservation, empty initialization, no inherited PLAN/strategy prompt, required six-attempt policy and prompt selection in the actual runner. Run the complete native suite on B and all-contract declaration preflight before starting generation. Record live model identity, first actual check and heartbeat. Preserve logs and all candidate/diagnostic evidence. At termination record proved/exhausted/infrastructure-blocked counts, attempts, seals, integration status, usage availability, timing and failure attribution. Completion of the experiment must not be represented as proving all contracts.

## User correction: free native files

The user explicitly rejected fixed theory grouping and submission slots. Superseding the initial wrapper design: arbitrary files and ordinary Isabelle theories in one isolated workspace; any number of contracts per theory; no VERO PLAN or historical content. The trusted adapter must freeze propositions in the parent context, certify submitted ordinary theorems against those propositions, isolate other pending targets, and retain the existing six-attempt semantics. Generated files, helpers and chosen theory dependencies belong entirely to this experiment. Success closes a target; all unresolved targets must exhaust first generation plus five revisions before normal termination. No overall resource cap was supplied. Keep a single repository-level Codex session with normal compaction, without a prescribed proof workflow.
