# Machine B Refine pilot protocol

## Input

- Host: `zljj@114.212.82.105`, dedicated `vero-isabelle-refine` worker.
- Original Vero commit: `0a7325df9e9e6dbc275c0ad483b3d1cbe38d9b09`.
- seL4: `daa0dfb1470c5ffbf13b3778f93111679574e80c`; l4v: `00d9073f70d064b31b4b1fad8a0e7086bb258007`; Isabelle: `74b2d1278b57797572abe5842e318d17ed131c55` (Isabelle2024).
- Docker image: `sel4-public:tuned`. Local OCI manifest digest `8e7ee430544e004b57343636038d5ce155c128e5ee002a22821cbeaa6f66d324`; machine B image config digest `65933e97a11542321961efe22999550b79e12cd909d147e50b3554c7517b3f9f`. The manifest/config relation and all 19 layers were checked during environment rebuild.
- The local verified deletion result was exported unchanged: 287 contracts, 21 owners, 84,281 legal premises, 6,364 files. Contract statements SHA-256: `285575f92434b7dc0faba83a3ea8ea4fbbc72507f97ed1f95df5148bff249aca`.
- Portable environment manifest SHA-256: `cb149517396a677fb99bb3dbbac7bd5b5ea3f52130177ce9a1e4408c8a0dfa34`.
- Benchmark identity: `4a9653c8e4b28ccd9a68e0c9f2b35cd1a6a6d3fe0b664670bdb6ecdd44f18be8`.

## Generation and evaluation

One repository-level Codex invocation uses `gpt-6-astra`, medium reasoning, and an 1,800-second wall-clock cap. The agent chooses targets and premises from the full frozen inventory. There is no GRA/Planner stage, no supplied target proof outline, and no reuse of existing generated or historical target proofs. All slots start as `oops`. Preflight successfully loaded all positive and negative declarations in 59.35 seconds; this proves no contract.

The benchmark fixes independent owner modules, with preceding generated facts reusable within a module. Cross-owner generated-proof imports are not supported in this initial version. This constraint matters when comparing against other repository-level systems and must not be hidden.

The agent has editable proof bodies and legal sources, separate from the supervising process and the nonroot final checker. The final evaluator extracts the artifact and rebuilds new theories from frozen declarations. It requires successful module checks and clean kernel-dependency audits. Negative proofs are reported separately. Whole-Refine completion additionally requires all 287 positive proofs and a successful integrated check.

The results directory records the exact configuration and implementation hash in `binding.json`. Resume does not reset spent wall time; unaccounted interrupted calls consume their reserved allowance. Missing token/cost accounting is unknown rather than zero.

## Setup and runtime caveats

Setup smoke calls are separate from the Refine trial. They exposed incomplete session search paths, normalization of quantified assumptions during negation, missing Codex code-mode runtime files, and unsupported nested user namespaces. These were corrected before the Refine invocation. The final backend passed 17 tests including real Isabelle kernel checks, and 31 selected upstream Vero regressions passed.

During the pilot, a missing `rg` PATH entry was fixed without supplying proof content. The SSH proxy also suffered interruptions; the implementation switched to one foreground tunnel with dynamically assigned ports, and the loopback relay now closes stale connections when the target port changes. Runtime timestamps for these changes are retained with the results. Therefore the pilot's wall time includes infrastructure disruption and cannot be interpreted as pure proof-search time or a stable performance estimate.

The model's own checker output is provisional. The summary is populated only from the final independent evaluator. No proof-search hints or reference proofs were injected during monitoring.

Temporary authentication was explicitly authorized by the user. Cleanup must remove the credential-bearing worker and both temporary authentication locations on machine B after the trial; the original local credential remains untouched. Cleanup evidence is recorded separately.
