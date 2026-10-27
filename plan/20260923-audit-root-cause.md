# Diagnose post-proof audit failures

Use original historical candidates and references, not new proofs. In isolated copies of checking sources, instrument Freeform_Reference.audit to print expected/exported propositions, candidate theorem propositions and intermediate proof states. Start with c0038 and distinguish mismatch in statement capture from an incomplete audit tactic. Test any hypothesized correction in diagnostic copies only, then check representative/all 23 first candidates without modifying historical evidence or scores. Preserve exact scripts, diagnostics and integrity hashes.

Completed: all 23 first candidates diagnosed; seven attribute cases additionally checked with reflexivity; six synthetic controls passed; join-before-audit control established asynchronous error masking. One candidate passes, 21 fail their proofs and one times out. Production repair and all-138 corrected replay remain separate follow-up work. Evidence: reports/audit-root-cause-20260923/.
