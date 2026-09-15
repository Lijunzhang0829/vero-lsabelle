# Validation

## Isabelle backend

The final backend passed 17 tests in 34.274 seconds. The saved [test output](backend-tests.txt) includes real Isabelle checks for positive proofs, counterexamples, vacuous assumptions, quantified assumptions with distinct types, and rejection of `sorry` through the audit even with `quick_and_dirty=true`. It also covers artifact tampering, duplicate markers, symlinks, missing audits, recovery accounting, live-process recovery refusal, and termination of detached prover descendants.

Command: `VERO_TEST_ISABELLE=/workspace/verification/isabelle/bin/isabelle PYTHONPATH=/repo/src python3 -m unittest discover -s /repo/tests/isabelle -v`, executed in `sel4-public:tuned` with the local Isabelle installation mounted read-only.

## Upstream Vero regression

31 tests passed in 0.82 seconds:

```text
python3 -m pytest -p no:cacheprovider -q \
  tests/generation/test_benchmark.py \
  tests/generation/test_render.py \
  tests/generation/test_extractor.py \
  tests/evaluation/test_joint_rerender.py
```

The original Lean implementation was unchanged. These tests cover the affected integration surface; this is not a claim that every upstream benchmark or Lean theorem was rebuilt.

## Machine B

- Portable environment verification: all 6,364 exported files and all public stream hashes matched the local deletion result.
- All 287 positive and negative Refine declarations loaded using `Semantic_Base` in 59.35 seconds. Slots were `oops`, so the preflight proves no contracts.
- The nonroot agent successfully ran the Isabelle check tool on the empty KHeap module.
- Model transport smoke 005 executed `pwd` and `printf VERO_SHELL_READY` with exit code zero, after checking the nested-sandbox compatibility issue.
- Final proof counts come only from the independent evaluation recorded alongside this report.
