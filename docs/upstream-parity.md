# Vero upstream constituent audit and Isabelle adaptation

## Source identity and scope

Upstream: `https://github.com/sunblaze-ucb/vero.git`, commit **0a7325df9e9e6dbc275c0ad483b3d1cbe38d9b09**. `git ls-remote ... HEAD` returned this same commit on 2026-09-16. The local fork already contains the upstream Lean code; no fresh clone or wholesale rewrite was needed. The Isabelle adapter now ports the materialize → expected-slot extraction → clean re-render path and the auxiliary-slot/instruction semantics from that code. Lean-specific parser, Lake and bundle machinery remain upstream code, not copied into an incompatible prover.

This audit concerns the fixed-implementation **proof mode** used for Refine. It does not claim full implementation of upstream codeproof, joint satisfiability, all agent adapters or every experiment control.

## Module-by-module findings

| Constituent / upstream source | Earlier Isabelle behavior | Current behavior and remaining adaptation |
| --- | --- | --- |
| Curation PLAN: `curation/stages/plan.py`, `curation/orchestrator/models.py` | Presented a fixed-contract regrouping PLAN after initially omitting PLAN entirely | Explicit distinction: upstream PLAN plans source-to-Lean benchmark translation, APIs/specs and vocabulary helpers. Isabelle `plan-run` organizes already formalized fixed contracts. They are different stages, not schema-equivalent. |
| Proof materialization: `generation/proof_materialize.py` | Only positive/negative proof bodies | v2 ports file imports/global_aux and target-associated proof_aux; declarations/contexts remain fixed. |
| Agent instructions: `templates/instruction/base.md.j2`, `proof.md.j2` | Omitted file-level helpers and much persistence/decomposition guidance | Adds helper decomposition, at least two distinct approaches before shelving, preserve clean proofs, rollback newest breakage, final checkpoint cleanup. Isabelle-specific syntax examples replace Lean tactics. |
| Expected slots / extraction: `generation/extractor.py` | Only contract prove/disprove slots | One shared slot schedule covers imports, module auxiliaries, polarity auxiliaries and target bodies; unexpected slots/outside edits never reach evaluation. |
| Artifact overlay: `generation/render.py` | Re-render discarded any top-level helpers | All permitted slot interiors are replayed into fresh frozen files and carried by artifact continuation; old schema v1 remains readable. |
| Imports | Fixed owner files, later fixed PLAN DAG | Additive generated-theory imports slots, validated effective DAG, topological checks and transitive closure. External imports remain restricted to the frozen legal environment. |
| Helpers | Proof-local only | File-level reusable theorems and definitions; target-associated slots preserve enclosing contexts. Helpers are not automatic graph nodes. Reserved `vero_aux_` names and conservative outer-command grammar prevent shadowing or changing frozen contexts. |
| Trust: `evaluation/hygiene.py`, `axioms.py` | Reject all outer declarations and all new axioms | Retains lexical safety plus transitive no-skip/no-oracle audit. Admits only reserved-name constant definitions registered by Isabelle's `Defs.dest_constdefs`; arbitrary named axioms still fail native audit. |
| Module failure and scoring | Whole failed theory loses its target credit | Legacy evaluation retains module compilation semantics. The user-requested contract retry mode deliberately isolates candidates against accepted proofs and keeps successful siblings; disclose this protocol change in comparisons. Helper failures still cannot establish parent proofs. |
| Proof/disproof grading | Primary result displayed positive Refine count | Both upstream-like resolved/passed counts and Refine-positive counts exposed, with separate integrated completion fields and explicit stopping metric. |
| Outer iterations: `cli_iterate.py` | Available, default one call; proof-only seed validation | Every slot type can be seeded; independent-iteration default retained; feedback file/prompt and best-resolved record index provided. Total-count versus retry-count option naming is documented. |
| Persistent checkpoints: `cli_iterate.py`, `generation/agents/codex.py` | Not implemented | Legacy same-workspace chunk resume is retained. The real long run exposed a missing tool return when its 600-second kill interrupted a check wait. New default uses the upstream passive-sampling design, with uninterrupted calls, independently gradable snapshots, durable checker responses and fresh conversation recovery after actual process failure. |
| Attempt observability | Module-only candidate snapshots | Full extracted artifact with hash, including helper and imported proof bodies; helper diagnostic positions separated. This is extra diagnostic instrumentation, not proof authority. |
| Prover backend | Lean/Lake versus Isabelle process | Native Isabelle fresh-process checks remain; no incremental persistent Isabelle server/heap cache equivalent to Lake `.olean` reuse. Check cost is a material comparison variable. |

## Remaining protocol differences: do not hide these in comparisons

1. **The repository PLAN is an Isabelle extension, not upstream curation PLAN reproduced verbatim.** Compare v2 `run` without this extra planner as the closer fixed-contract generation baseline; label `plan-run` as a planned variant with separate planning cost. Upstream curation is already outside the fixed-contract generation input here.
2. **Slot grammar is more conservative.** Isabelle accepts helper lemmas/theorems/definitions with reserved fresh names, not arbitrary new datatype/locale/syntax/ML declarations. Imports may name generated modules; external facts are frozen. These restrictions must be shared or reported in cross-system evaluations.
3. **Proof checking cost differs.** Fresh native Isabelle processes reload needed theories, whereas upstream uses Lake compilation/cache behavior. Report checker time and number of checks, not only generation wall time.
4. **Other modes and resource accounting remain different.** Passive sampling and optional post-hoc native grading are now implemented. `iterate.mode=self`, dollar/turn-budget equivalence, upstream transient-error classification/retry policy, non-Codex agents, codeproof and joint checks remain outside this proof-mode adapter. Do not present experiments that rely on them as supported equivalence. Current errors still terminate instead of silently resetting budgets; retries remain explicit.
5. **No helper-node scheduler was added.** Upstream auxiliary slots let the repository agent create reusable declarations. Independent helper planning, per-node budgets and parent rescheduling would be a separate experimental enhancement.
6. **No causal performance claim.** Historical 35/287 used v1. The first v2 long run was stopped at 29/287 after observing checkpoint-induced feedback loss; the continuous fresh rerun is a distinct experiment. Version, stopping rule, checking protocol and available compute differ, so raw scores alone do not establish improvement.

## Reproducible comparison configuration

Use `benchmarks/RefineIsabelleV2/benchmark.json`; all 287 original contract records remain byte-for-byte equal as JSON values. Keep identical sanitized definitions, legal premise catalog, model, reasoning effort and declared generation budget across systems. Record whether extra PLAN, chunking, feedback and artifact seeding are enabled. For upstream proof-mode accounting select `completion_metric="resolved"`; additionally report Refine-positive completion separately. Keep helper failures and module collateral failures visible.

Tests and deployment evidence: [native validation report](../reports/upstream-parity-20260916/README.md).

### Incremental Isabelle verification (2026-09-19)

The user-requested incremental checker is an Isabelle-specific harness change, not a claim of identical upstream Vero evaluation. Exact source/import-bound native evidence replaces unrelated full rechecks; incomplete theories receive prefix checks, and complete theories are fully checked and sealed after imports seal. Persistent Isabelle sessions/import heap caching remain unimplemented. Report mixed-model and checker-migration experiment boundaries explicitly.
