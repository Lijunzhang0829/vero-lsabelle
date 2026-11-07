# Shared Isabelle execution and experiment telemetry

The user authorized pausing B's current Codex experiment, unifying its Isabelle harness with A, and improving VERO/Codex logs for method × model coverage/cost/time curves. Continue on the previously approved feat/codex-autonomous-baseline branch. Preserve all historical proofs, bindings, ledgers and credentials; do not launch or resume a model experiment.

1. Record explicit user pause and verify no running worker or pending native request.
2. Reuse the actual A skill-bound persistent Isabelle ML core with a local isolated launcher on B. Preserve B frozen-proposition, oracle/axiom and command gate checks. Bind source/core versions, deadlines, threads and context; full theory and repository acceptance require isolated session builds.
3. Add versioned append-only experiment events and immutable manifests. Record individual model responses including interrupted turns, nullable missing usage, model/role, source-bound GREEN acceptance, helper stages, checks, builds, and lifecycle segments. Both VERO and Codex use the same event schema.
4. Export token-threshold coverage and 10/30/50/75/100 milestones with raw token components, active/wall time, accounting quality and pending/missing usage. Helpers do not count as original contracts; shared costs are not forcibly assigned to individual targets.
5. Validate unit/integration regressions and native controls for false proofs, context/locale, helper elaboration, prefix isolation/invalidation, deadlines and builds. Deploy a new versioned backend on B without changing the old frozen release. Keep shared sources/heaps read-only and use dedicated output homes.

Implementation split: native backend adapter/core tests; telemetry/agent response capture/export; root controller acceptance/lifecycle integration, build deployment, validation and documentation.
