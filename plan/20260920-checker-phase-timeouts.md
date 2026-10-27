# Dependency loading and owner checking budgets

User authorized pausing and repairing the running continuation while preserving progress. Continue on the previously authorized `feat/isabelle-proof-backend` branch.

- Stop at a completed checker boundary; preserve all accepted bodies, per-contract histories and counters.
- Add trusted rendered theory-entry markers and stream native output. Opt-in dependency-loading budget is separate from the existing owner-theory budget. Record elapsed time and timeout phase; old configuration remains compatible.
- Explain that successful evidence caching does not preserve an interrupted Isabelle process or loaded imports. This change removes dependency starvation, not repeated import-loading costs.
- Test phase deadlines, missing markers, owner-name matching and real Isabelle checks. Revalidate all accepted proofs without charging attempts.
- Create a separately identified continuation with frozen updated code/configuration and original state copied intact; retain original evidence. Resume Sol medium only after validation.
