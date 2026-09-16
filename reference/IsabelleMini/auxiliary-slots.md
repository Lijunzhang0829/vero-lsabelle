# Isabelle schema v2 slots

The canonical Lean BankLedger example remains unchanged. IsabelleMini is the prover-specific fixture for the same editable-slot constituents.

A module receives `(* VERO START Vero_Mini imports *)` and `global_aux` pairs. Imports bodies list additional generated theory names. Global auxiliaries occur before contract contexts.

Each target receives `(* VERO START c0001 prove_aux *)` and `disprove_aux` pairs before its frozen declaration, in its original enclosing context. For example:

```isabelle
lemma vero_aux_truth: "True" by simp
```

The existing `c0001 prove` slot can contain `by (rule vero_aux_truth)`. Other goals in the same context can reuse it; importing generated theories can use its qualified name. Every helper must have a real proof. Auxiliary slots have no independent score, dependency scheduler or assumed truth status.

Definitions require fresh `vero_aux_` names and Isabelle's conservative definition registry. No helper may introduce arbitrary axioms or alter contract contexts. Empty auxiliary slots are allowed; failed or unfinished auxiliary proofs are not accepted.
