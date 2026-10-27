# Resume authorized Sol experiment

Preserve original frozen code/config, candidate ledger, sandbox, and Codex thread. Archive terminated supervisor cycle, recreate only the owned worker from pinned image and existing mounts, restore authorized run-scoped credentials without logging them, and resume via the original repository runner. Seed a new supervisor lifecycle with immutable container identity and a new runner marker; retain previous lifecycle records. Validate unchanged ledger/config hashes before launch and observe model/check progress. Do not change proof inputs, budgets, or model.

User explicitly authorized transfer of local auth.json to B, and requires credentials retained at experiment end pending a later deletion question. Add --retain-auth to the external supervisor only; frozen proof backend unchanged.
