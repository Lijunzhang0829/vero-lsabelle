# Repository-level proof generation experiment

Prove all {{CONTRACT_COUNT}} fixed contracts in contracts.json over {{BASE_IMPORT}}. You are a general Codex agent ({{AGENT_MODEL}}, {{REASONING_EFFORT}}). Own the repository plan, contract order, theory grouping, helper design, local proof search and revisions. Decide how to use diagnostics and dependency information. No solution plan, dependency oracle, previous experiment result or tactic schedule is supplied.

Only sanitized parent-session definitions/premises and content generated in this experiment are legal. Search /public/premises.jsonl with the tool or read sanitized /semantic-base and /sel4-project/verification/l4v. Unproved goals are never premises. Do not access previous experiments, their histories or results, removed target-session human proofs, external proof sources or network services. This experiment's own generated files, check feedback, history and notes remain available for learning and recovery. Keep all generated files in /sandbox. A theory may contain one, many or all contracts and helpers; regrouping is allowed within frozen statement/context constraints. Maintain your own plan/notes if useful.

## Required theory planning

Before the first proof-candidate check, plan the repository and write /sandbox/theory_plan.json. Assign every fixed contract ID to exactly one generated theory. For each theory record its name, relative .thy path, imported theories and complete contract ID list; record helper IDs as they are introduced. Choose the grouping, dependency DAG and generation order yourself. Every contract must be covered once, and generated-theory imports must be acyclic. Original theory names visible in source_id or benchmark metadata are provenance; they do not require you to retain the original grouping.

Create the corresponding .thy files containing the full planned contract inventory, using the exact supplied statements/contexts and oops for unproved targets. Do this before submitting candidates, so a theory is not treated as complete while some of its planned contracts are still absent. Update both theory_plan.json and the source inventory when regrouping theories or adding helpers, while preserving registered statements/contexts and GREEN proofs. The plan file is your planning artifact; the checker currently validates submitted sources and import cycles rather than reading that JSON plan. You remain responsible for checking complete, unique plan coverage.

After all contracts and registered helpers in a theory are GREEN, a complete-theory seal must be attempted. The supervisor automatically runs sealing after each newly GREEN node; inspect progress.theory_seals to confirm its outcome. A theory with a non-GREEN node remains incomplete. If generated imports are not yet sealed, its status is waiting_for_imports: complete the predecessor theories first, then check the downstream seal result. Record seal_failed as an unresolved sealing failure, including its returned native status and evidence path. Neither a helper's success nor all individual node checks alone establish that the entire theory is sealed. Only sealed marks successful complete-theory verification; final repository integration remains an additional check.

## Basic tools and submission format

Use ordinary .thy files with matching theory filenames, importing {{BASE_IMPORT}} or current-run theories. Target binding names are their immutable cNNNN IDs. Preserve each contract's statement, attributes and original context from contracts.json, changing only its name. Top-level supporting theorems must have registered hNNNN IDs. Local Isar have/show facts within a candidate belong to that candidate. Definitions and abbreviations are permitted; once frozen in a submitted context they must remain present and unchanged. Raw ML, new axioms, sorry, custom executable hooks and untracked standalone proof commands (including crunch, schematic_goal and notepad) are outside this experiment's accepted source language. Local proof methods operate within the current node's charged candidate.

Use the trusted checker for every proof experiment. Do not invoke Isabelle separately or build another evaluator. These basic tools enforce counts and native acceptance; they do not choose a plan or proof:

```
python3 /backend/repository_tool.py check c0001 [h0001 ...]
python3 /backend/repository_tool.py progress
python3 /backend/repository_tool.py feedback c0001
python3 /backend/repository_tool.py search 'text' --limit 20
python3 /backend/repository_tool.py helpers batch.json
python3 /backend/repository_tool.py depend c0001 h0001 [c0002 ...]
python3 /backend/repository_tool.py result REQUEST_ID
python3 /backend/repository_tool.py retry REQUEST_ID
python3 /backend/repository_tool.py finish
```

Every check/helpers command snapshots all .thy files under /sandbox, including subdirectories. Keep scratch text or draft alternatives that should not participate in checking in files with another extension. Multiple IDs in one check command are checked independently in the order supplied; each eligible node consumes its own candidate slot. Blocked, already-GREEN or exhausted nodes are not checked and receive no new candidate charge.

To introduce supporting goals, first put the parent and helper statements in the desired source contexts (use oops while planning), and submit a JSON batch:

```
{"request_id":"split-unique-id","parent":"c0001","helpers":["h0001","h0002"],"rationale":"Describe how these intermediate facts support this parent."}
```

The helper command freezes and elaborates statements with unproved candidate bodies erased; it establishes no theorem and consumes no proof-candidate slot. Declaration-check costs are recorded separately. Each helper then needs an independently counted proof check; several helpers may be listed in the same check command. All top-level helper declarations must belong to this batch or earlier registered nodes. Preserve their statements/contexts after registration. Existing contract/helper nodes can be attached with depend, which provides no new budget; a node can be checked only when all its registered dependencies are GREEN. Complete any outstanding infrastructure recovery before changing the affected node's dependencies. Edits, theorem movement and new helper design remain your decisions within these constraints.

## Attempts, helpers and feedback

Every contract/helper starts with six candidate submissions: one initial generation and five adjustments. Each eligible node selected by a fresh check request reserves one slot before native checking; repeated, invalid, empty, timed-out and resource-exhausted candidates count. There are no hidden syntax-repair retries. Read the native diagnostics and authoritative progress as needed; decide how to revise your proof, find premises or decompose the goal yourself.

Registering a new required helper batch blocks its parent until every member is GREEN. When the whole batch becomes GREEN, reset that parent's remaining allowance TO EXACTLY 6, not remaining allowance plus 6: stage_attempts := 0 and remaining_attempts := 6. For example, a parent that used 4 attempts and has 2 left receives 6 remaining attempts, not 8. Discard unused old-stage slots, retain cumulative attempts/costs, and apply this reset only once for that batch. Only the parent's stage is reset; other nodes' budgets are unchanged. Helper creation, one success out of a batch, unrelated facts, renamed duplicate goals, or already-GREEN helpers do not reset budgets. Helpers have the same six-attempt/stage rule and may have their own helper batches. Nested helper success never proves an ancestor. Cycles are rejected. A failed helper remains visible and blocks its parents; it is not silently deleted. There is no separate configured global helper-count budget: helper growth and its full costs are experimental observations.

GREEN comes only from complete Isabelle proof evidence checked against the frozen proposition, with proof futures joined and no admitted/oracle/extra-axiom dependency. Proof bodies of non-GREEN helpers and contracts other than the current target are replaced with oops. Their declarations establish no facts. Already GREEN nodes remain available in the imported theories or preceding source context; referencing one still requires placing/importing it in a legal Isabelle context. A registered graph dependency alone does not import or cite a theorem. Keep accepted statements, proofs and contexts unchanged; they are frozen evidence for later checking and integration. A catalog match does not establish that a fact is available/applicable in the current context.

For a lost reply, retrieve result REQUEST_ID; do not issue a new check. For a recoverable infrastructure outcome, retry REQUEST_ID replays exactly the saved candidate and accepted context at most twice, with no new candidate slot. It cannot test edits or retry proof timeout/stack exhaustion for free. Request and stage identifiers survive restarts. A new candidate uses a new request ID.

## Checking and completion

Native checker configuration: {{CHECKER_POLICY}}. {{CHECKER_DESCRIPTION}} Transport uses streaming with a 180-second network inactivity limit and bounded same-session recovery; active checker tools and ordinary reasoning silence are not a three-minute generation deadline.

Continue across the repository until all contracts are GREEN, or no eligible node remains and the remaining goals are exhausted or blocked by retained exhausted dependencies. After exhausting a parent you may still register a new useful helper batch before finalizing; a sixth failed attempt does not itself prevent helper decomposition. Use progress for the authoritative stage, remaining_attempts, blockers, retry_requests and can_finalize fields. An exhausted or blocked contract is unresolved, not proved. When no check/recovery remains and you choose to end further helper decomposition, call finish; the controller requires can_finalize to be true. This does not require proving that no useful helper could ever be invented. A normal text reply does not finish the run. There is no fixed wall-clock generation budget or forced checkpoint interruption.

Report contract and helper outcomes separately, along with stage credits, cumulative attempts, blocked goals and evidence. The supervisor performs complete-theory sealing and final integration automatically; there is no separate agent seal/integrate command. All nodes GREEN triggers integration, whose recorded complete result is required for repository success. Partial contract success or all_proved alone must not be reported as a complete repository proof.
