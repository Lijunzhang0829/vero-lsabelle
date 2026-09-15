# Network retry protocol

User requested rerunning work affected by network interruptions. The first trial was one repository-level invocation, so failures cannot be assigned uniquely to individual contracts. This continuation uses the same benchmark/model and a new 1,800-second allowance; the previous charged budget and artifacts remain unchanged.

The configuration explicitly imports 39 independently validated positive proof bodies from `refine-001/trial-001`; failed candidates are not imported. The complete seed and its artifact/evaluation provenance are bound into `binding.json`. No hand-written proof hints were supplied by the supervising agent. Source change `42a1746` adds seed loading and a seed/binding regression test. Fourteen non-kernel tests passed; four kernel tests were skipped in the local test environment. The final benchmark evaluation uses real Isabelle on machine B.

A real model transport probe completed successfully before generation (15.80 seconds, separate from the proof budget). A reconnecting loopback SSH proxy was used. SSH connections still dropped during this run; a proxy WebSocket connection error also occurred in the agent stderr and subsequent commands resumed. This continuation is not a network-free timing baseline. Report CLI terminal errors separately from transport diagnostics; zero CLI error events does not imply zero network errors.

The final score must come from a fresh evaluator, including rechecking imported proofs. Report new proofs and any lost prior proofs separately. Remove the dedicated worker and both temporary credential copies after evaluation. The main `sel4-l4v` container remains in service.
