# Fresh Refine generation on machine B

Authorized by the user's request to start the new experiment; use the existing feat/isabelle-proof-backend branch.

- New run: refine-contract-retries-002; fresh proofs, reuse the accepted 71-theory/152-import PLAN with v2 auxiliary slots. Preserve the old plan and benchmark identities as provenance.
- Freeze the tested backend source and new benchmark inside this run. Verify the sanitized environment and perform native declaration/import preflight.
- gpt-6-astra, medium; null total/iteration limits, 600-second persistent checkpoints; each fixed contract has one initial attempt plus five retries.
- Reuse the authorized temporary authentication workflow, with credentials outside reports and automatic deletion when the experiment exits. Do not print authentication contents.
- Run detached on B, record startup/heartbeat/terminal status, retain all proof and retry records, and verify real model activity and first native feedback before reporting launch.
